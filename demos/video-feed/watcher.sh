#!/usr/bin/env bash
# video-feed watcher — long-running ingest loop (near-real-time cadence).
# Designed for nohup/launchd on the client machine (Mac analyst box).
#
# Uses a lockfile so overlapping invocations are no-ops; writes a dated log.
#
# Required env:
#   R4_API_KEY       gateway bearer token
#   R4_GATEWAY_URL   e.g. https://gateway.example.org/r4
# Optional:
#   R4_PROJECT_ID    (default: default)
#   FEED_PROFILE     (default: example)
#   FEED_SOURCE_DIR  clip watch root (required)
#   VIDEO_FEED_DIR   manifest/segments dir (default: ~/.video_feed)
#   FEED_WATCH_SEC   cadence seconds (default: 60)
#
# Usage: bash watcher.sh
#        nohup bash watcher.sh >> ~/.video_feed/watcher.log 2>&1 &
set -uo pipefail

FIND="video_feed.ingest"
LOCKDIR="${VIDEO_FEED_DIR:-$HOME/.video_feed}"
mkdir -p "$LOCKDIR"
LOCK="$LOCKDIR/watcher.lock"

mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
flock -n 9 || { echo "another watcher is running (lock: $LOCK)"; exit 0; }

PYTHONPATH="${PYTHONPATH:-$LOCKDIR/pkg}"
SRC="${FEED_SOURCE_DIR:?set FEED_SOURCE_DIR}"
PROFILE="${FEED_PROFILE:-example}"

echo "== video-feed watcher start $(date -u +%FT%TZ) profile=$PROFILE src=$SRC =="
exec python3 -m "$FIND" \
  --profile "$PROFILE" \
  --source-dir "$SRC" \
  --watch "${FEED_WATCH_SEC:-60}"
