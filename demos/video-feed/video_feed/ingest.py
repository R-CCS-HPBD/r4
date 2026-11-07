"""Ingest driver: scan client clips → upload via gateway → trigger workflow.

Py3.9 stdlib + optional yaml. Intended to run on the client side (cluster login node,
notebook, laptop). Watcher mode re-scans with a stable-id dedupe, so the same
crons/systemd-user timers keep feeds flowing near-real-time.

Idempotency signature: ``sig = profile|sig-of(dedupe_fields|size|mtime)``.
Metadata json sidecars power capture identity (camera, recordingStart/Stop).

Lays no SQLite/logs inside repo: manifest on client HOME only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import GatewayClient, GatewayError
from .manifest import Manifest
from .profiles import FeedProfile, load_profile

CHECKSUM_LIMIT = 256 * 1024 * 1024  # 256 MiB — above, use size+mtime fingerprint
LG_CLIP_BYTES = 4 * 1024 * 1024 * 1024  # >4 GiB → ffmpeg segment into ~N slices
SEGMENT_SEC = 3600  # 1 h per slice (keeps each upload under the gateway body limit)
SEGMENT_PREFIX = "seg"


@dataclass
class Clip:
    path: Path
    meta_path: Path | None
    video_id: str
    meta: dict[str, Any]
    size: int
    mtime: float

    def signature(self, profile: FeedProfile) -> str:
        values = []
        for k in profile.dedupe_fields:
            if k == "nsz":
                values.append(str(self.size))
            elif k == "nsz_mtime":
                values.append(f"{self.size}:{int(self.mtime)}")
            elif k == "sha256":
                values.append(self.sha256())
            else:
                values.append(str(self.meta.get(k, "")))
        return profile.name + "|" + "|".join(values)

    def sha256(self) -> str:
        if self.size > CHECKSUM_LIMIT:
            return f"nsz{self.size}m{int(self.mtime)}"
        h = hashlib.sha256()
        with self.path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()


def _read_sidecar(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan_clips(root: Path, profile: FeedProfile) -> list[Clip]:
    clips: list[Clip] = []
    for mkv in sorted(root.rglob(profile.pattern)):
        side = mkv.with_suffix("").with_name(mkv.name[: -len(mkv.suffix)] + profile.metadata_suffix)
        meta = _read_sidecar(side) if side.exists() else {}
        if not meta and mkv.with_suffix(mkv.suffix + profile.metadata_suffix).exists():
            meta = _read_sidecar(mkv.with_suffix(mkv.suffix + profile.metadata_suffix))
        clips.append(
            Clip(
                path=mkv,
                meta_path=side if side.exists() else None,
                video_id=mkv.stem,
                meta=meta,
                size=mkv.stat().st_size,
                mtime=mkv.stat().st_mtime,
            )
        )
    return clips


def _metadata_subset(profile: FeedProfile, meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta[k] for k in profile.meta_keys if k in meta}


def _split_clip(clip: Clip, dest_dir: Path, segment_sec: int = SEGMENT_SEC) -> list[Path]:
    """Split a large clip with ffmpeg segment (stream-copy; no re-encode).
    Segments are deterministic (index-based) and reused across runs if present.
    """
    import subprocess

    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = clip.path.suffix or ".mkv"
    pattern = dest_dir / f"{SEGMENT_PREFIX}-%03d{suffix}"
    existing = sorted(dest_dir.glob(f"{SEGMENT_PREFIX}-*{suffix}"))
    if existing:
        return existing
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(clip.path),
        "-c",
        "copy",
        "-map",
        "0",
        "-segment_time",
        str(segment_sec),
        "-f",
        "segment",
        str(pattern),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return sorted(dest_dir.glob(f"{SEGMENT_PREFIX}-*{suffix}"))


class ChunkedClip(Clip):
    """A child of a large clip; sig carries the segment index."""

    def __init__(self, parent: Clip, path: Path, index: int):
        super().__init__(
            path=path,
            meta_path=parent.meta_path,
            video_id=f"{parent.video_id}/{SEGMENT_PREFIX}-{index:03d}",
            meta=dict(parent.meta),
            size=path.stat().st_size,
            mtime=parent.mtime,
        )
        self.parent_video_id = parent.video_id
        self.segment_index = index

    def signature(self, profile: FeedProfile) -> str:
        sig = super().signature(profile)
        return f"{sig}|{SEGMENT_PREFIX}{self.segment_index}"


def _parse_iso(s: Any) -> float | None:
    """Parse ISO-8601 Z/offset timestamps w/ ><6-digit fractions into epoch seconds."""
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime

        ss = s.replace("Z", "+00:00")
        if "." in ss:
            head, tail = ss.split(".", 1)
            frac = re.match(r"^(\d+)", tail)
            rest = re.match(r"^\d+([+\-]\d+:\d+)?$", tail)
            if frac and rest:
                ss = f"{head}.{frac.group(0)[:6]}{rest.group(1) or '+00:00'}"
        return datetime.fromisoformat(ss).timestamp()
    except Exception:
        return None


def _clip_duration(meta: dict[str, Any]) -> float:
    for sk, ek in (("recordingStart", "recordingStop"), ("captured_start", "captured_stop")):
        st, ed = _parse_iso(meta.get(sk)), _parse_iso(meta.get(ek))
        if st is not None and ed is not None and ed > st:
            return ed - st
    return 60.0


def _write_plan_files(profile: FeedProfile, clip: Clip, tmp: Path) -> tuple[Path, Path, float]:
    """Build per-clip inventory.csv and analysis-plan.csv (per-clip single row).
    Worker side resolves the clip row through the `clip_key` artifact, so the
    path column stays as the worker-facing key path.
    """
    dur = _clip_duration(clip.meta)
    cam = clip.meta.get("cameraName") or clip.meta.get("camera", "")
    inv = tmp / "inventory.csv"
    inv.write_text(
        "video_id,path,camera_name,source_file\n"
        f"{clip.video_id},{clip.path.name},{cam},{clip.path.name}\n",
        encoding="utf-8",
    )
    plan = tmp / "plan.csv"
    plan.write_text(
        "analysis_id,video_id,start_sec,end_sec,description\n"
        f"{clip.video_id}-a0,{clip.video_id},0,{dur:.3f},feed-single-clip\n",
        encoding="utf-8",
    )
    return inv, plan, dur


# Entries with these statuses dedupe future passes; dry/failed are retried.
DEDUPE_STATUSES = {
    "uploaded",
    "triggered",
    "running",
    "pending",
    "succeeded",
    "success",
    "completed",
    "done",
}


def _expanded_clips(
    clips: list[Clip],
    profile: FeedProfile,
    *,
    large_clip_bytes: int,
    segment_root: Path | None,
) -> list[Clip]:
    """Split >LARGE clips into segment Clips when ffmpeg exists (else keep whole)."""
    import shutil

    have_ffmpeg = shutil.which("ffmpeg") is not None
    out: list[Clip] = []
    for clip in clips:
        if large_clip_bytes > 0 and clip.size > large_clip_bytes and have_ffmpeg:
            base = (segment_root or Path.home() / ".video_feed" / "segments") / clip.signature(
                profile
            )
            for i, seg in enumerate(_split_clip(clip, base), start=1):
                child = ChunkedClip(clip, seg, i)
                out.append(child)
        else:
            out.append(clip)
    return out


def _upload_plan_assets(
    client: GatewayClient, clip: Clip, profile: FeedProfile, params: dict[str, Any]
) -> None:
    """Materialize+upload inventory/plan CSVs and merge their keys into params."""
    with tempfile.TemporaryDirectory(prefix="video-feed-plan-") as td:
        inv, plan, _dur = _write_plan_files(profile, clip, Path(td))
        ir = client.upload_file(
            inv,
            key=f"{clip.video_id}/inventory.csv",
            purpose=profile.bucket_purpose,
        )
        pr = client.upload_file(
            plan,
            key=f"{clip.video_id}/plan.csv",
            purpose=profile.bucket_purpose,
        )
        params["inventory_key"] = (
            ir.get("key") or ir.get("s3_key") or f"{clip.video_id}/inventory.csv"
        )
        params["analysis_plan_key"] = (
            pr.get("key") or pr.get("s3_key") or f"{clip.video_id}/plan.csv"
        )
    if "model_key" in profile.assets:
        params.setdefault("model_path", profile.assets["model_key"])


def _real_clip_pipeline(
    client: GatewayClient,
    clip: Clip,
    profile: FeedProfile,
    wf: str,
    *,
    wait: bool,
) -> dict[str, Any]:
    """Upload clip (+plan assets) and trigger the workflow; return entry fields."""
    r = client.upload_file(clip.path, key=clip.path.name, purpose=profile.bucket_purpose)
    key = r.get("key") or r.get("s3_key") or clip.path.name
    update: dict[str, Any] = {"s3_key": key, "status": "uploaded"}

    params = dict(profile.workflow_params)
    params.update(
        {
            "clip_key": key,
            "video_id": clip.video_id,
            "metadata": _metadata_subset(profile, clip.meta),
        }
    )
    plan_mode = str(profile.assets.get("plan_mode", "none")).lower()
    if plan_mode == "per-clip":
        _upload_plan_assets(client, clip, profile, params)
        params.setdefault("video_keys", [key])

    trg = client.trigger_workflow(wf, parameters=params, input_files=[key], wait=False)
    exec_id = trg.get("execution_id") or trg.get("id") or trg.get("execution")
    update.update({"workflow": wf, "execution_id": exec_id, "status": "triggered"})

    if wait and exec_id:
        last = None
        for st in client.wait_execution(exec_id, timeout_s=6 * 3600):
            last = st
        state = str((last or {}).get("state") or (last or {}).get("status", "")).lower()
        update["status"] = state or "done"
    return update


def ingest_once(
    client: GatewayClient,
    man: Manifest,
    root: Path,
    profile: FeedProfile,
    *,
    workflow_name: str | None = None,
    wait: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
    large_clip_bytes: int = LG_CLIP_BYTES,
    segment_root: Path | None = None,
) -> dict[str, int]:
    """One scan→upload→trigger pass. Returns counters."""
    wf = workflow_name or profile.workflow
    done = {"scanned": 0, "duplicated": 0, "uploaded": 0, "triggered": 0, "failed": 0}
    clips = _expanded_clips(
        scan_clips(root, profile),
        profile,
        large_clip_bytes=large_clip_bytes,
        segment_root=segment_root,
    )
    for clip in clips:
        done["scanned"] += 1
        sig = clip.signature(profile)
        prior = man.find(sig)
        if prior is not None and str(prior.get("status", "")).lower() in DEDUPE_STATUSES:
            done["duplicated"] += 1
            continue
        entry: dict[str, Any] = {
            "sig": sig,
            "profile": profile.name,
            "video_id": clip.video_id,
            "path": str(clip.path),
            "meta": _metadata_subset(profile, clip.meta),
            "size": clip.size,
            "status": "staged",
        }
        if isinstance(clip, ChunkedClip):
            entry["parent_video_id"] = clip.parent_video_id
            entry["segment_index"] = clip.segment_index
        try:
            if dry_run:
                entry["status"] = "dry"
                done["uploaded"] += 1
            else:
                update = _real_clip_pipeline(client, clip, profile, wf, wait=wait)
                entry.update(update)
                done["uploaded"] += 1
                if update.get("execution_id"):
                    done["triggered"] += 1
        except GatewayError as e:
            entry.update({"status": "failed", "error": str(e)[:400]})
            done["failed"] += 1
            if verbose:
                print(f"[fail] {clip.path.name}: {e}", flush=True)
        man.append(entry)
        if verbose:
            print(f"[ok] {clip.path.name}: {entry['status']}", flush=True)
    return done


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="video-feed ingest (client side)")
    p.add_argument("--profile", required=True)
    p.add_argument("--profile-file", default=None)
    p.add_argument("--source-dir", required=True)
    p.add_argument("--gateway", default=None)
    p.add_argument("--project", default="default")
    p.add_argument("--workflow", default=None)
    p.add_argument("--manifest-dir", default=None)
    p.add_argument("--watch", type=int, default=0, help="rescan every N seconds (0=once)")
    p.add_argument("--wait", action="store_true", help="wait on workflow executions before exit")
    p.add_argument(
        "--large-clip-bytes",
        type=int,
        default=LG_CLIP_BYTES,
        help="segment clips larger than N bytes into ffmpeg time slices (0=off)",
    )
    p.add_argument("--segment-root", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    profile = load_profile(args.profile, file=args.profile_file)
    client = (
        GatewayClient.from_env(args.gateway, project_id=args.project) if not args.dry_run else None
    )  # type: ignore
    man = Manifest(args.profile, root=args.manifest_dir)
    root = Path(args.source_dir)

    while True:
        counters = ingest_once(
            client,
            man,
            root,
            profile,
            workflow_name=args.workflow,
            wait=args.wait,
            dry_run=args.dry_run,
            verbose=not args.quiet,
            large_clip_bytes=int(args.large_clip_bytes),
            segment_root=Path(args.segment_root) if args.segment_root else None,
        )
        if not args.quiet:
            print("counters:", counters, "manifest:", man.stat(), flush=True)
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
