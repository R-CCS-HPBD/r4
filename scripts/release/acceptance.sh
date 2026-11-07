#!/usr/bin/env bash
# =============================================================================
# acceptance.sh — backend acceptance journey (release plan P2-1, 2026-09-24)
# =============================================================================
# Usage:
#   scripts/release/acceptance.sh                 # read-only journey (safe on production)
#   scripts/release/acceptance.sh --full <proj>   # + write round trip on a SCRATCH project
#
# Runs the r4 CLI against the active profile (R4_PROFILE / `r4 config show`)
# and prints one PASS/FAIL line per step; exits non-zero when any step fails.
# The read-only journey found three live 500s on its first run (2026-09-24).
set -uo pipefail

mode="${1:-}"
scratch="${2:-}"
pass=0
fail=0
failed_steps=()
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

step() {
  local name="$1"
  shift
  if timeout 180 "$@" >"$tmp/out" 2>"$tmp/err"; then
    printf 'PASS  %s\n' "$name"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s  (%s)\n' "$name" "$(head -c 160 "$tmp/err" | tr '\n' ' ')"
    fail=$((fail + 1))
    failed_steps+=("$name")
  fi
}

echo "== read-only journey (profile: ${R4_PROFILE:-default})"
step "platform health"            r4 health
step "project list"               r4 project list --json
step "research loops (owner-scoped list)" r4 project loop list --json
step "skill proposals pending"    r4 skill pending --json
step "scientific catalogs"        r4 catalog list --json
step "workflows"                  r4 workflow list --json
step "endpoints"                  r4 endpoint list --json
step "models"                     r4 model list --json
step "knowledge graphs"           r4 knowledge graphs --json
step "mcp servers"                r4 mcp list --servers --json   # 2026-09-25: a 500 here went unnoticed

if [ "$mode" = "--full" ]; then
  [ -n "$scratch" ] || { echo "--full needs a SCRATCH project id" >&2; exit 2; }
  echo "== write round trip on scratch project '${scratch}'"
  printf 'Acceptance probe document %s\nline two\n' "$(date -u +%FT%TZ)" >"$tmp/probe.md"
  step "project exists or is created" bash -c "r4 project show '$scratch' >/dev/null 2>&1 || r4 project create '$scratch' --name 'release acceptance scratch'"
  step "ingest a document"           r4 project import "$scratch" "$tmp/probe.md"
  step "server export job"           r4 project export "$scratch" --json
  step "export archive downloads"    bash -c "sleep 20; job=\$(r4 project export '$scratch' --json | python3 -c 'import json,sys;print(json.load(sys.stdin).get(\"job_id\",\"\"))'); sleep 30; cd '$tmp' && r4 knowledge export-download \"\$job\""
  step "archive reads (info)"        bash -c "r4 knowledge info \$(ls '$tmp'/*.kb.tar.gz | head -1)"
fi

echo "== ${pass} passed, ${fail} failed"
if [ "$fail" -gt 0 ]; then
  printf '   failed: %s\n' "${failed_steps[@]}"
  exit 1
fi
