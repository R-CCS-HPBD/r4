#!/usr/bin/env python3
"""UMRS HTTP route inventory + auth-annotation auditor (X2).

Sources the mounted router set from ``umrs.server.http_sidecar._create_app``
and generates a machine-readable inventory: ``path methods router-module
auth-note``. The auth note is inferred from (in order):

1. ``Depends(require_auth)`` / explicit auth parameters in the route handler,
2. function-level docstring marker ``# auth: open`` or ``# auth: verified``.
3. module-level convention (health/readiness/metrics prefix whitelist).

``route_inventory.py generate`` writes an inventory JSON (checked into the
repo as the baseline). ``route_inventory.py check`` compares the live
surface against the baseline and fails the build on *any new unauthed
non-plain-text route* (health/metrics/readiness除外) that isn't covered by
an existing baseline row — that prevents quiet exposure as the surface
grows while keeping first-wave migration advisory-only.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any

_UNAUTH_WHITELIST_PREFIXES: tuple[str, ...] = (
    "/health",
    "/metrics",
    "/readiness",
)

# Exact open paths. H-1 (2026-10-18): this was a "/umrs/web" prefix, which
# marked every dashboard route open although each resolves the gateway-
# verified caller; only the static shell and the two probes are open.
_UNAUTH_WHITELIST_EXACT: frozenset[str] = frozenset(
    {
        "/umrs/web",  # static shell mount (the API it calls needs identity)
        "/umrs/web/dashboard/health",
        "/umrs/web/dashboard/metrics",
    }
)

# FastAPI dependencies that resolve the HMAC-verified gateway caller (401 in
# strict mode without it): a route that depends on one is "verified". Matched
# by qualified name, so a same-named local resolver does not count.
_VERIFIED_DEPENDENCIES: frozenset[str] = frozenset(
    {
        "umrs.api._scope_identity.get_caller_scope",
        "umrs.api._verified_headers.get_caller",
        "umrs.api._verified_headers.get_verified_caller",
        # Strict mode honours only an HMAC-verified project claim (2026-10-27:
        # without it a `{file_id}` -> `{file_id:path}` rename lost its
        # hand-set note and the sweep filed two authed routes, #592).
        "umrs.api.files.get_project_id",
    }
)


def _baseline_path() -> Path:
    return Path(__file__).resolve().parent / "route_inventory.base.json"


def _norm_method(methods: set[str] | list[str]) -> list[str]:
    return sorted(m for m in methods if m not in {"OPTIONS", "HEAD"}) or ["GET"]


def _auth_note(func: Any, path: str) -> str:
    """Infer auth note from handler annotations/docstring + whitelist prefix."""
    if path in _UNAUTH_WHITELIST_EXACT or path.startswith(_UNAUTH_WHITELIST_PREFIXES):
        return "open(white)"
    auth = _auth_from_docstring(func)
    if auth is not None:
        return auth
    return _auth_from_signature(func)


def _auth_from_docstring(func: Any) -> str | None:
    """Auth annotation marker if the docstring carries one, else None."""
    doc = (getattr(func, "__doc__", "") or "").lower()
    segments = doc.split("# auth:")
    if len(segments) < 2:
        return None
    return segments[1].splitlines()[0].strip() or None


def _is_verified_dependency(dep: Any) -> bool:
    """Is ``dep`` a ``Depends(...)`` on a verified-caller resolver?"""
    target = getattr(dep, "dependency", None)
    module = getattr(target, "__module__", "")
    qualname = getattr(target, "__qualname__", "")
    return f"{module}.{qualname}" in _VERIFIED_DEPENDENCIES


def _route_auth(route: Any, handler: Any, path: str) -> str:
    """Handler note, or "verified" when the route itself declares a
    verified-caller dependency (``dependencies=[Depends(...)]``)."""
    auth = _auth_note(handler, path)
    if auth != "unknown":
        return auth
    route_deps = getattr(route, "dependencies", None) or []
    return "verified" if any(map(_is_verified_dependency, route_deps)) else auth


def _auth_from_signature(func: Any) -> str:
    """Auth signal from handler's parameters, if inspectable."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return "unknown"
    if any(_is_verified_dependency(p.default) for p in sig.parameters.values()):
        return "verified"
    for param in sig.parameters.values():
        name = param.name.lower()
        if "authorization" in name or "token" in name or name == "user":
            return "suspect-param"
    return "unknown"


