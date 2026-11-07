#!/usr/bin/env bash
# =============================================================================
# cut.sh — cut an R4 + UMRS release (release plan P1-2, 2026-09-24)
# =============================================================================
# Usage:
#   scripts/release/cut.sh 0.2.0-rc2            # DRY RUN (default): print the plan
#   scripts/release/cut.sh 0.2.0-rc2 --apply    # execute
#
# Steps (in this order — the order is the point):
#   1. preconditions: clean tree, on main, level with origin/main, umrs pin on
#      UMRS-org (scripts/check-umrs-lineage.sh), CI green for HEAD
#   2. bump versions: umrs/src/pyproject.toml (submodule commit, pushed to
#      upstream AND origin), then the parent's Python packages (PEP 440,
#      e.g. 0.2.0rc2) and the JS packages (semver, e.g. 0.2.0-rc.2)
#   3. CHANGELOG: "## [Unreleased] — vX" becomes "## [vX] — <date>"
#   4. commit "release: vX" (parent, with the umrs pointer)
#   5. tag umrs "ai4s-platform/vX" on both remotes; tag parent "vX" (annotated)
#   6. push the parent commit, push the tag (tui-binaries.yaml builds and
#      attaches the binaries, creating the release when missing), then
#      create-or-find the release and set title + CHANGELOG notes
#      (pre-release when suffixed)
#
# rc1 was cut by hand (no script existed despite the rc1 plan's claim).
set -euo pipefail

version="${1:?usage: cut.sh <version e.g. 0.2.0-rc2> [--apply]}"
apply="${2:-}"
tag="v${version}"
py_version="$(echo "$version" | sed -E 's/-(rc|a|b)\.?([0-9]+)$/\1\2/')"
js_version="$(echo "$version" | sed -E 's/-(rc|a|b)\.?([0-9]+)$/-\1.\2/')"
root="$(git rev-parse --show-toplevel)"
cd "$root"

run() {
  if [ "$apply" = "--apply" ]; then
    echo "+ $*"
    eval "$*"
  else
    echo "[dry-run] $*"
  fi
}

fail() { echo "cut: $*" >&2; exit 1; }

echo "== release ${tag}  (python ${py_version}, js ${js_version})"

# ── 1. preconditions ─────────────────────────────────────────────────────────
[ -z "$(git status --porcelain --ignore-submodules=none)" ] || fail "working tree not clean"
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || fail "not on main"
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail "HEAD is not origin/main"
git rev-parse -q --verify "refs/tags/${tag}" >/dev/null && fail "tag ${tag} already exists"
bash scripts/check-umrs-lineage.sh HEAD
ci="$(gh run list --workflow ci.yaml --commit "$(git rev-parse HEAD)" -L 1 --json conclusion -q '.[0].conclusion' 2>/dev/null || true)"
if [ "$ci" != "success" ]; then
  if [ "$apply" = "--apply" ]; then
    fail "CI for HEAD is '${ci:-none}' — a release needs a green run"
  fi
  echo "[dry-run] WARNING: CI for HEAD is '${ci:-none}' (apply would refuse)"
fi
grep -q '^## \[Unreleased\]' CHANGELOG.md || fail "CHANGELOG.md has no [Unreleased] section"

# ── 2. version bumps ─────────────────────────────────────────────────────────
py_files=(pyproject.toml r4/pyproject.toml r4-contracts/pyproject.toml packages/r4-teamengine-spi/pyproject.toml)
js_files=(packages/r4-tui-v3/package.json packages/r4-web/package.json)
run "sed -i -E 's/^version = \"[^\"]+\"/version = \"${py_version}\"/' umrs/src/pyproject.toml"
run "git -C umrs commit -qam 'release: ${tag}'"
run "git -C umrs push -q upstream HEAD:ai4s-platform && git -C umrs push -q origin HEAD:ai4s-platform"
for f in "${py_files[@]}"; do
  run "sed -i -E 's/^version = \"[^\"]+\"/version = \"${py_version}\"/' ${f}"
done
run "sed -i -E 's/^__version__ = \"[^\"]+\"/__version__ = \"${py_version}\"/' r4/src/r4/__init__.py"
for f in "${js_files[@]}"; do
  run "sed -i -E 's/^  \"version\": \"[^\"]+\"/  \"version\": \"${js_version}\"/' ${f}"
done

# ── 3. CHANGELOG ─────────────────────────────────────────────────────────────
today="$(date +%Y-%m-%d)"
run "sed -i -E 's/^## \\[Unreleased\\].*/## [${tag}] — ${today}/' CHANGELOG.md"

# ── 4–5. commit + tags ───────────────────────────────────────────────────────
run "git add -A umrs ${py_files[*]} r4/src/r4/__init__.py ${js_files[*]} CHANGELOG.md"
run "git commit -qm 'release: ${tag}'"
run "git -C umrs tag -a 'ai4s-platform/${tag}' -m 'R4 + UMRS ${tag} (umrs side)'"
run "git -C umrs push -q upstream 'ai4s-platform/${tag}' && git -C umrs push -q origin 'ai4s-platform/${tag}'"
run "git tag -a '${tag}' -m 'R4 + UMRS ${tag}'"

# ── 6. push commit, create release, then push tag ────────────────────────────
notes="$(mktemp)"
awk -v h="## [${tag}]" 'index($0,h)==1{p=1;next} /^## \[/{if(p)exit} p' CHANGELOG.md > "$notes" || true
pre=""; [[ "$version" == *-* ]] && pre="--prerelease"
run "git push -q origin main"
run "git push -q origin '${tag}'"
# The tag push starts tui-binaries.yaml, which creates the release when
# missing (P0-3). Create-or-find it here too, then set the title + notes —
# creating it BEFORE the tag push would make GitHub mint its own tag and
# reject our annotated one.
run "gh release view '${tag}' >/dev/null 2>&1 || gh release create '${tag}' --verify-tag --title 'R4 + UMRS ${tag}' --notes-file '${notes}' ${pre} || true"
run "gh release edit '${tag}' --title 'R4 + UMRS ${tag}' --notes-file '${notes}' ${pre}"

echo "== done (${apply:-dry-run}). Next: converge the tag [owner], fill docs/verification/release/${tag}-signoff.md"
