#!/usr/bin/env bash
# Re-lock with the release cooldown and regenerate every image lock (ADR-100).
#
#   scripts/deps/refresh.sh                monthly refresh: cutoff = today minus
#                                          COOLDOWN_DAYS (7), every package upgraded
#   scripts/deps/refresh.sh PKG [PKG...]   security fast path: only PKG moves,
#                                          exempt from the cooldown
#
# Then: the image smoke test (scripts/deps/image-smoke.sh) and a review PR.
# Needs uv (the version in .github/workflows/deps-refresh.yaml) and network.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
lockctl=(python3 scripts/deps/lockctl.py)

if [ "$#" -eq 0 ]; then
  "${lockctl[@]}" cooldown --days "${COOLDOWN_DAYS:-7}"
  uv lock --upgrade
  "${lockctl[@]}" export --upgrade
else
  "${lockctl[@]}" allow-new "$@"
  upgrades=()
  for package in "$@"; do upgrades+=(--upgrade-package "$package"); done
  uv lock "${upgrades[@]}"
  # The compiled locks (gateway sidecar, assistant-frontend) move too: they
  # follow uv.lock as constraints, and PKG outside uv.lock is exempt up to now.
  "${lockctl[@]}" export "${upgrades[@]}"
fi
"${lockctl[@]}" derive-umrs
"${lockctl[@]}" check
