"""AF #58 M1 — Selectable AF host profiles & exact profile propagation.

Focused acceptance coverage for AF #58 M1/W1+W2:

* the pinned host contract (message-time ``agent`` is authoritative) is honored
  on EVERY AF-controlled OpenCode path:
    - task-main session create + every AF-submitted task-main turn;
    - worker session create + every AF-submitted worker prompt;
    - completion re-entry into the exact task-main parent session;
* the profile value comes ONLY from the operator RuntimeConfig binding (or the
  accepted AF contract constant for lower-level mechanics), never from
  TaskHandoff/model text;
* a host session row that does not persist the exact requested profile fails
  closed (no profile-less session, no silent fallback to the host default
  agent);
* the Hermes path is unchanged.

No live host is used: the FakeTransport scripts the pinned host responses
(including the created-row agent echo that the real pinned host returns).
"""

from __future__ import annotations

import json
import stat
from typing import Any

import pytest

from aota_forge.adapters.opencode.executor import (
    OPENCODE_EXECUTOR_ID,
    OpenCodeAdapter,
    OpenCodeAdapterError,
    OpenCodeDispatchFailureError,
    build_opencode_host_payload,
)
from aota_forge.adapters.opencode.host_client import (
    OpenCodeHostClient,
    OpenCodeHttpResponse,
    OpenCodeProtocolViolationError,
)
from aota_forge.adapters.opencode.session_reentry import OpenCodeExactSessionReentry
from aota_forge.adapters.opencode.profiles import (
    require_exact_profile,
    task_main_profile_default,
    worker_profile_default,
)
from aota_forge.adapters.opencode.task_main import (
    create_task_main_session,
    submit_task_main_turn,
)
from aota_forge.composition.execution import create_opencode_completion_delivery_transport
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.roles import RoleMapping
from aota_forge.runtime.config import (
    EXECUTOR_HERMES,
    EXECUTOR_OPENCODE,
    SHARED_WORKER_PROFILE,
    TASK_MAIN_PROFILE,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
    task_main_host_profile,
)

