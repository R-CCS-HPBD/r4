"""ADR-101 local tool sandbox: pure parts (argv builders, SBPL text, env scrub, the context).

Nothing here needs bubblewrap or sandbox-exec; the real-backend tests are in
``test_sandbox_bwrap.py`` and ``test_sandbox_seatbelt.py``.
"""

from __future__ import annotations

import gc
import os
import shutil
import subprocess
import sys
import tempfile
import weakref
from pathlib import Path

import pytest

from r4.companion.sandbox import (
    BwrapBackend,
    SandboxContext,
    SandboxError,
    SandboxPolicy,
    SandboxSetting,
    SeatbeltBackend,
    UnavailableBackend,
    UserSandboxPolicy,
    backend_for_platform,
    deny_read_paths,
    scrub_env,
    seatbelt_profile,
    set_current_sandbox,
    wrap_spawn,
)
from r4.companion.sandbox import backends as backends_mod
from r4.companion.sandbox import policy as policy_mod
from r4.companion.sandbox import selftest as selftest_mod
from r4.companion.sandbox.backends import NETNS_INIT, darwin_path, socket_mask_paths
from r4.companion.sandbox.context import reap_stale_sessions
from r4.companion.sandbox.policy import PROXY_ENV_NAMES, install_paths
from r4.companion.sandbox.proxy import ProxyEndpoint
from r4.companion.web_access import make_web_access

ON = SandboxSetting("on", "", "slash")
OFF = SandboxSetting("off", "", "default_off")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A workspace whose scratch HOME and r4 config dir sit INSIDE the writable root."""
    ws = tmp_path / "ws"
    (ws / "home" / ".ssh").mkdir(parents=True)
    (ws / "home" / ".netrc").write_text("machine x password y")
    (ws / ".git" / "hooks").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]")
    (ws / "xdg" / "r4").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(ws / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(ws / "xdg"))
    return ws


def _policy(ws: Path, tmp_path: Path, **kw) -> SandboxPolicy:
    session = tmp_path / "base" / "s"
    (session / "tmp").mkdir(parents=True)
    (session / "empty").touch()
    fields = {
        "write_roots": (str(ws), str(session / "tmp")),
        "deny_read": deny_read_paths(),
        "tmp_dir": str(session / "tmp"),
        "session_dir": str(session),
        "base_dir": str(tmp_path / "base"),
        "empty_file": str(session / "empty"),
    }
    fields.update(kw)
    return SandboxPolicy(**fields)


def _bwrap(monkeypatch) -> BwrapBackend:
    backend = BwrapBackend(executable="/usr/bin/bwrap")
    monkeypatch.setattr(backend, "supports_disable_userns", lambda: True)
    return backend


def _index(argv: list[str], *items: str) -> int:
    for i in range(len(argv) - len(items) + 1):
        if tuple(argv[i : i + len(items)]) == items:
            return i
    raise AssertionError(f"{items} not in {argv}")


def test_bwrap_argv_mount_order(home, tmp_path, monkeypatch):
    policy = _policy(home, tmp_path)
    argv = _bwrap(monkeypatch).build_argv(["echo", "hi"], policy, str(home))
    ssh, netrc = str(home / "home" / ".ssh"), str(home / "home" / ".netrc")
    root = _index(argv, "--ro-bind", "/", "/")
    tmp = _index(argv, "--bind", policy.tmp_dir, "/tmp")
    real_tmp = _index(argv, "--bind", policy.tmp_dir, policy.tmp_dir)
    write = _index(argv, "--bind", str(home), str(home))
    deny = _index(argv, "--tmpfs", ssh)
    deny_file = _index(argv, "--ro-bind", policy.empty_file, netrc)
    guard = _index(argv, "--bind", str(home / ".git"), str(home / ".git"))
    hooks = _index(argv, "--ro-bind", str(home / ".git" / "hooks"), str(home / ".git" / "hooks"))
    assert root == 1 and root < tmp < real_tmp < write < guard < hooks < deny < deny_file
    assert _index(argv, "--tmpfs", str(home / "xdg" / "r4")) > write
    assert argv[-3:] == ["--", "echo", "hi"]
    assert _index(argv, "--chdir", str(home)) > _index(argv, "--unshare-net")


def test_bwrap_rename_guards_pin_every_parent_of_protected_and_denied_paths(
    home, tmp_path, monkeypatch
):
    """SEC-1/2: a rename of .git or of a masked path's parent must not move the protection."""
    (home / ".r4").mkdir()
    (home / ".r4" / "mcp.json").write_text("{}")
    (home / "deep" / "er").mkdir(parents=True)
    (home / "deep" / "er" / ".env").write_text("TOPSECRET")
    policy = _policy(home, tmp_path, deny_read=(*deny_read_paths(), str(home / "deep" / "er")))
    argv = _bwrap(monkeypatch).build_argv(["true"], policy, None)
    write = _index(argv, "--bind", str(home), str(home))
    mcp = str(home / ".r4" / "mcp.json")
    first_protection = _index(
        argv, "--ro-bind", str(home / ".git" / "config"), str(home / ".git" / "config")
    )
    for directory in (".git", ".r4", "home", "xdg", "deep"):
        path = str(home / directory)
        assert write < _index(argv, "--bind", path, path) < first_protection
    assert first_protection < _index(argv, "--ro-bind", mcp, mcp)
    assert first_protection < _index(argv, "--tmpfs", str(home / "deep" / "er"))


