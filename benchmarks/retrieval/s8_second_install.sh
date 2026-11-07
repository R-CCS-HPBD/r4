#!/usr/bin/env bash
# ADR-093 S8: a second stand-alone installation, for the portable-text import.
#
#   s8_second_install.sh env          the exports later steps need: eval "$(... env)"
#   s8_second_install.sh up           bootstrap (idempotent) and bring it up
#   s8_second_install.sh down         stop it, volumes kept
#   s8_second_install.sh wipe --yes   stop it and DESTROY its volumes (never the source's)
#   s8_second_install.sh steps        the whole S8 run in order; (M) = maintainer only
#
# Why a second installation: node ids are installation-global, so an import
# into the source installation skips every node as held by another project
# (ADR-093, waves B-D review, D-12). The v1.2 and v1.3 bundles hold the same
# nodes, so each goes into a FRESH second installation:
#   up -> import v1.3 -> retrieve -> wipe -> up -> import v1.2 -> retrieve.
#
# What makes it a second installation is COMPOSE_PROJECT_NAME: containers,
# networks and volumes are named by it. The overrides live in
# s8_second_install.env and are exported before standalone.sh runs; a set
# variable wins over deploy/standalone/env/*.env (ADR-008). Shared with the
# source installation: env/.env.secrets (standalone.sh reads it from a fixed
# path; `bootstrap` keeps it and re-renders seaweedfs/iam.json and s3cfg from
# it), the umrs image (UMRS_IMAGE), and the gateway images, which `up` tags
# under the new project name so compose does not rebuild them.
#
# Ports: deploy/standalone/compose.yaml and compose.dev.yaml publish FIXED host
# ports (8080, 5555, 5556-5563; dev adds 8082, 5432, 9000, 8111, 8081) and
# read no port variable. Until they read the STANDALONE_*_PORT names in
# s8_second_install.env, the two installations cannot run together: `up` runs
# in SEQUENTIAL mode and refuses while anything answers on 127.0.0.1:8080.
# Once both compose files read them, `up` runs in PARALLEL mode.
#
# This script never mints a token and never reads a credential: it names the
# commands only the maintainer runs. Run it against the checkout the source
# stack is deployed from (ADR093_REPO_ROOT; default: this script's checkout).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${ADR093_REPO_ROOT:-$(cd "${HERE}/../.." && pwd)}"
STANDALONE="${REPO}/deploy/standalone/standalone.sh"
COMPOSE_DIR="${REPO}/deploy/standalone"
ENV_FILE="${S8_ENV_FILE:-${HERE}/s8_second_install.env}"
SOURCE_PROJECT="ai4s-standalone"
SOURCE_GATEWAY_PORT="${S8_SOURCE_GATEWAY_PORT:-8080}"
SOURCE_ZMQ_PORT=5555
PY="${ADR093_PYTHON:-${REPO}/.venv/bin/python}"
BUNDLES="${S8_BUNDLES:-${HOME}/s8-bundles}"
S8_XDG="${S8_XDG:-${HOME}/.config-r4-s8}"

