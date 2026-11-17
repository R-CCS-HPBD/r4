"""``r4 doctor`` / ``r4 health full`` show the platform's knowledge ingest models.

ADR-116 amendment (2026-11-13): Docling and GLiNER are on by default on a
platform. connect-info carries a ``knowledge_models`` block; the diagnostics
read it and print one line. The line is advisory: a model that cannot run
falls back inside the understand job, so it never fails ``r4 doctor``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from r4._cli import diagnostics
from r4._cli import knowledge_models_check as kmc

READY = {
    "doc_parser": "docling",
    "docling": {"enabled": True, "source": "bundled", "staged": True, "version": "2.150.0"},
    "gliner": {
        "enabled": True,
        "source": "bundled",
        "staged": True,
        "repo": "urchade/gliner_medium-v2.1",
        "revision": "a" * 40,
    },
    "service": {"url_set": True, "ready": True},
}


def _block(**changes) -> dict:
    block = json.loads(json.dumps(READY))
    for key, value in changes.items():
        block[key] = {**block[key], **value} if isinstance(value, dict) else value
    return block


def test_ready_platform_reads_as_one_line() -> None:
    assert kmc.summarize(READY) == "Docling 2.150.0 on, GLiNER on, umrs-docmodels ready"


def test_switched_off_and_in_server() -> None:
    block = _block(
        doc_parser="pypdf",
        docling={"enabled": False},
        gliner={"enabled": False},
        service={"url_set": False, "ready": None},
    )
    assert kmc.summarize(block) == "Docling off (pypdf), GLiNER off, run in the server"
    assert kmc.knowledge_models_result({"knowledge_models": block})["ok"] is True


def test_service_down_but_staged_locally_still_works() -> None:
    block = _block(service={"ready": False})
    result = kmc.knowledge_models_result({"knowledge_models": block})
    assert result["ok"] is True
    assert "umrs-docmodels not answering (server fallback)" in result["summary"]


def test_service_down_without_a_local_fallback_is_flagged() -> None:
    """Since 2026-11-13 a server with a service URL does not load the models
    itself by default: a silent service means pypdf and no hints, so the line
    must not read as a working "✓"."""
    block = _block(service={"ready": False, "local_fallback": "off"})
    result = kmc.knowledge_models_result({"knowledge_models": block})
    assert result["ok"] is None
    assert result["summary"] == (
        "Docling 2.150.0 on but service not answering (pypdf fallback), "
        "GLiNER on but service not answering (no hints), "
        "umrs-docmodels not answering (no local fallback)"
    )


def test_service_down_with_the_sandbox_fallback_still_works() -> None:
    block = _block(service={"ready": False, "local_fallback": "sandbox"})
    result = kmc.knowledge_models_result({"knowledge_models": block})
    assert result["ok"] is True
    assert result["summary"].endswith("umrs-docmodels not answering (sandbox fallback)")


def test_nothing_can_run_is_advisory_not_a_failure() -> None:
    block = _block(docling={"staged": False}, gliner={"staged": False}, service={"ready": False})
    result = kmc.knowledge_models_result({"knowledge_models": block})
    assert result["ok"] is None
    assert "Docling 2.150.0 on but not staged (pypdf fallback)" in result["summary"]
    assert "GLiNER on but not staged (no hints)" in result["summary"]
    assert diagnostics.diagnostics_exit_code({"knowledge_models": result}) == 0


def test_older_platform_without_the_block() -> None:
    result = kmc.knowledge_models_result({"zmq": {}})
    assert result == {"ok": None, "message": "not advertised by this platform (older UMRS)"}


def test_block_passes_through_for_json() -> None:
    result = kmc.knowledge_models_result({"knowledge_models": READY})
    for key in ("doc_parser", "docling", "gliner", "service"):
        assert result[key] == READY[key]


class _Client:
    _api_prefix = "/gw"
    seen: list = []
    answer: object = {"knowledge_models": READY}

    def __init__(self, config=None) -> None:
        pass

    def get(self, path, **kwargs):
        type(self).seen.append((path, kwargs.get("timeout")))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def client(monkeypatch):
    _Client.seen = []
    _Client.answer = {"knowledge_models": READY}
    monkeypatch.setattr("r4.client.Client", _Client)
    return _Client


def test_probe_reads_connect_info_with_a_short_timeout(client) -> None:
    result = kmc.check_knowledge_models(SimpleNamespace())
    assert result["ok"] is True
    path, timeout = client.seen[0]
    assert path == "/gw/ops/connect-info"
    assert timeout is not None and timeout <= 15


def test_unreachable_platform_is_unknown(client) -> None:
    client.answer = RuntimeError("connection refused")
    result = kmc.check_knowledge_models(SimpleNamespace())
    assert result["ok"] is None
    assert "not reachable" in result["message"]


def test_full_diagnostics_include_the_line(monkeypatch) -> None:
    for name in (
        "check_config",
        "check_umrs_profile_sync",
        "check_auth",
        "check_gateway",
        "check_umrs",
        "check_mcp",
        "check_rag",
        "check_embedding",
        "check_pgvector",
        "check_audit",
        "check_providers",
    ):
        monkeypatch.setattr(diagnostics, name, lambda _config: {"ok": True})
    monkeypatch.setattr(diagnostics, "check_s3", lambda: {"ok": True})
    monkeypatch.setattr(
        kmc,
        "check_knowledge_models",
        lambda _config: kmc.knowledge_models_result({"knowledge_models": READY}),
    )
    checks = diagnostics.run_full_diagnostics(SimpleNamespace())
    assert list(checks)[-1] == "knowledge_models"
    assert checks["knowledge_models"]["service"] == {"url_set": True, "ready": True}


def test_human_output_prints_the_summary(capsys) -> None:
    result = kmc.knowledge_models_result({"knowledge_models": READY})
    diagnostics.emit_full_diagnostics(
        {"knowledge_models": result}, as_json=False, title="R4 Doctor"
    )
    out = capsys.readouterr().out
    assert "✓ knowledge_models  (Docling 2.150.0 on, GLiNER on, umrs-docmodels ready)" in out


def test_json_output_carries_the_block(capsys) -> None:
    result = kmc.knowledge_models_result({"knowledge_models": READY})
    diagnostics.emit_full_diagnostics({"knowledge_models": result}, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["knowledge_models"]["gliner"]["revision"] == "a" * 40
    assert payload["knowledge_models"]["summary"].startswith("Docling 2.150.0 on")
