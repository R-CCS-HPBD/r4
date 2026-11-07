"""ADR-093 D-2: the retrieve verb's wire DTOs round-trip and tolerate gaps."""

from __future__ import annotations

import pytest

from r4_contracts.retrieval import (
    GRAPH_BRIDGE_LANE,
    RetrievalSufficiency,
    RetrievedPassage,
    RetrieveResult,
    bounded_passages,
    is_graph_bridge,
)


def test_a_result_round_trips_through_its_dict():
    result = RetrieveResult(
        query="q",
        passages=(
            RetrievedPassage(
                rank=1,
                source_id="node-chunk-x-0",
                artifact_id="art-x",
                source_name="x.md",
                text="t",
                span=(0, 10),
                scores={"dense": 0.9, "fused": 0.03},
                lanes=("dense", "lex"),
            ),
        ),
        sufficiency=RetrievalSufficiency("sufficient", "z_null", 4.2, 3.0),
        searched=1842,
        space="e5-base-v2",
        lanes={"dense": {"status": "ok", "n": 50}},
        timing_ms={"dense": 12.5},
    )
    again = RetrieveResult.from_dict(result.to_dict())
    assert again == result


def test_missing_and_unknown_fields_degrade_to_unknown_not_errors():
    result = RetrieveResult.from_dict({"query": "q", "sufficiency": {"status": "great"}})
    assert result.passages == () and result.sufficiency.status == "unknown"
    passage = RetrievedPassage.from_dict({"rank": "2", "scores": {"dense": "0.8"}})
    assert passage.rank == 2 and passage.scores == {"dense": 0.8} and passage.span is None


def _found(rank: int, *lanes: str) -> RetrievedPassage:
    return RetrievedPassage(
        rank=rank,
        source_id=f"n{rank}",
        artifact_id=f"a{rank}",
        source_name="",
        text="t",
        lanes=lanes,
    )


@pytest.mark.parametrize(
    ("lanes", "bridge"),
    [(("graph",), True), (("dense", "graph"), False), (("dense",), False), ((), False)],
)
def test_a_bridge_is_a_passage_the_graph_lane_alone_found(lanes, bridge):
    assert GRAPH_BRIDGE_LANE == "graph"
    assert is_graph_bridge(_found(1, *lanes)) is bridge
    assert is_graph_bridge(RetrievedPassage.from_dict(_found(1, *lanes).to_dict())) is bridge


@pytest.mark.parametrize(("limit", "kept"), [(10, 6), (6, 6), (3, 3), (2, 2), (0, 0)])
def test_a_bound_caps_the_cut_and_keeps_every_bridge_after_it(limit, kept):
    cut = [_found(rank, "dense", "lex") for rank in range(1, 7)]
    bridges = [_found(7, "graph"), _found(8, "graph")]
    shown = bounded_passages([*cut, *bridges], limit)
    assert shown == [*cut[:kept], *bridges]


def test_a_bound_without_bridges_is_a_plain_prefix():
    cut = [_found(rank, "dense") for rank in range(1, 5)]
    assert bounded_passages(cut, 3) == cut[:3]
    assert bounded_passages((), 3) == []