def build_inventory() -> list[dict[str, Any]]:
    """Construct the full mounted-route inventory."""
    import os

    src = Path(__file__).resolve().parents[1] / "umrs" / "src"
    if "PYTHONPATH" not in os.environ:
        sys.path.insert(0, str(src))

    from umrs.server.http_sidecar import _create_app

    app = _create_app()
    rows: list[dict[str, Any]] = []
    for mount in app.routes:
        mount_type = type(mount).__name__
        if mount_type != "_IncludedRouter":
            # Static/health mounts are captured separately at the end.
            continue
        router = mount.original_router
        for route in router.routes:
            path = getattr(route, "path", "")
            handler = getattr(route, "endpoint", None) or getattr(route, "handler", None)
            methods = _norm_method(getattr(route, "methods", set()) or set())
            rows.append(
                {
                    "path": path,
                    "methods": methods,
                    "router": router.__class__.__module__,
                    "auth": _route_auth(route, handler, path),
                }
            )
    # Plain mounts (health/metrics/readiness + static web UI).
    for explicit in (
        ("/health", ["GET"]),
        ("/metrics", ["GET"]),
        ("/readiness", ["GET"]),
        ("/umrs/web", ["GET"]),
    ):
        rows.append(
            {
                "path": explicit[0],
                "methods": explicit[1],
                "router": "umrs.server.http_sidecar",
                "auth": "open(white)",
            }
        )
    rows.sort(key=lambda r: (r["path"], r["methods"]))
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["auth"]] = counts.get(row["auth"], 0) + 1
    return counts


def _merged_rows(rows: list[dict[str, Any]], prior: dict | None) -> list[dict[str, Any]]:
    """Carry prior auth notes onto unchanged route keys (annotation preserve).

    A bare ``generate`` must not wipe hand-set auth notes (this already
    happened once — the 2026-08 annotation pass was lost on regen). Notes
    follow the (path, methods) key; brand-new routes keep the inferred note.
    """
    if not prior:
        return rows
    notes = {
        (row["path"], tuple(row["methods"])): row["auth"]
        for row in prior.get("routes", [])
        if row.get("auth")
    }
    for row in rows:
        key = (row["path"], tuple(row["methods"]))
        if key in notes:
            row["auth"] = notes[key]
    return rows


def cmd_generate(out_path: Path, fresh: bool = False) -> int:
    rows = build_inventory()
    if not fresh and out_path.exists():
        try:
            rows = _merged_rows(rows, json.loads(out_path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN unreadable prior baseline ({exc}); regenerating fresh")
    summary = _summary(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump({"routes": rows, "summary": summary}, f, indent=2)
        f.write("\n")
    print(f"Generated {out_path}: {len(rows)} routes ({summary})")
    return 0


def cmd_annotate(baseline_path: Path, from_note: str, note: str) -> int:
    """Bulk-rewrite baseline rows whose auth note matches ``from_note``."""
    if note == "unknown":
        print("refusing to annotate TO 'unknown' — that would un-audit rows")
        return 1
    data = json.loads(baseline_path.read_text())
    changed = 0
    for row in data.get("routes", []):
        if row.get("auth") == from_note:
            row["auth"] = note
            changed += 1
    data["summary"] = _summary(data["routes"])
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with baseline_path.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Annotated {changed} route(s) {from_note!r} -> {note!r} ({data['summary']})")
    return 0


def _newly_unauthed(rows: list[dict[str, Any]], baseline_keys: set) -> list[dict[str, Any]]:
    """Routes not in baseline and without an auth note — audit failures."""
    failing = [
        row
        for row in rows
        if (row["path"], tuple(row["methods"])) not in baseline_keys and row["auth"] == "unknown"
    ]
    for row in failing:
        print(f"FAIL newly-added unauthed route: {row['path']} {row['methods']}")
    return failing


def _missing_since_baseline(rows: list[dict[str, Any]], baseline_keys: set) -> set:
    current = {(row["path"], tuple(row["methods"])) for row in rows}
    return {key for key in baseline_keys if key not in current}


def cmd_check(baseline_path: Path) -> int:
    baseline = json.loads(baseline_path.read_text())
    baseline_keys = {(row["path"], tuple(row["methods"])) for row in baseline["routes"]}
    rows = build_inventory()
    failing = _newly_unauthed(rows, baseline_keys)
    missing = _missing_since_baseline(rows, baseline_keys)
    if missing:
        print(f"WARN {len(missing)} baseline routes gone since snapshot; regen if deliberate.")
    if failing:
        print(
            f"\n{len(failing)} route(s) bypass the audit baseline. "
            f"Annotate the handler with ``# auth: ...``, register in "
            f"route_inventory.base.json (git add), or delete the route."
        )
        return 1
    print(f"OK: {len(rows)} routes; no new unauthed surface ({_summary(rows)})")
    return 0


USAGE = "route_inventory.py <generate [--fresh]|check|annotate FROM_NOTE NOTE> [baseline_path]"


def _parse_target_and_opts(argv: list[str]) -> tuple[Path, bool]:
    """Extract baseline path + --fresh flag from argv tail."""
    fresh = "--fresh" in argv
    positional = [a for a in argv if not a.startswith("--")]
    return (Path(positional[0]).resolve() if positional else _baseline_path(), fresh)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"generate", "check", "annotate"}:
        print(USAGE, file=sys.stderr)
        return 2
    if sys.argv[1] == "annotate":
        if len(sys.argv) < 4:
            print(USAGE, file=sys.stderr)
            return 2
        out = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 else _baseline_path()
        return cmd_annotate(out, sys.argv[2], sys.argv[3])
    out, fresh = _parse_target_and_opts(sys.argv[2:])
    if sys.argv[1] == "generate":
        return cmd_generate(out, fresh=fresh)
    return cmd_check(out)


if __name__ == "__main__":
    raise SystemExit(main())
