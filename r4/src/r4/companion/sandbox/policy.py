"""What a sandboxed process may touch: paths and environment (ADR-101 D-5).

Reads: everything minus a deny list (the r4 config directory and the usual
credential stores, also where the environment relocates them), computed
against ``Path.home()``, ``os.environ`` and ``config._resolve_config_dir()``
at call time, never a copied or re-bound config directory. Writes: the
workspace root, a per-session temp directory and the user's
``extra_write_paths``; a root equal to or above ``$HOME`` or a temp
directory is refused (:func:`refused_root`). Inside the roots, what would let
a command reach outside the sandbox later stays read-only
(:func:`write_protection`): git hooks and config (also of submodules and
linked worktrees, and a ``.git`` file pointing at a git dir), the repo's r4
and MCP config, and r4's own installation. Every directory between a root
and a protected or denied path is a rename guard (:func:`rename_guards`):
the backend pins it, so renaming a parent cannot carry the protection away.
Environment: an allow-by-default copy with credentials removed
(:func:`scrub_env`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .setting import PROFILE_POLICY_KEY, SANDBOX_LAUNCH_ENV

#: Credential stores under ``$HOME`` that a sandboxed process cannot read.
CREDENTIAL_HOME_PATHS: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".config/gcloud",
    ".kube",
    ".docker",
    ".config/gh",
    ".netrc",
    ".git-credentials",
    ".pypirc",
    ".npmrc",
    ".Xauthority",
    ".ICEauthority",
    "Library/Keychains",
)
#: Environment variables that relocate a credential store (a file or a directory).
CREDENTIAL_ENV_PATHS: tuple[str, ...] = (
    "GH_CONFIG_DIR",
    "DOCKER_CONFIG",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "CLOUDSDK_CONFIG",
    "GNUPGHOME",
    "XAUTHORITY",
)
#: Stores under ``$XDG_CONFIG_HOME`` when it is set (``gh`` and ``gcloud`` follow it).
CREDENTIAL_XDG_PATHS: tuple[str, ...] = ("gh", "gcloud")
#: Inside every writable root these stay read-only when they exist.
PROTECTED_WRITE_NAMES: tuple[str, ...] = (
    ".git/hooks",
    ".git/config",
    ".git/config.worktree",
    ".r4/config.toml",
    ".r4/local.toml",
    ".r4/hooks.json",
    ".r4/mcp.json",
    ".r4/mcp-servers.json",
    ".github/mcp.json",
)
_GIT_DIR_ITEMS = ("config", "config.worktree", "hooks")
_LINKED_WORKTREE_ITEMS = ("commondir", "gitdir", "config.worktree")
_EXTRA_PACKAGES = ("r4_contracts",)
_MAX_DEPTH = 4
_GITMODULES_MAX = 256 * 1024
#: Always passed through: hatch runtimes need the gateway binding.
ENV_ALWAYS_KEEP = frozenset({"R4_GATEWAY_TOKEN", "R4_GATEWAY_URL"})
_ENV_DROP_EXACT = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "SSH_AUTH_SOCK",
        "GPG_AGENT_INFO",
        "DISPLAY",
        "XAUTHORITY",
        SANDBOX_LAUNCH_ENV,
    }
)
_ENV_DROP_PARTS = ("SECRET", "PASSWORD", "TOKEN")
#: Proxy variables: the host's are dropped inside, R4's own are set when web is allowed.
PROXY_ENV_NAMES: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@dataclass(frozen=True)
class UserSandboxPolicy:
    """The user's own additions (``config.json`` ``sandbox_policy``; never a repo layer)."""

    extra_write_paths: tuple[str, ...] = ()
    extra_deny_read_paths: tuple[str, ...] = ()
    env_keep: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Any) -> UserSandboxPolicy:
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            _str_tuple(raw.get("extra_write_paths")),
            _str_tuple(raw.get("extra_deny_read_paths")),
            _str_tuple(raw.get("env_keep")),
        )

    def to_mapping(self) -> dict[str, list[str]]:
        return {key: list(value) for key, value in asdict(self).items()}


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def user_sandbox_policy(profile: str | None = None) -> UserSandboxPolicy:
    """The profile's ``sandbox_policy`` from ``config.json``."""
    from ...config import ProfileConfigStore
    from .setting import profile_name

    stored = ProfileConfigStore().load(profile_name(profile))
    return UserSandboxPolicy.from_mapping(stored.get(PROFILE_POLICY_KEY))


