"""User-defined MCP server rows for UI surfaces (bridge ``list_mcp_servers``).

Merges the declarative user config (``~/.config/r4/mcp-servers.json``, project
``.r4/mcp.json`` / ``.github/mcp.json``, ``$R4_MCP_CONFIG``) as honest rows:
status is the DECLARED state, never probed — registry health checks cover
only gateway-registered servers. Rows are marked ``agent_visible: False``
because the companion agent only sees tools from the UMRS registry.

A stdio row's status also names its sandbox state (ADR-101 D-9) as the
remote-proxy daemon runs it: ``sandboxed`` while the per-user MCP sandbox is
on and the running daemon (if any) runs it, ``sandbox pending restart``
while the running daemon does not yet, and ``exempt from sandbox`` for an
entry the user-level file (the one the daemon serves) marks ``"sandbox":
false``; a project entry shadowing it does not change what the daemon runs.
"""

from __future__ import annotations

from typing import Any

_OFF: dict[str, Any] = {"state": "off", "exempt": []}
_SANDBOX_TAGS = {"sandboxed": " · sandboxed", "pending": " · sandbox pending restart"}


def _mcp_sandbox_labels() -> dict[str, Any]:
    try:
        from r4.companion.mcp_exposure import mcp_sandbox_labels

        return mcp_sandbox_labels()
    except Exception:  # noqa: BLE001 — a status label must never break the surface
        return _OFF


def user_defined_mcp_rows() -> list[dict[str, Any]]:
    """Declarative rows for the user's local MCP config (empty when absent)."""
    try:
        from r4.tui.mcp_config import load_mcp_configs

        configs = load_mcp_configs()
    except Exception:  # noqa: BLE001 — config load must never break the surface
        return []
    labels = _mcp_sandbox_labels() if configs else _OFF
    return [_row_for(cfg, labels) for cfg in configs]


def _sandbox_tag(cfg: Any, labels: dict[str, Any]) -> str:
    """The stdio row's sandbox tag, from what the daemon runs; "" when off."""
    if cfg.name in labels.get("exempt", ()):
        return " · exempt from sandbox"
    return _SANDBOX_TAGS.get(str(labels.get("state")), "")


def _status_for(cfg: Any, labels: dict[str, Any] | None = None) -> str:
    if not getattr(cfg, "enabled", True):
        return "disabled (off)"
    if cfg.type == "stdio":
        tag = _sandbox_tag(cfg, labels or _OFF)
        return f"declared stdio (child on demand){tag} · not agent-visible"
    return "declared (not probed) · not agent-visible"


def _row_for(cfg: Any, labels: dict[str, Any] | None = None) -> dict[str, Any]:
    target = cfg.url or " ".join([cfg.command, *cfg.args]).strip()
    enabled = bool(getattr(cfg, "enabled", True))
    description = getattr(cfg, "description", "") or f"{cfg.type} MCP server ({target})"
    return {
        "name": cfg.name,
        "type": cfg.type,
        "url": target,
        "status": _status_for(cfg, labels),
        "source": f"user-config:{cfg.source}",
        "agent_visible": False,
        "enabled": enabled,
        "description": description,
        "hint": usage_hint(cfg.name)
        if enabled
        else "off — toggle on in /mcp to include when bridging",
    }


def usage_hint(name: str) -> str:
    """Concrete steps to make a declared server usable by the agent."""
    return (
        "to use it: 1) r4-mcp remote-proxy --config ~/.config/r4/mcp-servers.json "
        "--host 0.0.0.0 --port 50800 (run on the UMRS host)  2) "
        f"r4 mcp register {name} --url http://localhost:50800/{name}  3) tools "
        f"then appear as mcp.{name}.<tool> in chat; check health with `r4 mcp list`"
    )
