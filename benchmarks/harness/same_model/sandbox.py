"""Shared bwrap sandbox for the three bench lanes (Claude Code, OpenCode, R4).

One builder, so every lane gets the same view of the machine (review fixes 2026-10-03):

network   --unshare-net: each case has its own network namespace with only a
          loopback interface. The only reachable endpoints are in-sandbox TCP
          relays (socat, started by INIT_SH before the harness is exec'd) on
          127.0.0.1:<port>, each forwarding to ONE host UNIX socket bound into
          /run/bench/:
            8330  -> run/bridge.sock        claude lane   (anthropic_bridge.py)
            8332  -> run/passthrough.sock   opencode + r4 (openai_passthrough.py)
            8339  -> run/g-<case>.sock      per-case egress guard (attempt logger)
            55432 -> run/pg.sock            arms B/C/P only (kgq Postgres)
            58333 -> run/s3.sock            arm B only (local SeaweedFS)
          Nothing else on the host (LiteLLM :18002, OpenCode servers, nimble
          proxy, kubelet, the internet) is routable; abstract UNIX sockets are
          netns-scoped and therefore unreachable too.
ipc       --unshare-ipc --unshare-uts --unshare-pid --unshare-cgroup-try
          --new-session --cap-drop ALL; /run, /tmp, /var/tmp, /dev/shm are private
          tmpfs (no D-Bus, ssh-agent, kwallet, podman, docker, libvirt, cc-socks);
          /dev is a minimal devtmpfs plus the NVIDIA nodes (kgq's embedder ran on
          the GPU in run_glm.py's --dev-bind /dev; kept for timing parity).
files     / read-only; /home, /media, /mnt, /srv, /opt/nfs, /opt/images, /var/log
          are replaced by empty tmpfs and an ALLOW-LIST is bound back:
            ro: the corpus dir (+ every symlink target it reaches that lies in a
                masked root), the arm's tool tree (C/P: proto/, B: current/ +
                repo umrs/src + r4/src), the harness binary (claude version file /
                opencode binary + bench config / repo r4/ + editable package
                sources + benchmarks/harness),
                the repo .venv + its base interpreter (python3 on PATH, kgq),
                ~/.cache/huggingface/hub (models only; no token file)
            rw: the case dir; proto/out (arms C/P: kgq query cache)
          gold/, results*/, other cases, the repo checkout, shell histories,
          keyrings, ~/.ansible, ~/.cache/claude*, ~/.config/* are simply absent.
env       --clearenv + the lane's pinned environment (PATH pinned, see PINNED_PATH).
          bwrap's whole argument vector (incl. --setenv values) is passed through
          an unlinked temp file (``bwrap --args FD``): the process table shows only that.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import paths as P  # noqa: E402

BENCH = P.BENCH
R4GLM = P.OUT
HERE = P.LANE  # lane outputs (results_<lane>, logs, run, work) — outside git
SMOKE = P.SMOKE  # synthetic smoke fixture (in git)
SMOKE_OUT = P.SMOKE_OUT  # smoke-run outputs (outside git)
LOGS = HERE / "logs"
RUN = HERE / "run"
REPO = P.REPO
REAL_HOME = Path.home()
VENV = REPO / ".venv"
OPENCODE_BIN = REAL_HOME / ".opencode/bin/opencode"
OPENCODE_CFG = P.CODE / "opencode.glm.json"
HF_HUB = REAL_HOME / ".cache/huggingface/hub"
IN_SOCK = "/run/bench"

SERVICES: dict[str, tuple[int, Path]] = {  # name -> (in-sandbox 127.0.0.1 port, host UNIX socket)
    "bridge": (8330, RUN / "bridge.sock"),
    "passthrough": (8332, RUN / "passthrough.sock"),
    "pg": (55432, RUN / "pg.sock"),
    "s3": (58333, RUN / "s3.sock"),
}
GUARD_PORT = 8339
LANE_SERVICES = {"claude": ("bridge",), "opencode": ("passthrough",), "r4": ("passthrough",)}
ARM_SERVICES = {"A": (), "B": ("pg", "s3"), "C": ("pg",), "P": ("pg",)}
PINNED_PATH = f"{VENV}/bin:/usr/local/bin:/usr/bin:/bin"
MASK_ROOTS = tuple(
    Path(p)
    for p in (
        "/home",
        "/tmp",
        "/var/tmp",
        "/run",
        "/media",
        "/mnt",
        "/srv",
        "/opt/nfs",
        "/opt/images",
        "/var/log",
        "/afs",
    )
    if os.path.isdir(p)
)
NVIDIA_DEVS = sorted(glob.glob("/dev/nvidia*"))

# Runs inside the sandbox as `bash -c INIT_SH bench-init CMD...`: start one TCP->UNIX relay per
# BENCH_RELAYS entry ("port=/run/bench/x.sock ..."), wait until each is LISTENing (read from
# /proc/net/tcp, so no connection is made), drop the variable, exec the harness.
INIT_SH = r"""
set -u
for spec in ${BENCH_RELAYS:-}; do
  /usr/bin/socat TCP-LISTEN:"${spec%%=*}",bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:"${spec#*=}" </dev/null >/dev/null 2>&1 &
