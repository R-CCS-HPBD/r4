"""Orphan module detector — find modules with zero inbound imports.

Scans UMRS, r4, Gateway, and mcp-servers source trees for Python
modules that are never imported by any other module in the repository.
These are candidates for removal, consolidation, or documentation.

Exclusion rules (to avoid false positives):
  * Entrypoints (console_scripts, __main__.py)
  * Test files and test fixtures
  * __init__.py files (package markers / re-export facades)
  * conftest.py files
  * Files explicitly loaded via importlib.import_module / dynamic imports
  * Plugin discovery targets
  * Migration scripts
  * Demo / sample scripts

Usage:
    python tools/orphan_module_detector.py          # human-readable
    python tools/orphan_module_detector.py --json   # machine-readable

Enforcement class: ADVISORY (no hard failures)
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Source trees to scan for orphan candidates
SOURCE_ROOTS: tuple[str, ...] = (
    "umrs/src/umrs",
    "r4/src/r4",
    "gateway/gateway",
    "mcp-servers/src/r4_mcp",
)

# Source trees to scan for inbound imports (broader)
IMPORT_SCAN_ROOTS: tuple[str, ...] = (
    "umrs/src",
    "r4/src",
    "gateway",
    "mcp-servers/src",
    "tests",
    "umrs/tests",
    "r4/tests",
    "gateway/tests",
    "mcp-servers/tests",
    "tools",
)

# Files that are known entrypoints (never imported, always invoked directly)
KNOWN_ENTRYPOINTS: frozenset[str] = frozenset(
    {
        # console_scripts from pyproject.toml
        "r4/src/r4/cli.py",
        "umrs/src/umrs/client/cli.py",
        "umrs/src/umrs/server/main.py",
        "gateway/gateway/main.py",
        "mcp-servers/src/r4_mcp/cli.py",
        # __main__.py entrypoints
        "umrs/src/umrs/cli/__main__.py",
        "umrs/src/umrs/plugins/plugin_runner/__main__.py",
        "umrs/src/umrs/migrations/__main__.py",
    }
)

# Glob patterns to exclude from orphan candidacy
EXCLUDE_PATTERNS: tuple[str, ...] = (
    "**/test_*.py",
    "**/tests/**",
    "**/*_test.py",
    "**/conftest.py",
    "**/__init__.py",
    "**/__main__.py",
    "**/migrations/*.py",
    "**/demos/**",
    "**/samples/**",
)

# Modules known to be loaded dynamically (plugin system, adapters), or kept
# with documented runtime/owner evidence (wave 2, 2026-09-11).
# A "# FROZEN: docs/development/dormant-modules.md#<section>" line directly
# above a pin (here or in PINNED_PUBLIC_SURFACE) marks a KEEP-FROZEN module
# and names the registry section holding its row (repo-cleanup C-0,
# 2026-10-16). Deleting the module strikes the row and the pin together;
# tests/test_orphan_module_detector.py checks the two stay in step.
DYNAMIC_IMPORT_MODULES: frozenset[str] = frozenset(
    {
        "umrs.vision_service.adapters",
        "umrs.plugins.plugin_runner.entrypoint",
        "umrs.server.workflow_runner",
        "umrs.server.step_executor",
        # bentofile.yaml: service: "umrs.vision_service.service:VisionModelService"
        "umrs.vision_service.service",
        # Ansible CMD: uvicorn umrs.api.mcp_service:app (Dockerfile.mcp-frontend:49)
        "umrs.api.mcp_service",
        # deploy/k8s-worker-airgap-setup.sh calls it directly
        "umrs.slurm.k8s_bootstrap",
        # declared Phase-2 planned work (CLEANUP plan row: format conversion)
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.model_registry.conversion",
        # in-flight conservation/reconciler families (owner-adjacent wave)
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.data_catalog.backfill",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.data_catalog.reconcile",
        # Phase-2 RC pending-work etchings (an in-flight concurrent session
        # owns the wave — review expiry 2026-Q1):
        # (umrs.api.knowledge_model row removed 2026-09-15: its router was
        #  unmounted and is now deleted outright by ADR-053 — a pin for a file
        #  that does not exist is a lie.)
        "umrs.artifact_registry.auto_register",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.client.discovery",
        # FROZEN: docs/development/dormant-modules.md#umrs-knowledge
        "umrs.knowledge.node_embedding_backfill",
        # FROZEN: docs/development/dormant-modules.md#umrs-knowledge
        "umrs.knowledge.summary",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.model_registry.certification",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.orchestrator.slurm_vision_script",
        # FROZEN: docs/development/dormant-modules.md#umrs-modules-the-non-goals-protect
        "umrs.scheduler.triggers",
        # FROZEN: docs/development/dormant-modules.md#umrs-re-exports-of-r4-contracts
        "umrs.security.scanner",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.security.retention",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.shared.bootstrap",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.vision_service.protocol",
        # FROZEN: docs/development/dormant-modules.md#umrs-modules-the-non-goals-protect
        "umrs.voice.whisper_adapter",
        "umrs.workflow.persistence",
        # FROZEN: docs/development/dormant-modules.md#umrs-ops-and-contract-modules
        "umrs.workflow.models",
        "umrs.admin.create_users",
        # FROZEN: docs/development/dormant-modules.md#umrs-modules-the-non-goals-protect
        "umrs.pipeline_sdk.connectors.pgvector",
        # FROZEN: docs/development/dormant-modules.md#umrs-modules-the-non-goals-protect
        "umrs.pipeline_sdk.connectors.s3",
        # FROZEN: docs/development/dormant-modules.md#umrs-modules-the-non-goals-protect
        "umrs.pipeline_sdk.connectors.bentoml",
        # WP-11 r3 (umrs 672581c4): compose service exec —
        # command: python -u /opt/rivault/sts_renewal.py (sts-renewal profile,
        # serve-model.yaml). File-mounted run, no import site.
        "umrs.mini-rivault-llm.sts_renewal",
        # umrs 684ce031 (2026-11-05): serve-model.slurm mounts it at
        # /opt/rivault/gb10_reclaim.py and runs `python3` on it before
        # `vllm serve` / `ray start` (GB10 pre-start reclaim). No import site.
        "umrs.mini-rivault-llm.gb10_reclaim",
    }
)

# Modules sanctioned as importable public/package surfaces — pinned by
# string-keyed importability smoke tests. They are flagged by the plain
# import-counter even though the package list declares them supported;
# the pins carry the pinning test as evidence (orphan wave 2026-09-10).
# Deleting any of these = retire the row out of the pinning test's list
# in the same wave (or the smoke test fails loudly).
PINNED_PUBLIC_SURFACE: frozenset[str] = frozenset(
    {
        # r4/tests/test_package_imports.py MODULES list
        # FROZEN: docs/development/dormant-modules.md#r4-test-only-and-published-surfaces
        "r4.observability",
        # tests/test_batch3_cor003_cor004_memb5_memc3.py pre-load smoke
        "r4_mcp.runners.sandbox_enforcer",
        # r4/tests/test_tui_backend_audit_coverage.py::api_modules list
        # (UMRS API route-module importability map; eight rows retired
        # 2026-09-15 with the ADR-053 router retirement — kept rows below
        # are the surviving mounted families).
        "umrs.api.context",
        "umrs.api.context_schema",
        # FROZEN: docs/development/dormant-modules.md#umrs-shipped-but-unwired
        "umrs.api.scopes",
    }
)

# Packages whose children are loaded via dynamic dispatch patterns
# (e.g., import_module(f"r4._cli.{name}") or similar)
DYNAMIC_DISPATCH_PACKAGES: frozenset[str] = frozenset(
    {
        "r4._cli",  # CLI subcommands loaded via import_module(f"r4._cli.{module_name}")
        "r4.providers",  # Provider modules loaded by name
        "r4.integrations",  # Integration modules with __import__ availability checks
        # umrs/jobs/__init__.py lazy-export map (string-keyed module paths)
        "umrs.jobs",
        # manifest/name-keyed adapter leaves (deploy yaml + ADR-023/029 referenced)
        "r4_mcp.stdio_adapters",
        # vision adapters: filesystem auto-discovery by filename stem
        # (umrs/src/umrs/vision_service/adapters/__init__.py docs)
        "umrs.vision_service.default_adapters",
        # workflow.json entrypoint dotted-path loading
        # ("umrs.pipeline_sdk.templates.<X>_step:<Y>Class")
        "umrs.pipeline_sdk.templates",
        "r4.agent",  # Agent modules loaded via dispatch
        "r4.tui",  # TUI submodules loaded via dispatch
        "umrs.connectors",  # Connector modules loaded by name
    }
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrphanCandidate:
    """A module that appears to have no inbound imports."""

    file: str  # Relative path
    module: str  # Dotted module path
    confidence: str  # "high" | "medium" | "low"
    reason: str  # Why it's flagged
    suggestion: str  # What to do about it


@dataclass
class OrphanReport:
    """Complete orphan detection report."""

    candidates: list[OrphanCandidate] = field(default_factory=list)
    total_modules_scanned: int = 0
    total_imports_collected: int = 0
    excluded_count: int = 0

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total_modules_scanned": self.total_modules_scanned,
            "total_imports_collected": self.total_imports_collected,
            "excluded_count": self.excluded_count,
            "orphan_candidates": len(self.candidates),
            "high_confidence": sum(1 for c in self.candidates if c.confidence == "high"),
            "medium_confidence": sum(1 for c in self.candidates if c.confidence == "medium"),
            "low_confidence": sum(1 for c in self.candidates if c.confidence == "low"),
        }


# ---------------------------------------------------------------------------
# Module path resolution
# ---------------------------------------------------------------------------


def _file_to_module(filepath: Path, repo_root: Path) -> str | None:
    """Convert a Python file path to a dotted module path."""
    rel = filepath.relative_to(repo_root)
    parts = list(rel.parts)

    # Strip 'src/' from paths like umrs/src/umrs/foo.py
    if "src" in parts:
        src_idx = parts.index("src")
        parts = parts[:src_idx] + parts[src_idx + 1 :]

    # Remove .py extension
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    # Remove __init__
    if parts[-1] == "__init__":
        parts = parts[:-1]

    # Drop top-level directory that isn't a package (umrs/, r4/, etc.)
    # For umrs/src/umrs/foo.py → parts=['umrs','umrs','foo'] → want 'umrs.foo'
    # For r4/src/r4/foo.py → parts=['r4','r4','foo'] → want 'r4.foo'
    # For gateway/gateway/foo.py → parts=['gateway','gateway','foo'] → want 'gateway.foo'
    # Heuristic: if parts[0] == parts[1], dedup them
    if len(parts) >= 2 and parts[0] == parts[1]:
        parts = parts[1:]

    # For mcp-servers/src/r4_mcp/foo.py → parts=['mcp-servers','r4_mcp','foo']
    # Drop non-Python-identifier top dirs
    if parts and not parts[0].replace("_", "").isalnum():
        parts = parts[1:]

    if not parts:
        return None
    return ".".join(parts)


def _is_excluded(filepath: Path, repo_root: Path) -> bool:
    """Check if a file matches exclusion patterns."""
    rel = filepath.relative_to(repo_root).as_posix()

    # Check known entrypoints
    if rel in KNOWN_ENTRYPOINTS:
        return True

    # Check exclusion patterns
    return any(filepath.match(pattern) for pattern in EXCLUDE_PATTERNS)


# ---------------------------------------------------------------------------
# Import collection
# ---------------------------------------------------------------------------


def _imports_from_tree(tree: ast.AST, py_file: Path, repo_root: Path, imports: set) -> None:
    """Record one file's imports into *imports* (children of ImportFrom
    counted too — orphan-classification wave)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_import_chain(imports, alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _importfrom_base(py_file, node, repo_root)
            if base:
                _add_import_chain(imports, base)
                # `from pkg import a, b` — the children are imported
                # modules too (invisible consumers caused flag-scale
                # false positives).
                for alias in node.names:
                    if alias.name != "*":
                        imports.add(f"{base}.{alias.name}")


def _collect_all_imports(repo_root: Path) -> set[str]:
    """Collect all imported module names from the repository."""
    imports: set[str] = set()

    for scan_root in IMPORT_SCAN_ROOTS:
        root_path = repo_root / scan_root
        if not root_path.exists():
            continue
        for py_file in root_path.rglob("*.py"):
            if not py_file.is_file():
                continue
            try:
                source = py_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            _imports_from_tree(tree, py_file, repo_root, imports)

    return imports


def _importfrom_base(py_file: Path, node: ast.ImportFrom, repo_root: Path) -> str | None:
    """Base module for an ImportFrom node, relative imports resolved."""
    if node.level == 0:
        return node.module or None
    file_mod = _file_to_module(py_file, repo_root)
    if not file_mod:
        return None
    # Relative resolution starts from the ENCLOSING package: foo/bar.py has
    # package foo (but foo/__init__.py has package foo — no filename part).
    package = file_mod if py_file.name == "__init__.py" else file_mod.rsplit(".", 1)[0]
    parts = package.split(".")
    # level=1 = current package; module=None = `from . import a`.
    if node.level > len(parts):
        return None
    effective = parts[: len(parts) - (node.level - 1)]
    if not effective:
        return None
    base = ".".join(effective)
    return f"{base}.{node.module}" if node.module else base


def _add_import_chain(imports: set[str], module: str) -> None:
    """Add a module and all its parent packages to the import set.

    When someone does ``from umrs.jobs.store import X``, this means
    ``umrs``, ``umrs.jobs``, and ``umrs.jobs.store`` are all referenced.
    """
    parts = module.split(".")
    for i in range(1, len(parts) + 1):
        imports.add(".".join(parts[:i]))


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------


def _is_dynamic_known(module: str) -> bool:
    """True when an explicit dynamic-load family covers *module*."""
    if module in PINNED_PUBLIC_SURFACE:
        return True
    if any(module.startswith(dyn + ".") or module == dyn for dyn in DYNAMIC_IMPORT_MODULES):
        return True
    return any(module.startswith(pkg + ".") or module == pkg for pkg in DYNAMIC_DISPATCH_PACKAGES)


def detect_orphans(repo_root: Path) -> OrphanReport:
    """Detect modules with zero inbound imports."""
    report = OrphanReport()

    # Collect all imports from the entire repository
    all_imports = _collect_all_imports(repo_root)
    report.total_imports_collected = len(all_imports)

    # Scan source trees for candidate modules
    for source_root in SOURCE_ROOTS:
        root_path = repo_root / source_root
        if not root_path.exists():
            continue

        for py_file in sorted(root_path.rglob("*.py")):
            if not py_file.is_file():
                continue

            report.total_modules_scanned += 1

            if _is_excluded(py_file, repo_root):
                report.excluded_count += 1
                continue

            module = _file_to_module(py_file, repo_root)
            if not module:
                continue

            # Check if this module is imported anywhere
            if module in all_imports:
                continue

            # Check explicit dynamic-load families
            if _is_dynamic_known(module):
                report.excluded_count += 1
                continue

            # Classify confidence
            rel_path = py_file.relative_to(repo_root).as_posix()
            confidence, reason, suggestion = _classify_orphan(module, rel_path, py_file)

            report.candidates.append(
                OrphanCandidate(
                    file=rel_path,
                    module=module,
                    confidence=confidence,
                    reason=reason,
                    suggestion=suggestion,
                )
            )

    return report


def _classify_orphan(module: str, rel_path: str, py_file: Path) -> tuple[str, str, str]:
    """Classify orphan confidence and generate suggestion."""
    # Check file size — empty or near-empty files are low confidence
    try:
        content = py_file.read_text()
        line_count = len(content.splitlines())
    except (OSError, UnicodeDecodeError):
        line_count = 0

    if line_count <= 5:
        return ("low", "Near-empty module", "Review: may be a placeholder")

    # Adapter/shim patterns
    if any(term in module for term in ("compat", "shim", "bridge", "migration")):
        return (
            "high",
            "Compatibility/migration module with no inbound imports",
            "Candidate for removal — verify no external consumers",
        )

    # Helper/util modules
    if any(term in module for term in ("helper", "util", "misc")):
        return (
            "medium",
            "Utility module with no inbound imports",
            "Review: may be used via dynamic import or external tooling",
        )

    # Large modules are more likely genuinely orphaned
    if line_count > 100:
        return (
            "high",
            f"Substantial module ({line_count} lines) with no inbound imports",
            "Review for removal or consolidation",
        )

    return (
        "medium",
        "Module with no detected inbound imports",
        "Review: verify no dynamic or external usage",
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_human(report: OrphanReport) -> str:
    lines: list[str] = []
    lines.append("Orphan Module Detection Report")
    lines.append("=" * 60)
    lines.append(f"Scanned: {report.total_modules_scanned} modules")
    lines.append(f"Excluded: {report.excluded_count} (entrypoints, tests, etc.)")
    lines.append(f"Imports collected: {report.total_imports_collected}")
    lines.append(f"Orphan candidates: {len(report.candidates)}")

    if report.candidates:
        lines.append("")
        for c in sorted(
            report.candidates,
            key=lambda x: (-{"high": 3, "medium": 2, "low": 1}[x.confidence], x.file),
        ):
            icon = {"high": "✗", "medium": "⚠", "low": "ℹ"}.get(c.confidence, "?")
            lines.append(f"  {icon} [{c.confidence.upper()}] {c.file}")
            lines.append(f"    Module:     {c.module}")
            lines.append(f"    Reason:     {c.reason}")
            lines.append(f"    Suggestion: {c.suggestion}")
    else:
        lines.append("\nNo orphan candidates detected.")

    return "\n".join(lines)


def _format_json(report: OrphanReport) -> str:
    return json.dumps(
        {
            "candidates": [asdict(c) for c in report.candidates],
            "summary": report.summary,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    output_json = "--json" in sys.argv

    report = detect_orphans(repo_root)

    if output_json:
        print(_format_json(report))
    else:
        print(_format_human(report))
        print("\nOrphan module detector completed")

    # WP-13 round-2 promotion #7s (2026-09-11): findings fail the gate
    # (post-classification-wave, zero-finding baseline).
    return 1 if report.candidates else 0


if __name__ == "__main__":
    raise SystemExit(main())