def _git_dir(path: Path) -> None:
    (path / "hooks").mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    (path / "config").write_text("[core]\n")


def test_bwrap_protects_submodules_and_linked_worktree_pointers(home, tmp_path, monkeypatch):
    """SEC-1: .git/modules/*, .git/worktrees/* and submodule .git files stay read-only."""
    _git_dir(home / ".git" / "modules" / "sub")
    _git_dir(home / ".git" / "modules" / "a" / "b")  # a module name with a slash
    (home / ".git" / "worktrees" / "w").mkdir(parents=True)
    (home / ".git" / "worktrees" / "w" / "commondir").write_text("../..\n")
    (home / ".git" / "worktrees" / "w" / "gitdir").write_text("/elsewhere/.git\n")
    (home / ".gitmodules").write_text('[submodule "sub"]\n\tpath = third/sub\n')
    (home / "third" / "sub").mkdir(parents=True)
    (home / "third" / "sub" / ".git").write_text("gitdir: ../../.git/modules/sub\n")
    argv = _bwrap(monkeypatch).build_argv(["true"], _policy(home, tmp_path), None)
    for rel in (
        ".git/modules/sub/config",
        ".git/modules/sub/hooks",
        ".git/modules/a/b/config",
        ".git/worktrees/w/commondir",
        ".git/worktrees/w/gitdir",
        "third/sub/.git",
    ):
        path = str(home / rel)
        assert _index(argv, "--ro-bind", path, path)
    for rel in (".git/modules", ".git/modules/a/b", ".git/worktrees/w", "third", "third/sub"):
        path = str(home / rel)
        assert _index(argv, "--bind", path, path)


def test_bwrap_linked_worktree_dot_git_file_is_read_only(tmp_path, monkeypatch):
    ws = tmp_path / "wt"
    ws.mkdir()
    (ws / ".git").write_text("gitdir: /main/.git/worktrees/wt\n")
    dot_git = str(ws / ".git")
    argv = _bwrap(monkeypatch).build_argv(["true"], _policy(ws, tmp_path), None)
    assert _index(argv, "--ro-bind", dot_git, dot_git)


def test_bwrap_pins_a_missing_hooks_dir_read_only(tmp_path, monkeypatch):
    ws = tmp_path / "repo"
    (ws / ".git").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]\n")
    hooks, git = str(ws / ".git" / "hooks"), str(ws / ".git")
    argv = _bwrap(monkeypatch).build_argv(["true"], _policy(ws, tmp_path), None)
    pin = _index(argv, "--tmpfs", hooks, "--remount-ro", hooks)
    assert _index(argv, "--bind", git, git) < pin
    plain = tmp_path / "plain"
    plain.mkdir()
    other = _bwrap(monkeypatch).build_argv(["true"], _policy(plain, tmp_path / "p"), None)
    assert "--remount-ro" not in other


def test_bwrap_keeps_r4_install_inside_a_root_read_only(home, tmp_path, monkeypatch):
    """SEC-5: r4's own venv / source inside the workspace cannot be changed from inside."""
    venv = home / "proj" / ".venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(policy_mod, "install_paths", lambda: (str(venv), "/usr"))
    argv = _bwrap(monkeypatch).build_argv(["true"], _policy(home, tmp_path), None)
    write = _index(argv, "--bind", str(home), str(home))
    guard = _index(argv, "--bind", str(home / "proj"), str(home / "proj"))
    assert write < guard < _index(argv, "--ro-bind", str(venv), str(venv))
    assert "/usr" not in argv[: argv.index("--")]
    assert os.path.realpath(sys.prefix) in install_paths()
    assert str(Path(policy_mod.__file__).resolve().parents[3]) in install_paths()


