#!/usr/bin/env bash
# Does a change touch what the umrs-server image installs? (ADR-100)
# Prints "true" or "false" and the reason on stderr. Used by
# .github/workflows/server-image-smoke.yaml so the image build (minutes, ~2.5 GB) runs
# only for dependency-relevant changes, not for every umrs pointer bump.
#
# Usage: scripts/deps/image-smoke-needed.sh <base-commit> [<head-commit>]
# Anything it cannot tell (base or a umrs pin not fetched) counts as "true".
set -euo pipefail
base="$1"
head="${2:-HEAD}"
say() { echo "$2" >&2; echo "$1"; }

if ! changed="$(git diff --name-only "$base" "$head" 2>/dev/null)"; then
  say true "base $base not available: build"; exit 0
fi
if grep -qxE 'uv\.lock|pyproject\.toml|\.dockerignore|scripts/deps/.*|\.github/workflows/server-image-smoke\.yaml' <<<"$changed"; then
  say true "parent dependency files changed"; exit 0
fi
if ! grep -qx 'umrs' <<<"$changed"; then
  say false "neither the umrs pin nor a parent dependency file changed"; exit 0
fi
old="$(git rev-parse "$base:umrs")"
new="$(git rev-parse "$head:umrs")"
umrs_git() { env -u GIT_DIR -u GIT_WORK_TREE git -C "$(git rev-parse --show-toplevel)/umrs" "$@"; }
if ! umrs_git cat-file -e "${old}^{commit}" 2>/dev/null || ! umrs_git cat-file -e "${new}^{commit}" 2>/dev/null; then
  say true "umrs ${old:0:8}..${new:0:8} not both fetched: build"; exit 0
fi
if umrs_git diff --quiet "$old" "$new" -- docker/Dockerfile.server docker/server-image src/pyproject.toml src/umrs/knowledge/text_embedder.py; then
  say false "umrs ${old:0:8}..${new:0:8} leaves the image's dependency files alone"
else
  say true "umrs ${old:0:8}..${new:0:8} changes the image's dependency files"
fi
