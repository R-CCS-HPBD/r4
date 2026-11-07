"""Static import-boundary validator for platform ownership rules.

Correctness notes (2026-05-13 audit):

* ``ast.walk`` traverses ALL nodes including function bodies,
  ``if TYPE_CHECKING`` blocks, and nested scopes.
* Relative imports (``from ..server.sql import X``) have ``node.level > 0``
  and must be resolved to absolute module paths using the file's package.
* Imports inside ``if TYPE_CHECKING:`` blocks are type-only and must be
  excluded from boundary enforcement.
* Function-scoped lazy imports are architectural decisions (deferred binding
  for optional deps or circular avoidance).  They are reported with an
  ``[lazy]`` tag so rules can distinguish them from module-level imports.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

STDLIB_MODULES = set(sys.stdlib_module_names) | set(sys.builtin_module_names)


@dataclass(frozen=True)
class ImportBoundaryRule:
    """One source tree import ownership rule."""

    root: str
    description: str
    forbidden_top_levels: tuple[str, ...] = ()
    forbidden_subpackages: tuple[str, ...] = ()
    stdlib_only: bool = False
    allowed_top_levels: tuple[str, ...] = ()
    # When True, function-scoped lazy imports are excluded from enforcement.
    # This allows composition-site lazy imports while enforcing module-level.
    allow_lazy: bool = False


@dataclass(frozen=True)
class ImportBoundaryFinding:
    """One import ownership violation."""

    path: str
    line: int
    imported: str
    message: str
    is_lazy: bool = False
    is_type_checking: bool = False

    def format(self) -> str:
        tags = []
        if self.is_lazy:
            tags.append("lazy")
        if self.is_type_checking:
            tags.append("type-only")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        return f"{self.path}:{self.line}: imports {self.imported!r}{tag_str}; {self.message}"


IMPORT_BOUNDARY_RULES: tuple[ImportBoundaryRule, ...] = (
    ImportBoundaryRule(
        root="r4/src/r4",
        description="r4 is a client/UX layer and must not import UMRS or Gateway internals",
        forbidden_top_levels=("umrs", "gateway"),
    ),
    ImportBoundaryRule(
        root="gateway/gateway",
        description="Gateway is an API edge and must talk to UMRS through HTTP contracts",
        forbidden_top_levels=("umrs",),
    ),
    ImportBoundaryRule(
        root="gateway/sidecar",
        description="Gateway sidecar is an edge component and must not import UMRS internals",
        forbidden_top_levels=("umrs",),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs",
        description="UMRS must remain independently workable without the r4 client package",
        forbidden_top_levels=("r4",),
    ),
    ImportBoundaryRule(
        root="r4-contracts/src/r4_contracts",
        description="r4-contracts must stay stdlib-only and free of platform/runtime dependencies",
        stdlib_only=True,
        allowed_top_levels=("r4_contracts",),
    ),
    ImportBoundaryRule(
        root="packages/r4-teamengine-spi/src/r4_teamengine_spi",
        description="Team Engine SPI depends only on r4-contracts and stdlib (ADR-020)",
        stdlib_only=True,
        allowed_top_levels=("r4_contracts", "r4_teamengine_spi"),
    ),
    ImportBoundaryRule(
        root="engines/r4-engine-langgraph/src/r4_engine_langgraph",
        description="Team engine adapters import SPI + contracts + engine SDK only (ADR-020)",
        forbidden_top_levels=("r4", "umrs", "gateway"),
    ),
)

# UMRS internal boundary rules (SI-01): foundation modules must not
# import from upper layers (api/, server/).  These are kept separate so
# they can be enabled independently while existing violations are fixed.
UMRS_INTERNAL_BOUNDARY_RULES: tuple[ImportBoundaryRule, ...] = (
    ImportBoundaryRule(
        root="umrs/src/umrs/model_registry",
        description="model_registry is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/mcp",
        description="mcp is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/rag",
        description="rag is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/pipeline_sdk",
        description="pipeline_sdk is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/orchestrator",
        description="orchestrator must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/jobs",
        description="jobs is a foundation module and must not import from api/ or server/ (ADR-053: the jobs↔api cycle forced tests to spec-load modules)",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/security",
        description="security module must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/shared",
        description="shared is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/inference",
        description="inference is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/jobs",
        description="jobs is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
        allow_lazy=True,  # reason=lazy composition (jobs/adapters import-sites) owner=agent-debug added=2026-09-05 expires=2026-12
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/skills",
        description="skills is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/recipes",
        description="recipes is a foundation module and must not import from api/ or server/",
        forbidden_subpackages=("umrs.api", "umrs.server"),
    ),
    # ADR-024: Knowledge module boundary (K2)
    ImportBoundaryRule(
        root="umrs/src/umrs/knowledge_model",
        description="knowledge_model is a leaf satellite store (ADR-024) and must not import knowledge/, api/, or server/",
        forbidden_subpackages=("umrs.knowledge", "umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/knowledge_graph",
        description="knowledge_graph is a leaf satellite store (ADR-024) and must not import knowledge/, api/, or server/",
        forbidden_subpackages=("umrs.knowledge", "umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/scientific_memory",
        description="scientific_memory is a leaf satellite store (ADR-024) and must not import knowledge/, api/, or server/",
        forbidden_subpackages=("umrs.knowledge", "umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/evidence",
        description="evidence is a leaf satellite store (ADR-024) and must not import knowledge/, api/, or server/",
        forbidden_subpackages=("umrs.knowledge", "umrs.api", "umrs.server"),
    ),
    ImportBoundaryRule(
        root="umrs/src/umrs/knowledge",
        description="knowledge is the composition layer (ADR-024) and must not import api/ or server/ (lazy composition imports tolerated)",
        forbidden_subpackages=("umrs.api", "umrs.server"),
        allow_lazy=True,  # reason=composition-site lazy imports (ingest meta-store fallback) owner=agent-debug added=2026-09-05 expires=2026-12
    ),
)


def validate_import_boundaries(
    repo_root: Path,
    rules: Sequence[ImportBoundaryRule] = IMPORT_BOUNDARY_RULES,
) -> list[ImportBoundaryFinding]:
    """Return import boundary violations for the configured source roots."""
    findings: list[ImportBoundaryFinding] = []
    for rule in rules:
        source_root = repo_root / rule.root
        if not source_root.exists():
            continue
        for path in _iter_python_files(source_root):
            findings.extend(_validate_file(repo_root, path, rule))
    return findings


def assert_no_import_boundary_violations(repo_root: Path) -> None:
    """Raise AssertionError when documented package import boundaries drift."""
    findings = validate_import_boundaries(repo_root)
    if findings:
        details = "\n".join(finding.format() for finding in findings)
        raise AssertionError(f"Import boundary violations detected:\n{details}")


def _iter_python_files(source_root: Path) -> Iterable[Path]:
    return sorted(path for path in source_root.rglob("*.py") if path.is_file())


# ---------------------------------------------------------------------------
# AST scope analysis helpers
# ---------------------------------------------------------------------------


@dataclass
class _ImportInfo:
    """Resolved import metadata extracted from AST."""

    module: str  # Fully-qualified module path (absolute)
    line: int
    is_lazy: bool  # True when inside a function/method body
    is_type_checking: bool  # True when inside ``if TYPE_CHECKING:`` block


def _resolve_package_for_file(repo_root: Path, filepath: Path, rule_root: str) -> str:
    """Derive the dotted package path for *filepath* within the rule's source root.

    Example: repo_root / "umrs/src/umrs/jobs/adapters.py" with
    rule_root "umrs/src/umrs" → package "umrs.jobs" (the directory's package).
    """
    source_root = repo_root / rule_root
    try:
        relative = filepath.relative_to(source_root)
    except ValueError:
        return ""

    # Translate path parts to package segments, stripping the filename
    # The rule root itself encodes the top-level package segments.
    # e.g. rule_root "umrs/src/umrs" → top package prefix from the last
    # directory segment chain that matches a Python package.
    # We use the rule_root's last path component(s) that form the package.
    # For "umrs/src/umrs" the package prefix is "umrs".
    # For "r4/src/r4" the package prefix is "r4".
    # For "gateway/gateway" the package prefix is "gateway".

    # Heuristic: walk rule_root segments from the right and take everything
    # after 'src/' or, failing that, just the last segment.
    root_parts = Path(rule_root).parts
    if "src" in root_parts:
        idx = len(root_parts) - 1 - list(reversed(root_parts)).index("src")
        pkg_prefix_parts = root_parts[idx + 1 :]
    else:
        # e.g. "gateway/gateway" → take the last segment
        pkg_prefix_parts = root_parts[-1:]

    pkg_prefix = ".".join(pkg_prefix_parts)

    # Build full package from relative path (strip filename)
    rel_parts = list(relative.parts[:-1])  # directory segments only
    if rel_parts:
        return pkg_prefix + "." + ".".join(rel_parts)
    return pkg_prefix


def _is_type_checking_guard(node: ast.AST) -> bool:
    """Return True if *node* is ``if TYPE_CHECKING:`` or ``if typing.TYPE_CHECKING:``."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _resolve_relative(file_package: str, level: int, module: str | None) -> str | None:
    """Convert a relative import to an absolute module path."""
    if not file_package:
        return module
    parts = file_package.split(".")
    # level=1 means current package, level=2 means parent, etc.
    if level > len(parts):
        return module  # Cannot resolve beyond root — return as-is
    base_parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
    base = ".".join(base_parts)
    if module:
        return f"{base}.{module}"
    return base


def _record_import(
    results: list[_ImportInfo],
    file_package: str,
    child: ast.AST,
    in_func: bool,
    in_tc: bool,
) -> None:
    if isinstance(child, ast.Import):
        for alias in child.names:
            results.append(
                _ImportInfo(
                    module=alias.name,
                    line=child.lineno,
                    is_lazy=in_func,
                    is_type_checking=in_tc,
                )
            )
        return
    if not isinstance(child, ast.ImportFrom):
        return
    if child.level == 0 and child.module:
        results.append(
            _ImportInfo(
                module=child.module,
                line=child.lineno,
                is_lazy=in_func,
                is_type_checking=in_tc,
            )
        )
        return
    resolved = _resolve_relative(file_package, child.level, child.module)
    if resolved:
        results.append(
            _ImportInfo(
                module=resolved,
                line=child.lineno,
                is_lazy=in_func,
                is_type_checking=in_tc,
            )
        )


def _walk_imports(
    results: list[_ImportInfo],
    file_package: str,
    node: ast.AST,
    in_function: bool,
    in_type_checking: bool,
) -> None:
    for child in ast.iter_child_nodes(node):
        child_in_func = in_function
        child_in_tc = in_type_checking

        if _is_type_checking_guard(child):
            assert isinstance(child, ast.If)
            # Body of the if-branch is type-only; else-branch is not.
            for body_node in child.body:
                _walk_imports(results, file_package, body_node, child_in_func, True)
            for else_node in child.orelse:
                _walk_imports(results, file_package, else_node, child_in_func, child_in_tc)
            continue  # Skip default recursion into this If node

        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child_in_func = True
        _record_import(results, file_package, child, child_in_func, child_in_tc)
        _walk_imports(results, file_package, child, child_in_func, child_in_tc)


def _collect_imports(
    tree: ast.Module,
    file_package: str,
) -> list[_ImportInfo]:
    """Walk the full AST and collect resolved import metadata.

    Correctly handles:
    * Relative imports (resolved via *file_package*)
    * ``if TYPE_CHECKING`` blocks
    * Function-scoped lazy imports
    * Alias imports (``import X as Y``)
    """
    results: list[_ImportInfo] = []
    _walk_imports(results, file_package, tree, False, False)
    return results


def _parse_source(
    path: Path, relative_path: str
) -> tuple[str, ast.Module] | list[ImportBoundaryFinding]:
    """Read + parse a file. Returns (source, tree), or a one-item finding
    list for parse errors, or [] for unreadable files."""
    try:
        source = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        return source, ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        return [
            ImportBoundaryFinding(
                path=relative_path,
                line=exc.lineno or 1,
                imported="<syntax-error>",
                message=f"could not parse Python source: {exc.msg}",
            )
        ]


def _exempted(source_lines: list[str], line: int) -> bool:
    if line > len(source_lines):
        return False
    return "# noqa: boundary" in source_lines[line - 1]


def _is_violation(imp: _ImportInfo, rule: ImportBoundaryRule) -> bool:
    top_level = imp.module.split(".", 1)[0]
    if top_level in rule.forbidden_top_levels:
        return True
    if rule.stdlib_only and not _is_stdlib_or_allowed(top_level, rule):
        return True
    return any(
        imp.module == forbidden or imp.module.startswith(forbidden + ".")
        for forbidden in rule.forbidden_subpackages
    )


def _finding_for(
    imp: _ImportInfo,
    rule: ImportBoundaryRule,
    source_lines: list[str],
    relative_path: str,
) -> ImportBoundaryFinding | None:
    # TYPE_CHECKING-only imports never execute at runtime
    if imp.is_type_checking:
        return None
    if _exempted(source_lines, imp.line):
        return None
    if not _is_violation(imp, rule):
        return None
    # If rule allows lazy imports and this is lazy, skip
    if rule.allow_lazy and imp.is_lazy:
        return None
    return ImportBoundaryFinding(
        path=relative_path,
        line=imp.line,
        imported=imp.module,
        message=rule.description,
        is_lazy=imp.is_lazy,
    )


def _validate_file(
    repo_root: Path,
    path: Path,
    rule: ImportBoundaryRule,
) -> list[ImportBoundaryFinding]:
    relative_path = path.relative_to(repo_root).as_posix()
    parsed = _parse_source(path, relative_path)
    if isinstance(parsed, list):
        return parsed
    source, tree = parsed

    file_package = _resolve_package_for_file(repo_root, path, rule.root)
    imports = _collect_imports(tree, file_package)
    source_lines = source.splitlines()
    findings = (_finding_for(imp, rule, source_lines, relative_path) for imp in imports)
    return [finding for finding in findings if finding is not None]


def _is_stdlib_or_allowed(top_level: str, rule: ImportBoundaryRule) -> bool:
    return top_level in STDLIB_MODULES or top_level in rule.allowed_top_levels


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    findings = validate_import_boundaries(repo_root)
    rc = 0
    if findings:
        for finding in findings:
            print(finding.format())
        rc = 1
    else:
        print("Import boundary validator passed")

    # UMRS internal boundaries — now enforced as hard-fail (Phase B escalation).
    # All known violations were remediated in Phase A.
    internal_findings = validate_import_boundaries(repo_root, rules=UMRS_INTERNAL_BOUNDARY_RULES)
    if internal_findings:
        print(f"\nUMRS internal boundary violations ({len(internal_findings)}):")
        for finding in internal_findings:
            print(f"  [FAIL] {finding.format()}")
        rc = 1
    else:
        print("UMRS internal boundary check passed")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
