#!/usr/bin/env python3
"""M1/W2 bounded OpenAI-compatible stub model server (probe instrument only).

Deterministically scripts model responses so the pinned OpenCode reference
server can be probed without depending on an external provider. Captures the
exact request shape (model-visible tool list) sent by OpenCode, which is the
mechanical evidence for the permission / MCP tool-surface proof.

Not a production component; loopback-only; operator-run during M1 probes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
STATE = {"script": [], "captures": []}


def _send_sse_event(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    data = json.dumps(payload)
    handler.wfile.write(f"data: {data}\n\n".encode())
    handler.wfile.flush()


def _chunk(model: str, delta: dict, finish: str | None) -> dict:
    return {
        "id": "chatcmpl-afstub",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        return

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def do_GET(self) -> None:
        if self.path.startswith("/_admin/captures"):
            with LOCK:
                caps = list(STATE["captures"])
            return self._json(200, {"captures": caps})
        if self.path.startswith("/_admin/health"):
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.startswith("/_admin/script"):
            body = self._read_body()
            with LOCK:
                STATE["script"] = list(body.get("steps") or [])
            return self._json(200, {"ok": True, "steps": len(STATE["script"])})
        if self.path.startswith("/_admin/reset"):
            with LOCK:
                STATE["script"] = []
                STATE["captures"] = []
            return self._json(200, {"ok": True})
        if not self.path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})

        body = self._read_body()
        tools = []
        for item in body.get("tools") or []:
            fn = (item or {}).get("function") or {}
            if fn.get("name"):
                tools.append(fn["name"])
        last_user = None
        for message in reversed(body.get("messages") or []):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, list):
                    content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
                last_user = str(content)[:200] if content else None
                break
        capture = {
            "ts": time.time(),
            "stream": bool(body.get("stream")),
            "model": body.get("model"),
            "tools": sorted(tools),
            "tool_count": len(tools),
            "message_count": len(body.get("messages") or []),
            "last_user": last_user,
        }
        with LOCK:
            STATE["captures"].append(capture)
            script = STATE["script"]
            step = script.pop(0) if script else {"type": "text", "text": "AF_STUB_DEFAULT_OK"}
        capture_file = os.environ.get("AF_STUB_CAPTURE_FILE")
        if capture_file:
            try:
                with open(capture_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(capture) + "\n")
            except Exception:
                pass

        model = str(body.get("model") or "stub-model")
        if body.get("stream"):
            return self._stream(step, model)
        return self._nonstream(step, model)

    def _stream(self, step: dict, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            kind = step.get("type", "text")
            if kind == "tool_call":
                args = json.dumps(step.get("arguments") or {})
                _send_sse_event(self, _chunk(model, {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_afstub_1",
                        "type": "function",
                        "function": {"name": step["name"], "arguments": args},
                    }],
                }, None))
                _send_sse_event(self, _chunk(model, {}, "tool_calls"))
            elif kind == "sleep":
                chunks = int(step.get("chunks") or 1)
                interval = float(step.get("interval") or 1.0)
                _send_sse_event(self, _chunk(model, {"role": "assistant", "content": ""}, None))
                for _ in range(chunks):
                    time.sleep(interval)
                    _send_sse_event(self, _chunk(model, {"content": step.get("text", "af-stub")}, None))
                _send_sse_event(self, _chunk(model, {}, "stop"))
            else:
                pieces = step.get("pieces")
                if pieces:
                    _send_sse_event(self, _chunk(model, {"role": "assistant", "content": pieces[0]}, None))
                    for piece in pieces[1:]:
                        interval = float(step.get("interval") or 0)
                        if interval:
                            time.sleep(interval)
                        _send_sse_event(self, _chunk(model, {"content": piece}, None))
                else:
                    _send_sse_event(self, _chunk(model, {"role": "assistant", "content": step.get("text", "")}, None))
                _send_sse_event(self, _chunk(model, {}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _nonstream(self, step: dict, model: str) -> None:
        content = step.get("text", "")
        message = {"role": "assistant", "content": content}
        finish = "stop"
        if step.get("type") == "tool_call":
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_afstub_1",
                    "type": "function",
                    "function": {"name": step["name"], "arguments": json.dumps(step.get("arguments") or {})},
                }],
            }
            finish = "tool_calls"
        self._json(200, {
            "id": "chatcmpl-afstub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def main() -> None:
    host = os.environ.get("AF_STUB_HOST", "127.0.0.1")
    port = int(os.environ.get("AF_STUB_PORT", "4097"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"af-stub listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