def safe_user_policy(profile: str | None = None) -> UserSandboxPolicy:
    """:func:`user_sandbox_policy`; an empty policy when it cannot be read."""
    try:
        return user_sandbox_policy(profile)
    except Exception:  # noqa: BLE001 — a broken sandbox_policy adds nothing
        return UserSandboxPolicy()


# ── paths ───────────────────────────────────────────────────────────────────


def real(path: str | Path) -> str:
    """Absolute real path with ``~`` expanded."""
    return os.path.realpath(os.path.expanduser(str(path)))


def realpaths(paths: Iterable[str | Path]) -> tuple[str, ...]:
    """Real paths, empty entries dropped, de-duplicated in order."""
    seen: dict[str, None] = {}
    for path in paths:
        if str(path).strip():
            seen.setdefault(real(path), None)
    return tuple(seen)


def within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or below it (both real paths)."""
    if path == root:
        return True
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(prefix)


def _env_credential_paths(env: Mapping[str, str]) -> list[str]:
    paths = [env.get(name, "") for name in CREDENTIAL_ENV_PATHS]
    paths += env.get("KUBECONFIG", "").split(os.pathsep)
    xdg = env.get("XDG_CONFIG_HOME", "")
    if xdg:
        paths += [os.path.join(xdg, name) for name in CREDENTIAL_XDG_PATHS]
    return paths


def deny_read_paths(
    user_policy: UserSandboxPolicy | None = None, env: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """D-5 deny list, computed now: the r4 config dir, credential stores, the user's own.

    Credential stores are taken under ``$HOME`` and where ``env`` (default
    ``os.environ``) relocates them: ``GH_CONFIG_DIR``, ``DOCKER_CONFIG``,
    ``KUBECONFIG`` (each entry), the AWS files, ``CLOUDSDK_CONFIG``,
    ``GNUPGHOME``, ``XAUTHORITY`` and ``$XDG_CONFIG_HOME/{gh,gcloud}``.
    """
    from ...config import _resolve_config_dir

    home = Path.home()
    extra = user_policy.extra_deny_read_paths if user_policy else ()
    paths: list[str | Path] = [_resolve_config_dir()]
    paths += [home / name for name in CREDENTIAL_HOME_PATHS]
    paths += _env_credential_paths(os.environ if env is None else env)
    paths += list(extra)
    return realpaths(paths)


def refused_root(root: str, *, home: str, temp_dirs: Sequence[str], base: str = "") -> str:
    """Why ``root`` cannot be a writable root ("" when it can); all paths real.

    A root at or above ``$HOME`` would make shell startup files writable and
    let a rename carry the credential masks away; a root at or above a temp
    directory, or inside the sandbox base, would bring back the host's
    ``/tmp`` sockets and the other sessions' proxy sockets.
    """
    if home and within(home, root):
        return f"{root} is your home directory or contains it"
    for temp in (*temp_dirs, base):
        if temp and within(temp, root):
            return f"{root} is or contains the temp directory {temp}"
    if base and within(root, base):
        return f"{root} is inside the sandbox's own directory {base}"
    return ""


# ── what stays read-only inside the writable roots ─────────────────────────


def _subdirs(path: str) -> list[str]:
    try:
        with os.scandir(path) as entries:
            return sorted(e.path for e in entries if e.is_dir(follow_symlinks=False))
    except OSError:
        return []


def _is_git_dir(path: str) -> bool:
    head = os.path.join(path, "HEAD")
    return os.path.isfile(head) and os.path.lexists(os.path.join(path, "config"))


def module_git_dirs(modules: str, depth: int = 0) -> list[str]:
    """Submodule git dirs under ``<git dir>/modules`` (names may hold ``/``), nested too."""
    if depth > _MAX_DEPTH:
        return []
    found: list[str] = []
    for path in _subdirs(modules):
        if _is_git_dir(path):
            found += [path, *module_git_dirs(os.path.join(path, "modules"), depth + 1)]
        else:
            found += module_git_dirs(path, depth + 1)
    return found


def _git_dir_items(git_dir: str) -> list[str]:
    """``config``, ``config.worktree``, ``hooks`` and the linked-worktree pointers."""
    items = [os.path.join(git_dir, name) for name in _GIT_DIR_ITEMS]
    for linked in _subdirs(os.path.join(git_dir, "worktrees")):
        items += [os.path.join(linked, name) for name in _LINKED_WORKTREE_ITEMS]
    return items


def _gitmodules_path(line: str) -> str:
    key, sep, value = line.partition("=")
    return value.strip().strip('"') if sep and key.strip() == "path" else ""


def submodule_paths(worktree: str) -> list[str]:
    """The ``path =`` entries of ``<worktree>/.gitmodules`` (real, inside the worktree)."""
    try:
        with open(os.path.join(worktree, ".gitmodules"), encoding="utf-8", errors="replace") as fh:
            text = fh.read(_GITMODULES_MAX)
    except OSError:
        return []
    found = []
    for value in filter(None, map(_gitmodules_path, text.splitlines())):
        path = real(os.path.join(worktree, value))
        if path != worktree and within(path, worktree):
            found.append(path)
    return found


def _dot_git_targets(worktree: str) -> list[str]:
    git = os.path.join(worktree, ".git")
    if os.path.isfile(git):  # linked worktree or submodule: the pointer to its git dir
        return [git]
    targets: list[str] = []
    for git_dir in [git, *module_git_dirs(os.path.join(git, "modules"))]:
        targets += _git_dir_items(git_dir)
    return targets


def git_targets(worktree: str, depth: int = 0) -> list[str]:
    """What git reads to run code, for ``worktree`` and its submodules (may not exist)."""
    if depth > _MAX_DEPTH:
        return []
    targets = _dot_git_targets(worktree)
    for sub in submodule_paths(worktree):
        targets += git_targets(sub, depth + 1)
    return targets


def install_paths() -> tuple[str, ...]:
    """r4's own installation: ``sys.prefix``, ``sys.base_prefix``, its packages' import roots."""
    paths = [sys.prefix, sys.base_prefix, str(Path(__file__).resolve().parents[3])]
    for name in _EXTRA_PACKAGES:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is not None and spec.origin:
            paths.append(str(Path(spec.origin).resolve().parents[1]))
    return tuple(path for path in realpaths(paths) if os.path.isdir(path))


def _root_targets(root: str) -> list[str]:
    return [os.path.join(root, name) for name in PROTECTED_WRITE_NAMES] + git_targets(root)


def _missing_hook_dir(path: str) -> bool:
    if os.path.basename(path) != "hooks" or os.path.lexists(path):
        return False
    return os.path.isdir(os.path.dirname(path))


def _installs_inside(write_roots: Sequence[str]) -> list[str]:
    return [p for p in install_paths() if any(within(p, root) for root in write_roots)]


def write_protection(write_roots: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(existing, missing_hook_dirs)`` to keep read-only under the writable roots.

    Existing: the protected names, the git items of each root and its
    submodules, and r4's installation where it lies inside a root. A missing
    ``hooks`` directory of an existing git dir comes back apart: the backend
    pins an empty read-only directory there. Other missing names are not
    pinned (ADR-101 D-5 protects them "when they exist").
    """
    found = [path for root in write_roots for path in _root_targets(root)]
    existing = realpaths(filter(os.path.lexists, [*found, *_installs_inside(write_roots)]))
    missing: list[str] = [path for path in found if _missing_hook_dir(path)]
    return existing, realpaths(missing)


