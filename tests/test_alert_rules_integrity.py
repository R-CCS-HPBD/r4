"""Alert-rules integrity pins — row P11-C3 of traceability.yaml.

The operator-facing alert contract ("operators see outages first with
playbooks") rests on observability/prometheus/alerts.yaml loading cleanly
and every rule carrying the fields Prometheus requires to fire and route.
These pins fail on the failure classes that silently killed alerting before:
rules without expressions, duplicate names, missing severity routing labels.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALERTS_YAML = ROOT / "observability" / "prometheus" / "alerts.yaml"

ALLOWED_SEVERITIES = {"critical", "warning", "info", "page"}


def _load_rules():
    doc = yaml.safe_load(ALERTS_YAML.read_text())
    assert isinstance(doc, dict) and doc.get("groups"), "alerts.yaml must be a groups document"
    rules = []
    for group in doc["groups"]:
        assert group.get("name"), "every alert group needs a name"
        assert group.get("rules"), f"group {group.get('name')} has no rules"
        for rule in group["rules"]:
            rules.append((group["name"], rule))
    return rules


def test_job_failure_rate_counts_terminal_outcomes_with_a_volume_floor():
    """Ops-watch #511 (2026-10-27): the ratio divided by every job event
    (``submitted`` twice per job, retries) with no volume floor, so 2 failures
    in ~4 jobs an hour fired a platform warning."""
    rules = {rule["alert"]: rule for _, rule in _load_rules()}
    expr = rules["JobFailureRateHigh"]["expr"]
    assert "sum(rate(umrs_job_total[1h]))" not in expr
    assert 'event=~"completed|failed|lost"' in expr
    assert ">= 10" in expr


def test_alert_count_floor():
    # 23 rules today (post ADR-014 prune); floor guards against accidental
    # whole-file truncation or a bad merge dropping every group but one.
    assert len(_load_rules()) >= 20


def test_every_rule_carries_the_firing_contract():
    for group, rule in _load_rules():
        for field in ("alert", "expr", "labels", "annotations"):
            assert rule.get(field), f"{group}: rule missing '{field}': {rule.get('alert')!r}"
        expr = rule["expr"].strip()
        assert expr, f"{group}/{rule['alert']}: empty PromQL expression"
        assert rule["annotations"].get("summary"), (
            f"{group}/{rule['alert']}: summary annotation empty — operators see unnamed firings"
        )


def test_severity_label_present_and_routable():
    for group, rule in _load_rules():
        severity = rule["labels"].get("severity")
        assert severity in ALLOWED_SEVERITIES, (
            f"{group}/{rule['alert']}: severity {severity!r} not in "
            f"{sorted(ALLOWED_SEVERITIES)} — Alertmanager routing keys off this label"
        )


def test_no_duplicate_alert_names():
    names = [rule["alert"] for _, rule in _load_rules()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate alert names confuse dedup/routing: {sorted(dupes)}"
