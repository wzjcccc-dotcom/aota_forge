#!/usr/bin/env python3
"""AF #56 M2/W4 bounded OpenAI-compatible stub model server (deterministic harness).

Deterministic, content-routed model scripting so the pinned OpenCode reference
server can drive the M2 real integration run without any external provider:

- ``scripts`` are matched against the full request text (regex, first match
  wins); this keeps concurrent sessions deterministic without a shared queue.
- ``tool_call`` steps may carry ``"capture_ref": true``: the handler scans the
  incoming request for the most recent tool result containing a JSON ``"ref"``
  and substitutes the literal ``$captured_ref`` placeholder inside the step's
  arguments (the Worker's governed ``handoff.write`` -> ``task.return`` flow
  needs the durable result handoff ref).
- ``echo_ack`` re-emits the EXACT ``AOTA_COMPLETION_ACK_V1
  canonical_task_id=... card_digest=...`` line found in the latest user
  message (the completion envelope). This is the bounded deterministic
  parent-side reconciliation used only by the M2 mechanics harness; M3 owns
  real autonomous task-main reasoning.
- scripts may set ``"repeat": true`` (steps repeat without consumption).

Not a production component; loopback-only; run only for the bounded M2 test.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
STATE: dict = {"scripts": [], "captures": []}

_ACK_LINE_RE = re.compile(
    r"AOTA_COMPLETION_ACK_V1[ \t]+canonical_task_id=\S+[ \t]+card_digest=\S+"
)
_REF_RE = re.compile(r'"ref"\s*:\s*"([^"]+)"')


def _send_sse_event(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    data = json.dumps(payload)
    handler.wfile.write(f"data: {data}\n\n".encode())
    handler.wfile.flush()


def _chunk(model: str, delta: dict, finish: str | None) -> dict:
    return {
        "id": "chatcmpl-afstub-m2",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""


def _request_text(body: dict) -> str:
    parts: list[str] = []
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            parts.append(_message_text(message))
    return "\n".join(parts)


def _substitute(value, ref: str | None):
    if isinstance(value, str):
        if value == "$captured_ref" and ref:
            return ref
        return value
    if isinstance(value, dict):
        return {key: _substitute(item, ref) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, ref) for item in value]
    return value


def _select_step(request_text: str) -> dict:
    with LOCK:
        for script in STATE["scripts"]:
            try:
                if re.search(script.get("match") or "", request_text, re.DOTALL):
                    break
            except re.error:
                continue
        else:
            return {"type": "text", "text": "AF_STUB_DEFAULT_OK"}
        steps = script.get("steps") or []
        if not steps:
            return {"type": "text", "text": "AF_STUB_EMPTY_SCRIPT"}
        if script.get("repeat"):
            return dict(steps[0])
        step = dict(steps[0])
        if not step.get("repeat"):
            steps.pop(0)
        return step


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
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
        if self.path.startswith("/_admin/script_state"):
            with LOCK:
                state = [
                    {"match": s.get("match"), "remaining": len(s.get("steps") or []), "repeat": bool(s.get("repeat"))}
                    for s in STATE["scripts"]
                ]
            return self._json(200, {"scripts": state})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.startswith("/_admin/scripts"):
            body = self._read_body()
            with LOCK:
                STATE["scripts"] = list(body.get("scripts") or [])
            return self._json(200, {"ok": True, "scripts": len(STATE["scripts"])})
        if self.path.startswith("/_admin/script"):
            body = self._read_body()
            with LOCK:
                STATE["scripts"] = [
                    {"match": "(?s).*", "steps": list(body.get("steps") or [])}
                ]
            return self._json(200, {"ok": True})
        if self.path.startswith("/_admin/reset"):
            with LOCK:
                STATE["scripts"] = []
                STATE["captures"] = []
            return self._json(200, {"ok": True})
        if not self.path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})

        body = self._read_body()
        request_text = _request_text(body)
        tools = []
        for item in body.get("tools") or []:
            fn = (item or {}).get("function") or {}
            if fn.get("name"):
                tools.append(fn["name"])
        last_user = None
        for message in reversed(body.get("messages") or []):
            if message.get("role") == "user":
                text = _message_text(message)
                last_user = text[:300] if text else None
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
        capture_file = os.environ.get("AF_STUB_CAPTURE_FILE")
        if capture_file:
            try:
                with open(capture_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(capture) + "\n")
            except Exception:
                pass

        step = _select_step(request_text)
        if step.get("type") == "echo_ack":
            # Echo the ACK line from the NEWEST user message only (the exact
            # completion envelope); older envelopes in the conversation
            # history must never be re-echoed (identity-bound ACK).
            newest_user_text = ""
            for message in reversed(body.get("messages") or []):
                if isinstance(message, dict) and message.get("role") == "user":
                    newest_user_text = _message_text(message)
                    break
            match = _ACK_LINE_RE.search(newest_user_text)
            text = match.group(0) if match else "AF_STUB_ACK_NOT_FOUND"
            step = {"type": "text", "text": text}
        elif step.get("type") == "tool_call":
            ref_match = None
            for match in _REF_RE.finditer(request_text):
                ref_match = match.group(1)
            step = dict(step)
            step["arguments"] = _substitute(step.get("arguments") or {}, ref_match)

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
            "id": "chatcmpl-afstub-m2",
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
    print(f"af-m2-stub listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
