"""Enforcement maturity scorer — assess validator health and test coverage.

Provides a dashboard-level view of enforcement infrastructure readiness.
Each validator is scored on:
  * test_coverage — number of unit/integration tests
  * correctness — are there known blind spots or false positives?
  * enforcement_level — hard-fail CI, advisory, or disabled
  * stability — does the validator produce stable, reproducible results?

Usage:
    python tools/enforcement_maturity_scorer.py          # human-readable
    python tools/enforcement_maturity_scorer.py --json   # machine-readable

Enforcement class: INFORMATIONAL (never fails)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration: validator registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatorSpec:
    """Specification for a validator tool."""

    tool: str  # Relative path to tool
    test_file: str  # Relative path to primary test file
    enforcement_level: str  # "hard_fail" | "advisory" | "informational"
    known_blind_spots: int = 0  # Number of documented blind spots
    known_false_positives: int = 0


VALIDATOR_REGISTRY: tuple[ValidatorSpec, ...] = (
    ValidatorSpec(
        tool="tools/import_boundary_validator.py",
        test_file="tests/test_import_boundary_validator.py",
        enforcement_level="hard_fail",
        known_blind_spots=0,
        known_false_positives=0,
    ),
    ValidatorSpec(
        tool="tools/test_import_hygiene_validator.py",
        test_file="tests/test_test_import_hygiene_validator.py",
        enforcement_level="hard_fail",
        known_blind_spots=0,
        known_false_positives=0,
    ),
    ValidatorSpec(
        tool="tools/complexity_baseline.py",
        test_file="tests/test_complexity_baseline.py",
        enforcement_level="hard_fail",
        known_blind_spots=0,
        known_false_positives=0,
    ),
    ValidatorSpec(
        tool="tools/lint_baseline.py",
        test_file="tests/test_lint_baseline.py",
        enforcement_level="hard_fail",
        known_blind_spots=0,
        known_false_positives=0,
    ),
    ValidatorSpec(
        tool="tools/contracts_fence.py",
        test_file="tests/test_contracts_fence.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/dto_equivalence_check.py",
        test_file="tests/test_dto_equivalence.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/schema_version_tracker.py",
        test_file="tests/test_schema_version_tracker.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/stale_name_validator.py",
        test_file="tests/test_stale_name_validator.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/config_drift_validator.py",
        test_file="tests/test_config_drift_validator.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/data_authority_validator.py",
        test_file="tests/test_data_authority_validator.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/runtime_support_matrix.py",
        test_file="tests/test_runtime_support_matrix.py",
        enforcement_level="hard_fail",
    ),
    # D-1 (WP-13): ADR index uniqueness — new hard-fail tier entry.
    ValidatorSpec(
        tool="tools/adr_index_uniqueness_validator.py",
        test_file="tests/test_adr_index_uniqueness_validator.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #2 (ADR-035 rollout order): exemption
        # governance flipped advisory ⇒ hard_fail 2026-09-10 (baseline pinned
        # in the ADR rollout block: 0 errors / 16 warnings / 17 info, soft
        # tiers stay soft; errors fail). Unblocks ADR-035 open item (a).
        tool="tools/exemption_governance_validator.py",
        test_file="tests/test_exemption_governance.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #1 (ADR-035 rollout order): traceability
        # flipped advisory ⇒ hard_fail 2026-09-10 (baseline pinned in the
        # ADR rollout block: OK with 3 warnings, warnings stay WARN-tier).
        tool="tools/traceability_validator.py",
        test_file="tests/test_traceability_validator.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #7sibling (2026-09-11): orphan detector
        # flipped advisory ⇒ hard_fail after the FULL classification
        # pipeline — detector fixes (2 bugs) + dynamic-family whitelists +
        # leg-1+2 zero-consumer deletions landed the census at ZERO.
        # Live baseline: 0 findings across ~1000 modules.
        tool="tools/orphan_module_detector.py",
        test_file="tests/test_orphan_module_detector.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #7 (ADR-035 rollout order, noisiest-last;
        # cluster split recorded in the ADR): ADR linkage flipped
        # advisory ⇒ hard_fail 2026-09-10 (baseline: 51 ADRs scanned,
        # 0 errors / 0 warnings / 0 info, 15 tests, grade A).
        # orphan_module_detector STAYS advisory — classification wave needed
        # for 83 findings incl. dynamic-load false positives.
        tool="tools/adr_linkage_validator.py",
        test_file="tests/test_drift_detectors.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #5a (ADR-035 rollout order, transport/bridge
        # cluster — split decision recorded in the ADR): bridge_detector
        # flipped advisory ⇒ hard_fail 2026-09-10 (baseline: active 6 /
        # likely_dead 0 / removable 0 / review 30; 17 tests, grade A band).
        tool="tools/bridge_detector.py",
        test_file="tests/test_drift_detectors.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #5c (ADR-035 rollout order): dead compat
        # bridge flipped advisory ⇒ hard_fail 2026-09-10 AFTER the detector
        # redesign WP-13 deferred it for (category-error fix: compat-intent
        # classes only). Baseline: 2 active bridges, 0 dead findings,
        # 22 tests, grade A.
        tool="tools/dead_compat_bridge_validator.py",
        test_file="tests/test_dead_compat_bridge.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #6 (ADR-035 rollout order): metrics
        # coverage flipped advisory ⇒ hard_fail 2026-09-10 after the
        # detection upgrade (lazy wrappers _counter/_histogram/_gauge as
        # metric-constructor aliases + vllm:/litellm: exporter prefixes);
        # baseline: dashboards 24 refs / code 70 defs, 0 MISSING
        # (exit 0 non-strict). 21 tests, grade A.
        tool="tools/check_metrics_coverage.py",
        test_file="tests/test_check_metrics_coverage.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/cleanup_readiness_report.py",
        test_file="tests/test_cleanup_readiness_report.py",
        enforcement_level="informational",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #5b (ADR-035 rollout order, transport/bridge
        # cluster — split decision recorded in the ADR): transport boundary
        # flipped advisory ⇒ hard_fail 2026-09-10 (baseline: 0 violations /
        # 578 transport sites / 5 approved ZMQ modules; 15 tests, grade A).
        tool="tools/transport_boundary_audit.py",
        test_file="tests/test_topology_tools.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        tool="tools/compat_retirement_tracker.py",
        test_file="tests/test_topology_tools.py",
        enforcement_level="advisory",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #3 (ADR-035 rollout order): security
        # posture flipped advisory ⇒ hard_fail 2026-09-10 (baseline pinned
        # in the ADR rollout block: 0 critical / 8 warnings unsalted-hash /
        # 7 info MD5-checksum; non-critical severities stay soft).
        tool="tools/security_compat_scanner.py",
        # Registry fix 2026-09-10: tests live in the shared topology file
        # (class TestSecurityCompatScanner — counted via the shared-file
        # counter); the previous row named a file that never existed
        # (registry drift from 22f2380f; surfaced by the promotion flip
        # when its score re-graded D-low).
        test_file="tests/test_topology_tools.py",
        enforcement_level="hard_fail",
    ),
    ValidatorSpec(
        # WP-13 round 2 promotion #4 (ADR-035 rollout order): tool-route
        # liveness flipped advisory ⇒ hard_fail 2026-09-10 AFTER its grade-B
        # baseline sweep — coverage 7→16 tests (grade A band), exit contract
        # moved to findings-fail, 0-finding live baseline pinned.
        tool="tools/tool_route_liveness.py",
        test_file="tests/test_tool_route_liveness.py",
        enforcement_level="hard_fail",
    ),
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatorScore:
    """Maturity score for a single validator."""

    tool: str
    test_count: int
    enforcement_level: str
    maturity_grade: str  # "A" | "B" | "C" | "D"
    confidence: str  # "high" | "medium" | "low"
    notes: str


@dataclass(frozen=True)
class ExemptionAgeRow:
    """One exemption's age measurement (from metadata or git blame)."""

    file: str
    line: int
    kind: str
    import_target: str
    age_days: int
    source: str  # "metadata" | "git_blame" | "file_mtime"
    flagged: bool  # True when age_days >= _EXEMPTION_REVIEW_AGE_DAYS


