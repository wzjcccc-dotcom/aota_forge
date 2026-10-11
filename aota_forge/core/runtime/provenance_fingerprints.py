"""Runtime identity fingerprints."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .provenance_helpers import coalesce_fingerprint, is_unknown_fingerprint

@dataclass(frozen=True, init=False)
class RuntimeFingerprintSet:
    source: str | None
    configured: str | None
    deployed: str | None
    running: str | None
    def __init__(self, source: Any = None, configured: Any = None, deployed: Any = None, running: Any = None, *, model_source: Any = None, source_fingerprint: Any = None, model_source_fingerprint: Any = None, configured_fingerprint: Any = None, deployed_fingerprint: Any = None, running_fingerprint: Any = None) -> None:
        object.__setattr__(self, "source", coalesce_fingerprint("source fingerprint", (source, model_source, source_fingerprint, model_source_fingerprint)))
        object.__setattr__(self, "configured", coalesce_fingerprint("configured fingerprint", (configured, configured_fingerprint)))
        object.__setattr__(self, "deployed", coalesce_fingerprint("deployed fingerprint", (deployed, deployed_fingerprint)))
        object.__setattr__(self, "running", coalesce_fingerprint("running fingerprint", (running, running_fingerprint)))
    source_fingerprint = property(lambda self: self.source)
    model_source_fingerprint = property(lambda self: self.source)
    configured_fingerprint = property(lambda self: self.configured)
    deployed_fingerprint = property(lambda self: self.deployed)
    running_fingerprint = property(lambda self: self.running)
    @property
    def complete(self) -> bool:
        return all(value is not None and not is_unknown_fingerprint(value) for value in self.to_tuple())
    def to_tuple(self) -> tuple[str | None, str | None, str | None, str | None]:
        return self.source, self.configured, self.deployed, self.running
    def to_dict(self) -> dict[str, Any]:
        return {"source_fingerprint": self.source, "configured_fingerprint": self.configured, "deployed_fingerprint": self.deployed, "running_fingerprint": self.running}

RuntimeIdentityFingerprints = RuntimeFingerprintSet
