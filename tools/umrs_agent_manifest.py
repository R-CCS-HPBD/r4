#!/usr/bin/env python3
"""UMRS agent capability manifest exporter (ADR-105 D-1, slice S1).

Builds the UMRS HTTP app offline (``_create_app().openapi()``, no database,
no network) and, from the hand-curated allowlist
``contracts/umrs-http/v1/agent-tools.allowlist.json``, writes:

- ``agent-tools.json`` — one row per agent-exposed operation: the native
  tool name, method and path, risk / taint / scope / site, and the request
  and response schema names;
- ``agent-openapi.json`` — the OpenAPI subset for those operations (their
  paths plus every component schema they reference), with the
  gateway-injected identity headers (``x-user-*``, ``x-verified-*``,
  ``*-hmac``), the legacy MVP identity query parameters (``caller_*``,
  ``verified_*``, ``is_admin``, and owner parameters a route honours only
  in MVP mode) and the client-set ``x-r4-session-id`` header removed: an
  agent must never see them as settable inputs (S5, 2026-11-05: S1 stripped
  only the headers).

Both files are mirrored byte-exact into ``mcp-servers/src/r4_mcp/contracts/``
(ADR-105 D-2, S5): the MCP platform server generates its tools from them and
its image is built without ``contracts/``. ``--check`` covers the mirror.

Only the agent surface is committed; the full specification is served by
the sidecar at ``/openapi.json``. An operation is refused unless it carries
an explicit ``operation_id`` and a typed request (when it has a body) and a
typed success response ("type before expose").

The OpenAPI text depends on the fastapi / pydantic versions that render it,
so ``agent-openapi.json`` records them (``info.x-generated-with``). With the
same versions the check is byte-exact; with others it compares the shape
(the manifest exactly, plus the subset's paths, methods and schema names), so
an unrelated library upgrade does not turn the drift test red.

Modes (mirrors ``tools/rpc_schema_export.py``):
  --write    regenerate the artifacts in place
  --check    exit 1 when the artifacts drift from UMRS (default; CI)

Needs ``umrs/src`` importable (the repo-root test lane sets PYTHONPATH).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "contracts" / "umrs-http" / "v1"
ALLOWLIST = OUT_DIR / "agent-tools.allowlist.json"
MANIFEST_NAME = "agent-tools.json"
OPENAPI_NAME = "agent-openapi.json"
# ADR-105 D-2 (S5): the MCP platform server generates its tools from these two
# files at runtime. Its image is built from mcp-servers/ only (contracts/ is
# outside the Docker context), so the package carries a byte-exact copy.
MIRROR_DIR = REPO_ROOT / "mcp-servers" / "src" / "r4_mcp" / "contracts"

_IDENTITY_HEADER_RE = re.compile(r"^x-(user|verified)-|-hmac$", re.IGNORECASE)
# The client sets the session header (ADR-105 S2), never the model.
_CLIENT_HEADERS = frozenset({"x-r4-session-id"})
# Legacy MVP identity query parameters (``get_caller_identity``): strict mode
# reads only the gateway-verified headers, so an agent must never set them.
_IDENTITY_QUERY_RE = re.compile(r"^(caller_|verified_)|^is_admin$", re.IGNORECASE)
# Per-operation query parameters a route honours only in MVP mode. Strict mode
# always has ``X-User-ID`` and ``list_workflows`` resolves the owner as
# ``x_user_id or user_id``, so ``user_id`` never filters there. Kept on
# purpose: ``job_list.project_id`` (an admin's project filter in strict mode),
# ``rag_list_documents.project_id`` (names the corpus, checked against the
# ADR-080 access list) and ``job_events.session_id`` (a real filter).
_MVP_ONLY_QUERY = frozenset({("workflow_list", "user_id")})
_DB_ENV_RE = re.compile(r"^(PG[A-Z]*|DATABASE_URL|POSTGRES_[A-Z_]+|UMRS_DB_[A-Z_]+)$")
_SUCCESS_CODES = ("200", "201", "202")
_METHODS = ("get", "post", "put", "patch", "delete")
_REF_PREFIX = "#/components/schemas/"


@contextmanager
def _without_db_env() -> Iterator[None]:
    saved = {k: os.environ.pop(k) for k in list(os.environ) if _DB_ENV_RE.match(k)}
    try:
        yield
    finally:
        os.environ.update(saved)


def build_openapi() -> dict[str, Any]:
    """The full UMRS OpenAPI document, built offline."""
    src = str(REPO_ROOT / "umrs" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from umrs.server import http_sidecar

    original = http_sidecar._build_db_pool
    http_sidecar._build_db_pool = lambda *_a, **_k: None
    try:
        with _without_db_env():
            return http_sidecar._create_app().openapi()
    finally:
        http_sidecar._build_db_pool = original


def _operations(spec: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    found: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for path, item in spec.get("paths", {}).items():
        for method in _METHODS:
            op = item.get(method)
            if isinstance(op, dict) and op.get("operationId"):
                found[op["operationId"]] = (method, path, op)
    return found


def _schema_name(schema: Any) -> str | None:
    """Component name of a typed schema, or None when untyped."""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref") or (schema.get("items") or {}).get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref[len(_REF_PREFIX) :]
        return f"{name}[]" if "items" in schema else name
    return "inline" if schema.get("properties") else None


def _request_schema(op: dict[str, Any]) -> tuple[bool, str | None]:
    """(has_body, schema name) for an operation's request body."""
    body = op.get("requestBody")
    if not body:
        return False, None
    for media in body.get("content", {}).values():
        return True, _schema_name(media.get("schema"))
    return True, None


