#!/usr/bin/env python3
"""Special-case inventory and audit tool (COR-007).

Scans the entire platform source for hidden fallbacks, compatibility modes,
experimental pathways, and duplicate runtime branches.  Classifies each
into one of four categories:

  - **blessed**:      Permanent and intentional — documented in ADR or spec.
  - **compatibility**: Temporary — has a sunset date / retirement ticket.
  - **experimental**:  Behind a feature flag — requires explicit opt-in.
  - **retired**:       Dead code — safe to remove.

Every exception that is not ``blessed`` must be explicitly test-gated.

Usage:
    python tools/special_case_inventory.py              # Human summary
    python tools/special_case_inventory.py --json       # JSON output
    python tools/special_case_inventory.py --csv        # CSV output
    python tools/special_case_inventory.py --check      # Exit 1 if ungoverned items exist
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Source trees to scan
SCAN_DIRS = [
    REPO_ROOT / "umrs" / "src" / "umrs",
    REPO_ROOT / "r4" / "src" / "r4",
    REPO_ROOT / "gateway" / "gateway",
    REPO_ROOT / "mcp-servers" / "src" / "r4_mcp",
]

# File extensions to scan
SCAN_EXTENSIONS = {".py", ".sh", ".yaml", ".yml", ".toml"}

# ── Pattern definitions ─────────────────────────────────────────────────

# Marker patterns with initial category suggestion
MARKER_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Experimental / feature flags
    (
        re.compile(r"EXPERIMENTAL|ENABLE_EXPERIMENTAL", re.IGNORECASE),
        "experimental",
        "Feature flag / experimental pathway",
    ),
    (re.compile(r"RIVAULT_ENABLE_\w+"), "experimental", "Platform feature flag"),
    # Fallback patterns
    (re.compile(r"#\s*fallback\b", re.IGNORECASE), "compatibility", "Fallback logic"),
    (
        re.compile(r"os\.environ\.get\(.+?,\s*.+?\).*#.*fallback", re.IGNORECASE),
        "compatibility",
        "Env var with fallback",
    ),
    # Legacy / deprecated / compat
    (
        re.compile(r"#\s*backward[\s-]*compat", re.IGNORECASE),
        "compatibility",
        "Backward compatibility shim",
    ),
    (re.compile(r"#\s*legacy\b", re.IGNORECASE), "compatibility", "Legacy code path"),
    (
        re.compile(r"#\s*DEPRECATED\b|DEPRECATED\s*[-—]", re.IGNORECASE),
        "compatibility",
        "Deprecated code",
    ),
    (
        re.compile(r"#\s*kept\s+for\s+compat", re.IGNORECASE),
        "compatibility",
        "Kept for compatibility",
    ),
    # Duplicate / alternative runtime branches
    (
        re.compile(r"#\s*alternative\s+(to|path|approach)", re.IGNORECASE),
        "compatibility",
        "Alternative code path",
    ),
    # In-memory fallback for dev mode
    (
        re.compile(r"in[_-]?memory\s*(fallback|store|mode)", re.IGNORECASE),
        "blessed",
        "Dev-mode in-memory fallback",
    ),
]

# Override patterns: explicit governance markers in comments
GOVERNED_PATTERN = re.compile(
    r"#\s*special-case:\s*(blessed|compatibility|experimental|retired)"
    r"(?:\s*sunset=(\S+))?(?:\s*flag=(\S+))?(?:\s*adr=(\S+))?",
    re.IGNORECASE,
)

VALID_CATEGORIES = {"blessed", "compatibility", "experimental", "retired"}


@dataclass(frozen=True)
class SpecialCase:
    """One special-case marker found in source."""

    file: str
    line: int
    kind: str  # pattern description
    category: str  # blessed | compatibility | experimental | retired
    content: str  # line content (trimmed)
    governed: bool  # True if explicit governance comment present
    sunset_date: str  # sunset date if compatibility
    feature_flag: str  # feature flag name if experimental
    adr_ref: str  # ADR reference if documented


@dataclass
class InventoryReport:
    """Complete special-case inventory."""

    items: list[SpecialCase] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        for item in self.items:
            by_category[item.category] = by_category.get(item.category, 0) + 1
        governed = sum(1 for i in self.items if i.governed)
        ungoverned = sum(1 for i in self.items if not i.governed)
        return {
            "total_items": len(self.items),
            "by_category": by_category,
            "governed": governed,
            "ungoverned": ungoverned,
            "unique_files": len({i.file for i in self.items}),
        }


def _rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def scan_file(filepath: Path) -> list[SpecialCase]:
    """Scan a single file for special-case markers."""
    results: list[SpecialCase] = []
    rel = _rel_path(filepath)

    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    lines = content.splitlines()
    for lineno, line in enumerate(lines, 1):
        for pattern, default_category, kind in MARKER_PATTERNS:
            if not pattern.search(line):
                continue

            # Check if this line (or adjacent lines) have a governance marker
            governed = False
            category = default_category
            sunset = ""
            flag = ""
            adr = ""

            # Check current line and next 2 lines for governance
            for check_offset in range(3):
                check_idx = lineno - 1 + check_offset
                if check_idx >= len(lines):
                    break
                gov_match = GOVERNED_PATTERN.search(lines[check_idx])
                if gov_match:
                    governed = True
                    category = gov_match.group(1).lower()
                    sunset = gov_match.group(2) or ""
                    flag = gov_match.group(3) or ""
                    adr = gov_match.group(4) or ""
                    break

            results.append(
                SpecialCase(
                    file=rel,
                    line=lineno,
                    kind=kind,
                    category=category,
                    content=line.strip()[:120],
                    governed=governed,
                    sunset_date=sunset,
                    feature_flag=flag,
                    adr_ref=adr,
                )
            )
            break  # One match per line

    return results


def run_scan() -> InventoryReport:
    """Scan all platform source trees."""
    report = InventoryReport()

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for filepath in sorted(scan_dir.rglob("*")):
            if "__pycache__" in str(filepath):
                continue
            if filepath.suffix not in SCAN_EXTENSIONS:
                continue
            report.items.extend(scan_file(filepath))

    return report


# ── Output formatters ───────────────────────────────────────────────────


def format_human(report: InventoryReport) -> str:
    lines: list[str] = []
    lines.append("Special-Case Inventory (COR-007)")
    lines.append("=" * 66)

    s = report.summary
    lines.append(f"\nTotal items:    {s['total_items']}")
    lines.append(f"Unique files:   {s['unique_files']}")
    lines.append(f"Governed:       {s['governed']}")
    lines.append(f"Ungoverned:     {s['ungoverned']}")
    lines.append(f"By category:    {s['by_category']}")

    # Ungoverned items first (actionable)
    ungoverned = [i for i in report.items if not i.governed]
    if ungoverned:
        lines.append("\nUNGOVERNED (action required):")
        lines.append("-" * 66)
        for item in ungoverned:
            lines.append(f"  [{item.category}] {item.file}:{item.line}")
            lines.append(f"        {item.content}")
            lines.append(f"        Kind: {item.kind}")
            lines.append(f"        → Add: # special-case: {item.category}")

    # Governed items
    governed = [i for i in report.items if i.governed]
    if governed:
        lines.append(f"\nGOVERNED ({len(governed)} items):")
        lines.append("-" * 66)
        for item in governed:
            suffix = ""
            if item.sunset_date:
                suffix += f" sunset={item.sunset_date}"
            if item.feature_flag:
                suffix += f" flag={item.feature_flag}"
            if item.adr_ref:
                suffix += f" adr={item.adr_ref}"
            lines.append(f"  [{item.category}] {item.file}:{item.line}{suffix}")

    return "\n".join(lines) + "\n"


def format_json(report: InventoryReport) -> str:
    return json.dumps(
        {"summary": report.summary, "items": [asdict(i) for i in report.items]},
        indent=2,
    )


def format_csv(report: InventoryReport) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "file",
            "line",
            "kind",
            "category",
            "governed",
            "sunset_date",
            "feature_flag",
            "adr_ref",
            "content",
        ]
    )
    for item in report.items:
        writer.writerow(
            [
                item.file,
                item.line,
                item.kind,
                item.category,
                item.governed,
                item.sunset_date,
                item.feature_flag,
                item.adr_ref,
                item.content,
            ]
        )
    return buf.getvalue()


def main() -> int:
    report = run_scan()

    if "--json" in sys.argv:
        print(format_json(report))
    elif "--csv" in sys.argv:
        print(format_csv(report))
    else:
        print(format_human(report))

    if "--check" in sys.argv:
        ungoverned = sum(1 for i in report.items if not i.governed)
        if ungoverned > 0:
            print(f"\n❌ {ungoverned} ungoverned special cases found.", file=sys.stderr)
            return 1
        print("\n✅ All special cases are governed.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
