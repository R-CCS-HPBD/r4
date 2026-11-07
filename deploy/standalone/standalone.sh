#!/usr/bin/env bash
# Stand-alone R4 + UMRS lifecycle driver (ADR-090).
#
# One host, one scientist. This script is the ONLY supported entry point: it
# picks the container engine, layers the env overlays in ADR-008 order, selects
# the compose profiles, and refuses by name rather than failing obscurely.
# The repository root's ./r4-local forwards up|down|status|doctor|logs|register
# here (`./r4-local up` is `quickstart`).
#
#   ./standalone.sh quickstart [--yes] [--model NAME]  everything, in order, for
#                                    one scientist (dev profile, decision D-6):
#                                    preflight with fixes, model by RAM, ollama,
#                                    bootstrap, up, r4 client, register, smoke test
#   ./standalone.sh preflight        check the host; refuse early and loudly
#   ./standalone.sh bootstrap        generate secrets, migrate, seed buckets
#   ./standalone.sh up [--dev|--hardened]  bring the stack up; records the profile
#   ./standalone.sh register [user] [project]  register the stack as the r4
#                                    source 'standalone' (30-day token, stdin
#                                    only); your active source is not changed
#   ./standalone.sh down             stop, keep volumes
#   ./standalone.sh restart [svc...] restart services (default umrs)
#   ./standalone.sh status           service + health summary
#   ./standalone.sh logs [service]   follow logs (default: every service)
#   ./standalone.sh exec SVC CMD...  run CMD in a service, no TTY (suite vocabulary)
#   ./standalone.sh model pull NAME  stage an ollama model (needs network)
#   ./standalone.sh model list       list staged models
#   ./standalone.sh embedder pull R D stage another text embedder (needs network)
#   ./standalone.sh reranker pull [R] [D]  stage the cross-encoder (needs network)
#   ./standalone.sh migrate [--status] apply (or list) schema migrations
#   ./standalone.sh orphan-repair [--apply] [--project P]...  knowledge orphan
#                                    repair (ADR-093 MD-17); dry run by default
#   ./standalone.sh doctor           post-up conformance probe
#   ./standalone.sh token [user]     client login commands with a fresh JWT
#   ./standalone.sh model register   (re)adopt the chat model; `up` does it
#   ./standalone.sh reset --yes      DESTROY all volumes and start clean
#
# State: env/.state (gitignored) records the profile and engine `up` used, so
# later verbs need no STANDALONE_PROFILE prefix; an exported one still wins.
# Never run as root. Never edit a host file outside this directory, except
# with --yes or an interactive yes (the macOS podman machine, the Ollama app).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${HERE}/env"
# The generated secrets, per-host choices (gitignored) and the recorded
# state; all overridable so the tests never read or write this checkout's own.
SECRETS="${STANDALONE_SECRETS_FILE:-${ENV_DIR}/.env.secrets}"
LOCAL_ENV="${STANDALONE_LOCAL_ENV:-${ENV_DIR}/local.env}"
# shellcheck disable=SC2034  # read by lib/*.sh
STATE_FILE="${STANDALONE_STATE_FILE:-${ENV_DIR}/.state}"
COMPOSE_FILE="${HERE}/compose.yaml"
DEV_OVERLAY="${HERE}/compose.dev.yaml"
# host.containers.internal for Docker (podman maps it itself), and the GPU
# for the container ollama: see load_env.
DOCKER_OVERLAY="${HERE}/compose.docker.yaml"
GPU_NVIDIA_OVERLAY="${HERE}/compose.gpu-nvidia.yaml"
GPU_CDI_OVERLAY="${HERE}/compose.gpu-cdi.yaml"
# STANDALONE_BUILD=1: build the UMRS image from this checkout (see load_env).
BUILD_OVERLAY="${HERE}/compose.build.yaml"
# The published UMRS image (linux/amd64 + linux/arm64, CPU), pinned by digest;
# the same default as compose.yaml. UMRS_IMAGE overrides it.
UMRS_IMAGE_DEFAULT="ghcr.io/r-ccs-hpbd/r4-standalone-umrs@sha256:70332826aa110b284d44f795e520cd5ef79dfe2689563b1e4b650a41ffe0d04d"

for _lib in common state host model preflight client quickstart; do
  # shellcheck source=/dev/null
  . "${HERE}/lib/${_lib}.sh"
done
unset _lib

# Host floor. The server image carries torch/spacy/bentoml/opencv before a
# single GGUF is staged; below these numbers the failure mode is an OOM-killed
# container, not a clear message.
# shellcheck disable=SC2034  # read by lib/*.sh
MIN_RAM_GB="${STANDALONE_MIN_RAM_GB:-16}"
# Two different numbers, measured 2026-10-08 — do not conflate them:
#   RUN   the stack's own images total ~3.8 GB (umrs 2.37, seaweedfs 0.51,
#         pgvector 0.47, sidecar 0.37, nginx 0.04) plus data volumes.
#   BUILD building the server image from scratch took the host from 51 GB to
#         35 GB free: ~16 GB of transient layers on top of the final 2.37 GB.
# `up` needs only the first once images exist, so a single floor would refuse
# hosts that can perfectly well run the stack.
# shellcheck disable=SC2034  # read by lib/*.sh
MIN_DISK_GB="${STANDALONE_MIN_DISK_GB:-15}"
# shellcheck disable=SC2034  # read by lib/*.sh
MIN_BUILD_DISK_GB="${STANDALONE_MIN_BUILD_DISK_GB:-30}"

# Read KEY from env/base.env WITHOUT sourcing it. Sourcing would overwrite a
# value the operator already exported, which base.env's own header promises
# never happens (ADR-008 order: a set variable always wins).
cfg_get() {
  local key="$1" file_val="" f v
  # Later files win: base.env < local.env (gitignored, this host only).
  for f in "${ENV_DIR}/base.env" "$LOCAL_ENV"; do
    v="$(kv_get "$f" "$key")"
    [[ -n "$v" ]] && file_val="$v"
  done
  printf '%s' "${!key:-$file_val}"
}

# Where the chat model runs (env/local.env: STANDALONE_MODEL_HOST):
#   container  ollama in the `models` service on the internal, egress-less
#              network — offline enforced at the network tier. The default.
#   native     the host's own ollama (e.g. Metal on macOS), reached from the
#              containers as host.containers.internal:11434. Much faster on a
#              Mac than CPU inside the VM; offline is then only as strong as
#              the host's own setup.
model_host() { local h; h="$(cfg_get STANDALONE_MODEL_HOST)"; printf '%s' "${h:-container}"; }

