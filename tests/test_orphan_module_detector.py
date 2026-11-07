"""Tests for orphan module detector."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tools.orphan_module_detector import (
    _add_import_chain,
    _classify_orphan,
    _collect_all_imports,
    _file_to_module,
    _is_excluded,
    detect_orphans,
)


class TestFileToModule:
    """Test dotted module path resolution from file paths."""

    def _resolve(self, rel_path: str) -> str | None:
        repo = Path("/fake/repo")
        return _file_to_module(repo / rel_path, repo)

    def test_umrs_module(self):
        assert self._resolve("umrs/src/umrs/jobs/store.py") == "umrs.jobs.store"

    def test_r4_module(self):
        assert self._resolve("r4/src/r4/transport.py") == "r4.transport"

    def test_gateway_module(self):
        assert self._resolve("gateway/gateway/metrics.py") == "gateway.metrics"

    def test_init_file(self):
        assert self._resolve("umrs/src/umrs/jobs/__init__.py") == "umrs.jobs"

    def test_nested_module(self):
        assert (
            self._resolve("umrs/src/umrs/model_registry/store/crud.py")
            == "umrs.model_registry.store.crud"
        )

    def test_mcp_module(self):
        result = self._resolve("mcp-servers/src/r4_mcp/cli.py")
        assert result == "r4_mcp.cli"


class TestClassifyOrphan:
    def test_compat_module(self, tmp_path):
        f = tmp_path / "compat.py"
        f.write_text("# compat shim\nfoo = 1\nbar = 2\n" * 10)
        conf, reason, _ = _classify_orphan("pkg.compat", "pkg/compat.py", f)
        assert conf == "high"
        assert "Compatibility" in reason

    def test_small_module(self, tmp_path):
        f = tmp_path / "small.py"
        f.write_text("x = 1\n")
        conf, _, _ = _classify_orphan("pkg.small", "pkg/small.py", f)
        assert conf == "low"

    def test_large_module(self, tmp_path):
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 200)
        conf, reason, _ = _classify_orphan("pkg.big", "pkg/big.py", f)
        assert conf == "high"
        assert "200 lines" in reason

    def test_helper_module(self, tmp_path):
        f = tmp_path / "helper.py"
        f.write_text("x = 1\n" * 20)
        conf, reason, _ = _classify_orphan("pkg.helper", "pkg/helper.py", f)
        assert conf == "medium"
        assert "Utility" in reason

    def test_medium_non_special_module(self, tmp_path):
        f = tmp_path / "regular.py"
        f.write_text("x = 1\n" * 50)
        conf, _, _ = _classify_orphan("pkg.regular", "pkg/regular.py", f)
        assert conf == "medium"


class TestAddImportChain:
    def test_adds_all_parents(self):
        imports: set[str] = set()
        _add_import_chain(imports, "umrs.jobs.store")
        assert "umrs" in imports
        assert "umrs.jobs" in imports
        assert "umrs.jobs.store" in imports

    def test_single_level(self):
        imports: set[str] = set()
        _add_import_chain(imports, "r4")
        assert imports == {"r4"}


class TestIsExcluded:
    def test_excludes_test_files(self, tmp_path):
        f = tmp_path / "tests" / "test_foo.py"
        f.parent.mkdir()
        f.touch()
        assert _is_excluded(f, tmp_path)

    def test_excludes_init(self, tmp_path):
        f = tmp_path / "pkg" / "__init__.py"
        f.parent.mkdir()
        f.touch()
        assert _is_excluded(f, tmp_path)

    def test_excludes_conftest(self, tmp_path):
        f = tmp_path / "conftest.py"
        f.touch()
        assert _is_excluded(f, tmp_path)

    def test_does_not_exclude_regular(self, tmp_path):
        f = tmp_path / "pkg" / "module.py"
        f.parent.mkdir()
        f.touch()
        assert not _is_excluded(f, tmp_path)


class TestCollectAllImports:
    def test_collects_from_source(self, tmp_path):
        # Create a minimal source tree
        src = tmp_path / "umrs" / "src" / "umrs" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("from umrs.bar import baz\nimport os\n")
        imports = _collect_all_imports(tmp_path)
        assert "umrs.bar" in imports
        assert "umrs" in imports

    def test_handles_syntax_errors(self, tmp_path):
        src = tmp_path / "umrs" / "src" / "bad.py"
        src.parent.mkdir(parents=True)
        src.write_text("def incomplete(\n")
        # Should not raise
        imports = _collect_all_imports(tmp_path)
        assert isinstance(imports, set)


class TestDetectOrphansUnit:
    def test_empty_repo(self, tmp_path):
        report = detect_orphans(tmp_path)
        assert report.total_modules_scanned == 0
        assert report.candidates == []

    def test_detects_orphan(self, tmp_path):
        # Create a module with no imports pointing to it
        mod = tmp_path / "umrs" / "src" / "umrs" / "lonely.py"
        mod.parent.mkdir(parents=True)
        mod.write_text("# never imported\nx = 1\n" * 10)
        report = detect_orphans(tmp_path)
        orphan_modules = {c.module for c in report.candidates}
        assert "umrs.lonely" in orphan_modules

    def test_non_orphan_excluded(self, tmp_path):
        # Create two modules where one imports the other
        pkg = tmp_path / "umrs" / "src" / "umrs"
        pkg.mkdir(parents=True)
        (pkg / "target.py").write_text("x = 1\n" * 10)
        (pkg / "consumer.py").write_text("from umrs.target import x\n" * 10)
        report = detect_orphans(tmp_path)
        orphan_modules = {c.module for c in report.candidates}
        assert "umrs.target" not in orphan_modules


class TestLiveRepository:
    """Tests against the actual repository state."""

    def test_detector_runs_without_error(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_orphans(repo_root)
        assert report.total_modules_scanned > 0
        assert report.total_imports_collected > 0

    def test_known_modules_not_orphaned(self):
        """Core modules should never appear as orphan candidates."""
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_orphans(repo_root)
        orphan_modules = {c.module for c in report.candidates}

        # These are well-imported modules that must never be flagged
        core_modules = {
            "umrs.jobs.store",
            "umrs.jobs.models",
            "umrs.jobs.reconciler",
            "umrs.jobs.scheduler",
            "umrs.jobs.monitor",
            "umrs.metrics",
            "umrs.orchestrator.site_config",
            "umrs.model_registry.store.crud",
            "r4.client",
            "r4.config",
            "r4.auth",
        }
        false_positives = core_modules & orphan_modules
        assert false_positives == set(), f"Core modules flagged as orphans: {false_positives}"

    def test_entrypoints_excluded(self):
        """CLI entrypoints should not appear as orphans."""
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_orphans(repo_root)
        orphan_files = {c.file for c in report.candidates}

        entrypoint_files = {
            "r4/src/r4/cli.py",
            "gateway/gateway/main.py",
        }
        flagged_entrypoints = entrypoint_files & orphan_files
        assert flagged_entrypoints == set(), f"Entrypoints flagged: {flagged_entrypoints}"

    def test_report_has_summary(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_orphans(repo_root)
        summary = report.summary
        assert "total_modules_scanned" in summary
        assert "orphan_candidates" in summary
        assert summary["orphan_candidates"] == len(report.candidates)


def test_importfrom_records_child_modules(tmp_path):
    """Classification wave: `from pkg import child` counts child as imported."""
    import tools.orphan_module_detector as omd

    importer = tmp_path / "a.py"
    importer.write_text("from umrs.api import assistant_service\n")
    tree = ast.parse(importer.read_text())
    imports: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = omd._importfrom_base(importer, node, tmp_path)
            if base:
                omd._add_import_chain(imports, base)
                for alias in node.names:
                    imports.add(f"{base}.{alias.name}")
    assert "umrs.api.assistant_service" in imports


def test_relative_base_resolves_enclosing_package(tmp_path):
    """`from . import sib` in pkg/mod.py resolves pkg.sib, not pkg.mod.sib."""
    import tools.orphan_module_detector as omd

    pkg = tmp_path / "umrs" / "cli"
    pkg.mkdir(parents=True)
    mod = pkg / "main.py"
    mod.write_text("from . import scenario\n")
    tree = ast.parse(mod.read_text())
    node = tree.body[0]
    base = omd._importfrom_base(mod, node, tmp_path)
    assert base == "umrs.cli"


def test_relative_base_annex_level(tmp_path):
    """`from ..server.rdr import x` from umrs.api.workflows climbs one level."""
    import tools.orphan_module_detector as omd

    pkg = tmp_path / "umrs" / "api"
    pkg.mkdir(parents=True)
    mod = pkg / "workflows.py"
    mod.write_text("from ..server.run_definition_registry import get_definition\n")
    tree = ast.parse(mod.read_text())
    node = tree.body[0]
    base = omd._importfrom_base(mod, node, tmp_path)
    assert base == "umrs.server.run_definition_registry"


def test_pinned_public_surface_excluded():
    """Test-pinned importability surfaces never surface as orphans."""
    import tools.orphan_module_detector as omd

    for module in ("r4.observability", "r4_mcp.runners.sandbox_enforcer", "umrs.api.context"):
        assert omd._is_dynamic_known(module) is True
    # Dynamic/trigger-evidenced rows are whitelisted too (wave 2).
    for module in ("umrs.api.mcp_service", "umrs.slurm.k8s_bootstrap"):
        assert omd._is_dynamic_known(module) is True
    # Non-evidenced rows are NOT swallowed by accident.
    assert omd._is_dynamic_known("umrs.perhaps_a_deletion_someday") is False


# Dormant-module registry <-> detector pins (repo-cleanup wave C-0, 2026-10-16).
_REGISTRY = "docs/development/dormant-modules.md"
_FROZEN_COMMENT_RE = re.compile(r"#\s*FROZEN:\s*" + re.escape(_REGISTRY) + r"#([a-z0-9-]+)\s*$")
_PIN_LINE_RE = re.compile(r'^\s*"([\w.-]+)",')
_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
_CODE_NAME_RE = re.compile(r"`([\w.-]+)`")
_CODE_PATH_RE = re.compile(r"`([\w./-]+)`")
_REGISTRY_ROOTS = ("r4", "umrs")  # "## r4" / "## umrs": rows give paths under <root>/src/<root>/


def _heading_anchor(heading: str) -> str:
    """GitHub-style anchor of a markdown heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _frozen_pin_comments(source: str) -> dict[str, str]:
    """Pinned module -> registry anchor, for each pin line directly under a FROZEN comment."""
    found: dict[str, str] = {}
    pending: str | None = None
    for line in source.splitlines():
        comment = _FROZEN_COMMENT_RE.search(line)
        if comment:
            pending = comment.group(1)
            continue
        if pending is not None:
            pin = _PIN_LINE_RE.match(line)
            assert pin, f"a FROZEN comment must sit directly above its pin line: {line!r}"
            found[pin.group(1)] = pending
            pending = None
    return found


