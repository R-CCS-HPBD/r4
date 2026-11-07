# shellcheck shell=bash
# The chat model sized to the host, and where ollama runs (2026-10-15).
#
# The RAM table (ADR-090 / onboarding decision D-7):
#
#   memory                              model          notes
#   < 12 GB                             llama3.2:3b
#   12-24 GB                            qwen2.5:7b
#   >= 24 GB unified, or >= 12 GB VRAM  qwen2.5:14b
#   >= 48 GB                            qwen3:30b      MoE, reads medium
#
# "Unified" is Apple silicon with the model on the host's own ollama
# (Metal). VRAM counts only when the model will run on that GPU (the host's
# own ollama, or a container ollama with a GPU overlay). A CPU-only Linux host
# between 24 and 48 GB therefore stays on qwen2.5:7b: it has the memory for
# 14b but not the speed. Thresholds are nominal GB, memory within 5 % and
# VRAM within 2 % (lib/host.sh TABLE_TOLERANCE_PCT, VRAM_TOLERANCE_PCT): an
# 11 GB card is not a 12 GB one, and a 44 GiB VM is not a 48 GB host.
#
# Needs: lib/common.sh, lib/host.sh.

# Args: mem_mib vram_mib unified(0|1). Prints the model.
pick_model() {
  local mem="$1" vram="$2" unified="$3" t="$TABLE_TOLERANCE_PCT"
  if mem_at_least "$mem" 48 "$t"; then printf 'qwen3:30b'
  elif { [[ "$unified" == 1 ]] && mem_at_least "$mem" 24 "$t"; } \
    || mem_at_least "$vram" 12 "$VRAM_TOLERANCE_PCT"; then printf 'qwen2.5:14b'
  elif mem_at_least "$mem" 12 "$t"; then printf 'qwen2.5:7b'
  else printf 'llama3.2:3b'
  fi
  return 0
}

# The container ollama's memory limit for MODEL with SLOTS parallel slots at
# the 32k window: weights + KV cache x slots + ~1 GiB runtime, rounded up.
# Measured for llama3.2:3b (base.env: ~6.0 GiB with 1 slot, ~9.6 GiB with
# 2); the others from their weights (Q4_K_M) and KV geometry: qwen2.5:7b
# 4.7 GB + 1.75 GiB/slot, qwen2.5:14b 9.0 GB + 6 GiB/slot, qwen3:30b
# 18.6 GB + 3 GiB/slot. Empty for a model not listed.
model_mem() {
  case "$1:$2" in
    llama3.2:3b:1|qwen2.5:7b:1) printf '8g' ;;
    llama3.2:3b:2|qwen2.5:7b:2) printf '12g' ;;
    qwen2.5:14b:1) printf '18g' ;;
    qwen2.5:14b:2) printf '24g' ;;
    qwen3:30b:1) printf '24g' ;;
    qwen3:30b:2) printf '28g' ;;
  esac
  return 0
}

# Parallel slots (ADR-093 MD-11): 2 on a capable host (>= 16 GB or a GPU)
# when the two-slot limit still leaves 10 GiB for the platform services;
# otherwise 1. Args: mem_mib vram_mib model.
pick_slots() {
  local mem="$1" vram="$2" model="$3" two
  two="$(model_mem "$model" 2)"
  if [[ -n "$two" ]] && { mem_at_least "$mem" 16 || (( vram > 0 )); } \
    && (( mem - ${two%g} * 1024 >= 10 * 1024 )); then
    printf '2'
  else
    printf '1'
  fi
  return 0
}

# ── where ollama runs ─────────────────────────────────────────────────────
# What a native ollama's listen addresses ("127.0.0.1:11434", "[::]:11434",
# "*:11434", one per line on stdin) mean for the containers:
#   none      nothing listens
#   loopback  127.0.0.1 / ::1 only: unreachable from a container on Linux
#   wide      some other address: reachable through host.containers.internal
listen_scope() {
  awk '
    NF == 0 { next }
    { a = $1; seen = 1
      if (a ~ /^127\./ || a ~ /^\[::1\]:/ || a ~ /^::1:/ || a ~ /^localhost:/) next
      wide = 1 }
    END { if (wide) print "wide"; else if (seen) print "loopback"; else print "none" }'
}

