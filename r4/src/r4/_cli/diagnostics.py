"""Shared diagnostic checks for ``r4 health`` and compatibility commands."""

from __future__ import annotations

import json
import os
import time
from typing import Any


def auth_configured(profile: str, *, config=None) -> bool:
    """Return whether a profile has locally usable credentials.

    When *config* is provided, also accepts ``config.api_key`` set by the
    active source overlay (sources.json) — even if that key is not stored
    under the profile name in the token/API-key store.
    """
    try:
        from ..auth import get_auth_manager

        if get_auth_manager(profile).is_authenticated():
            return True
        # Source overlay may inject api_key under a different profile name
        if config is not None and getattr(config, "api_key", None):
            return True
        return False
    except Exception:
        return False


def health_context(config) -> dict[str, object]:
    """Return the local runtime context shown by quick health checks."""
    return {
        "profile": config.profile,
        "project_id": config.project_id,
        "gateway_url": config.gateway_url,
        "model": config.model,
        "auth_configured": auth_configured(config.profile, config=config),
    }


def health_payload(config, readiness: dict) -> dict[str, object]:
    """Build the stable JSON payload for quick health checks."""
    return {
        "ready": bool(readiness.get("ready")),
        "checks": readiness.get("checks", {}),
        "context": health_context(config),
    }


def probe_umrs_direct_payload(default_url: str | None = None) -> dict[str, object]:
    """Probe UMRS directly for JSON health output."""
    import urllib.request

    umrs_url = os.environ.get("UMRS_BASE_URL") or default_url
    if not umrs_url:
        return {
            "ready": None,
            "message": "n/a (UMRS reached via gateway, not directly)",
        }

    try:
        req = urllib.request.Request(f"{umrs_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode()
        return {"ready": True, "url": umrs_url, "response_preview": data[:200]}
    except Exception as exc:
        return {"ready": False, "url": umrs_url, "error": str(exc)}


def check_gateway(config) -> dict[str, Any]:
    """Probe the platform Gateway readiness endpoint."""
    start = time.time()
    try:
        from ..client import Client

        data = Client(config=config).readiness()
        latency = int((time.time() - start) * 1000)
        return {
            "ok": bool(data.get("ready")),
            "latency_ms": latency,
            "checks": data.get("checks", {}),
            "message": data.get("message"),
        }
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "error": str(exc)}


def check_umrs(config) -> dict[str, Any]:
    """Probe UMRS — via HTTP if ``UMRS_BASE_URL`` is set, or via ZMQ when transport is direct."""
    result = probe_umrs_direct_payload()
    if result.get("ready") is True:
        return {"ok": True, "url": result.get("url"), "transport": "http"}
    if result.get("ready") is False:
        return {"ok": False, "url": result.get("url"), "error": result.get("error")}

    if _uses_remote_zmq(config):
        return _probe_umrs_zmq(config)

    return {"ok": None, "message": result.get("message")}


def _uses_remote_zmq(config) -> bool:
    from ..config import DeployMode, TransportMode

    direct = config.transport_mode == TransportMode.DIRECT
    auto_hpc = config.transport_mode == TransportMode.AUTO and config.deploy_mode in (
        DeployMode.HPC_LOCAL,
        DeployMode.HYBRID,
    )
    return (direct or auto_hpc) and config.umrs_server_host != "127.0.0.1"


def _probe_umrs_zmq(config) -> dict[str, Any]:
    try:
        from ..source_context import transport_session_fields
        from ..umrs_transport import UMRSTransport, UMRSTransportConfig

        transport_config = UMRSTransportConfig.for_server(
            server_host=config.umrs_server_host,
            control_port=config.umrs_control_port,
            tunnel_mode=config.umrs_tunnel_mode,
            **transport_session_fields(config),
        )
        transport = UMRSTransport(transport_config)
        transport.connect(timeout_ms=3_000)
        username = config.umrs_username or os.environ.get("USER", "r4")
        transport.request_status_cmd(username, "all")
        return _umrs_zmq_result(config, ok=True)
    except Exception as exc:
        result = _umrs_zmq_result(config, ok=False)
        result["error"] = str(exc)
        return result


def _umrs_zmq_result(config, *, ok: bool) -> dict[str, Any]:
    return {
        "ok": ok,
        "transport": "zmq",
        "host": config.umrs_server_host,
        "port": config.umrs_control_port,
    }


