# shellcheck shell=bash
# Shared helpers for standalone.sh and its lib/*.sh (2026-10-15).
#
# Sourcing a file in lib/ only defines functions and constants: nothing runs,
# so a test can source a lib and call one function with fixed inputs. Every
# file here must run on macOS's bash 3.2 as well as bash 5: no associative
# arrays, no ${var,,}, no mapfile, and an array that may be empty is expanded
# as ${a[@]+"${a[@]}"} (set -u on bash 3.2 calls an empty array unbound).

RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; DIM=$'\033[2m'; OFF=$'\033[0m'
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$OFF" >&2; exit 1; }
warn() { printf '%s! %s%s\n' "$YEL" "$*" "$OFF" >&2; }
ok()   { printf '%s✓ %s%s\n' "$GRN" "$*" "$OFF"; }
info() { printf '%s  %s%s\n' "$DIM" "$*" "$OFF"; }

# A yes from the person at the keyboard, or --yes (ASSUME_YES=1). Without a
# terminal and without --yes the answer is no: a fix that changes the host
# never runs unasked.
confirm() {
  [[ "${ASSUME_YES:-0}" == 1 ]] && return 0
  [[ -t 0 ]] || return 1
  local answer=""
  read -r -p "$1 [y/N] " answer || return 1
  case "$answer" in y|Y|yes|Yes|YES) return 0 ;; esac
  return 1
}

# ── KEY=VALUE files (env/local.env, env/.state) ───────────────────────────
# Read and written without sourcing, so a value can never run as code and a
# variable already exported is never overwritten by reading a file.

# The last value of KEY in FILE; empty when the file or the key is missing.
kv_get() {
  local file="$1" key="$2" line=""
  [[ -f "$file" ]] || return 0
  line="$(grep -E "^${key}=" "$file" | tail -1 || true)"
  [[ -n "$line" ]] && printf '%s' "${line#*=}"
  return 0
}

kv_has() { [[ -f "$1" ]] && grep -qE "^${2}=" "$1"; }

# Set KEY=VALUE in FILE: every earlier KEY line goes, one new line is
# appended. The file keeps its inode and mode (cat >, not mv), and other
# lines, comments included, are kept in order.
kv_set() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/standalone-kv.XXXXXX")"
  if [[ -f "$file" ]]; then
    awk -v k="$key" 'index($0, k "=") != 1' "$file" >"$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat "$tmp" >"$file"
  rm -f "$tmp"
}
