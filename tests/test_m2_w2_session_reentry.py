"""M2/W2 exact session re-entry adapter tests.

Covers the structural fail-closed guarantees:

    EXACT_SESSION_REENTRY=yes
    MISSING_SESSION_FAILS_CLOSED=yes
    SILENT_NEW_SESSION_FALLBACK=no
    SOFT_RESUME_PATH_USED=no (structurally unproducible)
    SAME_LINEAGE_REDIRECT_ACCEPTED=yes (exact row + Hermes lineage target)
    BUSY_SESSION_CLASSIFIED_RETRYABLE=yes
    FORCED_SESSION_INTERRUPT=no
    DELIVERY/ACK state machine NOT implemented here (W3 owns it)
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from aota_forge.adapters.hermes.session_reentry import (
    HermesExactSessionReentry,
    HermesSessionReentryError,
    build_exact_reentry_argv,
    validate_exact_session_id,
)

FAKE_CHAT = """#!/bin/sh
# fake hermes CLI: records argv, behaves per FAKE_REENTRY_MODE
echo "$0 $*" >> "${FAKE_REENTRY_LOG}"
resume_id=""
seen_resume=0
for a in "$@"; do
  if [ "$seen_resume" = 1 ]; then resume_id="$a"; seen_resume=0; continue; fi
  if [ "$a" = "--resume" ]; then seen_resume=1; fi
done
if [ "${FAKE_REENTRY_ECHO_QUERY}" = 1 ]; then
  need=0
  for a in "$@"; do
    if [ "$need" = 1 ]; then cat "$a" > "${FAKE_REENTRY_CAPTURED}"; break; fi
    if [ "$a" = "--query-file" ]; then need=1; fi
  done
fi
case "${FAKE_REENTRY_MODE}" in
  ok)
    printf 'task-main acknowledged the completion\n'
    printf 'session_id: %s\n' "${FAKE_REENTRY_RESOLVED:-$resume_id}" >&2
    exit 0;;
  leasetimeout)
    printf 'Error: session_turn_lease_timeout:some-id\n' >&2
    exit 1;;
  notfound)
    printf 'Session not found: %s\n' "$resume_id" >&2
    exit 1;;
  refuse)
    printf 'SESSION_COORDINATION_UNAVAILABLE: active session owner exists\n' >&2
    exit 1;;
  hardfail)
    printf 'provider exploded\n' >&2
    exit 1;;
  interrupt)
    exit 130;;
  hang)
    trap 'touch "${FAKE_REENTRY_KILLED}"; exit 143' TERM
    sleep 30 &
    wait "$!" ;;
  *)
    exit 64;;
esac
"""


def make_chat(tmp_path: Path, mode: str = "ok", *, resolved: str | None = None) -> dict[str, Path]:
    bin_path = tmp_path / "hermes"
    bin_path.write_text(FAKE_CHAT)
    bin_path.chmod(0o755)
    files = {
        "bin": bin_path,
        "log": tmp_path / "argv.log",
        "captured": tmp_path / "captured.query",
        "killed": tmp_path / "killed.marker",
    }
    os.environ["FAKE_REENTRY_LOG"] = str(files["log"])
    os.environ["FAKE_REENTRY_MODE"] = mode
    os.environ["FAKE_REENTRY_CAPTURED"] = str(files["captured"])
    os.environ["FAKE_REENTRY_KILLED"] = str(files["killed"])
    os.environ["FAKE_REENTRY_ECHO_QUERY"] = "1"
    if resolved:
        os.environ["FAKE_REENTRY_RESOLVED"] = resolved
    else:
        os.environ.pop("FAKE_REENTRY_RESOLVED", None)
    return files


@pytest.fixture(autouse=True)
def _clean_fake_env():
    for key in (
        "FAKE_REENTRY_LOG",
        "FAKE_REENTRY_MODE",
        "FAKE_REENTRY_CAPTURED",
        "FAKE_REENTRY_KILLED",
        "FAKE_REENTRY_ECHO_QUERY",
        "FAKE_REENTRY_RESOLVED",
        "HERMES_HOME",
    ):
        os.environ.pop(key, None)
    yield
    for key in (
        "FAKE_REENTRY_LOG",
        "FAKE_REENTRY_MODE",
        "FAKE_REENTRY_CAPTURED",
        "FAKE_REENTRY_KILLED",
        "FAKE_REENTRY_ECHO_QUERY",
        "FAKE_REENTRY_RESOLVED",
        "HERMES_HOME",
    ):
        os.environ.pop(key, None)


def make_home(tmp_path: Path, sessions: list[tuple[str, str | None]], leases: list[tuple[str, str, float]]) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir(exist_ok=True)
    connection = sqlite3.connect(home / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT);
        CREATE TABLE session_turn_leases (
            conversation_id TEXT PRIMARY KEY, holder TEXT NOT NULL,
            acquired_at REAL, expires_at REAL
        );
        """
    )
    for sid, parent in sessions:
        connection.execute("INSERT INTO sessions VALUES (?, ?, NULL)", (sid, parent))
    for conversation_id, holder, expires_at in leases:
        connection.execute(
            "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
            (conversation_id, holder, expires_at - 300, expires_at),
        )
    connection.commit()
    connection.close()
    return home