done
for spec in ${BENCH_RELAYS:-}; do
  want=$(printf '0100007F:%04X 00000000:0000 0A' "${spec%%=*}")
  for _ in $(seq 1 250); do grep -q "$want" /proc/net/tcp && break; sleep 0.02; done
done
unset BENCH_RELAYS
exec "$@"
"""


class SandboxError(RuntimeError):
    pass


def _within(p: Path, parent: Path) -> bool:
    try:
        p.relative_to(parent)
        return True
    except ValueError:
        return False


def masked(p: Path) -> bool:
    return any(_within(p, r) for r in MASK_ROOTS)


def venv_base() -> Path | None:
    """Install root of the venv's base interpreter (uv python under ~/.local/share)."""
    real = Path(os.path.realpath(VENV / "bin/python"))
    root = real.parents[1]
    return root if masked(root) else None


def _pth_dirs(site: Path) -> list[Path]:
    """Absolute repo paths listed in the venv's .pth files."""
    out: list[Path] = []
    for pth in sorted(site.glob("*.pth")):
        for line in pth.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("/") and _within(Path(line), REPO):
                out.append(Path(line))
    return out


def _finder_dirs(site: Path) -> list[Path]:
    """Repo package dirs from the editable finders' MAPPING (src/<pkg> -> src)."""
    out: list[Path] = []
    for finder in sorted(site.glob("__editable___*_finder.py")):
        for _name, path in re.findall(r"'([^']+)': '(/[^']+)'", finder.read_text(errors="replace")):
            p = Path(path)
            if _within(p, REPO):
                out.append(p.parent if p.parent.name == "src" else p)
    return out


def repo_src_dirs() -> list[Path]:
    """Source dirs of the repo packages installed editable into the .venv (r4, r4_contracts,
    umrs, ...): read from the venv's .pth files and the umrs editable finder's MAPPING."""
    site = next(iter(sorted((VENV / "lib").glob("python3*/site-packages"))), None)
    if site is None:
        return []
    out = _pth_dirs(site) + _finder_dirs(site)
    return [p for p in dict.fromkeys(out) if p.exists()]


def claude_binary() -> Path:
    cand = shutil.which("claude") or str(REAL_HOME / ".local/bin/claude")
    return Path(os.path.realpath(cand))


_TREE_CACHE: dict[tuple[str, ...], tuple[list[Path], list[tuple[str, Path]]]] = {}


def _split_links(keep: list[Path]) -> tuple[list[Path], list[tuple[str, Path]]]:
    """keep -> (existing roots, (link text, link) for every symlink in keep, bound by target)."""
    roots: list[Path] = []
    links: list[tuple[str, Path]] = []
    for k in keep:
        if k.is_symlink():  # recreate the link itself, bind its target
            links.append((os.readlink(k), k))
            k = Path(os.path.realpath(k))
        if k.exists():
            roots.append(k)
    return roots, links