# ── engine detection ──────────────────────────────────────────────────────
# Supported: `docker compose` (v2) and `podman compose` (the v2-compatible
# shim, podman >= 4.7). The Python `podman-compose` is refused by name: its
# healthcheck + depends_on:service_healthy + profiles support is uneven and the
# entire bring-up ordering depends on all three.
#
# A host with both (Linux with Docker Engine and podman, a Mac with Docker
# Desktop and a podman machine): lib/state.sh choose_engine — STANDALONE_ENGINE,
# else the engine `up` recorded in env/.state (it holds the volumes), else
# docker when its daemon answers, else podman. A `docker` that is podman
# underneath (the podman-docker shim) counts as podman. On macOS podman is
# usable before its machine runs: preflight creates or starts the machine,
# then checks `podman compose` — except with Docker Desktop installed (its
# app bundle — docker_desktop_installed; a bare docker CLI is not Desktop)
# but not running and no podman machine: then the run stops and names both
# choices (podman_usable, no_engine). An exported or recorded engine must answer.
# Detected once per process.
detect_engine() {
  [[ "${ENGINE_DETECTED:-0}" == 1 ]] && return 0
  local explicit="${STANDALONE_ENGINE:-}" recorded docker_ok=0 podman_ok=0
  recorded="$(state_get engine)"
  if [[ -z "$explicit" && -z "$recorded" ]]; then
    if docker_answers; then
      if docker_is_podman; then podman_ok=1; else docker_ok=1; fi
    elif podman_usable; then podman_ok=1
    fi
  fi
  ENGINE="$(choose_engine "$explicit" "$recorded" "$docker_ok" "$podman_ok")"
  [[ -n "$ENGINE" ]] || no_engine
  case "$ENGINE" in
    docker) COMPOSE=(docker compose); ENGINE_BIN=docker ;;
    podman) refuse_python_podman_compose; COMPOSE=(podman compose); ENGINE_BIN=podman ;;
    *) die "unknown STANDALONE_ENGINE='${ENGINE}' (expected 'docker' or 'podman')" ;;
  esac
  if [[ -n "$explicit$recorded" ]]; then engine_must_answer "$explicit"; fi
  ENGINE_DETECTED=1
}

docker_answers() { docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

# `podman compose` works; on macOS, podman installed is enough here (its
# compose needs the machine, which preflight creates or starts) — unless
# Docker Desktop is installed but not running (docker_answers already
# failed). Then podman counts only when its machine exists, with a warning
# naming Docker Desktop; with no machine, no_engine asks to start Docker
# Desktop and names STANDALONE_ENGINE=podman, rather than a podman machine
# being made for a Docker Desktop user.

# Docker Desktop is installed: its app bundle, not a docker CLI. The CLI and
# its compose plugin are client-only and answer without it (brew's docker
# used with podman, or colima), so only the bundle says Desktop is there —
# which is also what `open -a Docker` needs. STANDALONE_DESKTOP_APP overrides
# the bundle path (tests, unusual installs).
docker_desktop_installed() {
  [[ "$(uname -s)" == Darwin ]] || return 1
  [[ -d "${STANDALONE_DESKTOP_APP:-/Applications/Docker.app}" || -d "${HOME}/Applications/Docker.app" ]]
}

podman_usable() {
  podman compose version >/dev/null 2>&1 && return 0
  [[ "$(uname -s)" == Darwin ]] || return 1
  command -v podman >/dev/null 2>&1 || return 1
  docker compose version >/dev/null 2>&1 || return 0
  docker_desktop_installed || return 0
  podman machine inspect >/dev/null 2>&1 || return 1
  warn "Docker Desktop is installed but not running; using podman, whose machine exists. For Docker Desktop instead: start it, then STANDALONE_ENGINE=docker (its own, separate volumes)."
}

# The engine named by STANDALONE_ENGINE or env/.state answers; otherwise
# stop now, naming it, not later as a failed pull. podman on macOS is left
# to preflight, which offers to start its machine. Args: explicit.
engine_must_answer() {
  local origin="recorded for this stack (env/.state)"
  [[ -n "$1" ]] && origin="chosen by STANDALONE_ENGINE"
  case "$ENGINE" in
    docker) docker info >/dev/null 2>&1 \
      || die "docker is ${origin}, but its daemon does not answer. Start it (Docker Desktop, or: sudo systemctl start docker), or STANDALONE_ENGINE=podman for the podman engine (its own, separate volumes)." ;;
    podman) [[ "$(uname -s)" == Darwin ]] || podman info >/dev/null 2>&1 \
      || die "podman is ${origin}, but 'podman info' fails (see: podman info). Fix podman, or STANDALONE_ENGINE=docker for the docker engine (its own, separate volumes)." ;;
  esac
}

# The provider `podman compose` delegates to, from its banner; empty when
# `podman compose` does not run.
podman_compose_provider() {
  podman compose version 2>&1 | grep -oE 'compose provider "[^"]+"' | head -1 || true
}

# `podman compose` is a shim that delegates to an external provider and names
# it in a banner. If that provider is the Python podman-compose, refuse.
refuse_python_podman_compose() {
  local provider; provider="$(podman_compose_provider)"
  if [[ "$provider" == *podman-compose\"* ]]; then
    die "'podman compose' is delegating to the Python podman-compose (${provider}), which is not supported.
   Install Docker Compose v2 so podman uses it as the provider."
  fi
}

# After the macOS podman machine runs: `podman compose` must work now.
podman_compose_ready() {
  refuse_python_podman_compose
  podman compose version >/dev/null 2>&1 && return 0
  die "podman runs, but 'podman compose' finds no compose provider. Install Docker Compose v2, which podman then uses:
   $(compose_provider_fix)"
}

compose_provider_fix() {
  case "$(os_id)" in
    darwin) printf 'brew install docker-compose' ;;
    fedora|rhel|centos|rocky|almalinux) printf 'sudo dnf install -y docker-compose' ;;
    debian|ubuntu|linuxmint|pop) printf 'sudo apt-get install -y docker-compose-v2   (or docker-compose-plugin from Docker'"'"'s repository)' ;;
    *) printf 'install the docker-compose v2 binary with your package manager' ;;
  esac
}

no_engine() {
  # macOS: reached with podman installed only when Docker Desktop is
  # installed (docker_desktop_installed) but not running and podman has no
  # machine (podman_usable). A bare docker CLI never gets this message.
  if [[ "$(uname -s)" == Darwin ]] && command -v podman >/dev/null 2>&1 && docker_desktop_installed; then
    die "Docker Desktop is installed but not running, and podman has no machine yet.
   Start Docker Desktop (open -a Docker), then run this again.
   Or use podman: STANDALONE_ENGINE=podman in front of the same command; preflight then offers to create its machine (its own, separate volumes)."
  fi
  if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
    warn "docker is installed, but its daemon does not answer (start Docker Desktop, or: sudo systemctl start docker)."
  fi
  if command -v podman-compose >/dev/null 2>&1; then
    die "podman-compose (the Python implementation) is not supported for this stack.
   Install podman >= 4.7 so that 'podman compose' works, or use Docker Compose v2.
   Reason: uneven healthcheck / depends_on:service_healthy / profiles support."
  fi
  if command -v podman >/dev/null 2>&1; then
    die "podman is installed, but 'podman compose' does not run: it needs podman >= 4.7 and a compose provider (Docker Compose v2):
   $(compose_provider_fix)"
  fi
  die "no supported container engine found (or its daemon is not running). Install Docker Compose v2 or podman >= 4.7."
}

