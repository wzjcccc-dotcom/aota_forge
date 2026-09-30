"""Trusted per-invocation bridge for the headed OpenCode MCP path.

The bridge daemon is the only owner of the HMAC secret and one-time nonce set.
OpenCode and the AF MCP child use a bounded Unix-socket protocol to issue and
verify credentials; neither side receives the secret itself.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import socketserver
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from aota_forge.core.contracts.canonical import canonical_json

BRIDGE_TOKEN_PREFIX = "aota-bridge-v1"
BRIDGE_ISSUER = "aota.trusted-opencode-plugin"
BRIDGE_AUDIENCE = "aota.mcp.verify"
BRIDGE_TOOL = "aota_aota_invoke"
BRIDGE_CARRIER_KEY = "__aota_trusted_invocation"
BRIDGE_CARRIER_SCHEMA = BRIDGE_TOKEN_PREFIX
BRIDGE_TOKEN_MAX_BYTES = 4096
BRIDGE_TOKEN_TTL_SECONDS = 10
BRIDGE_CLOCK_SKEW_SECONDS = 2
BRIDGE_SOCKET_MAX_BYTES = 16 * 1024
BRIDGE_SOCKET_ENV = "AOTA_BRIDGE_SOCKET"

_PAYLOAD_FIELDS = frozenset(
    {
        "v",
        "iss",
        "aud",
        "bridge_id",
        "session_id",
        "call_id",
        "tool",
        "args_sha256",
        "nonce",
        "iat",
        "exp",
    }
)


class BridgeVerificationError(ValueError):
    """Bounded fail-closed bridge error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class VerifiedInvocationIdentity:
    """Ephemeral identity for exactly one verified dispatch."""

    session_id: str
    call_id: str
    bridge_id: str
    tool: str
    args_sha256: str
    nonce: str


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: object, *, label: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > BRIDGE_TOKEN_MAX_BYTES:
        raise BridgeVerificationError("TOKEN_MALFORMED", f"{label} is malformed")
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in value):
        raise BridgeVerificationError("TOKEN_MALFORMED", f"{label} is malformed")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise BridgeVerificationError("TOKEN_MALFORMED", f"{label} is malformed") from None


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        raise BridgeVerificationError("TOKEN_IDENTITY_INVALID", f"{label} is invalid")
    return value


