"""``r4 pool`` command — provision multiple endpoints from a YAML roster.

MM-4-MA Phase D. A *pool* is a roster of single-spec mini-RiVault endpoints
that the gateway router can dispatch across based on per-request task or
agent hints (X-R4-Task / X-R4-Agent).

Schema:

    pool: research-team           # optional label, currently informational
    defaults:                     # optional defaults applied to each entry
      tier: project
      wait: false
    endpoints:
      - name: chat                # informational, used in summary
        model: chat-3b
      - name: coder
        model: Qwen/Qwen3-Coder-7B
        tier: project
      - name: reasoner
        model: deepseek-r1
        loras:
          - name: chem_v2
            path: /shared/loras/chem_v2

Each entry maps directly to ``r4 endpoint up`` semantics. The pool command
provisions sequentially by default (so failures are surfaced immediately)
and prints a summary table.  Use ``--parallel`` to submit all Slurm jobs
concurrently — useful when you need several endpoints up quickly.
"""

from __future__ import annotations

import hashlib
import json
import sys
from argparse import ArgumentParser, Namespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..client import Client
from ..config import _resolve_config_dir
from ._helpers import _die, _is_http_404, _parse_context_len
from .base import CommandHandler
from .registry import command


def _positive_int(value: Any) -> int | None:
    """Parse a positive integer from various types."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@command
class PoolCommand(CommandHandler):
    """``r4 pool`` — provision a roster of endpoints (MM-4-MA Phase D)."""

    name = "pool"
    help = "Provision or stop a roster of endpoints from a YAML file"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Provision or stop multiple mini-RiVault endpoints from a YAML "
                "roster. Useful with X-R4-Task routing (`r4 chat --task ...`)."
            ),
        )
        sub = parser.add_subparsers(dest="pool_command", metavar="<pool-command>")
        sub.required = True
        _register_pool_up(sub)
        _register_pool_down(sub)
        return parser

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="pool")

        if args.pool_command == "up":
            return _cmd_pool_up(args)
        if args.pool_command == "down":
            return _cmd_pool_down(args)
        return 2


def _register_pool_up(sub) -> None:
    up = sub.add_parser("up", help="Provision all endpoints in a roster")
    up.add_argument("file", help="Path to the roster YAML file")
    up.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep going if an individual endpoint fails to provision",
    )
    up.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit a JSON summary instead of a human-readable table",
    )
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved entries without calling the gateway",
    )
    up.add_argument(
        "--sequential",
        action="store_true",
        help="Submit Slurm jobs one-by-one instead of in parallel (default: parallel)",
    )


def _register_pool_down(sub) -> None:
    down = sub.add_parser("down", help="Stop all endpoints tracked by a roster")
    down.add_argument("file", help="Path to the roster YAML file used for provisioning")
    down.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep going if an individual endpoint fails to stop",
    )
    down.add_argument(
        "--force",
        action="store_true",
        help="Force stop without graceful shutdown",
    )
    down.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit a JSON summary instead of a human-readable table",
    )


def _load_roster(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        print(
            "Error: 'pyyaml' is required for `r4 pool up`. Install with: pip install pyyaml",
            file=sys.stderr,
        )
        sys.exit(2)
    if not path.exists():
        print(f"Error: roster file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        print(f"Error: failed to parse roster YAML: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("Error: roster file must be a YAML mapping at the top level.", file=sys.stderr)
        sys.exit(2)
    return data


def _resolve_entries(roster: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply defaults to each endpoint entry; validate required fields."""
    defaults = roster.get("defaults") or {}
    if not isinstance(defaults, dict):
        print("Error: 'defaults' must be a mapping.", file=sys.stderr)
        sys.exit(2)
    raw_entries = roster.get("endpoints")
    if not isinstance(raw_entries, list) or not raw_entries:
        print(
            "Error: roster must declare a non-empty 'endpoints' list.",
            file=sys.stderr,
        )
        sys.exit(2)

    resolved: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            print(f"Error: endpoints[{idx}] must be a mapping.", file=sys.stderr)
            sys.exit(2)
        merged = {**defaults, **entry}
        if not merged.get("model"):
            print(
                f"Error: endpoints[{idx}] is missing required field 'model'.",
                file=sys.stderr,
            )
            sys.exit(2)
        merged.setdefault("name", merged["model"])
        merged.setdefault("tier", "user")
        resolved.append(merged)

    # Expand entries with replicas > 1 into multiple entries.
    # Each replica gets a unique name suffix ("-r2", "-r3", ...)
    # but shares the same variant (base name) so the registry groups them.
    expanded: list[dict[str, Any]] = []
    for entry in resolved:
        replicas = _positive_int(entry.get("replicas")) or 1
        if replicas <= 1:
            expanded.append(entry)
            continue
        base_name = entry.get("name", entry["model"])
        for i in range(1, replicas + 1):
            replica = dict(entry)
            if i > 1:
                replica["name"] = f"{base_name}-r{i}"
            # Store base variant so replicas share the same group
            replica["_base_variant"] = base_name
            expanded.append(replica)
    return expanded