def _module_cell_names(root: str, cell: str) -> set[str]:
    """Dotted names a Module cell covers.

    Forms in use: `pkg/mod.py`, `mod.py`, `pkg/__init__.py`, and a package
    followed by its files, `pkg/` (`__init__.py`, `sub.py`).
    """
    names: set[str] = set()
    package = ""
    for path in _CODE_PATH_RE.findall(cell):
        if path.endswith("/"):
            package = path.rstrip("/")
            names.add(f"{root}.{package.replace('/', '.')}")
            continue
        if package and "/" not in path:
            path = f"{package}/{path}"
        parts = path.removesuffix(".py").split("/")
        if parts[-1] == "__init__":
            parts.pop()
        names.add(".".join([root, *parts]))
    return names


def _registry_frozen_pins(text: str) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Heading anchors; Detector-pin name -> section anchor; module -> section anchor.

    A table row counts when one cell is exactly ``KEEP-FROZEN``. Its module's
    dotted name comes from the ``## r4`` / ``## umrs`` root plus the first
    cell ("Module"); its pin is the backticked dotted name in the last cell
    ("Detector pin"), or none.
    """
    anchors: set[str] = set()
    pins: dict[str, str] = {}
    frozen: dict[str, str] = {}
    section = root = ""
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            section = _heading_anchor(heading.group(2))
            anchors.add(section)
            if len(heading.group(1)) == 2:
                root = section if section in _REGISTRY_ROOTS else ""
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not (line.startswith("|") and "KEEP-FROZEN" in cells):
            continue
        assert root, f"KEEP-FROZEN row outside the ## r4 / ## umrs sections: {line!r}"
        pins.update(dict.fromkeys(_CODE_NAME_RE.findall(cells[-1]), section))
        frozen.update(dict.fromkeys(_module_cell_names(root, cells[0]), section))
    return anchors, pins, frozen


def test_module_cell_names_cover_every_registry_form():
    assert _module_cell_names("r4", "`a2l_client.py`") == {"r4.a2l_client"}
    assert _module_cell_names("umrs", "`api/scopes.py`") == {"umrs.api.scopes"}
    assert _module_cell_names("umrs", "`voice/__init__.py`") == {"umrs.voice"}
    assert _module_cell_names("umrs", "`recipes/` (`__init__.py`)") == {"umrs.recipes"}
    assert _module_cell_names("umrs", "`tenants/` (`__init__.py`, `pg_store.py`)") == {
        "umrs.tenants",
        "umrs.tenants.pg_store",
    }


def _assert_frozen_rows_name_their_pins(
    rows: dict[str, str], frozen: dict[str, str], pinned: frozenset[str]
) -> None:
    """Every pinned KEEP-FROZEN module names its pin in its own row, and every
    Detector pin cell names its own row's module."""
    for module in sorted(frozen.keys() & pinned):
        assert module in rows, (
            f"{module}: KEEP-FROZEN and pinned, but its Detector pin cell is empty"
        )
    for module, anchor in rows.items():
        assert frozen.get(module) == anchor, f"{module}: Detector pin names another row's module"


