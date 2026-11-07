"""Offline tests for ResultsAdapter — fake cursor collects SQL."""

from __future__ import annotations

from pathlib import Path

from video_feed.results import ResultsAdapter


class FakeCursor:
    def __init__(self, conn: FakeConn):
        self.conn = conn

    def execute(self, sql, params=None):  # noqa: D401 — simple recorder
        self.conn.calls.append(("execute", sql, params))

    def executemany(self, sql, rows):
        self.conn.calls.append(("executemany", sql, list(rows)))


class FakeConn:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def test_run_lifecycle_and_keypoints(tmp_path: Path):
    conn = FakeConn()
    ad = ResultsAdapter(conn, profile="example", rows_per_flush=2)

    ad.start_run("r1", "example-workflow", video_id="cl0001", camera="CAM-0001", site="local")
    csv_path = tmp_path / "pose.csv"
    csv_path.write_text(
        "frame,time_sec,bodypart,x,y,likelihood\n"
        "1,0.034,nose,100,200,0.9\n"
        "2,0.067,nose,101,201,0.95\n"
        "3,0.1,nose,102,202,0.97\n",
        encoding="utf-8",
    )
    n = ad.add_pose_csv("r1", "cl0001", csv_path)
    assert n == 3

    # flush happened (>=rows_per_flush triggers once, plus tail flush)
    batches = [c for c in conn.calls if c[0] == "executemany"]
    assert batches, "expected executemany batches"
    rows = [row for _, _, batch in batches for row in batch]
    assert len(rows) == 3

    ad.finish_run("r1", status="succeeded", metrics={"fps": 30}, outputs={"pose": "s3://k"})
    upd = [c for c in conn.calls if c[0] == "execute" and "UPDATE video_run" in c[1]]
    assert upd
    assert conn.commits >= 2


def test_motion_tsv_and_events(tmp_path: Path):
    conn = FakeConn()
    ad = ResultsAdapter(conn, profile="example-b")
    ad.start_run("e1", "example-events", video_id="clip42")
    tsv = tmp_path / "events.tsv"
    tsv.write_text("start\tend\tarea\n1.5\t2.0\t1332\n10.2\t11.1\t221\n")
    n = ad.add_motion_tsv("e1", "clip42", tsv)
    assert n == 2
    ad.finish_run("e1", status="succeeded")
    kinds = [c[1] for c in conn.calls if c[0] == "executemany"]
    assert any("video_event" in k for k in kinds)


def test_embedding_rows():
    conn = FakeConn()
    ad = ResultsAdapter(conn, profile="example")
    ad.start_run("emb1", "example-workflow", video_id="cl")
    ad.add_embedding("emb1", "cl", "vjepa2", [0.1, 0.2, 0.3], t_start=1.0, t_end=2.0)
    ins = [c for c in conn.calls if c[0] == "execute" and "video_embedding" in c[1]]
    assert ins and ins[0][2][5].startswith("[0.1")