def test_bwrap_chdir_refuses_an_invisible_cwd(home, tmp_path, monkeypatch):
    """COR-3: a hidden or masked cwd is refused, never silently swapped for the root."""
    backend = _bwrap(monkeypatch)
    elsewhere = tmp_path / "elsewhere"  # under /tmp, outside every root: hidden
    elsewhere.mkdir()
    for index, cwd in enumerate((home / "home" / ".ssh", elsewhere, tmp_path / "missing")):
        policy = _policy(home, tmp_path / f"p{index}")
        with pytest.raises(SandboxError) as info:
            backend.build_argv(["true"], policy, str(cwd))
        assert info.value.code == "sandbox.refused"


def test_bwrap_chdir_tmp_is_the_sandbox_tmp_and_none_falls_back(home, tmp_path, monkeypatch):
    backend = _bwrap(monkeypatch)
    argv = backend.build_argv(["true"], _policy(home, tmp_path), "/tmp")
    assert argv[argv.index("--chdir") + 1] == "/tmp"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    argv = backend.build_argv(["true"], _policy(home, tmp_path / "n"), None)
    assert argv[argv.index("--chdir") + 1] == str(home)


def test_socket_masks_cover_resolvers_only_with_an_isolated_network():
    """SEC-6: name-service sockets are a DNS channel out of the namespace."""
    isolated, host = socket_mask_paths("isolated"), socket_mask_paths("host")
    assert "/run/systemd/resolve" in isolated and "/run/systemd/resolve" not in host
    for path in ("/run/.heim_org.h5l.kcm-socket", "/run/gssproxy.sock", "/run/pcscd"):
        assert path in isolated and path in host


def test_bwrap_dies_with_parent_only_when_the_policy_asks(home, tmp_path, monkeypatch):
    backend = _bwrap(monkeypatch)
    argv = backend.build_argv(["true"], _policy(home, tmp_path, die_with_parent=True), None)
    assert _index(argv, "--die-with-parent") < argv.index("--")


def test_bwrap_masks_only_existing_paths_and_never_dev_null(home, tmp_path, monkeypatch):
    argv = _bwrap(monkeypatch).build_argv(["true"], _policy(home, tmp_path), None)
    assert str(home / "home" / ".aws") not in argv
    assert "/dev/null" not in argv[: argv.index("--")]
    assert "--new-session" not in argv and "--die-with-parent" not in argv


def test_bwrap_socket_masks_come_after_protected_binds(home, tmp_path, monkeypatch):
    run_user = home / "runuser"  # visible: inside the writable root, not hidden by /tmp
    run_user.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_user))
    argv = _bwrap(monkeypatch).build_argv(["true"], _policy(home, tmp_path), None)
    config = str(home / ".git" / "config")
    assert _index(argv, "--tmpfs", str(run_user)) > _index(argv, "--ro-bind", config, config)


def test_bwrap_namespaces_and_flags(home, tmp_path, monkeypatch):
    backend = _bwrap(monkeypatch)
    argv = backend.build_argv(["true"], _policy(home, tmp_path), None)
    for flag in ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts"):
        assert flag in argv
    assert "--unshare-cgroup-try" in argv and "--disable-userns" in argv
    assert _index(argv, "--cap-drop", "ALL") > 0
    monkeypatch.setattr(backend, "supports_disable_userns", lambda: False)
    host = backend.build_argv(["true"], _policy(home, tmp_path / "h", network="host"), None)
    assert "--unshare-net" not in host and "--disable-userns" not in host


def test_bwrap_proxied_command_runs_under_the_netns_bridge(home, tmp_path, monkeypatch):
    sock = str(tmp_path / "base" / "s" / "proxy" / "p.sock")
    (tmp_path / "base" / "s" / "proxy").mkdir(parents=True)
    policy = _policy(home, tmp_path, proxy_socket=sock)
    argv = _bwrap(monkeypatch).build_argv(["curl", "x"], policy, None)
    tail = argv[argv.index("--") + 1 :]
    assert tail == [sys.executable, "-I", NETNS_INIT, sock, "3128", "--", "curl", "x"]
    proxy_dir = os.path.dirname(sock)
    assert _index(argv, "--ro-bind", proxy_dir, proxy_dir) < _index(
        argv, "--bind", str(home), str(home)
    )