def _format_ctx_literal(value: int) -> str:
    """Format a context-length value for display/state files."""
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024}k"
    return str(value)


def _pool_state_path(roster_path: Path) -> Path:
    """Return the local state file used to track pool instance ids."""
    resolved = roster_path.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return _resolve_config_dir() / "pool-state" / f"{resolved.stem}-{digest}.json"


def _matching_pool_state_path(roster_path: Path) -> Path:
    state_path = _pool_state_path(roster_path)
    if state_path.exists():
        return state_path
    return _pool_state_path_from_payload(roster_path) or state_path


def _pool_state_path_from_payload(roster_path: Path) -> Path | None:
    resolved = roster_path.expanduser().resolve()
    state_dir = _resolve_config_dir() / "pool-state"
    if not state_dir.exists():
        return None
    for candidate in state_dir.glob(f"{resolved.stem}-*.json"):
        try:
            payload = json.loads(candidate.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if payload.get("roster_file") == str(resolved):
            return candidate
    return None


def _write_pool_state(roster_path: Path, payload: dict[str, Any]) -> None:
    """Persist pool instance tracking state."""
    state_path = _pool_state_path(roster_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2))


def _save_pool_state(roster_path: Path, pool_label: str, results: list[dict[str, Any]]) -> None:
    """Persist the successful instance ids for a provisioned roster."""
    entries = [
        {
            "name": r["name"],
            "model": r["model"],
            "tier": r["tier"],
            "instance_id": r["instance_id"],
            "endpoint_url": r.get("endpoint_url"),
            "source": r.get("source"),
        }
        for r in results
        if r.get("ok") and r.get("instance_id")
    ]
    state_path = _matching_pool_state_path(roster_path)
    if not entries:
        if state_path.exists():
            state_path.unlink()
        return
    _write_pool_state(
        roster_path,
        {
            "pool": pool_label,
            "roster_file": str(roster_path.expanduser().resolve()),
            "entries": entries,
        },
    )


