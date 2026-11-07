"""Compatibility bridge detector — track re-export bridges, deprecation layers,
and transitional APIs to prevent lifecycle drift.

Classifies each bridge as:
  * active      — in use, load-bearing
  * likely_dead — no apparent consumers
  * removable   — marked for removal and conditions are met
  * review      — uncertain, needs manual review

Usage:
    python tools/bridge_detector.py               # human-readable
    python tools/bridge_detector.py --json         # machine-readable

Enforcement class: ADVISORY (no hard failures)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeEntry:
    """One compatibility bridge found in the source."""

    file: str
    line: int
    kind: str  # Category of bridge
    description: str  # What the bridge does
    classification: str  # "active" | "likely_dead" | "removable" | "review"
    evidence: str  # Why classified this way


@dataclass
class BridgeReport:
    """Complete bridge detection report."""

    bridges: list[BridgeEntry] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total_bridges": len(self.bridges),
            "active": sum(1 for b in self.bridges if b.classification == "active"),
            "likely_dead": sum(1 for b in self.bridges if b.classification == "likely_dead"),
            "removable": sum(1 for b in self.bridges if b.classification == "removable"),
            "review": sum(1 for b in self.bridges if b.classification == "review"),
        }


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------


def _detect_dto_reexports(repo_root: Path) -> list[BridgeEntry]:
    """Detect DTO convergence re-exports (r4_contracts → UMRS)."""
    entries = []
    targets = [
        ("umrs/src/umrs/skills/__init__.py", "r4_contracts.skills", "DTO re-export: Skills"),
        ("umrs/src/umrs/recipes/__init__.py", "r4_contracts.recipes", "DTO re-export: Recipes"),
        ("umrs/src/umrs/security/scanner.py", "r4_contracts.security", "DTO re-export: Security"),
    ]

    for rel_path, import_source, desc in targets:
        filepath = repo_root / rel_path
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        if import_source in content:
            # Find the line
            for lineno, line in enumerate(content.splitlines(), 1):
                if import_source in line:
                    entries.append(
                        BridgeEntry(
                            file=rel_path,
                            line=lineno,
                            kind="dto_reexport",
                            description=desc,
                            classification="active",
                            evidence="Converged DTO — UMRS consumers import via this bridge",
                        )
                    )
                    break

    return entries


def _detect_gen1_gen2_bridges(repo_root: Path) -> list[BridgeEntry]:
    """Detect Gen1/Gen2 coexistence bridges (ADR-007)."""
    entries = []
    bridge_files = [
        (
            "umrs/src/umrs/server/durable_job_runtime.py",
            "DurableJobRuntime",
            "Gen1/Gen2 bridge: async loop alongside ZMQ",
        ),
        (
            "umrs/src/umrs/server/job_tracking_handler.py",
            "DurableJobCommandBackend",
            "Gen1/Gen2 bridge: ZMQ→durable job adapter",
        ),
    ]

    for rel_path, class_name, desc in bridge_files:
        filepath = repo_root / rel_path
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        if class_name in content:
            for lineno, line in enumerate(content.splitlines(), 1):
                if f"class {class_name}" in line:
                    entries.append(
                        BridgeEntry(
                            file=rel_path,
                            line=lineno,
                            kind="gen1_gen2_bridge",
                            description=desc,
                            classification="active",
                            evidence="ADR-007: permanent bridge, not tech debt",
                        )
                    )
                    break

    return entries


def _detect_deprecated_modules(repo_root: Path) -> list[BridgeEntry]:
    """Detect modules marked as deprecated."""
    entries = []

    # Known deprecated modules
    deprecated = [
        (
            "gateway/gateway/main.py",
            "Deprecated Python Gateway — replaced by NGINX edge proxy",
            "GATEWAY_ALLOW_LEGACY_PYTHON_EDGE",
        ),
        (
            "umrs/src/umrs/pipeline_sdk/_pipeline_templates_legacy.py",
            "Legacy pipeline templates",
            "_legacy filename convention",
        ),
    ]

    for rel_path, desc, evidence in deprecated:
        filepath = repo_root / rel_path
        if not filepath.exists():
            continue
        entries.append(
            BridgeEntry(
                file=rel_path,
                line=1,
                kind="deprecated_module",
                description=desc,
                classification="review",
                evidence=f"Gated behind: {evidence}",
            )
        )

    return entries


def _detect_backward_compat_aliases(repo_root: Path) -> list[BridgeEntry]:
    """Detect function/variable aliases kept for backward compatibility."""
    entries = []

    # Scan for common alias patterns
    alias_patterns = [
        (
            re.compile(r"^(\w+)\s*=\s*(\w+)\s*#.*(?:backward|compat|legacy|alias)", re.IGNORECASE),
            "backward_compat_alias",
        ),
    ]

    scan_roots = ["umrs/src/umrs", "r4/src/r4", "gateway/gateway"]

    for scan_root in scan_roots:
        root_path = repo_root / scan_root
        if not root_path.exists():
            continue
        for py_file in root_path.rglob("*.py"):
            if not py_file.is_file() or py_file.name.startswith("test_"):
                continue
            try:
                lines = py_file.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = py_file.relative_to(repo_root).as_posix()
            for lineno, line in enumerate(lines, 1):
                for pattern, kind in alias_patterns:
                    m = pattern.match(line.strip())
                    if m:
                        new_name = m.group(1)
                        old_name = m.group(2)
                        # Check if the alias is actually used elsewhere
                        classification = _classify_alias(new_name, py_file, repo_root)
                        entries.append(
                            BridgeEntry(
                                file=rel_path,
                                line=lineno,
                                kind=kind,
                                description=f"Alias: {new_name} = {old_name}",
                                classification=classification,
                                evidence=line.strip(),
                            )
                        )

    return entries


def _classify_alias(name: str, source_file: Path, repo_root: Path) -> str:
    """Check if an alias name is used anywhere else in the codebase."""
    # Quick grep for the alias name
    scan_roots = ["umrs/src", "r4/src", "gateway", "tests", "umrs/tests", "r4/tests"]
    usage_count = 0

    for scan_root in scan_roots:
        root_path = repo_root / scan_root
        if not root_path.exists():
            continue
        for py_file in root_path.rglob("*.py"):
            if py_file == source_file or not py_file.is_file():
                continue
            try:
                content = py_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if name in content:
                usage_count += 1
                if usage_count >= 2:
                    return "active"

    return "likely_dead" if usage_count == 0 else "active"


def _detect_transitional_tools(repo_root: Path) -> list[BridgeEntry]:
    """Detect tools explicitly marked as transitional."""
    entries = []

    # Scan tool files for "TRANSITIONAL" markers
    tools_dir = repo_root / "tools"
    if not tools_dir.exists():
        return entries

    for py_file in tools_dir.glob("*.py"):
        if not py_file.is_file() or py_file.name == "bridge_detector.py":
            continue
        try:
            content = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        if "TRANSITIONAL" in content.upper():
            rel_path = py_file.relative_to(repo_root).as_posix()
            for lineno, line in enumerate(content.splitlines(), 1):
                if "transitional" in line.lower():
                    entries.append(
                        BridgeEntry(
                            file=rel_path,
                            line=lineno,
                            kind="transitional_tool",
                            description=f"Tool marked as transitional: {py_file.name}",
                            classification="review",
                            evidence=line.strip(),
                        )
                    )
                    break

    return entries


def _detect_legacy_code_paths(repo_root: Path) -> list[BridgeEntry]:
    """Detect code paths explicitly marked as legacy."""
    entries = []
    legacy_re = re.compile(
        r"#\s*(?:legacy|backward\s+compat|back-compat|deprecated)\b", re.IGNORECASE
    )

    scan_roots = ["umrs/src/umrs", "r4/src/r4", "gateway/gateway"]

    for scan_root in scan_roots:
        root_path = repo_root / scan_root
        if not root_path.exists():
            continue
        for py_file in root_path.rglob("*.py"):
            if not py_file.is_file() or py_file.name.startswith("test_"):
                continue
            try:
                lines = py_file.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = py_file.relative_to(repo_root).as_posix()
            for lineno, line in enumerate(lines, 1):
                m = legacy_re.search(line)
                if m:
                    entries.append(
                        BridgeEntry(
                            file=rel_path,
                            line=lineno,
                            kind="legacy_code_path",
                            description="Code path marked as legacy",
                            classification="review",
                            evidence=line.strip()[:120],
                        )
                    )

    return entries


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def detect_bridges(repo_root: Path) -> BridgeReport:
    """Run all bridge detection patterns."""
    report = BridgeReport()

    report.bridges.extend(_detect_dto_reexports(repo_root))
    report.bridges.extend(_detect_gen1_gen2_bridges(repo_root))
    report.bridges.extend(_detect_deprecated_modules(repo_root))
    report.bridges.extend(_detect_backward_compat_aliases(repo_root))
    report.bridges.extend(_detect_transitional_tools(repo_root))
    report.bridges.extend(_detect_legacy_code_paths(repo_root))

    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _format_human(report: BridgeReport) -> str:
    lines: list[str] = []
    lines.append("Compatibility Bridge Detection Report")
    lines.append("=" * 60)

    for classification in ("active", "review", "likely_dead", "removable"):
        group = [b for b in report.bridges if b.classification == classification]
        if not group:
            continue
        icon = {"active": "●", "review": "⚠", "likely_dead": "✗", "removable": "⊘"}.get(
            classification, "?"
        )
        lines.append(f"\n{icon} {classification.upper()} ({len(group)}):")
        for b in group:
            lines.append(f"  {b.file}:{b.line}")
            lines.append(f"    Kind:           {b.kind}")
            lines.append(f"    Description:    {b.description}")
            lines.append(f"    Evidence:       {b.evidence}")

    lines.append(f"\nSummary: {report.summary}")
    return "\n".join(lines)


def _format_json(report: BridgeReport) -> str:
    return json.dumps(
        {
            "bridges": [asdict(b) for b in report.bridges],
            "summary": report.summary,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    output_json = "--json" in sys.argv

    report = detect_bridges(repo_root)

    if output_json:
        print(_format_json(report))
    else:
        print(_format_human(report))
        print("\nBridge detector completed")

    return 0  # Advisory only


if __name__ == "__main__":
    raise SystemExit(main())
