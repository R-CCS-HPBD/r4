"""ADR-101: the Linux sandbox for real (bubblewrap). Skipped where bwrap cannot run.

The probe is module level and cached: ``bwrap`` on PATH is not enough, since
unprivileged user namespaces may be off (Ubuntu AppArmor, CI containers).
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from r4.companion.sandbox import (
    EgressProxy,
    SandboxContext,
    SandboxSetting,
    UserSandboxPolicy,
    run_self_test,
)
from r4.companion.sandbox import selftest as selftest_mod
from r4.companion.web_access import REFUSAL_OFF, make_web_access


@functools.lru_cache(maxsize=1)
def bwrap_works() -> bool:
    """bwrap can make a user + network namespace here (cached)."""
    exe = shutil.which("bwrap")
    if not exe or not sys.platform.startswith("linux"):
        return False
    argv = [exe, "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not bwrap_works(), reason="bubblewrap with user namespaces unavailable"
)

ON = SandboxSetting("on", "", "slash")


class _Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server API
        body = f"upstream {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


@pytest.fixture
def upstream():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Workspace (a git repo) with the XDG config and a user-denied dir inside it.

    The scratch HOME sits OUTSIDE the workspace: a root at or above $HOME is
    refused, and the self-test needs a probe target outside every root.
    """
    root = tmp_path / "ws"
    (tmp_path / "home" / ".ssh").mkdir(parents=True)
    (tmp_path / "home" / ".ssh" / "id_x").write_text("PRIVATE-KEY")
    (root / "xdg" / "r4").mkdir(parents=True)
    (root / "xdg" / "r4" / "config.json").write_text('{"api_key": "SECRET"}')
    (root / "secrets").mkdir()
    (root / "secrets" / ".env").write_text("TOPSECRET")
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n")
    (root / "notes.txt").write_text("readable")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "xdg"))
    selftest_mod.clear_cache()
    return root


def _proxy_factory(upstream_port: int):
    async def resolver(host, _port):
        return ["93.184.216.34"]

    async def connector(_address, _port):
        return await asyncio.open_connection("127.0.0.1", upstream_port)

    def factory(web, unix_path=""):
        return EgressProxy(web, unix_path=unix_path, resolver=resolver, connector=connector)

    return factory


@pytest.fixture
def make_ctx(ws):
    made: list[SandboxContext] = []

    def build(web: str = "none", domains=(), upstream_port: int = 0) -> SandboxContext:
        access = make_web_access(web, list(domains), source="flag")
        ctx = SandboxContext(
            setting=lambda: ON,
            web_access=lambda: access,
            workspace_root=ws,
            user_policy=UserSandboxPolicy(extra_deny_read_paths=(str(ws / "secrets"),)),
            proxy_factory=_proxy_factory(upstream_port) if upstream_port else None,
        )
        made.append(ctx)
        return ctx

    yield build
    for ctx in made:
        ctx.close()


def run(ctx: SandboxContext, command: str, cwd: Path, env=None, timeout: int = 30):
    spec = ctx.wrap(command, shell=True, cwd=str(cwd), env=dict(env or os.environ))
    return subprocess.run(
        spec.argv,
        env=spec.env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        start_new_session=True,
    )


