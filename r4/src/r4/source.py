"""R4 endpoint source registry.

A *source* represents a backend that can serve LLM requests — either a
platform UMRS instance (reached via a gateway URL) or a public provider
(OpenAI-compatible URL, AWS Bedrock, Anthropic, etc.).

Sources are persisted in ``~/.config/r4/sources.json`` and are selectable
at runtime via ``--source <name>`` or ``R4_SOURCE`` env-var.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from ._atomic import atomic_write_text

logger = logging.getLogger(__name__)

SourceKind = Literal["platform", "public"]

#: Source names r4 keeps for itself (onboarding plan 2026-10-15, decision D-8):
#: ``site`` for the site a login belongs to and ``local`` for this machine's
#: stand-alone stack. ``r4 source add`` refuses them, in any letter case.
RESERVED_SOURCE_NAMES = frozenset({"site", "local"})


def is_reserved_source_name(name: object) -> bool:
    """Whether ``name`` is one of :data:`RESERVED_SOURCE_NAMES` (case-insensitive)."""
    return str(name or "").strip().lower() in RESERVED_SOURCE_NAMES


class ReservedSourceNameError(ValueError):
    """A new source row may not take a reserved name (decision D-8)."""

    def __init__(self, name: object) -> None:
        kept = " and ".join(f"'{n}'" for n in sorted(RESERVED_SOURCE_NAMES))
        super().__init__(
            f"'{name}' is a reserved source name (r4 keeps {kept} for itself); choose another name."
        )


@dataclass
class Source:
    """A named endpoint backend."""

    name: str
    kind: SourceKind
    url: str
    # Optional fields
    api_key: str | None = None
    provider: str | None = None  # "bedrock", "anthropic", None (OpenAI-compat)
    aws_region: str | None = None
    aws_profile: str | None = None
    description: str | None = None
    # ZMQ connection details for platform sources (umrs sync)
    zmq_host: str | None = None
    zmq_control_port: int | None = None
    zmq_tunnel_mode: bool | None = None
    # ZMQ CURVE authentication (for direct-connect deployments)
    zmq_auth_type: str | None = None  # "none" | "curve"
    zmq_curve_server_public_key: str | None = None
    zmq_curve_client_public_key: str | None = None
    zmq_curve_client_secret_key_file: str | None = None
    # OIDC configuration (stored per-source for multi-deployment setups)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    # Per-source defaults (onboarding plan 2026-10-15, Phase 4-C): the model
    # r4 uses for this source when a command names none (no --model,
    # model@source or R4_MODEL), and the project sent to it as X-Project-ID
    # (r4.source_context.config_for_source). Files written before these
    # fields existed load unchanged.
    default_model: str | None = None
    project: str | None = None
    # Platform sources only: the site's institutional-provider allowlist as
    # advertised by connect-info at login (ADR-062 amendment 2026-10-15).
    # Server-provided data, never user input; the source LOCALITY derived
    # from it (r4.source_locality) is computed, never stored. None = the
    # site advertised nothing → the packaged default (Rikyu) applies; an
    # advertised [] is honoured as "no institutional providers".
    institutional_hosts: list[str] | None = None
    # Platform sources only: the service-tier role aliases the site serves
    # (ADR-106 D-9), as advertised by connect-info at login: role -> alias
    # ("chat": "svc-chat", …) plus an ordered "fallback" list. Server data,
    # never user input. ``default_model`` above is filled from "chat" when
    # the user has not chosen one.
    default_models: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> Source:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def _resolve_sources_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) / "r4" if xdg else Path.home() / ".config" / "r4"
    return base / "sources.json"


class SourceStore:
    """Persist and retrieve named endpoint sources."""

    def __init__(self, path: Path | None = None):
        self._path = path or _resolve_sources_path()

    def list(self) -> list[Source]:
        data = self._load()
        return [Source.from_dict(s) for s in data.get("sources", [])]

    def get(self, name: str) -> Source | None:
        for src in self.list():
            if src.name == name:
                return src
        return None

    def add(self, source: Source) -> None:
        """Write ``source``, replacing a row of the same name.

        A NEW row may not take a reserved name (decision D-8): every writer —
        ``r4 source add``, the login sync, the companion's ``source_add`` tool
        — is refused here. A row of that name written before 2026-10-15 can
        still be updated or removed.
        """
        data = self._load()
        sources = data.get("sources", [])
        if is_reserved_source_name(source.name) and not any(
            s.get("name") == source.name for s in sources
        ):
            raise ReservedSourceNameError(source.name)
        # Replace existing with same name
        sources = [s for s in sources if s.get("name") != source.name]
        sources.append(source.to_dict())
        data["sources"] = sources
        self._save(data)

    def remove(self, name: str) -> bool:
        data = self._load()
        sources = data.get("sources", [])
        new_sources = [s for s in sources if s.get("name") != name]
        if len(new_sources) == len(sources):
            return False
        data["sources"] = new_sources
        # Clear active if it was the removed source
        if data.get("active") == name:
            data.pop("active", None)
        self._save(data)
        return True

    def get_active(self) -> str | None:
        data = self._load()
        return os.environ.get("R4_SOURCE") or data.get("active")

    def set_active(self, name: str | None) -> None:
        data = self._load()
        if name is None:
            data.pop("active", None)
        else:
            data["active"] = name
        self._save(data)

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        """Write the registry atomically: a temp file (0600 before any byte),
        then a rename — a reader never sees half a file, and a replaced row
        is never partly old and partly new (2026-10-15)."""
        import tempfile

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".sources-", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, indent=2))
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# UMRS connection profile sync
# ---------------------------------------------------------------------------
# When a platform source is added or selected, we mirror the connection
# details into ~/.config/umrs/connections/<name>.json so that umrs
# picks up the same backend without separate manual configuration.
# This uses only stdlib — no umrs imports (respects import boundary).
# ---------------------------------------------------------------------------


def _resolve_umrs_cfg_dir() -> Path:
    """Return the UMRS config directory, matching umrs.shared.config logic."""
    override = os.environ.get("UMRS_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "umrs"
    return Path.home() / ".config" / "umrs"


def _umrs_connections_dir() -> Path:
    return _resolve_umrs_cfg_dir() / "connections"


def _remove_umrs_connection_files(name: str, report: dict[str, bool]) -> None:
    """Delete the UMRS profile JSON and ``<name>.token`` written for a source."""
    connections = _umrs_connections_dir()
    for key, path in (
        ("profile", connections / f"{name}.json"),
        ("token", connections / f"{name}.token"),
    ):
        try:
            if path.exists():
                path.unlink()
                report[key] = True
        except OSError:
            logger.warning("Failed to remove %s — check permissions", path)


def _forget_stored_login(name: str) -> bool:
    """Delete the r4 auth-store login kept under ``name`` — local files only.

    The login of the profile r4 runs under (``R4_PROFILE``, else ``default``)
    is never touched: it is the user's own login, not a source's.
    """
    from .config import DEFAULT_PROFILE

    if not name or name == (os.environ.get("R4_PROFILE") or DEFAULT_PROFILE):
        return False
    from .auth import APIKeyManager, TokenStore

    token_deleted = TokenStore().delete(name)
    key_deleted = APIKeyManager().delete_api_key(name)
    return token_deleted or key_deleted


def _forget_bound_sessions(urls: tuple[str | None, ...]) -> bool:
    """Delete the per-source CURVE session files kept for ``urls`` (local only)."""
    from .config import _normalize_gateway_url
    from .curve_session import forget_bound_session

    deleted = False
    for url in {u for raw in urls if raw for u in (raw, _normalize_gateway_url(raw))}:
        deleted = forget_bound_session(url) or deleted
    return deleted


def forget_replaced_source(name: str, *urls: str | None) -> dict[str, bool]:
    """A replaced or re-pointed source: the old row's stored credentials go with it.

    Deletes the UMRS profile JSON and token written for the old row, the r4
    auth-store login kept under the source's name, and the CURVE session
    files a bound transport kept for ``urls`` (the old and new URL) — local
    files only, no network call. The login of the profile r4 runs under is
    never touched (onboarding plan 2026-10-15, Phase 4-C).
    """
    report: dict[str, bool] = {"profile": False, "token": False, "auth": False}
    _remove_umrs_connection_files(name, report)
    report["auth"] = _forget_stored_login(name)
    report["session"] = _forget_bound_sessions(urls)
    return report


def remove_source_credential_artifacts(name: str, url: str | None = None) -> dict[str, bool]:
    """Delete every local file that may hold credential material for *name*
    (G-2). Sources.json row removal alone left the UMRS profile JSON and the
    ``<name>.token`` file — a plaintext key surviving deletion. Also clears
    the r4 auth-store login kept under the same name and, with ``url``, the
    CURVE session file a bound transport kept for it.

    Local files only (onboarding plan 2026-10-15, D-8): no gateway is
    contacted. Before 2026-10-15 this called ``AuthManager.logout()``, which
    revoked the ACTIVE deployment's CURVE session at ``UMRS_BASE_URL`` with
    the removed source's credential and deleted the active session file.
    The login of the profile r4 runs under is never touched.

    Returns a report of what was removed (``False`` = nothing existed).
    """
    report: dict[str, bool] = {"profile": False, "token": False, "auth": False}
    _remove_umrs_connection_files(name, report)
    try:
        report["auth"] = _forget_stored_login(name)
    except Exception:  # noqa: BLE001 — auth store may not exist; never block removal
        logger.debug("auth-store cleanup for %s skipped", name)
    report["session"] = _forget_bound_sessions((url,))
    return report


def _umrs_current_profile_path() -> Path:
    return _resolve_umrs_cfg_dir() / "current_connection_profile"


def _extract_host_from_url(url: str) -> str:
    """Extract the hostname from a URL like http://host:port."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.hostname or "127.0.0.1"


