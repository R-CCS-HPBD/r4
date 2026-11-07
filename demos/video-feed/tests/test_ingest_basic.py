"""Offline tests for video_feed (no gateway, no Postgres)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_feed.ingest import Clip, ingest_once, scan_clips
from video_feed.manifest import Manifest
from video_feed.profiles import load_profile


def _mk_clip(
    root: Path, name: str, ts: str = "2026-01-01T00:00:00Z", body: bytes = b"x" * 64
) -> Path:
    v = root / f"{name}.mkv"
    v.write_bytes(body)
    (root / f"{name}_metadata.json").write_text(
        json.dumps(
            {
                "sourceFile": v.name,
                "cameraName": name.split("_")[0],
                "recordingStart": ts,
                "recordingStop": ts,
            }
        ),
        encoding="utf-8",
    )
    return v


def test_signature_includes_profile_and_dedupe(tmp_path: Path):
    clip = Clip(
        path=tmp_path / "a.mkv",
        meta_path=None,
        video_id="a",
        meta={"cameraName": "CAM-0001", "recordingStart": "T0"},
        size=123,
        mtime=1.0,
    )
    profile = load_profile("example")
    sig = clip.signature(profile)
    assert sig.startswith("example|")
    assert "CAM-0001" in sig and "T0" in sig


def test_scan_and_dedupe(tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    _mk_clip(root, "CAM-0001_2026-01-01_00_00_00")
    _mk_clip(root, "CAM-0002_2026-01-01_00_01_00")

    profile = load_profile("example")
    man = Manifest("example", root=tmp_path / "ledgers")

    counters = ingest_once(
        client=None,  # type: ignore[arg-type]
        man=man,
        root=root,
        profile=profile,
        dry_run=True,
        verbose=False,
    )
    assert counters["scanned"] == 2
    assert counters["uploaded"] == 2
    assert counters["triggered"] == 0  # dry run → not triggered

    # dry-run entries must NOT dedupe (retry semantics); a real upload will.
    counters2 = ingest_once(None, man, root, profile, dry_run=True, verbose=False)  # type: ignore[arg-type]
    assert counters2["scanned"] == 2
    assert counters2["duplicated"] == 0
    assert counters2["uploaded"] == 2
    assert man.stat()["entries"] == 4

    # simulate: mark last two as truly uploaded → dedupe on next pass
    e = list(man.entries())
    assert len(e) == 4


def test_manifest_find(tmp_path: Path):
    man = Manifest("x", root=tmp_path)
    man.append({"sig": "s1", "video_id": "v1"})
    assert man.find("s1")["video_id"] == "v1"
    assert man.find("missing") is None


def test_metadata_override(tmp_path: Path):
    # a clip with no sidecar should still ingest (empty meta)
    v = tmp_path / "plain.mkv"
    v.write_bytes(b"0" * 10)
    profile = load_profile("example")
    clips = scan_clips(tmp_path, profile)
    assert len(clips) == 1 and clips[0].meta == {}


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
