"""Generate docs/verification/gap-register.md from docs/verification/traceability.yaml.

Single source of truth for the gap register is traceability.yaml; this tool
renders the markdown. Run after editing the yaml:

    python tools/gen_gap_register.py            # write the register
    python tools/gen_gap_register.py --check    # drift check (CI-advisory)

Enforcement class: ADVISORY drift check (do not gate without a policy branch).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY = "docs/verification/traceability.yaml"
DEFAULT_OUTPUT = "docs/verification/gap-register.md"
SEVERITY_HEADINGS = {
    "S1": "## S1 — Contradicts a principle or invariant",
    "S2": "## S2 — Scientist-visible capability missing or builder-only",
    "S3": "## S3 — Friction / single-surface / untested",
}
GAP_COLUMNS = ("ID", "Claim", "Status", "Evidence", "Remedy", "WP")


def _flatten(text: Any) -> str:
    """Collapse multi-line scalars and escape pipes for a table cell."""
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _table(headers: tuple[str, ...], rows: list[list[str]]) -> str:
    """Render a GitHub-flavored markdown table."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("----" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def severity_table(gaps: list[dict[str, Any]], severity: str) -> str:
    """Render one severity's gap table (Closed gaps are not rendered)."""
    rows = [
        [
            g["id"],
            _flatten(g.get("claim")),
            g.get("status", ""),
            _flatten(g.get("evidence")),
            _flatten(g.get("remedy")),
            g.get("package", ""),
        ]
        for g in gaps
        if g.get("severity") == severity and g.get("status") != "Closed"
    ]
    return SEVERITY_HEADINGS[severity] + "\n\n" + _table(GAP_COLUMNS, rows)


def doc_drift_table(entries: list[dict[str, Any]]) -> str:
    """Render the S4 documentation-drift table."""
    rows = [
        [d["id"], _flatten(d.get("item")), _flatten(d.get("evidence")), _flatten(d.get("remedy"))]
        for d in entries
    ]
    return "## S4 — Documentation drift\n\n" + _table(("ID", "Item", "Evidence", "Remedy"), rows)


def _package_row(order: str, pid: str, pkg: dict[str, Any]) -> list[str]:
    """Render one work-package table row."""
    return [
        order,
        pid,
        _flatten(pkg.get("title")),
        _flatten(pkg.get("principle")),
        ", ".join(pkg.get("gaps_closed") or []) or "—",
        pkg.get("risk", ""),
        _flatten(pkg.get("adr")),
        _flatten(pkg.get("notes")),
    ]


def packages_section(doc: dict[str, Any]) -> str:
    """Render the work-package plan: ratified sequence first, then later additions.

    A package declared in traceability.yaml but absent from ``wp_sequence`` is a
    post-ratification addition. It has no ratified position, so it renders with an
    em-dash order after the sequenced rows instead of being dropped from the plan
    (dropping it also hid the gaps it closed from the whole register).
    """
    packages = {p["id"]: p for p in doc.get("packages", []) or []}
    sequence = (doc.get("ratifications") or {}).get("wp_sequence") or list(packages)
    sequenced = set(sequence)
    rows = [
        _package_row(str(order), pid, packages[pid])
        for order, pid in enumerate(sequence, start=1)
        if pid in packages
    ]
    rows += [_package_row("—", pid, pkg) for pid, pkg in packages.items() if pid not in sequenced]
    header = (
        "\n---\n\n## Work-package plan (ratified execution order, 2026-09-06)\n\n"
        "Order `—` marks a package added after the 2026-09-06 ratification "
        "(declared in `traceability.yaml`, not in `wp_sequence`).\n\n"
        + _table(
            ("Order", "ID", "Package", "Principle", "Gaps closed", "Risk", "ADR", "Notes"), rows
        )
    )
    return header


def render(doc: dict[str, Any]) -> str:
    """Render the full register document."""
    report = doc.get("report_file", f"{doc['date']}-design-verification.md")
    header = f"""# Gap Register — {doc["date"]} Design Verification

<!--
GENERATED FILE — DO NOT HAND-EDIT.
Source of truth: docs/verification/traceability.yaml
Regenerate: python tools/gen_gap_register.py  ·  Drift check: --check
-->

Date: {doc["date"]} · Branch: `{doc["branch"]}`
Companion: [{report}]({report}) · [traceability.yaml](traceability.yaml)

Severity scale: **S1** contradicts a principle or invariant (security, authority, accounting) · **S2** scientist-visible capability missing or builder-only · **S3** friction / single-surface / untested · **S4** documentation drift.

Every gap cites its evidence. Row ↔ register consistency is enforced by [tools/traceability_validator.py](../../tools/traceability_validator.py) (bidirectional cross-references, no gap on `Implemented` rows, status agreement).

"""
    body = "\n\n".join(severity_table(doc["gaps"], sev) for sev in ("S1", "S2", "S3"))
    closed_count = sum(1 for g in doc.get("gaps", []) if g.get("status") == "Closed")
    if closed_count:
        body += (
            f"\n\n_Closed gaps: {closed_count} (kept in traceability.yaml, not rendered here)._\n"
        )
    drift = doc_drift_table(doc.get("doc_drift", []))
    ordering = (doc.get("ratifications") or {}).get("wp_order", "")
    footer = (
        "\n\n**Ordering note:** ratified execution order per maintainer (2026-09-06): "
        + _flatten(ordering)
        + "\n"
    )
    return header + body + "\n\n" + drift + "\n" + packages_section(doc) + footer


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if output differs from file")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / args.registry).read_text(encoding="utf-8"))
    rendered = render(doc)
    out_path = root / args.out
    if args.check:
        current = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if current != rendered:
            count = len(doc.get("gaps", []))
            print(f"FAIL: {args.out} out of date ({count} gaps) — run tools/gen_gap_register.py")
            return 1
        print(f"gap-register: up to date ({len(doc.get('gaps', []))} gaps)")
        return 0
    out_path.write_text(rendered, encoding="utf-8")
    print(
        f"Rendered {args.out} ({len(doc.get('gaps', []))} gaps, "
        f"{len(doc.get('packages', []))} packages)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