def invocation_digest(operation: str, arguments: Mapping[str, Any]) -> str:
    """Digest the exact logical AOTA invocation before carrier removal."""
    if not isinstance(operation, str) or not operation:
        raise BridgeVerificationError("INVOCATION_INVALID", "operation is required")
    if not isinstance(arguments, Mapping):
        raise BridgeVerificationError("INVOCATION_INVALID", "arguments must be an object")
    try:
        encoded = canonical_json({"operation": operation, "arguments": dict(arguments)})
    except (TypeError, ValueError) as exc:
        raise BridgeVerificationError("INVOCATION_INVALID", str(exc)[:256]) from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract_carrier(arguments: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Return logical arguments and the one reserved carrier token."""
    if not isinstance(arguments, Mapping):
        raise BridgeVerificationError("INVOCATION_INVALID", "arguments must be an object")
    logical = dict(arguments)
    carrier = logical.pop(BRIDGE_CARRIER_KEY, None)
    if not isinstance(carrier, Mapping):
        raise BridgeVerificationError("TOKEN_MISSING", "trusted invocation carrier is required")
    if set(carrier) != {"schema", "token"}:
        raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation carrier fields are invalid")
    if carrier.get("schema") != BRIDGE_CARRIER_SCHEMA:
        raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation carrier schema is invalid")
    token = carrier.get("token")
    if not isinstance(token, str) or not token:
        raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation token is invalid")
    return logical, token


class InvocationBridge:
    """In-memory issuer/verifier used by the Unix-socket bridge service."""

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        bridge_id: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._bridge_id = bridge_id or _b64_encode(secrets.token_bytes(16))
        self._clock = clock or time.time
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    @property
    def bridge_id(self) -> str:
        return self._bridge_id

    def rotate(self) -> str:
        """Start a new child epoch and invalidate every old credential."""
        with self._lock:
            self._secret = secrets.token_bytes(32)
            self._bridge_id = _b64_encode(secrets.token_bytes(16))
            self._consumed.clear()
            return self._bridge_id

    def issue(self, session_id: str, call_id: str, operation: str, arguments: Mapping[str, Any]) -> str:
        session_id = _bounded_text(session_id, "session_id")
        call_id = _bounded_text(call_id, "call_id")
        args_sha256 = invocation_digest(operation, arguments)
        with self._lock:
            issued_at = int(self._clock())
            payload = {
                "v": 1,
                "iss": BRIDGE_ISSUER,
                "aud": BRIDGE_AUDIENCE,
                "bridge_id": self._bridge_id,
                "session_id": session_id,
                "call_id": call_id,
                "tool": BRIDGE_TOOL,
                "args_sha256": args_sha256,
                "nonce": secrets.token_hex(16),
                "iat": issued_at,
                "exp": issued_at + BRIDGE_TOKEN_TTL_SECONDS,
            }
            encoded_payload = _b64_encode(canonical_json(payload).encode("utf-8"))
            mac = hmac.new(
                self._secret,
                f"{BRIDGE_TOKEN_PREFIX}.{encoded_payload}".encode("ascii"),
                hashlib.sha256,
            ).digest()
        token = f"{BRIDGE_TOKEN_PREFIX}.{encoded_payload}.{_b64_encode(mac)}"
        if len(token.encode("utf-8")) > BRIDGE_TOKEN_MAX_BYTES:
            raise BridgeVerificationError("TOKEN_TOO_LARGE", "trusted invocation token exceeds its bound")
        return token

    def verify(
        self,
        token: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        expected_session_id: str | None = None,
        expected_call_id: str | None = None,
    ) -> VerifiedInvocationIdentity:
        if not isinstance(token, str) or len(token.encode("utf-8")) > BRIDGE_TOKEN_MAX_BYTES:
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation token is invalid")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != BRIDGE_TOKEN_PREFIX:
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation token has an invalid shape")
        encoded_payload = parts[1]
        supplied_mac = _b64_decode(parts[2], label="token mac")
        with self._lock:
            expected_mac = hmac.new(
                self._secret,
                f"{BRIDGE_TOKEN_PREFIX}.{encoded_payload}".encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_mac, expected_mac):
                raise BridgeVerificationError("TOKEN_FORGERY", "trusted invocation MAC is invalid")
            current_bridge_id = self._bridge_id
        payload_raw = _b64_decode(encoded_payload, label="token payload")
        try:
            payload = json.loads(payload_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation payload is invalid") from None
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation payload fields are invalid")
        if payload.get("v") != 1 or payload.get("iss") != BRIDGE_ISSUER or payload.get("aud") != BRIDGE_AUDIENCE:
            raise BridgeVerificationError("TOKEN_AUDIENCE_DENIED", "trusted invocation audience is invalid")
        if payload.get("bridge_id") != current_bridge_id:
            raise BridgeVerificationError("TOKEN_EPOCH_STALE", "trusted invocation belongs to a stale bridge epoch")
        session_id = _bounded_text(payload.get("session_id"), "session_id")
        call_id = _bounded_text(payload.get("call_id"), "call_id")
        if payload.get("tool") != BRIDGE_TOOL:
            raise BridgeVerificationError("TOKEN_TOOL_MISMATCH", "trusted invocation tool is invalid")
        args_sha256 = payload.get("args_sha256")
        if not isinstance(args_sha256, str) or len(args_sha256) != 64 or any(c not in "0123456789abcdef" for c in args_sha256):
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation digest is invalid")
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation nonce is invalid")
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        if isinstance(issued_at, bool) or not isinstance(issued_at, int) or isinstance(expires_at, bool) or not isinstance(expires_at, int):
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation time fields are invalid")
        if expires_at > issued_at + BRIDGE_TOKEN_TTL_SECONDS:
            raise BridgeVerificationError("TOKEN_MALFORMED", "trusted invocation lifetime exceeds its bound")
        now = int(self._clock())
        if now < issued_at - BRIDGE_CLOCK_SKEW_SECONDS or now > expires_at + BRIDGE_CLOCK_SKEW_SECONDS:
            raise BridgeVerificationError("TOKEN_EXPIRED", "trusted invocation token is expired or not yet valid")
        if expected_session_id is not None and session_id != expected_session_id:
            raise BridgeVerificationError("TOKEN_SESSION_MISMATCH", "trusted invocation session differs")
        if expected_call_id is not None and call_id != expected_call_id:
            raise BridgeVerificationError("TOKEN_CALL_MISMATCH", "trusted invocation call differs")
        if invocation_digest(operation, arguments) != args_sha256:
            raise BridgeVerificationError("TOKEN_ARGUMENT_MISMATCH", "trusted invocation arguments differ")
        with self._lock:
            if self._bridge_id != current_bridge_id:
                raise BridgeVerificationError("TOKEN_EPOCH_STALE", "trusted invocation belongs to a stale bridge epoch")
            if nonce in self._consumed:
                raise BridgeVerificationError("TOKEN_REPLAY", "trusted invocation token was already consumed")
            self._consumed.add(nonce)
        return VerifiedInvocationIdentity(
            session_id=session_id,
            call_id=call_id,
            bridge_id=current_bridge_id,
            tool=BRIDGE_TOOL,
            args_sha256=args_sha256,
            nonce=nonce,
        )


class BridgeSocketClient:
    """Bounded client for the operator-owned Unix-socket bridge daemon."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 2.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout = timeout

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
        if len(encoded) > BRIDGE_SOCKET_MAX_BYTES:
            raise BridgeVerificationError("BRIDGE_REQUEST_TOO_LARGE", "bridge request exceeds its bound")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout)
                client.connect(self.socket_path)
                client.sendall(encoded)
                chunks: list[bytes] = []
                total = 0
                while total <= BRIDGE_SOCKET_MAX_BYTES:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if b"\n" in chunk:
                        break
        except (OSError, TimeoutError) as exc:
            raise BridgeVerificationError("BRIDGE_UNAVAILABLE", "trusted bridge IPC is unavailable") from exc
        raw = b"".join(chunks).split(b"\n", 1)[0]
        if not raw or len(raw) > BRIDGE_SOCKET_MAX_BYTES:
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge returned no bounded response")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge response is malformed") from exc
        if not isinstance(response, dict):
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge response is not an object")
        if response.get("ok") is not True:
            error = response.get("error")
            if isinstance(error, dict):
                raise BridgeVerificationError(str(error.get("code") or "BRIDGE_REJECTED"), str(error.get("message") or "trusted bridge rejected request"))
            raise BridgeVerificationError("BRIDGE_REJECTED", "trusted bridge rejected request")
        return response

    def issue(self, session_id: str, call_id: str, operation: str, arguments: Mapping[str, Any]) -> str:
        response = self._request(
            {
                "op": "issue",
                "session_id": session_id,
                "call_id": call_id,
                "operation": operation,
                "arguments": dict(arguments),
            }
        )
        token = response.get("token")
        if not isinstance(token, str):
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge returned no token")
        return token

    def verify(self, token: str, operation: str, arguments: Mapping[str, Any]) -> VerifiedInvocationIdentity:
        response = self._request(
            {"op": "verify", "token": token, "operation": operation, "arguments": dict(arguments)}
        )
        identity = response.get("identity")
        if not isinstance(identity, dict):
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge returned no identity")
        try:
            return VerifiedInvocationIdentity(
                session_id=str(identity["session_id"]),
                call_id=str(identity["call_id"]),
                bridge_id=str(identity["bridge_id"]),
                tool=str(identity["tool"]),
                args_sha256=str(identity["args_sha256"]),
                nonce=str(identity["nonce"]),
            )
        except (KeyError, TypeError) as exc:
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge identity is malformed") from exc

    def rotate(self) -> str:
        response = self._request({"op": "rotate"})
        bridge_id = response.get("bridge_id")
        if not isinstance(bridge_id, str) or not bridge_id:
            raise BridgeVerificationError("BRIDGE_PROTOCOL_ERROR", "trusted bridge returned no epoch")
        return bridge_id


