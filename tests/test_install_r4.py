"""Hermetic tests for ``scripts/install-r4.sh`` (one-command install, 2026-11-07).

The installer runs with HOME, TMPDIR and PATH inside ``tmp_path``. ``uv``,
``r4``, ``git`` and ``python3`` are small stub scripts that log their
arguments; the "release" is a directory read through real ``curl`` with
``file://`` URLs (``R4_RELEASE_BASE_URL`` / ``R4_RELEASE_API_URL``), so no
test touches the network or the real home directory.

Before 2026-11-07 the installer always cloned the repository and pip-installed
it into ~/.r4/venv: every release-path test here fails on that script.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install-r4.sh"
TAG = "v9.9.9-rc1"

#: Real tools the script may call; everything else on PATH is a stub.
SYSTEM_TOOLS = (
    "bash", "sh", "curl", "tar", "gzip", "mktemp", "rm", "mkdir", "find", "head",
    "dirname", "basename", "awk", "grep", "sed", "sha256sum", "shasum", "cat",
    "ls", "ln", "chmod", "cp", "tr", "cut", "env", "uname",
)  # fmt: skip

WHEELS = (
    "r4-9.9.9rc1-py3-none-any.whl",
    "r4_contracts-9.9.9rc1-py3-none-any.whl",
    "r4_teamengine_spi-9.9.9rc1-py3-none-any.whl",
    "r4_science-0.1.0-py3-none-any.whl",
    "umrs-9.9.9rc1-py3-none-any.whl",
)

UV_STUB = """#!/bin/sh
if [ "$1 $2" = "tool dir" ]; then echo "$STUB_TOOL_BIN"; exit 0; fi
if [ "$1 $2 $3" = "tool install --help" ]; then
    echo "  -w, --with <WITH>"
    [ -n "${STUB_UV_OLD:-}" ] || echo "      --with-executables-from <WITH_EXECUTABLES_FROM>"
    exit 0
fi
printf '%s\\n' "---" "$@" >> "$STUB_LOG_DIR/uv.log"
mkdir -p "$STUB_TOOL_BIN"
cp "$STUB_TEMPLATES/r4" "$STUB_TOOL_BIN/r4"
cp "$STUB_TEMPLATES/umrs" "$STUB_TOOL_BIN/umrs"
"""

R4_STUB = """#!/bin/sh
if [ "$1" = "--version" ] || [ "$1" = "--help" ]; then echo "r4 9.9.9rc1"; exit 0; fi
printf '%s\\n' "--- R4_TUI_VERSION=${R4_TUI_VERSION:-}" \\
    "R4_TUI_DIST_BASE_URL=${R4_TUI_DIST_BASE_URL:-}" "$@" >> "$STUB_LOG_DIR/r4.log"
exit "${STUB_R4_RC:-0}"
"""

UMRS_STUB = """#!/bin/sh
exit 0
"""

GIT_STUB = """#!/bin/sh
printf '%s\\n' "---" "$@" >> "$STUB_LOG_DIR/git.log"
if [ "$1" = "clone" ]; then for last; do :; done; mkdir -p "$last/.git"; fi
exit 0
"""

PYTHON_STUB = """#!/bin/sh
if [ "$1" = "-c" ]; then
    case "$2" in *print*) echo "3.12" ;; esac
    exit 0
fi
if [ "$1 $2" = "-m venv" ]; then
    mkdir -p "$3/bin"
    printf '#!/bin/sh\\nprintf "%%s\\\\n" "---" "$@" >> "$STUB_LOG_DIR/pip.log"\\n' > "$3/bin/pip"
    cp "$STUB_TEMPLATES/r4" "$3/bin/r4"
    cp "$STUB_TEMPLATES/umrs" "$3/bin/umrs"
    chmod +x "$3/bin/pip"
