# shellcheck shell=bash
# `standalone.sh quickstart` — the local stand-alone stack in one command
# (onboarding plan §2 Phase 1, 2026-10-15). The root wrapper `./r4-local up`
# runs it. The chain, in this order:
#
#   preflight (with fixes) -> model by RAM (D-7) -> native or container
#   ollama -> bootstrap -> up -> r4 client from the checkout -> register the
#   stack as an r4 source (D-5) -> smoke test (chat, ingest, retrieval)
#
# Idempotent: on a working stack every step only checks and reports (and
# applies a schema migration a `git pull` brought), and the token is
# re-minted. It never destroys data (no reset, no `down -v`). A fix
# that changes the host (the podman machine, the Ollama app's window) runs
# only with --yes or an interactive yes; values it writes go to the
# gitignored env/local.env, never to the tracked env/*.env.
#
# Needs: lib/common.sh, state.sh, host.sh, model.sh, preflight.sh, client.sh
# and the cmd_* verbs of standalone.sh.

QS_MODEL=""
QS_MODEL_HOST_WHY=""
QS_USAGE="usage: standalone.sh quickstart [--yes] [--model NAME]"

qs_args() {
  while (( $# )); do
    case "$1" in
      -y|--yes) export ASSUME_YES=1 ;;
      --model) [[ -n "${2:-}" && "${2:-}" != -* ]] || die "$QS_USAGE"
        QS_MODEL="$2"; shift ;;
      --model=*) QS_MODEL="${1#--model=}"; [[ -n "$QS_MODEL" ]] || die "$QS_USAGE" ;;
      *) die "${QS_USAGE}   (unknown argument '$1')" ;;
    esac
    shift
  done
  return 0
}

# STANDALONE_PROFILE, else the recorded profile, else dev for a NEW stack
# (decision D-6: dev until hardened passes T2 and the WS-08 socket lands).
# A stack that exists (its secrets or its volumes) with no recorded profile
# was brought up before profiles were recorded, under the hardened default:
# it stays hardened. Switching it to dev would publish its stores and the
# umrs sidecar on loopback and relax ZeroMQ auth over the data it holds.
qs_profile() {
  local fallback=dev origin
  origin="$(profile_origin)"
  if [[ "$origin" == default ]]; then
    if stack_exists; then
      fallback=hardened; origin="an existing stack with no recorded profile stays on the hardened default"
    else
      origin="a new stack: the quickstart's default, decision D-6"
    fi
  fi
  STANDALONE_PROFILE="$(resolve_profile "$fallback")"
  export STANDALONE_PROFILE
  # read by profile_origin, and by load_env: this line replaces the banner
  # shellcheck disable=SC2034
  PROFILE_ORIGIN="$origin"
  # shellcheck disable=SC2034
  DEV_BANNER_SHOWN=1
  if [[ "$STANDALONE_PROFILE" == dev ]]; then
    info "profile: dev (${origin}): relaxed ZeroMQ auth on loopback, for evaluation, not for sensitive data. STANDALONE_PROFILE=hardened ./r4-local up for the hardened one."
  else
    info "profile: ${STANDALONE_PROFILE} (${origin}). STANDALONE_PROFILE=dev ./r4-local up switches to dev: it publishes the stores and the umrs sidecar on loopback and relaxes ZeroMQ auth."
  fi
}

# This checkout already has a stack: its secrets, or its Postgres volume
# under either engine.
stack_exists() {
  local engine vol; vol="$(stack_project)_pg_data"
  [[ -f "$SECRETS" ]] && return 0
  for engine in docker podman; do
    command -v "$engine" >/dev/null 2>&1 || continue
    "$engine" volume inspect "$vol" >/dev/null 2>&1 && return 0
  done
  return 1
}

# Where the model runs, decided before preflight because it sizes the macOS
# VM (8 GB with the host's ollama, 16 GB with the model inside). Written to
# env/local.env as quickstart's own choice, unless you set it.
qs_model_host_intent() {
  local os answers=0 cli=0 scope=none pick
  if user_set STANDALONE_MODEL_HOST; then QS_MODEL_HOST_WHY="set by you"; return 0; fi
  os="$(uname -s)"
  if is_wsl; then os=WSL; fi
  if native_ollama_answers; then answers=1; fi
  if command -v ollama >/dev/null 2>&1; then cli=1; fi
  if [[ "$os" == Linux && "$answers" == 1 ]]; then scope="$(ollama_listen_scope)"; fi
  pick="$(choose_model_host "$os" "$answers" "$scope" "$cli")"
  auto_set STANDALONE_MODEL_HOST "${pick%% *}"
  QS_MODEL_HOST_WHY="${pick#* }"
}

qs_preflight() { QS_MOVE_DEV_PORTS=1 cmd_preflight; }