# ── env layering (ADR-008 order; a set variable always wins) ──────────────
# The profile: STANDALONE_PROFILE > the one `up` recorded > hardened. dev is
# only ever chosen on purpose: STANDALONE_PROFILE=dev, `up --dev`, quickstart
# (decision D-6), or a record of one of those.
load_env() {
  local profile; profile="$(resolve_profile hardened)"
  local overlay="${ENV_DIR}/profile.${profile}.env"
  [[ -f "$overlay" ]] || die "unknown profile '${profile}' (no ${overlay})"

  ENV_ARGS=(--env-file "${ENV_DIR}/base.env" --env-file "$overlay")
  if [[ -f "$LOCAL_ENV" ]]; then
    ENV_ARGS+=(--env-file "$LOCAL_ENV")
  fi
  if [[ -f "$SECRETS" ]]; then
    ENV_ARGS+=(--env-file "$SECRETS")
  fi

  if [[ "$profile" == "dev" ]]; then dev_banner; fi
  ACTIVE_PROFILE="$profile"
  # Host-access contract: only the dev profile publishes the stores on
  # loopback. Conformance lanes use `compose exec -T`, the one form valid in
  # both profiles.
  FILE_ARGS=(-f "$COMPOSE_FILE")
  # An `[[ ]] && ...` here would be the function's last command, so a false
  # condition would return 1 and `set -e` would abort the hardened path.
  if [[ "$profile" == "dev" ]]; then
    FILE_ARGS+=(-f "$DEV_OVERLAY")
  fi
  if build_wanted; then
    FILE_ARGS+=(-f "$BUILD_OVERLAY")
  fi
  engine_overlays
}

# STANDALONE_BUILD=1 (exported or in env/local.env): build the UMRS image
# from this checkout (compose.build.yaml) instead of pulling the published one.
build_wanted() { [[ "$(cfg_get STANDALONE_BUILD)" == 1 ]]; }

umrs_image_ref() {
  local ref; ref="$(cfg_get UMRS_IMAGE)"
  printf '%s' "${ref:-$UMRS_IMAGE_DEFAULT}"
}

# The published image is pulled here, with the engine's own CLI, before any
# compose command needs it: `podman login` keeps its credentials where the
# compose provider may not look, and a failed pull deserves its own message.
ensure_umrs_image() {
  build_wanted && return 0
  local ref; ref="$(umrs_image_ref)"
  "$ENGINE_BIN" image inspect "$ref" >/dev/null 2>&1 && return 0
  info "pulling the UMRS image (about 1 GB, once): ${ref}"
  "$ENGINE_BIN" pull "$ref" && return 0
  die "could not pull ${ref}.
  While the image is private, log in once with a GitHub token that has read:packages:
     ${ENGINE_BIN} login ghcr.io -u <github-user>
  or build it from this checkout instead (needs ${MIN_BUILD_DISK_GB} GB free):
     STANDALONE_BUILD=1 ./standalone.sh up"
}

# Once per process (quickstart says it in one line instead).
dev_banner() {
  [[ "${DEV_BANNER_SHOWN:-0}" == 1 ]] && return 0
  DEV_BANNER_SHOWN=1
  printf '%s┏━ DEV PROFILE ━ relaxed transport auth, throwaway state ━┓%s\n' "$RED" "$OFF" >&2
  printf '%s┃ Not for real research data. Use the hardened default.    ┃%s\n' "$RED" "$OFF" >&2
  printf '%s┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛%s\n' "$RED" "$OFF" >&2
}

# compose.docker.yaml when the engine is Docker (STANDALONE_HOST_GATEWAY=
# auto|on|off, exported or in env/local.env, overrides), and the container
# ollama's GPU overlay.
engine_overlays() {
  local hg; hg="$(cfg_get STANDALONE_HOST_GATEWAY)"
  if host_gateway_overlay_wanted "${ENGINE:-}" "${hg:-auto}"; then
    FILE_ARGS+=(-f "$DOCKER_OVERLAY")
  fi
  ensure_gpu_choice
  case "$GPU_CHOICE" in
    nvidia) FILE_ARGS+=(-f "$GPU_NVIDIA_OVERLAY") ;;
    cdi) FILE_ARGS+=(-f "$GPU_CDI_OVERLAY") ;;
  esac
  return 0
}

# The GPU overlay for the container ollama (lib/state.sh gpu_overlay_choice),
# probed once per process: ensure_gpu_choice sets GPU_CHOICE in the calling
# shell, so call it directly, never inside $(...). none for a native model
# host (that ollama finds its own GPU) and on macOS (no GPU passthrough into
# the VM). STANDALONE_GPU=auto|off|nvidia|cdi, exported or in env/local.env,
# overrides.
ensure_gpu_choice() {
  [[ -n "${GPU_CHOICE:-}" ]] || GPU_CHOICE="$(probe_gpu_choice)"
  return 0
}

gpu_choice() { ensure_gpu_choice; printf '%s' "$GPU_CHOICE"; }

probe_gpu_choice() {
  local want nvsmi=0 driver=0 cdi=0 engine_cdi=0
  want="$(cfg_get STANDALONE_GPU)"; want="${want:-auto}"
  if [[ "$want" == off || "$(model_host)" == native || "$(uname -s)" != Linux ]]; then
    printf 'none'; return 0
  fi
  if nvsmi_works; then nvsmi=1; fi
  if cdi_spec_present; then cdi=1; fi
  if [[ "${ENGINE:-}" == docker ]]; then
    if docker_has_nvidia_runtime || nvidia_hook_present; then driver=1; fi
    if (( cdi )) && docker_cdi_enabled; then engine_cdi=1; fi
  fi
  gpu_overlay_choice "${ENGINE:-}" "$nvsmi" "$driver" "$cdi" "$engine_cdi" "$want"
}

# Which compose profiles to start: the real environment wins, then the active
# profile overlay (env/profile.<name>.env), then the default. `agent` (the MCP
# container) is NOT in the default: it speaks the SSE dialect and nothing
# registers it, so it is unreachable from the agent until that is fixed (plan
# §3b.2). Opt in with STANDALONE_PROFILES=core,models-local,agent.
active_profiles() {
  local from_file=""
  [[ -f "${ENV_DIR}/profile.${ACTIVE_PROFILE:-hardened}.env" ]] && \
    from_file="$(grep -E '^STANDALONE_PROFILES=' "${ENV_DIR}/profile.${ACTIVE_PROFILE:-hardened}.env" | tail -1 | cut -d= -f2-)"
  printf '%s' "${STANDALONE_PROFILES:-${from_file:-core,models-local}}"
}