def _new_target(e: Path, seen: set[str]) -> Path | None:
    """Resolved target of symlink e when it exists and was not seen before (marks it seen)."""
    if not e.is_symlink():
        return None
    t = Path(os.path.realpath(e))
    if str(t) in seen or not t.exists():
        return None
    seen.add(str(t))
    return t


def _walk_targets(top: Path, seen: set[str]) -> tuple[list[Path], int]:
    """(new symlink targets under top in walk order, entries visited); links not followed."""
    found: list[Path] = []
    n = 0
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        for name in dirnames + filenames:
            n += 1
            t = _new_target(Path(dirpath) / name, seen)
            if t is not None:
                found.append(t)
    return found, n


def _absorb(t: Path, roots: list[Path], extra: list[Path], todo: list[Path]) -> list[Path]:
    """Add a masked target not covered yet (dropping the extras it covers); queue it if a dir."""
    if masked(t) and not any(_within(t, x) for x in roots + extra):
        extra = [x for x in extra if not _within(x, t)] + [t]
        if t.is_dir():
            todo.append(t)
    return extra


def with_link_targets(keep: list[Path]) -> tuple[list[Path], list[tuple[str, Path]]]:
    """keep + every symlink target reachable from it that lies in a masked root (walked
    recursively in-process, nothing printed) -> (paths to ro-bind, symlinks to recreate)."""
    key = tuple(str(k) for k in keep)
    if key in _TREE_CACHE:
        return _TREE_CACHE[key]
    roots, links = _split_links(keep)
    extra: list[Path] = []
    todo = [r for r in roots if r.is_dir()]
    seen: set[str] = set()
    budget = 500_000  # entries visited; checked between walks
    while todo and budget > 0:
        found, n = _walk_targets(todo.pop(), seen)
        budget -= n
        for t in found:
            extra = _absorb(t, roots, extra, todo)
    out = (roots + extra, links)
    _TREE_CACHE[key] = out
    return out


def arm_tree(arm: str, corpus_root: Path) -> tuple[list[Path], list[Path]]:
    """(read-only, writable) bench paths an arm's prompt refers to."""
    ro = [corpus_root / "corpus"]
    rw: list[Path] = []
    if arm in ("C", "P"):
        ro.append(BENCH / "proto")
        rw.append(BENCH / "proto/out")
    elif arm == "B":  # kgq-current runs the real UMRS/R4 code in-process
        ro += [BENCH / "current", REPO / "r4", *repo_src_dirs()]
    elif arm != "A":
        raise SandboxError(f"unknown arm {arm!r}")
    return ro, rw


def lane_ro(lane: str) -> list[Path]:
    base = [VENV] + ([venv_base()] if venv_base() else []) + ([HF_HUB] if HF_HUB.is_dir() else [])
    if lane == "claude":
        return base + [claude_binary()]
    if lane == "opencode":
        return base + [OPENCODE_BIN, OPENCODE_CFG]
    if lane == "r4":
        # r4/ = package root (src + resources/ read at runtime); the other editable sources it imports
        return base + [
            REPO / "r4",
            *repo_src_dirs(),
            REPO / "benchmarks/__init__.py",
            REPO / "benchmarks/harness",
        ]
    if lane == "probe":
        return base
    raise SandboxError(f"unknown lane {lane!r}")


@dataclass
class Box:
    lane: str  # claude | opencode | r4 | probe
    arm: str  # A | B | C | P
    work: Path  # case dir (bound rw)
    cwd: Path
    env: dict[str, str]
    cmd: list[str]
    corpus_root: Path  # BENCH (real) or SMOKE
    guard_sock: Path | None = None
    services: tuple[str, ...] | None = None  # default: lane + arm services
    extra_ro: list[Path] = field(default_factory=list)
    tool_tree: bool = True  # False: corpus only, whatever the arm (smoke mode)


def services_for(box: Box) -> tuple[str, ...]:
    if box.services is not None:
        return box.services
    arm_svcs = ARM_SERVICES[box.arm] if box.tool_tree else ()
    return tuple(dict.fromkeys(LANE_SERVICES.get(box.lane, ()) + arm_svcs))