def check_auth(config) -> dict[str, Any]:
    """Check that stored credentials are accepted by Gateway."""
    try:
        from ..auth import get_auth_manager
        from ..client import Client

        auth_manager = get_auth_manager(config.profile)
        has_creds = auth_manager.get_auth_header() is not None
        # Source overlay may inject api_key under a different profile name
        if not has_creds and not getattr(config, "api_key", None):
            return {"ok": False, "error": "no stored credentials"}
        _client = Client(config=config)
        _client.get(
            f"{getattr(_client, '_api_prefix', '/gw')}/models", params={"limit": 1}, timeout=10
        )
        return {"ok": True, "method": "bearer"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_account(config) -> dict[str, Any]:
    """Return account/authentication status for the active profile."""
    result: dict[str, Any] = {
        "ok": False,
        "profile": config.profile,
        "gateway_url": config.gateway_url,
        "project_id": config.project_id,
        "auth_configured": False,
        "accepted": False,
    }
    try:
        from ..auth import get_auth_manager
        from ..client import Client

        auth_manager = get_auth_manager(config.profile)
        result["auth_configured"] = auth_manager.is_authenticated()
        # Source overlay may inject api_key under a different profile name
        if not result["auth_configured"] and getattr(config, "api_key", None):
            result["auth_configured"] = True
        if not result["auth_configured"]:
            result["error"] = "no stored credentials"
            return result
        _client = Client(config=config)
        _client.get(
            f"{getattr(_client, '_api_prefix', '/gw')}/models", params={"limit": 1}, timeout=10
        )
        result["ok"] = True
        result["accepted"] = True
        result["method"] = "bearer"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def check_providers(config=None) -> dict[str, Any]:
    """Probe all registered providers via Provider.health()."""
    from ..providers import ProviderStatus
    from ..providers.factory import build_registry

    registry = build_registry(config)
    results: list[dict[str, Any]] = []

    for provider in registry:
        try:
            health = provider.health()
            entry: dict[str, Any] = {
                "name": provider.name,
                "ok": health.status == ProviderStatus.HEALTHY,
                "status": health.status.value,
                "latency_ms": int(health.latency_ms),
                "models": health.models_available,
                "local": provider.is_local,
            }
            if health.error:
                entry["error"] = health.error
            results.append(entry)
        except Exception as exc:
            results.append({"name": provider.name, "ok": False, "error": str(exc)})

    if not results:
        return {"ok": None, "message": "no providers registered", "providers": results}
    if any(r.get("ok") for r in results):
        return {"ok": True, "providers": results}
    return {"ok": None, "message": "no healthy providers", "providers": results}


def check_s3() -> dict[str, Any]:
    """Check S3/MinIO connectivity when configured."""
    endpoint = os.environ.get("UMRS_S3_ENDPOINT") or os.environ.get("AWS_ENDPOINT_URL")
    if not endpoint:
        return {"ok": None, "message": "n/a (S3 managed by umrs, not r4 directly)"}
    try:
        import urllib.request

        req = urllib.request.Request(f"{endpoint}/minio/health/live", method="GET")
        with urllib.request.urlopen(req, timeout=5):
            pass
        return {"ok": True, "endpoint": endpoint}
    except Exception as exc:
        return {"ok": False, "endpoint": endpoint, "error": str(exc)}


def check_config(config) -> dict[str, Any]:
    """Validate local config files and repository context."""
    from ..briefing import find_r4_md
    from ..config import _find_repo_root

    issues = list(config.validate())
    info: dict[str, Any] = {
        "profile": config.profile,
        "deploy_mode": config.deploy_mode.value,
        "transport_mode": config.transport_mode.value,
    }

    r4_md = find_r4_md()
    info["r4_md"] = str(r4_md) if r4_md else None
    repo_root = _find_repo_root()
    info["repo_root"] = str(repo_root) if repo_root else None
    return {"ok": len(issues) == 0, "issues": issues, **info}


# A diagnostic probe answers quickly or reports "not reachable": with the
# client's default read timeout (config.timeout, 300 s) `r4 doctor` and
# `r4 health --full` sat silent for minutes on a stalled gateway (c2-cli,
# 2026-10-21). Matches the auth/account probes' 10 s.
_PROBE_TIMEOUT_SEC = 10


def check_mcp(config) -> dict[str, Any]:
    """Probe MCP registry via Gateway."""
    try:
        from ..client import Client

        client = Client(config=config)
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/mcp/servers", timeout=_PROBE_TIMEOUT_SEC
        )
        count = len(data) if isinstance(data, list) else 0
        return {"ok": True, "registered_servers": count}
    except Exception as exc:
        return {"ok": None, "message": f"not reachable ({exc})"}


