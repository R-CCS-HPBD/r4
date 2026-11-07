# shellcheck shell=bash
# Host facts for preflight and the model choice (2026-10-15).
#
# Pure functions take their facts as arguments, so the tests can feed them a
# 16 GB host, a WSL2 VM or a broken NVML; the thin probes beside them read
# the real host. Needs: lib/common.sh.

# ── memory ────────────────────────────────────────────────────────────────
# Memory is compared in MiB with a tolerance. A "16 GB" machine reports less
# than 16 GiB of MemTotal (firmware and the kernel keep some), and the old
# whole-GiB division refused every 16 GB Linux and WSL2 host as "15 GB".
# The floor is generous (10 %): below it the stack is OOM-killed, above it
# only slow. The model table steps (lib/model.sh) use less: a "48 GB" Linux
# host reads ~47 GiB (98 %), so 5 % keeps it while a 44 GiB VM stays below.
# GPU memory is reported at its nominal size (nvidia-smi: 12288 MiB on most
# 12 GB cards, 12282 on some), so VRAM gets 2 %: an 11 GB card (11264 MiB)
# is not a 12 GB one.
RAM_TOLERANCE_PCT="${STANDALONE_RAM_TOLERANCE_PCT:-10}"
# shellcheck disable=SC2034  # read by lib/model.sh
TABLE_TOLERANCE_PCT=5
# shellcheck disable=SC2034  # read by lib/model.sh
VRAM_TOLERANCE_PCT=2

# MemTotal in MiB from a meminfo file (default /proc/meminfo).
meminfo_mib() {
  awk '/^MemTotal:/{printf "%d", $2/1024}' "${1:-/proc/meminfo}"
}

# Total memory this host gives the stack, in MiB. On WSL2 /proc/meminfo is
# the WSL VM's limit (.wslconfig), which is what the containers get.
host_mem_mib() {
  local bytes
  case "$(uname -s)" in
    Darwin) bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"; printf '%d' "$((bytes / 1048576))" ;;
    *) if [[ -r /proc/meminfo ]]; then meminfo_mib; else printf '0'; fi ;;
  esac
}

