"""``r4 tui`` — manage the compiled OpenTUI binary (install / status).

The v3 TUI ships as a self-contained bun-compiled release binary for installs
without a checkout. ``resolve_tui_binary`` finds a binary only; what ``r4
console`` / ``r4 chat`` actually launch is decided by
``console.plan_console_launch``:

1. ``R4_TUI_BIN`` environment variable (explicit binary)
2. The checkout's own Console v3 source with bun, when r4 is imported from a
   checkout whose deps are installed (``make tui-deps``) and bun >= 1.3.14
3. ``<user_bin>/r4-tui`` (installed by ``r4 tui install``)
4. Dev source fallback (a cwd ``packages/r4-tui-v3`` + bun)
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from argparse import ArgumentParser, Namespace
from pathlib import Path

from .base import CommandHandler
from .registry import command
from .tui_minisign import SignatureError, verify_file

#: Release repository. ``releases/latest`` skips prereleases (every v0.x tag
#: so far is one), so the install resolves the newest published release
#: through the API instead (production enablement 2026-09-25). The public
#: repository is the default; ``R4_RELEASE_REPO`` (``owner/name``, read once
#: at import) points the install at another one, such as the private
#: development repository.
RELEASE_REPO_ENV = "R4_RELEASE_REPO"
PUBLIC_RELEASE_REPO = "R-CCS-HPBD/r4"


def release_repo_from_env() -> str:
    """Return ``R4_RELEASE_REPO`` when set and non-empty, else the public repo."""
    return os.environ.get(RELEASE_REPO_ENV) or PUBLIC_RELEASE_REPO


RELEASE_REPO = release_repo_from_env()
#: Fallback asset base when the release lookup fails.
DEFAULT_DIST_BASE_URL = f"https://github.com/{RELEASE_REPO}/releases/latest/download"
DIST_BASE_URL_ENV = "R4_TUI_DIST_BASE_URL"
VERSION_ENV = "R4_TUI_VERSION"
BIN_PATH_ENV = "R4_TUI_BIN"

#: TUI binary signing, phase 1 (minisign; cycle 3, 2026-10-21): the release
#: key's public half, the second line of its ``minisign.pub`` (``RW...``).
#: Empty until the maintainer provisions the key pair and the release secret
#: (docs/admin/operations/TUI_RELEASE_SIGNING.md); until then a published
#: ``.minisig`` cannot be checked and the install says so.
MINISIGN_PUBLIC_KEY = ""

_BINARY_SUFFIXES = {
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "x86_64"): "darwin-arm64",  # Rosetta runs the arm64 build
    ("linux", "x86_64"): "linux-x64",
    ("linux", "aarch64"): "linux-arm64",
}


def platform_key() -> str | None:
    """Return the release asset suffix for this host, or None if unsupported."""
    return _BINARY_SUFFIXES.get((sys.platform, platform.machine()))


def tui_asset_name() -> str | None:
    key = platform_key()
    return f"r4-tui-{key}" if key else None


def user_bin_dir() -> Path:
    override = os.environ.get("R4_TUI_HOME")
    if override:
        return Path(override).expanduser() / "bin"
    return Path.home() / ".local" / "share" / "r4" / "bin"


def user_bin_path() -> Path:
    return user_bin_dir() / "r4-tui"


def resolve_tui_binary() -> Path | None:
    """Resolve the TUI binary per the documented order; None if not found."""
    env = os.environ.get(BIN_PATH_ENV)
    if env:
        path = Path(env).expanduser()
        return path if path.is_file() else None
    candidate = user_bin_path()
    return candidate if candidate.is_file() else None


def _github_token() -> str:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def _newest_release_tag() -> str | None:
    """Newest non-draft release tag, prereleases included; None when unreachable.

    The default release repository is public. A token (GH_TOKEN/GITHUB_TOKEN)
    or an authenticated ``gh`` is used when present; it is needed only when
    ``R4_RELEASE_REPO`` names a private repository, where an anonymous lookup
    404s.
    """
    url = f"https://api.github.com/repos/{RELEASE_REPO}/releases?per_page=10"
    headers = {"Accept": "application/vnd.github+json"}
    if _github_token():
        headers["Authorization"] = f"Bearer {_github_token()}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=30
        ) as resp:
            releases = json.load(resp)
    except (OSError, ValueError):
        releases = _gh_releases()
    for rel in releases or []:
        if not rel.get("draft") and rel.get("tag_name"):
            return str(rel["tag_name"])
    return None


def _gh_releases() -> list[dict]:
    if shutil.which("gh") is None:
        return []
    proc = subprocess.run(
        ["gh", "api", f"repos/{RELEASE_REPO}/releases?per_page=10"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        return json.loads(proc.stdout) if proc.returncode == 0 else []
    except ValueError:
        return []


def release_tag() -> str | None:
    """``R4_TUI_VERSION`` pin, else the newest release (prereleases included)."""
    return os.environ.get(VERSION_ENV) or _newest_release_tag()


def dist_base_url(tag: str | None = None) -> str:
    override = os.environ.get(DIST_BASE_URL_ENV)
    if override:
        return override.rstrip("/")
    if tag:
        return f"https://github.com/{RELEASE_REPO}/releases/download/{tag}"
    return DEFAULT_DIST_BASE_URL


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected_sha256(sum_file: Path) -> str:
    """The digest a ``<asset>.sha256`` file names, or "" when it names none
    (empty, an HTML error page): the install then aborts on the mismatch
    instead of an IndexError traceback."""
    words = sum_file.read_text(errors="replace").split()
    digest = words[0].lower() if words else ""
    return digest if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) else ""


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def _gh_download(tag: str, names: list[str], dest_dir: Path) -> bool:
    """Private-repo path: ``gh release download`` carries the user's auth."""
    if shutil.which("gh") is None:
        return False
    cmd = ["gh", "release", "download", tag, "-R", RELEASE_REPO, "-D", str(dest_dir), "--clobber"]
    for name in names:
        cmd += ["-p", name]
    return subprocess.run(cmd, capture_output=True, timeout=600, check=False).returncode == 0


