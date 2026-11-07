#!/usr/bin/env bash
# Release plan P0-2 (2026-09-24): the parent's umrs pin must be reachable on
# the CANONICAL remote (UMRS-org/umrs@ai4s-platform, ADR-010) — CI fetches it
# from there. A fork-only push turned every CI run red on 2026-09-24.
#
# Usage: scripts/check-umrs-lineage.sh [<parent-rev>]   (default: HEAD)
# Exit 0 when the pin is on upstream/ai4s-platform; 1 with the fix otherwise.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
rev="${1:-HEAD}"
pin="$(git -C "$root" rev-parse "${rev}:umrs")"
# The umrs repo's own git, never the parent's: git exports GIT_DIR to hooks
# run from a linked worktree, and `git -C umrs` would then read the parent's
# remotes ("no 'upstream' remote", 2026-10-04).
umrs_git() { env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR git -C "$root/umrs" "$@"; }
if ! umrs_git remote get-url upstream >/dev/null 2>&1; then
  echo "lineage: umrs has no 'upstream' remote; add it with: git -C umrs remote add upstream https://github.com/UMRS-org/umrs.git" >&2
  exit 1
fi
umrs_git fetch -q upstream ai4s-platform
if umrs_git merge-base --is-ancestor "$pin" upstream/ai4s-platform 2>/dev/null; then
  echo "lineage: umrs pin ${pin:0:8} is on UMRS-org ai4s-platform"
  exit 0
fi
cat >&2 <<MSG
lineage: umrs pin ${pin:0:8} is NOT on UMRS-org/umrs@ai4s-platform — CI cannot fetch it.
  fix: git -C umrs push upstream ${pin}:ai4s-platform   (and origin, the fork mirror)
MSG
exit 1
