"""Canonical machine-readable error contract (M1-G).

Error codes are stable bounded strings.  Legacy executor-specific errors
(SPEC_NOT_APPROVED / PROFILE_FORBIDDEN / STALE_WORKER) are NOT part of Core;
an adapter may map them at an executor boundary later.
"""

from __future__ import annotations

from typing import Optional


class ForgeError(Exception):
    """Base error for executor-neutral Forge Core failures."""

    code: str = "FORGE_ERROR"

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class ProjectNotFoundError(ForgeError):
    def __init__(self, message: str = "project not found", retryable: bool = False) -> None:
        super().__init__("PROJECT_NOT_FOUND", message, retryable)


class ProjectAmbiguousError(ForgeError):
    def __init__(self, message: str = "project resolution is ambiguous", retryable: bool = False) -> None:
        super().__init__("PROJECT_AMBIGUOUS", message, retryable)


class ProjectManifestInvalidError(ForgeError):
    def __init__(self, message: str = "project manifest is invalid", detail: str | None = None, retryable: bool = False) -> None:
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__("PROJECT_MANIFEST_INVALID", message, retryable)


class ProjectRegistryInvalidError(ForgeError):
    def __init__(self, message: str = "project registry is invalid", retryable: bool = False) -> None:
        super().__init__("PROJECT_REGISTRY_INVALID", message, retryable)


class GitNotFoundError(ForgeError):
    def __init__(self, message: str = "git repository not found within boundary", retryable: bool = False) -> None:
        super().__init__("GIT_NOT_FOUND", message, retryable)


class GitBoundaryViolationError(ForgeError):
    def __init__(self, message: str = "git search crossed the resolved project boundary", retryable: bool = False) -> None:
        super().__init__("GIT_BOUNDARY_VIOLATION", message, retryable)


class RuntimeNotRunningError(ForgeError):
    def __init__(self, message: str = "runtime is not running", retryable: bool = False) -> None:
        super().__init__("RUNTIME_NOT_RUNNING", message, retryable)


class RuntimeIdentityUnavailableError(ForgeError):
    def __init__(self, message: str = "runtime identity is unavailable", retryable: bool = False) -> None:
        super().__init__("RUNTIME_IDENTITY_UNAVAILABLE", message, retryable)


class ReceiptInvalidError(ForgeError):
    def __init__(self, message: str = "receipt is invalid", retryable: bool = False) -> None:
        super().__init__("RECEIPT_INVALID", message, retryable)


class SourceParityMismatchError(ForgeError):
    def __init__(self, message: str = "source/runtime parity mismatch", retryable: bool = True) -> None:
        super().__init__("SOURCE_PARITY_MISMATCH", message, retryable)


class UnsupportedOperationError(ForgeError):
    def __init__(self, message: str = "unsupported operation", retryable: bool = False) -> None:
        super().__init__("UNSUPPORTED_OPERATION", message, retryable)


class ContractVersionMismatchError(ForgeError):
    def __init__(self, message: str = "contract version mismatch", retryable: bool = False) -> None:
        super().__init__("CONTRACT_VERSION_MISMATCH", message, retryable)


class WorkspaceError(ForgeError):
    """Workspace registry-level failure, kept executor-neutral."""

    def __init__(self, message: str = "workspace error", retryable: bool = False) -> None:
        super().__init__("WORKSPACE_ERROR", message, retryable)


ERROR_CLASSES: dict[str, type[ForgeError]] = {
    cls.code: cls
    for cls in (
        ProjectNotFoundError,
        ProjectAmbiguousError,
        ProjectManifestInvalidError,
        ProjectRegistryInvalidError,
        GitNotFoundError,
        GitBoundaryViolationError,
        RuntimeNotRunningError,
        RuntimeIdentityUnavailableError,
        ReceiptInvalidError,
        SourceParityMismatchError,
        UnsupportedOperationError,
        ContractVersionMismatchError,
    )
}


def forge_error_to_dict(exc: ForgeError) -> dict[str, object]:
    return exc.to_dict()


def error_from_dict(payload: Optional[dict[str, object]]) -> Optional[ForgeError]:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    cls = ERROR_CLASSES.get(code if isinstance(code, str) else "")
    if cls is None:
        return ForgeError(str(code or "FORGE_ERROR"), str(payload.get("message") or "unknown error"), bool(payload.get("retryable")))
    return cls(str(payload.get("message") or cls.__name__), bool(payload.get("retryable")))