def _fetch_signature(name: str, tmp: Path, tag: str | None, base: str) -> Path | None:
    """The release's ``<asset>.minisig`` in ``tmp``, or None when none is
    published (or it cannot be fetched)."""
    sig = tmp / f"{name}.minisig"
    if tag and _gh_download(tag, [sig.name], tmp) and sig.is_file():
        return sig
    try:
        _download(f"{base}/{sig.name}", sig)
    except OSError:
        sig.unlink(missing_ok=True)
        return None
    return sig


def _fetch_assets(name: str, tmp: Path) -> tuple[Path, Path, Path | None]:
    """Download the binary, its checksum and (when published) its minisign
    signature into ``tmp``; raises OSError when the binary or checksum fails."""
    tag = None if os.environ.get(DIST_BASE_URL_ENV) else release_tag()
    base = dist_base_url(tag)
    tmp_bin, tmp_sum = tmp / name, tmp / f"{name}.sha256"
    print(f"Downloading {base}/{name} …")
    if not (tag and _gh_download(tag, [name, f"{name}.sha256"], tmp)):
        _download(f"{base}/{name}", tmp_bin)
        _download(f"{base}/{name}.sha256", tmp_sum)
    return tmp_bin, tmp_sum, _fetch_signature(name, tmp, tag, base)


def _warn_unauthenticated(reason: str) -> None:
    print(
        f"WARNING: {reason}\n"
        "WARNING: the download's INTEGRITY is checked (sha256 from the same release);\n"
        "WARNING: its AUTHENTICITY (that the project's release key built it) is NOT.",
        file=sys.stderr,
    )


def _names_asset(trusted_comment: str, name: str) -> bool:
    """The signed comment names this asset (``r4-tui <tag> <name>`` from the
    release workflow, or minisign's default ``file:<name>``): a signature for
    another platform's binary is refused."""
    return name in trusted_comment.replace(":", " ").split()


def _signature_allows_install(binary: Path, sig: Path | None, name: str) -> bool:
    """Verify the minisign signature; False (abort) only on a bad one.

    Phase 1: an unsigned release, or a signed one this r4 has no pinned key
    for, still installs, with a loud warning.
    """
    if sig is None:
        _warn_unauthenticated(f"no signature ({name}.minisig) is published for {name}.")
        return True
    if not MINISIGN_PUBLIC_KEY:
        _warn_unauthenticated(f"{name} is signed, but this r4 pins no release public key yet.")
        return True
    try:
        trusted = verify_file(binary, sig.read_text(errors="replace"), MINISIGN_PUBLIC_KEY)
        if not _names_asset(trusted, name):
            raise SignatureError(f"the signed comment {trusted!r} does not name {name}")
    except SignatureError as exc:
        print(f"Signature check FAILED for {name}: {exc}\nAborting install.", file=sys.stderr)
        return False
    print(f"Signature OK (minisign, pinned release key): {trusted}")
    return True


