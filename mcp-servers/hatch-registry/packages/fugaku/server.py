"""fugaku — manifest-driven Hatch adapter for the RIKEN-RCCS/fugaku-mcp server.

Spawns ``<venv>/python <FUGAKU_MCP_HOME>/fugaku_mcp.py`` via
mcp-servers/scripts/adapter_package.py. Requires the X.509 client
certificate at ``$FUGAKU_CERT`` and egress to the Fugaku WebAPI; vendor
policy (fugaku_policy.py) is tuned via standard FUGAKU_* env. Runtime deny
list: ``FUGAKU_TOOL_DENY`` (comma-separated).

Never fails at create time: when the vendor child is unavailable the static
hatch.json tool list is served and invoke() surfaces the spawn error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from adapter_package import create_adapter_server, venv_python  # noqa: E402

_PACKAGE_DIR = Path(__file__).resolve().parent
_FUGAKU_HOME = os.environ.get("FUGAKU_MCP_HOME", "/opt/umrs/mcp-runtimes/fugaku-mcp")


def create_server():
    """Entry point required by the Hatch runtime."""
    return create_adapter_server(
        _PACKAGE_DIR,
        command=[venv_python(), str(Path(_FUGAKU_HOME) / "fugaku_mcp.py")],
        deny_env="FUGAKU_TOOL_DENY",
        offline_hint="runtime offline — check venv/FUGAKU_CERT",
    )
