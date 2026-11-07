"""Dependency-free helpers for the Terminal-Bench lane.

Kept importable without ``harbor`` (the harbor CLI lives in its own uv tool
venv) so the plumbing tests can run on the plain project venv.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIRS = ("r4-contracts", "science", "packages/r4-teamengine-spi", "r4")
DRIVER_PATH = Path(__file__).with_name("driver.py")
#: Name of the driver inside the container. Not "driver.py": a task's own driver.py made
#: the model run `pkill -f driver.py`, which killed R4 (M0 2026-10-25, wdm-design, exit 143).
REMOTE_DRIVER_NAME = "r4bench_companion_entry.py"
HOST_SOURCES_PATH = Path.home() / ".config" / "r4" / "sources.json"


def filter_sources(raw: str, source: str) -> str:
    """Return a sources.json string holding ONLY the named public source.

    Fail closed: the bench must never carry platform profiles or extra keys
    into a task container.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"host sources.json is not valid JSON: {exc}") from exc
    entry = _pick_source(data, source)
    return json.dumps(
        {"sources": [entry], "active": source},
        indent=2,
    )


#: Placeholder the in-container driver replaces with its network's gateway
#: (the bench host), read from /proc/net/route.
HOST_GATEWAY_PLACEHOLDER = "__R4BENCH_HOST_GATEWAY__"
NIMBLE_SOURCE = "nimble"
NIMBLE_MODEL = "Bespoke-Nimble-9B"


#: model_proxy.py's port on the bench host: the lane default since 2026-10-05
#: (run_tb4.sh starts the proxy there; ``TB4_PROXY_PORT`` overrides both).
DEFAULT_MODEL_PROXY_PORT = 8333
MODEL_TOKEN_ENV = "R4BENCH_MODEL_TOKEN"

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", ""})


def parse_flag(value: object) -> bool:
    """A boolean ``--ak`` value: harbor passes ``true`` as a bool, other spellings as text."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise ValueError(f"expected true or false, not {value!r}")


def model_route(proxy_port: int, copy_source_key: bool, token: str) -> str:
    """How model calls leave the task container: ``"proxy"`` (the default) or
    ``"copy"`` (legacy: the source entry, API key included, is copied in).

    Fails closed with the exact fix: the key is copied only on an explicit
    ``--ak copy_source_key=true``, and proxy mode needs the proxy's token.
    """
    if copy_source_key:
        return "copy"
    if int(proxy_port) <= 0:
        raise ValueError(
            f"model_proxy_port={proxy_port} turns the host model proxy off; copying the "
            "source's API key into task containers needs an explicit --ak copy_source_key=true"
        )
    if not token:
        raise ValueError(
            f"model proxy mode (model_proxy_port={proxy_port}) needs {MODEL_TOKEN_ENV}: export "
            "the --token model_proxy.py was started with (run_tb4.sh does both), or pass "
            "--ak copy_source_key=true for the legacy key copy"
        )
    return "proxy"


def agent_config_owner_command(home: str) -> str:
    """Root command: give the uploaded sources.json to the agent.

    harbor's ``_upload_config_text`` chowns the file only when the environment names a
    ``default_user``. Task images that switch to a non-root ``USER`` without naming one
    left it root-owned, and R4 died on ``Permission denied: .../.config/r4/sources.json``
    within a minute (M0 2026-10-24: risk-scorer-replay, rs-archive-clone). The agent
    created the directory itself, so its owner is the agent user.
    """
    conf = shlex.quote(f"{home}/.config/r4")
    return f'chown "$(stat -c %u:%g {conf})" {conf}/sources.json && chmod 600 {conf}/sources.json'


def model_sources_json(
    source: str,
    *,
    proxy_port: int,
    copy_source_key: bool,
    token: str,
    host_sources: Callable[[], str],
) -> str:
    """The container's sources.json for the model: the proxy entry by default;
    the filtered host entry (``host_sources()`` read only then) on the legacy route."""
    if model_route(proxy_port, copy_source_key, token) == "copy":
        return filter_sources(host_sources(), source)
    return proxied_source(source, proxy_port, token)


#: Shell run as the agent user: prints a directory that user can write (B1,
#: 2026-10-05). The bench dir is root-owned, so a task that runs its agent as
#: a non-root user (TB 4.0 risk-scorer-replay) could not write result.json
#: there. $HOME/.r4bench first, a fresh /tmp dir when HOME is unusable.
# The container's outbound address (what the host model proxy logs as the peer):
# a UDP "connect" picks the default-route interface without sending a packet.
CONTAINER_IP_SCRIPT = (
    '/opt/r4bench/venv/bin/python -c "import socket; s = socket.socket(socket.AF_INET, '
    "socket.SOCK_DGRAM); s.connect(('10.255.255.255', 1)); print(s.getsockname()[0])\""
)

AGENT_OUTPUT_DIR_SCRIPT = (
    'd="${HOME:-/nonexistent}/.r4bench"; '
    'if ! { mkdir -p "$d" && [ -w "$d" ]; } 2>/dev/null; then '
    'd="$(mktemp -d /tmp/r4bench.XXXXXX)"; fi; printf %s "$d"'
)


def proxied_source(source: str, port: int, token: str) -> str:
    """A sources.json whose only source is the host model proxy (``model_proxy.py``)
    under the public source's name: the container holds the proxy's per-run
    token, never the source's API key."""
    if not token:
        raise ValueError(f"model proxy mode needs {MODEL_TOKEN_ENV} (the proxy's --token)")
    entry = {
        "name": source,
        "kind": "public",
        "url": f"http://{HOST_GATEWAY_PLACEHOLDER}:{int(port)}/v1",
        "api_key": token,
    }
    return json.dumps({"sources": [entry], "active": source}, indent=2)


