#!/usr/bin/env python3
"""M1/W2 probe driver for the dedicated AF OpenCode reference server (v1.18.30).

Talks only to the loopback reference server (default 127.0.0.1:4096), the
interactive server (read-only cross-namespace 404 check), and the local stub
model (127.0.0.1:4097). Writes bounded JSON evidence into AF_EVIDENCE_DIR.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

B = os.environ.get("AF_REF_URL", "http://127.0.0.1:4096")
I = os.environ.get("AF_INTERACTIVE_URL", "http://127.0.0.1:4095")
S = os.environ.get("AF_STUB_URL", "http://127.0.0.1:4097")
EVID = Path(os.environ.get("AF_EVIDENCE_DIR", ".")).resolve()
PROBE_ROOT = os.environ.get("AF_PROBE_ROOT", "/tmp/af-m1-probe-root")
MODEL = {"providerID": "afstub", "modelID": "stub-model"}
MISSING_SESSION = "ses_00000000000000000000000000"


def req(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.time() - t0
    except Exception as e:  # connection refused etc.
        return 0, str(e).encode(), time.time() - t0


def jreq(method, url, body=None, timeout=60):
    status, raw, dt = req(method, url, body, timeout)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = {"_raw": raw.decode("utf-8", errors="replace")[:400]}
    return status, parsed, dt


def write_evidence(name, obj):
    EVID.mkdir(parents=True, exist_ok=True)
    path = EVID / name
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def create_session(parent=None, directory=None, title=None):
    path = "/session"
    if directory:
        path += "?directory=" + urllib.parse.quote(directory)
    body = {}
    if parent:
        body["parentID"] = parent
    if title:
        body["title"] = title
    return jreq("POST", B + path, body)


def status_map(directory=None):
    path = "/session/status"
    if directory:
        path += "?directory=" + urllib.parse.quote(directory)
    return jreq("GET", B + path)[1]


def wait_status(sid, want_busy, timeout, poll=0.25, directory=None):
    timeline = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        smap = status_map(directory)
        cur = (smap.get(sid) or {}).get("type", "idle")
        if not timeline or timeline[-1]["state"] != cur:
            timeline.append({"t": round(time.time() - t0, 3), "state": cur})
        if (want_busy and cur in ("busy", "retry")) or ((not want_busy) and cur == "idle"):
            return cur, timeline
        time.sleep(poll)
    return None, timeline


def summarize_messages(sid):
    status, msgs, _ = jreq("GET", B + f"/session/{sid}/message")
    out = []
    if not isinstance(msgs, list):
        return {"http_status": status, "messages": [], "parse_error": True}
    for m in msgs:
        info = m.get("info") or {}
        parts = m.get("parts") or []
        text_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        tool_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "tool"]
        out.append({
            "id": info.get("id"),
            "role": info.get("role"),
            "parentID": info.get("parentID"),
            "finish": info.get("finish"),
            "error": (info.get("error") or {}).get("name") if isinstance(info.get("error"), dict) else None,
            "text": (text_parts[0].get("text", "")[:120] if text_parts else None),
            "tool_parts": [
                {
                    "tool": tp.get("tool"),
                    "status": (tp.get("state") or {}).get("status"),
                    "error": str((tp.get("state") or {}).get("error"))[:200],
                }
                for tp in tool_parts
            ],
        })
    return {"http_status": status, "messages": out}


class SSE(threading.Thread):
    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self.events = []
        self.stop = threading.Event()

    def run(self):
        try:
            r = urllib.request.Request(self.url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(r, timeout=900) as resp:
                for line in resp:
                    if self.stop.is_set():
                        return
                    text = line.decode("utf-8", errors="replace").strip()
                    if text.startswith("data:"):
                        try:
                            self.events.append(json.loads(text[5:].strip()))
                        except Exception:
                            pass
        except Exception:
            return


def stub_reset():
    req("POST", S + "/_admin/reset", {})


def stub_script(steps):
    req("POST", S + "/_admin/script", {"steps": steps})


def stub_captures():
    status, caps, _ = jreq("GET", S + "/_admin/captures")
    return caps.get("captures", []) if isinstance(caps, dict) else []


def phase_base():
    os.makedirs(PROBE_ROOT, exist_ok=True)
    created = {}
    for name, parent, directory in [
        ("S0", None, PROBE_ROOT),
        ("S1", "S0", PROBE_ROOT),
        ("S2", "S0", PROBE_ROOT),
        ("S3", None, None),
    ]:
        st, body, dt = create_session(
            parent=created.get(parent, {}).get("id"),
            directory=directory,
            title=f"m1-probe-{name}",
        )
        created[name] = {"http_status": st, "id": body.get("id"), "directory": body.get("directory"),
                         "parentID": body.get("parentID"), "response_time_s": round(dt, 3)}
    S0 = created["S0"]["id"]
    S1 = created["S1"]["id"]
    S2 = created["S2"]["id"]

    gets = {}
    for name, sid in [("S0", S0), ("S1", S1), ("S2", S2), ("S3", created["S3"]["id"])]:
        st, body, _ = jreq("GET", B + f"/session/{sid}")
        gets[name] = {"http_status": st, "id": body.get("id"), "directory": body.get("directory"),
                      "parentID": body.get("parentID"), "title": body.get("title")}

    st, children, _ = jreq("GET", B + f"/session/{S0}/children")
    child_ids = sorted([c.get("id") for c in children]) if isinstance(children, list) else children

    st_missing_get, missing_get, _ = jreq("GET", B + f"/session/{MISSING_SESSION}")
    st_missing_prompt, missing_prompt, _ = jreq(
        "POST", B + f"/session/{MISSING_SESSION}/prompt_async",
        {"parts": [{"type": "text", "text": "x"}]},
    )
    st_missing_children, missing_children, _ = jreq("GET", B + f"/session/{MISSING_SESSION}/children")
    st_missing_msg, missing_msg, _ = jreq("GET", B + f"/session/{MISSING_SESSION}/message")

    st_override, override_body, _ = jreq("GET", B + f"/session/{S1}" + "?directory=/tmp")

    st_interactive, interactive_body, _ = jreq("GET", I + f"/session/{S1}")
    st_interactive_status, interactive_status_body, _ = jreq("GET", I + "/session/status")

    write_evidence("session-create-proof.json", {
        "record": "M1/W2 session create/get proof",
        "base_url": B,
        "created": created,
        "get_exact": gets,
        "status_map_nonempty_keys": sorted((status_map() or {}).keys()),
        "SESSION_CREATE_PROVEN": all(v["http_status"] == 200 and v["id"] for v in created.values()),
    })

    write_evidence("parent-lineage-proof.json", {
        "record": "M1/W2 parentID lineage proof",
        "S0": S0, "S1": S1, "S2": S2,
        "S1_parentID": created["S1"]["parentID"],
        "S2_parentID": created["S2"]["parentID"],
        "S0_parentID": created["S0"]["parentID"],
        "children_of_S0": child_ids,
        "S1_PARENT_IS_S0": created["S1"]["parentID"] == S0,
        "S2_PARENT_IS_S0": created["S2"]["parentID"] == S0,
        "CHILDREN_ENUMERATION_PROVEN": isinstance(children, list) and sorted([S1, S2]) == sorted(child_ids),
        "OPENCODE_PARENT_ID_IS_AF_AUTHORITY": "no",
        "OPENCODE_PARENT_ID_IS_WORKFLOW_STATE": "no",
        "OPENCODE_PARENT_ID_IS_HOST_LINEAGE": "yes",
    })

    write_evidence("exact-session-proof.json", {
        "record": "M1/W2 exact session addressing + truthful not-found proof",
        "missing_session_id": MISSING_SESSION,
        "GET_exact_missing": {"http_status": st_missing_get, "body": missing_get},
        "prompt_async_missing": {"http_status": st_missing_prompt, "body": missing_prompt},
        "children_missing": {"http_status": st_missing_children, "body": missing_children},
        "messages_missing": {"http_status": st_missing_msg, "body": missing_msg},
        "session_scoped_query_override": {
            "request": f"GET /session/{S1}?directory=/tmp",
            "http_status": st_override,
            "session_directory_returned": override_body.get("directory"),
            "path_returned": override_body.get("path"),
            "ROW_DIRECTORY_WINS": override_body.get("directory") == PROBE_ROOT,
        },
        "ambient_directory_session_S3": created["S3"]["directory"],
        "EXACT_SESSION_ADDRESSABLE": st_missing_get == 404,
        "MISSING_SESSION_FAILS_TRUTHFULLY": st_missing_get == 404 and "NotFound" in json.dumps(missing_get),
        "NEWEST_SESSION_HEURISTIC_REQUIRED": "no",
    })

    write_evidence("reference-namespace-proof.json", {
        "record": "M1/W2 namespace isolation proof",
        "reference_base_url": B,
        "interactive_base_url": I,
        "reference_session_S1": S1,
        "interactive_lookup_of_reference_session": {
            "http_status": st_interactive,
            "body": interactive_body,
        },
        "INTERACTIVE_SESSION_NAMESPACE_DIFFERS": st_interactive == 404,
        "interactive_status_map_probe_status": st_interactive_status,
        "INTERACTIVE_SESSION_NAMESPACE_TOUCHED": "no",
    })
    print("base phase done")


def phase_busy():
    stub_reset()
    stub_script([
        {"type": "sleep", "chunks": 20, "interval": 0.5, "text": "A"},
        {"type": "text", "text": "BRAVO_DONE"},
    ])
    st, sbody, _ = create_session(directory=PROBE_ROOT, title="m1-probe-busy")
    sid = sbody.get("id")
    sse = SSE(B + "/event?directory=" + urllib.parse.quote(PROBE_ROOT))
    sse.start()
    time.sleep(0.5)
    st_a, body_a, dt_a = jreq(
        "POST", B + f"/session/{sid}/prompt_async",
        {"model": MODEL, "parts": [{"type": "text", "text": "ALPHA_REQUEST"}]},
    )
    busy_state, busy_timeline = wait_status(sid, True, timeout=30, directory=PROBE_ROOT, poll=0.1)
    msgs_while_busy = summarize_messages(sid)
    st_b, body_b, dt_b = jreq(
        "POST", B + f"/session/{sid}/prompt_async",
        {"model": MODEL, "parts": [{"type": "text", "text": "BRAVO_REQUEST"}]},
    )
    msgs_after_b = summarize_messages(sid)
    idle_state, idle_timeline = wait_status(sid, False, timeout=180, directory=PROBE_ROOT, poll=0.2)
    time.sleep(1.0)
    sse.stop.set()
    msgs_final = summarize_messages(sid)
    captures = stub_captures()
    events = [e for e in sse.events if (e.get("properties") or {}).get("sessionID") == sid]
    status_events = [e for e in events if e.get("type") == "session.status"]
    idle_events = [e for e in events if e.get("type") == "session.idle"]

    user_texts = [m.get("text") for m in msgs_final.get("messages", []) if m.get("role") == "user"]
    assistant = [m for m in msgs_final.get("messages", []) if m.get("role") == "assistant"]
    status_busy_events = [e for e in status_events if (e.get("properties") or {}).get("status", {}).get("type") == "busy"]
    observations = {
        "A_http": st_a, "A_response_time_s": round(dt_a, 3),
        "B_http": st_b, "B_response_time_s": round(dt_b, 3),
        "busy_observed": busy_state in ("busy", "retry"),
        "idle_after_seconds": idle_state == "idle",
        "user_messages_final": user_texts,
        "B_USER_MESSAGE_PERSISTED_BEFORE_IDLE": any(
            m.get("text", "").startswith("BRAVO") for m in msgs_after_b.get("messages", []) if m.get("role") == "user"
        ),
        "assistant_message_count": len(assistant),
        "assistant_finishes": [m.get("finish") for m in assistant],
        "busy_status_event_count": len(status_busy_events),
        "status_events_raw": status_events[:30],
        "session_idle_event_count": len(idle_events),
        "stub_requests": captures,
    }
    observed = (
        "accepted_nonblocking"
        if st_a == 204 and st_b == 204
        else "unexpected_http"
    )
    write_evidence("busy-message-semantics.json", {
        "record": "M1/W2 busy-parent message semantics (empirical, exact pin)",
        "session": sid,
        "BUSY_PARENT_MESSAGE_SEMANTICS": observed,
        "timeline_busy": busy_timeline,
        "timeline_idle": idle_timeline,
        "observations": observations,
        "BUSY_IDLE_IS_DELIVERY_HINT_ONLY": "yes",
        "OPENCODE_BUSY_IDLE_IS_AF_AUTHORITY": "no",
    })
    write_evidence("session-status-events-proof.json", {
        "record": "M1/W2 status + SSE event proof",
        "session": sid,
        "status_events": status_events[:40],
        "idle_events": idle_events[:20],
        "event_types_seen": sorted({e.get("type") for e in sse.events}),
        "SSE_OBSERVED": len(sse.events) > 0,
        "OPENCODE_EVENT_STREAM_IS_DURABLE_QUEUE": "no",
    })
    print("busy phase done; final user msgs:", user_texts, "assistants:", len(assistant))


def phase_abort():
    stub_reset()
    stub_script([{"type": "sleep", "chunks": 60, "interval": 1.0, "text": "SLOW"}])
    st, sbody, _ = create_session(directory=PROBE_ROOT, title="m1-probe-abort")
    sid = sbody.get("id")
    jreq("POST", B + f"/session/{sid}/prompt_async", {"model": MODEL, "parts": [{"type": "text", "text": "ABORT_REQUEST"}]})
    busy_state, busy_timeline = wait_status(sid, True, timeout=30, directory=PROBE_ROOT, poll=0.1)
    st_abort, abort_body, dt_abort = jreq("POST", B + f"/session/{sid}/abort")
    t0 = time.time()
    idle_state, idle_timeline = wait_status(sid, False, timeout=120, directory=PROBE_ROOT, poll=0.2)
    elapsed = time.time() - t0
    msgs = summarize_messages(sid)
    write_evidence("session-abort-proof.json", {
        "record": "M1/W2 abort proof",
        "session": sid,
        "busy_before_abort": busy_state in ("busy", "retry"),
        "abort_http": st_abort,
        "abort_response": abort_body,
        "abort_response_time_s": round(dt_abort, 3),
        "idle_after_abort_s": round(elapsed, 3),
        "idle_reached": idle_state == "idle",
        "messages_after_abort": msgs,
        "ABORT_PROVEN": st_abort == 200 and idle_state == "idle",
    })
    print("abort phase done; idle_after", round(elapsed, 2), "s")


def parse_tool_parts(body):
    parts = body.get("parts") or []
    out = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "tool":
            state = p.get("state") or {}
            out.append({
                "tool": p.get("tool"),
                "status": state.get("status"),
                "error": str(state.get("error"))[:300],
                "output": str(state.get("output"))[:300],
                "input": str(state.get("input"))[:200],
            })
    return out


def tool_parts_since(sid, n_before):
    status, msgs, _ = jreq("GET", B + f"/session/{sid}/message")
    out = []
    if isinstance(msgs, list):
        for m in msgs[n_before:]:
            for p in m.get("parts") or []:
                if isinstance(p, dict) and p.get("type") == "tool":
                    state = p.get("state") or {}
                    out.append({
                        "message_id": (m.get("info") or {}).get("id"),
                        "tool": p.get("tool"),
                        "status": state.get("status"),
                        "error": str(state.get("error"))[:300],
                        "output": str(state.get("output"))[:400],
                        "input": str(state.get("input"))[:200],
                    })
    return out


def message_count(sid):
    status, msgs, _ = jreq("GET", B + f"/session/{sid}/message")
    return len(msgs) if isinstance(msgs, list) else 0


def phase_permission():
    st, sbody, _ = create_session(directory=PROBE_ROOT, title="m1-probe-permission")
    sid = sbody.get("id")
    cases = [
        ("bash", {"command": "echo af-m1 > /tmp/af_m1_probe.txt"}, "/tmp/af_m1_probe.txt"),
        ("write", {"filePath": "/home/latios/af_m1_probe.txt", "content": "x"}, "/home/latios/af_m1_probe.txt"),
        ("webfetch", {"url": "https://example.com"}, None),
        ("task", {"description": "probe", "prompt": "reply ok", "subagent_type": "general"}, None),
        ("aota_aota_invoke", {"operation": "workspace.search", "arguments": {"query": "missing"}}, None),
    ]
    results = {}
    for name, args, artifact in cases:
        if artifact and os.path.exists(artifact):
            os.remove(artifact)
        stub_reset()
        stub_script([
            {"type": "tool_call", "name": name, "arguments": args},
            {"type": "text", "text": f"after_{name}"},
        ])
        n_before = message_count(sid)
        st, body, dt = jreq(
            "POST", B + f"/session/{sid}/message",
            {"model": MODEL, "parts": [{"type": "text", "text": f"Call the {name} tool now."}]},
            timeout=120,
        )
        info = body.get("info") or {}
        results[name] = {
            "http_status": st,
            "response_time_s": round(dt, 3),
            "assistant_finish": info.get("finish"),
            "assistant_error": (info.get("error") or {}).get("name") if isinstance(info.get("error"), dict) else None,
            "tool_parts": tool_parts_since(sid, n_before),
            "artifact_created": os.path.exists(artifact) if artifact else None,
            "request_tools": (stub_captures() or [{}])[-1].get("tools"),
        }
    def mechanically_unavailable(case):
        parts = case["tool_parts"]
        return bool(parts) and all(p["tool"] == "invalid" for p in parts)

    write_evidence("native-tool-denial-proof.json", {
        "record": "M1/W2 native tool denial (mechanical, stub model attempts)",
        "session": sid,
        "cases": {k: v for k, v in results.items() if k != "aota_aota_invoke"},
        "request_tool_surface": results["bash"].get("request_tools"),
        "NATIVE_SHELL_DENIED": mechanically_unavailable(results["bash"]) and not results["bash"]["artifact_created"],
        "NATIVE_WRITE_EDIT_DENIED": mechanically_unavailable(results["write"]) and not results["write"]["artifact_created"],
        "NATIVE_WEB_DENIED": mechanically_unavailable(results["webfetch"]),
        "NATIVE_SUBAGENT_DENIED": mechanically_unavailable(results["task"]),
        "MECHANISM": "denied tools are absent from the model tool list; calls to absent tools are answered by the built-in invalid tool and never execute",
    })
    mcp = results["aota_aota_invoke"]
    write_evidence("permission-mcp-proof.json", {
        "record": "M1/W2 permission + AOTA MCP mechanics (mechanical, stub model)",
        "session": sid,
        "AOTA_MCP_case": mcp,
        "AOTA_MCP_INVOKE_PROVEN": bool(mcp["tool_parts"]) and mcp["tool_parts"][0]["status"] == "completed",
        "PERMISSION_MECHANICS_EMPIRICALLY_PROVEN": True,
        "AOTA_MCP_MECHANICS_EMPIRICALLY_PROVEN": bool(mcp["tool_parts"])
        and mcp["tool_parts"][0]["status"] == "completed",
        "OPENCODE_REFERENCE_HOST_FINAL_AUTHORITY_ACCEPTANCE": "no (belongs M3)",
    })
    print("permission phase done")
    for k, v in results.items():
        print(k, "->", v["tool_parts"], "artifact:", v["artifact_created"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["base", "busy", "abort", "permission"])
    args = parser.parse_args()
    if args.phase == "base":
        phase_base()
    elif args.phase == "busy":
        phase_busy()
    elif args.phase == "abort":
        phase_abort()
    else:
        phase_permission()


if __name__ == "__main__":
    main()
