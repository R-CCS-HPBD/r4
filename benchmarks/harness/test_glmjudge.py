# SPDX-License-Identifier: Apache-2.0
"""Env/timeout contract for same_model/glmjudge.py (mocked; no rikyu needed).

2026-10-20: blind judge calls failed closed as ``__ERROR__ ... read operation
timed out`` on a loaded rikyu — r4's default 300 s read timeout aborts slow
temperature-0 reasoning completions. glm() must carry R4_TIMEOUT into the r4
subprocess (without trampling an operator's setting) and use a matching leash.
"""

import json
import subprocess
import sys
from pathlib import Path

SM = Path(__file__).resolve().parent / "same_model"
sys.path.insert(0, str(SM))

import glmjudge  # noqa: E402


def _capture(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout='{"score": 1}\n', stderr="")

    monkeypatch.setattr(glmjudge.subprocess, "run", fake_run)
    return calls


def test_glm_carries_r4_timeout(monkeypatch, tmp_path):
    """Old code never set R4_TIMEOUT: the judge inherited r4's 300 s default."""
    calls = _capture(monkeypatch, tmp_path)
    monkeypatch.delenv("R4_TIMEOUT", raising=False)
    assert glmjudge.glm("sys", "user") == '{"score": 1}'
    assert len(calls) == 1
    assert calls[0]["env"]["R4_TIMEOUT"] == "900"
    assert calls[0]["timeout"] >= 900


def test_operator_r4_timeout_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_TIMEOUT", "1234")
    calls = _capture(monkeypatch, tmp_path)
    glmjudge.glm("sys", "user")
    assert calls[0]["env"]["R4_TIMEOUT"] == "1234"


def test_glm_retries_then_error(monkeypatch, tmp_path):
    monkeypatch.delenv("R4_TIMEOUT", raising=False)

    def flaky(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(glmjudge.subprocess, "run", flaky)
    out = glmjudge.glm("sys", "user", retries=2)
    assert out.startswith("__ERROR__")
    json.dumps({"wrapped": out})  # stays embeddable in grade rows