def _assert_comments_match_rows(
    comments: dict[str, str], rows: dict[str, str], anchors: set[str], pinned: frozenset[str]
) -> None:
    """Every FROZEN comment sits on a pin, names a registry heading and the
    section holding its row; every pinned KEEP-FROZEN row has a comment."""
    assert comments, "no FROZEN: comment in the detector's pin sets"
    assert set(comments) == set(rows)
    for module, anchor in comments.items():
        assert module in pinned, f"{module}: FROZEN comment on a line that is not a pin"
        assert anchor in anchors, f"{module}: #{anchor} is not a heading in {_REGISTRY}"
        assert rows[module] == anchor, (
            f"{module}: comment says #{anchor}, row is under #{rows[module]}"
        )


def test_frozen_pins_match_dormant_module_registry():
    """Registry rule 2, both directions: every pinned KEEP-FROZEN module names
    its pin in its row and carries a FROZEN comment pointing at the section
    holding that row; every FROZEN comment has such a row."""
    import tools.orphan_module_detector as omd

    repo_root = Path(__file__).resolve().parents[1]
    registry_path = repo_root / _REGISTRY
    assert registry_path.is_file(), f"{_REGISTRY} is missing"
    source = (repo_root / "tools" / "orphan_module_detector.py").read_text(encoding="utf-8")
    anchors, rows, frozen = _registry_frozen_pins(registry_path.read_text(encoding="utf-8"))
    pinned = omd.DYNAMIC_IMPORT_MODULES | omd.PINNED_PUBLIC_SURFACE

    _assert_frozen_rows_name_their_pins(rows, frozen, pinned)
    _assert_comments_match_rows(_frozen_pin_comments(source), rows, anchors, pinned)