DIR = "/tmp/af58-m1/test-root"
WORKER_DIR = "/tmp/af58-m1/worker-root"
SES = "ses_aaaaaaaaaaaaaaaaaaaaaaaaaa"
PARENT = "ses_bbbbbbbbbbbbbbbbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeTransport:
    """Scripted OpenCodeHttpTransport double (records exact requests)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.queue: list[Any] = []

    def push(self, response: Any) -> None:
        self.queue.append(response)

    def _next(self) -> OpenCodeHttpResponse:
        if not self.queue:
            raise AssertionError("FakeTransport has no queued response")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(
        self, method: str, url: str, *, body: bytes | None = None, timeout: float
    ) -> OpenCodeHttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "body": json.loads(body.decode("utf-8")) if body else None,
                "timeout": timeout,
            }
        )
        return self._next()

    def stream_events(self, url: str, *, timeout: float, max_events: int) -> list[dict[str, Any]]:
        raise AssertionError("no SSE expected in the AF #58 M1 focused suite")


def _resp(status: int, payload: Any = None) -> OpenCodeHttpResponse:
    return OpenCodeHttpResponse(
        status=status,
        body=b"" if payload is None else json.dumps(payload).encode("utf-8"),
    )


def _client(transport: FakeTransport) -> OpenCodeHostClient:
    return OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)


def _exe(tmp_path) -> str:
    exe = tmp_path / "opencode-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def _opencode_config(
    tmp_path,
    *,
    task_main_profile: str = TASK_MAIN_PROFILE,
    worker_profile: str = SHARED_WORKER_PROFILE,
    provider: str = "afstub",
    model: str = "stub-model",
) -> RuntimeConfig:
    exe = _exe(tmp_path)
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor=EXECUTOR_OPENCODE,
            profile=task_main_profile if role == "task-main" else worker_profile,
            provider=provider,
            model=model,
            concurrency=1,
            executable=exe,
        )
        for role in ("analyst", "coder", "reviewer", "project-steward", "task-main")
    )
    return RuntimeConfig(
        executor=EXECUTOR_OPENCODE,
        executable=exe,
        concurrency=1,
        provider=provider,
        model=model,
        bindings=bindings,
        host_endpoint="http://127.0.0.1:4096",
    )


def _package(*, role: str = "coder", **overrides: Any) -> ExecutionPackage:
    fields: dict[str, Any] = {
        "canonical_task_id": "aota_forge:m1:wi-a:01234567:abcdef12",
        "project_id": "aota_forge",
        "canonical_role": role,
        "instruction": "do the bounded thing",
    }
    fields.update(overrides)
    return ExecutionPackage.create(**fields)


def _worker_adapter(
    transport: FakeTransport,
    *,
    config: RuntimeConfig,
    mapping: dict[str, str] | None = None,
) -> OpenCodeAdapter:
    return OpenCodeAdapter(
        host_client=_client(transport),
        role_mapping=RoleMapping.create(
            OPENCODE_EXECUTOR_ID,
            mapping if mapping is not None else {"coder": SHARED_WORKER_PROFILE},
        ),
        runtime_config=config,
        trusted_worker_directory=WORKER_DIR,
    )


# ---------------------------------------------------------------------------
# RuntimeConfig remains the profile authority
# ---------------------------------------------------------------------------


def test_runtime_config_opencode_af_profiles_survive_parsing(tmp_path) -> None:
    from aota_forge.runtime.config import load_runtime_config

    exe = _exe(tmp_path)
    cfg_path = tmp_path / "runtime-opencode.json"
    cfg_path.write_text(
        json.dumps(
            {
                "executor": "opencode",
                "executable": exe,
                "concurrency": 1,
                "provider": "afstub",
                "model": "stub-model",
                "host_endpoint": "http://127.0.0.1:4096",
                "bindings": {
                    "task-main": {"profile": TASK_MAIN_PROFILE},
                    "analyst": {"profile": SHARED_WORKER_PROFILE},
                    "coder": {"profile": SHARED_WORKER_PROFILE},
                    "reviewer": {"profile": SHARED_WORKER_PROFILE},
                    "project-steward": {"profile": SHARED_WORKER_PROFILE},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_runtime_config(config_path=cfg_path)
    assert task_main_host_profile(config) == "aota-task-main"
    assert config.get_binding("coder").profile == "aota-worker"
    assert config.get_binding("reviewer").profile == "aota-worker"
    assert config.get_binding("analyst").profile == "aota-worker"


def test_runtime_config_hermes_worker_profile_survives_parsing(tmp_path) -> None:
    from aota_forge.runtime.config import load_runtime_config

    exe = _exe(tmp_path)
    cfg_path = tmp_path / "runtime-hermes.json"
    cfg_path.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": exe,
                "concurrency": 1,
                "provider": "afstub",
                "model": "stub-model",
                "bindings": {
                    "task-main": {"profile": TASK_MAIN_PROFILE},
                    "analyst": {"profile": SHARED_WORKER_PROFILE, "toolsets": ["aota"]},
                    "coder": {"profile": SHARED_WORKER_PROFILE, "toolsets": ["aota"]},
                    "reviewer": {"profile": SHARED_WORKER_PROFILE, "toolsets": ["aota"]},
                    "project-steward": {"profile": SHARED_WORKER_PROFILE, "toolsets": ["aota"]},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_runtime_config(config_path=cfg_path)
    assert config.executor == EXECUTOR_HERMES
    assert task_main_host_profile(config) == "aota-task-main"
    assert config.get_binding("coder").profile == "aota-worker"


def test_task_main_host_profile_requires_operator_config() -> None:
    with pytest.raises(RuntimeConfigError):
        task_main_host_profile("not-a-config")  # type: ignore[arg-type]


def test_exact_profile_validation_rejects_profile_less_values() -> None:
    for bad in (None, 1, "", "   ", "aota task main", "aota\nworker"):
        with pytest.raises(ValueError):
            require_exact_profile(bad, label="profile")
    assert require_exact_profile(" aota-task-main ", label="profile") == "aota-task-main"
    assert task_main_profile_default() == "aota-task-main"
    assert worker_profile_default() == "aota-worker"


# ---------------------------------------------------------------------------
# Task-main propagation (session create + every AF-submitted turn)
# ---------------------------------------------------------------------------


def test_task_main_session_create_carries_exact_runtime_profile(tmp_path) -> None:
    transport = FakeTransport()
    client = _client(transport)
    config = _opencode_config(tmp_path, task_main_profile="aota-task-main")
    profile = task_main_host_profile(config)

    transport.push(_resp(200, {"id": SES, "directory": DIR, "agent": profile}))
    row = create_task_main_session(
        client, directory=DIR, instance_key="task-main-run-1", agent=profile
    )
    assert row["agent"] == profile == "aota-task-main"
    assert transport.requests[0]["body"]["agent"] == "aota-task-main"


def test_task_main_session_create_defaults_to_contract_profile(tmp_path) -> None:
    transport = FakeTransport()
    client = _client(transport)
    transport.push(_resp(200, {"id": SES, "directory": DIR, "agent": TASK_MAIN_PROFILE}))
    create_task_main_session(client, directory=DIR, instance_key="task-main-run-2")
    assert transport.requests[0]["body"]["agent"] == "aota-task-main"


def test_every_task_main_af_turn_carries_exact_profile(tmp_path) -> None:
    transport = FakeTransport()
    client = _client(transport)
    config = _opencode_config(tmp_path)
    profile = task_main_host_profile(config)
    transport.push(_resp(200, {"id": SES, "directory": DIR, "agent": profile}))
    create_task_main_session(client, directory=DIR, instance_key="task-main-run-3", agent=profile)

    for turn in ("startup", "productive continuation", "completion re-entry"):
        transport.push(_resp(200, []))  # baseline messages
        transport.push(_resp(204))  # prompt_async
        transport.push(
            _resp(
                200,
                [
                    {
                        "info": {"role": "assistant", "id": "a1", "finish": "stop"},
                        "parts": [{"type": "text", "text": turn}],
                    }
                ],
            )
        )
        result = submit_task_main_turn(
            client,
            session_id=SES,
            directory=DIR,
            text=turn,
            agent=profile,
            timeout_seconds=2,
            poll_interval_seconds=0.001,
        )
        assert result.completed is True

    prompt_requests = [
        request for request in transport.requests if "prompt_async" in request["url"]
    ]
    assert len(prompt_requests) == 3
    assert all(request["body"]["agent"] == "aota-task-main" for request in prompt_requests)
    # Never absent: a body without agent would resolve the host default agent.
    assert all("agent" in request["body"] for request in prompt_requests)


def test_task_main_turn_defaults_to_contract_profile(tmp_path) -> None:
    transport = FakeTransport()
    client = _client(transport)
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    transport.push(
        _resp(
            200,
            [
                {
                    "info": {"role": "assistant", "id": "a1", "finish": "stop"},
                    "parts": [{"type": "text", "text": "ok"}],
                }
            ],
        )
    )
    submit_task_main_turn(
        client,
        session_id=SES,
        directory=DIR,
        text="turn",
        timeout_seconds=2,
        poll_interval_seconds=0.001,
    )
    prompt = [r for r in transport.requests if "prompt_async" in r["url"]][0]
    assert prompt["body"]["agent"] == "aota-task-main"


def test_profile_less_session_row_fails_closed_before_any_prompt(tmp_path) -> None:
    transport = FakeTransport()
    client = _client(transport)
    # The host answered without persisting the requested profile: refuse it.
    transport.push(_resp(200, {"id": SES, "directory": DIR}))
    with pytest.raises(OpenCodeProtocolViolationError):
        create_task_main_session(
            client, directory=DIR, instance_key="task-main-run-4", agent="aota-task-main"
        )
    assert len(transport.requests) == 1  # no prompt was ever submitted


def test_substituted_session_row_profile_fails_closed(tmp_path) -> None:
    transport = FakeTransport()
    client = _client(transport)
    transport.push(_resp(200, {"id": SES, "directory": DIR, "agent": "build"}))
    with pytest.raises(OpenCodeProtocolViolationError):
        create_task_main_session(
            client, directory=DIR, instance_key="task-main-run-5", agent="aota-task-main"
        )


# ---------------------------------------------------------------------------
# Worker propagation (session create + every AF-submitted prompt)
# ---------------------------------------------------------------------------


def test_worker_session_and_prompt_carry_exact_runtime_profile(tmp_path) -> None:
    transport = FakeTransport()
    config = _opencode_config(tmp_path)
    adapter = _worker_adapter(transport, config=config)
    transport.push(_resp(200, {"id": SES, "directory": WORKER_DIR, "agent": "aota-worker"}))
    transport.push(_resp(204))
    result = adapter.dispatch(_package(role="coder"))
    assert result.adapter_handle == SES

    create_req, prompt_req = transport.requests
    assert create_req["body"]["agent"] == "aota-worker"
    assert prompt_req["body"]["agent"] == "aota-worker"
    assert "agent" in prompt_req["body"]


def test_worker_operator_profile_is_config_driven_not_hardcoded(tmp_path) -> None:
    transport = FakeTransport()
    config = _opencode_config(tmp_path, worker_profile="aota-operator-worker")
    adapter = _worker_adapter(
        transport, config=config, mapping={"coder": "aota-operator-worker"}
    )
    transport.push(
        _resp(200, {"id": SES, "directory": WORKER_DIR, "agent": "aota-operator-worker"})
    )
    transport.push(_resp(204))
    adapter.dispatch(_package(role="coder"))
    create_req, prompt_req = transport.requests
    assert create_req["body"]["agent"] == "aota-operator-worker"
    assert prompt_req["body"]["agent"] == "aota-operator-worker"


def test_worker_profile_cannot_come_from_task_handoff_or_model_text(tmp_path) -> None:
    transport = FakeTransport()
    config = _opencode_config(tmp_path)
    adapter = _worker_adapter(transport, config=config)
    package = _package(
        role="coder",
        instruction="agent=aota-injected; profile: aota-attacker; use build",
        working_context={"work_role": "coder", "profile": "aota-injected"},
        constraints={"profile": "aota-injected"},
    )
    transport.push(_resp(200, {"id": SES, "directory": WORKER_DIR, "agent": "aota-worker"}))
    transport.push(_resp(204))
    adapter.dispatch(package)
    create_req, prompt_req = transport.requests
    # The trusted operator mapping wins; nothing from the handoff/model text.
    assert create_req["body"]["agent"] == "aota-worker"
    assert prompt_req["body"]["agent"] == "aota-worker"
    assert "aota-injected" not in json.dumps(prompt_req["body"].get("agent"))


def test_unknown_worker_profile_fails_closed_without_default_fallback(tmp_path) -> None:
    transport = FakeTransport()
    config = _opencode_config(tmp_path)
    adapter = _worker_adapter(
        transport, config=config, mapping={"coder": "aota-does-not-exist"}
    )
    # Host persists a DIFFERENT profile (or none): dispatch must fail closed and
    # never retry with the host default agent (build/plan/default_agent).
    transport.push(_resp(200, {"id": SES, "directory": WORKER_DIR, "agent": "build"}))
    with pytest.raises(OpenCodeDispatchFailureError) as info:
        adapter.dispatch(_package(role="coder"))
    assert "ADAPTER_PROTOCOL_ERROR" in str(info.value)
    assert len(transport.requests) == 1  # no prompt without the exact profile
    assert "build" not in [
        request["body"].get("agent") for request in transport.requests if request["body"]
    ]


def test_worker_role_without_mapping_fails_closed_before_host_call(tmp_path) -> None:
    transport = FakeTransport()
    config = _opencode_config(tmp_path)
    adapter = _worker_adapter(transport, config=config, mapping={"coder": "aota-worker"})
    with pytest.raises(OpenCodeAdapterError):
        adapter.dispatch(_package(role="reviewer"))
    assert transport.requests == []


# ---------------------------------------------------------------------------
# Completion re-entry into the exact task-main parent session
# ---------------------------------------------------------------------------


def _reentry(
    transport: FakeTransport, *, agent: str | None = None
) -> OpenCodeExactSessionReentry:
    kwargs: dict[str, Any] = {}
    if agent is not None:
        kwargs["agent"] = agent
    return OpenCodeExactSessionReentry(
        _client(transport),
        timeout_seconds=2.0,
        poll_interval_seconds=0.001,
        **kwargs,
    )


def _reentry_script(transport: FakeTransport, *, agent: str) -> None:
    transport.push(_resp(200, {"id": PARENT, "directory": DIR, "agent": agent}))
    transport.push(_resp(200, []))  # baseline messages
    transport.push(_resp(204))  # prompt_async
    transport.push(
        _resp(
            200,
            [
                {
                    "info": {"role": "assistant", "id": "a1", "finish": "stop"},
                    "parts": [
                        {
                            "type": "text",
                            "text": "ACK AOTA_COMPLETION_ACK_V1 canonical_task_id=t card_digest=d",
                        }
                    ],
                }
            ],
        )
    )


def test_completion_reentry_carries_exact_task_main_profile(tmp_path) -> None:
    transport = FakeTransport()
    _reentry_script(transport, agent="aota-task-main")
    result = _reentry(transport, agent="aota-task-main").reenter(PARENT, "completion envelope")
    assert result.outcome == "completed"
    prompt = [r for r in transport.requests if "prompt_async" in r["url"]][0]
    assert prompt["body"]["agent"] == "aota-task-main"


def test_completion_reentry_default_carries_contract_profile(tmp_path) -> None:
    transport = FakeTransport()
    _reentry_script(transport, agent="aota-task-main")
    _reentry(transport).reenter(PARENT, "completion envelope")
    prompt = [r for r in transport.requests if "prompt_async" in r["url"]][0]
    assert prompt["body"]["agent"] == "aota-task-main"


def test_completion_delivery_factory_binds_runtime_task_main_profile(tmp_path) -> None:
    transport = FakeTransport()
    config = _opencode_config(tmp_path, task_main_profile="aota-operator-task-main")
    transport.push(
        _resp(200, {"id": PARENT, "directory": DIR, "agent": "aota-operator-task-main"})
    )
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    transport.push(
        _resp(
            200,
            [
                {
                    "info": {"role": "assistant", "id": "a1", "finish": "stop"},
                    "parts": [
                        {
                            "type": "text",
                            "text": "ACK AOTA_COMPLETION_ACK_V1 canonical_task_id=t card_digest=d",
                        }
                    ],
                }
            ],
        )
    )
    delivery = create_opencode_completion_delivery_transport(
        runtime_config=config,
        host_client=_client(transport),
        timeout_seconds=2.0,
        poll_interval_seconds=0.001,
    )
    evidence = delivery.deliver(session_ref=PARENT, envelope="completion envelope")
    assert evidence.outcome.value == "completed"
    prompt = [r for r in transport.requests if "prompt_async" in r["url"]][0]
    assert prompt["body"]["agent"] == "aota-operator-task-main"


# ---------------------------------------------------------------------------
# Hermes path unchanged
# ---------------------------------------------------------------------------


def test_hermes_invocation_translation_unchanged(tmp_path) -> None:
    exe = _exe(tmp_path)
    binding = RuntimeBinding(
        work_role="coder",
        executor=EXECUTOR_HERMES,
        profile=SHARED_WORKER_PROFILE,
        provider="afstub",
        model="stub-model",
        concurrency=1,
        executable=exe,
        toolsets=("aota",),
    )
    args = binding.hermes_args("do the bounded thing")
    assert args[:5] == [exe, "-p", "aota-worker", "-t", "aota"]
    assert args[-2:] == ["-z", "do the bounded thing"]


def test_opencode_worker_binding_host_payload_carries_trusted_context(tmp_path) -> None:
    package = _package(role="coder")
    payload = build_opencode_host_payload(package)
    assert payload["context"]["canonical_task_id"] == package.canonical_task_id
    assert payload["context"]["project_id"] == "aota_forge"
    assert payload["canonical_role"] == "coder"
    assert payload["operation"] == "task_dispatch"
