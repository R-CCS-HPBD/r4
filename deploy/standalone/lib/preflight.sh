# shellcheck shell=bash
# Preflight: check the host, refuse early and by name, and fix what can be
# fixed (onboarding plan §2 Phase 1, 2026-10-15).
#
#   - host tools (python3, curl, nc, openssl), with the exact install line;
#   - memory in MiB with a tolerance (lib/host.sh), the WSL2 VM's limit with
#     the exact .wslconfig fix, Docker Desktop's VM (macOS, Linux, WSL2);
#   - WSL2 systemd: required for rootless podman, a warning under Docker
#     Desktop;
#   - the macOS podman machine: present, RUNNING (podman info answers) and
#     big enough; quickstart offers to create, start or resize it, which runs
#     only with --yes or an interactive yes; then `podman compose` must run;
#   - disk;
#   - ports 8080 and 5555-5563 free or held by this stack; dev ports held by
#     another program are moved in env/local.env by quickstart, refused with
#     the fix line otherwise. A moved port never lands on a port the stack
#     itself publishes.
#
# One pass per process: quickstart, bootstrap and up all call it.
# Needs: lib/common.sh, state.sh, host.sh, and from standalone.sh: HERE,
# MIN_RAM_GB, MIN_DISK_GB, MIN_BUILD_DISK_GB, detect_engine, cfg_get,
# model_host.

cmd_preflight() {
  [[ "${PREFLIGHT_PASSED:-0}" == 1 ]] && return 0
  detect_engine
  ok "engine: ${COMPOSE[*]}"
  preflight_tools
  case "$(uname -s)" in
    Linux) preflight_linux ;;
    Darwin) preflight_macos ;;
    *) die "unsupported host OS '$(uname -s)'. Linux, macOS and WSL2 are supported; native Windows is not (no win32 console binary)." ;;
  esac
  preflight_disk
  preflight_ports
  PREFLIGHT_PASSED=1
}

preflight_tools() {
  local missing; missing="$(missing_tools)"
  if [[ -z "$missing" ]]; then ok "host tools: ${HOST_TOOLS}"; return 0; fi
  # shellcheck disable=SC2086  # one word per tool
  die "missing host tools: ${missing}. Install them:
   $(tools_fix_line "$(os_id)" $missing)"
}

# ── memory ────────────────────────────────────────────────────────────────
# Args: mem_mib what need_gb.
preflight_ram() {
  local mem="$1" what="$2" need="$3"
  if mem_at_least "$mem" "$need"; then
    ok "${what} $(mib_as_gib "$mem") GiB (floor ${need} GB, ${RAM_TOLERANCE_PCT}% tolerance)"
    return 0
  fi
  if is_wsl; then
    die "the WSL2 VM has $(mib_as_gib "$mem") GiB; the stack needs ${need} GB.
   $(wslconfig_fix "$need" "$(wslconfig_path)")"
  fi
  die "${what} $(mib_as_gib "$mem") GiB < required ${need} GB (STANDALONE_MIN_RAM_GB)"
}

# ── Linux and WSL2 ────────────────────────────────────────────────────────
# Under Docker Desktop (Linux or WSL2) the containers get Desktop's VM, not
# the host's memory.
preflight_linux() {
  if [[ "$ENGINE" == docker ]] && docker_is_desktop; then
    preflight_ram "$(docker_mem_mib)" "Docker Desktop VM" "$MIN_RAM_GB"
  else
    preflight_ram "$(host_mem_mib)" "RAM" "$MIN_RAM_GB"
  fi
  if [[ "$ENGINE" == podman ]]; then preflight_cgroup; fi
  if is_wsl; then preflight_wsl; fi
  return 0
}

# cgroup v2 delegation is what makes rootless podman's resource limits real
# (a rootful Docker daemon enforces them itself).
preflight_cgroup() {
  local ctl
  if [[ ! -r /sys/fs/cgroup/cgroup.controllers ]]; then
    warn "cgroup v2 not detected — resource limits may be ignored"
    return 0
  fi
  ctl="$(cat "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers" 2>/dev/null || true)"
  if [[ "$ctl" != *cpu* || "$ctl" != *memory* ]]; then
    warn "cgroup v2 cpu+memory are not delegated to your user; mem_limit/cpus will be ignored."
    info "fix: sudo mkdir -p /etc/systemd/system/user@.service.d && add Delegate=cpu cpuset io memory pids"
  else
    ok "cgroup v2 delegation: ${ctl}"
  fi
}