def _innermost_root(path: str, write_roots: Sequence[str]) -> str:
    inside = [root for root in write_roots if root != path and within(path, root)]
    return max(inside, key=len) if inside else ""


def _chain(root: str, path: str) -> list[str]:
    """The directories strictly between ``root`` and ``path``."""
    out: list[str] = []
    parent = os.path.dirname(path)
    while parent != root and within(parent, root) and parent != os.path.dirname(parent):
        out.append(parent)
        parent = os.path.dirname(parent)
    return out


def rename_guards(write_roots: Sequence[str], targets: Iterable[str]) -> tuple[str, ...]:
    """Every directory between a writable root and a target, shallowest first.

    The backend pins these (bwrap binds each onto itself, so renaming it
    gives EBUSY; Seatbelt denies writes to the directory node itself), so a
    rename of a parent cannot carry a mask or a read-only bind away.
    """
    guards: dict[str, None] = {}
    for target in targets:
        root = _innermost_root(target, write_roots)
        for directory in _chain(root, target) if root else ():
            if os.path.isdir(directory) and not os.path.islink(directory):
                guards.setdefault(directory, None)
    return tuple(sorted(guards, key=lambda path: (path.count(os.sep), path)))


# ── environment ─────────────────────────────────────────────────────────────


def _is_credential_name(key: str) -> bool:
    from ...venue import _is_session_credential

    upper = key.upper()
    if _is_session_credential(key) or upper in _ENV_DROP_EXACT:
        return True
    if upper == "API_KEY" or upper.endswith("_API_KEY"):
        return True
    return any(part in upper for part in _ENV_DROP_PARTS)