die()  { printf 'x %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*" >&2; }

load_overrides() {
  [[ -f "$ENV_FILE" ]] || die "no override file ${ENV_FILE}"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  [[ -n "${COMPOSE_PROJECT_NAME:-}" ]] || die "${ENV_FILE} sets no COMPOSE_PROJECT_NAME"
  [[ "$COMPOSE_PROJECT_NAME" != "$SOURCE_PROJECT" ]] \
    || die "refusing: COMPOSE_PROJECT_NAME is the source stack's (${SOURCE_PROJECT})"
}

check_repo() {
  [[ -x "$STANDALONE" ]] || die "no ${STANDALONE}: set ADR093_REPO_ROOT to the deployed checkout"
  [[ -d "${REPO}/umrs/src/umrs" ]] \
    || die "the dev profile mounts ${REPO}/umrs/src, which is empty: set ADR093_REPO_ROOT"
}

# parallel when both compose files publish the STANDALONE_*_PORT variables.
ports_mode() {
  if grep -q 'STANDALONE_GATEWAY_PORT' "${COMPOSE_DIR}/compose.yaml" \
    && grep -q 'STANDALONE_POSTGRES_PORT' "${COMPOSE_DIR}/compose.dev.yaml"; then
    echo parallel
  else
    echo sequential
  fi
}

gateway_port() {
  if [[ "$(ports_mode)" == parallel ]]; then echo "$STANDALONE_GATEWAY_PORT"; else echo "$SOURCE_GATEWAY_PORT"; fi
}

zmq_port() {
  if [[ "$(ports_mode)" == parallel ]]; then echo "$STANDALONE_ZMQ_PORT"; else echo "$SOURCE_ZMQ_PORT"; fi
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

engine_bin() {
  if [[ -n "${STANDALONE_ENGINE:-}" ]]; then
    echo "$STANDALONE_ENGINE"
  elif docker info >/dev/null 2>&1; then
    echo docker
  else
    echo podman
  fi
}

# The gateway images carry no `image:` key, so compose names them after the
# project and would rebuild them for a new one; tag the source's instead.
reuse_images() {
  local bin svc src dst
  bin="$(engine_bin)"
  for svc in gateway-sidecar gateway-nginx; do
    src="${SOURCE_PROJECT}-${svc}"
    dst="${COMPOSE_PROJECT_NAME}-${svc}"
    if "$bin" image inspect "$dst" >/dev/null 2>&1; then
      continue
    fi
    if "$bin" image inspect "$src" >/dev/null 2>&1; then
      "$bin" tag "$src" "$dst"
      info "reusing image ${src} as ${dst}"
    else
      info "no ${src} image: compose builds ${dst} (the build may need network)"
    fi
  done
}

login_steps() {
  local port zmq
  port="$(gateway_port)"
  zmq="$(zmq_port)"
  cat <<EOF
(M) Log an ISOLATED r4 config into the second installation (never the source's):
    COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME} STANDALONE_PROFILE=${STANDALONE_PROFILE} ${STANDALONE} token scientist default
    Run the r4 lines it prints with XDG_CONFIG_HOME=${S8_XDG}, gateway http://localhost:${port}, --zmq-port ${zmq}.
(M) The import lands in the token's project (default). If the second installation refuses it as
    unregistered, register project default there with the same owner and data class as the source's.
EOF
}

cmd_env() {
  load_overrides
  printf 'export COMPOSE_PROJECT_NAME=%q\n' "$COMPOSE_PROJECT_NAME"
  printf 'export STANDALONE_PROFILE=%q\n' "$STANDALONE_PROFILE"
  printf 'export STANDALONE_PROFILES=%q\n' "$STANDALONE_PROFILES"
  printf 'export ADR093_REPO_ROOT=%q\n' "$REPO"
  printf '# then: export XDG_CONFIG_HOME=%q  (the config logged into the second installation)\n' "$S8_XDG"
}

cmd_up() {
  load_overrides
  check_repo
  local mode
  mode="$(ports_mode)"
  if [[ "$mode" == sequential ]] && port_busy "$SOURCE_GATEWAY_PORT"; then
    die "sequential mode: something answers on 127.0.0.1:${SOURCE_GATEWAY_PORT} (the source stack?).
   compose.yaml publishes fixed ports, so the two installations cannot run together.
   (M) Stop the source stack first: ${STANDALONE} down   (its volumes are kept)"
  fi
  info "second installation ${COMPOSE_PROJECT_NAME}: ${mode} ports, profile ${STANDALONE_PROFILE}, compose profiles ${STANDALONE_PROFILES}"
  reuse_images
  "$STANDALONE" bootstrap
  "$STANDALONE" up
  login_steps
}

cmd_down() {
  load_overrides
  check_repo
  "$STANDALONE" down
}

cmd_wipe() {
  [[ "${1:-}" == "--yes" ]] || die "wipe DESTROYS the second installation's volumes. Re-run with --yes."
  load_overrides
  check_repo
  "$STANDALONE" reset --yes
}

cmd_steps() {
  load_overrides
  local me="${HERE}/s8_second_install.sh" p="${PY} ${HERE}/portable_probe.py" r="${PY} ${HERE}/run.py"
  cat <<EOF
ADR-093 S8, in order. (M) = maintainer only. D=<scifact data/>, bundles in ${BUNDLES}.
Ports: $(ports_mode) mode.

On the SOURCE installation (XDG_CONFIG_HOME = the eval config logged into it; COMPOSE_PROJECT_NAME unset):
 1 (M) Project default must let its chunk text leave (D-12 fails closed otherwise): registered with an
       owner and a data class, and either owned by the export's submitter or non_sensitive
       (SciFact is public, CC BY-NC 2.0): PATCH /gw/projects/default
       {"action": "set_data_class", "data_class": "non_sensitive"}, as its owner or an admin.
 2 ${p} export --project default --chunk-text --out ${BUNDLES}
 3 ${p} export --project default --out ${BUNDLES}
 4 ${p} verify --v13 ${BUNDLES}/default-v13.kb.tar.gz --v12 ${BUNDLES}/default-v12.kb.tar.gz
       Stop here if a gate fails or it warns that artifacts.jsonl is short: the import gate cannot pass.
 5 ${r} retrieve --data \$D --arms retrieve:dense --graph "" --label s8-source

On the SECOND installation:
 6 (M) Sequential mode only: stop the source stack: ${STANDALONE} down   (volumes kept)
 7 ${me} up
 8 (M) The login printed by step 7.
 9 eval "\$(${me} env)"; export XDG_CONFIG_HOME=${S8_XDG}
10 ${p} import ${BUNDLES}/default-v13.kb.tar.gz --target-compose-project ${COMPOSE_PROJECT_NAME}
       (M) add --allow-unsigned only if the import refuses the bundle's signature.
11 ${r} retrieve --data \$D --arms retrieve:dense --graph "" --label s8-v13
12 ${me} wipe --yes && ${me} up      (fresh: node ids are installation-global; (M) log in again if refused)
13 ${p} import ${BUNDLES}/default-v12.kb.tar.gz --target-compose-project ${COMPOSE_PROJECT_NAME}
14 ${r} retrieve --data \$D --arms retrieve:dense --graph "" --label s8-v12
15 ${me} wipe --yes      (or: ${me} down, to keep it)
16 (M) Sequential mode only: bring the source stack back: STANDALONE_PROFILE=dev ${STANDALONE} up

Anywhere (no stack):
17 ${p} gate --verify <the step-4 file> --source <*-retrieve-dev-retrieve-dense-s8-source.json> \\
       --v13 <*-retrieve-dev-retrieve-dense-s8-v13.json> --v12 <*-retrieve-dev-retrieve-dense-s8-v12.json>
EOF
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    env) cmd_env ;;
    up) cmd_up ;;
    down) cmd_down ;;
    wipe) cmd_wipe "$@" ;;
    steps) cmd_steps ;;
    *) die "usage: s8_second_install.sh {env|up|down|wipe --yes|steps}" ;;
  esac
}

main "$@"