# WSL2 is the supported Windows path (ADR-090 D-7); native Windows is refused.
preflight_wsl() {
  local sd=0 dd=0
  if wsl_has_systemd; then sd=1; fi
  if [[ "$ENGINE" == docker ]] && docker_is_desktop; then dd=1; fi
  case "$(wsl_systemd_verdict "$sd" "$dd")" in
    ok) ok "WSL2 with systemd" ;;
    warn) warn "WSL2 without systemd: fine for Docker Desktop's engine. Rootless podman needs it: [boot] systemd=true in /etc/wsl.conf, then wsl --shutdown." ;;
    *) die "WSL2 without systemd. Add to /etc/wsl.conf:
     [boot]
     systemd=true
   then in PowerShell: wsl --shutdown. Rootless podman needs it (loginctl enable-linger, user services); Docker Desktop with WSL integration does not." ;;
  esac
  case "$HERE" in /mnt/*) warn "running from /mnt/* — put the checkout and volumes on native ext4, not DrvFs" ;; esac
  return 0
}

# ── macOS ─────────────────────────────────────────────────────────────────
# The VM carries the platform services; with the model on the host's own
# ollama it needs ~8 GB, with the model inside it MIN_RAM_GB.
vm_need_gb() {
  if [[ "$(model_host)" == native ]]; then printf '%s' "${STANDALONE_MIN_VM_GB_NATIVE:-8}"
  else printf '%s' "$MIN_RAM_GB"
  fi
}

preflight_macos() {
  local need; need="$(vm_need_gb)"
  if [[ "$ENGINE" == docker ]]; then preflight_docker_desktop "$need"; return 0; fi
  if ! podman machine inspect >/dev/null 2>&1; then podman_machine_create "$need"; fi
  if ! podman info >/dev/null 2>&1; then podman_machine_start; fi
  podman_machine_fit "$need"
  podman_machine_report
  podman_compose_ready
}

# A VM of 0 MiB is a daemon that does not answer: Docker Desktop not started.
preflight_docker_desktop() {
  local need="$1" mem; mem="$(docker_mem_mib)"
  (( mem > 0 )) || die "Docker Desktop does not answer (docker info fails). Start Docker Desktop, then run this again."
  if ! mem_at_least "$mem" "$need"; then
    die "Docker Desktop gives its VM $(mib_as_gib "$mem") GiB; the stack needs ${need} GB.
   Fix: Docker Desktop > Settings > Resources > Memory: ${need} GB, then Apply & restart."
  fi
  ok "macOS + Docker Desktop (VM $(mib_as_gib "$mem") GiB)"
}

podman_machine_create() {
  local need="$1" size m c d line host
  host="$(host_mem_mib)"
  size="$(podman_vm_size "$host" "$need" "$(cpu_count)" "$(free_disk_gb "$HERE")")" \
    || die "this Mac has $(mib_as_gib "$host") GiB: a ${need} GB podman machine would leave macOS under 4 GiB.
   Install Ollama (https://ollama.com/download) so the model runs on the host; the machine then needs ${STANDALONE_MIN_VM_GB_NATIVE:-8} GB."
  read -r m c d <<<"$size"
  line="podman machine init --memory ${m} --cpus ${c} --disk-size ${d} && podman machine start"
  confirm "No podman machine yet. Create one with ${m} MiB, ${c} CPUs and a ${d} GB disk, and start it?" \
    || die "no podman machine. Create it:  ${line}"
  if ! { podman machine init --memory "$m" --cpus "$c" --disk-size "$d" && podman machine start; }; then
    die "creating the podman machine failed. By hand:  ${line}"
  fi
}

podman_machine_start() {
  confirm "The podman machine is not running. Start it?" \
    || die "the podman machine is not running (podman info fails). Start it:  podman machine start"
  podman machine start || die "podman machine start failed"
  podman info >/dev/null 2>&1 || die "the podman machine started but podman info still fails"
}

podman_machine_value() {
  local v; v="$(podman machine inspect --format "{{.Resources.$1}}" 2>/dev/null | head -1 || true)"
  [[ "$v" =~ ^[0-9]+$ ]] || v=0
  printf '%s' "$v"
}

# Memory: the fix line names the size actually needed (need_gb), and runs
# with consent. podman reports the machine's memory in MiB.
podman_machine_fit() {
  local need="$1" mem cpus want line
  mem="$(podman_machine_value Memory)"
  mem_at_least "$mem" "$need" && return 0
  cpus="$(podman_machine_value CPUs)"; (( cpus >= 2 )) || cpus=2
  want=$((need * 1024))
  line="$(podman_vm_fix_line "$want" "$cpus")"
  if (( $(host_mem_mib) - want < 4096 )); then
    die "the podman machine has $(mib_as_gib "$mem") GiB (< ${need} GB), and this Mac cannot spare ${need} GB.
   Install Ollama (https://ollama.com/download) so the model runs on the host; the machine then needs ${STANDALONE_MIN_VM_GB_NATIVE:-8} GB."
  fi
  confirm "The podman machine has $(mib_as_gib "$mem") GiB; the stack needs ${need} GB. Resize it to ${want} MiB (stop, set, start)?" \
    || die "the podman machine has $(mib_as_gib "$mem") GiB (< ${need} GB). Fix:  ${line}"
  if ! { podman machine stop && podman machine set --memory "$want" --cpus "$cpus" && podman machine start; }; then
    die "resizing the podman machine failed. By hand:  ${line}"
  fi
}

# The VM's virtual disk is a SPARSE FILE on the host: a build inside the VM
# can grow it until macOS itself runs out. Measured 2026-10-08 — one image
# build took the host from 35 GB free to 0.12 GB and killed the VM; pruning
# inside does not return space. A warning: the hard floor (host free vs
# MIN_DISK_GB) is what caught that incident.
podman_machine_report() {
  local mem cpus vm_disk host_free
  mem="$(podman_machine_value Memory)"; cpus="$(podman_machine_value CPUs)"
  vm_disk="$(podman_machine_value DiskSize)"; host_free="$(free_disk_gb "$HERE")"
  if (( vm_disk > host_free )); then
    warn "podman machine disk is ${vm_disk} GB but macOS has only ${host_free} GB free."
    info "The VM image is sparse and a build can grow it until the HOST fills, which kills the VM mid-build."
    info "If a build dies with 'ssh: handshake failed', this is why: podman machine rm -f && podman machine init --disk-size ${host_free} --memory ${mem} --cpus ${cpus}"
  fi
  ok "podman machine: running, $(mib_as_gib "$mem") GiB RAM, ${cpus} CPUs, ${vm_disk} GB disk (host has ${host_free} GB free)"
}

# ── disk ──────────────────────────────────────────────────────────────────
preflight_disk() {
  local free_gb; free_gb="$(free_disk_gb "$HERE")"
  (( free_gb >= MIN_DISK_GB )) || die "free disk ${free_gb} GB < ${MIN_DISK_GB} GB needed to RUN the stack"
  # The build floor applies only to STANDALONE_BUILD=1: the default pulls the
  # published image (repo-rename plan P4).
  if build_wanted && (( free_gb < MIN_BUILD_DISK_GB )); then
    warn "disk ${free_gb} GB is under the ${MIN_BUILD_DISK_GB} GB a from-scratch image BUILD needs (~16 GB transient)."
    info "Running pre-built images is fine. If a build is required, free space first."
  else
    ok "disk ${free_gb} GB free"
  fi
}

# ── ports ─────────────────────────────────────────────────────────────────
stack_project() { local p; p="$(cfg_get COMPOSE_PROJECT_NAME)"; printf '%s' "${p:-ai4s-standalone}"; }

# Why PORT cannot be used; nothing when it is this stack's own.
port_conflict() {
  local holder
  holder="$(port_holder "$1")"
  if [[ -z "$holder" ]]; then printf 'another program (not a container)'; return 0; fi
  case "$(holder_kind "$holder" "$ENGINE" "$(stack_project)")" in
    ours) return 0 ;;
    other-engine) printf 'this stack under %s (%s): stop it there, or run with STANDALONE_ENGINE=%s' \
      "${holder%% *}" "${holder#* }" "${holder%% *}" ;;
    *) printf '%s container %s' "${holder%% *}" "${holder#* }" ;;
  esac
}

preflight_ports() {
  local p why
  for p in $REQUIRED_PORTS; do
    port_busy "$p" || continue
    why="$(port_conflict "$p")"
    [[ -z "$why" ]] || die "port 127.0.0.1:${p} is in use by ${why}. The stack needs 8080 and 5555-5563: the r4 client and the ZMQ data range name them."
  done
  if [[ "$(resolve_profile hardened)" == dev ]]; then preflight_dev_ports; fi
  ok "ports 8080 and 5555-5563: free or held by this stack"
}

# The host port a dev variable publishes: its value, else its default.
dev_port_value() {
  local pair="$1" port; port="$(cfg_get "${pair%%:*}")"
  printf '%s' "${port:-${pair#*:}}"
}

# Every host port the stack will publish (the fixed ones and each dev
# variable's current value), so a moved port never lands on one of them.
stack_ports() {
  local pair out="$REQUIRED_PORTS"
  for pair in $DEV_PORT_VARS; do out="${out} $(dev_port_value "$pair")"; done
  printf '%s' "$out"
}

preflight_dev_ports() {
  local pair var port why
  for pair in $DEV_PORT_VARS; do
    var="${pair%%:*}"
    port="$(dev_port_value "$pair")"
    port_busy "$port" || continue
    why="$(port_conflict "$port")"
    [[ -n "$why" ]] && dev_port_move "$var" "$port" "$why"
  done
  return 0
}

# A dev debugging port held by another program: quickstart moves it in
# env/local.env (unless you set it); every other verb stops with the line.
# The new port is free now and is none the stack publishes (stack_ports is
# read again after each move, so two moves never pick the same port).
dev_port_move() {
  local var="$1" port="$2" why="$3" new
  # shellcheck disable=SC2046  # one word per port
  new="$(free_port_from $((port + 1)) $(stack_ports))" || die "no free port near ${port} for ${var}"
  if [[ "${QS_MOVE_DEV_PORTS:-0}" == 1 ]] && ! user_set "$var"; then
    auto_set "$var" "$new"
    info "dev port ${port} is held by ${why}: ${var}=${new} in env/local.env"
    return 0
  fi
  die "dev port 127.0.0.1:${port} (${var}) is held by ${why}.
   Put ${var}=${new} in env/local.env (the dev profile publishes it on loopback for debugging only)."
}
