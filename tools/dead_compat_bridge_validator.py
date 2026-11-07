"""Dead compat bridge validator — find compatibility bridges whose old API is unused.

Detects Python files that act as compatibility shims/wrappers (re-exporting from
old module paths, forwarding to legacy APIs, or providing deprecated entry points)
and checks whether the old API they protect is still referenced anywhere in the
repository.  When the old side has zero consumers the bridge is dead.

Patterns detected (compat-intent classes ONLY — design wave 2026-09-10: the
first version's every-ImportFrom "re-export bridge" class was a category
error at platform scale; 688/688 live findings were that class on ACTIVE
modules, so that class was removed):
  * __getattr__ forwarding to an old module path
  * re-export-only __init__.py files
  * Files named *_compat.py or in compat/parent directories
  * Deprecation-marker modules (DeprecationWarning / __deprecated__)
Reference matching counts the full dotted path AND trailing-stem
from/import + string-dispatch shapes.
  * Legacy __init__.py that only re-exports from a new submodule

Usage:
    python tools/dead_compat_bridge_validator.py          # human-readable
    python tools/dead_compat_bridge_validator.py --json   # machine-readable

Enforcement class: ADVISORY (no hard failures)
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Source trees to scan for bridge candidates
SOURCE_ROOTS: tuple[str, ...] = (
    "umrs/src",
    "r4/src",
    "gateway",
    "mcp-servers/src",
    "a2l/src",
)

# Source trees to scan for references (broader, includes tests)
REF_SCAN_ROOTS: tuple[str, ...] = (
    "umrs/src",
    "umrs/tests",
    "r4/src",
    "r4/tests",
    "gateway",
    "gateway/tests",
    "mcp-servers/src",
    "mcp-servers/tests",
    "a2l/src",
    "a2l/tests",
    "tools",
)

# Files excluded from bridge candidacy
BRIDGE_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "**/test_*.py",
    "**/tests/**",
    "**/conftest.py",
    "**/__pycache__/**",
)

# Bridge patterns to detect (functions referenced by name, evaluated at call time)
BRIDGE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("__getattr__", "__getattr__ compat forwarding"),
    ("re-export", "re-export bridge"),
    ("reexport_init", "re-export only __init__.py"),
)

# Known bridges that are intentionally active (false positive suppression)
KNOWN_ACTIVE_BRIDGES: frozenset[str] = frozenset(
    {
        # Gateway backward-compat aliases
        "gateway/gateway/auth.py",
        "gateway/gateway/main.py",
        # a2l dwell-managed rows (ADR-034; WP-14 round 2 removal gate is 7
        # meaningful reconcile days — until then both rows are LIVE:
        #   * a2l/science_lineage.py carries science-lineage rule helpers
        #     consumed directly by r4/tests/test_e2e_autonomous_loop.py,
        #     r4/tests/test_integration_week3.py,
        #     r4/tests/test_working_research_state.py (test-file consumers
        #     are invisible to this validator's source roots);
        #   * a2l/main.py is the standalone a2l service entrypoint — its
        #     .api.events import is the active router path.
        # Both are deleted along with the a2l tree at WP-14 round 2, not
        # piecewise during the dwell.
        "a2l/src/a2l/science_lineage.py",
        "a2l/src/a2l/main.py",
        # umrs.tui — RETIRED v1 Textual TUI package held until its cleanup
        # cycle (per package docstring: kept for test coverage + the
        # Dashboard compat shim; removal is the cleanup-cycle's call, not
        # piecewise deletion here).
        "umrs/src/umrs/tui/__init__.py",
    }
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompatBridge:
    """A compatibility bridge candidate."""

    file: str  # Relative path to bridge file
    kind: str  # Pattern description
    old_path: str  # The old module/API path being bridged
    evidence: str  # The specific line or pattern matched


@dataclass(frozen=True)
class DeadBridgeFinding:
    """A bridge whose old API is no longer referenced."""

    bridge: CompatBridge
    reason: str  # Why it is considered dead
    suggestion: str  # What to do about it


@dataclass
class DeadBridgeReport:
    """Complete dead compat bridge report."""

    findings: list[DeadBridgeFinding] = field(default_factory=list)
    total_bridges_scanned: int = 0
    active_bridges: int = 0
    dead_bridges: int = 0

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total_bridges_scanned": self.total_bridges_scanned,
            "active_bridges": self.active_bridges,
            "dead_bridges": self.dead_bridges,
        }


# ---------------------------------------------------------------------------
# Bridge detection
# ---------------------------------------------------------------------------


def _collect_source_files(repo_root: Path, roots: tuple[str, ...]) -> list[Path]:
    """Collect all Python source files under the given roots."""
    files: list[Path] = []
    for root in roots:
        root_path = repo_root / root
        if not root_path.exists():
            continue
        for p in root_path.rglob("*.py"):
            _rel = p.relative_to(repo_root).as_posix()
            excluded = any(p.match(pat) for pat in BRIDGE_EXCLUDE_PATTERNS)
            if not excluded:
                files.append(p)
    return files


def _extract_import_sources(filepath: Path) -> list[tuple[str, str]]:
    """Extract (module, names) from 'from module import X' statements."""
    results: list[tuple[str, str]] = []
    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(alias.name for alias in (node.names or []))
            results.append((module, names))
    return results


def _extract_getattr_targets(filepath: Path) -> list[tuple[str, str]]:
    """Extract __getattr__ bodies that forward to other modules."""
    results: list[tuple[str, str]] = []
    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    for match in re.finditer(
        r"__getattr__.*?return\s+(?:importlib\.import_module\s*\(\s*[\"'])([\w.]+)[\"']",
        content,
        re.DOTALL,
    ):
        results.append((match.group(1), "getattr"))

    for match in re.finditer(
        r"__getattr__.*?from\s+([\"'])([\w.]+)\1.*?import_module",
        content,
        re.DOTALL,
    ):
        results.append((match.group(2), "getattr"))

    return results


def _is_reexport_only_init(filepath: Path) -> bool:
    """Check if __init__.py contains only imports (no real logic)."""
    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return False

    # Strip comments and blank lines
    lines = [
        line for line in content.splitlines() if line.strip() and not line.strip().startswith("#")
    ]

    if not lines:
        return False

    # Check if every non-comment line is an import
    import_re = re.compile(r"^\s*(from\s+|import\s+)")
    return all(import_re.match(line) for line in lines)


_CLASS_PREFIXES = ("umrs/src/", "r4/src/", "gateway/", "mcp-servers/src/")


def _rel_to_module(rel: str) -> str:
    """Dotted module path from a repo-relative source path."""
    mod = rel
    for prefix in _CLASS_PREFIXES:
        if mod.startswith(prefix):
            mod = mod[len(prefix) :]
            break
    return mod[:-3].replace("/", ".") if mod.endswith(".py") else mod.replace("/", ".")


_DEPRECATION_RE = re.compile(r"DeprecationWarning|__deprecated__")


def _is_compat_named(filepath: Path, rel: str) -> bool:
    """Compat-intent filename/module: compat in stem, parent, or package."""
    stem = filepath.stem.lower()
    if "compat" in stem:
        return True
    parent = filepath.parent.name.lower()
    if "compat" in parent:
        return True
    return filepath.parent.parent.name.lower() == "compat" if filepath.parent.parent.name else False


def _has_deprecation_marker(filepath: Path) -> bool:
    """Deprecated-surface intent: DeprecationWarning / __deprecated__ token."""
    try:
        return bool(_DEPRECATION_RE.search(filepath.read_text()))
    except (OSError, UnicodeDecodeError):
        return False


def _collect_file_bridges(filepath: Path, rel: str, seen: set) -> list[CompatBridge]:
    """Bridges detected in ONE file (compat-intent classes ONLY — design wave
    2026-09-10: the previous every-ImportFrom "re-export bridge" class was a
    category error at platform scale; 688 of 688 findings were that class).
    Classes: __getattr__ forwarding, re-export-only __init__.py, compat-named
    modules, deprecation-marker modules. Dedup against *seen*."""
    found: list[CompatBridge] = []

    # Check __getattr__ patterns
    getattr_targets = _extract_getattr_targets(filepath)
    for target, _kind in getattr_targets:
        key = (rel, target)
        if key not in seen:
            seen.add(key)
            found.append(
                CompatBridge(
                    file=rel,
                    kind="getattr forwarding",
                    old_path=target,
                    evidence=f"__getattr__ forwards to {target}",
                )
            )

    # Check re-export-only __init__.py
    if filepath.name == "__init__.py" and _is_reexport_only_init(filepath):
        sources = _extract_import_sources(filepath)
        for module, _names in sources:
            # Use a distinct key to avoid collision with generic re-export check
            key = (rel, module, "init")
            if key not in seen:
                seen.add(key)
                found.append(
                    CompatBridge(
                        file=rel,
                        kind="re-export only __init__.py",
                        old_path=module,
                        evidence=f"__init__.py re-exports from {module}",
                    )
                )

    # self-intent classes (compat-named module + deprecation marker)
    found.extend(_self_intent_bridges(filepath, rel, seen))
    return found


def _self_intent_bridges(filepath: Path, rel: str, seen: set) -> list[CompatBridge]:
    """Compat-named module and deprecation-marker classes (filename/file
    tokens — module path is the file's own)."""
    found: list[CompatBridge] = []
    module_path = _rel_to_module(rel)

    if _is_compat_named(filepath, rel):
        key = (rel, module_path, "compat-name")
        if key not in seen:
            seen.add(key)
            found.append(
                CompatBridge(
                    file=rel,
                    kind="compat-named module",
                    old_path=module_path,
                    evidence=f"compat-intent filename/path: {rel}",
                )
            )

    if _has_deprecation_marker(filepath):
        key = (rel, module_path, "deprecation")
        if key not in seen:
            seen.add(key)
            found.append(
                CompatBridge(
                    file=rel,
                    kind="deprecation marker",
                    old_path=module_path,
                    evidence=f"DeprecationWarning/__deprecated__ in {rel}",
                )
            )
    return found


def find_compat_bridges(repo_root: Path) -> list[CompatBridge]:
    """Find all compatibility bridges in the source trees."""
    bridges: list[CompatBridge] = []
    seen: set[tuple[str, str]] = set()  # (file, old_path) dedup

    for filepath in _collect_source_files(repo_root, SOURCE_ROOTS):
        rel = filepath.relative_to(repo_root).as_posix()
        # Skip known active bridges
        if rel in KNOWN_ACTIVE_BRIDGES:
            continue
        bridges.extend(_collect_file_bridges(filepath, rel, seen))

    return bridges


# ---------------------------------------------------------------------------
# Dead detection
# ---------------------------------------------------------------------------


def _old_path_referenced(repo_root: Path, old_path: str, exclude_file: str | None = None) -> bool:
    """Check if an old module path is still referenced anywhere in the codebase.

    Args:
        repo_root: repository root path
        old_path: the old module path to search for
        exclude_file: optional bridge file to exclude from search (avoids self-references)

    Reference shapes counted (design wave): the full dotted path in any
    file, OR the trailing module stem in from/import/string-dispatch
    positions (``from .openai_compat import``, ``("._cli.init", ...)``) —
    the package-relative and string-dispatch consumers the first version
    was blind to.
    """
    ref_files = _collect_source_files(repo_root, REF_SCAN_ROOTS)

    # Build a search pattern: match the old path as a module reference
    # This handles: import old_path.X, from old_path import Y, etc.
    search_pattern = re.compile(
        r"(?:^|\s|['\"])" + re.escape(old_path) + r"(?:\s|['\".,;:]|$)",
        re.MULTILINE,
    )
    stem = old_path.rsplit(".", 1)[-1]
    stem_pattern = re.compile(
        r"(?:from\s+[\w.]*\." + re.escape(stem) + r"\s+import"
        r"|from\s+[\w.]*" + re.escape(stem) + r"\s+import"
        r"|import\s+[\w.]*\." + re.escape(stem) + r"\b"
        r"|[\w.]*\." + re.escape(stem) + r"['\"])"
    )

    for filepath in ref_files:
        # Skip the bridge file itself (it's the source of the re-export)
        if exclude_file and filepath.relative_to(repo_root).as_posix() == exclude_file:
            continue

        try:
            content = filepath.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        if search_pattern.search(content) or _stem_referenced(stem_pattern, stem, content):
            return True
    return False


def _stem_referenced(stem_pattern: re.Pattern[str], stem: str, content: str) -> bool:
    """Trailing-stem match (import forms + string dispatch literals)."""
    return bool(stem_pattern.search(content))
    return False


def find_dead_compat_bridges(repo_root: Path) -> list[DeadBridgeFinding]:
    """Find compatibility bridges whose old API is no longer referenced."""
    bridges = find_compat_bridges(repo_root)
    findings: list[DeadBridgeFinding] = []

    for bridge in bridges:
        referenced = _old_path_referenced(repo_root, bridge.old_path, bridge.file)
        if not referenced:
            findings.append(
                DeadBridgeFinding(
                    bridge=bridge,
                    reason=f"No references to old path '{bridge.old_path}' found in codebase",
                    suggestion=f"Consider removing dead compat bridge: {bridge.file} (bridges {bridge.old_path})",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """Run dead compat bridge detection and print results."""
    repo_root = Path.cwd()
    findings = find_dead_compat_bridges(repo_root)

    bridges = find_compat_bridges(repo_root)
    report = DeadBridgeReport(
        findings=findings,
        total_bridges_scanned=len(bridges),
        active_bridges=len(bridges) - len(findings),
        dead_bridges=len(findings),
    )

    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "report": report.summary,
                    "findings": [
                        {
                            "bridge": asdict(f.bridge),
                            "reason": f.reason,
                            "suggestion": f.suggestion,
                        }
                        for f in findings
                    ],
                },
                indent=2,
            )
        )
        return 1 if findings else 0
    else:
        print("Dead Compat Bridge Validator")
        print(f"{'=' * 40}")
        print(f"Total bridges scanned: {report.total_bridges_scanned}")
        print(f"Active bridges:        {report.active_bridges}")
        print(f"Dead bridges:          {report.dead_bridges}")
        print()

        if findings:
            print("Dead bridges found:")
            print("-" * 40)
            for f in findings:
                print(f"  {f.bridge.file}")
                print(f"    kind:  {f.bridge.kind}")
                print(f"    old:   {f.bridge.old_path}")
                print(f"    reason: {f.reason}")
                print(f"    fix:   {f.suggestion}")
                print()
            return 1
        else:
            print("No dead compat bridges found. All bridges are active.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
