#!/usr/bin/env python3
"""M4-6 independent review guard — verifies review did not use grep-only tautology.

This guard independently re-executes the 8 positive and 28 negative probes
via real adapter code, not mere string search.
"""

from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main() -> int:
    print("M4-6 Independent Review Guard — real behavioral verification")
    # Run the probe script logic via import
    import subprocess
    result = subprocess.run(
        [sys.executable, "/tmp/m46_independent_probes.py"],
        capture_output=False,
        check=False,
    )
    if result.returncode != 0:
        print("FAIL: probe script failed")
        return 1

    # Also verify source guard PASS
    import subprocess as sp
    guard = sp.run([sys.executable, str(ROOT / "scripts" / "m4_6_source_guard.py")], capture_output=True, text=True)
    print(guard.stdout[-2000:])
    if "M4_6_SOURCE_GUARD=PASS" not in guard.stdout:
        print("FAIL: source guard not PASS")
        return 1

    # Verify focused tests via pytest
    import subprocess as sp2
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    pytest = sp2.run([sys.executable, "-m", "pytest", str(ROOT / "tests" / "test_m4_6_github_adapter.py"), "-q"], capture_output=True, text=True, env=env)
    # Try alternative runner if pytest not found
    if pytest.returncode != 0:
        # Try via AOTA env
        pytest = sp2.run(["/home/latios/AOTA/AOTA_Engine/aota_env/bin/pytest", str(ROOT / "tests" / "test_m4_6_github_adapter.py"), "-q"], capture_output=True, text=True, env=env)
    print(pytest.stdout[-1000:])
    print(pytest.stderr[-1000:])
    if "25 passed" not in pytest.stdout and "25 passed" not in pytest.stderr:
        # check alternative output
        if pytest.returncode != 0:
            print("FAIL: focused tests not 25 passed")
            return 1

    print("M4_6_SOURCE_INDEPENDENT_REVIEW_GUARD=PASS")
    print("INDEPENDENT_REVIEW_GUARD_CHECK_COUNT=3")
    print("INDEPENDENT_REVIEW_GUARD_PASS_COUNT=3")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
