"""Metrics coverage linter (bug.memo OBS1).

Compares the Prometheus metrics referenced by Grafana dashboards under
``observability/grafana/dashboards/*.json`` against the metrics actually
defined in the source code (``prometheus_client.Counter/Histogram/Gauge``
constructors) and reports:

  * **missing** metrics — referenced by dashboards but not defined in the
    code; these produce "No data" panels.
  * **unused** metrics — defined in the code but never referenced by any
    dashboard; usually a sign that telemetry is being emitted with no one
    watching.

Run:

    python tools/check_metrics_coverage.py

Exit codes:

    0  — dashboards and code are in sync.
    1  — at least one missing metric (hard failure; default).
    2  — only unused metrics (informational; controllable via --strict).

Design notes
------------

* We parse Grafana JSON as plain dicts, walking for any ``"expr"`` keys
  and extracting bare metric names via a regex. Recording-rule metrics
  (``rivault:requests_total``) are preserved verbatim; builtin Prom
  metrics like ``up`` are filtered via :data:`_BUILTINS`.
* For code scanning we use AST instead of regex, so docstrings and
  comments do not produce false positives.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys
from collections.abc import Iterable

REPO = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD_GLOB = "observability/grafana/dashboards/*.json"
# Alert rules consume series without appearing on dashboards — the
# "defined but unreferenced" set is only exactly true when BOTH surfaces are
# scanned (WP-13 r2 follow-through; rivault_* gateway series were falsely
# marked unused).
ALERT_GLOBS = ("observability/prometheus/*.yaml", "observability/prometheus/*.yml")
CODE_DIRS = ("gateway", "umrs/src", "r4/src", "mcp-servers/src")

# Prometheus builtins + node-exporter/kube-state series we don't own.
_BUILTINS = {
    "up",
    "scrape_duration_seconds",
    "scrape_samples_scraped",
    "process_cpu_seconds_total",
    "process_resident_memory_bytes",
    "python_info",
    "ALERTS",
    "go_goroutines",
}

# Third-party metric prefixes we don't emit ourselves (vLLM, LiteLLM,
# node-exporter). Dashboards may reference them but code coverage does
# not apply.
_THIRDPARTY_PREFIXES = (
    "vllm_",
    "vllm:",  # vLLM exporter metric names (Prometheus exporter convention)
    "litellm_",
    "litellm:",
    "node_",
    "container_",
    "kube_",
    "minio_",  # MinIO exporter metrics (dashboards/alerts consume, never code)
    "pg_",  # postgres_exporter metrics (same)
    "kubelet_",
)

# Matches a metric-name token. Drops PromQL keywords and function calls.
_METRIC_RE = re.compile(r"\b([a-zA-Z_:][a-zA-Z0-9_:]*)\b")
_PROMQL_KEYWORDS = {
    "by",
    "on",
    "without",
    "group_left",
    "group_right",
    "ignoring",
    "sum",
    "rate",
    "irate",
    "avg",
    "max",
    "min",
    "count",
    "histogram_quantile",
    "increase",
    "clamp",
    "clamp_min",
    "clamp_max",
    "delta",
    "topk",
    "bottomk",
    "quantile",
    "stddev",
    "stdvar",
    "vector",
    "scalar",
    "time",
    "if",
    "else",
    "and",
    "or",
    "unless",
    "bool",
    "offset",
    "last_over_time",
    "avg_over_time",
    "max_over_time",
    "min_over_time",
    "sum_over_time",
    "count_over_time",
    "rate_limit",
    "label_replace",
    "label_join",
    "sort",
    "sort_desc",
    "absent",
    "absent_over_time",
    "changes",
    "deriv",
    "predict_linear",
    "resets",
    "le",
    "job",
    "instance",
}


def _extract_dashboard_metrics(path: pathlib.Path) -> set[str]:
    data = json.loads(path.read_text())
    names: set[str] = set()

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            expr = obj.get("expr")
            if isinstance(expr, str):
                names.update(_parse_promql(expr))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return names


def _parse_promql(expr: str) -> Iterable[str]:
    # Strip label selectors `{...}`, string literals, and grouping
    # clauses (`by (...)`, `without (...)`, `on (...)`, `ignoring(...)`)
    # so label names don't leak in as metric names.
    stripped = re.sub(r"\{[^}]*\}", "", expr)
    stripped = re.sub(r'"[^"]*"', "", stripped)
    stripped = re.sub(
        r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)",
        "",
        stripped,
    )
    # Also strip duration literals like [5m], [1h30s].
    stripped = re.sub(r"\[[^\]]+\]", "", stripped)
    for match in _METRIC_RE.finditer(stripped):
        tok = match.group(1)
        if tok in _PROMQL_KEYWORDS or tok in _BUILTINS:
            continue
        if tok.startswith(_THIRDPARTY_PREFIXES):
            continue
        if tok.replace(".", "").isdigit():
            continue
        yield tok


_METRIC_CTORS = {"Counter", "Histogram", "Gauge", "Summary"}

# Lazy/wrapper factory aliases (this codebase indirections: umrs/metrics.py
# _counter/_gauge/_histogram choose real ctor vs NoOp at runtime — the
# metric names were still static-string detectable, they just weren't
# recognized).
_METRIC_ALIASES = {"_counter", "_histogram", "_gauge", "_summary"}


def _call_metric_ctor(node: ast.Call) -> str | None:
    """Constructor name of one metric call (attribute or bare id)."""
    return (
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else node.func.id
        if isinstance(node.func, ast.Name)
        else None
    )


def _string_constant(node: ast.expr) -> str | None:
    """Value of a compile-time string constant; None otherwise."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_metric_name(node: ast.Call) -> str | None:
    """Static name of one metric-constructor call (positional or ``name=``)."""
    fname = _call_metric_ctor(node)
    if fname not in _METRIC_CTORS and fname not in _METRIC_ALIASES:
        return None
    # First positional argument = metric name.
    if node.args:
        found = _string_constant(node.args[0])
        if found is not None:
            return found
    for kw in node.keywords:
        if kw.arg != "name":
            continue
        found = _string_constant(kw.value)
        if found is not None:
            return found
    return None


