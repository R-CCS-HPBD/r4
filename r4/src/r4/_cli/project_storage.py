"""``r4 project storage`` — bind a project's stores outside the front-end (ADR-077).

A project can keep its objects (S3) and its Postgres next to an HPC site's
large storage instead of on the UMRS front-end. The S3 URL and the Postgres
URI carry credentials: r4 reads them from flags, files or an env file at
invocation, sends them once to UMRS (which seals them at rest) and never
writes them anywhere. Every response is redacted by the server.

    r4 project storage show  [project]
    r4 project storage set   [project] --env-file FILE|-      (or the flags below)
    r4 project storage check [project]
    r4 project storage clear [project]

The env file uses ``UMRS_PROJECT_S3_URL=`` / ``UMRS_PROJECT_POSTGRES_URI=``
lines (the format ``umrs-extdb.sh project <name>`` writes); ``-`` reads stdin.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

ENV_S3 = "UMRS_PROJECT_S3_URL"
ENV_PG = "UMRS_PROJECT_POSTGRES_URI"
ACTIONS = ("show", "set", "check", "clear")


def add_storage_parser(sub: Any) -> None:
    """Register ``r4 project storage`` on the ``project`` subparsers."""
    parser = sub.add_parser(
        "storage",
        help="Bind the project's object store / Postgres outside the front-end (ADR-077)",
        description=__doc__.split("\n\n")[1] if __doc__ else None,
    )
    parser.add_argument("storage_action", choices=ACTIONS, help="show | set | check | clear")
    parser.add_argument(
        "project_id", nargs="?", default=None, help="Project ID (defaults to current project)"
    )
    parser.add_argument(
        "--env-file",
        metavar="FILE",
        help=f"KEY=VALUE file with {ENV_S3} / {ENV_PG} ('-' reads stdin)",
    )
    parser.add_argument("--s3-url-file", metavar="FILE", help="file holding the S3 URL")
    parser.add_argument("--postgres-uri-file", metavar="FILE", help="file holding the URI")
    parser.add_argument(
        "--s3-url",
        metavar="URL",
        help="http(s)://ACCESS:SECRET@host:port[/bucket] (prefer --env-file: argv is visible)",
    )
    parser.add_argument(
        "--postgres-uri",
        metavar="URI",
        help="postgresql://user:pw@host:port/db (prefer --env-file: argv is visible)",
    )
    parser.add_argument("--unset-s3", action="store_true", help="unbind the object store")
    parser.add_argument("--unset-postgres", action="store_true", help="unbind Postgres")
    parser.add_argument(
        "--no-check", action="store_true", help="save without probing from the server first"
    )
    parser.add_argument("--json", "-j", action="store_true", dest="json", help="Output as JSON")


# -- locator input -------------------------------------------------------------


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).expanduser().read_text(encoding="utf-8")


def _env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key in (ENV_S3, ENV_PG):
            values[key] = value.strip().strip("'\"")
    return values


def _first_line(path: str | None) -> str | None:
    if not path:
        return None
    lines = [ln.strip() for ln in _read_text(path).splitlines() if ln.strip()]
    return lines[0] if lines else ""


def _pick(flag: str | None, file_value: str | None, env_value: str | None, unset: bool):
    """Flag > dedicated file > env file; ``--unset-*`` sends "" (unbind)."""
    if unset:
        return ""
    for value in (flag, file_value, env_value):
        if value is not None:
            return value
    return None


def collect_locators(args: Namespace) -> tuple[str | None, str | None]:
    """``(s3_url, postgres_uri)`` for ``set``; None keeps the server's value."""
    env = _env_values(_read_text(args.env_file)) if getattr(args, "env_file", None) else {}
    s3 = _pick(
        getattr(args, "s3_url", None),
        _first_line(getattr(args, "s3_url_file", None)),
        env.get(ENV_S3),
        bool(getattr(args, "unset_s3", False)),
    )
    pg = _pick(
        getattr(args, "postgres_uri", None),
        _first_line(getattr(args, "postgres_uri_file", None)),
        env.get(ENV_PG),
        bool(getattr(args, "unset_postgres", False)),
    )
    return s3, pg


# -- rendering --------------------------------------------------------------------


def _s3_line(s3: dict[str, Any]) -> str:
    bucket = s3.get("bucket") or "(server default name)"
    tls = "" if s3.get("verify", True) else " tls-verify=off"
    return (
        f"  s3:       {s3.get('endpoint_url')}  bucket={bucket}"
        f" region={s3.get('region')} key={s3.get('access_key_hint')}{tls}"
    )


