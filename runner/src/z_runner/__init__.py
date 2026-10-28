"""z-runner: runs one agent job and exits (docs/04 §1).

Importing the package hardens the process environment *before* any submodule can import the OpenHands SDK or
LiteLLM (both read their configuration from the environment at import time):

* no SDK banner, no LLM request debug logs, no log files (read-only root filesystem);
* LiteLLM uses its bundled model cost map instead of fetching it from the internet (no egress);
* observability exporters are disabled unless explicitly configured for Z (traces must not carry secrets);
* SDK user-level state (profiles, SOUL.md, plugins, skills) lives in a private, empty temp directory.
"""

from __future__ import annotations

import os
import tempfile

_FORCED = {
    "OPENHANDS_SUPPRESS_BANNER": "1",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "DEBUG": "false",
    "DEBUG_LLM": "false",
    "LOG_TO_FILE": "false",
    "LITELLM_LOG": os.environ.get("LITELLM_LOG", "ERROR"),
}
_OBSERVABILITY = (
    "LMNR_PROJECT_API_KEY",
    "OTEL_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


def _harden_environment() -> None:
    os.environ.update(_FORCED)
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    for key in _OBSERVABILITY:
        os.environ.pop(key, None)
    base = os.environ.get("Z_RUNNER_TMP") or tempfile.gettempdir()
    os.environ["OH_PERSISTENCE_DIR"] = os.path.join(base, "z-runner-openhands-user")


_harden_environment()

__version__ = "0.1.0"