def _response_schema(op: dict[str, Any]) -> str | None:
    for code in _SUCCESS_CODES:
        content = op.get("responses", {}).get(code, {}).get("content", {})
        for media in content.values():
            return _schema_name(media.get("schema"))
    return None


def _is_identity_param(op_id: str, param: dict[str, Any]) -> bool:
    """An input the gateway or the client sets, never the agent."""
    name, where = param.get("name", ""), param.get("in")
    if where == "header":
        return bool(_IDENTITY_HEADER_RE.search(name)) or name.lower() in _CLIENT_HEADERS
    if where == "query":
        return bool(_IDENTITY_QUERY_RE.search(name)) or (op_id, name) in _MVP_ONLY_QUERY
    return False


def _strip_identity(op: dict[str, Any]) -> dict[str, Any]:
    op_id = op.get("operationId", "")
    params = [p for p in op.get("parameters", []) if not _is_identity_param(op_id, p)]
    return {**op, "parameters": params}


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        is_local = isinstance(ref, str) and ref.startswith(_REF_PREFIX)
        own = {ref[len(_REF_PREFIX) :]} if is_local else set()
        return own.union(*(_refs(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(v) for v in node))
    return set()


def _closure(schemas: dict[str, Any], roots: set[str]) -> dict[str, Any]:
    seen: set[str] = set()
    todo = sorted(roots)
    while todo:
        name = todo.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        todo.extend(sorted(_refs(schemas[name]) - seen))
    return {name: schemas[name] for name in sorted(seen)}


def _row_problems(entry: dict[str, Any], op: dict[str, Any] | None) -> list[str]:
    oid = entry["operation_id"]
    if op is None:
        return [f"{oid}: no UMRS route declares this explicit operation_id"]
    problems = []
    has_body, request = _request_schema(op)
    if has_body and request is None:
        problems.append(f"{oid}: untyped request body (type before expose)")
    if _response_schema(op) is None:
        problems.append(f"{oid}: untyped success response (type before expose)")
    return problems


def build(spec: dict[str, Any], allowlist: dict[str, Any]) -> tuple[dict, dict, list[str]]:
    """(manifest, openapi subset, problems) for ``allowlist`` against ``spec``."""
    ops = _operations(spec)
    rows, paths, problems = [], {}, []
    for entry in sorted(allowlist["operations"], key=lambda e: e["operation_id"]):
        method, path, op = ops.get(entry["operation_id"], (None, None, None))
        problems += _row_problems(entry, op)
        if op is None:
            continue
        clean = _strip_identity(op)
        paths.setdefault(path, {})[method] = clean
        rows.append(
            {
                **entry,
                "method": method.upper(),
                "path": path,
                "request_schema": _request_schema(op)[1],
                "response_schema": _response_schema(op),
                "summary": op.get("summary", ""),
            }
        )
    schemas = spec.get("components", {}).get("schemas", {})
    subset = {
        "openapi": spec.get("openapi"),
        "info": {
            **spec.get("info", {}),
            "description": "UMRS agent surface (ADR-105 D-1); generated",
            "x-generated-with": generator_versions(),
        },
        "paths": paths,
        "components": {"schemas": _closure(schemas, _refs(paths))},
    }
    manifest = {"version": allowlist.get("version", 1), "operations": rows}
    return manifest, subset, problems


def generator_versions() -> dict[str, str]:
    """The libraries whose versions shape the OpenAPI text."""
    return {name: metadata.version(name) for name in ("fastapi", "pydantic")}


def _shape(openapi_text: str) -> dict[str, Any]:
    doc = json.loads(openapi_text)
    paths = {path: sorted(item) for path, item in doc.get("paths", {}).items()}
    return {"paths": paths, "schemas": sorted(doc.get("components", {}).get("schemas", {}))}


def _same(name: str, committed: str, fresh: str) -> bool:
    if committed == fresh or name != OPENAPI_NAME:
        return committed == fresh
    recorded = json.loads(committed).get("info", {}).get("x-generated-with")
    if recorded == generator_versions():
        return False
    return _shape(committed) == _shape(fresh)


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def render(spec: dict[str, Any] | None = None) -> tuple[dict[str, str], list[str]]:
    """({filename: content}, problems)."""
    allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    manifest, subset, problems = build(spec or build_openapi(), allowlist)
    return {MANIFEST_NAME: _dump(manifest), OPENAPI_NAME: _dump(subset)}, problems


def drifted(out_dir: Path = OUT_DIR, spec: dict[str, Any] | None = None) -> list[str]:
    """Problems plus artifact names that are missing or stale."""
    artifacts, problems = render(spec)
    stale = [
        name
        for name, content in artifacts.items()
        if not (out_dir / name).exists()
        or not _same(name, (out_dir / name).read_text(encoding="utf-8"), content)
    ]
    return problems + stale + mirror_drift(out_dir)


def mirror_drift(out_dir: Path = OUT_DIR, mirror_dir: Path = MIRROR_DIR) -> list[str]:
    """Package copies (``r4_mcp/contracts``) that differ from the contract."""
    stale = []
    for name in (MANIFEST_NAME, OPENAPI_NAME):
        source, copy = out_dir / name, mirror_dir / name
        if not copy.exists() or copy.read_bytes() != source.read_bytes():
            stale.append(f"{copy} (stale mirror of {name})")
    return stale


def _write(artifacts: dict[str, str]) -> None:
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")
        (MIRROR_DIR / name).write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.write:
        drift = drifted()
        for item in drift:
            print(f"drift: {item}", file=sys.stderr)
        if drift:
            print("run: python tools/umrs_agent_manifest.py --write", file=sys.stderr)
        return 1 if drift else 0
    artifacts, problems = render()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    _write(artifacts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