def with_decision_source(sources_json: str, port: int, token: str) -> str:
    """Add the host decision-model proxy (``nimble_proxy.py``) as a public
    source. The token is the proxy's per-run token, never a platform
    credential."""
    if not token:
        raise ValueError("decision=nimble needs R4BENCH_NIMBLE_TOKEN (the proxy's --token)")
    data = json.loads(sources_json)
    data["sources"].append(
        {
            "name": NIMBLE_SOURCE,
            "kind": "public",
            "url": f"http://{HOST_GATEWAY_PLACEHOLDER}:{int(port)}/v1",
            "api_key": token,
        }
    )
    return json.dumps(data, indent=2)


def _entries(data: object) -> list:
    entries = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("host sources.json has no 'sources' list")
    return entries


def _named(entries: list, source: str) -> dict:
    matches = [entry for entry in entries if entry.get("name") == source]
    if not matches:
        available = ", ".join(str(entry.get("name")) for entry in entries)
        raise ValueError(f"source {source!r} not in host sources.json (have: {available})")
    return matches[0]


def _validate_public(entry: dict, source: str) -> None:
    if entry.get("kind") != "public":
        raise ValueError(
            f"source {source!r} is kind={entry.get('kind')!r}; terminal lane needs a "
            "kind=public OpenAI-compatible source"
        )
    if not entry.get("url") or not entry.get("api_key"):
        raise ValueError(f"source {source!r} lacks url/api_key in host sources.json")


def _pick_source(data: object, source: str) -> dict:
    """Select the one usable entry, or raise naming the exact failure."""
    entry = _named(_entries(data), source)
    _validate_public(entry, source)
    return entry


# Tool caches inside the package trees (a pytest run writes .pytest_cache) must
# not force a wheel rebuild.
_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})


def _package_signature(root: Path) -> str:
    """Content signature over the four package trees (all text data files).

    SKILL.md/reference files ship inside the wheels and change runtime
    behavior (skill keywords, guides) — a *.py-only hash let stale wheels
    survive content edits.
    """
    digest = hashlib.sha256()
    for relpath in PACKAGE_DIRS:
        package = root / relpath
        pyproject = package / "pyproject.toml"
        digest.update(relpath.encode())
        if pyproject.is_file():
            digest.update(pyproject.read_bytes())
        for entry in sorted(package.rglob("*")):
            if not entry.is_file() or _CACHE_DIRS.intersection(entry.parts):
                continue
            if entry.suffix in {".pyc", ".so", ".zip", ".whl", ".png", ".jpg"}:
                continue
            digest.update(str(entry.relative_to(package)).encode())
            try:
                digest.update(entry.read_bytes())
            except OSError:
                continue
    return hashlib.sha256(digest.digest()).hexdigest()[:16]