@dataclass
class MaturityReport:
    """Overall enforcement maturity report."""

    scores: list[ValidatorScore] = field(default_factory=list)
    total_tests: int = 0
    exemption_ages: list[ExemptionAgeRow] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, object]:
        return {
            "total_validators": len(self.scores),
            "total_tests": self.total_tests,
            **_grade_counts(self.scores),
            **_level_counts(self.scores),
            "exemptions_total": len(self.exemption_ages),
            "exemptions_flagged_for_review": sum(1 for r in self.exemption_ages if r.flagged),
        }


def _grade_counts(scores: list[ValidatorScore]) -> dict[str, int]:
    """Per-grade validator counts (extracted for the complexity gate)."""
    return {
        f"grade_{letter}": sum(1 for s in scores if s.maturity_grade == letter) for letter in "ABCD"
    }


def _level_counts(scores: list[ValidatorScore]) -> dict[str, int]:
    """Per-enforcement-level counts (extracted for the complexity gate)."""
    return {
        "hard_fail_count": sum(1 for s in scores if s.enforcement_level == "hard_fail"),
        "advisory_count": sum(1 for s in scores if s.enforcement_level == "advisory"),
    }


# ---------------------------------------------------------------------------
# Test counting
# ---------------------------------------------------------------------------


def _count_tests_in_file(test_file: Path, tool_name: str) -> int:
    """Count test functions/methods relevant to a validator in a test file."""
    if not test_file.exists():
        return 0

    try:
        content = test_file.read_text()
    except (OSError, UnicodeDecodeError):
        return 0

    # Count def test_ lines
    count = len(re.findall(r"^\s*def test_", content, re.MULTILINE))

    # If the test file is shared (e.g., test_drift_detectors.py), try to
    # count only tests in the relevant class
    if _is_shared_test_file(test_file):
        count = _count_class_tests(content, tool_name)

    return count


