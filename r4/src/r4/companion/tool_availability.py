"""Static tool-availability gate (round-2 agent debug, finding D).

Drops tools the session cannot serve BEFORE the offered set reaches the
model. Availability facts only — no model-side selection, no ranking:
ADR-037 stays passthrough; this is a harness precondition filter, not a
strategy. Every removal is recorded in the ADR-037 record under
``filtered_out["unavailable"]`` so the roll-up line can report it.

Coverage today (evidence 2026-09-05):
- ``tool_search`` — wired since 2026-10-28 (G18): ``CompanionSession``
  registers the catalog callback (``tool_search.py``); the gate still drops
  the tool where no session wired it (direct-agent tests, embedders).
- ``web_search`` — needs a search backend: an injected callback or the
  built-in SearXNG client (``r4.agent.web_search``: ``R4_WEB_SEARCH_URL``
  or the local instance of ``r4 search-server up``, ADR-102; 2026-10-30).
  Until then no surface wired one and the tool was dropped in every mode.
- ``file_list_platform`` — unconditionally dead: the only backing route
  (``/gw/data/files``) was retired 2026-08-22 and ``DataClient.list_files``
  always raises the retired-route error.
- ``file_upload_platform`` — dead when the client has no ZMQ transport:
  the tool pre-stages its own ``UMRSTransport`` at *definition* time
  (``tools.py``); when ZMQ is unavailable the executor falls back to the
  retired ``/gw/data/upload`` route and raises by construction.
- ``web_fetch`` / ``web_search`` — refuse every call while the session's web
  access is off. In unattended runs (``R4_AUTONOMOUS=1`` or the terminal
  profile) they are not offered then (H15, 2026-10-24;
  ``R4_COMPANION_PROMPT_HYGIENE=0`` restores the old offer). An interactive
  session keeps them: their refusal tells the model the user chose this and
  can change it with /web. A web tool the session's choice lets run is
  outside every skill limit (:func:`web_tool_enabled`, 2026-10-30).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from r4.client import Client

# ADR-037 filtered_out bucket key (record + roll-up line read this).
UNAVAILABLE_REASON = "unavailable"

_MAX_SAMPLE = 6

# Round-5 task 2: availability gate preconditions are probes, not shapes — a
# cheap TCP reachability probe cached for the process lifetime (the tool
# differs per session but a resolved ZMQ endpoint is a machine-level fact;
# re-dialing dangling endpoints on every turn scan would hang the loop).
_CONNECT_PROBE_TIMEOUT_SEC = 2.0
_CONNECT_PROBE_CACHE: dict[tuple[str, int], bool] = {}


def _connect_probe(host: str, port: int) -> bool:
    """True when ``host:port`` answers a TCP connect within the probe timeout.

    Cached per process. Negative results cache too (firewalled endpoints
    must not cost the timeout on every availability scan).
    """
    key = (host, port)
    cached = _CONNECT_PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    import socket

    ok = False
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_PROBE_TIMEOUT_SEC):
            ok = True
    except Exception:
        ok = False
    _CONNECT_PROBE_CACHE[key] = ok
    return ok


# W3 (2026-10-05): platform-backed tools need a live platform — credentials
# plus a reachable gateway. Without one (offline workstation, Terminal-Bench
# task container) the model burned rounds on platform_health / endpoint_list /
# list_models that could only fail ("connection refused"). Local config tools
# (source_*) and the platform_guide text stay. The TB 4.0 re-run added the
# platform readers that still slipped through: search_docs (RAG), lab_file_read
# (gateway file source) and the model_* registry tools. 2026-10-06: the twelve
# workflow_* tools (UMRS workflows API) were still offered to TB 4.0 containers
# whenever a task's wording matched the workflow_ops skill.
_PLATFORM_TOOL_NAMES = frozenset(
    {
        "platform_health",
        "hpc_sites",
        "list_models",
        "search_agentic",
        "search_docs",
        "open_passage",
        "lab_file_read",
        # ADR-108 W1 (2026-11-03): it uploads the session's history through
        # client.knowledge, so it failed only when called while the platform
        # was down. research_loop_* stay offered (a local runtime needs no
        # platform); project_ensure writes the local registry only.
        "knowledge_build_from_context",
    }
)
_PLATFORM_TOOL_PREFIXES = (
    "endpoint_",
    "model_",
    "job_",
    "pool_",
    "rag_",
    "lab_catalog",
    "vision_",
    "knowledge_query",
    "knowledge_show",
    "knowledge_status",
    "knowledge_graph",
    "knowledge_ingest",
    "workflow_",
)
_PLATFORM_LIVE_TTL_SEC = 60.0
_PLATFORM_LIVE_CACHE: dict[tuple[str, int], tuple[float, bool]] = {}


def is_platform_tool(tool: dict[str, Any]) -> bool:
    """True for tools that call the platform (UMRS / gateway) to do anything."""
    name = str(tool.get("name") or "")
    if tool.get("requires_platform") is True:
        return True
    return (
        name in _PLATFORM_TOOL_NAMES
        or name.endswith("_platform")
        or name.startswith(_PLATFORM_TOOL_PREFIXES)
    )


def _gateway_host_port(config: Any) -> tuple[str, int] | None:
    from urllib.parse import urlsplit

    url = urlsplit(str(getattr(config, "gateway_url", "") or ""))
    if not url.hostname:
        return None
    return url.hostname, url.port or (443 if url.scheme == "https" else 80)


def platform_live(client: Any) -> bool:
    """Platform credentials present and the gateway answers a TCP connect.

    Cached per gateway for ``_PLATFORM_LIVE_TTL_SEC`` (positive and negative)
    so a gateway that comes up mid-session is picked up again.
    """
    config = getattr(client, "config", None) or getattr(client, "_config", None)
    if config is None or not getattr(config, "api_key", None):
        return False
    target = _gateway_host_port(config)
    if target is None:
        return False
    import time

    now = time.monotonic()
    cached = _PLATFORM_LIVE_CACHE.get(target)
    if cached is not None and now - cached[0] < _PLATFORM_LIVE_TTL_SEC:
        return cached[1]
    _CONNECT_PROBE_CACHE.pop(target, None)  # the process-lifetime cache is too sticky here
    ok = _connect_probe(*target)
    _PLATFORM_LIVE_CACHE[target] = (now, ok)
    return ok


# Tools runnable only when a surface wires their callback in-process.
# (tool name, module, global holding the callback).
_CALLBACK_REQUIRED: tuple[tuple[str, str, str], ...] = (
    ("tool_search", "r4.agent.tools", "_tool_search_fn"),
)


def _callback_wired(module_name: str, attr: str) -> bool:
    try:
        import importlib

        module = importlib.import_module(module_name)
        return getattr(module, attr, None) is not None
    except Exception:  # import failure → treat as unwired (fail closed)
        return False


def _upload_transport_available(client: Any) -> bool:
    """Mirror of ``file_upload_platform``'s definition-time precondition
    (``tools.py``: ``from r4.umrs_transport import UMRSTransport`` +
    ``UMRSTransportConfig.for_server(...)`` + instantiate). Any exception ⇒ the tool
    would have no transport instance and its executor falls back to the
    retired HTTP route."""
    if client is None:
        return False
    try:
        from r4.umrs_transport import UMRSTransport, UMRSTransportConfig

        config = getattr(client, "config", None)
        if config is None or not getattr(config, "use_umrs_transport", False):
            return False
        tc = UMRSTransportConfig.for_server(
            server_host=getattr(config, "umrs_server_host", "127.0.0.1"),
            control_port=getattr(config, "umrs_control_port", 5555),
            tunnel_mode=getattr(config, "umrs_tunnel_mode", False),
        )
        UMRSTransport(tc)
    except Exception:
        return False

    # Round-5 task 2: the precondition is reachability, not constructibility.
    host = getattr(config, "umrs_server_host", "127.0.0.1")
    port = int(getattr(config, "umrs_control_port", 5555))
    return _connect_probe(host, port)


def _retired_route(tool: dict[str, Any], client: "Client | None") -> str | None:
    if str(tool.get("name") or "") == "file_list_platform":
        return "route retired: /gw/data/files (no transport path)"
    return None


def _upload_unavailable(tool: dict[str, Any], client: "Client | None") -> str | None:
    if str(tool.get("name") or "") != "file_upload_platform":
        return None
    if _upload_transport_available(client):
        return None
    return "no ZMQ data transport; /gw/data/upload retired"


def _unwired(tool: dict[str, Any], client: "Client | None") -> str | None:
    return _unwired_callback(str(tool.get("name") or ""))


def _no_search_backend(tool: dict[str, Any], client: "Client | None") -> str | None:
    if str(tool.get("name") or "") != "web_search":
        return None
    from r4.agent import web_search

    if _callback_wired("r4.agent.tools", "_web_search_fn") or web_search.configured():
        return None
    return (
        "no web search backend (run `r4 search-server up`, "
        f"or set {web_search.SEARXNG_URL_ENV} to a SearXNG instance)"
    )


def _platform_down(tool: dict[str, Any], client: "Client | None") -> str | None:
    if client is not None and is_platform_tool(tool) and not platform_live(client):
        return "no live platform (no credentials or gateway unreachable)"
    return None


#: Tools that refuse every call unless the session's web access allows the
#: mode named here (``web_access.check_url`` / ``check_search``).
_WEB_TOOL_MODES: dict[str, frozenset[str]] = {
    "web_fetch": frozenset({"allowlist", "public"}),
    "web_search": frozenset({"public"}),
}
#: Key under which the session hands a web tool its web-access getter.
WEB_ACCESS_KEY = "web_access"


def web_tool_enabled(tool: dict[str, Any]) -> bool:
    """True when the session's web choice lets *tool* run: ``web_fetch``
    with allowlist or public access, ``web_search`` with public access.

    The skill filter keeps such a tool (2026-10-30): the user turned the web
    on for this session, and a skill's narrower tool list does not take it
    back. Before, only the ``research`` skill listed the web tools, so a
    public session refused them on almost every turn.
    """
    modes = _WEB_TOOL_MODES.get(str(tool.get("name") or ""))
    getter = tool.get(WEB_ACCESS_KEY)
    return modes is not None and callable(getter) and getter().mode in modes


def _hides_refusing_web_tools() -> bool:
    """Unattended runs only: nobody can answer the refusal with /web there."""
    from . import agent_profile
    from .env_switch import autonomous

    if not agent_profile.hygiene_enabled():
        return False
    return autonomous() or agent_profile.active()


def _web_off(tool: dict[str, Any], client: "Client | None") -> str | None:
    """H15: in an unattended run, a web tool whose session refuses every call
    is not offered.

    Only tools carrying the session's getter (``WEB_ACCESS_KEY``) are judged:
    a /web change reaches the next turn's offer. Interactive sessions keep
    the tool (review 2026-10-24): its refusal names the user's choice and
    the /web command, which a silently missing tool does not.
    """
    modes = _WEB_TOOL_MODES.get(str(tool.get("name") or ""))
    getter = tool.get(WEB_ACCESS_KEY)
    if modes is None or not callable(getter) or not _hides_refusing_web_tools():
        return None
    if getter().mode in modes:
        return None
    return "web access off for this session (the user's choice; /web or r4 --web)"


#: ADR-114: a messaging tool carries a getter for its session's bus presence.
SESSION_BUS_KEY = "session_bus_presence"


def _off_session_bus(tool: dict[str, Any], client: "Client | None") -> str | None:
    """ADR-114: offer messaging only with interactive console/REPL presence;
    headless one-shot runs and unbound workers never see refusing tools."""
    getter = tool.get(SESSION_BUS_KEY)
    if not callable(getter) or getter() is not None:
        return None
    return "not on the local session bus (interactive console/REPL only; one-shot r4 -p is off)"


_CHECKS = (
    _off_session_bus,
    _retired_route,
    _upload_unavailable,
    _unwired,
    _no_search_backend,
    _web_off,
    _platform_down,
)


def unavailable_reason(tool: dict[str, Any], client: "Client | None") -> str | None:
    """Why *tool* cannot be served in this session, or ``None`` when live."""
    for check in _CHECKS:
        reason = check(tool, client)
        if reason:
            return reason
    return None


def is_platform_skill(tool_names: Any) -> bool:
    """H19: every domain tool of a skill needs the platform.

    The workspace and always-on tools a skill may also list do not count; a
    skill without domain tools is not a platform skill.
    """
    from .skills import ALWAYS_ON_TOOL_NAMES, WORKSPACE_TOOL_NAMES

    domain = [
        name
        for name in tool_names or ()
        if name not in WORKSPACE_TOOL_NAMES and name not in ALWAYS_ON_TOOL_NAMES
    ]
    return bool(domain) and all(is_platform_tool({"name": name}) for name in domain)


def _unwired_callback(name: str) -> str | None:
    for tool_name, module, attr in _CALLBACK_REQUIRED:
        if name == tool_name and not _callback_wired(module, attr):
            return f"callback never wired ({module}.{attr})"
    return None


def filter_unavailable_tools(
    tools: list[dict[str, Any]],
    client: "Client | None",
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Split *tools* into (kept, unavailable bucket).

    Returns the kept list plus an ADR-037 ``filtered_out[UNAVAILABLE_REASON]``
    bucket (``None`` when nothing was removed) for the selection record.
    """
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for tool in tools:
        if unavailable_reason(tool, client) is None:
            kept.append(tool)
        else:
            removed.append(str(tool.get("name") or "?"))
    if not removed:
        return kept, None
    return kept, {
        "count": len(removed),
        "sample": removed[:_MAX_SAMPLE],
    }
