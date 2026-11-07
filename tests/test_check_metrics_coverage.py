"""Tests for tools/check_metrics_coverage.py — dashboard↔code metric sync."""

from __future__ import annotations

import json
import textwrap

# Import the module under test.
from tools.check_metrics_coverage import (
    _expand_emitted_names,
    _extract_code_metrics,
    _extract_dashboard_metrics,
    _parse_promql,
    main,
)

# ---------------------------------------------------------------------------
# PromQL parser
# ---------------------------------------------------------------------------


class TestParsePromql:
    def test_simple_metric(self):
        names = list(_parse_promql("umrs_job_total"))
        assert "umrs_job_total" in names

    def test_rate_expression(self):
        names = list(_parse_promql("rate(umrs_job_total[5m])"))
        assert "umrs_job_total" in names
        assert "rate" not in names

    def test_strips_label_selectors(self):
        names = list(_parse_promql('umrs_job_total{backend="slurm"}'))
        assert "umrs_job_total" in names
        assert "backend" not in names
        assert "slurm" not in names

    def test_strips_by_clause(self):
        names = list(_parse_promql("sum by (backend) (umrs_job_total)"))
        assert "umrs_job_total" in names
        assert "backend" not in names
        assert "sum" not in names

    def test_filters_builtins(self):
        names = list(_parse_promql("up"))
        assert "up" not in names

    def test_filters_thirdparty_prefixes(self):
        names = list(_parse_promql("vllm_request_total + litellm_cost"))
        assert len(list(names)) == 0

    def test_ignores_numeric_literals(self):
        names = list(_parse_promql("umrs_job_total > 0"))
        assert "umrs_job_total" in names
        assert "0" not in names

    def test_histogram_quantile(self):
        expr = "histogram_quantile(0.99, rate(umrs_reconciler_duration_seconds_bucket[5m]))"
        names = list(_parse_promql(expr))
        assert "umrs_reconciler_duration_seconds_bucket" in names
        assert "histogram_quantile" not in names


# ---------------------------------------------------------------------------
# Dashboard extraction
# ---------------------------------------------------------------------------


class TestExtractDashboardMetrics:
    def test_extracts_expr_fields(self, tmp_path):
        dashboard = {
            "panels": [
                {"targets": [{"expr": "rate(umrs_job_total[5m])"}]},
                {"targets": [{"expr": "umrs_transport_errors_total"}]},
            ]
        }
        p = tmp_path / "dash.json"
        p.write_text(json.dumps(dashboard))
        names = _extract_dashboard_metrics(p)
        assert "umrs_job_total" in names
        assert "umrs_transport_errors_total" in names

    def test_empty_dashboard(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("{}")
        assert _extract_dashboard_metrics(p) == set()


# ---------------------------------------------------------------------------
# Code metric extraction (AST-based)
# ---------------------------------------------------------------------------


class TestExtractCodeMetrics:
    def test_counter(self, tmp_path):
        code = textwrap.dedent("""\
            from prometheus_client import Counter
            c = Counter("umrs_test_counter_total", "Test counter", ["label"])
        """)
        p = tmp_path / "mod.py"
        p.write_text(code)
        names = _extract_code_metrics(p)
        assert "umrs_test_counter_total" in names

    def test_histogram(self, tmp_path):
        code = textwrap.dedent("""\
            from prometheus_client import Histogram
            h = Histogram("umrs_test_duration_seconds", "Test duration", ["label"])
        """)
        p = tmp_path / "mod.py"
        p.write_text(code)
        names = _extract_code_metrics(p)
        assert "umrs_test_duration_seconds" in names

    def test_gauge_keyword(self, tmp_path):
        code = textwrap.dedent("""\
            from prometheus_client import Gauge
            g = Gauge(name="umrs_test_gauge", documentation="Test gauge")
        """)
        p = tmp_path / "mod.py"
        p.write_text(code)
        names = _extract_code_metrics(p)
        assert "umrs_test_gauge" in names

    def test_ignores_non_metric_calls(self, tmp_path):
        code = textwrap.dedent("""\
            x = dict("not_a_metric", "hello")
            y = int("42")
        """)
        p = tmp_path / "mod.py"
        p.write_text(code)
        names = _extract_code_metrics(p)
        assert len(names) == 0

    def test_syntax_error_returns_empty(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def broken(:")
        names = _extract_code_metrics(p)
        assert names == set()


# ---------------------------------------------------------------------------
# Suffix expansion
# ---------------------------------------------------------------------------


class TestExpandEmittedNames:
    def test_adds_total_suffix(self):
        expanded = _expand_emitted_names({"umrs_job"})
        assert "umrs_job" in expanded
        assert "umrs_job_total" in expanded

    def test_does_not_double_total(self):
        expanded = _expand_emitted_names({"umrs_job_total"})
        assert "umrs_job_total" in expanded
        # Should NOT contain umrs_job_total_total
        assert "umrs_job_total_total" not in expanded

    def test_adds_histogram_suffixes(self):
        expanded = _expand_emitted_names({"umrs_duration_seconds"})
        assert "umrs_duration_seconds_bucket" in expanded
        assert "umrs_duration_seconds_sum" in expanded
        assert "umrs_duration_seconds_count" in expanded


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------


class TestMain:
    def test_returns_zero_when_in_sync(self, tmp_path, monkeypatch):
        """If dashboards reference exactly the metrics code defines, exit 0."""
        # Create a dashboard referencing one metric
        dash_dir = tmp_path / "observability" / "grafana" / "dashboards"
        dash_dir.mkdir(parents=True)
        (dash_dir / "test.json").write_text(
            json.dumps({"panels": [{"targets": [{"expr": "umrs_test_total"}]}]})
        )

        # Create a code file defining that metric
        code_dir = tmp_path / "umrs" / "src"
        code_dir.mkdir(parents=True)
        (code_dir / "metrics.py").write_text(
            textwrap.dedent("""\
            from prometheus_client import Counter
            c = Counter("umrs_test", "Test", [])
        """)
        )

        import tools.check_metrics_coverage as mod

        monkeypatch.setattr(mod, "REPO", tmp_path)
        assert main([]) == 0

    def test_returns_one_when_missing(self, tmp_path, monkeypatch):
        """If dashboards reference a metric not in code, exit 1."""
        dash_dir = tmp_path / "observability" / "grafana" / "dashboards"
        dash_dir.mkdir(parents=True)
        (dash_dir / "test.json").write_text(
            json.dumps({"panels": [{"targets": [{"expr": "umrs_nonexistent_total"}]}]})
        )

        # No code dirs
        import tools.check_metrics_coverage as mod

        monkeypatch.setattr(mod, "REPO", tmp_path)
        assert main([]) == 1

    def test_json_output(self, tmp_path, monkeypatch, capsys):
        """--json flag produces valid JSON output."""
        dash_dir = tmp_path / "observability" / "grafana" / "dashboards"
        dash_dir.mkdir(parents=True)
        (dash_dir / "test.json").write_text("{}")

        import tools.check_metrics_coverage as mod

        monkeypatch.setattr(mod, "REPO", tmp_path)
        main(["--json"])
        output = json.loads(capsys.readouterr().out)
        assert "dashboard_count" in output
        assert "code_count" in output
        assert "missing" in output
        assert "unused" in output
