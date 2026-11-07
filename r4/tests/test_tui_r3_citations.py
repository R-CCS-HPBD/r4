"""The TUI's citation block gets the passages an answer cited (TUI audit r2
#66, 2026-10-17): the ADR-093 check records the cited labels, and
send_message's result carries them for the turn that just ran."""

from __future__ import annotations

from types import SimpleNamespace

from r4_contracts.retrieval import RetrievedPassage

from r4.companion.citation_check import check_session_turn, turn_citations
from r4.companion.evidence_ledger import SESSION_LEDGER_ATTR, EvidenceLedger
from r4.companion.ui import bridge_server as bs


def _session(turn_id: str = "t1"):
    passage = RetrievedPassage(
        rank=1,
        source_id="chunk-7",
        artifact_id="art-42",
        source_name="mof5-capture.pdf",
        text="MOF-5 adsorbs   carbon dioxide strongly at room temperature.",
    )
    ledger = EvidenceLedger()
    ledger.record({"[S1]": "chunk-7"}, [passage], turn_id=turn_id)
    session = SimpleNamespace(metadata={}, _current_turn_id=turn_id, agent=None, messages=[])
    setattr(session, SESSION_LEDGER_ATTR, ledger)
    return session


def test_cited_passages_resolve_for_their_turn(monkeypatch):
    monkeypatch.delenv("R4_RAG_VERIFY", raising=False)
    session = _session()
    check_session_turn(
        session, "Does MOF-5 adsorb CO2?", "MOF-5 adsorbs carbon dioxide strongly [S1]."
    )
    rows = turn_citations(session, "t1")
    assert rows == [
        {
            "sid": "S1",
            "artifact_id": "art-42",
            "title": "mof5-capture.pdf",
            "kind": "passage",
            "preview": "MOF-5 adsorbs carbon dioxide strongly at room temperature.",
            # The passage handle, so the chip opens once the label is gone
            # (review of K1 part A, 2026-10-24).
            "source_id": "chunk-7",
            "project_id": "",
        }
    ]
    assert turn_citations(session, "t2") == []


def test_nothing_without_a_check():
    assert turn_citations(SimpleNamespace(metadata={}), "t1") == []


def test_turn_result_carries_the_citations(monkeypatch):
    monkeypatch.delenv("R4_RAG_VERIFY", raising=False)
    session = _session()
    check_session_turn(session, "q", "MOF-5 adsorbs carbon dioxide strongly [S1].")
    server = bs.CompanionBridgeServer.__new__(bs.CompanionBridgeServer)
    server._session = session
    server.snapshot = lambda **_kw: {}
    result = server._turn_result("answer", False, turn_before="t0")
    assert result["turn_id"] == "t1"
    assert [row["sid"] for row in result["citations"]] == ["S1"]
    plain = server._turn_result("answer", False, turn_before="t1")
    assert "citations" not in plain