def _pg_line(pg: dict[str, Any]) -> str:
    sslmode = f" sslmode={pg['sslmode']}" if pg.get("sslmode") else ""
    where = f"{pg.get('host')}:{pg.get('port')}/{pg.get('dbname')}"
    return f"  postgres: {pg.get('user')}@{where}{sslmode}"


def render_binding(project_id: str, storage: dict[str, Any]) -> list[str]:
    """Human lines for a redacted binding."""
    if not storage.get("bound"):
        return [f"{project_id}: no external storage — the server's own stores are used"]
    lines = [
        f"{project_id}: external storage bound"
        f" (updated {storage.get('updated_at') or '?'} by {storage.get('updated_by') or '?'})"
    ]
    if storage.get("s3"):
        lines.append(_s3_line(storage["s3"]))
    if storage.get("postgres"):
        lines.append(_pg_line(storage["postgres"]))
    return lines


def _probe_s3_line(report: dict[str, Any]) -> str:
    created = report.get("buckets_created") or []
    made = f", created {', '.join(created)}" if created else ""
    return f"  s3:       ok (bucket {report.get('bucket')}{made}; {report.get('roundtrip_ms')} ms)"


def _probe_pg_line(report: dict[str, Any]) -> str:
    tls = "tls" if report.get("tls") else "no tls"
    vector = report.get("pgvector")
    vec = f"pgvector {vector}" if vector else "pgvector MISSING"
    if report.get("vector_roundtrip"):
        vec += " (round-trip ok)"
    return (
        f"  postgres: ok (PostgreSQL {report.get('server_version')}, {tls}, {vec};"
        f" {report.get('latency_ms')} ms)"
    )


def render_probe(probe: dict[str, Any] | None) -> list[str]:
    """Human lines for a probe report (per store)."""
    if not probe:
        return []
    lines = ["probe: " + ("ok" if probe.get("ok") else "FAILED")]
    for name, ok_line in (("s3", _probe_s3_line), ("postgres", _probe_pg_line)):
        report = probe.get(name)
        if report is None:
            continue
        if report.get("ok"):
            lines.append(ok_line(report))
        else:
            lines.append(f"  {name}: FAILED {report.get('error')}: {report.get('detail')}")
    return lines


# -- handler ------------------------------------------------------------------------


def _project_id(args: Namespace, pm: Any) -> str:
    from ..source_context import selected_project

    # Under --source on another deployment, only that source's own project
    # (onboarding plan S1): never the active deployment's switched project.
    project_id = getattr(args, "project_id", None) or selected_project(config_fallback=False)
    if not project_id:
        from ._helpers import _die

        _die(
            "No project specified. Pass <project-id> or run: r4 project switch <id>",
            error_id="project.storage.no_project",
        )
    return str(project_id)


def _dispatch(client: Any, project_id: str, args: Namespace) -> dict[str, Any]:
    action = args.storage_action
    if action == "show":
        return client.get_project_storage(project_id)
    if action == "check":
        return client.check_project_storage(project_id)
    if action == "clear":
        return client.clear_project_storage(project_id)
    s3_url, postgres_uri = collect_locators(args)
    if s3_url is None and postgres_uri is None:
        raise ValueError(
            "nothing to set: pass --env-file, --s3-url(-file), --postgres-uri(-file) or --unset-*"
        )
    return client.set_project_storage(
        project_id,
        s3_url=s3_url,
        postgres_uri=postgres_uri,
        check=not getattr(args, "no_check", False),
    )


def project_storage(
    args: Namespace,
    pm: Any,
    _active_profile: Any = None,
    _reload_config: Any = None,
    _project_config: Any = None,
) -> None:
    """``r4 project storage show|set|check|clear [project]``."""
    from ..client import Client

    project_id = _project_id(args, pm)
    try:
        payload = _dispatch(Client(project_id=project_id), project_id, args)
    except Exception as exc:  # noqa: BLE001 — named CLI failure
        from ._helpers import _die

        _die(
            f"project storage {args.storage_action} failed: {exc}",
            action=f"project.storage.{args.storage_action}",
            error_id=f"project.storage.{args.storage_action}.failed",
        )
        return
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
        return
    if args.storage_action == "clear":
        print(
            f"{project_id}: binding cleared — the server's own stores are used again"
            " (objects already in the external stores stay there)"
        )
        return
    lines = render_binding(project_id, payload.get("storage") or {})
    lines += render_probe(payload.get("probe"))
    print("\n".join(lines))


__all__ = [
    "ENV_PG",
    "ENV_S3",
    "add_storage_parser",
    "collect_locators",
    "project_storage",
    "render_binding",
    "render_probe",
]