def scrub_env(env: Mapping[str, str], keep: Iterable[str] = ()) -> dict[str, str]:
    """D-5: an allow-by-default copy with credential variables removed.

    Removed: the venue list (``venue._TOKEN_ENV_NAMES``, ``*SESSION_TOKEN*``),
    ``*_API_KEY``, ``*SECRET*``, ``*PASSWORD*``, ``*TOKEN*``, the AWS key
    names, ``SSH_AUTH_SOCK``, ``GPG_AGENT_INFO``, ``DISPLAY`` and
    ``XAUTHORITY`` (hatch runtimes share the host network, where the X
    server's abstract socket is reachable). Kept regardless:
    ``R4_GATEWAY_TOKEN``, ``R4_GATEWAY_URL`` (hatch is essential) and ``keep``.
    """
    kept = ENV_ALWAYS_KEEP | {name for name in keep if name}
    return {k: v for k, v in env.items() if k in kept or not _is_credential_name(k)}


# ── the effective policy of one spawn ──────────────────────────────────────


@dataclass(frozen=True)
class SandboxPolicy:
    """Everything a backend needs to build one wrapper (all paths real).

    ``die_with_parent``: the wrapper dies with the process that started it
    (the MCP launcher, whose own SIGKILL must not orphan the server). Off at
    the spawn sites, whose ``start_new_session`` + ``killpg`` reach the tree.
    """

    write_roots: tuple[str, ...]
    deny_read: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    tmp_dir: str = ""
    session_dir: str = ""
    base_dir: str = ""
    empty_file: str = ""
    network: str = "isolated"
    proxy_socket: str = ""
    proxy_port: int = 0
    die_with_parent: bool = False

    def digest(self) -> str:
        """Stable digest of what the self-test proves (paths, network class)."""
        body = {
            "write": self.write_roots,
            "deny": self.deny_read,
            "read": self.read_paths,
            "tmp": self.tmp_dir,
            "network": self.network,
        }
        text = json.dumps(body, sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