profile_args() {
  local profiles; profiles="$(active_profiles)"
  PROFILE_ARGS=()
  local p
  IFS=',' read -ra _ps <<<"$profiles"
  for p in "${_ps[@]}"; do
    [[ "$p" == "core" ]] && continue   # core has no `profiles:` key
    # With a native model host there is no models container to start.
    [[ "$p" == "models-local" && "$(model_host)" == "native" ]] && continue
    PROFILE_ARGS+=(--profile "$p")
  done
}

# NOTE: "${arr[@]:-}" yields ONE EMPTY ARGUMENT when arr is empty, which
# compose rejects ('unknown docker command: ""'). The +"..." form expands to
# nothing at all, and is also safe under `set -u` on bash 3.2 (macOS).
compose() {
  "${COMPOSE[@]}" "${FILE_ARGS[@]}" "${ENV_ARGS[@]}" \
    ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} "$@"
}

# ── preflight: lib/preflight.sh (cmd_preflight) ───────────────────────────

# ── bootstrap ─────────────────────────────────────────────────────────────
gen_secret() { LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 64; }

# Keys a later release added to the generated set: appended to an existing
# secrets file (never printed, never overwriting a key that is present), so
# an installation bootstrapped before them gains them on its next bootstrap.
add_missing_secrets() {
  if ! grep -q '^UMRS_SECRET_KEY=' "$SECRETS"; then
    ( umask 077; echo "UMRS_SECRET_KEY=$(gen_secret)" >>"$SECRETS" )
    ok "added UMRS_SECRET_KEY (bundle signing) to ${SECRETS}"
  fi
}

# The generated secrets, written once (bootstrap); never printed.
write_secrets() {
  {
    echo "# Generated by standalone.sh bootstrap on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# 0600. Never commit. Never print."
    echo "POSTGRES_USER=umrs"
    echo "POSTGRES_PASSWORD=$(gen_secret)"
    # SeaweedFS admin identity (ADR-055). The IAM trust policy pins the
    # principal to "admin-" + the first 8 chars of S3_ROOT_USER.
    echo "S3_ROOT_USER=$(gen_secret | head -c 20)"
    echo "S3_ROOT_PASSWORD=$(gen_secret)"
    # Pre-generated here on purpose: render_seaweedfs_iam.sh would otherwise
    # mint it with `base64 -w0`, which is GNU-only and fails on macOS, and
    # would append it to a .env this profile does not use.
    echo "S3_STS_SIGNING_KEY=$(openssl rand -base64 32 2>/dev/null || gen_secret)"
    echo "JWT_SECRET=$(gen_secret)"
    echo "UMRS_HANDSHAKE_HMAC_SECRET=$(gen_secret)"
    echo "LITELLM_MASTER_KEY=sk-$(gen_secret | head -c 32)"
    # HMAC key that signs knowledge bundles (INV-13); without it every
    # export is refused unless the caller opts out (allow_unsigned).
    echo "UMRS_SECRET_KEY=$(gen_secret)"
  } >"$SECRETS"
}

cmd_bootstrap() {
  cmd_preflight
  if [[ -f "$SECRETS" ]]; then
    ok "secrets already present (${SECRETS}) — not regenerating"
    add_missing_secrets
  else
    # umask in a subshell: in this shell it would make every later file
    # private, quickstart's .venv included.
    ( umask 077; write_secrets )
    chmod 600 "$SECRETS"
    ok "generated ${SECRETS} (0600)"
  fi

  # The secrets are read in a subshell: exported into this shell they would
  # reach every later child process (quickstart's uv, make and r4 among
  # them), and JWT_SECRET mints admin tokens for the stack.
  ( render_secret_files )

  # Text embedder: bundled in the umrs image (e5-base-v2). A model chosen in
  # env/local.env must already be staged — the runtime has no network.
  local emb; emb="$(cfg_get UMRS_KNOWLEDGE_EMBED_MODEL)"
  if [[ -z "$emb" ]]; then
    ok "text embedder: the bundled e5-base-v2"
  elif embedder_is_staged "$(basename "$emb")"; then
    ok "text embedder ${emb} staged"
  else
    die "UMRS_KNOWLEDGE_EMBED_MODEL=${emb} is not staged: ./standalone.sh embedder pull <hf-repo> $(basename "$emb")"
  fi

  # Stores first, then schema, then the bucket — umrs must never boot healthy
  # against an empty schema (plan §3e).
  detect_engine; load_env; profile_args
  info "starting postgres + seaweedfs"
  compose up -d --wait postgres seaweedfs || die "stores did not become healthy"
  # The volumes now live under this engine; later verbs follow it.
  state_set engine "$ENGINE"
  info "applying migrations"
  run_migrations
  info "creating bucket $(cfg_get S3_BUCKET)"
  compose run --rm --no-deps -T -e BUCKET="$(cfg_get S3_BUCKET)" umrs python -c '
import os, boto3, botocore
s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"],
                  aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["S3_SECRET_KEY"], region_name="us-east-1")
b = os.environ["BUCKET"]
try:
    s3.head_bucket(Bucket=b); print("bucket exists:", b)
except botocore.exceptions.ClientError:
    s3.create_bucket(Bucket=b); print("bucket created:", b)
' || die "bucket creation failed"
  ok "bootstrap complete"
  info "Next: ./standalone.sh up, then ./standalone.sh doctor"
}

# Files rendered from the secrets, which this function exports: call it in
# a subshell, `( render_secret_files )`.
render_secret_files() {
  set -a
  # shellcheck disable=SC1090
  . "$SECRETS"
  set +a
  # SeaweedFS IAM/STS config — ADR-043 attempt-scoped credentials fail closed
  # without it. Reuses the shared renderer every cluster profile sources rather
  # than minting a second one.
  # shellcheck disable=SC1091
  . "${HERE}/../../umrs/deploy/_common/render_seaweedfs_iam.sh"
  render_seaweedfs_iam "${HERE}"
  ok "rendered ${HERE}/seaweedfs/iam.json"

  # The server's OWN admin S3 credential. conf_umrs.json's schema requires
  # `admin_s3_configuration_file` (umrs/shared/json_config.py:228), so this is
  # not optional. It is NOT the gap-D-16 lane: D-16 is about per-user s3cfg
  # files delivered to CLIENTS; this one never leaves the server container.
  umask 077
  cat >"${HERE}/s3cfg" <<EOF
[default]
host_base = seaweedfs:9000
host_bucket = seaweedfs:9000
bucket_location = us-east-1
use_https = False
check_ssl_certificate = False
access_key = ${S3_ROOT_USER}
secret_key = ${S3_ROOT_PASSWORD}
EOF
  chmod 600 "${HERE}/s3cfg"
  ok "rendered ${HERE}/s3cfg (0600, server-side only)"
}