# The memory Docker's engine sees, in MiB (Docker Desktop: its VM's), or 0
# when the daemon does not answer.
docker_mem_mib() {
  local bytes
  bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
  [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
  printf '%d' "$((bytes / 1048576))"
}

# Apple silicon, whatever the shell runs as: under Rosetta (an Intel
# Homebrew bash, a terminal opened with Rosetta) `uname -m` says x86_64 while
# the Mac and its Ollama app are arm64 with unified memory.
is_apple_silicon() { [[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" == 1 ]]; }

# 0 when MEM_MIB counts as NOMINAL_GB of memory, within the tolerance.
# Args: mem_mib nominal_gb [tolerance_pct]
mem_at_least() {
  local mem="$1" gb="$2" tol="${3:-$RAM_TOLERANCE_PCT}"
  (( mem * 100 >= gb * 1024 * (100 - tol) ))
}

# GiB with one decimal, for messages.
mib_as_gib() { awk -v m="$1" 'BEGIN{printf "%.1f", m/1024}'; }

cpu_count() {
  if [[ "$(uname -s)" == Darwin ]]; then sysctl -n hw.ncpu 2>/dev/null || echo 2
  else getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2
  fi
}

# Free disk in whole GB where DIR lives.
free_disk_gb() { df -Pk "$1" | awk 'NR==2{print int($4/1024/1024)}'; }

# ── GPU ───────────────────────────────────────────────────────────────────
# The largest GPU's memory in MiB from nvidia-smi, or 0. A broken NVML
# ("Driver/library version mismatch" after a driver update without a
# reboot) makes nvidia-smi fail: that is a CPU host until it is fixed.
vram_mib() {
  local out
  command -v nvidia-smi >/dev/null 2>&1 || { printf '0'; return 0; }
  out="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null)" \
    || { printf '0'; return 0; }
  printf '%s\n' "$out" | awk 'BEGIN{m=0} $1+0>m{m=$1+0} END{printf "%d", m}'
}

# Why there is no usable GPU, in one line; empty when nvidia-smi works.
gpu_problem() {
  local out
  command -v nvidia-smi >/dev/null 2>&1 || { printf 'no nvidia-smi'; return 0; }
  if out="$(nvidia-smi -L 2>&1)"; then return 0; fi
  printf 'nvidia-smi failed: %s' "$(printf '%s\n' "$out" | head -1)"
}

nvsmi_works() { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; }

# Docker can hand an NVIDIA GPU to a container through the `driver: nvidia`
# device request (compose.gpu-nvidia.yaml): the toolkit's runtime is
# registered with the daemon, or its hook is on PATH (moby registers the
# nvidia device driver whenever nvidia-container-runtime-hook exists).
docker_has_nvidia_runtime() {
  docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null | grep -w nvidia >/dev/null
}

nvidia_hook_present() { command -v nvidia-container-runtime-hook >/dev/null 2>&1; }

# The Docker daemon resolves CDI devices (compose.gpu-cdi.yaml). Off by
# default before Engine 28.2 (daemon.json "features": {"cdi": true}): then
# CDISpecDirs is [] and a CDI device fails `compose up`. Older daemons do not
# know the field at all.
docker_cdi_enabled() {
  local dirs
  dirs="$(docker info --format '{{json .CDISpecDirs}}' 2>/dev/null || true)"
  [[ "$dirs" == '['*']' && "$dirs" != '[]' ]]
}

# A CDI spec for NVIDIA GPUs (nvidia-ctk cdi generate) is on this host.
cdi_spec_present() {
  local f
  for f in /etc/cdi/*nvidia* /var/run/cdi/*nvidia*; do
    [[ -f "$f" ]] && return 0
  done
  return 1
}

# ── WSL2 ──────────────────────────────────────────────────────────────────
is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# systemd is PID 1 here (or wsl.conf asks for it at the next boot).
wsl_has_systemd() {
  [[ -d /run/systemd/system ]] || grep -q 'systemd *= *true' /etc/wsl.conf 2>/dev/null
}

# `grep >/dev/null`, not `grep -q`, after a pipe: -q exits at the first
# match, the writer then dies of SIGPIPE, and under `set -o pipefail` the
# pipeline fails although it matched.
docker_is_desktop() { docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep 'Docker Desktop' >/dev/null; }

# `docker` is podman underneath: the podman-docker package's shim
# (`exec podman "$@"`), or a docker CLI pointed at a podman socket. Its
# version output names Podman; Docker's never does.
docker_is_podman() { docker version 2>&1 | grep -i podman >/dev/null; }

# systemd under WSL2 is needed for rootless podman (linger, user services).
# Docker Desktop runs its engine in its own VM, so there it is a warning.
# Args: has_systemd(0|1) docker_desktop(0|1). Prints ok|warn|die.
wsl_systemd_verdict() {
  if [[ "$1" == 1 ]]; then printf 'ok'
  elif [[ "$2" == 1 ]]; then printf 'warn'
  else printf 'die'
  fi
}

# The .wslconfig fix for a WSL2 VM below the floor. Args: need_gb [path].
wslconfig_fix() {
  local need="$1" path="${2:-%UserProfile%\\.wslconfig}"
  printf 'Give WSL2 more memory. In Windows, edit %s:\n' "$path"
  printf '     [wsl2]\n     memory=%sGB\n' "$need"
  printf '   then in PowerShell: wsl --shutdown   and open this terminal again.'
}

# %UserProfile%\.wslconfig as a path this shell can read; empty if unknown.
wslconfig_path() {
  local win
  command -v cmd.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1 || return 0
  win="$(cmd.exe /c 'echo %UserProfile%' 2>/dev/null | tr -d '\r')"
  [[ -n "$win" ]] && printf '%s/.wslconfig' "$(wslpath "$win")"
  return 0
}

# ── host tools ────────────────────────────────────────────────────────────
HOST_TOOLS="python3 curl nc openssl"

missing_tools() {
  local t out=""
  for t in $HOST_TOOLS; do
    command -v "$t" >/dev/null 2>&1 || out="${out}${out:+ }${t}"
  done
  printf '%s' "$out"
}

# The package that provides TOOL on OS.
tool_package() {
  local os="$1" tool="$2"
  [[ "$tool" == nc ]] || { printf '%s' "$tool"; return 0; }
  case "$os" in
    fedora|rhel|centos|rocky|almalinux) printf 'nmap-ncat' ;;
    arch|manjaro) printf 'openbsd-netcat' ;;
    darwin) printf 'netcat' ;;
    *) printf 'netcat-openbsd' ;;
  esac
}

# The install line for the missing tools. Args: os_id (debian, ubuntu,
# fedora, rhel, arch, opensuse, darwin, ...) tool...
tools_fix_line() {
  local os="$1" pkgs="" t; shift
  for t in "$@"; do pkgs="${pkgs}${pkgs:+ }$(tool_package "$os" "$t")"; done
  case "$os" in
    debian|ubuntu|linuxmint|pop) printf 'sudo apt-get install -y %s' "$pkgs" ;;
    fedora|rhel|centos|rocky|almalinux) printf 'sudo dnf install -y %s' "$pkgs" ;;
    arch|manjaro) printf 'sudo pacman -S --needed %s' "$pkgs" ;;
    opensuse*|sles) printf 'sudo zypper install -y %s' "$pkgs" ;;
    darwin) printf 'xcode-select --install   (or: brew install %s)' "$pkgs" ;;
    *) printf 'install with your package manager: %s' "$pkgs" ;;
  esac
}

# The distribution id: /etc/os-release ID, else ID_LIKE's first word;
# darwin on macOS.
os_id() {
  local id="" like=""
  if [[ "$(uname -s)" == Darwin ]]; then printf 'darwin'; return 0; fi
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    # shellcheck source=/dev/null
    like="$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")"
  fi
  case "$id" in
    debian|ubuntu|linuxmint|pop|fedora|rhel|centos|rocky|almalinux|arch|manjaro|opensuse*|sles) printf '%s' "$id" ;;
    *) printf '%s' "${like%% *}" ;;
  esac
}

# ── the macOS podman machine ──────────────────────────────────────────────
# Size a podman machine from the host. Args: host_mib need_gb ncpu free_gb.
# Prints "MEM_MIB CPUS DISK_GB", or fails when the Mac cannot spare need_gb
# and still keep 4 GiB for macOS itself.
podman_vm_size() {
  local host="$1" need="$2" ncpu="$3" free="$4" mem cpus disk
  mem=$((need * 1024))
  (( host - mem >= 4096 )) || return 1
  cpus=$((ncpu - 2)); (( cpus > 6 )) && cpus=6; (( cpus < 2 )) && cpus=2
  disk=$((free - 10)); (( disk > 120 )) && disk=120; (( disk < 30 )) && disk=30
  printf '%s %s %s' "$mem" "$cpus" "$disk"
}

# The resize line for a machine that exists: memory and CPUs only (a
# machine's disk can only grow). Args: mem_mib cpus.
podman_vm_fix_line() {
  printf 'podman machine stop && podman machine set --memory %s --cpus %s && podman machine start' "$1" "$2"
}

# ── ports ─────────────────────────────────────────────────────────────────
# Fixed: the client and the ZMQ data range the server advertises name them.
# shellcheck disable=SC2034  # read by lib/preflight.sh
REQUIRED_PORTS="8080 5555 5556 5557 5558 5559 5560 5561 5562 5563"
# Dev only, debugging ports on loopback (compose.dev.yaml): each one is moved
# by its variable, so a port another program holds is a fix, not a wall.
# shellcheck disable=SC2034  # read by lib/preflight.sh
DEV_PORT_VARS="STANDALONE_UMRS_HTTP_PORT:8082 STANDALONE_POSTGRES_PORT:5432 STANDALONE_S3_PORT:9000 STANDALONE_S3_IAM_PORT:8111 STANDALONE_SIDECAR_PORT:8081"

# 0 when something accepts connections on 127.0.0.1:PORT. Bash's /dev/tcp,
# so no nc dialect matters (BSD and GNU nc disagree on -z/-w).
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# The container holding PORT, from `<engine> ps --format '{{.Names}}\t{{.Ports}}'`
# lines on stdin. Ranges ("127.0.0.1:5556-5563->5556-5563/tcp") count.
port_owner() {
  awk -F'\t' -v p="$1" '
    { n = split($2, maps, ",")
      for (i = 1; i <= n; i++) {
        m = maps[i]; sub(/^ +/, "", m)
        if (index(m, "->") == 0) continue
        host = substr(m, 1, index(m, "->") - 1); sub(/.*:/, "", host)
        lo = host; hi = host
        if (index(host, "-") > 0) { lo = substr(host, 1, index(host, "-") - 1); hi = substr(host, index(host, "-") + 1) }
        if (p + 0 >= lo + 0 && p + 0 <= hi + 0) { print $1; exit }
      }
    }'
}

# "<engine> <container>" holding PORT under docker or podman; empty when no
# container of either engine publishes it (another program, or nothing).
port_holder() {
  local engine name
  for engine in docker podman; do
    command -v "$engine" >/dev/null 2>&1 || continue
    name="$("$engine" ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | port_owner "$1" || true)"
    [[ -n "$name" ]] && { printf '%s %s' "$engine" "$name"; return 0; }
  done
  return 0
}

# Whether HOLDER ("<engine> <container>") is this stack under ENGINE.
# Args: holder engine project. Prints ours|other-engine|foreign.
holder_kind() {
  local holder="$1" engine="$2" project="$3" name
  name="${holder#* }"
  case "$name" in
    "${project}"-*|"${project}"_*) ;;
    *) printf 'foreign'; return 0 ;;
  esac
  if [[ "${holder%% *}" == "$engine" ]]; then printf 'ours'; else printf 'other-engine'; fi
}

# The first port from START up that nothing listens on and that is not in
# RESERVED, the ports the stack itself will publish but has not bound yet
# (100 tries). Args: start [reserved...]
free_port_from() {
  local p="$1" end=$(( $1 + 100 )) reserved
  shift
  reserved=" $* "
  while (( p < end )); do
    if [[ "$reserved" != *" $p "* ]] && ! port_busy "$p"; then printf '%s' "$p"; return 0; fi
    p=$((p + 1))
  done
  return 1
}