def _load_pool_state(roster_path: Path) -> dict[str, Any]:
    """Load tracked instance ids for a roster file."""
    state_path = _matching_pool_state_path(roster_path)
    if not state_path.exists():
        _die(
            f"No tracked instances found for roster: {roster_path}",
            action="pool down",
            hint=(
                "Provision it first with `r4 up --pool-file <file>` or `r4 pool up <file>`, "
                "or stop endpoints individually with `r4 down <instance-id>`."
            ),
            code=2,
        )
    try:
        payload = json.loads(state_path.read_text())
    except json.JSONDecodeError as exc:
        _die(
            f"Pool state is unreadable: {exc}",
            action="pool down",
            hint=f"Delete {_pool_state_path(roster_path)} and reprovision the roster.",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        _die(
            "Pool state is malformed.",
            action="pool down",
            hint=f"Delete {_pool_state_path(roster_path)} and reprovision the roster.",
        )
    return payload


def _resolve_entry_max_model_len(entry: dict[str, Any], *, action: str) -> int | None:
    """Parse an optional max_model_len value from a roster entry."""
    raw_value = entry.get("max_model_len")
    if raw_value is None:
        return None
    parsed = _parse_context_len(raw_value)
    if parsed is None:
        entry_label = entry.get("name", entry.get("model", "?"))
        _die(
            f"Invalid max_model_len for entry '{entry_label}': {raw_value!r}",
            action=action,
            code=2,
        )
    return parsed


def _reconcile_submitted_endpoint(
    client: Any,
    *,
    model: str,
    tier: str,
    submit_started_at: float,
    grace_window_sec: float = 90.0,
) -> dict[str, Any] | None:
    """Look up a recently-submitted endpoint that matches a failed submission.

    Used as a fallback path when the provision HTTP call timed out or
    failed mid-flight: UMRS may have already created a Slurm job and
    registered the endpoint.  We try to find it so the user is not
    misled into thinking provisioning failed.

    Returns the normalized endpoint record if a plausible match is
    found, otherwise ``None``.
    """
    from datetime import datetime, timezone

    try:
        response = client.list_endpoints(include_stopped=False)
    except Exception:  # noqa: BLE001 - reconciliation is best-effort
        return None

    candidates: list[dict[str, Any]] = []
    cutoff_dt = datetime.fromtimestamp(submit_started_at - grace_window_sec, tz=timezone.utc)
    for ep in response.get("endpoints", []):
        if _is_reconcile_candidate(ep, model=model, tier=tier, cutoff_dt=cutoff_dt):
            candidates.append(ep)

    if not candidates:
        return None

    # Prefer the most recently created candidate.
    def _created_key(ep: dict[str, Any]) -> str:
        return str(ep.get("created_at") or "")

    candidates.sort(key=_created_key, reverse=True)
    return candidates[0]


def _is_reconcile_candidate(
    endpoint: dict[str, Any],
    *,
    model: str,
    tier: str,
    cutoff_dt,
) -> bool:
    if endpoint.get("model") != model:
        return False
    endpoint_tier = str(endpoint.get("tier") or endpoint.get("owner_type") or "user").lower()
    if tier and endpoint_tier and endpoint_tier != tier.lower():
        return False
    if _endpoint_status_is_terminal(endpoint):
        return False
    return _endpoint_created_after_cutoff(endpoint, cutoff_dt)


def _endpoint_status_is_terminal(endpoint: dict[str, Any]) -> bool:
    status = str(endpoint.get("status") or "").lower()
    return status in {"failed", "terminated", "expired", "deprovisioned"}


def _endpoint_created_after_cutoff(endpoint: dict[str, Any], cutoff_dt) -> bool:
    from datetime import datetime

    created_at = endpoint.get("created_at")
    if not created_at:
        return True
    try:
        created_dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return created_dt >= cutoff_dt


def _provision_one_entry(
    client: Any,
    entry: dict[str, Any],
    *,
    default_timeout: int | None,
    wait: bool,
    progress_cb: "Any" = None,
) -> dict[str, Any]:
    """Provision a single endpoint entry.  Returns a result dict.

    ``progress_cb`` (optional) is ``progress_cb(state: str, detail: str)``
    invoked at submission start, after the call returns, and on
    reconciliation. ``state`` is one of ``submitting``, ``submitted``,
    ``ready``, ``reconciled``, ``failed``, ``unknown``.

    If the entry has a ``source`` key, a per-source Client is created
    targeting that source's gateway URL instead of the default.
    """
    import time as _time

    effective_client = _resolve_entry_client(client, entry)
    spec = _entry_provision_spec(entry, default_timeout=default_timeout)
    _entry_progress(progress_cb, "submitting", "contacting UMRS")

    submit_started_at = _time.time()
    try:
        response = _call_provision_endpoint(effective_client, entry, spec, wait=wait)
        result = _provision_success_result(spec, response)
        if entry.get("source"):
            result["source"] = entry["source"]
        _entry_progress(progress_cb, result["state"], f"job_id={result.get('instance_id') or '?'}")
        return result
    except Exception as exc:  # noqa: BLE001 - report and continue/abort
        result = _handle_provision_error(
            effective_client, spec, submit_started_at, exc, progress_cb
        )
        if entry.get("source"):
            result["source"] = entry["source"]
        return result


def _resolve_entry_client(default_client: Any, entry: dict[str, Any]) -> Any:
    """Return a Client for the entry's source, or the default client."""
    source_name = entry.get("source")
    if not source_name:
        return default_client

    from ..client import Client
    from ..config import get_config
    from ..source import get_source_store
    from ..source_context import config_for_source

    source = get_source_store().get(source_name)
    if source is None:
        entry_label = entry.get("name", entry.get("model", "?"))
        print(
            f"Warning: source '{source_name}' not found for entry '{entry_label}', "
            f"using default client.",
            file=sys.stderr,
        )
        return default_client

    config = get_config()
    if source.kind == "platform":
        config = config_for_source(source, config)  # its own credential only (S1)
    return Client(config=config)


def _entry_progress(progress_cb: Any, state: str, detail: str) -> None:
    if progress_cb:
        progress_cb(state, detail)


def _handle_provision_error(
    client: Any,
    spec: dict[str, Any],
    submit_started_at: float,
    exc: Exception,
    progress_cb: Any,
) -> dict[str, Any]:
    _entry_progress(progress_cb, "reconciling", f"submission error: {exc}")
    reconciled = _reconcile_provision_failure(client, spec, submit_started_at)
    if reconciled is not None:
        _entry_progress(progress_cb, "reconciled", _reconciled_job_detail(reconciled))
        return _provision_reconciled_result(spec, reconciled)
    _entry_progress(progress_cb, "failed", str(exc))
    return _provision_failed_result(spec, exc)


def _entry_provision_spec(entry: dict[str, Any], *, default_timeout: int | None) -> dict[str, Any]:
    model = entry["model"]
    loras = entry.get("loras")
    # Variant = pool YAML entry base name (not replica suffix).
    # Replicas share the same variant so they're grouped in the registry.
    variant = entry.get("_base_variant") or entry.get("name")
    return {
        "name": entry["name"],
        "model": model,
        "variant": variant,
        "models": entry.get("models") or [model],
        "loras": loras,
        "serving_mode": entry.get("serving_mode") or ("multi-lora" if loras else "single"),
        "tier": entry.get("tier", "user"),
        "timeout": _entry_timeout(entry, default_timeout=default_timeout),
        "ttl_sec": _entry_ttl_sec(entry),
        "max_model_len": _resolve_entry_max_model_len(entry, action="pool up"),
        "gpu_count": entry.get("gpu_count"),
        "node_count": entry.get("node_count"),
        "partition": entry.get("partition"),
        "nodelist": entry.get("nodelist"),
        "parallel_strategy": entry.get("parallel_strategy"),
        "env_vars": entry.get("env_vars"),
    }


def _call_provision_endpoint(
    client: Any,
    entry: dict[str, Any],
    spec: dict[str, Any],
    *,
    wait: bool,
) -> dict[str, Any]:
    return client.provision_endpoint(
        model=spec["models"][0],
        models=spec["models"],
        serving_mode=spec["serving_mode"],
        loras=spec["loras"],
        tier=spec["tier"],
        variant=spec.get("variant"),
        mounts=entry.get("mounts"),
        egress_domains=entry.get("egress_domains"),
        timeout=spec["timeout"],
        max_model_len=spec["max_model_len"],
        gpu_count=spec.get("gpu_count"),
        node_count=spec.get("node_count"),
        partition=spec.get("partition"),
        nodelist=spec.get("nodelist"),
        parallel_strategy=spec.get("parallel_strategy"),
        ttl_sec=spec.get("ttl_sec"),
        env_vars=spec.get("env_vars"),
        wait=wait,
    )


def _provision_result_state(status: str) -> str:
    if status == "ready":
        return "ready"
    if status in {"provisioning", "starting", "pending"}:
        return "submitted"
    return status or "submitted"


def _provision_success_result(spec: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    status = str(response.get("status") or "pending").lower()
    return {
        "name": spec["name"],
        "model": spec["model"],
        "tier": spec["tier"],
        "max_model_len": spec["max_model_len"],
        "status": response.get("status", "pending"),
        "state": _provision_result_state(status),
        "instance_id": response.get("instance_id"),
        "endpoint_url": response.get("endpoint_url"),
        "ok": True,
    }


def _reconcile_provision_failure(
    client: Any,
    spec: dict[str, Any],
    submit_started_at: float,
) -> dict[str, Any] | None:
    return _reconcile_submitted_endpoint(
        client,
        model=spec["model"],
        tier=spec["tier"],
        submit_started_at=submit_started_at,
    )


def _reconciled_job_detail(reconciled: dict[str, Any]) -> str:
    job_id = reconciled.get("instance_id") or reconciled.get("job_id") or "?"
    return f"job_id={job_id}"


def _provision_reconciled_result(
    spec: dict[str, Any],
    reconciled: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": spec["name"],
        "model": spec["model"],
        "tier": spec["tier"],
        "max_model_len": spec["max_model_len"],
        "status": reconciled.get("status", "provisioning"),
        "state": "submitted",
        "reconciled": True,
        "instance_id": reconciled.get("instance_id") or reconciled.get("job_id"),
        "endpoint_url": reconciled.get("endpoint_url"),
        "warning": "submission HTTP call failed but UMRS has the job; treating as submitted",
        "ok": True,
    }


def _provision_failed_result(spec: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "name": spec["name"],
        "model": spec["model"],
        "tier": spec["tier"],
        "max_model_len": spec["max_model_len"],
        "state": "failed",
        "ok": False,
        "error": str(exc),
    }


def provision_pool_roster(
    roster: dict[str, Any],
    *,
    roster_path: Path | None = None,
    continue_on_error: bool = False,
    as_json: bool = False,
    dry_run: bool = False,
    default_timeout: int | None = None,
    parallel: bool = True,
) -> int:
    """Provision a resolved roster and optionally persist local pool state.

    By default all Slurm jobs are submitted concurrently via a thread
    pool.  Pass ``parallel=False`` (CLI: ``--sequential``) to fall back
    to one-by-one provisioning where failures abort early.
    """
    from ..client import Client

    entries = _resolve_entries(roster)
    pool_label = roster.get("pool") or "(unnamed)"

    # Detect duplicate (model, name) pairs — these would actually collide
    # since variant = pool YAML entry name. Different entries with the same
    # model but different names are fine (multi-variant deployments).
    pair_counts: dict[tuple[str, str], int] = {}
    for e in entries:
        pair = (e["model"], e.get("name") or e["model"])
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
    dup_pairs = sorted([p for p, c in pair_counts.items() if c > 1])
    if dup_pairs:
        dup_names = ", ".join(f"{m}/{n}" for m, n in dup_pairs)
        if not as_json:
            print(
                f"WARN: Duplicate (model, name) pairs in roster: {dup_names}\n"
                "      Each variant should have a unique name.",
                file=sys.stderr,
            )

    if dry_run:
        _emit_pool_dry_run(entries, pool_label, default_timeout=default_timeout, parallel=parallel)
        return 0

    client = Client()
    _warn_existing_pool_state(roster_path, as_json=as_json)

    results = _provision_roster_entries(
        client,
        entries,
        roster_path=roster_path,
        pool_label=pool_label,
        continue_on_error=continue_on_error,
        as_json=as_json,
        default_timeout=default_timeout,
        parallel=parallel,
    )

    fail_count = sum(1 for r in results if not r.get("ok"))
    if roster_path is not None:
        _save_pool_state(roster_path, pool_label, results)
    _emit_up_summary(results, as_json, pool_label)
    return 1 if fail_count else 0


def _emit_pool_dry_run(
    entries: list[dict[str, Any]],
    pool_label: str,
    *,
    default_timeout: int | None,
    parallel: bool,
) -> None:
    mode_label = "parallel" if parallel else "sequential"
    print(f"Pool: {pool_label}  (mode: {mode_label})")
    for entry in entries:
        timeout = _entry_timeout(entry, default_timeout=default_timeout)
        ttl_sec = _entry_ttl_sec(entry)
        ttl_label = (
            "never" if ttl_sec == 0 else (str(ttl_sec) if ttl_sec is not None else "default")
        )
        max_model_len = _resolve_entry_max_model_len(entry, action="pool up")
        ctx = _format_ctx_literal(max_model_len) if max_model_len is not None else "-"
        source_part = f" source={entry['source']}" if entry.get("source") else ""
        print(
            f"  - {entry['name']}: model={entry['model']} tier={entry['tier']} "
            f"ctx_max={ctx} timeout={timeout} ttl_sec={ttl_label}{source_part}"
        )


def _entry_timeout(entry: dict[str, Any], *, default_timeout: int | None) -> int:
    timeout = entry.get("timeout")
    if timeout is None:
        return default_timeout if default_timeout is not None else 600
    return timeout


def _entry_ttl_sec(entry: dict[str, Any]) -> int | None:
    """Resolve the endpoint TTL (seconds) for a roster entry.

    ``ttl_sec: 0`` requests a non-expiring endpoint. ``None`` (key absent)
    lets the server apply its default TTL. This is distinct from ``timeout``,
    which only bounds the provisioning HTTP request.
    """
    ttl_sec = entry.get("ttl_sec")
    if ttl_sec is None:
        return None
    return int(ttl_sec)


def _warn_existing_pool_state(roster_path: Path | None, *, as_json: bool) -> None:
    if roster_path is None or as_json:
        return
    state_path = _matching_pool_state_path(roster_path)
    if not state_path.exists():
        return
    try:
        prev = json.loads(state_path.read_text())
        prev_entries = prev.get("entries", [])
        prev_ids = [entry.get("instance_id", "?") for entry in prev_entries]
        print(
            f"Note: pool state already exists for this roster "
            f"({len(prev_ids)} tracked endpoint(s): {', '.join(prev_ids)}).\n"
            f"  Provisioning will overwrite the tracked state.\n"
            f"  Run `r4 pool down {roster_path}` first to stop existing endpoints."
        )
    except (json.JSONDecodeError, OSError):
        print(
            "Note: a previous pool state file exists but could not be read; it will be overwritten."
        )


def _provision_roster_entries(
    client: Any,
    entries: list[dict[str, Any]],
    *,
    roster_path: Path | None,
    pool_label: str,
    continue_on_error: bool,
    as_json: bool,
    default_timeout: int | None,
    parallel: bool,
) -> list[dict[str, Any]]:
    if parallel:
        return _provision_parallel(
            client,
            entries,
            default_timeout=default_timeout,
            as_json=as_json,
        )
    return _provision_sequential(
        client,
        entries,
        default_timeout=default_timeout,
        continue_on_error=continue_on_error,
        as_json=as_json,
        roster_path=roster_path,
        pool_label=pool_label,
    )


def _provision_sequential(
    client: Any,
    entries: list[dict[str, Any]],
    *,
    default_timeout: int | None,
    continue_on_error: bool,
    as_json: bool,
    roster_path: Path | None,
    pool_label: str,
) -> list[dict[str, Any]]:
    """Submit endpoint provisions one-by-one (existing behaviour)."""
    results: list[dict[str, Any]] = []
    total = len(entries)
    for idx, entry in enumerate(entries, 1):
        if not as_json:
            print(
                f"-> [{idx}/{total}] Provisioning '{entry['name']}' "
                f"(model={entry['model']}, tier={entry.get('tier', 'user')})..."
            )
        result = _provision_one_entry(
            client,
            entry,
            default_timeout=default_timeout,
            wait=True,
        )
        results.append(result)
        if not result["ok"] and not continue_on_error:
            if roster_path is not None:
                _save_pool_state(roster_path, pool_label, results)
            _emit_up_summary(results, as_json, pool_label)
            return results  # early exit; caller will check fail_count
    return results


def _provision_parallel(
    client: Any,
    entries: list[dict[str, Any]],
    *,
    default_timeout: int | None,
    as_json: bool,
) -> list[dict[str, Any]]:
    """Submit all endpoint provisions concurrently via a thread pool.

    Renders a live multi-line progress board (one row per endpoint)
    when stdout is a TTY.  Falls back to per-completion line printing
    in non-TTY environments and JSON mode.
    """
    total = len(entries)
    interactive = sys.stdout.isatty() and not as_json
    results: list[dict[str, Any]] = [None] * total  # type: ignore[list-item]
    rows, rows_lock = _parallel_rows(entries)

    if not as_json:
        print(f"-> Submitting {total} endpoint(s) in parallel...", flush=True)

    stop_render, renderer = _start_parallel_renderer(
        rows=rows, rows_lock=rows_lock, total=total, interactive=interactive, as_json=as_json
    )
    _submit_parallel_entries(
        client,
        entries,
        results,
        rows,
        rows_lock,
        default_timeout=default_timeout,
        as_json=as_json,
        interactive=interactive,
    )
    _stop_parallel_renderer(stop_render, renderer)
    return results


def _parallel_rows(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Any]:
    import threading
    import time as _time

    rows = [
        {
            "name": entry["name"],
            "model": entry["model"],
            "state": "queued",
            "detail": "",
            "started_at": _time.monotonic(),
        }
        for entry in entries
    ]
    return rows, threading.Lock()


def _submit_parallel_entries(
    client: Any,
    entries: list[dict[str, Any]],
    results: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    rows_lock: Any,
    *,
    default_timeout: int | None,
    as_json: bool,
    interactive: bool,
) -> None:
    done_count = 0
    total = len(entries)
    with ThreadPoolExecutor(max_workers=total) as pool:
        future_to_idx = {
            pool.submit(
                _provision_one_entry,
                client,
                entry,
                default_timeout=default_timeout,
                wait=False,
                progress_cb=_make_progress_cb(idx, rows, rows_lock),
            ): idx
            for idx, entry in enumerate(entries)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            done_count += 1
            _emit_parallel_completion(
                results[idx],
                done_count=done_count,
                total=total,
                as_json=as_json,
                interactive=interactive,
            )


def _make_progress_cb(idx: int, rows: list[dict[str, Any]], rows_lock: Any):
    def _cb(state: str, detail: str) -> None:
        with rows_lock:
            rows[idx]["state"] = state
            rows[idx]["detail"] = detail

    return _cb


def _parallel_state_label(state: str) -> str:
    return {
        "queued": "QUEUED",
        "submitting": "SUBMIT",
        "submitted": "SUBMITTED",
        "ready": "READY",
        "reconciling": "RECONCILING",
        "reconciled": "SUBMITTED*",
        "failed": "FAILED",
        "unknown": "UNKNOWN",
    }.get(state, state.upper())


def _format_parallel_row(idx: int, row: dict[str, Any], *, total: int) -> str:
    import time as _time

    elapsed = int(_time.monotonic() - row["started_at"])
    detail = str(row.get("detail") or "")[:60]
    name = str(row["name"])[:24]
    model = str(row["model"])[:32]
    state_label = _parallel_state_label(row["state"])
    return f"  [{idx + 1}/{total}] {name:<24} {model:<32} {state_label:<11} {elapsed:>4}s  {detail}"


def _parallel_lines(rows: list[dict[str, Any]], rows_lock: Any, total: int) -> list[str]:
    with rows_lock:
        return [_format_parallel_row(index, rows[index], total=total) for index in range(total)]


def _redraw_parallel_lines(lines: list[str], last_render_lines: list[int]) -> None:
    sys.stdout.write(f"\x1b[{last_render_lines[0]}A")
    for line in lines:
        sys.stdout.write("\r\x1b[2K" + line + "\n")
    sys.stdout.flush()
    last_render_lines[0] = len(lines)


def _render_parallel_progress(
    rows: list[dict[str, Any]],
    rows_lock: Any,
    total: int,
    stop_render: Any,
    last_render_lines: list[int],
) -> None:
    sys.stdout.write("\n" * total)
    sys.stdout.flush()
    last_render_lines[0] = total

    while not stop_render.is_set():
        _redraw_parallel_lines(_parallel_lines(rows, rows_lock, total), last_render_lines)
        stop_render.wait(0.5)
    _redraw_parallel_lines(_parallel_lines(rows, rows_lock, total), last_render_lines)


def _start_parallel_renderer(
    *,
    rows: list[dict[str, Any]],
    rows_lock: Any,
    total: int,
    interactive: bool,
    as_json: bool,
):
    import threading

    stop_render = threading.Event()
    last_render_lines = [0]
    if as_json or not interactive:
        return stop_render, None

    def _target() -> None:
        _render_parallel_progress(
            rows,
            rows_lock,
            total,
            stop_render,
            last_render_lines,
        )

    renderer = threading.Thread(target=_target, daemon=True)
    renderer.start()
    return stop_render, renderer


def _stop_parallel_renderer(stop_render: Any, renderer: Any) -> None:
    stop_render.set()
    if renderer is not None:
        renderer.join(timeout=2.0)


def _emit_parallel_completion(
    result: dict[str, Any],
    *,
    done_count: int,
    total: int,
    as_json: bool,
    interactive: bool,
) -> None:
    if as_json or interactive:
        return
    state = str(result.get("state") or ("ok" if result.get("ok") else "failed")).upper()
    if result.get("reconciled"):
        state = "SUBMITTED*"
    print(
        f"   [{done_count}/{total}] [{state}] {result['name']} (model={result['model']})",
        flush=True,
    )


def _parse_selection(raw: str, count: int) -> set[int] | None:
    """Parse a user selection string into a set of 1-based indices.

    Accepts comma-separated indices (``1,3``), ranges (``1-3``),
    the word ``all``, or ``q``/``quit``/``cancel`` to abort.

    Returns:
        * A set of 1-based indices on success.
        * ``None`` if the user chose to quit.
    """
    lowered = raw.strip().lower()
    if lowered in {"q", "quit", "cancel"}:
        return None
    if not lowered or lowered == "all":
        return set(range(1, count + 1))

    selected: set[int] = set()
    for part in lowered.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            bounds = token.split("-", 1)
            try:
                start_i, end_i = int(bounds[0]), int(bounds[1])
            except ValueError:
                return set()
            if start_i < 1 or end_i < 1 or start_i > end_i:
                return set()
            selected.update(range(start_i, end_i + 1))
            continue
        try:
            selected.add(int(token))
        except ValueError:
            return set()

    out_of_range = [i for i in selected if i < 1 or i > count]
    if out_of_range:
        return set()
    return selected


def _prompt_pool_selection(roster: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Interactively ask which roster entries to provision.

    Returns the selected raw endpoint dictionaries, or ``None`` if the
    user cancels.
    """
    raw_entries = roster.get("endpoints") or []
    if not isinstance(raw_entries, list):
        raw_entries = []
    count = len(raw_entries)

    pool_label = roster.get("pool") or "(unnamed)"
    print(f"\nPool '{pool_label}' has {count} endpoint(s):")
    for idx, entry in enumerate(raw_entries, 1):
        name = entry.get("name") or entry.get("model", "?")
        model = entry.get("model", "?")
        tier = entry.get("tier", "user")
        print(f"  [{idx}] {name:<24} model={model:<28} tier={tier}")

    print()
    print("Select endpoints to provision:")
    print("  - Comma-separated indices (e.g. 1,3)")
    print("  - Range (e.g. 1-3)")
    print("  - 'all' to provision every endpoint")
    print("  - 'q' to cancel")

    while True:
        try:
            raw = input("Selection [all]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        selected = _parse_selection(raw, count)
        if selected is None:
            return None
        if not selected:
            print("  Invalid input. Enter indices (e.g. 1,3), a range (e.g. 1-3), 'all', or 'q'.")
            continue

        return [raw_entries[i - 1] for i in sorted(selected)]


def _cmd_pool_up(args: Namespace) -> int:
    roster_path = Path(args.file)
    roster = _load_roster(roster_path)

    as_json = getattr(args, "as_json", False)
    dry_run = getattr(args, "dry_run", False)
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not as_json

    if interactive and len(roster.get("endpoints") or []) > 1:
        selected = _prompt_pool_selection(roster)
        if selected is None:
            print("Cancelled.")
            return 0
        roster = dict(roster)
        roster["endpoints"] = selected

    return provision_pool_roster(
        roster,
        roster_path=roster_path,
        continue_on_error=getattr(args, "continue_on_error", False),
        as_json=as_json,
        dry_run=dry_run,
        default_timeout=getattr(args, "default_timeout", None),
        parallel=not getattr(args, "sequential", False),
    )


def _cmd_pool_down(args: Namespace) -> int:
    roster_path = Path(args.file)
    state = _load_pool_state(roster_path)
    entries = list(state.get("entries", []))
    pool_label = state.get("pool") or roster_path.stem
    state_path = _matching_pool_state_path(roster_path)

    _warn_untracked_pool_entries(roster_path, entries, as_json=getattr(args, "as_json", False))

    results: list[dict[str, Any]] = []
    remaining_entries: list[dict[str, Any]] = []
    fail_count = 0

    for index, entry in enumerate(entries):
        result = _stop_pool_entry(
            entry,
            force=getattr(args, "force", False),
            as_json=getattr(args, "as_json", False),
        )
        results.append(result)
        if result.get("ok"):
            continue
        fail_count += 1
        remaining_entries.append(entry)
        if not getattr(args, "continue_on_error", False):
            remaining_entries.extend(entries[index + 1 :])
            break

    if remaining_entries:
        state["entries"] = remaining_entries
        _write_pool_state(roster_path, state)
    elif state_path.exists():
        state_path.unlink()

    _emit_down_summary(results, getattr(args, "as_json", False), pool_label)
    return 1 if fail_count else 0


def _warn_untracked_pool_entries(
    roster_path: Path,
    entries: list[dict[str, Any]],
    *,
    as_json: bool,
) -> None:
    try:
        roster = _load_roster(roster_path)
        roster_entries = _resolve_entries(roster)
    except (SystemExit, Exception):
        return
    if len(roster_entries) <= len(entries) or as_json:
        return
    diff = len(roster_entries) - len(entries)
    print(
        f"Note: roster defines {len(roster_entries)} endpoint(s) but only "
        f"{len(entries)} were tracked ({diff} may have failed to provision).\n"
        f"  Use `r4 endpoint list --include-stopped` or `r4 down --all`\n"
        f"  to find and stop any orphaned endpoints."
    )


def _pool_entry_identity(entry: dict[str, Any]) -> tuple[Any, str, str, str]:
    return (
        entry.get("instance_id"),
        entry.get("name") or entry.get("model") or "?",
        entry.get("model") or "?",
        entry.get("tier") or "user",
    )


def _stop_pool_entry(
    entry: dict[str, Any],
    *,
    force: bool,
    as_json: bool,
) -> dict[str, Any]:
    """Stop a single pool entry using its own source gateway."""
    from ..client import Client

    instance_id, name, model, tier = _pool_entry_identity(entry)
    if not as_json:
        print(f"-> Stopping '{name}' ({instance_id})...")
    try:
        source_name = entry.get("source")
        client = _get_source_client(source_name)
        if client is None:
            client = Client()
        response = client.stop_endpoint(instance_id, force=force)
        return _pool_down_result(name, model, tier, instance_id, response.get("status", "stopped"))
    except Exception as exc:  # noqa: BLE001 - report and continue/abort
        if _is_http_404(exc):
            return _pool_down_result(name, model, tier, instance_id, "not-found")
        return _pool_down_result(name, model, tier, instance_id, "error", error=str(exc))


def _get_source_client(source_name: str | None) -> Client | None:
    """Return a Client targeting the named source's gateway, or None."""
    from ..client import Client
    from ..config import get_config
    from ..source import get_source_store
    from ..source_context import config_for_source

    if not source_name:
        return None

    source = get_source_store().get(source_name)
    if source is None:
        return None
    config = get_config()
    if source.kind == "platform":
        config = config_for_source(source, config)  # its own credential only (S1)
    return Client(config=config)


def _pool_down_result(
    name: str,
    model: str,
    tier: str,
    instance_id: Any,
    status: str | None,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    result = {
        "name": name,
        "model": model,
        "tier": tier,
        "instance_id": instance_id,
        "ok": error is None,
    }
    if status is not None:
        result["status"] = status
    if error is not None:
        result["error"] = error
    return result


def _emit_up_summary(
    results: list[dict[str, Any]],
    as_json: bool,
    pool_label: str,
) -> None:
    if as_json:
        print(json.dumps({"pool": pool_label, "results": results}, indent=2))
        return

    has_sources = any(r.get("source") for r in results)
    print()
    print(f"Pool '{pool_label}' provisioning summary:")
    _print_up_summary_header(has_sources)
    has_reconciled = False
    for r in results:
        status, extra, reconciled = _up_summary_status(r)
        has_reconciled = has_reconciled or reconciled
        _print_up_summary_row(r, status, extra, has_sources)
    if has_reconciled:
        print(
            "\n  * = HTTP submission timed out but UMRS already accepted the job.\n"
            "      Use `r4 endpoint list` to confirm readiness."
        )
    print(
        "\n  Hint: jobs are submitted; container/model startup continues "
        "in the background.\n"
        "        Use `r4 endpoint list` to watch readiness."
    )


def _print_up_summary_header(has_sources: bool) -> None:
    if has_sources:
        print(f"  {'NAME':<24} {'SOURCE':<14} {'MODEL':<28} {'TIER':<10} STATUS")
    else:
        print(f"  {'NAME':<24} {'MODEL':<32} {'TIER':<10} STATUS")


def _up_summary_status(result: dict[str, Any]) -> tuple[str, str, bool]:
    if not result.get("ok"):
        return "FAILED", f"  {result.get('error', '?')[:40]}", False

    status = result.get("status", "pending")
    extra = f"  ({result.get('instance_id', '?')})"
    if result.get("reconciled"):
        return f"{status}*", extra, True
    return status, extra, False


def _print_up_summary_row(
    result: dict[str, Any],
    status: str,
    extra: str,
    has_sources: bool,
) -> None:
    if has_sources:
        source = str(result.get("source", ""))[:12]
        print(
            f"  {result['name'][:24]:<24} {source:<14} {result['model'][:28]:<28} "
            f"{result['tier']:<10} {status}{extra}"
        )
        return

    print(
        f"  {result['name'][:24]:<24} {result['model'][:32]:<32} "
        f"{result['tier']:<10} {status}{extra}"
    )


def _emit_down_summary(
    results: list[dict[str, Any]],
    as_json: bool,
    pool_label: str,
) -> None:
    if as_json:
        print(json.dumps({"pool": pool_label, "results": results}, indent=2))
        return

    print()
    print(f"Pool '{pool_label}' stop summary:")
    print(f"  {'NAME':<24} {'INSTANCE':<20} STATUS")
    for result in results:
        if result.get("ok"):
            status = result.get("status", "stopped")
        else:
            status = f"FAILED  {result.get('error', '?')[:40]}"
        print(
            f"  {result['name'][:24]:<24} {str(result.get('instance_id', '?'))[:20]:<20} {status}"
        )
