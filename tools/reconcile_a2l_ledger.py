#!/usr/bin/env python3
"""A2L ledger reconciliation validator (ADR-034 Phase 2).

Read-only audit of the conservation ledger against the authoritative
execution spine (jobs/attempts) during the parallel-run window.

Checks (per window):
  1. **coverage** — every terminal attempt in the window that produced
     attempt-scoped artifacts must have a corresponding `a2l_artifacts`
     row keyed by (job_id, attempt_id). Missing rows are divergences.
  2. **orphans** — every a2l row (artifacts, decisions, executions) with a
     job_id must join to a `jobs` row counterpart. Unjoined = orphan.
  3. **dup_key_drift** — the same (content_hash, operation-ish hint,
     artifact type) must not materialize into different deterministic ids.

Divergence classes:
  - ``poller_latency``: an uncovered attempt whose completion is within
    ``--tolerance`` seconds of the window end (a poller/emission would still
    be catching up). Expected; does NOT fail the run.
  - ``unexplained``: everything else. Zero unexplained = pass.

Exit code: 0 pass, 1 fail (any unexplained), 2 usage/config error.

Usage:
  python tools/reconcile_a2l_ledger.py \
      [--since-hours 24] [--tolerance 600] [--dsn postgres...] [--json]

DSN resolution order: --dsn > UMRS_DATA_CATALOG_DB > DATABASE_URL >
component vars (UMRS_JOBS_DB/DB_HOST/POSTGRES_* — jobs-pool parity).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone


def _resolve_dsn(cli_dsn: str) -> str:
    """DSN resolution — mirrors api._asyncpg_pool._resolve_knowledge_dsn."""
    if cli_dsn:
        return cli_dsn
    for var in ("UMRS_DATA_CATALOG_DB", "DATABASE_URL"):
        dsn = os.environ.get(var, "")
        if dsn:
            return dsn
    component_vars = (
        "DB_HOST",
        "DB_PORT",
        "UMRS_JOBS_DB",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "DB_SUPERUSER",
        "DB_SUPERUSER_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    )
    if not any(os.environ.get(var) for var in component_vars):
        return ""
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", os.environ.get("POSTGRES_PORT", "5432"))
    name = os.environ.get("UMRS_JOBS_DB", os.environ.get("POSTGRES_DB", "umrs_db"))
    user = os.environ.get("DB_SUPERUSER", os.environ.get("POSTGRES_USER", "postgres"))
    pw = os.environ.get("DB_SUPERUSER_PASSWORD", os.environ.get("POSTGRES_PASSWORD", "postgres"))
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


def _classify(divergence_kind: str, *, within_tolerance: bool) -> str:
    """Classify a divergence: tolerance window → poller_latency, else unexplained."""
    if divergence_kind == "uncovered_attempt" and within_tolerance:
        return "poller_latency"
    return "unexplained"


async def _check_coverage(
    conn, window_start: datetime, window_end: datetime, tolerance: int
) -> dict:
    """Attempts terminal in window vs conserved artifact rows per (job, attempt)."""
    rows = await conn.fetch(
        """
        SELECT job_id, attempt_id, ended_at AS completed_at
        FROM job_attempts
        WHERE status IN ('succeeded', 'failed', 'lost')
          AND ended_at >= $1 AND ended_at <= $2
        ORDER BY ended_at
        """,
        window_start,
        window_end,
    )
    conserved = await conn.fetch(
        """
        SELECT DISTINCT job_id, attempt_id
        FROM a2l_artifacts
        WHERE job_id IS NOT NULL
          AND created_at >= $1
        """,
        window_start,
    )
    conserved_keys = {(r["job_id"], r["attempt_id"]) for r in conserved}
    divergences = []
    covered = 0
    for row in rows:
        key = (row["job_id"], row["attempt_id"])
        if key in conserved_keys:
            covered += 1
            continue
        completed = row["completed_at"]
        lag_ok = (
            isinstance(completed, datetime)
            and (window_end - completed).total_seconds() <= tolerance
        )
        if not lag_ok:
            divergences.append(
                {
                    "class": "unexplained",
                    "kind": "uncovered_attempt",
                    "job_id": key[0],
                    "attempt_id": key[1],
                    "completed_at": str(completed),
                }
            )
        else:
            divergences.append(
                {
                    "class": "poller_latency",
                    "kind": "uncovered_attempt",
                    "job_id": key[0],
                    "attempt_id": key[1],
                    "completed_at": str(completed),
                }
            )
    return {
        "check": "coverage",
        "expected": len(rows),
        "actual": covered,
        "divergences": divergences,
    }


async def _check_orphans(conn, window_start: datetime) -> dict:
    """a2l rows with job_id that lacks a spine counterpart."""
    divergences = []
    for table in ("a2l_artifacts", "a2l_decisions", "a2l_agent_executions"):
        rows = await conn.fetch(
            f"""
            SELECT t.id AS row_id, t.job_id
            FROM {table} t
            LEFT JOIN jobs j ON j.job_id = t.job_id
            WHERE t.job_id IS NOT NULL
              AND t.created_at >= $1
              AND j.job_id IS NULL
            """,
            window_start,
        )
        for row in rows:
            divergences.append(
                {
                    "class": "unexplained",
                    "kind": "orphan",
                    "table": table,
                    "row_id": row["row_id"],
                    "job_id": row["job_id"],
                }
            )
    return {
        "check": "orphans",
        "expected": 0,
        "actual": len(divergences),
        "divergences": divergences,
    }


async def _check_dup_drift(conn, window_start: datetime) -> dict:
    """Same content hash must not map to multiple a2l ids (id determinism)."""
    # metadata->>'operation' is only set when callers pass it; uri+type is
    # the stable proxy for "same derived content".
    rows = await conn.fetch(
        """
        SELECT uri, type, COUNT(DISTINCT id) AS id_count
        FROM a2l_artifacts
        WHERE created_at >= $1
        GROUP BY uri, type
        HAVING COUNT(DISTINCT id) > 1
        """,
        window_start,
    )
    divergences = [
        {
            "class": "unexplained",
            "kind": "dup_key_drift",
            "uri": row["uri"],
            "type": row["type"],
            "id_count": row["id_count"],
        }
        for row in rows
    ]
    return {
        "check": "dup_key_drift",
        "expected": 0,
        "actual": len(divergences),
        "divergences": divergences,
    }


async def _run(args: argparse.Namespace) -> int:
    dsn = _resolve_dsn(args.dsn)
    if not dsn:
        print("No DSN available (--dsn / UMRS_DATA_CATALOG_DB / DATABASE_URL)", file=sys.stderr)
        return 2
    try:
        import asyncpg
    except ImportError:
        print("asyncpg not installed in this environment", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(hours=args.since_hours)
        results = [
            await _check_coverage(conn, window_start, window_end, args.tolerance),
            await _check_orphans(conn, window_start),
            await _check_dup_drift(conn, window_start),
        ]
    finally:
        await conn.close()

    unexplained_total = 0
    for result in results:
        unexplained = [d for d in result["divergences"] if d["class"] == "unexplained"]
        unexplained_total += len(unexplained)
        line = {
            "check": result["check"],
            "expected": result["expected"],
            "actual": result["actual"],
            "unexplained": len(unexplained),
            "divergences": result["divergences"],
        }
        if args.json:
            print(json.dumps(line, default=str))
        else:
            latency = len(result["divergences"]) - len(unexplained)
            print(
                f"{result['check']:<14} expected={result['expected']:>5} "
                f"actual={result['actual']:>5} unexplained={len(unexplained)} "
                f"poller_latency={latency}"
            )
    return 1 if unexplained_total else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--tolerance", type=int, default=600, help="poller latency seconds")
    parser.add_argument("--dsn", default="", help="Postgres DSN override")
    parser.add_argument("--json", action="store_true", help="JSONL output")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
