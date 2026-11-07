"""``r4 map`` — security-boundary connection map for the active project.

Renders a visualization of who-talks-to-what so the user can see at a
glance which connections cross a trust boundary: user → gateway → UMRS
(registry/scheduler, ZMQ dataplane) → endpoints → egress externals, plus
MCP servers from BOTH sources — the UMRS registry (agent-visible, health
tracked) and the declarative user config (~/.config/r4/mcp-servers.json,
.project files, $R4_MCP_CONFIG; not agent-visible until bridged +
registered). The map is drawn read-only from data already known (endpoint
registry, MCP registry, local config files) — no prompts are sent.

Symbols used in the ASCII map:

  user          actor; the caller's identity (always inside the trust
                  boundary).
  ━━━━          safe in-cluster channel (TLS terminates inside platform).
  ╍╍╍╍          *unsafe* boundary crossing (presigned URL fetch, allowed
                  egress domain, inbound external client).
  [SYSTEM]      shared system-tier mini-rivault (read-only for caller).
  [PROJECT]     project-tier mini-rivault.
  [USER]        user-tier (private) mini-rivault.
  ⚠ EGRESS      this endpoint has at least one allowlisted external
                  domain — outbound traffic leaves the cluster.
  ⚠ MOUNT       this endpoint mounts user-supplied data/MCP code.

Goals:
- Help users notice when a chat will *leave* the platform (e.g. via a
  custom MCP server with allowed egress) BEFORE they send the prompt.
- Make the difference between "system shared model" and "private project
  model" visible.
- Keep the command read-only and offline-capable (works without sending
  any prompts).

The command is intentionally conservative: anything it cannot prove is
safe is rendered as unsafe (pessimistic default).
"""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .base import CommandHandler
from .registry import command


# ANSI styling. Suppressed when stdout isn't a TTY or NO_COLOR is set.
class _Style:
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    if not _color_enabled():
        return text
    return f"{color}{text}{_Style.RESET}"


# ---------- node model ------------------------------------------------------


@dataclass
class MapNode:
    label: str
    kind: str  # "user" | "gateway" | "endpoint" | "external"
    tier: Optional[str] = None
    served_name: Optional[str] = None
    base_model: Optional[str] = None
    backend: Optional[str] = None
    status: Optional[str] = None
    mcp_status: Optional[str] = None
    egress_domains: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)
    job_id: Optional[str] = None
    instance_id: Optional[str] = None

    @property
    def has_egress(self) -> bool:
        return bool(self.egress_domains)

    @property
    def has_user_mounts(self) -> bool:
        return bool(self.mounts) or bool(self.mcp_servers)


@dataclass
class MapEdge:
    src: str
    dst: str
    label: str
    safe: bool  # True = inside trust boundary; False = crosses it
    note: Optional[str] = None


# ---------- map builder -----------------------------------------------------


def _classify_endpoint(ep: dict[str, Any]) -> MapNode:
    return MapNode(
        label=ep.get("served_model_name") or ep.get("model") or "(unknown)",
        kind="endpoint",
        tier=_endpoint_tier(ep),
        served_name=ep.get("served_model_name") or ep.get("model"),
        base_model=ep.get("model"),
        backend=ep.get("backend"),
        status=_endpoint_status(ep),
        egress_domains=list(ep.get("egress_domains") or []),
        mcp_servers=list(ep.get("mcp_servers") or []),
        mounts=_endpoint_mount_labels(ep),
        job_id=ep.get("job_id"),
        instance_id=ep.get("instance_id"),
    )


def _endpoint_tier(endpoint: dict[str, Any]) -> str:
    return (endpoint.get("tier") or endpoint.get("owner_type") or "user").lower()


def _endpoint_status(endpoint: dict[str, Any]) -> Any:
    status = endpoint.get("status")
    if endpoint.get("expires_at") and status == "ready" and _endpoint_is_expired(endpoint):
        return "expired"
    return status