def test_writes_land_only_inside_the_writable_roots(ws, make_ctx, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run(make_ctx(), f"echo in > inside.txt; echo out > {outside}/x.txt; echo rc=$?", ws)
    assert (ws / "inside.txt").read_text() == "in\n"
    assert not (outside / "x.txt").exists()
    assert "rc=0" not in result.stdout


def test_credential_stores_and_r4_config_are_unreadable(ws, make_ctx):
    script = (
        "cat notes.txt; cat ~/.ssh/id_x; cat $XDG_CONFIG_HOME/r4/config.json;"
        " cat secrets/.env; echo done"
    )
    result = run(make_ctx(), script, ws)
    assert "readable" in result.stdout and "done" in result.stdout
    assert "PRIVATE-KEY" not in result.stdout and "SECRET" not in result.stdout


def test_renames_cannot_carry_masks_or_protection_away(ws, make_ctx):
    """SEC-1/SEC-2/COR-2: rename .git, a masked dir's parent or the masked dir: all EBUSY."""
    script = (
        "mv .git .gx; mv xdg xdg2; mv secrets s2;"
        " cat .gx/config xdg2/r4/config.json s2/.env 2>/dev/null;"
        " echo '[core] fsmonitor = touch PWNED' >> .git/config;"
        " echo x > .git/hooks/pre-commit; echo inside > .git/newfile; echo done"
    )
    result = run(make_ctx(), script, ws)
    assert "done" in result.stdout and "SECRET" not in result.stdout
    assert sorted(p.name for p in ws.iterdir() if p.name in {".gx", "xdg2", "s2"}) == []
    assert (ws / ".git" / "config").read_text() == "[core]\n"
    assert list((ws / ".git" / "hooks").iterdir()) == []
    assert (ws / ".git" / "newfile").read_text() == "inside\n"  # writes inside .git still go


def test_cwd_tmp_runs_in_the_sandbox_tmp_not_the_workspace(ws, make_ctx):
    """COR-3: cwd=/tmp used to fall back to the workspace (``rm`` there hit the workspace)."""
    (ws / "keep.txt").write_text("keep")
    ctx = make_ctx()
    result = run(ctx, "pwd; rm -f keep.txt; touch made-here", Path("/tmp"))
    assert result.stdout.splitlines()[:1] == ["/tmp"]
    assert (ws / "keep.txt").exists()
    assert Path(ctx.session_dirs().tmp, "made-here").exists()


def test_isolated_sandbox_cannot_resolve_names_through_host_sockets(ws, make_ctx):
    """SEC-6: nss ``resolve`` reached systemd-resolved over its socket (a DNS channel)."""
    if not shutil.which("getent"):
        pytest.skip("getent not installed")
    result = run(make_ctx(), "getent hosts example.com; echo rc=$?", ws)
    assert "rc=0" not in result.stdout, result.stdout


def test_web_none_has_no_network_even_to_host_loopback(ws, make_ctx, upstream):
    code = textwrap.dedent(
        f"""
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:{upstream}/", timeout=3)
            print("REACHED")
        except OSError as exc:
            print("BLOCKED", exc)
        """
    )
    spec = make_ctx().wrap(
        [sys.executable, "-c", code], shell=False, cwd=str(ws), env=dict(os.environ)
    )
    result = subprocess.run(spec.argv, env=spec.env, capture_output=True, text=True, timeout=30)
    assert "BLOCKED" in result.stdout and "REACHED" not in result.stdout
    assert "HTTP_PROXY" not in spec.env


_FETCH = textwrap.dedent(
    """
    import sys, urllib.error, urllib.request
    try:
        print(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode())
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read().decode())
    """
)


def _fetch(ctx: SandboxContext, url: str, ws: Path) -> str:
    spec = ctx.wrap(
        [sys.executable, "-c", _FETCH, url], shell=False, cwd=str(ws), env=dict(os.environ)
    )
    result = subprocess.run(spec.argv, env=spec.env, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


@pytest.mark.parametrize("web", ["public", "allowlist"])
def test_allowed_host_is_reached_through_the_proxy(ws, make_ctx, upstream, web):
    ctx = make_ctx(web, domains=["allowed.example"], upstream_port=upstream)
    assert "upstream /hello" in _fetch(ctx, "http://allowed.example/hello", ws)


def test_host_not_on_the_allowlist_gets_403_with_refusal_text(ws, make_ctx, upstream):
    ctx = make_ctx("allowlist", domains=["allowed.example"], upstream_port=upstream)
    out = _fetch(ctx, "http://elsewhere.example/", ws)
    assert "HTTP 403" in out and "'elsewhere.example' is not on it" in out


def test_web_switched_off_live_refuses_running_children(ws, upstream):
    state = {"web": make_web_access("public", source="flag")}
    ctx = SandboxContext(
        setting=lambda: ON,
        web_access=lambda: state["web"],
        workspace_root=ws,
        proxy_factory=_proxy_factory(upstream),
    )
    try:
        spec = ctx.wrap(
            [sys.executable, "-c", _FETCH, "http://a.example/"], shell=False, cwd=None, env={}
        )
        state["web"] = make_web_access("none", source="flag")
        result = subprocess.run(spec.argv, env=spec.env, capture_output=True, text=True, timeout=60)
        assert "HTTP 403" in result.stdout and REFUSAL_OFF in result.stdout
    finally:
        ctx.close()


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("web", ["none", "public"])
def test_killpg_reaches_every_sandboxed_process(ws, make_ctx, upstream, web):
    ctx = make_ctx(web, upstream_port=upstream)
    spec = ctx.wrap("sleep 60 & sleep 60; wait", shell=True, cwd=str(ws), env=dict(os.environ))
    proc = subprocess.Popen(spec.argv, env=spec.env, start_new_session=True)
    time.sleep(0.8)
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=2)
    deadline = time.monotonic() + 2
    while _group_alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _group_alive(proc.pid)


def test_tmp_inside_is_the_per_session_dir(ws, make_ctx):
    ctx = make_ctx()
    result = run(ctx, 'echo hi > /tmp/marker; echo "TMPDIR=$TMPDIR"', ws)
    tmp = ctx.session_dirs().tmp
    assert Path(tmp, "marker").read_text() == "hi\n"
    assert f"TMPDIR={tmp}" in result.stdout


def test_self_test_passes_on_this_host(ws, make_ctx):
    ctx = make_ctx()
    ctx.check_available()
    assert run_self_test(ctx) == (True, "")
    leftovers = list((ws / "xdg" / "r4").glob(".sandbox-selftest-*"))
    assert leftovers == []


def test_env_scrub_inside(ws, make_ctx):
    env = dict(os.environ, OPENAI_API_KEY="sk-test", R4_GATEWAY_TOKEN="gw-token")
    result = run(make_ctx(), "env", ws, env=env)
    assert "OPENAI_API_KEY" not in result.stdout
    assert "R4_GATEWAY_TOKEN=gw-token" in result.stdout
    assert "R4_SANDBOXED=bwrap" in result.stdout


def test_inherited_context_does_not_wrap_again(ws, monkeypatch):
    monkeypatch.setenv("R4_SANDBOXED", "bwrap")
    from r4.companion.sandbox import resolve_sandbox

    ctx = SandboxContext(
        setting=lambda: resolve_sandbox(metadata={}, env=os.environ),
        web_access=lambda: make_web_access("none", source="flag"),
        workspace_root=ws,
    )
    spec = ctx.wrap("true", shell=True, cwd=None, env={})
    assert (spec.argv, spec.sandboxed, spec.backend) == ("true", True, "bwrap")
