"""Offline tests for large-clip segment policy."""

from __future__ import annotations

from pathlib import Path

from video_feed.ingest import Clip, _expanded_clips
from video_feed.profiles import load_profile


def _clip(path: Path, size: int) -> Clip:
    return Clip(
        path=path,
        meta_path=None,
        video_id=path.stem,
        meta={"cameraName": "CAM-0001"},
        size=size,
        mtime=1.0,
    )


def test_small_clip_passes_through(tmp_path: Path):
    prof = load_profile("example")
    p = tmp_path / "a.mkv"
    p.write_bytes(b"x" * 8)
    clips = _expanded_clips([_clip(p, 100)], prof, large_clip_bytes=1000, segment_root=tmp_path)
    assert len(clips) == 1 and getattr(clips[0], "segment_index", None) is None


def test_large_clip_splices_with_sig(monkeypatch, tmp_path: Path):
    prof = load_profile("example")
    p = tmp_path / "big.mkv"
    p.write_bytes(b"x" * 8)
    segs = []
    for i in range(3):
        s = tmp_path / f"seg-{i:03d}.mkv"
        s.write_bytes(b"s")
        segs.append(s)

    monkeypatch.setattr("video_feed.ingest._split_clip", lambda *_args, **_kwargs: segs)
    out = _expanded_clips(
        [_clip(p, 5 * 1024 * 1024 * 1024)], prof, large_clip_bytes=1, segment_root=tmp_path
    )
    assert len(out) == 3
    idx = [c.segment_index for c in out]
    assert idx == [1, 2, 3]
    sigs = {c.signature(prof) for c in out}
    assert any("seg1" in s for s in sigs) and any("seg3" in s for s in sigs)


def test_large_clip_no_ffmpeg_keeps_whole(monkeypatch, tmp_path: Path):
    prof = load_profile("example")
    p = tmp_path / "big.mkv"
    p.write_bytes(b"x" * 8)
    monkeypatch.setattr("shutil.which", lambda _x: None)
    out = _expanded_clips(
        [_clip(p, 5 * 1024 * 1024 * 1024)], prof, large_clip_bytes=1, segment_root=tmp_path
    )
    assert len(out) == 1
