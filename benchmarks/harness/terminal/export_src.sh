#!/usr/bin/env bash
# Clean source export for the TB4 lanes (R4BENCH_PACKAGE_ROOT), split out of
# run_tb4_lanes.sh on 2026-11-13 (U2.0a review) so it can be tested.
#   usage: export_src.sh <cache-root>     (run from the repository root)
# Exports the parent tree at HEAD, plus umrs/protocol (umrs-protocol, U2.0,
# ADR-117 D-2) at the umrs commit that HEAD records: the pin, never whatever
# the submodule has checked out. Prints the export directory,
# <cache-root>/<short parent sha>.
# The export is built in <dir>.partial and renamed into place only when both
# archives succeeded. A cached export is reused only when it carries the
# .umrs-pin marker for this pin, so an interrupted or pre-fix export (r4/
# present, umrs/protocol missing or from another commit) is rebuilt.
set -euo pipefail
CACHE=${1:?usage: export_src.sh <cache-root>}

die() { echo "export_src: $*" >&2; exit 2; }

# Without this, `git -C umrs` would read the parent repository.
[[ -e umrs/.git ]] || die "umrs submodule not initialised: git submodule update --init umrs"
PIN=$(git rev-parse HEAD:umrs) || die "HEAD records no umrs gitlink"
git -C umrs cat-file -e "${PIN}^{commit}" 2>/dev/null \
  || die "umrs pin $PIN is not fetched: git submodule update --init umrs"

SRC="$CACHE/$(git rev-parse --short HEAD)"
if [[ -f $SRC/umrs/protocol/pyproject.toml && $(cat "$SRC/.umrs-pin" 2>/dev/null) == "$PIN" ]]; then
  echo "$SRC"
  exit 0
fi

PART="$SRC.partial"
rm -rf "$PART"
mkdir -p "$PART/umrs"
git archive HEAD r4-contracts science packages/r4-teamengine-spi r4 | tar -x -C "$PART"
git -C umrs archive "$PIN" protocol | tar -x -C "$PART/umrs"
[[ -f $PART/umrs/protocol/pyproject.toml ]] || die "umrs pin $PIN has no protocol/pyproject.toml"
echo "$PIN" > "$PART/.umrs-pin"
rm -rf "$SRC"
mv "$PART" "$SRC"
echo "$SRC"