# listen_scope of the host's port 11434 (Linux), or "unknown" without ss.
ollama_listen_scope() {
  command -v ss >/dev/null 2>&1 || { printf 'unknown'; return 0; }
  ss -ltnH 'sport = :11434' 2>/dev/null | awk '{print $4}' | listen_scope
}

native_ollama_answers() { curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; }

# Where the model should run on this host, and why ("host reason").
# Args: os(Darwin|Linux|WSL) answers(0|1) scope(none|loopback|wide|unknown) cli(0|1).
#
# macOS: the host's ollama whenever one is installed (Metal, and the podman
# VM then needs only ~8 GB).
# Linux: the host's ollama only when it listens beyond loopback AND it is a
# host install (the `ollama` CLI is on PATH: it pulls the model, and the
# systemd fixes printed for it apply). One bound to 127.0.0.1 (the systemd
# unit's default) is unreachable from containers, and widening it serves an
# unauthenticated model API to the network, so the default is the container
# ollama: no host change, no sudo, and offline enforced by the egress-less
# models network. The fix to use your own is printed (loopback_ollama_fix).
# An ollama that listens widely with no CLI on the host is usually a
# container (`docker run -p 11434:11434 ollama/ollama`): not ours to pull
# into or to fix, so the stack runs its own (wide-not-ours).
# WSL2: always the container by default; whether a distro's ollama is
# reachable depends on the engine (Docker Desktop's host gateway is Windows,
# not the distro), and that has not been run yet.
# STANDALONE_MODEL_HOST in env/local.env overrides all of this.
choose_model_host() {
  local os="$1" answers="$2" scope="$3" cli="$4"
  if [[ "$os" == Darwin ]]; then
    if [[ "$answers" == 1 || "$cli" == 1 ]]; then printf 'native macos-ollama'; else printf 'container no-ollama'; fi
  elif [[ "$os" == WSL ]]; then printf 'container wsl-default'
  elif [[ "$answers" != 1 ]]; then printf 'container no-ollama'
  elif [[ "$scope" == wide && "$cli" == 1 ]]; then printf 'native listens-wide'
  elif [[ "$scope" == wide ]]; then printf 'container wide-not-ours'
  elif [[ "$scope" == unknown ]]; then printf 'container listen-unknown'
  else printf 'container loopback-only'
  fi
  return 0
}

# Every refusal of the host's ollama names the way out that needs no change
# to it.
# shellcheck disable=SC2034  # read by lib/quickstart.sh
CONTAINER_OLLAMA_HINT="Or let the stack run its own ollama: STANDALONE_MODEL_HOST=container in deploy/standalone/env/local.env, then ./r4-local up."

# The fix for a native ollama on Linux that the containers cannot reach.
loopback_ollama_fix() {
  cat <<'EOF'
To use your own ollama instead (e.g. for its GPU), let it listen beyond loopback:
     sudo systemctl edit ollama      # add:
       [Service]
       Environment="OLLAMA_HOST=0.0.0.0:11434"
       Environment="OLLAMA_CONTEXT_LENGTH=32768"
     sudo systemctl restart ollama
   then STANDALONE_MODEL_HOST=native in deploy/standalone/env/local.env and ./r4-local up.
   0.0.0.0 serves the unauthenticated ollama API to your network unless a firewall blocks 11434.
EOF
}

# The fix for a native ollama whose window is below CTX. Args: os ctx.
native_context_fix() {
  local os="$1" ctx="$2"
  if [[ "$os" == Darwin ]]; then
    printf 'launchctl setenv OLLAMA_CONTEXT_LENGTH %s   then quit and reopen Ollama\n' "$ctx"
    printf '   (launchctl setenv lasts until you log out; ./r4-local up --yes sets it again)'
  else
    printf 'sudo systemctl edit ollama   add  [Service]  Environment="OLLAMA_CONTEXT_LENGTH=%s"\n' "$ctx"
    printf '   then: sudo systemctl restart ollama   (an ollama started by hand: OLLAMA_CONTEXT_LENGTH=%s ollama serve)' "$ctx"
  fi
}

# 0 when SERVED (the window a native ollama reports) holds WANT tokens.
context_holds() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= $2 )); }
