"""Safe environment identity adapter (M1-F).

Strict allowlist only.  Never dumps the full environment and never exposes
secrets.
"""

from __future__ import annotations

import os
import platform
from typing import Any

ALLOWED_ENV_KEYS = (
    "HOME",
    "USER",
    "LANG",
    "TZ",
    "HERMES_HOME",
)


def safe_environment_identity() -> dict[str, Any]:
    """Return bounded, allowlisted environment identity.

    Only presence booleans for allowlisted keys plus bounded platform
    identity.  Values of arbitrary variables are never emitted.
    """
    return {
        "platform": platform.system(),
        "python": platform.python_version(),
        "hostname": platform.node()[:128],
        "env_keys_present": {key: (key in os.environ) for key in ALLOWED_ENV_KEYS},
    }
