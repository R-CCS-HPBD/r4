# video-feed — general video ingest → workflow → results plane

Generalized feed path for video applications built on UMRS:

```text
local clips+metadata (client side)
  → upload via r4 Gateway (HTTPS :443, no direct MinIO on clients)
  → durable ingest record (checksum+camera+recordingStart idempotency)
  → trigger registered workflow with uploaded S3 keys
  → workers pull inputs via internal presigned URLs
  → outputs land in S3 + results/vector rows in Postgres + knowledge graph nodes
```

Profiles are YAML data. `example` (fixed-camera shaped, mock runtime) ships in
`video_feed/profiles/`; application profiles for real projects live outside
this repository (2026-11-03) and load by name from `$VIDEO_FEED_PROFILE_DIR`,
or from a file with `--profile-file`.

## Usage (client side)

```bash
pip install -e demos/video-feed           # or PYTHONPATH.run
export R4_API_KEY=<token>                 # r4 auth token (gateway)
export VIDEO_FEED_PROFILE_DIR=<dir with your profile YAMLs>   # optional
python -m video_feed.ingest \
  --profile example \
  --source-dir ~/video-drops/<YYYY-MM-DD> \
  --gateway https://ai4s-gateway:port \
  --workflow video-example --wait
```

Watcher mode (near-real-time): `--watch 60` re-scans every 60 s, only new clips upload.

### Large clips
Clips bigger than `--large-clip-bytes` (default 4 GiB) segment client-side via
`ffmpeg -c copy -segment_time 3600` — no re-encode. Each segment is its own
manifest entry (`<parent>/seg-001`) with dedupe per segment. Needed for
multi-hour continuous recordings (and the NGINX upload size limit on the gateway plane).

### Long-running watcher on Mac
```bash
export R4_API_KEY=... R4_GATEWAY_URL=... R4_PROJECT_ID=...
export FEED_SOURCE_DIR=~/video-drops
nohup bash demos/video-feed/watcher.sh >> ~/.video_feed/watcher.log 2>&1 &
```
Lockfile at `~/.video_feed/watcher.lock` — safe to re-run.

### Results plane
Aggregate tail in `workflow-arm` emits `runs/<run_id>/results/{run,keypoints}.jsonl`
(failure-isolated) shaped 1:1 on `video_*` schema — once DDL applies on the platform database,
the consumer replays rows verbatim (see [schema/001_video_feed.sql](schema/001_video_feed.sql)).

Invariant reminders:

- Clients NEVER get presigned URLs (Phase 2l) — uploads are gateway HTTPS only.
- Workers pull artifacts internally; the ingest helper uses `r4` upload + trigger only.
- Results/vectoring happen worker-side (aggregate step writes to Postgres via DSN);
  knowledge graph nodes live through `knowledge_ingest` on the platform.

Site facts for a deployment live with the operations configuration, not here.