# The memory the model gets. The host's for the host's own ollama; for the
# container ollama, the engine's: Docker's MemTotal (Docker Desktop's VM on
# macOS, Linux and WSL2; the host under Docker Engine) or the macOS podman
# machine's. The host's when the engine does not say.
qs_model_memory() {
  local mem=0
  if [[ "$1" == container ]]; then
    case "${ENGINE:-}" in
      docker) mem="$(docker_mem_mib)" ;;
      podman) if [[ "$(uname -s)" == Darwin ]]; then mem="$(podman_machine_value Memory)"; fi ;;
    esac
  fi
  (( mem > 0 )) || mem="$(host_mem_mib)"
  printf '%s' "$mem"
}

# Whose memory that is, for the one line that says it.
qs_memory_whose() {
  if [[ "$1" == container && "${ENGINE:-}" == docker ]] && docker_is_desktop; then printf " (Docker Desktop's VM)"
  elif [[ "$1" == container && "${ENGINE:-}" == podman && "$(uname -s)" == Darwin ]]; then printf ' (the podman machine)'
  elif is_wsl; then printf ' (the WSL2 VM limit)'
  fi
  return 0
}

# VRAM counts only when the model will run on that GPU.
qs_usable_vram() {
  if [[ "$1" == container && "$(gpu_choice)" == none ]]; then printf '0'; else vram_mib; fi
}

# The model: --model, else a value you set, else the D-7 table. "Unified"
# is Apple silicon by its hardware (lib/host.sh is_apple_silicon), not by
# the shell's architecture, which Rosetta reports as x86_64.
qs_choose_model() {
  local host mem vram unified=0 model why problem
  host="$(model_host)"; mem="$(qs_model_memory "$host")"
  ensure_gpu_choice   # once, in this shell: qs_usable_vram runs in $(...)
  vram="$(qs_usable_vram "$host")"
  problem="$(gpu_problem)"
  if [[ "$(uname -s)" == Darwin && "$host" == native ]] && is_apple_silicon; then unified=1; fi
  info "memory $(mib_as_gib "$mem") GiB$(qs_memory_whose "$host"); GPU: $( (( vram > 0 )) && printf '%s MiB VRAM' "$vram" || printf '%s' "${problem:-none usable by the model}")"
  if [[ -n "$QS_MODEL" ]]; then
    model="$QS_MODEL"; why="--model"
    user_choice_set STANDALONE_CHAT_MODEL "$model"
  elif user_set STANDALONE_CHAT_MODEL; then
    model="$(cfg_get STANDALONE_CHAT_MODEL)"; why="set by you; kept"
  else
    model="$(pick_model "$mem" "$vram" "$unified")"; why="RAM table D-7"
    auto_set STANDALONE_CHAT_MODEL "$model"
  fi
  [[ "$host" == container ]] && qs_container_sizing "$mem" "$vram" "$model"
  ok "chat model: ${model} (${why}) on the ${host} ollama${QS_MODEL_HOST_WHY:+ (${QS_MODEL_HOST_WHY})}"
}

# Memory limit and parallel slots for the container ollama, unless set.
qs_container_sizing() {
  local slots limit
  slots="$(pick_slots "$1" "$2" "$3")"; limit="$(model_mem "$3" "$slots")"
  if [[ -z "$limit" ]]; then
    info "no size on record for $3: STANDALONE_CHAT_MEM stays $(cfg_get STANDALONE_CHAT_MEM); raise it in env/local.env if the model needs more"
    return 0
  fi
  user_set STANDALONE_CHAT_MEM || auto_set STANDALONE_CHAT_MEM "$limit"
  user_set OLLAMA_NUM_PARALLEL || auto_set OLLAMA_NUM_PARALLEL "$slots"
  info "container ollama: STANDALONE_CHAT_MEM=$(cfg_get STANDALONE_CHAT_MEM) OLLAMA_NUM_PARALLEL=$(cfg_get OLLAMA_NUM_PARALLEL) (env/local.env)"
}

qs_ollama() {
  local name; name="$(cfg_get STANDALONE_CHAT_MODEL)"
  if [[ "$(model_host)" == native ]]; then qs_native_ollama "$name"; else qs_container_ollama "$name"; fi
}

qs_container_ollama() {
  case "$QS_MODEL_HOST_WHY" in
    loopback-only) qs_loopback_notice "your ollama listens on 127.0.0.1 only, which containers cannot reach" ;;
    listen-unknown) qs_loopback_notice "your ollama answers, but without ss its listen address is unknown" ;;
  esac
  if model_is_staged "$1"; then ok "model $1 already staged in the models volume"; else cmd_model pull "$1"; fi
}

qs_loopback_notice() {
  info "$1: the stack runs its own, with no host change and no route off the host."
  loopback_ollama_fix | sed 's/^/  /'
}

