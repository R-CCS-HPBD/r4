"""Every non-CI install site takes umrs-protocol from the checkout (U2.0a).

umrs-protocol (ADR-117 D-2) is on no package index: the name is unclaimed on
PyPI, so an install site that leaves it to the resolver becomes a
dependency-confusion path once r4-contracts depends on it (U2.0b). These
guards pin the U2.0a wiring of spec §4.5: images, compose mounts, the
Makefile, the client and demo installers, the release cut and the local gate.
The CI workflows are pinned in test_ci_workflow_contract.py, install-r4.sh in
test_install_r4.py, the ansible tasks in test_gateway_topology.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.deps import lockctl

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def _before(text: str, first: str, second: str) -> bool:
    return first in text and second in text and text.index(first) < text.index(second)


def test_dockerignore_admits_umrs_protocol():
    lines = [ln.strip() for ln in _read(".dockerignore").splitlines()]
    assert lines.index("*") < lines.index("!umrs/protocol/")


def test_r4_image_installs_umrs_protocol_first_and_locked():
    text = _read("r4/Dockerfile")
    copy = "COPY umrs/protocol/pyproject.toml umrs/protocol/README.md umrs/protocol/LICENSE /build/umrs-protocol/"
    assert copy in text
    assert "COPY umrs/protocol/src/ /build/umrs-protocol/src/" in text
    run = "RUN pip install --no-cache-dir --no-deps /build/umrs-protocol"
    assert _before(text, run, "RUN pip install --no-cache-dir --no-deps /build/r4-contracts")
    assert lockctl.dockerfile_problems(text) == []


def test_mcp_image_installs_both_siblings_in_one_resolve():
    text = re.sub(r"\s+", " ", re.sub(r"\\\n", " ", _read("mcp-servers/Dockerfile")))
    assert "COPY umrs/protocol/ /tmp/umrs-protocol/" in text
    assert "RUN pip install --no-cache-dir /tmp/umrs-protocol /tmp/r4-contracts &&" in text
    assert _before(text, "/tmp/umrs-protocol /tmp/r4-contracts", 'pip install -e ".[all]"')


def test_makefile_installs_umrs_protocol_before_r4_contracts():
    text = _read("Makefile")
    installs = [ln for ln in text.splitlines() if '-e "./r4-contracts"' in ln]
    assert len(installs) == 4
    assert all('-e "./umrs/protocol" -e "./r4-contracts"' in ln for ln in installs)


@pytest.mark.parametrize("service", ["umrs", "rag", "umrs-docmodels"])
def test_standalone_dev_mounts_umrs_protocol(service: str):
    dev = yaml.safe_load(_read("deploy/standalone/compose.dev.yaml"))
    volumes = dev["services"][service]["volumes"]
    assert "../../umrs/protocol:/app/umrs-protocol:ro,z" in volumes


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _client_setup(tmp_path: Path, *, protocol: bool) -> tuple[subprocess.CompletedProcess, Path]:
    """Run a copy of setup_r4_client.sh inside a fake repository tree, with a
    stub python3 (its venv python logs pip calls) and a stub git (logs)."""
    repo, stubs = tmp_path / "repo", tmp_path / "stubs"
    siblings = ("r4", "r4-contracts", "packages/r4-teamengine-spi", "science")
    for rel in siblings + (("umrs/protocol",) if protocol else ()):
        (repo / rel).mkdir(parents=True)
        (repo / rel / "pyproject.toml").write_text("[project]\n")
    (repo / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "setup_r4_client.sh", repo / "scripts")
    stubs.mkdir()
    _stub(tmp_path / "venv-python", f'printf \'%s\\n\' --- "$@" >> "{tmp_path}/pip.log"\n')
    venv_python = tmp_path / "venv-python"
    # Stub every interpreter name find_python tries (python3.13 … python3): a
    # runner with a real /usr/bin/python3.11 otherwise wins over a lone
    # `python3` stub and real pip runs on these name-less stub projects
    # (CI run 43581859444, 2026-11-14).
    venv_stub = f'if [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin" && cp "{venv_python}" "$3/bin/python"; fi\n'
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        _stub(stubs / name, venv_stub)
    _stub(stubs / "git", f'printf \'%s\\n\' --- "$@" >> "{tmp_path}/git.log"\n')
    # A decoy interpreter after the stubs stands in for a runner's real
    # /usr/bin/python3.11; the setup must never reach it.
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _stub(decoy / "python3.11", f'echo decoy >> "{tmp_path}/decoy.log"; exit 1\n')
    env = {
        "PATH": f"{stubs}:{decoy}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "R4_CLIENT_HOME": str(tmp_path / "client"),
        "R4_CLIENT_BIN_DIR": str(tmp_path / "bin"),
    }
    done = subprocess.run(
        ["bash", str(repo / "scripts" / "setup_r4_client.sh")],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    return done, repo


def test_client_setup_installs_umrs_protocol_first(tmp_path: Path):
    done, repo = _client_setup(tmp_path, protocol=True)
    assert not (tmp_path / "decoy.log").exists(), "a real interpreter shadowed the stubs"
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "pip.log").read_text().split("---\n")
    install = next(c.split("\n") for c in calls if "-e" in c)
    editable = [install[i + 1] for i, arg in enumerate(install) if arg == "-e"]
    assert editable == [
        str(repo / "umrs" / "protocol"),
        str(repo / "r4-contracts"),
        str(repo / "packages" / "r4-teamengine-spi"),  # unpublished: never by name
        str(repo / "science"),
        f"{repo / 'r4'}[companion]",
    ]
    assert not (tmp_path / "git.log").exists(), "the tree had protocol/: no fetch"


def test_client_setup_fetches_the_submodule_and_stops_without_it(tmp_path: Path):
    done, repo = _client_setup(tmp_path, protocol=False)
    assert done.returncode == 1
    assert "umrs/protocol/pyproject.toml" in done.stderr
    git = (tmp_path / "git.log").read_text().split()
    assert git[:4] == ["---", "-C", str(repo), "-c"]
    assert git[4:] == [
        "url.https://github.com/.insteadOf=git@github.com:",
        "submodule",
        "update",
        "--init",
        "--depth",
        "1",
        "umrs",
    ]
    assert not (tmp_path / "pip.log").exists(), "nothing may be installed by name"


def test_client_and_watchdog_setup_fetch_the_submodule_anonymously():
    for rel in ("scripts/setup_r4_client.sh", "ops/watchdog/setup.sh"):
        text = _read(rel)
        assert "-c 'url.https://github.com/.insteadOf=git@github.com:'" in text, rel
        assert "submodule update --init --depth 1 umrs" in text, rel
    watchdog = _read("ops/watchdog/setup.sh")
    assert _before(
        watchdog,
        '-e "$STATE_DIR/ai4s-platform/umrs/protocol"',
        '-e "$STATE_DIR/ai4s-platform/r4-contracts"',
    )


def _e2e_setup(tmp_path: Path, *, protocol: bool) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run e2e-demo-setup.sh's refresh path (venv exists) with a logging uv stub."""
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed (the script's precheck needs it)")
    repo, venv, stubs, log = (tmp_path / d for d in ("repo", "venv", "stubs", "uv.log"))
    (repo / "r4").mkdir(parents=True)
    if protocol:
        (repo / "umrs" / "protocol").mkdir(parents=True)
        (repo / "umrs" / "protocol" / "pyproject.toml").write_text("[project]\n")
    venv.mkdir()
    stubs.mkdir()
    uv = stubs / "uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "---" "$@" >> "{log}"\n')
    uv.chmod(0o755)
    env = {
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "E2E_REPO_DIR": str(repo),
        "E2E_DEMO_VENV": str(venv),
    }
    done = subprocess.run(
        ["bash", str(ROOT / "scripts" / "e2e-demo-setup.sh")],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    return done, log


def test_e2e_setup_installs_umrs_protocol_first(tmp_path: Path):
    done, log = _e2e_setup(tmp_path, protocol=True)
    assert done.returncode == 0, done.stdout + done.stderr
    first = log.read_text().split("---\n")[1].split()
    repo = tmp_path / "repo"
    assert first[:6] == [
        "pip",
        "install",
        "-e",
        f"{repo}/umrs/protocol",
        "-e",
        f"{repo}/r4-contracts",
    ]


def test_e2e_setup_stops_without_umrs_protocol(tmp_path: Path):
    done, log = _e2e_setup(tmp_path, protocol=False)
    assert done.returncode == 2
    assert "umrs/protocol missing" in done.stdout
    assert not log.exists(), "nothing may be installed by name"


def test_release_cut_bumps_umrs_protocol_with_umrs():
    text = _read("scripts/release/cut.sh")
    sed = next(
        ln for ln in text.splitlines() if "umrs/src/pyproject.toml" in ln and ln.startswith("run ")
    )
    assert sed.rstrip('"').endswith("umrs/src/pyproject.toml umrs/protocol/pyproject.toml")


def _cut_line(text: str, needle: str) -> str:
    return next(ln for ln in text.splitlines() if needle in ln and ln.startswith("run "))


def test_release_cut_bumps_the_umrs_protocol_floors_then_relocks(tmp_path: Path):
    """U2.0b: r4-contracts and r4 require the umrs-protocol they ship with.
    Runs cut.sh's floor sed on copies of both manifests."""
    text = _read("scripts/release/cut.sh")
    assert "floor_files=(r4-contracts/pyproject.toml r4/pyproject.toml)" in text
    floor = _cut_line(text, "${floor_files[*]}")
    for rel in ("r4-contracts/pyproject.toml", "r4/pyproject.toml"):
        (tmp_path / rel).parent.mkdir(parents=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    script = floor.replace("${floor_files[*]}", "r4-contracts/pyproject.toml r4/pyproject.toml")
    script = "py_version=9.9.9rc1\n" + "eval " + script[len("run ") :]
    subprocess.run(["bash", "-c", script], cwd=tmp_path, check=True)
    for rel in ("r4-contracts/pyproject.toml", "r4/pyproject.toml"):
        old, new = _read(rel), (tmp_path / rel).read_text()
        assert re.findall(r'"umrs-protocol>=[^"]+"', new) == ['"umrs-protocol>=9.9.9rc1"'], rel
        assert new == old.replace(
            re.findall(r'"umrs-protocol>=[^"]+"', old)[0], '"umrs-protocol>=9.9.9rc1"'
        )
    lines = text.splitlines()
    relock = lines.index('run "uv lock"')
    assert lines.index(floor) < relock
    assert "uv.lock" in _cut_line(text, "git add -A umrs").split()


def test_release_cut_bumps_the_umrs_floor_in_the_umrs_commit(tmp_path: Path):
    """U2.0b review finding 2: umrs/src requires umrs-protocol too (U-A1), so
    its floor moves with the release, before the umrs release commit."""
    text = _read("scripts/release/cut.sh")
    floor = next(
        ln for ln in text.splitlines()
        if ln.startswith("run ") and "umrs-protocol>=" in ln and "umrs/src/pyproject.toml" in ln
    )  # fmt: skip
    rel = "umrs/src/pyproject.toml"
    (tmp_path / rel).parent.mkdir(parents=True)
    shutil.copy(ROOT / rel, tmp_path / rel)
    script = "py_version=9.9.9rc1\n" + "eval " + floor[len("run ") :]
    subprocess.run(["bash", "-c", script], cwd=tmp_path, check=True)
    new = (tmp_path / rel).read_text()
    assert re.findall(r'"umrs-protocol>=[^"]+"', new) == ['"umrs-protocol>=9.9.9rc1"']
    lines = text.splitlines()
    assert lines.index(floor) < lines.index("run \"git -C umrs commit -qam 'release: ${tag}'\"")


def test_r4_image_asserts_one_gate_after_pip_check():
    """U2.0b: r4_contracts.hpc_gate is the umrs_protocol module in the image."""
    text = re.sub(r"\\\n\s*", " ", _read("r4/Dockerfile"))
    identity = 'RUN python -c "import r4_contracts.hpc_gate as g, umrs_protocol.hpc_gate as p; assert g is p"'
    assert _before(text, "&& pip check", identity)


def test_local_gate_runs_both_suites_on_a_contracts_change():
    text = _read("scripts/ci-lint-local.sh")
    assert '[[ "$f" =~ ^r4-contracts/ ]] && HAS_CONTRACTS=1' in text
    assert 'git -C umrs diff --name-only "$old" "$new" -- protocol' in text
    lane = text[text.index("if [ $HAS_CONTRACTS -eq 1 ]") :]
    lane = lane[: lane.index("\nfi\n")]
    assert 'run_tests "r4-contracts"      "r4-contracts/tests/"' in lane
    assert 'run_tests "umrs-protocol"     "umrs/protocol/tests/"' in lane


def test_local_gate_runs_the_contracts_lane_on_a_protocol_change():
    """A umrs/protocol change runs the r4-contracts lane, whose suite goes
    through the aliases. U2.0b (2026-11-15) deleted the U2.0a byte-parity test
    and its gate line: the 16 r4_contracts files are shims now, one copy left."""
    text = _read("scripts/ci-lint-local.sh")
    assert '[[ "$f" =~ ^umrs/protocol/ ]] && HAS_CONTRACTS=1' in text
    assert "test_umrs_protocol_parity" not in text
    assert not (ROOT / "tests" / "test_umrs_protocol_parity.py").exists()


# ── r4's unpublished first-party siblings (review U2.0a, 2026-11-14) ─────────
# r4 depends on r4-teamengine-spi and r4-science, which are on no package index
# either; plain pip ignores tool.uv.sources, so an install line that names ./r4
# without them resolves both by name: the same dependency-confusion path.

#: Install sites that name ./r4: CI, Makefile, client setup, and the README
#: and CONTRIBUTING install commands (public and private).
R4_INSTALL_SITES = [
    str(p.relative_to(ROOT))
    for d in (".github/workflows", "scripts/public_export/public-workflows")
    for p in sorted((ROOT / d).glob("*.yaml"))
] + [
    "Makefile",
    "scripts/setup_r4_client.sh",
    "README.md",
    "docs/user-manual/16-deployment.md",
    "scripts/public_export/public-overlay/README.md",
    "scripts/public_export/public-overlay/CONTRIBUTING.md",
]
#: ./r4 (editable or a path), with an optional extra; never ./r4-contracts.
R4_TOKEN = re.compile(
    r"""(?:-e\s+['"]?(?:\./)?r4|['"]?\./r4|"\$\{R4_DIR\})(?:\[[^\]]*\])?['"]?(?=\s|;|$)"""
)
R4_SIBLINGS = {
    "r4-teamengine-spi": re.compile(r"packages/r4-teamengine-spi|\$\{SPI_DIR\}"),
    "r4-science": re.compile(r"(?:\./|\s)science\b|\$\{SCIENCE_DIR\}"),
}


def _r4_install_lines(rel: str) -> list[str]:
    """Commands (backslash continuations joined, split at ';') that install ./r4.

    The split keeps the uv and pip branches of one Makefile recipe apart."""
    text = re.sub(r"\\\n\s*", " ", _read(rel))
    commands = [cmd for ln in text.splitlines() for cmd in ln.split(";")]
    return [cmd for cmd in commands if "install" in cmd and R4_TOKEN.search(cmd)]


def _missing_siblings(line: str) -> list[str]:
    return [name for name, pattern in R4_SIBLINGS.items() if not pattern.search(line)]


def test_every_r4_install_names_its_unpublished_siblings():
    lines = [(rel, ln) for rel in R4_INSTALL_SITES for ln in _r4_install_lines(rel)]
    assert len(lines) >= 15, "the scan must see every install of ./r4"
    gaps = [f"{rel}: {_missing_siblings(ln)}" for rel, ln in lines if _missing_siblings(ln)]
    assert gaps == []


@pytest.mark.parametrize(
    ("line", "missing"),
    [
        (
            "pip install -e ./umrs/protocol -e ./r4-contracts -e ./r4",
            ["r4-teamengine-spi", "r4-science"],
        ),
        ("pip install -e r4-contracts -e packages/r4-teamengine-spi -e r4", ["r4-science"]),
        ("pip install -e ./packages/r4-teamengine-spi -e ./science -e './r4[dev]'", []),
    ],
)
def test_the_sibling_scan_flags_a_bare_r4_install(line: str, missing: list[str]):
    assert R4_TOKEN.search(line)
    assert _missing_siblings(line) == missing


def test_the_r4_scan_ignores_r4_contracts_alone():
    assert R4_TOKEN.search("pip install -e ./umrs/protocol -e ./r4-contracts") is None
