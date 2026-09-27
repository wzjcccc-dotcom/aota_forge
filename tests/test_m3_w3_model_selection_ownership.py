"""M3/W3 guard for profile-owned provider/model selection.

Semantic and operational runner code may select an AF host profile, but it must
not pin a provider/model or pass one as a per-invocation override. Concrete
selection belongs to the operator-owned host/profile configuration and the
existing adapter translation boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_RUNNERS = (
    REPO_ROOT / "scripts" / "m3_w2_real_task_main_vertical_slice.py",
    REPO_ROOT / "scripts" / "m3_w3_integration_smoke.py",
)

SEMANTIC_PATHS = (
    REPO_ROOT / "aota_forge" / "core",
    REPO_ROOT / "aota_forge" / "work_plane",
    REPO_ROOT / "aota_forge" / "runtime" / "task_main",
)

LEGACY_SELECTION_LITERALS = ("opencode-go", "deepseek-v4-flash")


def _python_sources(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_active_runners_are_profile_only() -> None:
    for path in ACTIVE_RUNNERS:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(?:PROVIDER|MODEL)\s*=", text), path
        assert "--provider" not in text, path
        assert not re.search(r"(?:\"provider\"|\"model\")\s*:\s*(?:PROVIDER|MODEL)", text), path
        assert not any(literal in text for literal in LEGACY_SELECTION_LITERALS), path
        assert '"-p", TASK_MAIN_PROFILE' in text, path


def test_semantic_paths_do_not_carry_legacy_deployment_selection() -> None:
    for root in SEMANTIC_PATHS:
        for path in _python_sources(root):
            text = path.read_text(encoding="utf-8")
            assert not any(literal in text for literal in LEGACY_SELECTION_LITERALS), path


def test_runner_operator_configs_explicitly_defer_model_selection() -> None:
    for path in ACTIVE_RUNNERS:
        text = path.read_text(encoding="utf-8")
        assert '"provider": None' in text, path
        assert '"model": None' in text, path