def check_rag(config) -> dict[str, Any]:
    """Probe RAG service via Gateway."""
    try:
        from ..client import Client

        client = Client(config=config)
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/rag/stats",
            params={"project_id": config.project_id or "default"},
            timeout=_PROBE_TIMEOUT_SEC,
        )
        return {
            "ok": True,
            "documents": data.get("documents", 0),
            "chunks": data.get("chunks", 0),
            "embedding_model": data.get("embedding_model", ""),
        }
    except Exception as exc:
        return {"ok": None, "message": f"not reachable ({exc})"}


def check_embedding(config) -> dict[str, Any]:
    """Probe embedding backend via RAG stats to verify model is loaded."""
    try:
        from ..client import Client

        client = Client(config=config)
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/rag/stats",
            params={"project_id": config.project_id or "default"},
            timeout=_PROBE_TIMEOUT_SEC,
        )
        model = data.get("embedding_model", "")
        dim = data.get("embedding_dim", 0)
        if model:
            return {"ok": True, "model": model, "dimensions": dim}
        return {"ok": False, "error": "no embedding model reported"}
    except Exception as exc:
        return {"ok": None, "message": f"not reachable ({exc})"}


def check_pgvector(config) -> dict[str, Any]:
    """Probe pgvector reachability via RAG document count."""
    try:
        from ..client import Client

        client = Client(config=config)
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/rag/stats",
            params={"project_id": config.project_id or "default"},
            timeout=_PROBE_TIMEOUT_SEC,
        )
        return {
            "ok": True,
            "chunks": data.get("chunks", 0),
            "documents": data.get("documents", 0),
        }
    except Exception as exc:
        from ..errors import APIError

        if isinstance(exc, APIError) and getattr(exc, "status_code", 0) == 503:
            return {"ok": False, "error": "pgvector/RAG database unreachable"}
        return {"ok": None, "message": f"not reachable ({exc})"}


def check_audit(config) -> dict[str, Any]:
    """Probe UMRS audit event infrastructure via health endpoint."""
    umrs_url = os.environ.get("UMRS_BASE_URL")
    if not umrs_url:
        return _audit_no_http_result(config)
    try:
        return _audit_http_result(umrs_url)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _audit_no_http_result(config) -> dict[str, Any]:
    if _uses_remote_zmq(config):
        return {"ok": None, "message": "n/a (audit logs on server, not probed via ZMQ)"}
    return {"ok": None, "message": "n/a (audit logs on server, not probed via gateway)"}


def _audit_http_result(umrs_url: str) -> dict[str, Any]:
    data = _read_umrs_health(umrs_url)
    audit_info = data.get("audit") if isinstance(data, dict) else None
    if isinstance(data, dict) and data.get("status") == "ok":
        result: dict[str, Any] = {"ok": True}
        if audit_info:
            result["audit"] = audit_info
        return result
    return {"ok": True, "message": "health endpoint reachable"}