fi
exit 0
"""


@dataclass
class Sandbox:
    root: Path
    home: Path
    logs: Path
    tool_bin: Path
    release: Path
    env: dict[str, str]

    def run(self, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **extra}
        return subprocess.run(
            [str(self.root / "sys" / "bash"), str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            check=False,
        )

    def calls(self, name: str) -> list[list[str]]:
        log = self.logs / f"{name}.log"
        if not log.is_file():
            return []
        out: list[list[str]] = []
        for line in log.read_text().splitlines():
            if line.startswith("---"):
                out.append([line] if line != "---" else [])
            else:
                out[-1].append(line)
        return out


def _write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def _link_system_tools(dest: Path) -> None:
    dest.mkdir(parents=True)
    for tool in SYSTEM_TOOLS:
        found = shutil.which(tool)
        if found:
            (dest / tool).symlink_to(found)


def make_bundle(
    release: Path, tag: str, *, corrupt: str = "", sums: bool = True, flat: bool = False
) -> Path:
    """Write ``<release>/<tag>/r4-client-<tag>.tar.gz`` with dummy wheels,
    under one top directory, or ``flat`` (the release workflow's layout)."""
    top = "" if flat else f"r4-client-{tag}/"
    lines, members = [], {}
    for name in WHEELS:
        data = f"wheel {name}".encode()
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
        members[name] = b"tampered" if name == corrupt else data
    if sums:
        members["SHA256SUMS"] = ("\n".join(lines) + "\n").encode()
    out = release / tag / f"r4-client-{tag}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(f"{top}{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out


def write_listing(path: Path, releases: list[dict], *, indent: int | None = None) -> str:
    path.write_text(json.dumps(releases, indent=indent))
    return f"file://{path}"


def release_entry(tag: str, *, bundle: bool, draft: bool = False) -> dict:
    tui = "r4-tui-linux-x64"
    assets = [{"name": tui, "browser_download_url": f"https://x/{tag}/{tui}"}]
    if bundle:
        name = f"r4-client-{tag}.tar.gz"
        assets.append({"name": name, "browser_download_url": f"https://x/{tag}/{name}"})
    return {"tag_name": tag, "draft": draft, "prerelease": "-" in tag, "assets": assets}


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    home, logs, stubs, templates = (tmp_path / d for d in ("home", "logs", "stubs", "templates"))
    for d in (home, logs, tmp_path / "tmp"):
        d.mkdir()
    _link_system_tools(tmp_path / "sys")
    _write_exe(stubs / "uv", UV_STUB)
    _write_exe(stubs / "git", GIT_STUB)
    _write_exe(stubs / "python3", PYTHON_STUB)
    _write_exe(templates / "r4", R4_STUB)
    _write_exe(templates / "umrs", UMRS_STUB)
    tool_bin = tmp_path / "toolbin"
    env = {
        "HOME": str(home),
        "TMPDIR": str(tmp_path / "tmp"),
        "PATH": f"{stubs}:{tmp_path / 'sys'}",
        "STUB_LOG_DIR": str(logs),
        "STUB_TOOL_BIN": str(tool_bin),
        "STUB_TEMPLATES": str(templates),
        "R4_RELEASE_BASE_URL": f"file://{tmp_path / 'release'}",
        "R4_RELEASE_API_URL": f"file://{tmp_path / 'no-such-listing.json'}",
    }
    return Sandbox(tmp_path, home, logs, tool_bin, tmp_path / "release", env)


def _install_args(sb: Sandbox) -> list[str]:
    installs = [c for c in sb.calls("uv") if c[:2] == ["tool", "install"]]
    assert len(installs) == 1, installs
    return installs[0]


def _suffix(ref: str) -> str:
    """The random part of the installer's ``r4-install.XXXXXX`` temp dir."""
    return ref.split("/r4-install.", 1)[1].split("/", 1)[0]


def _value_after(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


class TestReleaseInstall:
    def test_installs_only_local_wheels_as_direct_references(self, sandbox):
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode == 0, proc.stderr
        args = _install_args(sandbox)
        assert args[2:5] == ["--python", "3.12", "--force"]
        refs = [args[5], *_value_after(args, "--with")]
        refs += _value_after(args, "--with-executables-from")
        names = ["r4", "r4-contracts", "r4-teamengine-spi", "r4-science", "umrs[client]"]
        tmp = sandbox.root / "tmp"
        assert refs == [
            f"{name} @ file://{tmp}/r4-install.{_suffix(refs[0])}/bundle/r4-client-{TAG}/{wheel}"
            for name, wheel in zip(names, WHEELS, strict=True)
        ]

    def test_console_is_fetched_for_the_same_tag_and_temp_dir_removed(self, sandbox):
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode == 0, proc.stderr
        dist = f"R4_TUI_DIST_BASE_URL={sandbox.env['R4_RELEASE_BASE_URL']}/{TAG}"
        assert sandbox.calls("r4") == [[f"--- R4_TUI_VERSION={TAG}", dist, "tui", "install"]]
        assert list((sandbox.root / "tmp").iterdir()) == []
        assert "uv tool uninstall r4" in proc.stdout

    def test_an_explicit_console_base_wins_over_the_release_mirror(self, sandbox):
        # Before 2026-11-07 a mirror (R4_RELEASE_BASE_URL) served the bundle but
        # `r4 tui install` still went to GitHub for the console.
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG, R4_TUI_DIST_BASE_URL="https://mirror/tui")
        assert proc.returncode == 0, proc.stderr
        assert sandbox.calls("r4")[0][1] == "R4_TUI_DIST_BASE_URL=https://mirror/tui"

    def test_uninstall_hint_removes_only_the_console_binary(self, sandbox):
        # `rm -rf ~/.local/share/r4` would also delete the delete-guard trash.
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG)
        assert f"rm -f {sandbox.home}/.local/share/r4/bin/r4-tui" in proc.stdout
        assert "rm -rf" not in proc.stdout.replace("rm -rf ~/.r4/venv", "")
        proc = sandbox.run(R4_VERSION=TAG, R4_TUI_HOME="/opt/r4tui")
        assert "rm -f /opt/r4tui/bin/r4-tui" in proc.stdout

    def test_release_install_names_the_old_source_install_and_update_shell(self, sandbox):
        make_bundle(sandbox.release, TAG)
        (sandbox.home / ".r4" / "venv").mkdir(parents=True)
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode == 0, proc.stderr
        assert "is no longer used (rm -rf ~/.r4/venv ~/.r4/src)" in proc.stdout
        assert "uv tool update-shell" in proc.stdout  # the tool bin is not on PATH

    def test_skip_tui_and_tui_failure_is_not_fatal(self, sandbox):
        make_bundle(sandbox.release, TAG)
        assert sandbox.run(R4_VERSION=TAG, R4_SKIP_TUI="1").returncode == 0
        assert sandbox.calls("r4") == []
        proc = sandbox.run(R4_VERSION=TAG, STUB_R4_RC="1")
        assert proc.returncode == 0
        assert "r4 tui install" in proc.stderr

    def test_old_uv_installs_the_umrs_cli_as_its_own_tool(self, sandbox):
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG, STUB_UV_OLD="1")
        assert proc.returncode == 0, proc.stderr
        installs = [c for c in sandbox.calls("uv") if c[:2] == ["tool", "install"]]
        assert [c[5].split(" @ ")[0] for c in installs] == ["r4", "umrs[client]"]
        assert "umrs[client] @ file://" in " ".join(_value_after(installs[0], "--with"))
        assert "--with-executables-from" not in installs[0]
        assert "uv tool uninstall r4 && uv tool uninstall umrs" in proc.stdout

    def test_new_uv_needs_no_umrs_uninstall(self, sandbox):
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode == 0, proc.stderr
        assert "uv tool uninstall umrs" not in proc.stdout


