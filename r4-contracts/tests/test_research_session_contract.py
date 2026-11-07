"""Research Session contract (ADR-025) — stdlib DTO round trips."""

from r4_contracts.research import (
    ResearchSession,
    ResearchSessionLoopLink,
    ResearchSessionStatus,
)


def test_session_round_trip():
    link = ResearchSessionLoopLink(
        session_id="sess-1", loop_id="loop-9", venue="local", loop_role="attached"
    )
    session = ResearchSession(
        session_id="sess-1",
        project_id="proj-x",
        title="MERMAID filter",
        research_goal="digital filter for seismic peaks",
        status="paused",
        authoritative_runtime="hybrid",
        metadata={"source": "companion"},
        loops=[link],
    )
    data = session.to_dict()
    assert data["status"] == "paused"
    assert data["loops"][0]["venue"] == "local"

    restored = ResearchSession.from_mapping(data)
    assert restored.status is ResearchSessionStatus.PAUSED
    assert restored.loops[0].loop_id == "loop-9"
    assert restored.metadata == {"source": "companion"}


def test_link_defaults_and_mapping_tolerance():
    link = ResearchSessionLoopLink.from_mapping({"session_id": "s", "loop_id": "l"})
    assert link.venue == "umrs" and link.loop_role == "auto"
    session = ResearchSession.from_mapping({})
    assert session.session_id == ""
    assert session.status is ResearchSessionStatus.ACTIVE
    assert session.loops == []


def test_archived_marker_preserved():
    session = ResearchSession.from_mapping(
        {
            "session_id": "s",
            "project_id": "p",
            "status": "archived",
            "archived_at": "2026-08-06T00:00:00Z",
        }
    )
    assert session.archived_at == "2026-08-06T00:00:00Z"
    assert session.to_dict()["archived_at"] == "2026-08-06T00:00:00Z"
