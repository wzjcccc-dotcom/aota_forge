"""Disposable pre-W3 proof fixture for the coder workspace.edit path."""

MARKER = "AFTER"


def test_workspace_edit_replacement_is_live() -> None:
    assert MARKER == "AFTER"