class TestVerification:
    def test_checksum_mismatch_aborts_before_uv_installs(self, sandbox):
        make_bundle(sandbox.release, TAG, corrupt=WHEELS[1])
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode != 0
        assert "checksum mismatch" in proc.stderr
        assert sandbox.calls("uv") == []

    def test_flat_bundle_installs(self, sandbox):
        make_bundle(sandbox.release, TAG, flat=True)
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode == 0, proc.stderr
        assert _install_args(sandbox)[5].endswith(f"/bundle/{WHEELS[0]}")

    def test_release_sha256sums_must_match_the_bundle(self, sandbox):
        bundle = make_bundle(sandbox.release, TAG)
        sums = bundle.parent / "SHA256SUMS"
        sums.write_text(f"{'0' * 64}  {bundle.name}\n{'1' * 64}  install.sh\n")
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode != 0
        assert "differs from the release's SHA256SUMS" in proc.stderr
        assert sandbox.calls("uv") == []
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        sums.write_text(f"{digest}  {bundle.name}\n{'1' * 64}  install.sh\n")
        assert sandbox.run(R4_VERSION=TAG).returncode == 0

    def test_bundle_without_sha256sums_is_refused(self, sandbox):
        make_bundle(sandbox.release, TAG, sums=False)
        proc = sandbox.run(R4_VERSION=TAG)
        assert proc.returncode != 0
        assert "no SHA256SUMS" in proc.stderr
        assert sandbox.calls("uv") == []

    def test_pinned_tag_without_bundle_names_the_source_path(self, sandbox):
        proc = sandbox.run(R4_VERSION="v0.1.0")
        assert proc.returncode != 0
        assert "R4_FROM_SOURCE=1 R4_BRANCH=v0.1.0" in proc.stderr
        assert sandbox.calls("git") == []


class TestReleaseDiscovery:
    def test_newest_non_draft_release_with_a_bundle_wins(self, sandbox):
        listing = [
            release_entry("v2.0.0", bundle=True, draft=True),
            release_entry("v1.1.0-rc1", bundle=False),
            release_entry("v1.0.0-rc2", bundle=True),
            release_entry("v0.9.0", bundle=True),
        ]
        url = write_listing(sandbox.root / "releases.json", listing)  # compact JSON
        make_bundle(sandbox.release, "v1.0.0-rc2")
        proc = sandbox.run(R4_RELEASE_API_URL=url)
        assert proc.returncode == 0, proc.stderr
        assert "/v1.0.0-rc2/r4-client-v1.0.0-rc2.tar.gz" in proc.stdout + proc.stderr

    def test_r4_version_pin_skips_the_api_and_adds_the_v(self, sandbox):
        make_bundle(sandbox.release, "v1.2.3")
        proc = sandbox.run(R4_VERSION="1.2.3")  # the listing URL does not exist
        assert proc.returncode == 0, proc.stderr
        assert _install_args(sandbox)[5].endswith(WHEELS[0])

    def test_unreachable_listing_aborts_with_a_hint(self, sandbox):
        proc = sandbox.run()
        assert proc.returncode != 0
        assert "R4_VERSION=<tag>" in proc.stderr
        assert sandbox.calls("git") == [] and sandbox.calls("uv") == []

    def test_bad_tag_is_refused(self, sandbox):
        proc = sandbox.run(R4_VERSION="v1;rm -rf /")
        assert proc.returncode != 0
        assert "not a tag name" in proc.stderr