def sync_umrs_connection_profile(
    source: Source,
    *,
    username: str | None = None,
) -> Path | None:
    """Create/update a UMRS connection profile JSON from a platform source.

    Parameters
    ----------
    source:
        The platform source describing the UMRS server.
    username:
        Optional UMRS username to embed in the connection profile so that
        ``umrs`` picks it up without requiring a separate config.

    Returns the path of the written profile, or None if the source is not
    a platform type.
    """
    if source.kind != "platform":
        return None

    _raw_host = source.zmq_host
    # 0.0.0.0 is a server bind address, never a valid client destination.
    host = (_raw_host if _raw_host and _raw_host != "0.0.0.0" else None) or _extract_host_from_url(
        source.url
    )
    control_port = source.zmq_control_port or 5555
    data_port_start = control_port + 1
    data_port_end = control_port + 8
    tunnel_mode = source.zmq_tunnel_mode if source.zmq_tunnel_mode is not None else False
    mode = "ssh_tunnel" if tunnel_mode else "direct"

    # In tunnel mode, services are accessed through the tunnel on localhost
    service_host = "127.0.0.1" if tunnel_mode else host

    profile_data: dict = {
        "name": source.name,
        "mode": mode,
        "zmq": {
            "host": host,
            "control_port": control_port,
            "data_base_port": data_port_start,
            "data_port_range": {"start": data_port_start, "end": data_port_end},
            "tunnel_mode": tunnel_mode,
            "health_check_timeout_ms": 5000,
        },
        "services": {
            "minio_url": f"http://{service_host}:9000",
            "rag_url": f"http://{service_host}:8081",
        },
    }

    # SSH tunnel configuration — when tunnel_mode is true, generate
    # local_forwards for the ZMQ control and data ports so the client
    # knows which ports to tunnel.
    if tunnel_mode and "ssh" not in profile_data:
        ssh_host = _extract_host_from_url(source.url)
        forwards = [
            {"local_port": control_port, "remote_host": host, "remote_port": control_port},
        ]
        for p in range(data_port_start, data_port_end + 1):
            forwards.append({"local_port": p, "remote_host": host, "remote_port": p})
        # Build the ssh -L command hint
        fwd_args = " ".join(
            f"-L {f['local_port']}:{f['remote_host']}:{f['remote_port']}" for f in forwards
        )
        profile_data["ssh"] = {
            "host": ssh_host,
            "local_forwards": forwards,
            "command_hint": f"ssh {fwd_args} {ssh_host}",
        }

    # Gateway URL — allows umrs HTTP fallback and CURVE session bootstrap
    if source.url:
        profile_data["gateway_url"] = source.url.rstrip("/")

    # Username — propagated from JWT claims during r4 auth login
    if username:
        profile_data["username"] = username

    # ZMQ auth type lives at zmq.auth_type (not nested)
    zmq_auth_type = source.zmq_auth_type or "none"
    if zmq_auth_type != "none":
        profile_data["zmq"]["auth_type"] = zmq_auth_type

    connections_dir = _umrs_connections_dir()
    connections_dir.mkdir(parents=True, exist_ok=True)
    profile_path = connections_dir / f"{source.name}.json"

    # If a profile already exists, preserve SSH and service overrides
    if profile_path.exists():
        try:
            existing = json.loads(profile_path.read_text())
            # Preserve SSH config and services that were manually configured
            if "ssh" in existing:
                profile_data["ssh"] = existing["ssh"]
            if existing.get("zmq", {}).get("tunnel_mode"):
                profile_data["zmq"]["tunnel_mode"] = True
                profile_data["mode"] = "ssh_tunnel"
            for svc_key in ("minio_url", "rag_url"):
                if existing.get("services", {}).get(svc_key):
                    profile_data["services"][svc_key] = existing["services"][svc_key]
            # Preserve health_check_timeout_ms if already set
            existing_timeout = existing.get("zmq", {}).get("health_check_timeout_ms")
            if existing_timeout is not None:
                profile_data["zmq"]["health_check_timeout_ms"] = existing_timeout
            # Preserve gateway_url if not overridden by source
            if not profile_data.get("gateway_url") and existing.get("gateway_url"):
                profile_data["gateway_url"] = existing["gateway_url"]
            # Preserve auth_type if not overridden by source
            existing_auth = existing.get("zmq", {}).get("auth_type")
            if existing_auth and "auth_type" not in profile_data.get("zmq", {}):
                profile_data["zmq"]["auth_type"] = existing_auth
            # Preserve username if not overridden by caller
            if not profile_data.get("username") and existing.get("username"):
                profile_data["username"] = existing["username"]
        except (json.JSONDecodeError, OSError):
            pass  # Overwrite broken profile

    # 0600 from the first byte and atomic, like the registry (c2-cli,
    # 2026-10-21: write_text + chmod left it -rw-r--r-- until the chmod).
    atomic_write_text(profile_path, json.dumps(profile_data, indent=2) + "\n", mode=0o600)

    # Also sync the JWT token file if the source has an api_key
    sync_umrs_token(source)

    return profile_path


