"""rikyu-hpc — manifest-driven Hatch adapter for the Rikyu-Agent stdio server.

Spawns ``python -m rikyu_mcp.hpc_server`` from the shared MCP runtime venv
(``UMRS_MCP_VENV``) via mcp-servers/scripts/adapter_package.py. Requires
hpc-agent-core + mcp in that venv and a RIKYU SSH key on the host; configure
with ``RIKYU_HOST`` or ``~/.hpc-agent/rikyu.json``. Runtime deny list:
``RIKYU_TOOL_DENY`` (comma-separated).

Never fails at create time: when the vendor child is unavailable the static
hatch.json tool list is served and invoke() surfaces the spawn error.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from adapter_package import create_adapter_server, venv_python  # noqa: E402

_PACKAGE_DIR = Path(__file__).resolve().parent


def create_server():
    """Entry point required by the Hatch runtime."""
    return create_adapter_server(
        _PACKAGE_DIR,
        command=[venv_python(), "-m", "rikyu_mcp.hpc_server"],
        deny_env="RIKYU_TOOL_DENY",
        offline_hint="runtime offline — check venv/SSH config",
    )