def test_bwrap_missing_reports_install_fix(monkeypatch):
    monkeypatch.setattr(backends_mod.shutil, "which", lambda name: None)
    ok, reason = BwrapBackend().probe()
    assert not ok and "install bubblewrap" in reason


def test_bwrap_failure_reason_names_ubuntu_apparmor_fix(monkeypatch):
    monkeypatch.setattr(backends_mod, "_read_text", lambda path: "1")
    reason = backends_mod.bwrap_failure_reason("setting up uid map: Permission denied")
    assert "apparmor_parser" in reason and "uid map" in reason


def test_backend_for_platform():
    assert isinstance(backend_for_platform("linux"), BwrapBackend)
    assert isinstance(backend_for_platform("darwin"), SeatbeltBackend)
    other = backend_for_platform("win32")
    assert isinstance(other, UnavailableBackend) and not other.probe()[0]


# ── Seatbelt profile text (pure; runs on Linux too) ────────────────────────


def test_seatbelt_profile_text_and_params(tmp_path):
    policy = SandboxPolicy(
        write_roots=("/Users/u/proj",),
        deny_read=("/Users/u/.ssh", "/Users/u/.config/r4"),
        tmp_dir="/tmp/r4-sandbox-501/s-1/tmp",
        base_dir="/tmp/r4-sandbox-501",
        proxy_port=0,
    )
    text, params = seatbelt_profile(
        policy, protected=("/Users/u/proj/.git/hooks",), user_temp="/var/folders/x/T"
    )
    assert text.startswith("(version 1)\n(allow default)\n(deny file-write*)\n")
    expected = {
        "WRITE_0": "/Users/u/proj",
        "WRITE_1": "/private/tmp/r4-sandbox-501/s-1/tmp",
        "WRITE_2": "/private/var/folders/x/T",
        "DENY_1": "/Users/u/.config/r4",
        "PROTECT_0": "/Users/u/proj/.git/hooks",
    }
    assert {key: params[key] for key in expected} == expected
    order = ['(subpath (param "WRITE_0"))', '(param "PROTECT_0")', '(param "DENY_0")']
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)
    flags = ("(deny network*)" in text, "network-outbound" in text, "/Users/u" in text)
    assert flags == (True, False, False)


def test_seatbelt_profile_allows_only_the_proxy_port_when_web_open():
    policy = SandboxPolicy(write_roots=("/w",), proxy_port=54321)
    text, _ = seatbelt_profile(policy)
    assert '(allow network-outbound (remote ip "localhost:54321"))' in text
    assert text.index("(deny network*)") < text.index("network-outbound")


def test_seatbelt_profile_host_network_leaves_network_alone():
    text, _ = seatbelt_profile(SandboxPolicy(write_roots=("/w",), network="host"))
    assert "network" not in text


def test_seatbelt_argv_shape(tmp_path):
    backend = SeatbeltBackend()
    argv = backend.build_argv(
        ["/bin/sh", "-c", "ls"], SandboxPolicy(write_roots=(str(tmp_path),)), None
    )
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert argv[3] == "-D" and argv[4].startswith("WRITE_0=")
    assert argv[-3:] == ["/bin/sh", "-c", "ls"]


def test_seatbelt_rename_guards_are_literal_write_denies(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".git" / "hooks").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]\n")
    argv = SeatbeltBackend().build_argv(["true"], SandboxPolicy(write_roots=(str(ws),)), None)
    profile = argv[2]
    assert '(deny file-write* (literal (param "GUARD_0")))' in profile
    assert f"GUARD_0={darwin_path(str(ws / '.git'))}" in argv
    assert profile.index("PROTECT_0") < profile.index("GUARD_0")


def test_darwin_path_resolves_private_prefixes():
    assert darwin_path("/tmp/x") == "/private/tmp/x"
    assert darwin_path("/var/folders/a") == "/private/var/folders/a"
    assert darwin_path("/private/tmp") == "/private/tmp"
    assert darwin_path("/Users/u/tmp") == "/Users/u/tmp"


# ── env scrub and deny list ─────────────────────────────────────────────────


