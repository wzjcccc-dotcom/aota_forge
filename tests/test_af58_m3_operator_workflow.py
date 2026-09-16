from __future__ import annotations

import json, os, sys

import pytest

from aota_forge.adapters.opencode import executor as EX, host_client as HC, session_reentry as SR
from aota_forge.core.execution import package as EP, roles as ER
from aota_forge.runtime import config as CFG
from aota_forge.runtime.task_main import reconciliation as R
from aota_forge.work_plane import af_roles as AR

SES, DIR, PLAN = "ses_m3parent000000000001", "/tmp/af58m3", "wzjcccc-dotcom/aota-hermes-tools#58"
EXE = os.path.realpath(sys.executable)
URL = "http://127.0.0.1:4096"
ROLES = ("coder", "reviewer", "analyst", "project-steward", "task-main")


class T:
    def __init__(self, *q): self.q, self.reqs = list(q), []
    def request(self, m, u, *, body=None, timeout):
        self.reqs.append((m, u, json.loads(body) if body else None))
        return self.q.pop(0)


def R_(s, p=None):
    return HC.OpenCodeHttpResponse(status=s, body=b"" if p is None else json.dumps(p).encode())


def RE(t): return SR.OpenCodeExactSessionReentry(HC.OpenCodeHostClient(URL, http_transport=t),
    timeout_seconds=1.0, poll_interval_seconds=0.001)
def row(a): return {"id": SES, "directory": DIR, "agent": a}


def test_worker_profile_exact_no_fallback():
    RB, cfg = CFG.RuntimeBinding, CFG.RuntimeConfig
    b = tuple(RB(work_role=r, executor="opencode", provider="p", model="m", concurrency=1, executable=EXE,
        profile="aota-worker" if r != "task-main" else "aota-task-main") for r in ROLES)
    c = cfg(executor="opencode", executable=EXE, concurrency=1, provider="p", model="m",
        host_endpoint=URL, bindings=b)
    m = CFG.worker_canonical_profile_mapping(c)
    assert set(m.values()) == {"aota-worker"} and "task-main" not in m
    pkg = EP.ExecutionPackage.create(canonical_task_id="p:m:wi:01234567:abcdef12", project_id="p",
        canonical_role="coder", instruction="agent=aota-injected")
    A = lambda t: EX.OpenCodeAdapter(host_client=HC.OpenCodeHostClient(URL, http_transport=t),
        runtime_config=c, trusted_worker_directory=DIR,
        role_mapping=ER.RoleMapping.create(EX.OPENCODE_EXECUTOR_ID, m))
    t = T(R_(200, row("aota-worker")), R_(204)); A(t).dispatch(pkg)
    assert {r[2]["agent"] for r in t.reqs} == {"aota-worker"}
    with pytest.raises(EX.OpenCodeDispatchFailureError):
        A(T(R_(200, row("build")))).dispatch(pkg)


def test_task_return_and_ack_gate():
    assert AR.TASK_RETURN_OP in AR.WORKER_NORMAL_PATH_OPS and AR.TASK_RETURN_OP not in AR.TASK_MAIN_NORMAL_PATH_OPS
    assert not R.ACK_BEFORE_SEMANTIC_RECONCILIATION and R.ACK_REQUIRES_DURABLE_RECONCILIATION


def test_reentry_exact_session_204_not_ack():
    assert SR.CREATE_PARENT_ON_MISS is False and SR.HTTP_ACCEPTED_IS_ACK is False
    t = T(R_(200, row("aota-task-main")), R_(200, []), R_(204),
          R_(200, [{"info": {"role": "assistant"}}]), R_(200, {}))
    r = RE(t).reenter(SES, "e")
    assert (r.outcome, r.ack_observed, r.error_code) == ("completed", False, "ACK_NOT_OBSERVED")