def _endpoint_is_expired(endpoint: dict[str, Any]) -> bool:
    from datetime import datetime, timezone

    try:
        exp_dt = datetime.fromisoformat(str(endpoint["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return False
    return exp_dt < datetime.now(timezone.utc)


def build_map(
    *,
    user_id: str,
    project_id: str,
    gateway_url: str,
    endpoints: list[dict[str, Any]],
    mcp_servers: list[dict[str, Any]] | None = None,
    user_mcp_servers: list[dict[str, Any]] | None = None,
) -> tuple[list[MapNode], list[MapEdge]]:
    """Construct the connection-map graph from the current pool snapshot."""
    nodes, edges, gateway_node = _base_map_graph(user_id, gateway_url)
    umrs_node = _append_umrs_graph(nodes, edges, gateway_node, gateway_url)

    for raw in endpoints:
        _append_endpoint_graph(nodes, edges, gateway_node, raw)

    _append_registry_mcp(nodes, edges, umrs_node, mcp_servers or [], gateway_url)
    _append_user_mcp(nodes, edges, user_id, user_mcp_servers or [])
    return nodes, edges


def _base_map_graph(user_id: str, gateway_url: str) -> tuple[list[MapNode], list[MapEdge], MapNode]:
    user_node = MapNode(label=f"user:{user_id}", kind="user")
    gateway_node = MapNode(label=f"gateway ({_short_host(gateway_url)})", kind="gateway")
    edge = MapEdge(src=user_node.label, dst=gateway_node.label, label="auth+routing", safe=True)
    return [user_node, gateway_node], [edge], gateway_node


def _append_endpoint_graph(
    nodes: list[MapNode],
    edges: list[MapEdge],
    gateway_node: MapNode,
    raw_endpoint: dict[str, Any],
) -> None:
    endpoint = _classify_endpoint(raw_endpoint)
    nodes.append(endpoint)
    edges.append(_gateway_endpoint_edge(gateway_node, endpoint))
    _append_egress_graph(nodes, edges, endpoint)


def _gateway_endpoint_edge(gateway_node: MapNode, endpoint: MapNode) -> MapEdge:
    return MapEdge(
        src=gateway_node.label,
        dst=endpoint.label,
        label=f"litellm ({endpoint.backend or 'unknown'})",
        safe=True,
        note=endpoint.status,
    )


def _append_egress_graph(nodes: list[MapNode], edges: list[MapEdge], endpoint: MapNode) -> None:
    for domain in endpoint.egress_domains:
        external = MapNode(label=f"external:{domain}", kind="external")
        nodes.append(external)
        edges.append(_egress_edge(endpoint, external))


def _append_umrs_graph(
    nodes: list[MapNode],
    edges: list[MapEdge],
    gateway_node: MapNode,
    gateway_url: str,
) -> MapNode:
    """UMRS node behind the gateway (accounts, jobs, MCP registry, ZMQ dataplane)."""
    umrs = MapNode(label=f"umrs ({_short_host(gateway_url)})", kind="umrs")
    nodes.append(umrs)
    edges.append(
        MapEdge(
            src=gateway_node.label,
            dst=umrs.label,
            label="umrs api (/umrs/*)",
            safe=True,
            note="zmq dataplane: direct TCP 50110-50118 (no SSH tunnel)",
        )
    )
    return umrs


def _is_local_mcp_url(url: str, gateway_url: str) -> bool:
    """True when the MCP server URL is loopback or the gateway host itself."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    gw_host = (urlparse(gateway_url).hostname or "").lower()
    return host in {"", "localhost", "127.0.0.1", "::1", gw_host}


def _append_registry_mcp(
    nodes: list[MapNode],
    edges: list[MapEdge],
    umrs_node: MapNode,
    servers: list[dict[str, Any]],
    gateway_url: str = "",
) -> None:
    """Registry MCP servers; externally hosted ones are boundary crossings."""
    for srv in servers:
        _append_registry_mcp_entry(nodes, edges, umrs_node, srv, gateway_url)


def _first(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value:
            return str(value)
    return ""


def _append_registry_mcp_entry(
    nodes: list[MapNode],
    edges: list[MapEdge],
    umrs_node: MapNode,
    srv: dict[str, Any],
    gateway_url: str,
) -> None:
    name = _first(srv, "name", "server_id") or "(unnamed)"
    url = str(srv.get("url") or "")
    status = str(srv.get("status") or "unknown")
    local = _is_local_mcp_url(url, gateway_url)
    node = MapNode(
        label=f"mcp:{name}" if local else f"mcp-ext:{name}",
        kind="mcp" if local else "external",
        mcp_status=status,
        backend="registry" if local else "registry-external",
    )
    nodes.append(node)
    edges.append(
        MapEdge(
            src=umrs_node.label,
            dst=node.label,
            label="mcp registry" if local else "mcp (externally hosted)",
            safe=local,
            note=status if local else "calls leave the cluster",
        )
    )


def _append_user_mcp(
    nodes: list[MapNode],
    edges: list[MapEdge],
    user_id: str,
    entries: list[dict[str, Any]],
) -> None:
    """Declarative user-config MCP entries (~/.config/r4/mcp-servers.json & friends)."""
    user_label = f"user:{user_id}"
    for entry in entries:
        name = entry.get("name") or "(unnamed)"
        etype = entry.get("type") or "http"
        if not entry.get("enabled", True):
            nodes.append(
                MapNode(
                    label=f"mcp-local:{name}" if etype == "stdio" else f"mcp-user:{name}",
                    kind="mcp" if etype == "stdio" else "external",
                    mcp_status="disabled (off)",
                )
            )
            continue
        if etype == "stdio":
            node = MapNode(label=f"mcp-local:{name}", kind="mcp", mcp_status="stdio (child)")
            nodes.append(node)
            edges.append(
                MapEdge(src=user_label, dst=node.label, label="user-config stdio", safe=True)
            )
        else:
            node = MapNode(label=f"mcp-user:{name}", kind="external", mcp_status="declared")
            nodes.append(node)
            edges.append(
                MapEdge(
                    src=user_label,
                    dst=node.label,
                    label="user-config http",
                    safe=False,
                    note="public internet until bridged+registered",
                )
            )


def _egress_edge(endpoint: MapNode, external: MapNode) -> MapEdge:
    return MapEdge(
        src=endpoint.label,
        dst=external.label,
        label="egress (allowlisted)",
        safe=False,
        note="data leaves the platform",
    )


def _short_host(url: str) -> str:
    """Return the host:port portion of a URL for compact display."""
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
        return u.netloc or url
    except Exception:
        return url


def _endpoint_mount_labels(endpoint: dict[str, Any]) -> list[str]:
    mounts = endpoint.get("mounts") or {}
    if isinstance(mounts, dict):
        return sorted(mounts.keys())
    return list(mounts)


# ---------- renderers -------------------------------------------------------


def render_ascii(nodes: list[MapNode], edges: list[MapEdge]) -> str:
    """Render a compact ASCII tree (user → gateway → endpoints → externals)."""
    lines: list[str] = []
    lines.append(_c("Project connection map (security boundary view)", _Style.BOLD))
    lines.append("")
    _append_ascii_actors(lines, nodes)

    endpoints = [node for node in nodes if node.kind == "endpoint"]
    if not endpoints:
        _append_ascii_empty_state(lines)
    else:
        _append_ascii_endpoints(lines, endpoints)

    _append_ascii_mcp_section(lines, nodes)
    _append_ascii_boundary_summary(lines, edges)
    _append_ascii_legend(lines)
    return "\n".join(lines)


def _append_ascii_actors(lines: list[str], nodes: list[MapNode]) -> None:
    user_nodes = [n for n in nodes if n.kind == "user"]
    gateway_nodes = [n for n in nodes if n.kind == "gateway"]
    for n in user_nodes:
        lines.append(f"  {_c('●', _Style.GREEN)} {n.label}")
    for n in gateway_nodes:
        lines.append(f"  {_c('│', _Style.DIM)}  ── safe channel")
        lines.append(f"  {_c('▼', _Style.GREEN)} {n.label}  {_c('[trust boundary]', _Style.DIM)}")

    lines.append("")


def _append_ascii_empty_state(lines: list[str]) -> None:
    lines.append(_c("  (no endpoints provisioned — nothing to map yet)", _Style.DIM))
    lines.append("")
    lines.append("  Hint: bring up a pool with  r4 pool up <roster.yaml>")


def _append_ascii_endpoints(lines: list[str], endpoints: list[MapNode]) -> None:
    lines.append(_c("  Endpoints in this project:", _Style.BOLD))
    for ep in endpoints:
        _append_ascii_endpoint(lines, ep)


def _append_ascii_endpoint(lines: list[str], endpoint: MapNode) -> None:
    marker, color = _tier_marker(endpoint.tier or "user")
    status = _c(f"[{endpoint.status}]", _Style.DIM) if endpoint.status else ""
    lines.append(
        f"    {_c(marker, color)} {_c(endpoint.label, _Style.BOLD)}  "
        f"{_c('(' + (endpoint.backend or '?') + ')', _Style.DIM)} "
        f"{status}{_endpoint_warning_text(endpoint)}"
    )
    if endpoint.mcp_servers:
        lines.append(f"        mcp:    {', '.join(endpoint.mcp_servers)}")
    if endpoint.mounts:
        lines.append(f"        mounts: {', '.join(endpoint.mounts)}")
    for domain in endpoint.egress_domains:
        lines.append(
            f"        {_c('╍╍▶', _Style.RED)} {_c(domain, _Style.RED)} "
            f"{_c('(outbound — data may leave the platform)', _Style.RED)}"
        )


def _endpoint_warning_text(endpoint: MapNode) -> str:
    warnings = []
    if endpoint.has_egress:
        warnings.append(_c("⚠ EGRESS", _Style.RED))
    if endpoint.has_user_mounts:
        warnings.append(_c("⚠ MOUNT", _Style.YELLOW))
    return ("  " + " ".join(warnings)) if warnings else ""


_MCP_LABEL_PREFIXES = ("mcp:", "mcp-ext:", "mcp-local:", "mcp-user:")


def _append_ascii_mcp_section(lines: list[str], nodes: list[MapNode]) -> None:
    mcp_nodes = [n for n in nodes if n.label.startswith(_MCP_LABEL_PREFIXES)]
    umrs_nodes = [n for n in nodes if n.kind == "umrs"]
    lines.append("")
    for umrs in umrs_nodes:
        mark = _c("▼", _Style.GREEN)
        tag = _c("[registry + scheduler]", _Style.DIM)
        lines.append(f"    {mark} {umrs.label}  {tag}")
    lines.append(_c("  MCP servers (vigilance view):", _Style.BOLD))
    if not mcp_nodes:
        lines.append(_c("    (none registered on the gateway, none declared locally)", _Style.DIM))
        return
    for node in mcp_nodes:
        lines.append(_ascii_mcp_line(node))
    _append_ascii_user_mcp_guidance(lines, mcp_nodes)


def _append_ascii_user_mcp_guidance(lines: list[str], mcp_nodes: list[MapNode]) -> None:
    declared = [
        n
        for n in mcp_nodes
        if n.label.startswith(("mcp-local:", "mcp-user:")) and n.mcp_status != "disabled (off)"
    ]
    if not declared:
        return
    lines.append(
        _c(
            "    → declared rows are NOT agent-visible. To use them: "
            "r4-mcp remote-proxy --config ~/.config/r4/mcp-servers.json "
            "--host 0.0.0.0 --port 50800 (on the UMRS host), then "
            "`r4 mcp register <name> --url http://localhost:50800/<name>`.",
            _Style.YELLOW,
        )
    )


def _ascii_mcp_line(node: MapNode) -> str:
    status = node.mcp_status or "unknown"
    if status == "disabled (off)":
        return f"    {_c('◌', _Style.DIM)} {_c(node.label + ' — disabled (off)', _Style.DIM)}"
    if node.label.startswith("mcp:"):
        icon = "✓" if status.lower() in ("healthy", "ok", "up") else "✗"
        colored = _c(icon, _Style.GREEN if icon == "✓" else _Style.RED)
        return f"    {colored} {node.label} — registry · {status}"
    if node.label.startswith("mcp-local:"):
        tail = f" — user-config stdio · {status} (not agent-visible until bridged+registered)"
        return f"    {node.label}{tail}"
    # externally hosted (mcp-ext: / mcp-user:) — boundary crossing
    return (
        f"    {_c('╍╍▶', _Style.RED)} {_c(node.label, _Style.RED)} — "
        f"{_c('external · ' + status + ' (calls leave the cluster)', _Style.RED)}"
    )


def _append_ascii_boundary_summary(lines: list[str], edges: list[MapEdge]) -> None:
    lines.append("")
    unsafe_edges = [edge for edge in edges if not edge.safe]
    if not unsafe_edges:
        lines.append(
            _c(
                "  ✓ No unsafe crossings (endpoints offline; MCP in-cluster only).",
                _Style.GREEN,
            )
        )
        return
    lines.append(_c("  ⚠ Unsafe boundary crossings detected:", _Style.RED))
    for edge in unsafe_edges:
        lines.append(
            f"      {edge.src}  {_c('╍╍▶', _Style.RED)}  {edge.dst}   "
            f"{_c('(' + edge.label + ')', _Style.DIM)}"
        )
    lines.append("")
    lines.append(
        _c(
            "  Treat these as untrusted: presume any prompt that reaches a\n"
            "  flagged endpoint may exfiltrate to the listed domain(s).\n"
            "  Use system-tier (offline) endpoints for sensitive prompts.",
            _Style.YELLOW,
        )
    )


def _append_ascii_legend(lines: list[str]) -> None:
    lines.append("")
    lines.append(_c("Legend:", _Style.DIM))
    lines.append(_c("  ●/▼ actors and gateway  ── safe channel  ╍╍▶ unsafe boundary", _Style.DIM))
    lines.append(
        _c("  [SYS] shared system tier  [PROJ] project tier  [USR] private user tier", _Style.DIM)
    )


def _tier_marker(tier: str) -> tuple[str, str]:
    t = tier.lower()
    if t == "system":
        return "[SYS]", _Style.GREEN
    if t == "project":
        return "[PROJ]", _Style.CYAN
    return "[USR]", _Style.YELLOW


def render_dot(nodes: list[MapNode], edges: list[MapEdge]) -> str:
    """Render a Graphviz DOT graph for users who want a visual diagram.

    Run with: ``r4 map --dot | dot -Tpng > map.png``
    """
    out: list[str] = [
        "digraph r4_map {",
        "  rankdir=LR;",
        "  node [shape=box, fontname=monospace];",
    ]
    out.extend(_dot_node_line(node) for node in nodes)
    out.extend(_dot_edge_line(edge) for edge in edges)
    out.append("}")
    return "\n".join(out)


def _dot_node_line(node: MapNode) -> str:
    label = _dot_escape(node.label)
    attrs = _dot_node_attrs(node)
    attr_str = ", ".join(attrs) if attrs else ""
    suffix = _dot_node_label_suffix(node)
    tail = f", {attr_str}" if attr_str else ""
    return f'  "{label}" [label="{label}{suffix}"{tail}];'


def _dot_node_attrs(node: MapNode) -> list[str]:
    if node.kind == "user":
        return ["shape=ellipse", "style=filled", "fillcolor=palegreen"]
    if node.kind in ("gateway", "umrs"):
        return ["style=filled", "fillcolor=lightblue"]
    if node.kind == "mcp":
        return ['style="dashed,filled"', "fillcolor=khaki"]
    if node.kind == "external":
        return ['style="dashed,filled"', "fillcolor=mistyrose", "color=red"]
    return _dot_endpoint_attrs(node)


def _dot_endpoint_attrs(node: MapNode) -> list[str]:
    attrs = _dot_endpoint_tier_attrs(node.tier or "user")
    if node.has_egress or node.has_user_mounts:
        attrs += ["color=red", "penwidth=2"]
    return attrs


def _dot_endpoint_tier_attrs(tier: str) -> list[str]:
    colors = {
        "system": "palegreen",
        "project": "lightcyan",
    }
    return ["style=filled", f"fillcolor={colors.get(tier.lower(), 'lightyellow')}"]


def _dot_node_label_suffix(node: MapNode) -> str:
    if node.kind != "endpoint":
        return ""
    warnings = []
    if node.has_egress:
        warnings.append("⚠ EGRESS")
    if node.has_user_mounts:
        warnings.append("⚠ MOUNT")
    return "\\n" + " ".join(warnings) if warnings else ""


def _dot_edge_line(edge: MapEdge) -> str:
    src = _dot_escape(edge.src)
    dst = _dot_escape(edge.dst)
    return f'  "{src}" -> "{dst}" [{", ".join(_dot_edge_attrs(edge))}];'


def _dot_edge_attrs(edge: MapEdge) -> list[str]:
    attrs = [f'label="{edge.label}"']
    if not edge.safe:
        return attrs + ["color=red", "style=dashed", "penwidth=2"]
    return attrs + ['color="#444"']


def _dot_escape(value: str) -> str:
    return value.replace('"', '\\"')


def render_json(nodes: list[MapNode], edges: list[MapEdge]) -> str:
    return json.dumps(
        {
            "nodes": [n.__dict__ for n in nodes],
            "edges": [e.__dict__ for e in edges],
        },
        indent=2,
    )


# ---------- command ---------------------------------------------------------


@command
class MapCommand(CommandHandler):
    """``r4 map`` — visualize project connection / security boundaries."""

    name = "map"
    help = "Show a connection map for the project (with security boundary)"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Render a connection map showing user → gateway → endpoint(s)"
                " → external domains, with unsafe (boundary-crossing) edges"
                " highlighted. Read-only; sends no prompts."
            ),
        )
        out_group = parser.add_mutually_exclusive_group()
        out_group.add_argument(
            "--dot",
            action="store_true",
            help="Emit Graphviz DOT (pipe to `dot -Tpng > map.png`)",
        )
        out_group.add_argument(
            "--json",
            dest="as_json",
            action="store_true",
            help="Emit JSON (nodes + edges) for tooling",
        )
        parser.add_argument(
            "--from-file",
            metavar="PATH",
            help=(
                "Read endpoint records from a JSON file instead of querying "
                "the gateway. Useful for offline review of `r4 endpoint list "
                "--json` output."
            ),
        )
        return parser

    def handle(self, args: Namespace) -> int:
        from ..client import Client
        from ..config import get_config
        from ..errors import R4Error

        config = get_config()
        endpoints = _load_map_endpoints(args, config, Client, R4Error)
        if endpoints is None:
            return 1 if not args.from_file else 2

        mcp_servers = [] if args.from_file else _load_registry_mcp(config, Client)
        nodes, edges = build_map(
            user_id=getattr(config, "umrs_username", None) or "anonymous",
            project_id=getattr(config, "project_id", None) or "(no project)",
            gateway_url=getattr(config, "gateway_url", None) or "",
            endpoints=endpoints,
            mcp_servers=mcp_servers,
            user_mcp_servers=_load_user_mcp_entries(),
        )
        _print_map_output(args, nodes, edges)
        return 0


def _load_registry_mcp(config, client_cls) -> list[dict]:
    """UMRS-registry MCP servers; empty on any failure (map degrades, never dies)."""
    try:
        raw = client_cls(config=config).mcp.list_servers()
    except Exception:  # noqa: BLE001 — optional surface
        return []
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _load_user_mcp_entries() -> list[dict]:
    """Declarative user-config MCP entries; empty when no config file exists."""
    try:
        from ..tui.mcp_config import load_mcp_configs

        configs = load_mcp_configs()
    except Exception:  # noqa: BLE001 — optional surface
        return []
    return [
        {
            "name": cfg.name,
            "type": cfg.type,
            "url": cfg.url or " ".join([cfg.command, *cfg.args]).strip(),
            "enabled": bool(getattr(cfg, "enabled", True)),
        }
        for cfg in configs
    ]


def _load_map_endpoints(args: Namespace, config, client_cls, r4_error) -> list[dict] | None:
    if args.from_file:
        return _load_map_endpoints_from_file(args.from_file)
    return _load_map_endpoints_from_gateway(config, client_cls, r4_error)


def _load_map_endpoints_from_file(path: str) -> list[dict] | None:
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: failed to read {path}: {exc}", file=sys.stderr)
        return None
    if isinstance(payload, list):
        return payload
    return payload.get("endpoints", [])


def _load_map_endpoints_from_gateway(config, client_cls, r4_error) -> list[dict] | None:
    client = client_cls(config=config)
    try:
        data = client.list_endpoints()
    except r4_error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "Tip: use `r4 map --from-file endpoints.json` to render from a saved snapshot.",
            file=sys.stderr,
        )
        return None
    return data.get("endpoints", [])


def _print_map_output(args: Namespace, nodes: list[MapNode], edges: list[MapEdge]) -> None:
    if args.dot:
        print(render_dot(nodes, edges))
    elif args.as_json:
        print(render_json(nodes, edges))
    else:
        print(render_ascii(nodes, edges))
