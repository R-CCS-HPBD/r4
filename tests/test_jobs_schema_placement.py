"""Tests for tools/check_jobs_schema_placement.py (G-P4-4, WP-14).

Simple import machinery never opens a connection — the connection helper is
module-level so tests can stub it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from tools import check_jobs_schema_placement as m  # noqa: E402


class TestClassifyPlacement:
    def test_happy_path(self):
        report = m.classify_placement(
            {"jobs", "job_attempts", "a2l_artifacts"},
            {"umrs_ai4s": {"endpoint_registry", "schema_migrations"}},
        )
        assert report.ok

    def test_missing_jobs_table_in_shared_db(self):
        report = m.classify_placement({"jobs"}, {"umrs_ai4s": set()})
        assert not report.ok
        assert [f.rule for f in report.findings] == ["jobs-present"]

    def test_leaked_table_in_registry_db_fails(self):
        report = m.classify_placement(
            {"jobs", "job_attempts"},
            {"umrs_ai4s": {"jobs", "endpoint_registry"}},
        )
        assert not report.ok
        assert [f.rule for f in report.findings] == ["jobs-unexpected"]
        assert report.findings[0].scope == "umrs_ai4s"

    def test_multiple_leaks_reported_in_order(self):
        report = m.classify_placement(
            {"jobs", "job_attempts"},
            {
                "umrs_ai4s": {"job_attempts"},
                "umrs_rccs": {"jobs"},
            },
        )
        assert len(report.findings) == 2
        assert [f.scope for f in report.findings] == ["umrs_ai4s", "umrs_rccs"]

    def test_registry_db_with_only_whitelisted_surface_ok(self):
        report = m.classify_placement(
            {"jobs", "job_attempts"},
            {
                "umrs_ai4s": {
                    "endpoint_registry",
                    "gateway_audit_log",
                    "llm_usage_log",
                    "schema_migrations",
                }
            },
        )
        assert report.ok


class TestCli:
    def test_registry_dsn_rejects_malformed(self):
        bad = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools/check_jobs_schema_placement.py"),
                "--jobs-dsn",
                "postgresql://u:p@127.0.0.1:5432/umrs_db",
                "--registry-dsn",
                "no-equals-sign",
            ],
            capture_output=True,
            text=True,
        )
        assert bad.returncode != 0
        assert "NAME=DSN" in bad.stderr


class TestPlacementDecisionIsDocumented:
    """The decision itself stays discoverable where an operator looks first."""

    def test_compose_pins_every_cluster_server(self):
        compose = (REPO_ROOT / "umrs/deploy/umrs-front/docker-compose.yaml").read_text()
        for service in (
            "umrs-server-rccs",
            "umrs-server-ai4s",
            "umrs-server-bdrt",
            "umrs-server-octopus",
        ):
            section = compose.split(f"  {service}:", 1)[1].split("\n  umrs-server-", 1)[0]
            assert "UMRS_JOBS_DB" in section, f"{service} missing the explicit jobs-DB pin"
        assert compose.count("UMRS_JOBS_DB: ${UMRS_JOBS_DB:-umrs_db}") == 4
