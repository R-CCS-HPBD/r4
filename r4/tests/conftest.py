"""Shared test fixtures for the r4 test suite.

Ensures tests are hermetic: they must never read the developer's real
``~/.config/r4/sources.json``. Reading the real source store makes tests
non-deterministic (a machine with multiple registered sources triggers the
client-side cross-source aggregation path, which probes live gateways).

CI runners have no ``~/.config/r4`` and therefore already behave as if zero
sources are registered; this fixture makes local runs match that behavior.
Tests that need specific sources set ``XDG_CONFIG_HOME`` or monkeypatch the
source-store path themselves, which overrides this default.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import pytest

# Many r4 paths are ``expanduser("~/.config/r4/...")`` (companion memory and
# lessons, teamup_sessions, logs, progress), several resolved at import time,
# so XDG_CONFIG_HOME alone did not isolate them: every local run wrote into
# the developer's real ~/.config/r4 (2026-10-17). The session gets a scratch
# HOME before any r4 module is imported.
_TEST_HOME = tempfile.mkdtemp(prefix="r4-tests-home-")
os.environ["HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, True)

# A commit hook runs this suite with GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE
# set for the real repo; any test that shells out to git would then write the
# real repo (from a linked worktree it once set core.bare=true in the shared
# .git/config). Tests get a clean git environment (plan H-3.3, 2026-10-18).
for _git_var in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_git_var]


@pytest.fixture(autouse=True)
def _isolate_r4_config(tmp_path_factory, monkeypatch):
    """Point the r4 config dir at an isolated temp dir for every test."""
    cfg_dir = tmp_path_factory.mktemp("r4-config")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))

    # Reset module-level caches that depend on the source store so state does
    # not leak between tests.
    try:
        import r4.companion.model_resolution as mr

        mr._AGG_CACHE = None
        mr._ENDPOINT_CACHE.clear()
    except Exception:
        pass
    try:
        import r4.tui.model_options as mo

        mo.reset_extra_source_model_cache()
    except Exception:
        pass
    try:
        import r4.sites as rs

        rs.clear_cache()  # multi-HPC S2: site rows are cached per source
    except Exception:
        pass
    _offline_model_scale(monkeypatch)
    _no_host_mcp_daemon(monkeypatch)
    yield


#: A closed port: a probe that was not stubbed sees "no daemon", at once.
DEAD_PROXY_URL = "http://127.0.0.1:9/"


def _no_host_mcp_daemon(monkeypatch) -> None:
    """No test reaches the developer's MCP daemon (ADR-101 review Q-1).

    ``session.sandbox_set`` once probed the real ``127.0.0.1:50800`` and,
    finding an older r4-mcp there, SIGTERMed it. Every test sees a dead
    proxy port and an empty process table unless it stubs its own.
    """
    try:
        import r4.companion.mcp_exposure as mcp_exposure
    except Exception:
        return
    monkeypatch.setattr(mcp_exposure, "_proxy_health_url", lambda: DEAD_PROXY_URL)
    monkeypatch.setattr(mcp_exposure, "_proc_pids", lambda: [])


def _offline_model_scale(monkeypatch) -> None:
    """Model-scale probes (ADR-093 D-14, C22) never reach a network in tests.

    The pre-retrieval router and search_docs detect the session model's scale;
    every probe gets a 404 here, so the name rule decides unless a test
    installs its own transport (``test_model_scale.py``). The per-process
    memo is cleared so no detection leaks between tests.
    """
    try:
        import httpx

        import r4.model_scale as ms
    except Exception:
        return
    import r4.companion.ollama_window as ow

    ms.clear_scale_memo()
    ow.clear_ollama_window_memo()
    offline = httpx.MockTransport(lambda _request: httpx.Response(404, json={}))
    monkeypatch.setattr(ms, "_TRANSPORT", offline)
    # The served-window probe of an Ollama route (2026-10-30) likewise.
    monkeypatch.setattr(ow, "_TRANSPORT", offline)