def ensure_wheelhouse(root: Path = REPO_ROOT) -> Path:
    """Build (or reuse) the wheelhouse for the four r4 packages.

    Cached under $XDG_CACHE_HOME/r4bench/terminal-wheels/<signature>; rebuilt
    automatically when any package source file changes.
    """
    # R4BENCH_PACKAGE_ROOT: build from a patched copy of the four package trees (variant runs).
    root = Path(os.environ.get("R4BENCH_PACKAGE_ROOT") or root)
    signature = _package_signature(root)
    cache_root = (
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        / "r4bench"
        / "terminal-wheels"
    )
    target = cache_root / signature
    wheels = list(target.glob("*.whl")) if target.is_dir() else []
    if len(wheels) >= len(PACKAGE_DIRS):
        return target
    staging = cache_root / f".staging-{signature}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    subprocess.run(  # noqa: S603
        [
            *["pip", "wheel", "--no-deps", "-w", str(staging)],
            *[str(root / relpath) for relpath in PACKAGE_DIRS],
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return target


# ---------------------------------------------------------------------------
# Agent env (B5, 2026-10-24): what R4CompanionAgent hands the in-container
# driver, built in one place so run-meta.json records exactly what a trial got.

#: The in-container driver sets these with setdefault (driver.UNATTENDED_ENV):
#: nobody answers checkpoints or ask_user questions in a benchmark task.
DRIVER_ENV_DEFAULTS = {"R4_AUTONOMOUS": "1", "R4_ASK_USER_MODE": "auto"}
DEFAULT_R4_TIMEOUT_SEC = 1200
#: ``--ak profile=``: empty or ``default`` = product defaults (the headline);
#: ``terminal`` = the R4_AGENT_PROFILE=terminal ablation (plan H2).
AGENT_PROFILES = frozenset({"", "default", "terminal"})
#: Harbor's names for the system packages the R4 agent installs in a task container.
R4_SYSTEM_DEPENDENCIES = ("python3", "python_pip", "python_venv", "curl")


def decision_env(decision: object) -> str:
    """R4_DECISION_MODEL for ``--ak decision=``: ``off`` or the Nimble proxy source.
    Set explicitly either way: r4 turns the decision model on by default."""
    name = str(decision).strip().lower()
    if name not in ("off", NIMBLE_SOURCE):
        raise ValueError(f"decision must be off or nimble, not {decision!r}")
    return f"{NIMBLE_MODEL}@{NIMBLE_SOURCE}" if name == NIMBLE_SOURCE else "off"


def _number_text(label: str, value: object, upper: float) -> str | None:
    """A sampling ``--ak`` value as env text; None when unset; ValueError when bad."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a number, not {value!r}") from exc
    if not 0.0 <= number <= upper:
        raise ValueError(f"{label} must be between 0 and {upper}, not {value!r}")
    return str(number)


def knob_env(profile: object = "", temperature: object = None, top_p: object = None) -> dict:
    """R4 env for ``--ak profile= / temperature= / top_p=``: each variable only when
    asked, so a default run leaves R4's own defaults in force."""
    name = str(profile or "").strip().lower()
    if name not in AGENT_PROFILES:
        raise ValueError(f"profile must be default or terminal, not {profile!r}")
    env = {"R4_AGENT_PROFILE": name} if name == "terminal" else {}
    sampling = (
        ("R4_COMPANION_TEMPERATURE", _number_text("temperature", temperature, 2.0)),
        ("R4_COMPANION_TOP_P", _number_text("top_p", top_p, 1.0)),
    )
    env.update({key: text for key, text in sampling if text is not None})
    return env


def agent_env(
    r4_timeout_sec: object = DEFAULT_R4_TIMEOUT_SEC,
    decision: object = "off",
    profile: object = "",
    temperature: object = None,
    top_p: object = None,
) -> dict:
    """The env R4CompanionAgent passes to the driver exec (harbor ``--ae`` values
    are layered on top by harbor itself)."""
    return {
        "R4_TIMEOUT": str(int(str(r4_timeout_sec))),
        "R4_DECISION_MODEL": decision_env(decision),
        **knob_env(profile, temperature, top_p),
    }


# ---------------------------------------------------------------------------
# run-meta.json (B3, 2026-10-24): every run says what it ran with. Names and
# values of allowlisted knobs only; anything that looks like a credential is
# dropped (the per-run proxy tokens travel in the environment, never in args).

RUN_META_NAME = "run-meta.json"
AGENT_KNOB_NAMES = frozenset(
    {
        "R4_AUTONOMOUS",
        "R4_AGENT_PROFILE",
        "R4_ASK_USER_MODE",
        "R4_TOOL_PYTHON",
        "R4_TIMEOUT",
        "R4_DECISION_MODEL",
    }
)
AGENT_KNOB_PREFIXES = ("R4_COMPANION_", "R4_SHELL_")
_SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSW", "JWT", "CREDENTIAL", "BEARER", "AUTH")
#: --ak names that contain a marker but hold no credential.
_NOT_SECRET_NAMES = frozenset(
    {"copy_source_key", "default_max_tokens", "max_tokens", "max_output_tokens"}
)
#: Values shaped like credentials: hex tokens (``openssl rand -hex``), JWTs, sk- keys.
_TOKEN_LIKE = re.compile(r"[0-9a-fA-F]{32,}|eyJ[\w-]+\.[\w-]+(\.[\w-]*)?|sk-[\w-]{16,}")

R4_INSTALL_FOOTPRINT = (
    "system packages (harbor names): " + ", ".join(R4_SYSTEM_DEPENDENCIES),
    "/opt/r4bench: venv, wheels, r4bench_companion_entry.py, prompt.txt",
    "$HOME/.config/r4/sources.json: the model proxy entry with the per-run token",
    "$HOME/.r4bench (else /tmp/r4bench.*): driver result and transcript",
    "<workdir>/.r4/: R4 session state and large-result spills",
)
TERMINUS_INSTALL_FOOTPRINT = ("system packages when missing: tmux, asciinema (terminus-2)",)
LANE_FOOTPRINTS = {"r4": R4_INSTALL_FOOTPRINT, "terminus-2": TERMINUS_INSTALL_FOOTPRINT}

_HARBOR_VALUE_FLAGS = {
    **dict.fromkeys(("-d", "--dataset"), "dataset"),
    **dict.fromkeys(("-t", "--task"), "task"),
    **dict.fromkeys(("-a", "--agent"), "agent"),
    **dict.fromkeys(("-m", "--model"), "model"),
    **dict.fromkeys(("-e", "--env"), "environment"),
    **dict.fromkeys(("-n", "--n-concurrent"), "concurrency"),
    **dict.fromkeys(("-k", "--n-attempts"), "attempts"),
    **dict.fromkeys(("-l", "--n-tasks"), "n_tasks"),
    "--agent-timeout-multiplier": "agent_timeout_multiplier",
    "--timeout-multiplier": "timeout_multiplier",
}
_HARBOR_LIST_FLAGS = {
    **dict.fromkeys(("-i", "--include-task-name"), "include"),
    **dict.fromkeys(("-x", "--exclude-task-name"), "exclude"),
}
_HARBOR_PAIR_FLAGS = {
    **dict.fromkeys(("--ak", "--agent-kwarg"), "ak"),
    **dict.fromkeys(("--ae", "--agent-env"), "ae"),
}
_HARBOR_TAKES_VALUE = {**_HARBOR_VALUE_FLAGS, **_HARBOR_LIST_FLAGS, **_HARBOR_PAIR_FLAGS}


def is_secret(name: str, value: object = "") -> bool:
    """A name or value that may carry a credential (never written to run-meta)."""
    if name not in _NOT_SECRET_NAMES and any(m in name.upper() for m in _SECRET_MARKERS):
        return True
    return bool(_TOKEN_LIKE.search(str(value)))


def is_agent_knob(name: str) -> bool:
    """An R4 behaviour knob run-meta records (the plan's B3 allowlist)."""
    allowed = name in AGENT_KNOB_NAMES or name.startswith(AGENT_KNOB_PREFIXES)
    return allowed and not is_secret(name)


def _split_flag(args: Sequence[str], index: int) -> tuple[str, str | None, int]:
    """(flag, value, items consumed) at ``args[index]``; ``--flag=value`` too."""
    item = args[index]
    if item.startswith("--") and "=" in item:
        flag, _, value = item.partition("=")
        return flag, value, 1
    if item in _HARBOR_TAKES_VALUE and index + 1 < len(args):
        return item, args[index + 1], 2
    return item, None, 1


def _store_flag(parsed: dict, flag: str, value: str | None) -> None:
    if value is None:
        return
    if flag in _HARBOR_VALUE_FLAGS:
        parsed[_HARBOR_VALUE_FLAGS[flag]] = value
    elif flag in _HARBOR_LIST_FLAGS:
        parsed[_HARBOR_LIST_FLAGS[flag]].append(value)
    elif flag in _HARBOR_PAIR_FLAGS:
        key, _, pair_value = value.partition("=")
        parsed[_HARBOR_PAIR_FLAGS[flag]][key] = pair_value


def parse_harbor_args(args: Sequence[str]) -> dict:
    """The parts of a ``harbor run`` command line a run is described by."""
    parsed: dict = {"include": [], "exclude": [], "ak": {}, "ae": {}}
    index = 0
    while index < len(args):
        flag, value, used = _split_flag(args, index)
        _store_flag(parsed, flag, value)
        index += used
    return parsed


def _public(pairs: Mapping[str, object]) -> tuple[dict, list[str]]:
    """(pairs without credentials, names of the dropped ones)."""
    kept = {k: v for k, v in pairs.items() if not is_secret(k, v)}
    return kept, sorted(set(pairs) - set(kept))


def _effective_knobs(lane: str, ak: Mapping[str, str], ae: Mapping[str, str]) -> dict:
    """R4 knobs in the agent env: driver defaults < R4CompanionAgent's env < --ae
    (harbor layers --ae over the per-exec env; the driver only fills gaps)."""
    if lane != "r4":
        return {k: v for k, v in ae.items() if is_agent_knob(k)}
    names = ("r4_timeout_sec", "decision", "profile", "temperature", "top_p")
    merged = {**DRIVER_ENV_DEFAULTS, **agent_env(**{n: ak[n] for n in names if n in ak}), **ae}
    return {k: v for k, v in merged.items() if is_agent_knob(k) and not is_secret(k, v)}


def _sampling(ak: Mapping[str, str], knobs: Mapping[str, str]) -> dict:
    """Temperature and top_p as set for the run; None = the agent's own default."""
    return {
        "temperature": ak.get("temperature") or knobs.get("R4_COMPANION_TEMPERATURE"),
        "top_p": ak.get("top_p") or knobs.get("R4_COMPANION_TOP_P"),
    }


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip()


def _git_state(repo: Path) -> dict:
    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {
        "commit": _git(repo, "rev-parse", "HEAD"),
        "dirty": None if status is None else bool(status),
    }


def run_meta(
    lane: str,
    harbor_args: Sequence[str],
    *,
    extra: Mapping[str, object] | None = None,
    repo: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """What a run ran with: commit, harbor settings, R4 knobs, sampling, footprint."""
    if lane not in LANE_FOOTPRINTS:
        raise ValueError(f"lane must be one of {sorted(LANE_FOOTPRINTS)}, not {lane!r}")
    environ = os.environ if environ is None else environ
    parsed = parse_harbor_args(harbor_args)
    ak, ak_dropped = _public(parsed.pop("ak"))
    ae, ae_dropped = _public(parsed.pop("ae"))
    knobs = _effective_knobs(lane, ak, ae)
    extra_kept, extra_dropped = _public(dict(extra or {}))
    return {
        "schema": 1,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "lane": lane,
        **_git_state(repo),
        "package_root": environ.get("R4BENCH_PACKAGE_ROOT") or None,
        "harbor": {**parsed, "ak": ak},
        "agent_knobs": knobs,
        **_sampling(ak, knobs),
        "exec_timeout_sec": ak.get("exec_timeout_sec"),
        "install_footprint": list(LANE_FOOTPRINTS[lane]),
        **extra_kept,
        "dropped_as_secret": [
            *(f"ak:{name}" for name in ak_dropped),
            *(f"ae:{name}" for name in ae_dropped),
            *(f"set:{name}" for name in extra_dropped),
        ],
    }


def write_run_meta(
    path: Path,
    lane: str,
    harbor_args: Sequence[str],
    *,
    extra: Mapping[str, object] | None = None,
    repo: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Write ``run-meta.json`` (``run_meta``) and return what was written."""
    meta = run_meta(lane, harbor_args, extra=extra, repo=repo, environ=environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


def main(argv: Sequence[str] | None = None) -> int:
    """``run-meta --out FILE --lane r4|terminus-2 [--set k=v]... -- <harbor run args>``"""
    import argparse

    argv = list(sys.argv[1:] if argv is None else argv)
    split = argv.index("--") if "--" in argv else len(argv)
    parser = argparse.ArgumentParser(prog="support.py run-meta")
    parser.add_argument("command", choices=["run-meta"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lane", required=True, choices=sorted(LANE_FOOTPRINTS))
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv[:split])
    extra = dict(item.partition("=")[::2] for item in args.set)
    write_run_meta(args.out, args.lane, argv[split + 1 :], extra=extra)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