def test_scrub_env_drops_credentials_keeps_gateway_and_env_keep():
    env = {
        "OPENAI_API_KEY": "a",
        "MY_SERVICE_API_KEY": "b",
        "R4_SESSION_TOKEN": "c",
        "GITHUB_TOKEN": "d",
        "DB_PASSWORD": "e",
        "CLIENT_SECRET_X": "f",
        "AWS_ACCESS_KEY_ID": "g",
        "AWS_SECRET_ACCESS_KEY": "h",
        "SSH_AUTH_SOCK": "/s",
        "GPG_AGENT_INFO": "i",
        "R4_VENUE_SESSION_TOKEN_FILE": "/f",
        "R4_GATEWAY_TOKEN": "keep",
        "R4_GATEWAY_URL": "keep",
        "HF_TOKEN": "user-kept",
        "PATH": "/bin",
    }
    out = scrub_env(env, keep=["HF_TOKEN"])
    assert out == {
        "R4_GATEWAY_TOKEN": "keep",
        "R4_GATEWAY_URL": "keep",
        "HF_TOKEN": "user-kept",
        "PATH": "/bin",
    }


def test_scrub_env_drops_the_x11_display_unless_kept():
    """SEC-7: hatch runtimes share the host network, where the X server is reachable."""
    env = {"DISPLAY": ":0", "XAUTHORITY": "/home/u/.Xauthority", "LANG": "C"}
    assert scrub_env(env) == {"LANG": "C"}
    assert scrub_env(env, keep=["DISPLAY"]) == {"DISPLAY": ":0", "LANG": "C"}


def test_deny_list_follows_relocated_credential_stores(tmp_path):
    """SEC-7/8: stores moved by the environment, and the X authority files, are denied."""
    env = {
        "GH_CONFIG_DIR": str(tmp_path / "gh"),
        "DOCKER_CONFIG": str(tmp_path / "docker"),
        "KUBECONFIG": os.pathsep.join([str(tmp_path / "k1"), str(tmp_path / "k2")]),
        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "aws-creds"),
        "CLOUDSDK_CONFIG": str(tmp_path / "gcloud"),
        "XAUTHORITY": str(tmp_path / "xauth"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    paths = deny_read_paths(env=env)
    for name in ("gh", "docker", "k1", "k2", "aws-creds", "gcloud", "xauth", "xdg/gh"):
        assert os.path.realpath(tmp_path / name) in paths
    assert str(Path.home().resolve() / ".Xauthority") in paths


def test_deny_list_is_computed_from_home_and_config_dir_now(home):
    paths = deny_read_paths(UserSandboxPolicy(extra_deny_read_paths=("~/private",)))
    assert str(home / "xdg" / "r4") in paths
    assert str(home / "home" / ".ssh") in paths
    assert str(home / "home" / "private") in paths
    assert str(home / "home" / "Library" / "Keychains") in paths


# ── the context with a fake backend ─────────────────────────────────────────


class FakeBackend:
    name = "fake"

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[list[str], SandboxPolicy]] = []

    def probe(self):
        return (self.ok, "" if self.ok else "no fake backend here")

    def build_argv(self, command, policy, cwd):
        self.calls.append((list(command), policy))
        return ["FAKE", *command]


class FakeProxy:
    def __init__(self, web, unix_path=""):
        self.web, self.unix_path, self.stopped = web, unix_path, False

    def start(self):
        return ProxyEndpoint(port=4242, nonce="n")

    def stop(self):
        self.stopped = True


@pytest.fixture
def selftest_ok(monkeypatch):
    calls = []

    def fake(context, policy=None):
        calls.append(policy)
        return True, ""

    monkeypatch.setattr(selftest_mod, "run_self_test", fake)
    return calls


def _ctx(ws, setting=ON, web="none", backend=None, **kw):
    access = make_web_access(web, ["example.org"] if web == "allowlist" else (), source="flag")
    return SandboxContext(
        setting=lambda: setting,
        web_access=lambda: access,
        workspace_root=ws,
        backend=backend or FakeBackend(),
        proxy_factory=FakeProxy,
        **kw,
    )


def test_wrap_off_is_a_pass_through(tmp_path):
    env = {"OPENAI_API_KEY": "k"}
    spec = _ctx(tmp_path, setting=OFF).wrap("echo $X", shell=True, cwd=None, env=env)
    assert (spec.argv, spec.shell, spec.sandboxed, spec.backend) == ("echo $X", True, False, "")
    assert spec.env is env