class _BridgeRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # pragma: no cover - exercised by socket integration tests
        if not _same_user_peer(self.request):
            self._write_error("BRIDGE_PEER_DENIED", "bridge peer credentials are not trusted")
            return
        raw = self.rfile.readline(BRIDGE_SOCKET_MAX_BYTES + 1)
        if not raw or len(raw) > BRIDGE_SOCKET_MAX_BYTES:
            self._write_error("BRIDGE_REQUEST_TOO_LARGE", "bridge request exceeds its bound")
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request must be an object")
            authority: InvocationBridge = self.server.authority  # type: ignore[attr-defined]
            op = payload.get("op")
            if op == "issue":
                token = authority.issue(payload["session_id"], payload["call_id"], payload["operation"], payload["arguments"])
                response: dict[str, Any] = {"ok": True, "token": token, "bridge_id": authority.bridge_id}
            elif op == "verify":
                identity = authority.verify(payload["token"], payload["operation"], payload["arguments"])
                evidence_path = getattr(self.server, "evidence_path", None)  # type: ignore[attr-defined]
                if evidence_path:
                    with open(evidence_path, "a", encoding="utf-8") as evidence:
                        evidence.write(
                            canonical_json(
                                {
                                    "bridge_id": identity.bridge_id,
                                    "session_id": identity.session_id,
                                    "call_id": identity.call_id,
                                    "tool": identity.tool,
                                    "args_sha256": identity.args_sha256,
                                }
                            )
                            + "\n"
                        )
                response = {"ok": True, "identity": identity.__dict__}
            elif op == "rotate":
                response = {"ok": True, "bridge_id": authority.rotate()}
            else:
                raise BridgeVerificationError("BRIDGE_OPERATION_INVALID", "unknown bridge operation")
        except BridgeVerificationError as exc:
            self._write_error(exc.code, str(exc))
            return
        except (KeyError, TypeError, ValueError) as exc:
            self._write_error("BRIDGE_REQUEST_INVALID", str(exc)[:256])
            return
        self.wfile.write((canonical_json(response) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _write_error(self, code: str, message: str) -> None:
        self.wfile.write((canonical_json({"ok": False, "error": {"code": code, "message": message[:256]}}) + "\n").encode("utf-8"))
        self.wfile.flush()


class _BridgeSocketServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, authority: InvocationBridge, evidence_path: str | None = None) -> None:
        self.authority = authority
        self.evidence_path = evidence_path
        super().__init__(socket_path, _BridgeRequestHandler)


def _same_user_peer(sock: socket.socket) -> bool:
    if not hasattr(socket, "SO_PEERCRED"):
        return False
    try:
        _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid == os.getuid()
    except OSError:
        return False


def serve_bridge_socket(socket_path: str | Path, *, authority: InvocationBridge | None = None) -> None:
    path = Path(socket_path)
    if path.exists():
        if not path.is_socket():
            raise BridgeVerificationError("BRIDGE_SOCKET_INVALID", "bridge socket path is not a socket")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    server = _BridgeSocketServer(
        str(path),
        authority or InvocationBridge(),
        evidence_path=os.environ.get("AOTA_BRIDGE_EVIDENCE_PATH") or None,
    )
    os.chmod(path, 0o600)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:  # pragma: no cover - exercised by bounded process integration
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    serve_bridge_socket(args.socket)


if __name__ == "__main__":
    main()


__all__ = [
    "BRIDGE_AUDIENCE",
    "BRIDGE_CARRIER_KEY",
    "BRIDGE_CARRIER_SCHEMA",
    "BRIDGE_ISSUER",
    "BRIDGE_SOCKET_ENV",
    "BRIDGE_TOOL",
    "BRIDGE_TOKEN_MAX_BYTES",
    "BRIDGE_TOKEN_TTL_SECONDS",
    "BridgeSocketClient",
    "BridgeVerificationError",
    "InvocationBridge",
    "VerifiedInvocationIdentity",
    "extract_carrier",
    "invocation_digest",
    "serve_bridge_socket",
]