def _base_opts() -> list[str]:
    """Namespaces, read-only /, minimal /dev (+ NVIDIA nodes), masked roots as empty tmpfs."""
    a = [
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--hostname",
        "bench-sandbox",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/dev/shm",
    ]
    for d in NVIDIA_DEVS:
        a += ["--dev-bind", d, d]
    for r in MASK_ROOTS:
        a += ["--tmpfs", str(r)]
    return a


def _shallow_first(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda x: (len(x.parts), str(x)))


def _bind_opts(box: Box) -> list[str]:
    """The allow-list: ro tree (+ link targets, lane binaries), recreated links, rw dirs."""
    ro, rw = (
        arm_tree(box.arm, box.corpus_root) if box.tool_tree else ([box.corpus_root / "corpus"], [])
    )
    tree, links = with_link_targets(ro)
    ro_all = [p for p in dict.fromkeys(tree + lane_ro(box.lane) + list(box.extra_ro)) if p.exists()]
    a: list[str] = []
    for p in _shallow_first(ro_all):
        a += ["--ro-bind", str(p), str(p)]
    for target, link in links:
        a += ["--symlink", target, str(link)]
    for p in _shallow_first([*rw, box.work]):
        p.mkdir(parents=True, exist_ok=True)
        a += ["--bind", str(p), str(p)]
    return a


def _relay_opts(box: Box) -> tuple[list[str], list[str]]:
    """(socket binds into /run/bench, BENCH_RELAYS specs) for the box's services + guard."""
    a: list[str] = []
    relays = []
    for name in services_for(box):
        port, sock = SERVICES[name]
        if not sock.is_socket():
            raise SandboxError(f"{name}: {sock} is not a socket (run start_proxies.sh)")
        a += ["--bind", str(sock), f"{IN_SOCK}/{name}.sock"]
        relays.append(f"{port}={IN_SOCK}/{name}.sock")
    if box.guard_sock is not None:
        a += ["--bind", str(box.guard_sock), f"{IN_SOCK}/guard.sock"]
        relays.append(f"{GUARD_PORT}={IN_SOCK}/guard.sock")
    return a, relays


def bwrap_args(box: Box) -> tuple[list[str], list[str]]:
    """(options -> passed via --args FD, command -> on the real argv after '--'; bwrap
    does not accept the command inside an args file)."""
    a = _base_opts()
    a += _bind_opts(box)
    relay_opts, relays = _relay_opts(box)
    a += relay_opts
    a += ["--clearenv"]
    env = dict(box.env)
    env["BENCH_RELAYS"] = " ".join(relays)
    for k, v in env.items():
        a += ["--setenv", k, v]
    a += ["--chdir", str(box.cwd)]
    return a, ["/bin/bash", "-c", INIT_SH, "bench-init", *box.cmd]


def run(box: Box, input_text: str | None, timeout: float) -> tuple[int | None, str, str, bool]:
    """Run the box; bwrap's argv travels through an unlinked temp file in run/ (0700) passed as
    ``--args FD`` (this Python has no os.memfd_create). Returns (rc, stdout, stderr, timed_out)."""
    opts, command = bwrap_args(box)
    data = b"".join(x.encode("utf-8") + b"\0" for x in opts)
    RUN.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryFile(dir=RUN) as f:
        f.write(data)
        f.flush()
        f.seek(0)
        fd = f.fileno()
        try:
            p = subprocess.run(
                ["bwrap", "--args", str(fd), "--", *command],
                input=input_text if input_text is not None else "",
                capture_output=True,
                text=True,
                timeout=timeout,
                pass_fds=(fd,),
            )
            return p.returncode, p.stdout, p.stderr, False
        except subprocess.TimeoutExpired as exc:
            out = (
                exc.stdout.decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            err = (
                exc.stderr.decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            return None, out, err, True


def host_netns() -> str:
    return os.readlink("/proc/self/ns/net")
