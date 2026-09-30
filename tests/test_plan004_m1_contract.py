"""Plan #004 M1 bounded token contract proof.

This is deliberately a proof harness, not the production Session Bridge.
It exercises the frozen HMAC shape, exact call/session/argument binding,
restart invalidation and atomic one-time consumption with standard-library
code only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass
class _ProofTokenAuthority:
    secret: bytes = b"plan004-contract-proof-secret"
    bridge_id: str = "bridge-epoch-1"
    now: int = 1_000

    def __post_init__(self) -> None:
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    def issue(self, session_id: str, call_id: str, logical_args: dict, *, ttl: int = 10) -> str:
        payload = {
            "v": 1,
            "iss": "aota.trusted-opencode-plugin",
            "aud": "aota.mcp.verify",
            "bridge_id": self.bridge_id,
            "session_id": session_id,
            "call_id": call_id,
            "tool": "aota_aota_invoke",
            "args_sha256": _digest(logical_args),
            "nonce": secrets.token_hex(16),
            "iat": self.now,
            "exp": self.now + ttl,
        }
        encoded = _b64(_canonical(payload))
        mac = hmac.new(self.secret, f"aota-bridge-v1.{encoded}".encode("ascii"), hashlib.sha256).digest()
        return f"aota-bridge-v1.{encoded}.{_b64(mac)}"

    def restart(self) -> None:
        self.secret = hashlib.sha256(self.secret + b":restart").digest()
        self.bridge_id = "bridge-epoch-2"

    def consume(self, token: str | None, session_id: str, call_id: str, logical_args: dict) -> tuple[str, str]:
        if not token:
            raise ValueError("missing token")
        try:
            prefix, encoded, supplied_mac = token.split(".", 2)
            payload = json.loads(_unb64(encoded))
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("malformed token") from None
        if prefix != "aota-bridge-v1":
            raise ValueError("wrong token version")
        expected_mac = hmac.new(
            self.secret,
            f"aota-bridge-v1.{encoded}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        try:
            if not hmac.compare_digest(_unb64(supplied_mac), expected_mac):
                raise ValueError("invalid mac")
        except (ValueError, TypeError):
            raise ValueError("invalid mac") from None
        if payload.get("bridge_id") != self.bridge_id:
            raise ValueError("stale bridge epoch")
        if payload.get("iss") != "aota.trusted-opencode-plugin" or payload.get("aud") != "aota.mcp.verify":
            raise ValueError("wrong audience")
        if not (payload.get("iat", 0) <= self.now <= payload.get("exp", 0)):
            raise ValueError("expired token")
        if payload.get("session_id") != session_id or payload.get("call_id") != call_id:
            raise ValueError("identity mismatch")
        if payload.get("tool") != "aota_aota_invoke" or payload.get("args_sha256") != _digest(logical_args):
            raise ValueError("invocation mismatch")
        nonce = payload.get("nonce")
        if not isinstance(nonce, str):
            raise ValueError("missing nonce")
        with self._lock:
            if nonce in self._consumed:
                raise ValueError("replayed token")
            self._consumed.add(nonce)
        return session_id, call_id


def test_contract_rejects_missing_forged_expired_and_mismatched_tokens() -> None:
    authority = _ProofTokenAuthority()
    logical = {"operation": "workspace.search", "arguments": {"query": "a"}}
    token = authority.issue("ses-A", "call-A", logical)

    with pytest.raises(ValueError, match="missing"):
        authority.consume(None, "ses-A", "call-A", logical)
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ValueError, match="invalid mac"):
        authority.consume(forged, "ses-A", "call-A", logical)
    with pytest.raises(ValueError, match="identity mismatch"):
        authority.consume(token, "ses-B", "call-A", logical)
    with pytest.raises(ValueError, match="invocation mismatch"):
        authority.consume(token, "ses-A", "call-A", {"operation": "workspace.read", "arguments": {}})

    expired = authority.issue("ses-A", "call-expired", logical, ttl=-1)
    with pytest.raises(ValueError, match="expired"):
        authority.consume(expired, "ses-A", "call-expired", logical)


def test_contract_consumes_each_token_once_and_rejects_replay() -> None:
    authority = _ProofTokenAuthority()
    logical = {"operation": "workspace.search", "arguments": {"query": "needle"}}
    token = authority.issue("ses-A", "call-A", logical)
    assert authority.consume(token, "ses-A", "call-A", logical) == ("ses-A", "call-A")
    with pytest.raises(ValueError, match="replayed"):
        authority.consume(token, "ses-A", "call-A", logical)


def test_contract_uses_utf8_and_rejects_non_finite_numbers() -> None:
    authority = _ProofTokenAuthority()
    logical = {"operation": "workspace.search", "arguments": {"query": "測試"}}
    token = authority.issue("ses-A", "call-unicode", logical)
    assert authority.consume(token, "ses-A", "call-unicode", logical) == ("ses-A", "call-unicode")
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        _canonical({"value": float("nan")})


def test_concurrent_interleaving_is_identity_bound_not_fifo_bound() -> None:
    authority = _ProofTokenAuthority()
    logical_a = {"operation": "workspace.search", "arguments": {"query": "A"}}
    logical_b = {"operation": "workspace.search", "arguments": {"query": "B"}}
    token_a = authority.issue("ses-A", "call-A", logical_a)
    token_b = authority.issue("ses-B", "call-B", logical_b)
    barrier = threading.Barrier(2)

    def consume(token: str, session_id: str, call_id: str, logical_args: dict) -> tuple[str, str]:
        barrier.wait()
        return authority.consume(token, session_id, call_id, logical_args)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(consume, token_b, "ses-B", "call-B", logical_b),
            executor.submit(consume, token_a, "ses-A", "call-A", logical_a),
        ]
        assert {future.result() for future in futures} == {
            ("ses-A", "call-A"),
            ("ses-B", "call-B"),
        }


def test_restart_rotates_epoch_and_invalidates_old_credentials() -> None:
    authority = _ProofTokenAuthority()
    logical = {"operation": "workspace.read", "arguments": {"path": "x"}}
    old = authority.issue("ses-A", "call-A", logical)
    authority.restart()
    with pytest.raises(ValueError, match="invalid mac|stale bridge epoch"):
        authority.consume(old, "ses-A", "call-A", logical)
    fresh = authority.issue("ses-A", "call-B", logical)
    assert authority.consume(fresh, "ses-A", "call-B", logical) == ("ses-A", "call-B")


def test_atomic_consumption_allows_one_winner_for_a_replayed_race() -> None:
    authority = _ProofTokenAuthority()
    logical = {"operation": "workspace.search", "arguments": {"query": "race"}}
    token = authority.issue("ses-A", "call-race", logical)
    barrier = threading.Barrier(8)

    def attempt() -> bool:
        barrier.wait()
        try:
            authority.consume(token, "ses-A", "call-race", logical)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: attempt(), range(8)))
    assert results.count(True) == 1
    assert results.count(False) == 7
