"""AF #57 M3/W1 bounded repair for governed Plan mutation safety."""

from __future__ import annotations

import pytest

from aota_forge.work_plane.github_tools import (
    _is_portable_plan_body,
    _portable_plan_replacement_error,
)

from test_af59_m2_bootstrap_skill_help_safety import _bound_plan_ref_host


# Compact fixture preserving the live #57 shape: the normative identity and
# lifecycle fields live in a Governance 2.0 section, while a managed current
# state subsection is updated mechanically below it.
GOVERNANCE_57_LIKE_BODY = """# [AF][ARCH] Project Governance Subsystem 2.0

```text
PLAN_TYPE=portable_plan
PLAN_KIND=architecture_successor
PROJECT_ID=aota_forge
PLAN_REVISION=R2
PLAN_STATUS=active
CURRENT_MILESTONE=M1
FROZEN_BASELINE_ID=AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1
M1_STATUS=ready
M2_STATUS=accepted
M3_STATUS=in_progress
M3_WORK_ITEMS=W1,W2,W3,W4
M3_DAG=W1 -> (W2 || W3) -> W4 -> RV1
```

<!-- af57-m3-current-state:begin -->
M3_STATUS=in_progress
M3_USER_APPROVAL_SATISFIED=yes
CURRENT_WORK_ITEM=W1
<!-- af57-m3-current-state:end -->

## 1. Goal

Create the bounded local Governance 2.0 foundation.

## 28. Governance 1.x -> 2.0 authority cutover

ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
SILENT_DUAL_AUTHORITY=no

## 32. Milestones

### M1 - Authority, Identity, Storage & Local Governance Foundation

W1 owns production authority and durable governance foundation.

### M2 - Progressive Context & Stewardship Subsystem

W2 owns bounded progressive context.

### M3 - Migration, Aggregate Progress & Real Governance Dogfood

W1 -> (W2 || W3) -> W4 -> RV1
"""


def _error_code(response: dict) -> str:
    return str((response.get("error") or {}).get("code", ""))


def _assert_plan_body_invalid(response: dict) -> None:
    assert response.get("is_success") is False, response
    assert _error_code(response) == "INVALID_INPUT"
    assert "PLAN_BODY_INVALID" in str((response.get("error") or {}).get("message"))


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    yield
    from aota_forge.core.ingress import reset_execution_dispatcher

    reset_execution_dispatcher()


def test_governance_plan_shape_is_recognized() -> None:
    assert _is_portable_plan_body(GOVERNANCE_57_LIKE_BODY) is True
    assert _portable_plan_replacement_error(GOVERNANCE_57_LIKE_BODY, GOVERNANCE_57_LIKE_BODY) is None


def test_governance_plan_invalid_whole_body_zero_provider_write(tmp_path, monkeypatch) -> None:
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, GOVERNANCE_57_LIKE_BODY)
    candidate = GOVERNANCE_57_LIKE_BODY.replace("PLAN_TYPE=portable_plan", "PLAN_TYPE=not_a_plan", 1)

    response = host.invoke(
        "github.issue.update",
        {"body": candidate, "expected_updated_at": "2026-09-16T00:00:00Z"},
    )

    _assert_plan_body_invalid(response)
    assert not any(call.startswith("update_issue") for call in port.calls), port.calls


def test_governance_plan_invalid_section_zero_provider_write(tmp_path, monkeypatch) -> None:
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, GOVERNANCE_57_LIKE_BODY)

    response = host.invoke(
        "github.issue.update",
        {
            "section_marker": "af57-m3-current-state",
            "section_content": "PLAN_TYPE=not_a_plan\nPLAN_KIND=architecture_successor\nPROJECT_ID=aota_forge",
            "expected_updated_at": "2026-09-16T00:00:00Z",
        },
    )

    _assert_plan_body_invalid(response)
    assert not any(call.startswith("update_issue") for call in port.calls), port.calls


def test_governance_plan_identity_change_whole_body_zero_provider_write(tmp_path, monkeypatch) -> None:
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, GOVERNANCE_57_LIKE_BODY)
    candidate = GOVERNANCE_57_LIKE_BODY.replace("PROJECT_ID=aota_forge", "PROJECT_ID=other_project", 1)

    response = host.invoke(
        "github.issue.update",
        {"body": candidate, "expected_updated_at": "2026-09-16T00:00:00Z"},
    )

    _assert_plan_body_invalid(response)
    assert not any(call.startswith("update_issue") for call in port.calls), port.calls


def test_governance_plan_identity_change_section_zero_provider_write(tmp_path, monkeypatch) -> None:
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, GOVERNANCE_57_LIKE_BODY)

    response = host.invoke(
        "github.issue.update",
        {
            "section_marker": "af57-m3-current-state",
            "section_content": "PROJECT_ID=other_project",
            "expected_updated_at": "2026-09-16T00:00:00Z",
        },
    )

    _assert_plan_body_invalid(response)
    assert not any(call.startswith("update_issue") for call in port.calls), port.calls


def test_valid_governance_plan_whole_body_preserves_existing_behavior(tmp_path, monkeypatch) -> None:
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, GOVERNANCE_57_LIKE_BODY)
    candidate = GOVERNANCE_57_LIKE_BODY.replace("M3_STATUS=in_progress", "M3_STATUS=completed", 1)

    response = host.invoke(
        "github.issue.update",
        {"body": candidate, "expected_updated_at": "2026-09-16T00:00:00Z"},
    )

    assert response.get("is_success") is True, response
    assert port.issue["body"] == candidate
    assert len([call for call in port.calls if call.startswith("update_issue")]) == 1


def test_valid_governance_plan_section_preserves_existing_behavior(tmp_path, monkeypatch) -> None:
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, GOVERNANCE_57_LIKE_BODY)

    response = host.invoke(
        "github.issue.update",
        {
            "section_marker": "af57-m3-current-state",
            "section_content": "M3_STATUS=completed\nM3_USER_APPROVAL_SATISFIED=yes\nCURRENT_WORK_ITEM=W1",
            "expected_updated_at": "2026-09-16T00:00:00Z",
        },
    )

    assert response.get("is_success") is True, response
    assert "M3_STATUS=completed" in port.issue["body"]
    assert len([call for call in port.calls if call.startswith("update_issue")]) == 1


def test_generic_non_plan_issue_not_forced_through_plan_validator(tmp_path, monkeypatch) -> None:
    ordinary = "# ordinary issue\n\nThis is not a governed Plan.\n"
    host, port = _bound_plan_ref_host(tmp_path, monkeypatch, ordinary)

    response = host.invoke(
        "github.issue.update",
        {"body": "# ordinary issue updated\n", "expected_updated_at": "2026-09-16T00:00:00Z"},
    )

    assert _is_portable_plan_body(ordinary) is False
    assert response.get("is_success") is True, response
    assert port.issue["body"] == "# ordinary issue updated\n"
    assert len([call for call in port.calls if call.startswith("update_issue")]) == 1