def test_wrap_inherited_is_a_pass_through_marked_sandboxed(tmp_path, monkeypatch):
    """COR-8: ``backend`` is the outer sandbox's backend name, never "inherited"."""
    monkeypatch.delenv("R4_SANDBOXED", raising=False)
    ctx = _ctx(tmp_path, setting=SandboxSetting("on", "", "inherited"))
    spec = ctx.wrap(["ls"], shell=False, cwd=None, env={"R4_SANDBOXED": "bwrap"})
    assert (spec.argv, spec.sandboxed, spec.backend) == (["ls"], True, "bwrap")
    odd = ctx.wrap(["ls"], shell=False, cwd=None, env={"R4_SANDBOXED": "weird"})
    assert odd.backend == ""


def test_wrap_on_shell_becomes_bin_sh_and_env_is_scrubbed(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path)
    env = {"OPENAI_API_KEY": "k", "R4_GATEWAY_TOKEN": "g", "HTTPS_PROXY": "http://corp:8080"}
    spec = ctx.wrap("echo hi", shell=True, cwd=str(tmp_path), env=env)
    try:
        policy = ctx.backend.calls[0][1]
        seen = {
            "argv": spec.argv,
            "shell": spec.shell,
            "sandboxed": spec.sandboxed,
            "backend": spec.backend,
            "openai": spec.env.get("OPENAI_API_KEY"),
            "gateway": spec.env.get("R4_GATEWAY_TOKEN"),
            "marker": spec.env.get("R4_SANDBOXED"),
            "tmpdir": spec.env.get("TMPDIR"),
            "proxy_vars": sorted(set(PROXY_ENV_NAMES) & set(spec.env)),
            "root_writable": str(tmp_path.resolve()) in policy.write_roots,
            "proxy_port": policy.proxy_port,
        }
        assert seen == {
            "argv": ["FAKE", "/bin/sh", "-c", "echo hi"],
            "shell": False,
            "sandboxed": True,
            "backend": "fake",
            "openai": None,
            "gateway": "g",
            "marker": "fake",
            "tmpdir": ctx.session_dirs().tmp,
            "proxy_vars": [],
            "root_writable": True,
            "proxy_port": 0,
        }
    finally:
        ctx.close()


def test_wrap_list_with_shell_keeps_posix_semantics(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path)
    try:
        spec = ctx.wrap(["echo $0", "zero"], shell=True, cwd=None, env={})
        assert spec.argv == ["FAKE", "/bin/sh", "-c", "echo $0", "zero"]
    finally:
        ctx.close()


def test_wrap_web_open_sets_proxy_env(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path, web="allowlist")
    try:
        spec = ctx.wrap(["curl", "x"], shell=False, cwd=None, env={})
        assert spec.env["HTTPS_PROXY"] == "http://r4:n@127.0.0.1:4242"
        assert spec.env["no_proxy"] == "localhost,127.0.0.1,::1"
        assert ctx.backend.calls[0][1].proxy_port == 4242
    finally:
        ctx.close()


def test_wrap_refuses_when_backend_unavailable(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path, backend=FakeBackend(ok=False))
    try:
        with pytest.raises(SandboxError) as info:
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert info.value.code == "sandbox.unavailable"
        assert ctx.status()["available"] is False
    finally:
        ctx.close()


def test_wrap_refuses_when_self_test_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(selftest_mod, "run_self_test", lambda c, p=None: (False, "net open"))
    ctx = _ctx(tmp_path)
    try:
        with pytest.raises(SandboxError) as info:
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert info.value.code == "sandbox.self_test_failed" and "net open" in info.value.reason
        assert ctx.status()["reason"] == "net open"
    finally:
        ctx.close()


def test_agent_workspace_root_wins_over_session_root(tmp_path, selftest_ok):
    from r4.companion.agent_workspace import AgentWorkspace, agent_workspace

    agent_root = tmp_path / "agent"
    agent_root.mkdir()
    ctx = _ctx(tmp_path / "session")
    try:
        with agent_workspace(AgentWorkspace.of(agent_root)):
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert ctx.backend.calls[0][1].write_roots[0] == str(agent_root.resolve())
    finally:
        ctx.close()


def test_status_shape_and_close_removes_session_dir(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path, web="public")
    ctx.wrap(["ls"], shell=False, cwd=None, env={})
    status = ctx.status()
    assert set(status) == {
        "mode",
        "source",
        "backend",
        "available",
        "reason",
        "tmp_dir",
        "write_roots",
        "network",
    }
    assert status["network"] == "isolated (web: public)" and status["available"] is True
    session = ctx.session_dirs().session
    proxy = ctx._proxy
    ctx.close()
    assert not os.path.exists(session) and proxy.stopped