# ── lifecycle ─────────────────────────────────────────────────────────────
# Profile-aware required-variable checks. These cannot live in the compose file
# as `${VAR:?}`: compose interpolates every service at parse time regardless of
# --profile, so a required variable on a profile-gated service breaks the others.
require_for_profile() {
  case ",$(active_profiles)," in
    *,models-local,*)
      local chat; chat="$(cfg_get STANDALONE_CHAT_MODEL)"
      [[ -n "$chat" ]] || die "profile models-local needs STANDALONE_CHAT_MODEL (env/local.env; e.g. llama3.2:3b)."
      if [[ "$(model_host)" == "native" ]]; then
        curl -sf -m 3 http://127.0.0.1:11434/api/tags >/dev/null \
          || die "STANDALONE_MODEL_HOST=native but no ollama answers on 127.0.0.1:11434. Start it: ollama serve"
        curl -s -m 3 http://127.0.0.1:11434/api/tags | grep "\"name\":\"${chat}\"" >/dev/null \
          || die "native ollama does not have '${chat}'. Pull it: ollama pull ${chat}"
      elif ! model_is_staged "$chat"; then
        die "chat model '${chat}' is not staged. The runtime network has no egress, so pull it first:
   ./standalone.sh model pull ${chat}"
      fi
      ;;
  esac
}

cmd_up() {
  # The floor is enforced here too: `preflight` can be run once and forgotten,
  # and an out-of-disk bring-up fails with symptoms that look like app bugs.
  cmd_preflight >/dev/null || die "preflight failed. Run ./standalone.sh preflight to see why."
  [[ -f "$SECRETS" ]] || die "no ${SECRETS}. Run: ./standalone.sh bootstrap"
  detect_engine; load_env; profile_args; require_for_profile
  info "profile=${ACTIVE_PROFILE} ($(profile_origin)) profiles=$(active_profiles) engine=${ENGINE} files=$(overlay_names)"
  ensure_umrs_image
  compose up -d --wait --remove-orphans || die "stack did not become healthy. See: ./standalone.sh logs umrs"
  # Later verbs (down, status, logs, doctor, register) read these.
  state_set profile "$ACTIVE_PROFILE"
  state_set engine "$ENGINE"
  # nginx resolved its upstreams when it started; containers recreated by this
  # `up` may have new addresses (see cmd_restart).
  compose exec -T gateway-nginx nginx -s reload >/dev/null 2>&1 || true
  case ",$(active_profiles)," in
    *,models-local,*) register_model || die "model registration failed" ;;
  esac
  ok "stack up (profile ${ACTIVE_PROFILE}, recorded in env/.state). Register it with r4:  ./standalone.sh register"
}

overlay_names() {
  local f out=""
  for f in "${FILE_ARGS[@]}"; do
    [[ "$f" == -f ]] && continue
    out="${out}${out:+,}$(basename "$f")"
  done
  printf '%s' "$out"
}

cmd_down()   { detect_engine; load_env; profile_args; compose down; ok "stopped (volumes kept)"; }
# Restart one service (default: umrs). With the dev overlay's live source
# mounts this is the whole edit-run loop for UMRS code — no image rebuild.
cmd_restart() {
  detect_engine; load_env; profile_args
  local svcs=("$@")
  (( ${#svcs[@]} )) || svcs=(umrs)
  compose restart "${svcs[@]}"
  # nginx resolves its upstream hostnames (umrs, gateway-sidecar) once, at
  # config load. A restarted container can come back on a new address, and
  # nginx would keep dialling the old one: 503 "Host is unreachable". A reload
  # re-resolves them without dropping the gateway.
  if [[ " ${svcs[*]} " =~ " "(umrs|gateway-sidecar)" " ]]; then
    if compose exec -T gateway-nginx nginx -s reload >/dev/null 2>&1; then
      info "gateway re-resolved its upstreams"
    else
      warn "could not reload gateway-nginx; run: ./standalone.sh restart gateway-nginx"
    fi
  fi
  ok "restarted ${svcs[*]}"
}
cmd_status() { detect_engine; load_env; profile_args; compose ps; }
# No service: every service (an empty-string argument is 'no such service').
cmd_logs()   { detect_engine; load_env; profile_args; compose logs -f "$@"; }

# One command in a service, without a TTY. `compose exec -T` is the vocabulary
# valid in both profiles (hardened publishes no store port; plan §3c.5 #3), so
# the live conformance suite (SL-CONF T2) reaches Postgres and umrs this way.
cmd_exec()   { detect_engine; load_env; profile_args; compose exec -T "$@"; }

cmd_reset() {
  [[ "${1:-}" == "--yes" ]] || die "reset DESTROYS all local platform data (projects, knowledge, vectors, artifacts, S3 objects). Re-run with --yes if that is what you want."
  detect_engine; load_env; profile_args
  compose down -v
  ok "all volumes removed"
  warn "NOT touched: ~/.config/r4/** (sources, profiles, companion stores) and ${SECRETS}."
  info "To start completely clean, also remove those by hand."
}

# Weight staging. The models network is `internal: true`, so nothing can be
# fetched at runtime — this is the one place that is allowed egress, and it is
# an explicit operator action rather than a surprise at first chat.
_ollama_vol() { echo "${COMPOSE_PROJECT_NAME:-ai4s-standalone}_umrs_models"; }

# `ollama pull`/`list` are CLIENT commands: they talk to a running ollama
# server over 127.0.0.1:11434 and fail with "could not connect to ollama
# server" if none is up. So each staging call starts a throwaway server inside
# the same container, waits for it, runs the command, and exits.
_ollama_run() {
  local vol; vol="$(_ollama_vol)"
  # `volume inspect` works on both engines; `volume exists` is podman-only.
  # The compose labels make the volume the stack's own: Docker Compose warns
  # about (and on some versions refuses) a same-named volume it did not create.
  "${ENGINE_BIN}" volume inspect "$vol" >/dev/null 2>&1 || "${ENGINE_BIN}" volume create \
    --label "com.docker.compose.project=${COMPOSE_PROJECT_NAME:-ai4s-standalone}" \
    --label com.docker.compose.volume=umrs_models "$vol" >/dev/null
  "${ENGINE_BIN}" run --rm -v "${vol}:/models" \
    -e OLLAMA_MODELS=/models/ollama \
    --entrypoint sh "$(cfg_get OLLAMA_IMAGE)" -c \
    "ollama serve >/tmp/serve.log 2>&1 &
     for i in \$(seq 1 60); do ollama list >/dev/null 2>&1 && break; sleep 1; done
     $*"
}

model_is_staged() {
  # grep >/dev/null, not -q: an early exit would SIGPIPE awk under pipefail.
  _ollama_run "ollama list" 2>/dev/null | awk 'NR>1{print $1}' | grep -x "$1" >/dev/null
}

cmd_model() {
  detect_engine
  case "${1:-}" in
    pull)
      local name="${2:-$(cfg_get STANDALONE_CHAT_MODEL)}"
      [[ -n "$name" ]] || die "usage: standalone.sh model pull <name>   (e.g. llama3.2:3b)"
      if [[ "$(model_host)" == "native" ]]; then
        command -v ollama >/dev/null || die "STANDALONE_MODEL_HOST=native but no ollama CLI on PATH"
        ollama pull "$name" || die "pull failed for ${name}"; ok "staged ${name} in the native ollama"; return 0
      fi
      info "pulling ${name} into volume $(_ollama_vol) — this step needs network"
      _ollama_run "ollama pull '${name}'" || die "pull failed for ${name}"
      ok "staged ${name}"
      [[ "$(cfg_get STANDALONE_CHAT_MODEL)" == "$name" ]] \
        || info "Set STANDALONE_CHAT_MODEL=${name} in env/local.env if this is the chat model."
      ;;
    list) if [[ "$(model_host)" == "native" ]]; then ollama list; else _ollama_run "ollama list"; fi ;;
    register) load_env; profile_args; register_model "${2:-}" || die "model registration failed" ;;
    *) die "usage: standalone.sh model {pull <name>|list|register [name]}" ;;
  esac
}

