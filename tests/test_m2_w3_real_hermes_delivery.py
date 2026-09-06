"""M2/W3 bounded REAL Hermes durable vertical slice (opt-in gate).

Thin pytest wrapper over scripts/m2_w3_integration_smoke.py so the accepted
operator-run proof is also runnable under the standard suite gate
(``AOTA_M2_W3_REAL_HERMES_SMOKE=1``). The driver itself proves:

    disposable aota-task-main session (exact id from Hermes usage evidence)
    -> real governed aota-worker dispatch through the durable composition
    -> AF runtime A destroyed while the supervised Hermes Worker keeps running
    -> fresh B runtime recovers to terminal CanonicalResult + governance CARD
       (durable BEFORE any delivery attempt)
    -> CARD-first envelope into the EXACT origin session + identity-bound
       real task-main ACK -> acknowledged
    -> second restart: recovery proves no post-ACK redelivery

It uses real provider credentials from the operator Hermes profiles; nothing
here touches the user's ordinary conversations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = "AOTA_M2_W3_REAL_HERMES_SMOKE"
REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get(GATE) != "1", reason=f"set {GATE}=1 for the bounded real Hermes durable slice")
def test_real_hermes_durable_vertical_slice() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "m2_w3_integration_smoke.py")],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output[-4000:]
    for key, value in (
        ("REAL_HERMES_DURABLE_VERTICAL_SLICE", "PASS"),
        ("R8_AF_CRASH_WHILE_HERMES_RUNNING", "PASS"),
        ("REAL_TASK_MAIN_ACK", "yes"),
        ("POST_ACK_REDELIVERY", "no"),
        ("BLIND_REDISPATCH_ON_UNRESOLVED", "no"),
        ("FABRICATED_COMPLETION_ON_UNRESOLVED", "no"),
        ("REAL_WORKSPACE_SEARCH", "yes"),
        ("REAL_WORKSPACE_READ", "yes"),
        ("REAL_WORKSPACE_WRITE", "yes"),
    ):
        assert f"{key}={value}" in output, f"{key}!={value}: {output[-2000:]}"