def sync_umrs_token(source: Source) -> Path | None:
    """Write (or remove) a UMRS token file for a platform source.

    UMRS stores JWTs as ``<profile_name>.token`` files alongside the
    connection profile JSON.  Returns the path of the written token file,
    or None if the source has no api_key.
    """
    if source.kind != "platform":
        return None
    connections_dir = _umrs_connections_dir()
    connections_dir.mkdir(parents=True, exist_ok=True)
    token_path = connections_dir / f"{source.name}.token"
    if source.api_key:
        # The JWT itself: never readable by others, not even for a moment.
        atomic_write_text(token_path, source.api_key + "\n", mode=0o600)
        return token_path
    # No key — remove stale token if present
    if token_path.exists():
        token_path.unlink()
    return None


def clear_platform_source_api_keys(*, gateway_url: str | None = None) -> int:
    """Remove stored API keys from platform sources, optionally by gateway URL.

    OIDC tokens live in the auth token store. A stale source-level API key must
    not override them when the active source is applied to the runtime config.
    """
    store = get_source_store()
    expected_url = gateway_url.rstrip("/") if gateway_url else None
    cleared = 0
    for source in store.list():
        source_url = source.url.rstrip("/")
        if source.kind != "platform" or not source.api_key:
            continue
        if expected_url and source_url != expected_url:
            continue
        source.api_key = None
        store.add(source)
        sync_umrs_token(source)
        cleared += 1
    return cleared


