-- video-feed results plane (platform Postgres).
-- Three app-agnostic row kinds covering pose/keypoints + embeddings
-- and motion events, plus run catalogue for provenance.
--
-- Apply on the platform Postgres inside the umrs_db database:
--   psql -h 127.0.0.1 -U umrs -d umrs_db -f schema/001_video_feed.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- Provenance / metric rollup per workflow execution on a video.
CREATE TABLE IF NOT EXISTS video_run (
    run_id          TEXT PRIMARY KEY,           -- e.g. <exec_id> or deterministic sha
    profile         TEXT NOT NULL,              -- example | <app profile> | ...
    workflow        TEXT NOT NULL,
    video_id        TEXT NOT NULL,              -- clip stem / catalog id
    camera          TEXT,
    site            TEXT,
    day             DATE,
    params          JSONB NOT NULL DEFAULT '{}'::jsonb,
    inputs          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- clip/meta s3 keys
    outputs         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- produced artifacts (s3 keys)
    metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- FPS, wall time, coverage, ...
    status          TEXT NOT NULL DEFAULT 'running',      -- running | succeeded | failed
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS video_run_profile_day_idx ON video_run (profile, day);
CREATE INDEX IF NOT EXISTS video_run_video_idx ON video_run (video_id);
CREATE INDEX IF NOT EXISTS video_run_status_idx ON video_run (status);

-- Per-frame, per-bodypart pose row (DLC / SuperAnimal / KP pipelines).
CREATE TABLE IF NOT EXISTS video_keypoint (
    run_id          TEXT NOT NULL REFERENCES video_run (run_id) ON DELETE CASCADE,
    video_id        TEXT NOT NULL,
    frame           INT  NOT NULL,
    t_rel           REAL NOT NULL,             -- seconds rel. to analysis segment
    bodypart        TEXT NOT NULL,
    x               REAL,
    y               REAL,
    likelihood      REAL,
    PRIMARY KEY (run_id, bodypart, frame)
);
CREATE INDEX IF NOT EXISTS video_keypoint_video_idx ON video_keypoint (video_id, frame);
CREATE INDEX IF NOT EXISTS video_keypoint_lhb_idx ON video_keypoint (run_id, bodypart, likelihood) WHERE likelihood >= 0.6;

-- Event rows (motion tracking events; activity bouts; VLM tags).
CREATE TABLE IF NOT EXISTS video_event (
    run_id          TEXT NOT NULL REFERENCES video_run (run_id) ON DELETE CASCADE,
    video_id        TEXT NOT NULL,
    event_id        TEXT NOT NULL,           -- per-run local identifier
    kind            TEXT NOT NULL,           -- motion_bout | activity_bout | track_lost | vlm_caption | ...
    t_start         REAL NOT NULL,
    t_end           REAL NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, event_id)
);
CREATE INDEX IF NOT EXISTS video_event_kind_idx ON video_event (kind, t_start);
CREATE INDEX IF NOT EXISTS video_event_video_idx ON video_event (video_id, t_start);

-- pgvector embeddings for similarity search (V-JEPA2 frame clusters, DLC pose seq).
CREATE TABLE IF NOT EXISTS video_embedding (
    run_id          TEXT NOT NULL REFERENCES video_run (run_id) ON DELETE CASCADE,
    video_id        TEXT NOT NULL,
    kind            TEXT NOT NULL,           -- vjepa2 | dlc-bodypart-seq | motion-event-seq
    t_start         REAL NOT NULL DEFAULT 0,
    t_end           REAL NOT NULL DEFAULT 0,
    embedding       VECTOR(1024),            -- dim normalized at ingest (doubly stored dims get dedicated idx)
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, kind, t_start)
);
CREATE INDEX IF NOT EXISTS video_embedding_video_idx ON video_embedding (video_id, kind);
-- ANN index is created by the results adapter once vector rows exist
-- (ivfflat requires data to pre-exist for meaningful centroids).

COMMIT;
