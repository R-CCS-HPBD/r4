"""Client-adjacent companion tools: sources, pools, endpoint upgrades,
workflow gates, and guided demo preparation.

Bridges the last CLI-only surfaces (``r4 source``, ``r4 pool``, rolling
endpoint upgrades, workflow manual gates) into the agent tool surface so
the TUI/webview chat can drive the whole platform without shell fallbacks,
and carries the guided prep checklist for platform demos. The demos
themselves are data, not code: ``R4_DEMO_SPECS`` names a JSON file of demo
definitions (the maintainer's private demo repository ships one; 2026-11-03,
the built-in definitions left this public module).
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..client import Client


def _tool(name: str, description: str, parameters: dict[str, Any], execute) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "execute": execute,
    }


def _str_param(desc: str, default: Optional[str] = None) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": "string", "description": desc}
    if default is not None:
        prop["default"] = default
    return prop


# ---------------------------------------------------------------------------
# Sources (local config registry — no platform client needed)
# ---------------------------------------------------------------------------


def _store():
    from ..source import get_source_store

    return get_source_store()


def _source_list() -> str:
    store = _store()
    active = store.get_active()
    lines = []
    for src in store.list():
        mark = "*" if src.name == active else " "
        auth = "key" if src.api_key else "-"
        lines.append(f"{mark} {src.name}  kind={src.kind}  url={src.url}  auth={auth}")
    return "\n".join(lines) if lines else "No sources registered."


def _source_add(
    name: str,
    url: str,
    kind: str = "platform",
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> str:
    from ..source import Source

    if kind not in ("platform", "public"):
        return f"Error: kind must be 'platform' or 'public', got {kind!r}"
    _store().add(
        Source(name=name, kind=kind, url=url.rstrip("/"), api_key=api_key, provider=provider)  # type: ignore[arg-type]
    )
    return f"Source '{name}' registered ({kind}: {url})."


#: The running session keeps the site it started with (multi-HPC plan S3,
#: X-c / H-13): a mid-session swap would change the client under running
#: tools and the ZMQ binding. ``site=`` on each tool is the route now.
_NEXT_SESSION = (
    "This takes effect in the next session; this session keeps its current "
    "site. To act on another site now, pass site=<name> to the tool."
)


def _source_use(name: Optional[str] = None) -> str:
    store = _store()
    if not name:
        store.set_active(None)
        return f"Active source cleared (profile default). {_NEXT_SESSION}"
    if store.get(name) is None:
        return f"Error: no source named '{name}'."
    store.set_active(name)
    return f"Active source stored: {name}. {_NEXT_SESSION}"


def _source_remove(name: str) -> str:
    if _store().remove(name):
        return f"Source '{name}' removed."
    return f"No source named '{name}'."


def build_source_tools() -> list[dict[str, Any]]:
    """Local source-registry tools (mutations classified as risky)."""
    return [
        _tool(
            "source_list",
            "List registered LLM sources (platform gateways and public providers);"
            " '*' marks the active one",
            {"type": "object", "properties": {}},
            _source_list,
        ),
        _tool(
            "source_add",
            "Register a new LLM source (platform gateway or public OpenAI-compatible API)",
            {
                "type": "object",
                "properties": {
                    "name": _str_param("Source name"),
                    "url": _str_param("Base URL of the gateway/provider"),
                    "kind": _str_param("'platform' or 'public'", "platform"),
                    "api_key": _str_param("Optional API key (bearer)"),
                    "provider": _str_param(
                        "Optional provider id (bedrock/anthropic; omit for OpenAI-compatible)"
                    ),
                },
                "required": ["name", "url"],
            },
            _source_add,
        ),
        _tool(
            "source_use",
            "Set the stored active source for the NEXT session (omit name to clear);"
            " the running session keeps its site — use site= on tools instead",
            {
                "type": "object",
                "properties": {"name": _str_param("Source name to activate")},
            },
            _source_use,
        ),
        _tool(
            "source_remove",
            "Remove a registered source by name",
            {
                "type": "object",
                "properties": {"name": _str_param("Source name")},
                "required": ["name"],
            },
            _source_remove,
        ),
    ]


# ---------------------------------------------------------------------------
# Pools (compose on the CLI-layer roster helpers; no duplicated logic)
# ---------------------------------------------------------------------------


def _capture(fn, *args, **kwargs) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = fn(*args, **kwargs)
    return int(rc), buf.getvalue()


def _pool_up(
    file: str,
    dry_run: bool = False,
    continue_on_error: bool = False,
    sequential: bool = False,
) -> str:
    path = Path(file)
    if not path.is_file():
        return f"Error: pool file not found: {file}"
    try:
        from .._cli.pool import _load_roster, provision_pool_roster

        roster = _load_roster(path)
        rc, out = _capture(
            provision_pool_roster,
            roster,
            roster_path=path,
            continue_on_error=continue_on_error,
            dry_run=dry_run,
            parallel=not sequential,
        )
        return (out.strip() + f"\nexit={rc}").strip()
    except BaseException as e:  # noqa: BLE001 - report CLI errors as tool text
        return f"Error in pool up: {e}"


def _pool_down_stop_entries(
    entries: list[dict[str, Any]],
    *,
    force: bool,
    continue_on_error: bool,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    from .._cli.pool import _stop_pool_entry

    fail_count = 0
    remaining: list[dict[str, Any]] = []
    lines: list[str] = []
    for index, entry in enumerate(entries):
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                result = _stop_pool_entry(entry, force=force, as_json=True)
            ok = bool(result.get("ok")) if isinstance(result, dict) else False
        except BaseException as e:  # noqa: BLE001 - keep teardown going
            ok = False
            result = {"error": str(e)}
        name = str(entry.get("name") or entry.get("model") or "?")
        status = str(result.get("status", "ok" if ok else "error"))
        detail = buf.getvalue().strip()
        lines.append(f"- {name} ({entry.get('instance_id', '?')}): {status} [{detail}]")
        if ok:
            continue
        fail_count += 1
        remaining.append(entry)
        if not continue_on_error:
            remaining.extend(entries[index + 1 :])
            break
    return fail_count, remaining, lines


def _pool_down(file: str, force: bool = False, continue_on_error: bool = False) -> str:
    path = Path(file)
    try:
        from .._cli.pool import (
            _load_pool_state,
            _matching_pool_state_path,
            _write_pool_state,
        )

        state = _load_pool_state(path)
        entries = list(state.get("entries", []))
        if not entries:
            return f"No recorded pool entries for {path} (nothing to stop)."
        fail_count, remaining, lines = _pool_down_stop_entries(
            entries, force=force, continue_on_error=continue_on_error
        )
        if remaining:
            state["entries"] = remaining
            _write_pool_state(path, state)
        else:
            state_path = _matching_pool_state_path(path)
            if state_path.exists():
                state_path.unlink()
        stopped = len(entries) - len(remaining)
        rc = 1 if fail_count else 0
        return "\n".join(lines) + f"\npool down: {stopped} stopped, exit={rc}"
    except BaseException as e:  # noqa: BLE001 - report CLI errors as tool text
        return f"Error in pool down: {e}"


def build_pool_tools() -> list[dict[str, Any]]:
    return [
        _tool(
            "pool_up",
            "Provision an endpoint pool from a YAML roster file (like `r4 pool up`);"
            " use dry_run=true to preview",
            {
                "type": "object",
                "properties": {
                    "file": _str_param("Path to pool YAML"),
                    "dry_run": {"type": "boolean", "default": False},
                    "continue_on_error": {"type": "boolean", "default": False},
                    "sequential": {"type": "boolean", "default": False},
                },
                "required": ["file"],
            },
            _pool_up,
        ),
        _tool(
            "pool_down",
            "Stop all endpoints recorded for a pool roster file (like `r4 pool down`)",
            {
                "type": "object",
                "properties": {
                    "file": _str_param("Path to pool YAML"),
                    "force": {"type": "boolean", "default": False},
                    "continue_on_error": {"type": "boolean", "default": False},
                },
                "required": ["file"],
            },
            _pool_down,
        ),
    ]


# ---------------------------------------------------------------------------
# Demo preparation (guided checklist + mechanical mutations)
# ---------------------------------------------------------------------------

DEMO_SPECS_ENV = "R4_DEMO_SPECS"
NO_DEMO_SPECS = (
    "No demos configured: set R4_DEMO_SPECS to a demo-spec JSON file "
    '({"<name>": {"title": ..., "bundle_candidates": [...], ...}}).'
)
_SPEC_TUPLE_KEYS = ("aliases", "bundle_candidates", "sample_candidates")


def _normalize_spec(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    slug = "".join(ch if ch.isalnum() else "_" for ch in name).upper()
    merged: dict[str, Any] = {
        "title": name,
        "bundle_env": f"{slug}_BUNDLE_DIR",
        "video_env": f"{slug}_VIDEO_DIR",
        "video_glob": ".mp4",
        "default_project": None,
        "trigger_params": None,
        "synthesize": None,
        "outputs": "see the workflow bundle README",
        **spec,
    }
    for key in _SPEC_TUPLE_KEYS:
        merged[key] = tuple(merged.get(key) or ())
    return merged


def _load_demo_specs() -> dict[str, dict[str, Any]]:
    """Demo definitions from the JSON file named by ``R4_DEMO_SPECS`` ({} when unset)."""
    path = os.environ.get(DEMO_SPECS_ENV)
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): _normalize_spec(str(name), spec)
        for name, spec in raw.items()
        if isinstance(spec, dict)
    }


def _specs_dir() -> Optional[str]:
    path = os.environ.get(DEMO_SPECS_ENV)
    return str(Path(path).expanduser().parent) if path else None


def _root_env() -> dict[str, Optional[str]]:
    return {
        "SPECS_DIR": _specs_dir(),
        "UMRS_PRIVATE_DEMOS": os.environ.get("UMRS_PRIVATE_DEMOS"),
        "ROOT": os.environ.get("R4_DEMO_REPO_ROOT"),
    }


def _demo_path_env_override(spec: dict[str, Any], key: str) -> Optional[Path]:
    env_name = spec.get("bundle_env" if key == "bundle_candidates" else "video_env")
    value = os.environ.get(env_name) if env_name else None
    return Path(value) if value else None


def _demo_path_candidates(spec: dict[str, Any], key: str) -> list[Path]:
    paths: list[Path] = []
    roots = _root_env()
    for template in spec.get(key, ()):
        for var, value in roots.items():
            if value and ("{" + var + "}") in template:
                paths.append(Path(template.replace("{" + var + "}", value)))
    cwd = str(Path.cwd())
    for template in spec.get(key, ()):
        paths.append(Path(template.replace("{ROOT}", cwd).replace("{UMRS_PRIVATE_DEMOS}", cwd)))
    return paths


def _resolve_demo_path(spec: dict[str, Any], key: str) -> Optional[Path]:
    override = _demo_path_env_override(spec, key)
    if override is not None:
        return override  # env overrides always win
    for candidate in _demo_path_candidates(spec, key):
        if candidate.exists():
            return candidate
    return None


def _resolve_demo(
    demo: Optional[str],
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    specs = _load_demo_specs()
    if not specs:
        return None, None, NO_DEMO_SPECS
    key = (demo or "").strip().lower()
    for name, spec in specs.items():
        if key == name or key in spec["aliases"]:
            return name, spec, None
    known = ", ".join(sorted(specs))
    return None, None, f"Unknown demo {demo!r}; known: {known}"


def _plan_block(
    name: str,
    spec: dict[str, Any],
    bundle: Optional[Path],
    samples: Optional[Path],
) -> str:
    lines = [
        f"# {spec['title']} ({name})",
        f"- workflow bundle: {bundle or 'NOT FOUND - set ' + spec['bundle_env']}",
        f"- videos: env {spec['video_env']}"
        + (f" or samples at {samples}" if samples else " (real camera data required)"),
    ]
    if spec["synthesize"]:
        lines.append(f"- synthesize samples: {spec['synthesize']}")
    lines += [
        f"- project: {spec['default_project'] or 'ask user'}",
        "- steps: 1) confirm source/project with ask_user "
        "2) demo_prepare upload_bundle 3) demo_prepare stage 4) demo_prepare trigger "
        "5) follow with workflow_status / workflow_results",
    ]
    if spec["trigger_params"]:
        lines.append("- trigger params: " + json.dumps(spec["trigger_params"]))
    lines.append(f"- expected outputs: {spec['outputs']}")
    return "\n".join(lines)


def _demo_plan(demo: str = "all") -> str:
    specs = _load_demo_specs()
    if not specs:
        return NO_DEMO_SPECS
    targets = list(specs) if demo.strip().lower() == "all" else [demo]
    blocks = []
    for name in targets:
        resolved, spec, err = _resolve_demo(name)
        if spec is None:
            return err or f"Unknown demo {demo!r}"
        bundle = _resolve_demo_path(spec, "bundle_candidates")
        samples = None
        if spec["sample_candidates"]:
            samples = _resolve_demo_path(spec, "sample_candidates")
        blocks.append(_plan_block(resolved or name, spec, bundle, samples))
    return "\n\n".join(blocks)


def _demo_upload_bundle(client: "Client", spec: dict[str, Any], **_: Any) -> str:
    bundle = _resolve_demo_path(spec, "bundle_candidates")
    if bundle is None:
        return f"Error: workflow bundle not found - set {spec['bundle_env']}"
    manifest = bundle / "workflow.json"
    if not manifest.exists():
        return f"Error: workflow.json missing in {bundle}"
    name = json.loads(manifest.read_text()).get("name", bundle.name)
    result = client.workflows.upload(str(bundle), workflow_name=name)
    return f"Uploaded workflow '{name}' from {bundle}\n" + json.dumps(result, indent=2, default=str)


def _demo_stage(
    client: "Client", spec: dict[str, Any], video_dir: Optional[str] = None, **_: Any
) -> str:
    root = Path(video_dir) if video_dir else _resolve_demo_path(spec, "sample_candidates")
    if root is None or not root.exists():
        return f"Error: no videos - set {spec['video_env']} or pass video_dir"
    videos = sorted(root.rglob(f"*{spec['video_glob']}"))
    if not videos:
        return f"Error: no *{spec['video_glob']} under {root}"
    video = videos[0]
    result = client.vision.upload_file(str(video))
    key = result.get("uri") or result.get("key") or ""
    return f"Staged {video}\nkey: {key}"


def _demo_trigger(
    client: "Client",
    spec: dict[str, Any],
    *,
    input_key: Optional[str] = None,
    project_id: Optional[str] = None,
    params: Optional[str] = None,
    wait: bool = False,
    **_: Any,
) -> str:
    bundle = _resolve_demo_path(spec, "bundle_candidates")
    name = bundle.name if bundle else ""
    if bundle and (bundle / "workflow.json").exists():
        name = json.loads((bundle / "workflow.json").read_text()).get("name", name)
    if not name:
        return f"Error: cannot resolve workflow name - set {spec['bundle_env']}"
    extra: Any = spec["trigger_params"]
    if params:
        extra = json.loads(params)
    files = [input_key] if input_key else None
    result = client.workflows.trigger(
        name, input_files=files, parameters=extra, project_id=project_id, wait=wait
    )
    return json.dumps(result, indent=2, default=str)


_DEMO_ACTIONS = ("upload_bundle", "stage", "trigger")


def _demo_prepare(
    client: "Client",
    demo: str,
    action: str,
    video_dir: Optional[str] = None,
    input_key: Optional[str] = None,
    project_id: Optional[str] = None,
    params: Optional[str] = None,
    wait: bool = False,
) -> str:
    _, spec, err = _resolve_demo(demo)
    if spec is None:
        return err or "Unknown demo"
    if action not in _DEMO_ACTIONS:
        return f"Error: action must be one of {_DEMO_ACTIONS}, got {action!r}"
    handlers = {
        "upload_bundle": _demo_upload_bundle,
        "stage": _demo_stage,
        "trigger": _demo_trigger,
    }
    try:
        return handlers[action](
            client,
            spec,
            video_dir=video_dir,
            input_key=input_key,
            project_id=project_id,
            params=params,
            wait=wait,
        )
    except Exception as e:  # noqa: BLE001 - surface as tool text, not an agent crash
        return f"Error in demo_prepare({demo}, {action}): {e}"


def build_demo_tools(client: Optional["Client"]) -> list[dict[str, Any]]:
    tools = [
        _tool(
            "demo_plan",
            "Show the guided preparation checklist for a platform demo"
            " (a demo name from R4_DEMO_SPECS, or 'all')",
            {
                "type": "object",
                "properties": {"demo": _str_param("demo name or 'all'", "all")},
            },
            _demo_plan,
        )
    ]
    if client is not None:
        tools.append(
            _tool(
                "demo_prepare",
                "Execute one demo prep step: upload_bundle | stage | trigger."
                " Run demo_plan first and confirm source/project with ask_user",
                {
                    "type": "object",
                    "properties": {
                        "demo": _str_param("demo name, as demo_plan lists it"),
                        "action": _str_param("upload_bundle | stage | trigger"),
                        "video_dir": _str_param("Override video directory (stage)"),
                        "input_key": _str_param("Staged s3 key (trigger)"),
                        "project_id": _str_param("Target project id"),
                        "params": _str_param("Extra trigger parameters as JSON"),
                        "wait": {"type": "boolean", "default": False},
                    },
                    "required": ["demo", "action"],
                },
                lambda **kw: _demo_prepare(client, **kw),
            )
        )
    return tools


# ---------------------------------------------------------------------------
# Client-gated one-to-one gap wrappers
# ---------------------------------------------------------------------------


def build_client_gap_tools(client: "Client") -> list[dict[str, Any]]:
    def _endpoint_upgrade(endpoint_id: str, model_path: str, wait: bool = False) -> str:
        result = client.upgrade_endpoint(endpoint_id, model_path, wait=wait)
        return json.dumps(result, indent=2, default=str)

    def _approve_gate(
        execution_id: str,
        step_name: str,
        approved: bool = True,
        approver: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> str:
        result = client.workflows.approve_gate(
            execution_id, step_name, approved=approved, approver=approver, comment=comment
        )
        return json.dumps(result, indent=2, default=str)

    def _mcp_register_server(
        name: str,
        url: str,
        description: str = "",
        auth_type: str = "none",
    ) -> str:
        result = client.mcp.register_server(
            name=name, url=url, description=description, auth_type=auth_type
        )
        return json.dumps(result, indent=2, default=str)

    def _mcp_unregister_server(server_id: str) -> str:
        result = client.mcp.unregister_server(server_id)
        payload = {"unregistered": bool(result), "server_id": server_id}
        return json.dumps(payload, indent=2)

    def _project_status(project: Optional[str] = None, section: Optional[str] = None) -> str:
        from .status_report import build_project_status

        snapshot = build_project_status(client, project_id=project, include_local=True)
        if not section:
            return snapshot
        wanted = section.strip().lower()
        blocks = [b for b in snapshot.split("\n\n") if wanted in b.splitlines()[0].lower()]
        return "\n\n".join(blocks) or f"No section matching '{section}'."

    return [
        _tool(
            "endpoint_upgrade",
            "Rolling model-version upgrade of a running endpoint",
            {
                "type": "object",
                "properties": {
                    "endpoint_id": _str_param("Endpoint instance id"),
                    "model_path": _str_param("New model path/name"),
                    "wait": {"type": "boolean", "default": False},
                },
                "required": ["endpoint_id", "model_path"],
            },
            _endpoint_upgrade,
        ),
        _tool(
            "workflow_approve_gate",
            "Approve or reject a waiting manual-gate step of a workflow execution",
            {
                "type": "object",
                "properties": {
                    "execution_id": _str_param("Execution id"),
                    "step_name": _str_param("Gate step name"),
                    "approved": {"type": "boolean", "default": True},
                    "approver": _str_param("Approver identity (optional)"),
                    "comment": _str_param("Decision comment (optional)"),
                },
                "required": ["execution_id", "step_name"],
            },
            _approve_gate,
        ),
        _tool(
            "mcp_register_server",
            "Register a user-managed MCP server with the platform (X-206);"
            " verify afterwards with mcp_list_tools",
            {
                "type": "object",
                "properties": {
                    "name": _str_param("Unique server name"),
                    "url": _str_param("Server base URL"),
                    "description": _str_param("Server description", ""),
                    "auth_type": _str_param("none | bearer | api_key", "none"),
                },
                "required": ["name", "url"],
            },
            _mcp_register_server,
        ),
        _tool(
            "mcp_unregister_server",
            "Unregister an MCP server by id",
            {
                "type": "object",
                "properties": {"server_id": _str_param("Server id")},
                "required": ["server_id"],
            },
            _mcp_unregister_server,
        ),
        _tool(
            "project_status",
            "Aggregate read-only status: health, endpoints, models, workflows"
            " (registered + running jobs), MCP servers/tools, knowledge graphs,"
            " research loops/sessions, RAG documents, catalog, skills",
            {
                "type": "object",
                "properties": {
                    "project": _str_param("Project id filter (optional)"),
                    "section": _str_param("Return only one section (optional)"),
                },
            },
            _project_status,
        ),
    ]


def build_client_extra_tools(client: Optional["Client"]) -> list[dict[str, Any]]:
    """Aggregate builder plugged into get_builtin_tools()."""
    tools = build_source_tools()
    tools.extend(build_pool_tools())
    tools.extend(build_demo_tools(client))
    if client is not None:
        tools.extend(build_client_gap_tools(client))
    return tools
