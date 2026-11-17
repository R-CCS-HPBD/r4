"""Tests for ``scripts/ci/assert_umrs_protocol.py``, the CI proof that
umrs-protocol was installed from this checkout (U2.0, ADR-117 D-2).

Each case builds a scratch repository root (``umrs/protocol`` with a pyproject,
a package and the golden gate pin) and a scratch site directory holding a
hand-written ``umrs_protocol-<v>.dist-info``, then runs the script in a
``python -S`` subprocess whose ``sys.path`` is only the stdlib plus those
directories. Nothing reads the real environment's packages. Before U2.0 the
script did not exist, so every case here fails on the old tree.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "assert_umrs_protocol.py"
VERSION = "9.9.9rc1"
GATE = b'"""fake gate"""\nGATE_VERSION = "3"\n'


@dataclass
class Layout:
    root: Path
    site: Path

    @property
    def protocol(self) -> Path:
        return self.root / "umrs" / "protocol"

    @property
    def package(self) -> Path:
        return self.protocol / "src" / "umrs_protocol"

    def install(self, *, version: str = VERSION, url: str | None = "checkout") -> None:
        """Write the dist-info an install would leave (``url=None``: none)."""
        info = self.site / f"umrs_protocol-{version}.dist-info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: umrs-protocol\nVersion: {version}\n"
        )
        if url is not None:
            target = self.protocol.as_uri() if url == "checkout" else url
            direct = {"url": target, "dir_info": {"editable": True}}
            (info / "direct_url.json").write_text(json.dumps(direct))

    def run(self, *args: str, path: list[Path] | None = None) -> subprocess.CompletedProcess[str]:
        entries = path if path is not None else [self.site, self.protocol / "src"]
        return subprocess.run(
            [sys.executable, "-S", "-c", _RUNNER, str(SCRIPT), *map(str, entries), "--", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
        )


#: Put the given directories first on sys.path, then run the script's main().
_RUNNER = """
import runpy, sys
script, rest = sys.argv[1], sys.argv[2:]
cut = rest.index("--")
sys.path[:0] = rest[:cut]
sys.argv = [script, *rest[cut + 1:]]
runpy.run_path(script, run_name="__main__")
"""


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    lay = Layout(tmp_path / "repo", tmp_path / "site")
    lay.package.mkdir(parents=True)
    (lay.package / "__init__.py").write_text('"""fake umrs_protocol"""\n')
    (lay.package / "hpc_gate.py").write_bytes(GATE)
    (lay.protocol / "pyproject.toml").write_text(
        f'[project]\nname = "umrs-protocol"\nversion = "{VERSION}"\n'
    )
    golden = lay.protocol / "tests" / "golden"
    golden.mkdir(parents=True)
    pin = {"gate_version": "3", "sha256": hashlib.sha256(GATE).hexdigest(), "blob": "0" * 40}
    (golden / "gate.json").write_text(json.dumps(pin))
    lay.site.mkdir()
    return lay


def _errors(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [ln for ln in proc.stdout.splitlines() if ln.startswith("::error")]


def test_an_editable_install_from_the_checkout_passes(layout: Layout) -> None:
    layout.install()
    proc = layout.run("--root", str(layout.root))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"umrs-protocol proof: {VERSION} from {layout.package / '__init__.py'}" in proc.stdout


def test_an_index_install_without_direct_url_fails(layout: Layout) -> None:
    layout.install(url=None)
    proc = layout.run("--root", str(layout.root))
    assert proc.returncode == 1
    assert any("was not installed from" in ln for ln in _errors(proc))


def test_a_direct_url_to_another_tree_fails(layout: Layout, tmp_path: Path) -> None:
    layout.install(url=(tmp_path / "elsewhere").as_uri())
    proc = layout.run("--root", str(layout.root))
    assert proc.returncode == 1
    assert any("was not installed from" in ln for ln in _errors(proc))


def _shadow(layout: Layout, gate: bytes = GATE) -> None:
    """A copy of the package in the site directory, first on sys.path."""
    shadow = layout.site / "umrs_protocol"
    shutil.copytree(layout.package, shadow)
    (shadow / "hpc_gate.py").write_bytes(gate)


def test_a_stale_shadow_outside_the_checkout_fails_the_editable_proof(layout: Layout) -> None:
    layout.install()
    _shadow(layout)
    proc = layout.run("--root", str(layout.root))
    assert proc.returncode == 1
    assert any("imports from" in ln for ln in _errors(proc))


def test_non_editable_accepts_a_built_copy_with_the_checkout_bytes(layout: Layout) -> None:
    layout.install()
    _shadow(layout)
    proc = layout.run("--root", str(layout.root), "--non-editable", path=[layout.site])
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_other_gate_bytes_fail_even_non_editable(layout: Layout) -> None:
    layout.install()
    _shadow(layout, gate=GATE + b"# drift\n")
    proc = layout.run("--root", str(layout.root), "--non-editable", path=[layout.site])
    assert proc.returncode == 1
    assert any("differs from the checkout's" in ln for ln in _errors(proc))


def test_a_version_other_than_the_checkouts_fails(layout: Layout) -> None:
    layout.install(version="0.0.1")
    proc = layout.run("--root", str(layout.root))
    assert proc.returncode == 1
    assert any(f"0.0.1 is installed; the checkout is {VERSION}" in ln for ln in _errors(proc))


def test_nothing_installed_fails_with_one_error(layout: Layout) -> None:
    proc = layout.run("--root", str(layout.root), path=[layout.site])
    assert proc.returncode == 1
    errors = _errors(proc)
    assert len(errors) == 1 and "is not installed" in errors[0]


def _contracts(layout: Layout, *, alias: bool) -> None:
    """A fake r4_contracts whose hpc_gate is (or is not) the protocol module."""
    pkg = layout.site / "r4_contracts"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    body = (
        "import sys\nimport umrs_protocol.hpc_gate as _gate\nsys.modules[__name__] = _gate\n"
        if alias
        else GATE.decode()
    )
    (pkg / "hpc_gate.py").write_text(body)


def test_flipped_passes_on_the_alias_and_the_pinned_gate(layout: Layout) -> None:
    layout.install()
    _contracts(layout, alias=True)
    proc = layout.run("--root", str(layout.root), "--flipped")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "(alias flipped)" in proc.stdout


def test_flipped_fails_without_the_alias(layout: Layout) -> None:
    layout.install()
    _contracts(layout, alias=False)
    proc = layout.run("--root", str(layout.root), "--flipped")
    assert proc.returncode == 1
    assert any("no alias" in ln for ln in _errors(proc))


def test_flipped_fails_on_an_unpinned_gate(layout: Layout) -> None:
    layout.install()
    _contracts(layout, alias=True)
    pin = layout.protocol / "tests" / "golden" / "gate.json"
    pin.write_text(json.dumps({"gate_version": "4", "sha256": "f" * 64, "blob": "0" * 40}))
    proc = layout.run("--root", str(layout.root), "--flipped")
    assert proc.returncode == 1
    errors = _errors(proc)
    assert any("is not the pinned release" in ln for ln in errors)
    assert any("GATE_VERSION 3 is not 4" in ln for ln in errors)


def test_the_default_root_is_this_repository() -> None:
    proc = subprocess.run(
        [sys.executable, "-S", "-c", "import runpy, sys; m = runpy.run_path(sys.argv[1]); "
         "print(m['default_root']())", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )  # fmt: skip
    assert Path(proc.stdout.strip()) == REPO_ROOT


def test_the_script_is_stdlib_only() -> None:
    """CI runs it before anything but umrs-protocol is installed; ops-watch
    may run it with the host's python3."""
    import ast

    tree = ast.parse(SCRIPT.read_text())
    roots = {
        (node.module or "").split(".")[0]
        if isinstance(node, ast.ImportFrom)
        else alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert roots - {"__future__"} <= set(sys.stdlib_module_names)