# The context window a native ollama actually serves for a model (loads it
# first). The compose file's OLLAMA_CONTEXT_LENGTH reaches only the CONTAINER
# ollama; a native one (e.g. the macOS app) uses its own setting, 4096 by
# default — measured 2026-10-09.
native_context() {
  local name="$1"
  curl -s -m 120 http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"${name}\",\"prompt\":\"\",\"keep_alive\":\"30m\"}" >/dev/null 2>&1 || true
  curl -s -m 5 http://127.0.0.1:11434/api/ps | python3 -c '
import json, sys
name = sys.argv[1]
models = json.load(sys.stdin).get("models", [])
print(next((m.get("context_length", "") for m in models if m.get("name") == name), ""))' "$name" 2>/dev/null
}

# A native ollama serving less than the configured window, or a window it
# does not report (/api/ps without context_length, or a model that did not
# load within the warm-up): never registered silently (onboarding plan §2).
# Stop with the exact fix, or, with STANDALONE_ALLOW_SHORT_CONTEXT=1,
# register the window actually served (the configured one when unknown) so
# the agent budgets honestly. Args: model served(number or empty)
# configured. Called directly, never in $(...), so its die stops the script.
short_context_verdict() {
  local name="$1" served="$2" ctx="$3" window
  window="a ${served}-token window"
  [[ -n "$served" ]] || window="a window it does not report (no context_length in /api/ps, or the model did not load within 120 s)"
  if [[ "${STANDALONE_ALLOW_SHORT_CONTEXT:-0}" != 1 ]]; then
    die "your ollama serves '${name}' with ${window}; the stack needs ${ctx} (the agent's tool set alone is ~10.5k tokens). Fix:
   $(native_context_fix "$(uname -s)" "$ctx")
   (or STANDALONE_ALLOW_SHORT_CONTEXT=1 to register it anyway)"
  fi
  warn "registering '${name}' although your ollama serves it with ${window} (STANDALONE_ALLOW_SHORT_CONTEXT=1; ${ctx} configured): the agent's tools may be truncated."
}