def _read_umrs_health(umrs_url: str) -> Any:
    import urllib.request

    req = urllib.request.Request(f"{umrs_url}/health", method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def check_umrs_profile_sync(config) -> dict[str, Any]:
    """Check whether the umrs connection profile exists and matches r4.

    Uses only stdlib (JSON file reads) — no umrs package import.
    """
    from pathlib import Path

    # Prefer the active source name (source-first model), fall back to profile
    try:
        from ..source import resolve_active_source

        src = resolve_active_source()
        profile_name = src.name if src else config.profile
    except Exception:
        profile_name = config.profile

    # Resolve umrs config dir
    umrs_cfg = os.environ.get("UMRS_CONFIG")
    if umrs_cfg:
        umrs_dir = Path(umrs_cfg).expanduser().resolve()
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        umrs_dir = Path(xdg, "umrs") if xdg else Path.home() / ".config" / "umrs"

    profile_path = umrs_dir / "connections" / f"{profile_name}.json"
    token_path = profile_path.with_suffix(".token")

    info: dict[str, Any] = {
        "profile_name": profile_name,
        "umrs_profile_path": str(profile_path),
    }

    if not profile_path.is_file():
        info["ok"] = False
        info["error"] = "umrs profile not found (run: r4 login)"
        return info

    try:
        umrs_data = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        info["ok"] = False
        info["error"] = f"cannot read umrs profile: {exc}"
        return info

    # Check field sync
    mismatches: list[str] = []
    r4_gw = (config.gateway_url or "").rstrip("/")
    umrs_gw = (umrs_data.get("gateway_url") or "").rstrip("/")
    if r4_gw and umrs_gw and r4_gw != umrs_gw:
        mismatches.append(f"gateway_url: r4={r4_gw} umrs={umrs_gw}")
    elif r4_gw and not umrs_gw:
        mismatches.append("gateway_url: missing in umrs profile")

    zmq_cfg = umrs_data.get("zmq", {})
    info["umrs_zmq_host"] = zmq_cfg.get("host")
    info["umrs_zmq_port"] = zmq_cfg.get("control_port")
    info["umrs_gateway_url"] = umrs_gw
    info["has_token"] = token_path.is_file()

    # Check active profile pointer
    pointer_path = umrs_dir / "current_connection_profile"
    if pointer_path.is_file():
        active = pointer_path.read_text(encoding="utf-8").strip()
        info["umrs_active_profile"] = active
        if active != profile_name:
            mismatches.append(f"active profile: r4={profile_name} umrs={active}")

    if mismatches:
        info["ok"] = False
        info["error"] = "; ".join(mismatches)
    else:
        info["ok"] = True

    return info


def run_full_diagnostics(config) -> dict[str, dict[str, Any]]:
    """Run comprehensive platform diagnostics.

    Independent network probes run in parallel to minimize wall-clock time.
    Local-only checks (config, umrs_profile) run synchronously first.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Fast local checks first (no network)
    results: dict[str, dict[str, Any]] = {
        "config": check_config(config),
        "umrs_profile": check_umrs_profile_sync(config),
    }

    # Network checks — run in parallel
    checks = {
        "auth": lambda: check_auth(config),
        "gateway": lambda: check_gateway(config),
        "umrs": lambda: check_umrs(config),
        "mcp": lambda: check_mcp(config),
        "rag": lambda: check_rag(config),
        "embedding": lambda: check_embedding(config),
        "pgvector": lambda: check_pgvector(config),
        "audit": lambda: check_audit(config),
        "providers": lambda: check_providers(config),
        "s3": check_s3,
    }

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in checks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}

    # Stable output order
    ordered = [
        "config",
        "auth",
        "gateway",
        "umrs",
        "umrs_profile",
        "mcp",
        "rag",
        "embedding",
        "pgvector",
        "audit",
        "providers",
        "s3",
    ]
    return {k: results[k] for k in ordered if k in results}


def symbol(ok: Any) -> str:
    """Return a compact human status marker."""
    if ok is True:
        return "✓"
    if ok is False:
        return "✗"
    return "–"


def diagnostics_exit_code(checks: dict[str, dict[str, Any]]) -> int:
    """Return a process status for diagnostic check dictionaries."""
    return 0 if all(result.get("ok") is not False for result in checks.values()) else 1


def print_json(payload: object) -> None:
    """Print JSON consistently for diagnostic commands."""
    print(json.dumps(payload, indent=2, default=str))


def emit_full_diagnostics(
    checks: dict[str, dict[str, Any]],
    *,
    as_json: bool,
    title: str = "R4 Health",
    verbose: bool = False,
) -> None:
    """Render comprehensive diagnostics."""
    if as_json:
        print_json(checks)
        return

    print(f"{title}\n")
    for name, result in checks.items():
        _print_diagnostic_result(name, result, verbose=verbose)

    config_info = checks.get("config", {})
    _print_config_paths(config_info)
    _print_sandbox_line(checks.get("local", {}))
    _print_provider_models(checks.get("providers", {}))
    _print_transport_hints(checks, config_info)


def _print_sandbox_line(local: dict[str, Any]) -> None:
    """ADR-101: the local tool sandbox line (``r4 doctor``), also in plain text."""
    nested = local.get("checks") if isinstance(local, dict) else None
    sandbox = nested.get("sandbox") if isinstance(nested, dict) else None
    if not isinstance(sandbox, dict) or not sandbox.get("message"):
        return
    default = sandbox.get("default")
    suffix = f" · default: {default}" if default else ""
    print(f"\n  {sandbox['message']}{suffix}")


def _diagnostic_detail(result: dict[str, Any]) -> str:
    ok = result.get("ok")
    if not ok and result.get("error"):
        return f"  ({result['error']})"
    if ok and result.get("latency_ms"):
        return f"  ({result['latency_ms']}ms)"
    if ok is None and result.get("message"):
        return f"  ({result['message']})"
    return ""


def _print_diagnostic_result(name: str, result: dict[str, Any], *, verbose: bool) -> None:
    print(f"  {symbol(result.get('ok'))} {name}{_diagnostic_detail(result)}")
    if verbose:
        _print_diagnostic_verbose_fields(result)


def _print_diagnostic_verbose_fields(result: dict[str, Any]) -> None:
    hidden_fields = {"ok", "error", "latency_ms", "message"}
    for key, value in result.items():
        if key not in hidden_fields:
            print(f"      {key}: {value}")


def _print_config_paths(config_info: dict[str, Any]) -> None:
    if config_info.get("r4_md"):
        print(f"\n  R4.md: {config_info['r4_md']}")
    if config_info.get("repo_root"):
        print(f"  Repo root: {config_info['repo_root']}")


def _print_provider_models(providers: dict[str, Any]) -> None:
    provider_list = providers.get("providers", [])
    if not provider_list:
        return
    print("")
    for provider in provider_list:
        name = provider.get("name", "?")
        status = provider.get("status", "unknown")
        local_tag = " (local)" if provider.get("local") else ""
        if provider.get("ok"):
            models = ", ".join(provider.get("models", []))
            latency = provider.get("latency_ms", "")
            lat_str = f" {latency}ms" if latency else ""
            print(f"  ✓ {name}{local_tag}:{lat_str} {models}")
        else:
            err = provider.get("error", status)
            print(f"  ✗ {name}{local_tag}: {err}")


def _print_transport_hints(checks: dict[str, dict[str, Any]], config_info: dict[str, Any]) -> None:
    umrs_info = checks.get("umrs", {})
    if umrs_info.get("transport") == "zmq":
        print(f"\n  ZMQ transport: {umrs_info.get('host')}:{umrs_info.get('port')}")
    if not _should_show_quick_start(config_info):
        return
    hints = _quick_start_hints(checks, umrs_info)
    if hints:
        print("\n  Quick start:")
        for hint in hints:
            print(f"    $ {hint}")


def _should_show_quick_start(config_info: dict[str, Any]) -> bool:
    deploy_mode = config_info.get("deploy_mode", "")
    transport_mode = config_info.get("transport_mode", "")
    return deploy_mode in ("hybrid", "hpc-local") or transport_mode == "direct"


def _quick_start_hints(
    checks: dict[str, dict[str, Any]],
    umrs_info: dict[str, Any],
) -> list[str]:
    gw_ok = checks.get("gateway", {}).get("ok")
    hints = _umrs_quick_start_hints(umrs_info, gw_ok=gw_ok)
    if gw_ok and not umrs_info.get("ok"):
        hints.append("r4 model list       — try listing models via gateway")
    return hints


def _umrs_quick_start_hints(umrs_info: dict[str, Any], *, gw_ok: Any) -> list[str]:
    if umrs_info.get("ok") is True:
        return [
            "r4 chat 'hello'    — start chatting",
            "r4 model list       — list available models",
            "r4 job list          — list jobs",
        ]
    if umrs_info.get("ok") is False and umrs_info.get("transport") == "zmq":
        hints = [
            "Check UMRS is running on the server and CURVE keys are configured",
            "Set UMRS_ZMQ_CURVE_* env vars if CURVE auth is enabled",
        ]
        if gw_ok:
            hints.append("r4 chat 'hello'    — gateway is up, try chatting via HTTP")
        return hints
    if umrs_info.get("ok") is None:
        return ["Set UMRS_SERVER_HOST or re-run bootstrap --configure-client to configure ZMQ"]
    return []


def emit_account_status(status: dict[str, Any], *, as_json: bool) -> None:
    """Render account diagnostics."""
    if as_json:
        print_json(status)
        return

    print("R4 Account\n")
    print(f"  {symbol(status.get('auth_configured'))} credentials stored")
    print(f"  {symbol(status.get('accepted'))} credentials accepted by Gateway")
    print(f"  Profile: {status.get('profile')}")
    print(f"  Gateway: {status.get('gateway_url')}")
    if status.get("project_id"):
        print(f"  Project: {status.get('project_id')}")
    if status.get("error"):
        print(f"  Error: {status['error']}")