def _is_shared_test_file(test_file: Path) -> bool:
    """Check if a test file is shared between multiple validators."""
    return test_file.name in ("test_drift_detectors.py", "test_topology_tools.py")


def _count_class_tests(content: str, tool_name: str) -> int:
    """Count tests in classes relevant to a specific tool."""
    # Map tool names to class name patterns
    class_patterns = {
        "adr_linkage_validator": r"class TestADRLinkage",
        "bridge_detector": r"class TestBridgeDetector",
        "transport_boundary_audit": r"class TestTransportBoundaryAudit",
        "compat_retirement_tracker": r"class TestCompatRetirementTracker",
        "security_compat_scanner": r"class TestSecurityCompatScanner",
    }

    stem = Path(tool_name).stem
    pattern = class_patterns.get(stem)
    if not pattern:
        return len(re.findall(r"^\s*def test_", content, re.MULTILINE))

    # Find the class and count tests within it
    count = 0
    in_class = False
    for line in content.splitlines():
        if re.search(pattern, line):
            in_class = True
            continue
        if in_class and re.match(r"^class ", line):
            in_class = _still_target_class(line, pattern, stem)
        if in_class and re.match(r"\s+def test_", line):
            count += 1

    return count


def _still_target_class(line: str, pattern: str, stem: str) -> bool:
    """Decide whether a new 'class …' line still belongs to this tool's
    family of test classes (extracted from _count_class_tests)."""
    if re.search(pattern.replace("class Test", "class Test.*"), line):
        return True
    if stem == "adr_linkage_validator" and "ADR" in line:
        return True
    return stem == "bridge_detector" and "Bridge" in line


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _band_grade(test_count: int, bands: tuple[tuple[int, str], ...]) -> str:
    """First band whose threshold test_count clears; else 'D'."""
    for minimum, grade in bands:
        if test_count >= minimum:
            return grade
    return "D"


def _confidence(grade: str, blind_spots: int) -> str:
    """Confidence band from grade + known blind spots."""
    if grade in ("A", "B") and blind_spots == 0:
        return "high"
    if grade in ("C", "D") or blind_spots > 1:
        return "low"
    return "medium"


def _grade_validator(spec: ValidatorSpec, test_count: int) -> tuple[str, str, str]:
    """Assign maturity grade and confidence.

    Returns (grade, confidence, notes).
    """
    notes_parts: list[str] = []

    # Grade based on test count and enforcement level
    if spec.enforcement_level == "hard_fail":
        grade = _band_grade(test_count, bands=((10, "A"), (4, "B"), (1, "C")))
    else:
        # Advisory/informational: slightly lower bar
        grade = _band_grade(test_count, bands=((15, "A"), (6, "B"), (1, "C")))

    # Confidence based on blind spots and grade
    if spec.known_blind_spots > 0:
        notes_parts.append(f"{spec.known_blind_spots} known blind spot(s)")
    if spec.known_false_positives > 0:
        notes_parts.append(f"{spec.known_false_positives} known false positive(s)")

    confidence = _confidence(grade, spec.known_blind_spots)

    notes = "; ".join(notes_parts) if notes_parts else "No known issues"
    return grade, confidence, notes


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


_EXEMPTION_REVIEW_AGE_DAYS = 90  # Flag exemptions older than this for review


