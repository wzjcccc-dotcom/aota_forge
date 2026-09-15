"""AF #57 M1/W3 — bounded composition seam for the Project Governance Store.

One local factory that returns the SQLite-backed store behind the
storage-neutral port.  This is W3's own seam; cross-cutting runtime wiring
(task-main host, AuthorizedRoots, Plan authority adapters) is W4 and is
deliberately not touched here, keeping W2/W3 physically parallel.

No configuration ceremony: no backend marketplace, no speculative options.
"""

from __future__ import annotations

from pathlib import Path

from aota_forge.governance.project_store import ProjectGovernanceStore
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore

PROJECT_GOVERNANCE_STORE_DEFAULT_BACKEND = "sqlite"
PROJECT_GOVERNANCE_STORE_LOCAL_ONLY = True


def open_project_governance_store(storage_path: str | Path) -> ProjectGovernanceStore:
    """Open the local SQLite Project Governance Store for ``storage_path``."""
    return SQLiteProjectGovernanceStore(storage_path)