def reentry(home: Path, tmp_path: Path, *, timeout: float = 8.0, popen_factory=None) -> HermesExactSessionReentry:
    files = make_chat(tmp_path)
    return HermesExactSessionReentry(
        str(files["bin"]),
        hermes_home=home,
        spool_root=tmp_path / "spool",
        timeout_seconds=timeout,
        popen_factory=popen_factory,
    )


def argv_lines(files: dict[str, Path]) -> list[str]:
    try:
        return files["log"].read_text().splitlines()
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# argv shape: exact session, source-confirmed flags, no soft modes — EVER
# ---------------------------------------------------------------------------


def test_exact_reentry_argv_shape_is_the_only_producible_form() -> None:
    argv = build_exact_reentry_argv("/usr/bin/hermes", "20260906_115225_08a2f1", "/tmp/s/q.txt")
    assert argv == ["/usr/bin/hermes", "chat", "-Q", "--resume", "20260906_115225_08a2f1", "--query-file", "/tmp/s/q.txt"]
    argv_profile = build_exact_reentry_argv(
        "/usr/bin/hermes", "20260906_115225_08a2f1", "/tmp/s/q.txt", profile="aota-task-main"
    )
    assert argv_profile[:3] == ["/usr/bin/hermes", "-p", "aota-task-main"]
    assert argv_profile[3:] == argv[1:]


@pytest.mark.parametrize(
    "forbidden",
    [
        "latest",  # Hermes' soft MRU resume keyword
        "-c",
        "--continue",
        "--create-if-missing",
        "@claude",
        "@codex",
        "",
        " ",
        "a b",
        "sess/../etc/passwd",
        "sess;rm -rf /",
        "-" + "x" * 130,
        "\x00session",
        "20260906_115225_08a2f1\nsecond-line",
    ],
)
def test_soft_and_injected_session_targets_are_structurally_unproducible(forbidden: str) -> None:
    with pytest.raises(HermesSessionReentryError):
        build_exact_reentry_argv("/usr/bin/hermes", forbidden, "/tmp/s/q.txt")
    with pytest.raises(HermesSessionReentryError):
        validate_exact_session_id(forbidden)


def test_argv_never_contains_forbidden_tokens_across_valid_inputs() -> None:
    # SOFT_RESUME_PATH_USED=no: even for every validly-shaped id, none of the
    # soft-resume/create-if-missing flags may appear in the composed argv.
    for sid in ("20260906_115225_08a2f1", "a", "A.b_c-1", "2026" * 8 + "e" * 2):
        argv = build_exact_reentry_argv("/bin/h", sid, "/tmp/f")
        joined = set(argv)
        assert joined.isdisjoint(
            {"--create-if-missing", "-c", "--continue", "-z", "--oneshot", "--usage-file", "latest"}
        )
        assert argv[argv.index("--resume") + 1] == sid


# ---------------------------------------------------------------------------
# missing session: fail closed, never a new session, never a launch
# ---------------------------------------------------------------------------


def test_missing_exact_session_fails_closed_without_launch(tmp_path: Path) -> None:
    home = make_home(tmp_path, [("20260101_000000_aaaaaa", None)], [])
    adapter = reentry(home, tmp_path)

    outcome = adapter.reenter("20991231_235959_zzzzzz", "completion CARD for task-main")

    assert outcome.outcome == "not_found"
    assert outcome.error_code == "HERMES_EXACT_SESSION_NOT_FOUND"
    assert outcome.exact_session_found is False
    assert outcome.turn_accepted is False
    assert outcome.retryable is False
    assert argv_lines(make_chat(tmp_path)) == [], "a missing session must never reach any launch"


def test_absent_session_store_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    adapter = reentry(home, tmp_path)
    outcome = adapter.reenter("20260906_115225_08a2f1", "CARD")
    assert outcome.outcome == "not_found"
    assert outcome.error_code == "HERMES_EXACT_SESSION_NOT_FOUND"