def _git_blame_unix_time(file_path: Path, line_no: int, repo_root: Path) -> int | None:
    """Get the unix author-time of a specific blame line. Returns None on failure."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "blame",
                "--porcelain",
                "-L",
                f"{line_no},{line_no}",
                "--",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("author-time "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _file_mtime_unix_time(file_path: Path) -> int | None:
    try:
        return int(file_path.stat().st_mtime)
    except OSError:
        return None


def _exemption_age_days(
    added_iso: str, file_path: Path, line_no: int, repo_root: Path, now: datetime
) -> tuple[int, str]:
    """Return (age_days, source). Tries metadata → git blame → file mtime."""

    def _age_from_unix(ts: int) -> int:
        return max(0, int((now.timestamp() - ts) / 86400))

    # 1. Metadata ``added=YYYY-MM-DD`` — already tracked accurately.
    if added_iso:
        try:
            introduced = datetime.strptime(added_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int((now - introduced).total_seconds() // 86400), "metadata"
        except ValueError:
            pass  # Fall through to git blame

    # 2. Git blame of the exact exemption line.
    blame_ts = _git_blame_unix_time(file_path, line_no, repo_root)
    if blame_ts is not None:
        return _age_from_unix(blame_ts), "git_blame"

    # 3. File mtime (last-resort).
    mtime_ts = _file_mtime_unix_time(file_path)
    if mtime_ts is not None:
        return _age_from_unix(mtime_ts), "file_mtime"

    return -1, "unknown"


def _score_exemption_ages(repo_root: Path, now: datetime | None = None) -> list[ExemptionAgeRow]:
    """Load exemptions via the governance validator and compute each one's age."""
    if now is None:
        now = datetime.now(timezone.utc)

    tools_dir = Path(__file__).resolve().parent
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        from exemption_governance_validator import audit_exemptions  # noqa: PLC0415
    except ImportError:
        return []

    try:
        report = audit_exemptions(repo_root)
    except Exception:  # broad — scorer must never crash
        return []

    rows: list[ExemptionAgeRow] = []
    for entry in report.exemptions:
        file_path = repo_root / entry.file
        if not file_path.exists():
            continue
        age_days, source = _exemption_age_days(
            added_iso=entry.added,
            file_path=file_path,
            line_no=entry.line,
            repo_root=repo_root,
            now=now,
        )
        if age_days < 0:
            continue
        rows.append(
            ExemptionAgeRow(
                file=entry.file,
                line=entry.line,
                kind=entry.kind,
                import_target=entry.import_target,
                age_days=age_days,
                source=source,
                flagged=age_days >= _EXEMPTION_REVIEW_AGE_DAYS,
            )
        )
    rows.sort(key=lambda r: (-r.age_days, r.file, r.line))
    return rows


def score_enforcement(repo_root: Path) -> MaturityReport:
    """Score all validators and produce maturity report."""
    report = MaturityReport()

    for spec in VALIDATOR_REGISTRY:
        test_file = repo_root / spec.test_file
        test_count = _count_tests_in_file(test_file, spec.tool)
        report.total_tests += test_count

        grade, confidence, notes = _grade_validator(spec, test_count)

        report.scores.append(
            ValidatorScore(
                tool=spec.tool,
                test_count=test_count,
                enforcement_level=spec.enforcement_level,
                maturity_grade=grade,
                confidence=confidence,
                notes=notes,
            )
        )

    # Exemption age tracking — informational, never fails.
    report.exemption_ages = _score_exemption_ages(repo_root)

    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _format_human(report: MaturityReport) -> str:
    lines: list[str] = []
    lines.append("Enforcement Maturity Scorecard")
    lines.append("=" * 70)
    lines.append(f"{'Validator':<40} {'Level':<10} {'Tests':<6} {'Grade':<6} {'Conf.':<8}")
    lines.append("-" * 70)

    for s in report.scores:
        tool_name = Path(s.tool).stem
        lines.append(
            f"{tool_name:<40} {s.enforcement_level:<10} {s.test_count:<6} "
            f"{s.maturity_grade:<6} {s.confidence:<8}"
        )

    lines.append("-" * 70)
    summary = report.summary
    lines.append(
        f"Total: {summary['total_validators']} validators, "
        f"{report.total_tests} tests | "
        f"A:{summary['grade_A']} B:{summary['grade_B']} "
        f"C:{summary['grade_C']} D:{summary['grade_D']}"
    )

    if report.exemption_ages:
        lines.append("")
        flagged = [r for r in report.exemption_ages if r.flagged]
        lines.append(
            f"EXEMPTION AGE TRACKING ({len(report.exemption_ages)} exemptions, "
            f"{len(flagged)} ≥ {_EXEMPTION_REVIEW_AGE_DAYS}d for review):"
        )
        for row in flagged[:10]:
            lines.append(
                f"  ⚠ {row.age_days:>4}d {row.file}:{row.line} "
                f"[{row.source}] {row.kind}({row.import_target})"
            )
        if not flagged:
            lines.append("  none past review age")
    return "\n".join(lines)


def _format_json(report: MaturityReport) -> str:
    return json.dumps(
        {
            "scores": [asdict(s) for s in report.scores],
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

    report = score_enforcement(repo_root)

    if output_json:
        print(_format_json(report))
    else:
        print(_format_human(report))

    return 0  # Always informational


if __name__ == "__main__":
    raise SystemExit(main())