qs_native_ollama() {
  native_ollama_answers || qs_start_macos_ollama \
    || die "STANDALONE_MODEL_HOST=native but no ollama answers on 127.0.0.1:11434. Start it (sudo systemctl start ollama, or: ollama serve).
   ${CONTAINER_OLLAMA_HINT}"
  if [[ "$(uname -s)" != Darwin && "$(ollama_listen_scope)" != wide ]]; then
    die "STANDALONE_MODEL_HOST=native but your ollama listens on loopback only, which containers cannot reach.
   $(loopback_ollama_fix)"
  fi
  if curl -s -m 5 http://127.0.0.1:11434/api/tags | grep -F "\"name\":\"$1\"" >/dev/null; then
    ok "model $1 present in your ollama"
  else
    command -v ollama >/dev/null 2>&1 || die "your ollama does not have $1, and there is no ollama CLI on PATH to pull it (an ollama in a container?).
   ${CONTAINER_OLLAMA_HINT}"
    ollama pull "$1" || die "pull failed for $1"
  fi
  qs_native_context "$1"
}

# macOS: start the installed Ollama app, with consent. Whether it is
# installed is read from the disk: `open -R` would reveal it in Finder
# before anyone was asked.
qs_start_macos_ollama() {
  [[ "$(uname -s)" == Darwin ]] || return 1
  ollama_app_installed || return 1
  confirm "Ollama is installed but not running. Start it?" || return 1
  open -a Ollama && wait_for_ollama
}

ollama_app_installed() { [[ -d /Applications/Ollama.app || -d "${HOME}/Applications/Ollama.app" ]]; }

wait_for_ollama() {
  local tries=0
  while (( tries < 15 )); do
    native_ollama_answers && return 0
    sleep 2; tries=$((tries + 1))
  done
  return 1
}

# The window the host's ollama really serves must hold the agent: fix it
# (macOS, with consent) or stop with the exact fix. Never registered short.
qs_native_context() {
  local want served os
  want="$(cfg_get OLLAMA_CONTEXT_LENGTH)"; want="${want:-32768}"; os="$(uname -s)"
  served="$(native_context "$1")"
  if context_holds "$served" "$want"; then ok "your ollama serves $1 with a ${served}-token window"; return 0; fi
  if [[ "$os" == Darwin ]] && confirm "Your Ollama serves $1 with a ${served:-unknown}-token window; the stack needs ${want}. Set OLLAMA_CONTEXT_LENGTH=${want} and restart the Ollama app?"; then
    qs_restart_macos_ollama "$want"
    served="$(native_context "$1")"
    if context_holds "$served" "$want"; then ok "your ollama now serves $1 with a ${served}-token window"; return 0; fi
  fi
  die "your ollama serves $1 with a ${served:-unknown}-token window; the stack needs ${want} (the agent's tools alone are ~10.5k tokens). Fix:
   $(native_context_fix "$os" "$want")
   ${CONTAINER_OLLAMA_HINT}"
}

qs_restart_macos_ollama() {
  launchctl setenv OLLAMA_CONTEXT_LENGTH "$1"
  osascript -e 'quit app "Ollama"' >/dev/null 2>&1 || true
  sleep 3
  open -a Ollama || die "could not reopen the Ollama app"
  wait_for_ollama || die "Ollama did not come back after the restart"
}

# The gateway's /health is nginx's own static answer, so it says nothing
# about umrs: umrs is asked from inside its container (doctor's probe).
stack_healthy() {
  curl -sf -m 3 -o /dev/null http://127.0.0.1:8080/health 2>/dev/null || return 1
  load_env; profile_args
  umrs_answers
}

# bootstrap once; on a rerun with umrs answering, only the schema: the
# migrations are idempotent and apply what a `git pull` brought (umrs cannot
# start on an old schema). A new stack's profile is recorded before its
# first secret, so a rerun after a failed bring-up keeps it (qs_profile).
qs_bootstrap() {
  if [[ -f "$SECRETS" ]] && stack_healthy; then
    ok "bootstrap: done before (secrets present, umrs answers in its container); skipped"
    info "schema: applying pending migrations, if any"
    cmd_migrate
    return 0
  fi
  [[ -f "$SECRETS" ]] || state_set profile "$STANDALONE_PROFILE"
  cmd_bootstrap
}

qs_up() { cmd_up; }

qs_client() { install_client; }

qs_register() { QS_QUIET_USAGE=1 cmd_register; }

qs_smoke() {
  local rc=0
  smoke_test || rc=1
  if (( rc == 0 )); then
    ok "the local stack works: chat, ingest and retrieval passed"
  else
    warn "the smoke test failed (table above). Look at: ./r4-local logs umrs   and   ./r4-local doctor"
  fi
  print_stack_usage
  info "Stop: ./r4-local down (data kept)   Status: ./r4-local status   Health: ./r4-local doctor"
  return "$rc"
}

cmd_quickstart() {
  qs_args "$@"
  qs_profile
  qs_model_host_intent
  qs_preflight
  qs_choose_model
  qs_ollama
  qs_bootstrap
  qs_up
  qs_client
  qs_register
  qs_smoke
}