def set_umrs_active_profile(name: str) -> bool:
    """Set the UMRS active connection profile to *name*.

    Returns True if the corresponding profile JSON exists (and was activated),
    False otherwise (profile pointer still updated for deferred creation).
    """
    if not name or not isinstance(name, str):
        return False

    profile_path = _umrs_connections_dir() / f"{name}.json"
    current_path = _umrs_current_profile_path()
    current_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so a crash mid-write cannot leave an empty pointer file.
    tmp_path = current_path.with_suffix(".tmp")
    tmp_path.write_text(name + "\n", encoding="utf-8")
    tmp_path.replace(current_path)
    return profile_path.exists()


def get_umrs_active_profile_name() -> str | None:
    """Return the name stored in the UMRS current_connection_profile pointer."""
    current_path = _umrs_current_profile_path()
    if not current_path.is_file():
        return None
    value = current_path.read_text(encoding="utf-8").strip()
    return value or None


def get_source_store() -> SourceStore:
    """Return the global source store instance."""
    return SourceStore()


def get_platform_sources() -> list[Source]:
    """Return all registered platform sources (kind='platform')."""
    return [s for s in get_source_store().list() if s.kind == "platform"]


def resolve_active_source(source_name: str | None = None) -> Source | None:
    """Resolve the active source by name, env-var, or stored active.

    Priority: --source flag > R4_SOURCE env var > stored active.
    If R4_GATEWAY_URL is explicitly set in the environment, the stored active
    source is NOT applied (explicit env var wins over implicit stored default).

    Args:
        source_name: Explicit source name (from --source flag). Takes priority.

    Returns:
        The resolved Source, or None if no source is active/configured.
    """
    store = get_source_store()
    name = source_name or os.environ.get("R4_SOURCE")
    if not name:
        # Only use stored active if R4_GATEWAY_URL not explicitly set
        if os.environ.get("R4_GATEWAY_URL"):
            return None
        name = store.get_active()
    if not name:
        return None
    return store.get(name)
