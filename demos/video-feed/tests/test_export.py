"""Offline tests for export writers (S3 JSONL shapes)."""

from __future__ import annotations

import json

from video_feed.export import (
    embedding_rows_json,
    event_rows_json,
    keypoint_rows_json,
    run_row_json,
)


def _lines(b: bytes):
    return [json.loads(t) for t in b.decode("utf-8").splitlines() if t.strip()]


def test_run_row():
    b = run_row_json({"run_id": "r1", "profile": "example", "workflow": "video-ai"})
    rows = _lines(b)
    assert len(rows) == 1 and rows[0]["run_id"] == "r1"


def test_keypoints_shape_matches_schema():
    b = keypoint_rows_json(
        "r1",
        "v1",
        [
            {"frame": 1, "time_sec": 0.05, "bodypart": "nose", "x": 10, "y": 20, "likelihood": 0.9},
            {"frame": 2, "time_sec": 0.1, "bodypart": "nose", "x": 11, "y": 21, "likelihood": 0.95},
        ],
    )
    rows = _lines(b)
    assert len(rows) == 2
    expect_keys = {"run_id", "video_id", "frame", "t_rel", "bodypart", "x", "y", "likelihood"}
    assert set(rows[0]) == expect_keys


def test_events_and_embedding_shapes():
    eb = event_rows_json(
        "r1", "v1", [{"event_id": "0", "t_start": 1.0, "t_end": 2.5, "payload": {"a": 1}}]
    )
    er = _lines(eb)
    assert er[0]["kind"] == "motion_bout" and er[0]["run_id"] == "r1"

    mb = embedding_rows_json("r1", "v1", "vjepa2", [(0.0, 1.0, [0.1, 0.2], {"src": "mock"})])
    rm = _lines(mb)
    assert rm[0]["kind"] == "vjepa2" and rm[0]["embedding"] == [0.1, 0.2]
