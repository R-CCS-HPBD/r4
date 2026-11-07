# shellcheck shell=bash
# The stack's recorded state, and the rules that read it (2026-10-15).
#
# env/.state (gitignored, 0600) holds what `up` last ran with: the profile
# (dev or hardened) and the engine whose volumes hold this stack's data.
# Later verbs read it, so `down`, `status`, `doctor`, `logs` and `register`
# need no environment prefix. An exported STANDALONE_PROFILE or
# STANDALONE_ENGINE always wins over the file.
#
# Needs: lib/common.sh. Callers set STATE_FILE and LOCAL_ENV.

state_get() { kv_get "${STATE_FILE:?}" "$1"; }

# Record KEY=VALUE, keeping the file private to this user.
state_set() {
  local old_umask; old_umask="$(umask)"
  umask 077
  kv_set "${STATE_FILE:?}" "$1" "$2"
  umask "$old_umask"
}

# ── values quickstart chose vs values the user set ────────────────────────
# Quickstart writes its choices (model host, model, memory, slots, moved dev
# ports) to env/local.env and records each as auto_<KEY> here. A later
# quickstart may rewrite a value it chose itself, e.g. after a RAM upgrade;
# any other value is the user's and always wins: exported, in env/local.env,
# or edited into the tracked env/base.env (the README before 2026-10-15 said
# to set the chat model there). The tracked env/base.env is never written.

# The values env/base.env ships with ("KEY=value ..."): a base.env value
# that differs from its shipped one was set by the user. T0 pins these
# against base.env itself.
SHIPPED_BASE_DEFAULTS="STANDALONE_CHAT_MODEL=llama3.2:3b STANDALONE_CHAT_MEM=8g OLLAMA_NUM_PARALLEL=1"

# 0 when env/base.env holds KEY with a value other than the shipped one.
base_env_edited() {
  local key="$1" have pair
  have="$(kv_get "${ENV_DIR:?}/base.env" "$key")"
  [[ -n "$have" ]] || return 1
  for pair in $SHIPPED_BASE_DEFAULTS; do
    [[ "${pair%%=*}" == "$key" ]] && { [[ "$have" != "${pair#*=}" ]]; return; }
  done
  return 1
}

user_set() {
  local key="$1" have
  [[ -n "${!key:-}" ]] && return 0
  if ! kv_has "${LOCAL_ENV:?}" "$key"; then base_env_edited "$key"; return; fi
  have="$(kv_get "$LOCAL_ENV" "$key")"
  [[ "$have" != "$(state_get "auto_${key}")" ]]
}

auto_set() {
  kv_set "${LOCAL_ENV:?}" "$1" "$2"
  state_set "auto_$1" "$2"
}

# A value the user chose through quickstart (--model): theirs from now on.
user_choice_set() {
  kv_set "${LOCAL_ENV:?}" "$1" "$2"
  state_set "auto_$1" ""
}

# The profile for this run: STANDALONE_PROFILE > the one `up` recorded > $1
# (hardened for every verb; quickstart passes dev, decision D-6).
resolve_profile() {
  local fallback="${1:-hardened}" recorded
  recorded="$(state_get profile)"
  printf '%s' "${STANDALONE_PROFILE:-${recorded:-$fallback}}"
}

# Where the profile came from, for the one line that says so. A caller that
# decided it (quickstart) names the reason in PROFILE_ORIGIN.
profile_origin() {
  if [[ -n "${PROFILE_ORIGIN:-}" ]]; then printf '%s' "$PROFILE_ORIGIN"
  elif [[ -n "${STANDALONE_PROFILE:-}" ]]; then printf 'STANDALONE_PROFILE'
  elif [[ -n "$(state_get profile)" ]]; then printf 'recorded by up in env/.state'
  else printf 'default'
  fi
}

# The engine rule, for a host with docker, podman or both:
#   1. STANDALONE_ENGINE, when set (the override);
#   2. the engine `up` recorded: it holds this stack's volumes, and the other
#      engine would start an empty stack beside it;
#   3. docker, when its daemon answers (`docker compose` + `docker info`);
#   4. podman, when `podman compose` answers.
# Args: explicit recorded docker_ok(0|1) podman_ok(0|1). Prints the engine,
# or nothing when none is usable.
choose_engine() {
  local explicit="$1" recorded="$2" docker_ok="$3" podman_ok="$4"
  if [[ -n "$explicit" ]]; then printf '%s' "$explicit"
  elif [[ -n "$recorded" ]]; then printf '%s' "$recorded"
  elif [[ "$docker_ok" == 1 ]]; then printf 'docker'
  elif [[ "$podman_ok" == 1 ]]; then printf 'podman'
  fi
  return 0
}

# compose.docker.yaml maps host.containers.internal to the host. podman adds
# that name to every container itself; Docker (Engine and Desktop) does not.
# Args: engine override(auto|on|off, STANDALONE_HOST_GATEWAY). Returns 0
# when the overlay applies.
host_gateway_overlay_wanted() {
  case "${2:-auto}" in
    on) return 0 ;;
    off) return 1 ;;
  esac
  [[ "$1" == docker ]]
}

# Which GPU overlay the container ollama gets:
#   nvidia  compose.gpu-nvidia.yaml (deploy.resources.reservations.devices,
#           `driver: nvidia`): Docker with the NVIDIA runtime registered or
#           the toolkit's hook on PATH;
#   cdi     compose.gpu-cdi.yaml (devices: nvidia.com/gpu=all), with a CDI
#           spec on the host: podman, or Docker whose daemon has CDI enabled
#           (CDISpecDirs non-empty; off by default before Engine 28.2);
#   none    no usable GPU: nvidia-smi failed (a driver/library mismatch is a
#           failed NVML), or nothing hands the GPU to a container.
# Args: engine nvsmi_ok(0|1) nvidia_driver(0|1) cdi_spec(0|1)
# engine_cdi(0|1; podman: 1) override (auto|off|nvidia|cdi, STANDALONE_GPU).
# An explicit nvidia|cdi is taken as given.
gpu_overlay_choice() {
  local engine="$1" nvsmi_ok="$2" driver="$3" cdi="$4" engine_cdi="$5" override="${6:-auto}"
  case "$override" in
    off) printf 'none'; return 0 ;;
    nvidia|cdi) printf '%s' "$override"; return 0 ;;
  esac
  [[ "$engine" == podman ]] && engine_cdi=1
  if [[ "$nvsmi_ok" != 1 ]]; then printf 'none'
  elif [[ "$engine" == docker && "$driver" == 1 ]]; then printf 'nvidia'
  elif [[ "$cdi" == 1 && "$engine_cdi" == 1 ]]; then printf 'cdi'
  else printf 'none'
  fi
  return 0
}