def test_unreadable_store_projects_unknown_not_missing_not_success(tmp_path: Path) -> None:
    home = tmp_path / "bogus-home"
    home.mkdir()
    (home / "state.db").write_bytes(b"this is definitely not sqlite")
    adapter = reentry(home, tmp_path)
    outcome = adapter.reenter("20260906_115225_08a2f1", "CARD")
    assert outcome.outcome == "unknown"


# ---------------------------------------------------------------------------
# successful exact re-entry + same-lineage redirect evidence
# ---------------------------------------------------------------------------


def test_exact_resume_completes_and_reports_lineage_target(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    adapter = reentry(home, tmp_path)

    outcome = adapter.reenter(session, "completion CARD for task-main")

    assert outcome.outcome == "completed"
    assert outcome.turn_accepted is True
    assert outcome.exact_session_found is True
    assert outcome.exit_code == 0
    assert outcome.resolved_session_id == session, "the CLI-reported lineage/session id is captured as evidence"
    assert "acknowledged" in (outcome.response_excerpt or "")
    lines = argv_lines({"log": Path(os.environ["FAKE_REENTRY_LOG"])})
    assert f"--resume {session}" in lines[0]
    assert "chat -Q" in lines[0]
    # payload transported via bounded file, verbatim
    captured = Path(os.environ["FAKE_REENTRY_CAPTURED"]).read_text()
    assert captured == "completion CARD for task-main"


def test_same_lineage_compressed_session_is_exact_recovery(tmp_path: Path) -> None:
    original = "20260901_000000_deadbe"
    descendant = "20260902_000000_cafe12"
    # resume-target row EXISTS (compression redirected to a descendant tip);
    # Hermes resolves within the SAME lineage. The adapter accepts the exact
    # id and surfaces the resolved id without substituting anything.
    home = make_home(tmp_path, [(original, None), (descendant, original)], [])
    adapter = reentry(home, tmp_path)
    outcome = adapter.reenter(original, "CARD")
    assert outcome.outcome == "completed"
    assert outcome.exact_session_found is True


# ---------------------------------------------------------------------------
# busy / lease semantics: typed retryable, no interrupt, no delivery claim
# ---------------------------------------------------------------------------


def test_live_lease_classifies_retryable_without_launch(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    holder = f"pid={os.getpid()}:turn=t1:platform=cli"
    home = make_home(tmp_path, [(session, None)], [(session, holder, time.time() + 300)])
    adapter = reentry(home, tmp_path)

    outcome = adapter.reenter(session, "CARD")

    assert outcome.outcome == "retryable"
    assert outcome.busy is True
    assert outcome.turn_accepted is False
    assert outcome.error_code == "HERMES_REENTRY_RETRYABLE"
    assert argv_lines(make_chat(tmp_path)) == [], "a busy session must not receive a second injected turn"


def test_lease_on_lineage_root_blocks_descendant_injection(tmp_path: Path) -> None:
    root = "20260901_000000_111111"
    tip = "20260902_000000_222222"
    holder = f"pid={os.getpid()}:turn=t2:platform=cli"
    home = make_home(tmp_path, [(root, None), (tip, root)], [(root, holder, time.time() + 300)])
    adapter = reentry(home, tmp_path)
    outcome = adapter.reenter(tip, "CARD")
    assert outcome.outcome == "retryable" and outcome.busy is True


def test_dead_lease_holder_does_not_block(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    # pid 0x7fffffff is not a live holder; Hermes reclaims expired/provably-dead
    # leases itself, so injection may proceed.
    home = make_home(tmp_path, [(session, None)], [(session, "pid=2147483646:turn=t:platform=cli", time.time() + 300)])
    adapter = reentry(home, tmp_path)
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "completed"


def test_cli_lease_timeout_sentinel_is_retryable(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    make_chat(tmp_path, "leasetimeout")
    adapter = HermesExactSessionReentry(
        str(tmp_path / "hermes"), hermes_home=home, spool_root=tmp_path / "spool"
    )
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "retryable"
    assert outcome.busy is True
    assert outcome.turn_accepted is False, "a timed-out bounded wait may never claim delivery"


def test_coordination_refusal_is_retryable(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    make_chat(tmp_path, "refuse")
    adapter = HermesExactSessionReentry(str(tmp_path / "hermes"), hermes_home=home, spool_root=tmp_path / "spool")
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "retryable" and outcome.busy is True


def test_raced_deletion_between_precheck_and_cli_is_not_found(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    make_chat(tmp_path, "notfound")
    adapter = HermesExactSessionReentry(str(tmp_path / "hermes"), hermes_home=home, spool_root=tmp_path / "spool")
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "not_found"
    assert outcome.exact_session_found is False


def test_hard_failure_is_typed_failed_not_retryable(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    make_chat(tmp_path, "hardfail")
    adapter = HermesExactSessionReentry(str(tmp_path / "hermes"), hermes_home=home, spool_root=tmp_path / "spool")
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "failed"
    assert outcome.error_code == "HERMES_REENTRY_FAILED"
    assert outcome.turn_accepted is False


# ---------------------------------------------------------------------------
# bounded timeout: only OUR injected process dies (FORCED_SESSION_INTERRUPT=no)
# ---------------------------------------------------------------------------


class FakeChatProcess:
    def __init__(self) -> None:
        self._stdout = type("S", (), {"read": lambda self, n=-1: b""})()
        self._stderr = type("S", (), {"read": lambda self, n=-1: b""})()
        self.stdout = self._stdout
        self.stderr = self._stderr
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (timeout or 0.05)
        while not self.terminated and not self.killed:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("fake-chat", timeout)
            time.sleep(0.01)
        return -15

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_bounded_wait_kills_only_the_injected_turn_process(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    fake = FakeChatProcess()

    def factory(args, **kwargs):
        assert "--query-file" in args
        # simulate an origin-session owner that must never be touched
        return fake

    adapter = HermesExactSessionReentry(
        str(make_chat(tmp_path, "hang")["bin"]),
        hermes_home=home,
        spool_root=tmp_path / "spool",
        timeout_seconds=0.2,
        popen_factory=factory,
    )
    # Patch stream attributes post-construction inside _run via duck typing.
    outcome = adapter.reenter(session, "CARD")

    assert outcome.outcome == "retryable"
    assert outcome.busy is True
    assert fake.terminated is True or fake.killed is True, "only OUR injected process is terminated"
    assert outcome.turn_accepted is False, "a timed-out injection may never claim a turn"


def test_interrupted_exit_is_retryable(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    make_chat(tmp_path, "interrupt")
    adapter = HermesExactSessionReentry(str(tmp_path / "hermes"), hermes_home=home, spool_root=tmp_path / "spool")
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "retryable"
    assert outcome.exit_code == 130


# ---------------------------------------------------------------------------
# bounded payload seam + no delivery/ACK state
# ---------------------------------------------------------------------------


def test_payload_bounds_and_cleanup(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    adapter = reentry(home, tmp_path)

    with pytest.raises(HermesSessionReentryError):
        adapter.reenter(session, "x" * (70 * 1024))
    with pytest.raises(HermesSessionReentryError):
        adapter.reenter(session, "has\x00nul")
    with pytest.raises(HermesSessionReentryError):
        adapter.reenter(session, "   ")

    outcome = adapter.reenter(session, "CARD " * 10)
    assert outcome.outcome == "completed"
    spool = tmp_path / "spool"
    assert list(spool.glob("*.query")) == [], "the bounded query file never lingers"


def test_result_model_carries_no_ack_or_delivery_state(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    adapter = reentry(home, tmp_path)
    outcome = adapter.reenter(session, "CARD")
    mapping = outcome.to_mapping()
    assert set(mapping) == {
        "outcome",
        "retryable",
        "exact_session_found",
        "turn_accepted",
        "busy",
        "exit_code",
        "session_id",
        "resolved_session_id",
        "error_code",
        "error_message",
        "response_excerpt",
        "stderr_excerpt",
    }
    assert outcome.turn_accepted is True
    # DELIVERY_ACK_IMPLEMENTED / state machine fields must not exist here.
    assert not hasattr(outcome, "delivered")
    assert not hasattr(outcome, "ack")
    assert not hasattr(outcome, "attempts")


def test_profile_binding_is_operator_supplied_and_argv_bounded(tmp_path: Path) -> None:
    session = "20260906_115225_08a2f1"
    home = make_home(tmp_path, [(session, None)], [])
    files = make_chat(tmp_path, "ok")
    adapter = HermesExactSessionReentry(
        str(files["bin"]),
        hermes_home=home,
        profile="aota-task-main",
        spool_root=tmp_path / "spool",
    )
    outcome = adapter.reenter(session, "CARD")
    assert outcome.outcome == "completed"
    line = argv_lines(files)[0]
    assert line.startswith(f"{files['bin']} -p aota-task-main chat -Q --resume {session} --query-file ")
    # a profile containing shell/flag smuggling is rejected
    with pytest.raises(HermesSessionReentryError):
        HermesExactSessionReentry(str(files["bin"]), hermes_home=home, profile="-x a").reenter(session, "CARD")