def test_session_dirs_are_private(tmp_path):
    ctx = _ctx(tmp_path)
    try:
        dirs = ctx.session_dirs()
        for path in (dirs.base, dirs.tmp, dirs.proxy):
            assert oct(os.stat(path).st_mode & 0o777) == "0o700"
        assert os.path.getsize(dirs.empty_file) == 0
    finally:
        ctx.close()


def test_write_root_is_fixed_at_open_never_the_per_call_cwd(tmp_path, monkeypatch, selftest_ok):
    """COR-1/SEC-2: the agent's cwd (here: anywhere) never becomes writable."""
    opened, other = tmp_path / "opened", tmp_path / "other"
    opened.mkdir()
    other.mkdir()
    monkeypatch.chdir(opened)
    ctx = _ctx(None)
    try:
        ctx.wrap(["ls"], shell=False, cwd=str(other), env={})
        roots = ctx.backend.calls[0][1].write_roots
        assert roots[0] == str(opened.resolve()) and str(other.resolve()) not in roots
    finally:
        ctx.close()


@pytest.mark.parametrize(
    "root", [lambda: Path.home(), lambda: Path.home().parent, lambda: "/tmp", lambda: "/"]
)
def test_root_at_or_above_home_or_tmp_is_refused(root, selftest_ok):
    """SEC-2/SEC-3: such a root would expose shell startup files or the host /tmp sockets."""
    ctx = _ctx(root())
    try:
        with pytest.raises(SandboxError) as info:
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert info.value.code == "sandbox.refused" and ctx.backend.calls == []
    finally:
        ctx.close()


def test_extra_write_path_over_the_sandbox_base_is_refused(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path)
    try:
        base = ctx.session_dirs().base
        ctx._user = UserSandboxPolicy(extra_write_paths=(base,))
        with pytest.raises(SandboxError, match="temp directory"):
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
    finally:
        ctx.close()


def test_refusal_starts_no_proxy(tmp_path, selftest_ok):
    """COR-7: the proxy starts only after the availability check passed."""
    ctx = _ctx(tmp_path, web="public", backend=FakeBackend(ok=False))
    try:
        with pytest.raises(SandboxError):
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert ctx._proxy is None
    finally:
        ctx.close()


class BrokenProxy(FakeProxy):
    def start(self):
        raise RuntimeError("sandbox proxy did not start: FileNotFoundError()")


def test_proxy_start_failure_is_a_sandbox_error(tmp_path, selftest_ok):
    """COR-6: spawn sites catch SandboxError only; nothing else may escape wrap()."""
    ctx = _ctx(tmp_path, web="public")
    ctx._proxy_factory = BrokenProxy
    try:
        with pytest.raises(SandboxError) as info:
            ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert info.value.code == "sandbox.unavailable" and ctx._proxy is None
    finally:
        ctx.close()


def test_session_dir_removed_under_the_context_is_rebuilt(tmp_path, selftest_ok):
    """COR-6: tmp cleaners may remove it; the next spawn gets a fresh one."""
    ctx = _ctx(tmp_path)
    try:
        ctx.wrap(["ls"], shell=False, cwd=None, env={})
        shutil.rmtree(ctx.session_dirs().session)
        ctx.wrap(["ls"], shell=False, cwd=None, env={})
        assert os.path.isdir(ctx.backend.calls[-1][1].tmp_dir)
    finally:
        ctx.close()


def test_context_is_collectable_and_its_dir_outlives_it(tmp_path):
    """COR-9: nothing keeps a context alive until exit. Review LC-3: its
    directory is the /tmp of sandboxed children that may still run (a
    detached shell, a language server), so it goes at close() or exit."""
    ctx = _ctx(tmp_path)
    session = ctx.session_dirs().session
    finalizer = ctx._finalizer
    ref = weakref.ref(ctx)
    del ctx
    gc.collect()
    assert ref() is None and os.path.isdir(session)
    assert finalizer is not None and finalizer.alive and finalizer.atexit
    finalizer()  # what process exit does
    assert not os.path.exists(session)


