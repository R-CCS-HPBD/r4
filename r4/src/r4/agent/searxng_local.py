"""A local SearXNG for ``web_search`` (ADR-102, 2026-10-30).

``r4 search-server up`` runs the SearXNG image on ``127.0.0.1`` with podman
(or docker) and records its URL; ``web_search`` then uses it unless
``R4_WEB_SEARCH_URL`` names another instance. The container holds no state:
its settings (JSON output on, limiter and image proxy off, a per-user secret
key) live in ``<config dir>/searxng/settings.yml``, mounted read-only, and
the container runs with every capability dropped.

Every function returns a result dict with a ``reason`` instead of raising,
like ``companion/mcp_exposure.ensure_proxy_running``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CONTAINER_NAME = "r4-searxng"
IMAGE_ENV = "R4_SEARXNG_IMAGE"
DEFAULT_IMAGE = "docker.io/searxng/searxng:2026.10.15-b00212a83"
PORT_ENV = "R4_SEARXNG_PORT"
DEFAULT_PORT = 8888
#: ``podman`` or ``docker``; unset = podman when installed, else docker.
ENGINE_ENV = "R4_CONTAINER_ENGINE"

_PULL_TIMEOUT_SEC = 900
_RUN_TIMEOUT_SEC = 60
_START_TIMEOUT_SEC = 60.0
_PROBE_TTL_SEC = 30.0
_PROBE_CACHE: Dict[str, tuple[float, bool]] = {}
_PULL_HINT = "a stale registry login also fails public pulls: `podman logout docker.io`"
_AUTH_ERROR = re.compile(r"unauthori[sz]ed|authenticat|incorrect username|denied", re.IGNORECASE)
#: "Installed but cannot run anything" (2026-11-01: a stopped podman machine
#: made the console say "downloading…" and then end on podman's raw error).
_PODMAN_DOWN = "Cannot connect to Podman"
_DOCKER_DOWN = "Cannot connect to the Docker daemon"

_SETTINGS = """\
# Written by `r4 search-server up` (ADR-102). Edits are kept: R4 writes this
# file only when it is missing. web_search needs the json format.
use_default_settings: true
server:
  secret_key: "{secret}"
  limiter: false
  image_proxy: false
search:
  formats: [html, json]
"""


def state_dir() -> Path:
    from r4.config import _resolve_config_dir

    return _resolve_config_dir() / "searxng"


def _state_file() -> Path:
    return state_dir() / "instance.json"


def recorded_url() -> Optional[str]:
    """The URL ``up`` recorded, or None (never started, or ``down``)."""
    try:
        url = json.loads(_state_file().read_text()).get("url")
    except (OSError, ValueError, AttributeError):
        return None
    return str(url) if url else None


def local_url() -> Optional[str]:
    """The recorded instance's URL while it answers (probe cached 30 s)."""
    url = recorded_url()
    return url if url and healthy(url) else None


def healthy(url: str, *, fresh: bool = False) -> bool:
    now = time.monotonic()
    cached = _PROBE_CACHE.get(url)
    if cached is not None and not fresh and now - cached[0] < _PROBE_TTL_SEC:
        return cached[1]
    ok = _probe(url.rstrip("/") + "/healthz")
    _PROBE_CACHE[url] = (now, ok)
    return ok


def _probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2.0):
            return True
    except Exception:  # noqa: BLE001 — probe failure == not answering
        return False


def container_engine() -> Optional[str]:
    named = os.environ.get(ENGINE_ENV, "").strip()
    candidates = (named,) if named else ("podman", "docker")
    return next((name for name in candidates if shutil.which(name)), None)