def _download_failure_hint(exc: OSError, name: str) -> str:
    """What to do when the release download fails.

    The default release repository (``PUBLIC_RELEASE_REPO``) is public; a
    token is needed only when ``R4_RELEASE_REPO`` points at a private one.
    Until 2026-11-07 this told every user the repository was private.
    """
    return (
        f"Download failed: {exc}\n"
        f"Release repository: {RELEASE_REPO}. If {RELEASE_REPO_ENV} names a private\n"
        f"repository, `gh auth login` (or GH_TOKEN) lets `r4 tui install` fetch\n"
        f"through gh. Pin a tag with {VERSION_ENV}.\n"
        f"In a checkout, `make tui-deps` runs the checkout source instead.\n"
        f"If no release is published yet, build locally:\n"
        f"  cd packages/r4-tui-v3 && bun run scripts/build-bin.mjs\n"
        f"Then: rm -f {user_bin_path()} && mv dist-bin/{name} {user_bin_path()}\n"
        f"(mv — never cp-over-live-binary: macOS provenance will\n"
        f" SIGKILL the recycled inode, exit 137, nothing runs)"
    )


def install_binary() -> int:
    name = tui_asset_name()
    if name is None:
        print(
            f"No prebuilt TUI binary for {sys.platform}/{platform.machine()} yet.",
            file=sys.stderr,
        )
        return 1
    dest_dir = user_bin_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Download next to the installed binary (same filesystem) so the swap is
    # one rename: a running TUI keeps its old inode. Before 2026-10-21 the
    # temp dir was on TMPDIR; shutil.move then copied over the live binary in
    # place — "Text file busy" traceback on Linux, a SIGKILLed binary on macOS.
    with tempfile.TemporaryDirectory(prefix=".r4-tui-", dir=dest_dir) as tmp:
        try:
            tmp_bin, tmp_sum, tmp_sig = _fetch_assets(name, Path(tmp))
        except OSError as exc:
            print(_download_failure_hint(exc, name), file=sys.stderr)
            return 1
        expected = _expected_sha256(tmp_sum)
        actual = _sha256(tmp_bin)
        if expected != actual:
            print(
                f"Checksum mismatch for {name}!\n"
                f"  expected {expected}\n  actual   {actual}\n"
                f"Aborting install.",
                file=sys.stderr,
            )
            return 1
        if not _signature_allows_install(tmp_bin, tmp_sig, name):
            return 1
        tmp_bin.chmod(tmp_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(tmp_bin, user_bin_path())
    print(f"Installed {user_bin_path()}  sha256={actual[:12]}…")
    note = _checkout_precedence_note()
    if note:
        print(note)
    print("Run: r4 console")
    return 0


def _checkout_precedence_note() -> str | None:
    """Say so when ``r4 console`` will keep running the checkout, not this binary."""
    from .console import plan_console_launch  # console imports this module

    _kind, target, reason = plan_console_launch()
    if reason != "checkout":
        return None
    return (
        f"note: this r4 runs from a checkout; r4 console runs its source ({target}).\n"
        f"      For the binary just installed: {BIN_PATH_ENV}={user_bin_path()} r4 console"
    )


@command
class TuiCommand(CommandHandler):
    """``r4 tui`` — TUI binary management."""

    name = "tui"
    help = "Install or inspect the compiled OpenTUI binary"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        sub = p.add_subparsers(dest="tui_command")
        sub.add_parser("install", help="Download + verify the binary for this host")
        sub.add_parser("status", help="Show which binary would be used")
        return p

    def handle(self, args: Namespace) -> int:
        sub = getattr(args, "tui_command", None)
        if sub == "install":
            return install_binary()
        if sub == "status":
            return self._status()
        print("usage: r4 tui {install|status}", file=sys.stderr)
        return 1

    def _status(self) -> int:
        from .console import plan_console_launch  # console imports this module

        env = os.environ.get(BIN_PATH_ENV)
        asset = tui_asset_name() or "unsupported-platform"
        print(f"platform asset : {asset}")
        print(f"{BIN_PATH_ENV}      : {env or '(unset)'}")
        print(
            f"user bin       : {user_bin_path()}"
            f" ({'present' if user_bin_path().is_file() else 'missing'})"
        )
        print(f"checkout src   : {_checkout_status()}")
        kind, target, reason = plan_console_launch()
        print(f"r4 console runs: {kind} {target or '(nothing found)'} ({reason})")
        print(f"version pin    : {os.environ.get(VERSION_ENV) or '(newest, incl. prereleases)'}")
        print(f"dist base      : {dist_base_url(os.environ.get(VERSION_ENV))}")
        signing = "minisign, pinned" if MINISIGN_PUBLIC_KEY else "none pinned (sha256 only)"
        print(f"release key    : {signing}")
        return 0


def _checkout_status() -> str:
    from .console import checkout_blocker, checkout_package_dir, deps_state

    pkg = checkout_package_dir()
    if pkg is None:
        return "(r4 is not imported from a checkout)"
    blocker = checkout_blocker(pkg)
    if blocker is not None:
        return f"{pkg} (not runnable: {blocker} — make tui-deps)"
    state = deps_state(pkg)
    tail = "" if state == "ok" else f"; node_modules {state} — make tui-deps"
    return f"{pkg} (runnable{tail})"