def test_stale_session_dirs_of_dead_owners_are_reaped(tmp_path, monkeypatch):
    """COR-4: a SIGKILLed launcher leaves its dir; the next session removes it."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    dead = subprocess.Popen(["true"])
    dead.wait()
    base = tmp_path / f"r4-sandbox-{os.getuid()}"
    (base / "s-dead").mkdir(parents=True)
    (base / "s-dead" / "owner").write_text(str(dead.pid))
    (base / "s-unknown").mkdir()
    (base / "s-mine").mkdir()
    (base / "s-mine" / "owner").write_text(str(os.getpid()))
    ctx = _ctx(tmp_path)
    try:
        ctx.session_dirs()
        assert not (base / "s-dead").exists()
        assert (base / "s-unknown").exists() and (base / "s-mine").exists()
        assert reap_stale_sessions(str(base)) == []
    finally:
        ctx.close()


def test_launcher_context_dies_with_its_parent(tmp_path, monkeypatch):
    """COR-4: the launcher's wrapper carries --die-with-parent; spawn sites do not."""
    from r4.companion.sandbox.launch import build_context, parse_args

    monkeypatch.chdir(tmp_path)
    args, _ = parse_args(["--policy", "p", "--", "x"])
    launcher = build_context(args, {}, UserSandboxPolicy())
    plain = _ctx(tmp_path)
    try:
        assert launcher.policy_for().die_with_parent is True
        assert plain.policy_for().die_with_parent is False
    finally:
        launcher.close()
        plain.close()


class _ReportsClean:
    """A "backend" whose probe always reports a clean result, sandbox or not."""

    name = "reports-clean"

    def build_argv(self, command, policy, cwd):
        line = '{"write": "blocked", "read": "blocked", "net": "isolated"}'
        return [sys.executable, "-c", f"print({line!r})"]


def test_self_test_fails_when_no_probe_target_is_outside_the_roots():
    """COR-5: a skipped write check is a failure, never a pass."""
    selftest_mod.clear_cache()
    package = str(Path(selftest_mod.__file__).resolve().parent)
    roots = (str(Path.home().resolve()), os.path.realpath(sys.prefix), package)
    policy = SandboxPolicy(write_roots=roots, network="host")
    ok, reason = selftest_mod.self_test_backend(_ReportsClean(), policy)
    assert not ok and "no probe location" in reason


def test_self_test_fails_when_the_sentinel_cannot_be_written(tmp_path, monkeypatch):
    selftest_mod.clear_cache()
    monkeypatch.setattr(selftest_mod, "_make_sentinel", lambda token: "")
    policy = SandboxPolicy(write_roots=(str(tmp_path),), network="host")
    ok, reason = selftest_mod.self_test_backend(_ReportsClean(), policy)
    assert not ok and "sentinel" in reason


def test_wrap_spawn_ambient_default_is_off(monkeypatch):
    for name in ("R4_SANDBOX", "R4_SANDBOXED", "R4_VENUE_SESSION_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    set_current_sandbox(None)
    spec = wrap_spawn("ls", shell=True, cwd=None, env={"A": "1"})
    assert (spec.argv, spec.sandboxed) == ("ls", False)


def test_wrap_spawn_uses_the_installed_context(tmp_path, selftest_ok):
    ctx = _ctx(tmp_path)
    set_current_sandbox(ctx)
    try:
        assert wrap_spawn(["ls"], shell=False, cwd=None, env={}).argv == ["FAKE", "ls"]
    finally:
        set_current_sandbox(None)
        ctx.close()


# ── 2026-10-31: the first Mac run ──────────────────────────────────────────


def test_seatbelt_declares_the_user_temp_dir_writable(monkeypatch):
    monkeypatch.setattr(backends_mod, "darwin_user_temp", lambda: "/private/var/folders/x/T")
    assert backends_mod.extra_write_roots(SeatbeltBackend()) == ("/private/var/folders/x/T",)
    assert backends_mod.extra_write_roots(BwrapBackend(executable="/usr/bin/bwrap")) == ()


def test_self_test_never_targets_a_dir_the_backend_leaves_writable(tmp_path, monkeypatch):
    # On macOS a HOME under the per-user temp dir is writable by the profile;
    # a probe write there succeeds and the self-test failed a working sandbox.
    home = tmp_path / "user-temp" / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    policy = _policy(tmp_path / "ws", tmp_path)
    target = selftest_mod._write_target(policy, "tok", (str(tmp_path / "user-temp"),))
    assert target
    assert not target.startswith(os.path.realpath(tmp_path / "user-temp"))