def _extract_code_metrics(path: pathlib.Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_metric_name(node)
        if name:
            names.add(name)
    return names


def collect_dashboard_metrics() -> set[str]:
    out: set[str] = set()
    for path in REPO.glob(DASHBOARD_GLOB):
        out.update(_extract_dashboard_metrics(path))
    return out


def collect_alert_metrics() -> set[str]:
    """Prometheus alert/group rule files — raw PromQL text (yaml-safe scan).

    Text-scanned instead of yaml-parsed so `expr:` values and label values
    never need yaml semantics; PromQL extraction handles the rest.
    """
    out: set[str] = set()
    for pattern in ALERT_GLOBS:
        for path in REPO.glob(pattern):
            try:
                out.update(_extract_alert_metric_names(path.read_text()))
            except (OSError, UnicodeDecodeError):
                continue
    return out


def _extract_alert_metric_names(text: str) -> set[str]:
    """Pull metric-name candidates from a raw alert yaml file."""
    out: set[str] = set()
    for match in re.finditer(r"expr\s*:\s*(?:\|\s*\n?)?(?:>?\s*\n?)?'([^\n']+)'", text):
        out.update(_parse_promql(match.group(1)))
    # plain-string + flow / multi-line quoted scalars
    for match in re.finditer(r"expr\s*:\s*([^\n#]+)", text):
        if match.group(1).strip() in ("|", ">", "|-", ">-"):
            continue
        out.update(_parse_promql(match.group(1)))
    # block-scalar bodies (expr: | / expr: >): following indented lines
    m_lines = text.splitlines()
    for i, line in enumerate(m_lines):
        if re.match(r"\s+expr\s*:\s*[|>]\s*$", line):
            indent = len(line) - len(line.lstrip())
            for body in m_lines[i + 1 :]:
                body_indent = len(body) - len(body.lstrip())
                if body.strip() and body_indent > indent:
                    out.update(_parse_promql(body))
                else:
                    break
    return out


def collect_code_metrics() -> set[str]:
    out: set[str] = set()
    for d in CODE_DIRS:
        root = REPO / d
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            out.update(_extract_code_metrics(path))
    return out


# Histogram-derived series Prometheus exposes alongside the base name.
_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


def _expand_emitted_names(base_names: set[str]) -> set[str]:
    """Expand AST-extracted names to the full wire-format set.

    ``prometheus_client`` exposes more than the literal constructor name:

    * Counter ``foo`` is exposed as ``foo_total`` (or ``foo`` if the user
      already wrote ``_total``).
    * Histogram ``foo_seconds`` is exposed as ``foo_seconds_bucket``,
      ``foo_seconds_sum``, ``foo_seconds_count``.
    * Summary works like Histogram (``_sum``, ``_count``).

    We don't know the constructor type from the AST visitor (we'd have to
    plumb that through), so we expand every base name with every suffix.
    Over-approximating is safe: dashboards that reference a non-existent
    suffix would still surface as MISSING because the base name wouldn't
    match either.
    """
    out: set[str] = set(base_names)
    for n in base_names:
        if not n.endswith("_total"):
            out.add(n + "_total")
        for s in _HISTOGRAM_SUFFIXES:
            out.add(n + s)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--strict", action="store_true", help="Treat unused metrics as a hard failure (exit 2)."
    )
    ap.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of text."
    )
    args = ap.parse_args(argv)

    dashboards = collect_dashboard_metrics()
    alerts = collect_alert_metrics()
    referenced = dashboards | alerts
    code = collect_code_metrics()
    emitted = _expand_emitted_names(code)

    missing = sorted(referenced - emitted)
    unused = _unused_series(code, referenced)

    _emit_report(args, missing, unused, len(dashboards), len(code))

    if missing:
        return 1
    if unused and args.strict:
        return 2
    return 0


def _unused_series(code: set[str], dashboards: set[str]) -> list[str]:
    """Defined but unreferenced — literal base names vs expanded spotted forms."""
    return sorted(
        n
        for n in code
        if not (
            n in dashboards
            or (n + "_total") in dashboards
            or any((n + s) in dashboards for s in _HISTOGRAM_SUFFIXES)
        )
    )


def _emit_report(
    args, missing: list[str], unused: list[str], dash_count: int, code_count: int
) -> None:
    """One output policy: JSON for pipelines, text for humans."""
    if args.json:
        print(
            json.dumps(
                {
                    "dashboard_count": dash_count,
                    "code_count": code_count,
                    "missing": missing,
                    "unused": unused,
                },
                indent=2,
            )
        )
        return
    print(f"Dashboards reference {dash_count} metric(s); code defines {code_count} metric(s).")
    if missing:
        print("\nMISSING (referenced by dashboards, not defined in code):")
        for name in missing:
            print(f"  - {name}")
    if unused:
        print("\nUNUSED (defined in code, not referenced by any dashboard):")
        for name in unused:
            print(f"  - {name}")
    if not missing and not unused:
        print("OK: metrics are in sync.")


if __name__ == "__main__":
    sys.exit(main())