def _port() -> int:
    try:
        return int(os.environ.get(PORT_ENV, "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def _image() -> str:
    return os.environ.get(IMAGE_ENV, "").strip() or DEFAULT_IMAGE


def write_settings() -> Path:
    """``settings.yml`` with a fresh secret key, unless one already exists."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "settings.yml"
    if not path.is_file():
        path.touch(mode=0o600)
        path.write_text(_SETTINGS.format(secret=secrets.token_hex(32)))
    return path


def run_command(engine: str, port: int, image: str, settings_dir: Path) -> List[str]:
    return [
        engine,
        "run",
        "-d",
        "--name",
        CONTAINER_NAME,
        "--label",
        "r4.managed=searxng",
        "-p",
        f"127.0.0.1:{port}:8080",
        "-v",
        f"{settings_dir}:/etc/searxng:Z,ro",
        "-e",
        f"SEARXNG_BASE_URL=http://127.0.0.1:{port}/",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        image,
    ]


def _run(command: List[str], timeout: int) -> Optional[str]:
    """None on success, else the command's error text."""
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, no shell
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if done.returncode == 0:
        return None
    return (done.stderr or done.stdout or f"exit {done.returncode}").strip()


#: Receives one human-readable line per step of ``up`` (the console shows them).
Progress = Callable[[str], None]


def _quiet(_message: str) -> None:
    return None


def up(progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Start the local instance (pulling the image first) unless it answers."""
    url = f"http://127.0.0.1:{_port()}"
    if healthy(url, fresh=True):
        _record(url, None)
        return {"state": "running", "url": url}
    engine = container_engine()
    if engine is None:
        return {"state": "failed", "reason": "no container engine (install podman or docker)"}
    unready = engine_unready(engine)
    if unready:
        return unready
    return _start(engine, url, progress or _quiet)


def engine_unready(engine: str) -> Optional[Dict[str, Any]]:
    """A failed result when *engine* is installed but cannot run containers
    (a stopped podman machine or Docker daemon), else None."""
    error = _run([engine, "info"], _RUN_TIMEOUT_SEC)
    if error is None:
        return None
    return {"state": "failed", **_explain(engine, error, f"{engine} is not usable")}


def _explain(engine: str, error: str, what: str) -> Dict[str, str]:
    """A short reason and, when one is known, the command that fixes it."""
    if _PODMAN_DOWN in error:
        return {"reason": "podman cannot reach its machine or service", "hint": _podman_hint()}
    if _DOCKER_DOWN in error:
        return {
            "reason": "the Docker daemon is not running",
            "hint": "start Docker Desktop (or `sudo systemctl start docker`), then try again",
        }
    explained = {"reason": f"{what}: {_first_line(error)}"}
    if _AUTH_ERROR.search(error):
        explained["hint"] = _PULL_HINT
    return explained


def _podman_hint() -> str:
    if sys.platform.startswith("linux"):
        return (
            "a remote connection or CONTAINER_HOST points at a service that is not "
            "running: check `podman system connection list`, then try again"
        )
    return (
        "start it with `podman machine start` (first time: `podman machine init`), then try again"
    )


def _first_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[0][:200] if lines else "no output"


def _start(engine: str, url: str, say: Progress) -> Dict[str, Any]:
    image = _image()
    settings = write_settings()
    error = _ensure_image(engine, image, say)
    if error:
        return {"state": "failed", **_explain(engine, error, f"pull {image}")}
    say(f"starting the container with {engine}")
    _run([engine, "rm", "-f", CONTAINER_NAME], _RUN_TIMEOUT_SEC)
    error = _run(run_command(engine, _port(), image, settings.parent), _RUN_TIMEOUT_SEC)
    if error:
        return {"state": "failed", **_explain(engine, error, f"{engine} run")}
    say(f"waiting for SearXNG to answer on {url}")
    if not _wait_healthy(url):
        return {"state": "failed", "reason": f"no answer on {url}/healthz within 60 s"}
    _record(url, engine)
    return {"state": "started", "url": url, "engine": engine, "image": image}


def _ensure_image(engine: str, image: str, say: Progress = _quiet) -> Optional[str]:
    """Pull *image* unless the engine already has it (the pin never moves)."""
    if _run([engine, "image", "inspect", image], _RUN_TIMEOUT_SEC) is None:
        return None
    say(f"downloading {image} (first start only, about 200 MB)")
    return _run([engine, "pull", image], _PULL_TIMEOUT_SEC)


def _wait_healthy(url: str) -> bool:
    deadline = time.monotonic() + _START_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if healthy(url, fresh=True):
            return True
        time.sleep(1.0)
    return False


def _record(url: str, engine: Optional[str]) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps({"url": url, "engine": engine}) + "\n")


def down() -> Dict[str, Any]:
    """Remove the container and forget its URL; the settings file stays."""
    engine = container_engine()
    error = _run([engine, "rm", "-f", CONTAINER_NAME], _RUN_TIMEOUT_SEC) if engine else None
    _state_file().unlink(missing_ok=True)
    _PROBE_CACHE.clear()
    if error:
        return {"state": "failed", "reason": f"{engine} rm: {error}"}
    return {"state": "stopped"}


def status() -> Dict[str, Any]:
    url = recorded_url()
    if url is None:
        return {"state": "absent"}
    return {"state": "running" if healthy(url, fresh=True) else "down", "url": url}
