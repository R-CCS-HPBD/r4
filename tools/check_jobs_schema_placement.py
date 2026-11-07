"""Jobs-authority schema placement check (G-P4-4, ADR-034 rollout 2026-09-07).

Decision: durable-job authority (``jobs``, ``job_attempts`` and their
conservation satellites like ``a2l_artifacts``) lives in the SHARED
``umrs_db`` for every cluster on `umrs-front`. Per-cluster databases
(``umrs_rccs``, ``umrs_ai4s``, ``front_bdrt``, ``front_octopus``) are
registry-only and must NEVER grow jobs tables.

This script is read-only. It classifies the table list of each DSN and
fails when placement drifts:

  jobs-present       the shared DB has the required jobs-authority tables
  jobs-unexpected    a registry-only DB has any jobs-authority table

Usage:
    python tools/check_jobs_schema_placement.py \
        --jobs-dsn postgresql://user:pw@127.0.0.1:5432/umrs_db \
        --registry-dsn umrs_ai4s=postgresql://user:pw@127.0.0.1:5432/umrs_ai4s
    python tools/check_jobs_schema_placement.py --json <same args>

Run it from a host or container that can reach the databases and has
psycopg2 installed (for example, inside a UMRS server container).

Exit 0 when placement matches the decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field

# Core jobs-authority tables that must exist in the shared DB.
REQUIRED_JOBS_TABLES: tuple[str, ...] = ("jobs", "job_attempts")

# Tables that mark a DB as jobs-authority when present anywhere else.
JOBS_MARKER_TABLES: frozenset[str] = frozenset({"jobs", "job_attempts"})


@dataclass
class Finding:
    rule: str
    scope: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def classify_placement(
    jobs_db_tables: set[str],
    registry_tables: dict[str, set[str]],
    jobs_scope: str = "umrs_db",
) -> Report:
    """Pure classification — unit-testable without a database."""
    report = Report()

    missing = [t for t in REQUIRED_JOBS_TABLES if t not in jobs_db_tables]
    for table in missing:
        report.findings.append(
            Finding(
                rule="jobs-present",
                scope=jobs_scope,
                message=f"jobs-authority table '{table}' missing from the shared jobs DB",
            )
        )

    for name, tables in sorted(registry_tables.items()):
        leaked = sorted(t for t in tables if t in JOBS_MARKER_TABLES)
        for table in leaked:
            report.findings.append(
                Finding(
                    rule="jobs-unexpected",
                    scope=name,
                    message=f"jobs-authority table '{table}' present in registry-only DB '{name}'",
                )
            )
    return report


def _list_tables(dsn: str) -> set[str]:
    """Read-only table listing via psycopg2 (or psycopg3)."""
    try:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(dsn, connect_timeout=5)
    except ImportError:
        try:
            import psycopg  # type: ignore

            conn = psycopg.connect(dsn, connect_timeout=5)
        except ImportError as exc:  # pragma: no cover - host dependent
            raise SystemExit("psycopg2 (or psycopg) is required to query DSNs") from exc
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-dsn", required=True, help="DSN of the shared jobs DB")
    parser.add_argument(
        "--registry-dsn",
        action="append",
        default=[],
        metavar="NAME=DSN",
        help="registry-only DB (repeatable)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    # Validate DSN shapes BEFORE opening any connection.
    registry_args: list[tuple[str, str]] = []
    for item in args.registry_dsn:
        name, _, dsn = item.partition("=")
        if not name or not dsn:
            parser.error(f"--registry-dsn expects NAME=DSN, got {item!r}")
        registry_args.append((name, dsn))

    jobs_tables = _list_tables(args.jobs_dsn)
    registry = {name: _list_tables(dsn) for name, dsn in registry_args}

    report = classify_placement(jobs_tables, registry)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2))
    elif report.ok:
        print(
            f"jobs-placement: OK (jobs DB has {sorted(REQUIRED_JOBS_TABLES)}; "
            f"{len(registry)} registry DB(s) clean)"
        )
    else:
        print(f"jobs-placement: {len(report.findings)} problem(s)")
        for f in report.findings:
            print(f"  FAIL [{f.rule}] {f.scope}: {f.message}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