# Adopt the chat model as an EndpointRegistry row (ADR-090 D-3): POST creates it
# as PROVISIONING, PUT makes it READY — consumers filter [READY, DEGRADED], so
# both calls are required. Runs inside umrs with a 1-day admin token minted there
# against the container's own 127.0.0.1:8082; the token never leaves it.
# Idempotent: an existing row is re-marked ready, not duplicated.
register_model() {
  local name host port ctx served
  name="${1:-$(cfg_get STANDALONE_CHAT_MODEL)}"
  if [[ "$(model_host)" == "native" ]]; then host="host.containers.internal"; else host="models"; fi
  port=11434
  ctx="$(cfg_get OLLAMA_CONTEXT_LENGTH)"; ctx="${ctx:-32768}"
  if [[ "$(model_host)" == "native" ]]; then
    served="$(native_context "$name")"
    if ! context_holds "$served" "$ctx"; then
      short_context_verdict "$name" "$served" "$ctx"
      if [[ "$served" =~ ^[0-9]+$ ]]; then ctx="$served"; fi
    fi
  fi
  compose exec -T -e NAME="$name" -e HOST="$host" -e PORT="$port" \
    -e CTX="$ctx" umrs python - <<'PYEOF'
import json, os, subprocess, sys, urllib.request, urllib.error
tok = json.loads(subprocess.check_output(
    ["umrs-server", "admin", "token", "generate-jwt", "--user", "admin", "--roles", "admin",
     "--project", "default", "--days", "1", "--no-gateway-url", "--json"]))["token"]
def call(method, path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8082" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
name, job = os.environ["NAME"], "standalone-chat"
ctx = int(os.environ.get("CTX") or 32768)
st, out = call("POST", "/umrs/endpoints", {
    "project_id": "default", "model": name, "job_id": job,
    "host": os.environ["HOST"], "port": int(os.environ["PORT"]), "backend": "compose", "ttl_sec": 0,
    # max_model_len, NOT context_window: api/llm.py:879-881 reads only this key.
    "metadata": {"max_model_len": ctx, "max_output_tokens": 8192, "access": ["system"],
                 "service_kind": "openai_compat", "locality": "local", "served_model_name": name}})
if st not in (200, 201, 409):
    sys.exit(f"register failed: HTTP {st} {out[:300]}")
st, out = call("PUT", f"/umrs/endpoints/{job}/status", {"status": "ready"})
if st != 200:
    sys.exit(f"status update failed: HTTP {st} {out[:300]}")
print(f"registered {name} at {os.environ['HOST']}:{os.environ['PORT']} (max_model_len {ctx}), READY")
PYEOF
}

# Another text embedder, or the cross-encoder, into the shared models volume
# (the image already bundles e5-base-v2). Needs network — the runtime does not
# have it. Both use the same staging code as the image build
# (umrs.knowledge.text_embedder): weights, config and tokenizer only.
_umrs_image() { echo "${COMPOSE_PROJECT_NAME:-ai4s-standalone}-umrs"; }
# Staged = what the model loaders accept offline: config.json AND
# model.safetensors (text_embedder.is_staged, rag.search.reranker_staged).
model_dir_is_staged() {
  # shellcheck disable=SC2016  # $1 expands in the container's sh, by design
  "${ENGINE_BIN}" run --rm -v "$(_ollama_vol):/models:ro" docker.io/library/alpine:latest \
    sh -c 'test -f "$1/config.json" && test -f "$1/model.safetensors"' sh "/models/$1" >/dev/null 2>&1
}
embedder_is_staged() { model_dir_is_staged "$1"; }
_stage_hf_model() {
  local repo="$1" name="$2" what="$3"
  [[ "$name" != */* && "$name" != .* ]] || die "${what} dir-name '${name}' must be a plain directory name under /models"
  info "staging ${repo} -> /models/${name} (this step needs network)"
  "${ENGINE_BIN}" run --rm -v "$(_ollama_vol):/models" -e HF_HOME=/tmp/hf \
    --entrypoint python "$(_umrs_image)" -m umrs.knowledge.text_embedder stage \
    --repo "$repo" --dest "/models/${name}" || die "${what} pull failed"
}
cmd_embedder() {
  detect_engine
  case "${1:-}" in
    pull)
      local repo="${2:-}" name="${3:-}"
      [[ -n "$repo" && -n "$name" ]] || die "usage: standalone.sh embedder pull <hf-repo> <dir-name> (e5-base-v2 is already bundled)"
      _stage_hf_model "$repo" "$name" embedder
      ok "embedder staged: /models/${name} — set UMRS_KNOWLEDGE_EMBED_MODEL=/models/${name} in env/local.env"
      ;;
    *) die "usage: standalone.sh embedder pull <hf-repo> <dir-name>" ;;
  esac
}

# The cross-encoder for POST /rag/rerank (ADR-093 D-7): ~91 MB of MiniLM,
# English, CPU. Off until UMRS_RAG_RERANKER_MODEL names the staged directory;
# whether `rerank=auto` uses it is UMRS_RAG_RERANK_DEFAULT (the S4 gate).
RERANKER_REPO="cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_DIR="ms-marco-MiniLM-L6-v2"
cmd_reranker() {
  detect_engine
  case "${1:-}" in
    pull)
      local repo="${2:-$RERANKER_REPO}" name="${3:-$RERANKER_DIR}"
      _stage_hf_model "$repo" "$name" reranker
      model_dir_is_staged "$name" || die "reranker staged into /models/${name} without config.json + model.safetensors"
      ok "reranker staged: /models/${name}"
      info "Next: UMRS_RAG_RERANKER_MODEL=/models/${name} in env/local.env, then ./standalone.sh up (restart does not re-read env)"
      ;;
    *) die "usage: standalone.sh reranker pull [hf-repo] [dir-name]   (default ${RERANKER_REPO} -> /models/${RERANKER_DIR})" ;;
  esac
}

# Schema migrations against the RUNNING postgres, with the command bootstrap
# uses (python -m umrs.migrations --db-url). A one-off umrs container, so it
# works while umrs itself cannot start on an old schema. The URL is built
# inside the container from its own DB_* environment, so the password never
# reaches this host's process list. The dev profile mounts umrs/src, so a
# migration in the checkout applies without a rebuild; hardened applies the
# migrations its image carries.
cmd_migrate() {
  local args=()
  case "${1:-}" in
    "") ;;
    --status) args+=(--status) ;;
    *) die "usage: standalone.sh migrate [--status]" ;;
  esac
  detect_engine; load_env; profile_args
  # shellcheck disable=SC2016  # expanded by the containers' sh from their own env
  compose exec -T postgres sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    || die "postgres is not running. Start the stack first: ./standalone.sh up"
  run_migrations ${args[@]+"${args[@]}"}
}

# `python -m umrs.migrations` in a one-off umrs container (bootstrap and
# migrate); extra args pass through (--status). The URL is built by the
# container's sh from its own DB_* environment, so the password never
# reaches this host's process list.
run_migrations() {
  ensure_umrs_image
  # shellcheck disable=SC2016
  compose run --rm --no-deps -T umrs sh -c \
    'exec python -m umrs.migrations --db-url "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}" "$@"' \
    sh "$@" || die "migrations failed"
}

# Knowledge orphan repair (ADR-093 D-11 amendment, MD-17; decided 2026-10-11):
# nodes that hang on no artifact row, left by embed writes from before the
# writer became one transaction. Runs `python -m umrs.knowledge.orphan_repair`
# in the running umrs container, which resolves the knowledge database from
# its own DB_* environment (no credential on this host's command line). A dry
# run is the default and writes nothing; --apply deletes in ONE transaction
# per project and is re-runnable. Each project prints one JSON line; a
# non-zero exit stops the loop and fails the command. Projects are explicit
# (--project, repeatable); without one, the stack's own `default` project —
# the one `token` and `model register` use. Run migrate first: migration 0122
# indexes the column the repair reads.
cmd_orphan_repair() {
  local apply=() projects=() p
  while (( $# )); do
    case "$1" in
      --apply) apply=(--apply) ;;
      --project)
        [[ -n "${2:-}" && "${2:-}" != --* ]] || die "usage: standalone.sh orphan-repair [--apply] [--project P]..."
        projects+=("$2"); shift ;;
      --project=*) [[ -n "${1#--project=}" ]] || die "usage: standalone.sh orphan-repair [--apply] [--project P]..."
        projects+=("${1#--project=}") ;;
      *) die "usage: standalone.sh orphan-repair [--apply] [--project P]...   (unknown argument '$1')" ;;
    esac
    shift
  done
  (( ${#projects[@]} )) || projects=(default)
  detect_engine; load_env; profile_args
  compose exec -T umrs true >/dev/null 2>&1 \
    || die "umrs is not running. Start the stack first: ./standalone.sh up"
  if (( ${#apply[@]} )); then
    warn "orphan-repair --apply: deleting orphan knowledge nodes in ${projects[*]} (one transaction per project)"
  else
    info "orphan-repair: dry run (counts only, writes nothing); re-run with --apply to delete"
  fi
  for p in "${projects[@]}"; do
    compose exec -T umrs python -m umrs.knowledge.orphan_repair --project "$p" ${apply[@]+"${apply[@]}"} \
      || die "orphan repair failed for project '${p}'"
  done
}

# A JWT for the r4 client, minted inside umrs (stand-alone has no identity
# provider; this is the admin-HS256 lane, parity row A3). Prints the client
# commands rather than running them, so your ~/.config/r4 is never edited here.
cmd_token() {
  detect_engine; load_env; profile_args
  local user="${1:-scientist}" project="${2:-default}" tok
  tok="$(compose exec -T umrs umrs-server admin token generate-jwt --user "$user" \
    --project "$project" --days 30 --no-gateway-url --json \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')" || die "could not mint a token"
  cat <<EOF
# 30-day token for '${user}' on project '${project}'. Treat it like a password.
# One step instead, which leaves your active r4 source as it is and never
# shows the token: ./standalone.sh register   (./r4-local register)
# --model matters: r4's built-in default is a cluster model that does not exist
# here, so without it a plain \`r4 chat\` answers "No active endpoint".
r4 source add standalone --kind platform --url http://localhost:8080 --zmq-host 127.0.0.1 --zmq-port 5555
r4 source use standalone
r4 auth login --gateway-url http://localhost:8080 --model $(cfg_get STANDALONE_CHAT_MODEL) --api-key ${tok}
r4 chat "hello"
EOF
}

cmd_doctor() {
  detect_engine; load_env; profile_args
  local fail=0
  probe() {
    local name="$1" url="$2"
    if curl -sf -o /dev/null --max-time 5 "$url"; then ok "$name"; else printf '%s✗ %s (%s)%s\n' "$RED" "$name" "$url" "$OFF"; fail=1; fi
  }
  probe "gateway /health"  "http://127.0.0.1:8080/health"
  # The sidecar is not published in the hardened profile (see compose.yaml),
  # so it is probed from inside its own container — the host-access contract.
  if umrs_answers; then
    ok "umrs sidecar (in-container)"
  else
    printf '%s✗ umrs sidecar (compose exec umrs)%s\n' "$RED" "$OFF"; fail=1
  fi
  # A ZMQ TCP probe only: ZAP rejects anonymous handshakes, so a successful
  # CURVE connect is not available to an unauthenticated prober.
  if command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 5555 2>/dev/null; then ok "zmq control 5555 open"; else warn "zmq control 5555 not reachable"; fi
  doctor_reranker || fail=1
  doctor_ollama_parallel
  warn "plane conformance (knowledge graph, artifacts, teamup, research loop): R4_RUN_E2E=1 pytest tests/test_standalone_live_e2e.py (SL-CONF T2)"
  return $fail
}

# umrs answers its own /health, asked from inside its container (8082 is
# not published in the hardened profile). Needs load_env.
umrs_answers() {
  compose exec -T umrs python -c 'import urllib.request;urllib.request.urlopen("http://localhost:8082/health",timeout=5).close()' >/dev/null 2>&1
}

# The reranker as the rag container sees it: off, staged, or why not. The
# configured value is compared with the running container's, because an env
# change needs `up` and a forgotten one is otherwise invisible.
doctor_reranker() {
  local want have
  want="$(cfg_get UMRS_RAG_RERANKER_MODEL)"
  if ! compose exec -T rag true >/dev/null 2>&1; then
    printf '%s✗ reranker: the rag container is not running%s\n' "$RED" "$OFF"; return 1
  fi
  have="$(compose exec -T rag printenv UMRS_RAG_RERANKER_MODEL 2>/dev/null || true)"
  if [[ "$want" != "$have" ]]; then
    printf '%s✗ reranker: configured "%s" but rag runs "%s" — run ./standalone.sh up (restart does not re-read env)%s\n' "$RED" "$want" "$have" "$OFF"; return 1
  fi
  if [[ -z "$want" ]]; then
    info "reranker: off (not staged/configured; ./standalone.sh reranker pull, then UMRS_RAG_RERANKER_MODEL in env/local.env)"
    return 0
  fi
  if [[ "$want" != /* ]]; then
    printf '%s✗ reranker: "%s" is a hub id; nothing downloads at runtime — ./standalone.sh reranker pull %s%s\n' "$RED" "$want" "$want" "$OFF"; return 1
  fi
  # shellcheck disable=SC2016  # $1 expands in the container's sh, by design
  if compose exec -T rag sh -c 'test -f "$1/config.json" && test -f "$1/model.safetensors"' sh "$want" >/dev/null 2>&1; then
    ok "reranker staged: ${want}"
  else
    printf '%s✗ reranker %s is not staged: ./standalone.sh reranker pull <hf-repo> %s%s\n' "$RED" "$want" "$(basename "$want")" "$OFF"; return 1
  fi
}

# ADR-093 MD-11 (decided 2026-10-11): OLLAMA_NUM_PARALLEL=2 is the recommended
# setting on a capable host (>= 16 GB RAM, or a GPU), so a side call (router,
# extraction, sufficiency) gets its own slot instead of evicting the agent's.
# A hint only — it never fails doctor. It reads the setting where it is cheap
# to read: the models container's env, or for a native ollama the macOS
# launchd env / the systemd unit's env. An `ollama serve` started by hand
# shows as unknown. ollama itself does not report its slot count (`ollama ps`
# lists loaded models only).
doctor_ollama_parallel() {
  case ",$(active_profiles)," in *,models-local,*) ;; *) return 0 ;; esac
  local n="" where fix
  if [[ "$(model_host)" == "native" ]]; then
    where="the host's ollama"
    if [[ "$(uname -s)" == Darwin ]] && command -v launchctl >/dev/null 2>&1; then
      n="$(launchctl getenv OLLAMA_NUM_PARALLEL 2>/dev/null || true)"
      fix="launchctl setenv OLLAMA_NUM_PARALLEL 2, then quit and reopen Ollama"
    elif command -v systemctl >/dev/null 2>&1; then
      n="$(systemctl show ollama.service -p Environment --value 2>/dev/null \
        | tr ' ' '\n' | sed -n 's/^OLLAMA_NUM_PARALLEL=//p' | tail -1 || true)"
      fix="systemctl edit ollama (Environment=OLLAMA_NUM_PARALLEL=2), then systemctl restart ollama; or OLLAMA_NUM_PARALLEL=2 ollama serve"
    else
      fix="OLLAMA_NUM_PARALLEL=2 ollama serve"
    fi
  else
    where="the models container"
    n="$(compose exec -T models printenv OLLAMA_NUM_PARALLEL 2>/dev/null || true)"
    fix="OLLAMA_NUM_PARALLEL=2 with STANDALONE_CHAT_MEM above ~10 GiB (e.g. 12g) in env/local.env, then ./standalone.sh up"
  fi
  if [[ "$n" =~ ^[0-9]+$ ]] && (( n >= 2 )); then
    ok "ollama parallel slots: ${n} (${where})"
  else
    info "ollama parallel slots: ${n:-unknown} (${where}). On a host with >= 16 GB RAM or a GPU, 2 keeps side calls from evicting the agent's slot (ADR-093 MD-11):"
    info "  ${fix}"
  fi
}

# `up --dev` / `up --hardened` choose the profile; `up` records it (env/.state).
up_profile_flag() {
  case "${1:-}" in
    --dev) export STANDALONE_PROFILE=dev ;;
    --hardened) export STANDALONE_PROFILE=hardened ;;
    "") ;;
    *) die "usage: standalone.sh up [--dev|--hardened]   (unknown argument '$1')" ;;
  esac
}

main() {
  case "${1:-}" in
    quickstart) shift; cmd_quickstart "$@" ;;
    preflight) shift; cmd_preflight "$@" ;;
    bootstrap) shift; cmd_bootstrap "$@" ;;
    up)        shift; up_profile_flag "$@"; cmd_up ;;
    register)  shift; cmd_register "$@" ;;
    down)      shift; cmd_down "$@" ;;
    restart)   shift; cmd_restart "$@" ;;
    status)    shift; cmd_status "$@" ;;
    logs)      shift; cmd_logs "$@" ;;
    exec)      shift; cmd_exec "$@" ;;
    model)     shift; cmd_model "$@" ;;
    embedder)  shift; cmd_embedder "$@" ;;
    reranker)  shift; cmd_reranker "$@" ;;
    migrate)   shift; cmd_migrate "$@" ;;
    orphan-repair) shift; cmd_orphan_repair "$@" ;;
    doctor)    shift; cmd_doctor "$@" ;;
    token)     shift; cmd_token "$@" ;;
    reset)     shift; cmd_reset "$@" ;;
    ""|-h|--help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}" ;;
    *) die "unknown command '${1}'. Try: ./standalone.sh --help" ;;
  esac
}

# Run only when executed. Sourced (the T0 tests), it only defines functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