class TestSourceFallback:
    def _assert_source_install(self, sb: Sandbox, proc) -> None:
        assert proc.returncode == 0, proc.stderr
        clone = sb.calls("git")[0]
        assert clone[:2] == ["clone", "--depth"]
        assert clone[-2:] == ["https://github.com/R-CCS-HPBD/r4.git", f"{sb.home}/.r4/src"]
        assert sb.calls("uv") == []
        assert os.readlink(sb.home / ".local/bin/r4") == f"{sb.home}/.r4/venv/bin/r4"
        editable = [c[-2] for c in sb.calls("pip") if "-e" in c]
        assert editable[-1] == f"{sb.home}/.r4/src/umrs/src[client]"

    def test_no_release_with_a_bundle_falls_back_to_the_source_install(self, sandbox):
        listing = [release_entry("v0.2.0-rc1", bundle=False)]
        url = write_listing(sandbox.root / "releases.json", listing, indent=2)
        proc = sandbox.run(R4_RELEASE_API_URL=url)
        self._assert_source_install(sandbox, proc)
        assert "falling back to the source install" in proc.stderr

    def test_r4_from_source_never_asks_the_api(self, sandbox):
        proc = sandbox.run(R4_FROM_SOURCE="1")  # the listing URL does not exist
        self._assert_source_install(sandbox, proc)

    def test_source_install_keeps_its_venv_and_names_no_uv_hint(self, sandbox):
        # Before 2026-11-07 the source fallback called its own fresh ~/.r4/venv
        # "no longer used" and pointed at `uv tool update-shell` (no uv there).
        proc = sandbox.run(R4_FROM_SOURCE="1")
        self._assert_source_install(sandbox, proc)
        out = proc.stdout + proc.stderr
        assert "no longer used" not in out
        assert "update-shell" not in out
        assert f'export PATH="{sandbox.home}/.local/bin:$PATH"' in out


class TestUvBootstrap:
    def _hide_uv(self, sb: Sandbox) -> Path:
        uv = sb.root / "templates" / "uv"
        (sb.root / "stubs" / "uv").rename(uv)
        return uv

    def test_missing_uv_is_installed_with_the_official_installer(self, sandbox):
        uv = self._hide_uv(sandbox)
        installer = sandbox.root / "uv-install.sh"
        installer.write_text(f'mkdir -p "$UV_INSTALL_DIR"; cp "{uv}" "$UV_INSTALL_DIR/uv"\n')
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(
            R4_VERSION=TAG,
            R4_UV_INSTALLER_URL=f"file://{installer}",
            UV_INSTALL_DIR=str(sandbox.root / "uvbin"),
        )
        assert proc.returncode == 0, proc.stderr
        assert "official installer" in proc.stderr
        assert _install_args(sandbox)[5].endswith(WHEELS[0])

    def test_no_uv_install_opt_out_stops(self, sandbox):
        self._hide_uv(sandbox)
        make_bundle(sandbox.release, TAG)
        proc = sandbox.run(R4_VERSION=TAG, R4_NO_UV_INSTALL="1")
        assert proc.returncode != 0
        assert "R4_NO_UV_INSTALL=1" in proc.stderr


def test_bundle_tag_parser_reads_one_line_json(sandbox):
    """The anonymous API listing may come minified; the parser is regex-based."""
    listing = [release_entry("v3.0.0", bundle=True)]
    url = write_listing(sandbox.root / "releases.json", listing)
    make_bundle(sandbox.release, "v3.0.0")
    assert sandbox.run(R4_RELEASE_API_URL=url).returncode == 0


def test_shellcheck_clean():
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    proc = subprocess.run([shellcheck, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout


def test_script_avoids_bash4_only_features():
    """macOS ships bash 3.2: no associative arrays, mapfile or ${x,,}."""
    text = SCRIPT.read_text()
    for needle in ("declare -A", "mapfile", "readarray", ",,}", "^^}", "inherit_errexit"):
        assert needle not in text, needle
