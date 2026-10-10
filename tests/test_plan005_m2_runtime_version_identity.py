from __future__ import annotations

from pathlib import Path

import aota_forge
from aota_forge.core.runtime.inspect import version_identity


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_source_version_file_matches_canonical_package_version() -> None:
    version_file = REPO_ROOT / "VERSION"
    assert version_file.read_text(encoding="utf-8") == f"{aota_forge.__version__}\n"


def test_version_identity_reads_the_canonical_source_version() -> None:
    identity = version_identity(REPO_ROOT)
    assert identity.name == "aota-forge"
    assert identity.version == aota_forge.__version__ == "0.1.0"
