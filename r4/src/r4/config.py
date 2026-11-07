"""R4 SDK configuration."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ._atomic import atomic_write_text


class DeployMode(Enum):
    """Deployment mode determining default transport selection.

    - CLOUD: Multi-tenant deployment, all traffic through Gateway (HTTP).
    - HPC_LOCAL: Single-tenant HPC, direct ZMQ to UMRS for most operations.
    - HYBRID: Gateway by default, ZMQ for data-plane operations.
    """

    CLOUD = "cloud"
    HPC_LOCAL = "hpc-local"
    HYBRID = "hybrid"


class TransportMode(Enum):
    """Explicit transport override.

    - GATEWAY: Force all traffic through HTTP Gateway.
    - DIRECT: Force direct ZMQ to UMRS (requires UMRS reachability).
    - AUTO: Let DeployMode decide per-operation.
    """

    GATEWAY = "gateway"
    DIRECT = "direct"
    AUTO = "auto"


# Operations that can be routed to either transport
OPERATION_ZMQ_ELIGIBLE = {
    "status",
    "model_list",
    "model_register",
    "model_download",
    "job_list",
    "job_submit",
    "job_cancel",
    "workflow_list",
    "workflow_run",
    "workflow_upload",
    "export_embeddings",
    "file_upload",
    "file_download",
}

# Operations that should ALWAYS use Gateway (policy-sensitive)
OPERATION_GATEWAY_ONLY = {
    "chat",
    "chat_completion",
    "mcp_list_tools",
    "mcp_call_tool",
    "mcp_register_server",
}


DEFAULT_PROFILE = "default"


def _resolve_config_dir() -> Path:
    """Resolve the XDG-compliant R4 config directory."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "r4"
    return Path.home() / ".config" / "r4"


def _as_optional_str(value: object) -> Optional[str]:
    """Coerce a config value to an optional non-empty string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_bool(value: object, default: bool) -> bool:
    """Parse a bool-like config value with a default."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _as_str_dict(value: object) -> dict[str, str]:
    """Coerce a config value to a string-to-string dict."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key).strip()
        item_text = str(item).strip()
        if key_text and item_text:
            result[key_text] = item_text
    return result


def _load_current_project_defaults() -> dict[str, object]:
    """Load defaults from the currently selected project, if any."""
    try:
        from .project import get_project_manager

        current_project = get_project_manager().get_current_config()
    except Exception:
        return {}

    if current_project is None:
        return {}

    return {
        "project_id": current_project.project_id,
        "gateway_url": current_project.gateway_url,
        "model": current_project.model,
    }


def _find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from *start* (default: cwd) looking for a ``.r4/`` directory.

    Returns the directory that **contains** ``.r4/``, or ``None``.
    """
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".r4").is_dir():
            return parent
    return None


def _load_toml_file(path: Path) -> dict[str, Any]:
    """Read a TOML file, returning ``{}`` on any error."""
    if not path.is_file():
        return {}
    try:
        # Python 3.11+
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {}  # No TOML reader available — skip gracefully
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _load_repo_config() -> dict[str, Any]:
    """Load the repo-level ``.r4/config.toml`` (shared, committed)."""
    root = _find_repo_root()
    if root is None:
        return {}
    return _load_toml_file(root / ".r4" / "config.toml")


def _load_local_config() -> dict[str, Any]:
    """Load the per-developer ``.r4/local.toml`` (git-ignored)."""
    root = _find_repo_root()
    if root is None:
        return {}
    return _load_toml_file(root / ".r4" / "local.toml")


def _normalize_gateway_url(url: str) -> str:
    """Ensure gateway URL has an http(s):// scheme prefix.

    For non-localhost URLs, defaults to ``https://`` since production
    deployments use TLS.  Localhost URLs default to ``http://``.
    """
    url = url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        # Localhost / 127.0.0.1 → http, everything else → https
        host = url.split("/")[0].split(":")[0]
        if host in ("localhost", "127.0.0.1", "::1"):
            url = f"http://{url}"
        else:
            url = f"https://{url}"
    return url


# The placeholder umrs mint flows print into their login hints (umrs
# ``cli/admin/main.py`` ``_GATEWAY_URL_EXAMPLE``), preceded by "replace ...
# with the site's public gateway URL". It is never a real gateway.
GATEWAY_URL_EXAMPLE = "https://HOST/SITE"


def gateway_url_arg(value: str) -> str:
    """argparse ``type`` for ``--gateway-url``: refuse the umrs hint example.

    Pasted straight out of a ``user add`` / ``token generate-jwt`` hint, the
    example string looks like a URL and used to be saved verbatim; every
    later failure then surfaced far from the mistake (2026-11-04 incident).
    umrs's own parsers refuse it (``_gateway_url_arg``); the r4 parsers now
    share this guard. The value itself is returned unchanged — scheme
    normalization stays with :func:`_normalize_gateway_url` at use time.
    """
    if _normalize_gateway_url(value) == GATEWAY_URL_EXAMPLE:
        raise argparse.ArgumentTypeError(
            f"{GATEWAY_URL_EXAMPLE} is the example in the umrs login hint; "
            "replace it with the site's public gateway URL"
        )
    return value


class _FieldSpec:
    """One config attribute's resolution contract for table-driven from_env.

    Precedence: env var > local.toml > repo.toml > stored config.json >
    project defaults > default. `coerce` maps the resolved raw value to the
    final attribute value (str normalization applies before coercion when
    raw is None: `default` is used, coerced).
    """

    __slots__ = ("attr", "env_key", "stored_key", "toml_key", "project_key", "default", "coerce")

    def __init__(
        self,
        attr: Any,
        env_key: Any = None,
        stored_key: Any = None,
        toml_key: Any = None,
        project_key: Any = None,
        default: Any = None,
        coerce: Any = None,
    ) -> None:
        self.attr = attr
        self.env_key = env_key
        self.stored_key = stored_key
        self.toml_key = toml_key
        self.project_key = project_key
        self.default = default
        self.coerce = coerce


def _first_non_empty(*values):
    """First value that is neither None nor an empty/whitespace-only string."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _layer_lookup(layers: list[dict], key: Optional[str]):
    """First non-empty value for key across ordered config layers."""
    if key is None:
        return None
    for layer in layers:
        value = layer.get(key)
        if value is not None and value != "":
            return value
    return None


def _env_bool(key: str, default: bool) -> bool:
    """Parse a boolean env var with fallback."""
    raw = os.getenv(key, "").lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes")


def _resolve_enum(stored: dict[str, object], env_key: str, stored_key: str, mapping: dict, default):
    """env > stored > default for small string→enum maps."""
    raw = (os.getenv(env_key) or _as_optional_str(stored.get(stored_key)) or "").lower()
    return mapping.get(raw, default)


def _resolve_model(
    stored: dict[str, object],
    project_defaults: dict[str, object],
    layers: list[dict] | None = None,
) -> str:
    """Model with the endpoint-configured stored-model special case.

    When a public endpoint is configured in the store, the stored model is
    the explicit choice for that endpoint and beats project TOML defaults.
    """
    if stored.get("endpoint_url") or stored.get("endpoint_provider"):
        explicit = _first_non_empty(
            os.getenv("R4_MODEL"),
            _layer_lookup(layers or [], "model"),
            stored.get("model"),
        )
        if explicit:
            return explicit
    return _first_non_empty(
        os.getenv("R4_MODEL"),
        _layer_lookup(layers or [], "model"),
        project_defaults.get("model"),
        stored.get("model"),
        "rivault-gpt-oss-static",
    )


def _resolve_endpoint_api_key(stored: dict[str, object]) -> Optional[str]:
    """Endpoint-only key with the pre-split backward-compat fallback."""
    explicit = os.getenv("R4_ENDPOINT_API_KEY") or _as_optional_str(stored.get("endpoint_api_key"))
    if explicit:
        return explicit
    if (
        stored.get("endpoint_url")
        and not stored.get("endpoint_provider")
        and not stored.get("endpoint_api_key")
    ):
        return stored.get("api_key")
    return None


def _resolve_verify_ssl(stored: dict[str, object], layers: list[dict] | None = None) -> bool:
    """verify_ssl: env bool > TOML bool > stored bool > True."""
    raw = _first_non_empty(
        os.getenv("R4_VERIFY_SSL"),
        _layer_lookup(layers or [], "verify_ssl"),
        stored.get("verify_ssl"),
        True,
    )
    return _parse_bool(raw, True)


def _resolve_aws_region(
    stored: dict[str, object], layers: list[dict] | None = None
) -> Optional[str]:
    """AWS region: AWS_REGION > AWS_DEFAULT_REGION > TOML > stored."""
    return _first_non_empty(
        os.getenv("AWS_REGION"),
        os.getenv("AWS_DEFAULT_REGION"),
        _layer_lookup(layers or [], "aws_region"),
        stored.get("aws_region"),
    )


def _apply_field_spec(spec: "_FieldSpec", raw):
    """Coerce or fall back to the field default."""
    if raw is None:
        return spec.coerce(spec.default) if spec.coerce else spec.default
    return spec.coerce(raw) if spec.coerce else raw


#: ADR-076 site decision model, on by default (mirrors
#: r4.decisions.settings.DEFAULT_DECISION_MODEL; a test pins the two equal —
#: importing it here would cycle through r4.decisions → r4.client).
DEFAULT_DECISION_MODEL = "Bespoke-Nimble-9B"


def _decision_model_value(raw: object) -> str:
    from .decisions.settings import normalize_decision_model

    return normalize_decision_model(raw)


def _ask_user_mode_value(raw: object) -> str:
    from .companion.agent_settings import normalize_ask_user_mode

    return normalize_ask_user_mode(raw)


def _stored_web_access(stored: dict[str, object]) -> dict[str, str]:
    """ADR-099 D-2: the profile's last web-access choice (``""`` = never
    chosen) and its domains as one comma-separated string (stored as a list)."""
    domains = stored.get("web_access_domains_last")
    if isinstance(domains, (list, tuple)):
        domains = ",".join(str(item) for item in domains if str(item).strip())
    return {
        "web_access_last": str(stored.get("web_access_last") or ""),
        "web_access_domains_last": str(domains or ""),
    }


_FIELD_SPECS: tuple[_FieldSpec, ...] = (
    _FieldSpec(
        "gateway_url",
        env_key="R4_GATEWAY_URL",
        stored_key="gateway_url",
        project_key="gateway_url",
        default="http://localhost:8080",
        coerce=_normalize_gateway_url,
    ),
    _FieldSpec("api_key", env_key="R4_API_KEY", stored_key="api_key"),
    _FieldSpec(
        "project_id", env_key="R4_PROJECT_ID", stored_key="project_id", project_key="project_id"
    ),
    _FieldSpec("timeout", env_key="R4_TIMEOUT", default=300, coerce=int),
    _FieldSpec("max_retries", env_key="R4_MAX_RETRIES", default=3, coerce=int),
    _FieldSpec("proxy", env_key="R4_PROXY"),
    _FieldSpec("endpoint_url", env_key="R4_ENDPOINT_URL", stored_key="endpoint_url"),
    _FieldSpec("endpoint_provider", env_key="R4_ENDPOINT_PROVIDER", stored_key="endpoint_provider"),
    _FieldSpec("aws_profile", env_key="AWS_PROFILE", stored_key="aws_profile"),
    _FieldSpec("umrs_username", env_key="UMRS_USERNAME", stored_key="umrs_username"),
    _FieldSpec(
        "umrs_server_host",
        env_key="UMRS_SERVER_HOST",
        stored_key="umrs_server_host",
        default="127.0.0.1",
    ),
    _FieldSpec(
        "umrs_control_port",
        env_key="UMRS_CONTROL_PORT",
        stored_key="umrs_control_port",
        default=5555,
        coerce=int,
    ),
    _FieldSpec("slurm_job_id", env_key="SLURM_JOB_ID"),
    # ADR-076: catalog name of the Jev-style decision model. On by default
    # for every user since 2026-10-03 (ADR-076 rollout); it is used only where
    # the platform lists it (r4.decisions.settings.decision_model_available).
    # The off-words "off"/"none"/"false"/"0"/"disabled" — what
    # `/decision-model off` stores — turn it off.
    _FieldSpec(
        "decision_model",
        env_key="R4_DECISION_MODEL",
        stored_key="decision_model",
        project_key="decision_model",
        default=DEFAULT_DECISION_MODEL,
        coerce=_decision_model_value,
    ),
    # ADR-099 D-8: the user's ask_user mode, local and remote ("ask" shows
    # the question; "auto" lets the agent pick and say so).
    _FieldSpec(
        "ask_user_mode",
        env_key="R4_ASK_USER_MODE",
        stored_key="ask_user_mode",
        default="ask",
        coerce=_ask_user_mode_value,
    ),
)


class ProfileConfigStore:
    """Persist R4 profile settings in config.json.

    Not only non-secret ones, as this said before 2026-10-21: a profile's
    ``api_key`` lives here too (``auth.APIKeyManager`` writes the same file),
    so config.json is a credential store — written 0600 from the first byte
    and atomically. Kept here rather than moved to a keyring: headless HPC
    login nodes have no keyring backend, and every reader (r4, the bridge,
    the umrs sync) already resolves the key from this file.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        self._config_dir = config_dir or _resolve_config_dir()
        self._config_file = self._config_dir / "config.json"

    def load(self, profile: str = DEFAULT_PROFILE) -> dict[str, object]:
        """Load the stored settings for a profile."""
        config = self._load_all()
        profiles = config.get("profiles", {})
        return dict(profiles.get(profile, {}))

    def list_profiles(self) -> dict[str, dict[str, object]]:
        """Return all stored profile settings keyed by profile name."""
        config = self._load_all()
        profiles = config.get("profiles", {})
        if not isinstance(profiles, dict):
            return {}
        return {
            str(name): dict(values)
            for name, values in sorted(profiles.items())
            if isinstance(values, dict)
        }

    def save(self, profile: str = DEFAULT_PROFILE, **values: object) -> None:
        """Update the stored settings for a profile."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        config = self._load_all()
        profiles = config.setdefault("profiles", {})
        profile_data = dict(profiles.get(profile, {}))

        for key, value in values.items():
            if value is None:
                profile_data.pop(key, None)
            else:
                if key == "gateway_url" and isinstance(value, str):
                    value = _normalize_gateway_url(value)
                profile_data[key] = value

        profiles[profile] = profile_data
        # A profile can carry its api_key: 0600 from the first byte, and
        # atomic. write_text + chmod left the key -rw-r--r-- until the chmod,
        # and an interrupted write left half a document that _load_all reads
        # as {} — every stored profile silently gone (c2-cli, 2026-10-21).
        atomic_write_text(self._config_file, json.dumps(config, indent=2), mode=0o600)

    def _load_all(self) -> dict[str, object]:
        """Load the full persisted config document."""
        if not self._config_file.exists():
            return {}
        try:
            return json.loads(self._config_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}


@dataclass
class Config:
    """
    R4 SDK configuration.

    Configuration is loaded from environment variables:
    - R4_GATEWAY_URL: Platform gateway URL
    - R4_API_KEY: API key for authentication
    - R4_PROJECT_ID: Default project ID
    - R4_MODEL: Default model name
    - R4_TIMEOUT: Request timeout in seconds

    For HPC environments, also checks:
    - SLURM_JOB_ID: Slurm job context

    UMRS ZeroMQ transport settings:
    - UMRS_SERVER_HOST: UMRS server IP (default: 127.0.0.1)
    - UMRS_CONTROL_PORT: Control channel port (default: 5555)
    - UMRS_TUNNEL_MODE: SSH tunnel mode (default: false)
    - R4_USE_UMRS_TRANSPORT: Use ZeroMQ for file transfers (default: true when available)

    Deployment & transport mode:
    - R4_DEPLOY_MODE: cloud | hpc-local | hybrid (default: hybrid)
    - R4_TRANSPORT_MODE: gateway | direct | auto (default: auto)
    """

    profile: str = DEFAULT_PROFILE
    gateway_url: str = "http://localhost:8080"
    api_key: Optional[str] = None
    project_id: Optional[str] = None
    model: str = "rivault-gpt-oss-static"
    timeout: int = 300  # 5 minutes
    max_retries: int = 3

    # HPC-specific
    slurm_job_id: Optional[str] = None

    # Advanced
    verify_ssl: bool = True
    proxy: Optional[str] = None

    # Public / external endpoint (bypasses gateway /gw/llm routing)
    endpoint_url: Optional[str] = None
    # Provider type for public endpoints: None (OpenAI-compat), "bedrock", etc.
    endpoint_provider: Optional[str] = None
    # API key for the public endpoint only (never sent to the gateway)
    endpoint_api_key: Optional[str] = None
    # AWS region for Bedrock provider
    aws_region: Optional[str] = None
    # AWS profile name for Bedrock provider
    aws_profile: Optional[str] = None
    # Per-model route hints. Values are currently "platform" or "public".
    model_routes: dict[str, str] = field(default_factory=dict)
    # Decision model (ADR-076): typed choice/score/noul judgments scored from
    # candidate-token logprobs (r4.decisions). Empty = no decision model.
    decision_model: str = DEFAULT_DECISION_MODEL
    # ADR-099 D-8: ask_user mode, local and remote: "ask" (default) | "auto".
    ask_user_mode: str = "ask"
    # ADR-099 D-2: the last web-access choice of this profile (none |
    # allowlist | public; "" = never chosen), preselected in the next
    # session-open question, and its allowlist domains (comma-separated).
    web_access_last: str = ""
    web_access_domains_last: str = ""
    # Per-source isolation (onboarding plan 2026-10-15, S1): the registered
    # source this config was built for when that source is NOT the profile's
    # own deployment (r4.source_context.config_for_source). Set, api_key is
    # that source's own credential or None, and nothing falls back to the
    # profile's stored login (HTTPCore, the ZMQ session). None = the
    # profile's own deployment (the normal single-source case).
    bound_source: Optional[str] = None
    # The deployment the profile's own login (or R4_API_KEY) belongs to, as
    # Config.from_env resolved it (_login_home); None = unknown. A source on
    # this deployment gets the profile's login back even while the process
    # targets another source (r4.source_context.config_for_source).
    login_home: Optional[str] = None

    # UMRS ZeroMQ transport
    use_umrs_transport: bool = True  # Use ZeroMQ when available
    umrs_server_host: str = "127.0.0.1"
    umrs_control_port: int = 5555
    umrs_tunnel_mode: bool = False
    umrs_username: Optional[str] = None  # UMRS username for bucket access

    # Deployment & transport mode
    deploy_mode: DeployMode = DeployMode.HYBRID
    transport_mode: TransportMode = TransportMode.AUTO

    def should_use_zmq(self, operation: str) -> bool:
        """Decide whether a given operation should use ZMQ transport.

        Args:
            operation: Logical operation name (e.g. "job_list", "model_register").

        Returns:
            True if ZMQ should be attempted for this operation.
        """
        if not self.use_umrs_transport:
            return False

        # Explicit override takes precedence
        if self.transport_mode == TransportMode.GATEWAY:
            return False
        if self.transport_mode == TransportMode.DIRECT:
            return operation in OPERATION_ZMQ_ELIGIBLE

        # AUTO mode: decide based on deploy_mode
        if operation in OPERATION_GATEWAY_ONLY:
            return False

        if self.deploy_mode == DeployMode.CLOUD:
            # Cloud: ZMQ for file transfers and embedding export
            return operation in ("file_upload", "file_download", "export_embeddings")
        elif self.deploy_mode == DeployMode.HPC_LOCAL:
            # HPC-local: use ZMQ for all eligible operations
            return operation in OPERATION_ZMQ_ELIGIBLE
        elif self.deploy_mode == DeployMode.HYBRID:
            # Hybrid: ZMQ for data-plane operations
            return operation in (
                "file_upload",
                "file_download",
                "export_embeddings",
                "status",
            )

        return False

    @classmethod
    def from_env(cls, profile: Optional[str] = None) -> "Config":
        """Create configuration from environment variables.

        Precedence (highest to lowest):
        1. Environment variables (``R4_*``, ``UMRS_*``, ``SLURM_*``)
        2. ``.r4/local.toml`` — per-developer, git-ignored
        3. ``.r4/config.toml`` — repo-level, committed
        4. ``~/.config/r4/config.json`` profile store
        5. Active project defaults
        6. Built-in defaults

        Table-driven (round-6 task 2): plain fields resolve through
        _FIELD_SPECS; special-cased fields resolve through named helpers
        (model endpoint special-case, endpoint_api_key back-compat, deploy/
        transport enums, verify_ssl, transport/tunnel bools, model_routes).

        Then (onboarding plan S1, 2026-10-15) ``login_home`` records where
        the profile's login belongs (:func:`_login_home`), and the active
        source is overlaid — when ``R4_SOURCE`` names one, or no
        ``R4_GATEWAY_URL`` is set. With ``R4_GATEWAY_URL`` alone, a login
        recorded for another deployment stays home
        (:func:`_apply_env_gateway_binding`).
        """
        from dataclasses import replace as _dc_replace

        config, stored = cls._profile_config(profile)
        config = _dc_replace(config, login_home=_login_home(config, stored))
        if os.getenv("R4_SOURCE") or not os.getenv("R4_GATEWAY_URL"):
            return _apply_active_source_overlay(config, stored)
        return _apply_env_gateway_binding(config, stored)

    @classmethod
    def _profile_config(cls, profile: Optional[str] = None) -> tuple["Config", dict[str, object]]:
        """The profile's own config (env, TOML, config.json, project defaults)
        with no source applied, and the profile's stored settings."""
        profile_name = profile or os.getenv("R4_PROFILE") or DEFAULT_PROFILE
        stored = ProfileConfigStore().load(profile_name)
        project_defaults = _load_current_project_defaults()
        layers = [_load_local_config(), _load_repo_config()]

        values: dict[str, object] = {"profile": profile_name}
        for spec in _FIELD_SPECS:
            toml_key = spec.toml_key or (
                spec.env_key.lower().removeprefix("r4_") if spec.env_key else None
            )
            raw = _first_non_empty(
                os.getenv(spec.env_key) if spec.env_key else None,
                _layer_lookup(layers, toml_key),
                _layer_lookup([stored], spec.stored_key),
                _layer_lookup([project_defaults], spec.project_key),
            )
            values[spec.attr] = _apply_field_spec(spec, raw)

        values.update(
            model=_resolve_model(stored, project_defaults, layers),
            verify_ssl=_resolve_verify_ssl(stored, layers),
            aws_region=_resolve_aws_region(stored, layers),
            endpoint_api_key=_resolve_endpoint_api_key(stored),
            use_umrs_transport=_env_bool("R4_USE_UMRS_TRANSPORT", True),
            umrs_tunnel_mode=_env_bool("UMRS_TUNNEL_MODE", False),
            deploy_mode=_resolve_enum(
                stored,
                "R4_DEPLOY_MODE",
                "deploy_mode",
                {
                    "cloud": DeployMode.CLOUD,
                    "hpc-local": DeployMode.HPC_LOCAL,
                    "hpc_local": DeployMode.HPC_LOCAL,
                    "hybrid": DeployMode.HYBRID,
                },
                DeployMode.HYBRID,
            ),
            transport_mode=_resolve_enum(
                stored,
                "R4_TRANSPORT_MODE",
                "transport_mode",
                {
                    "gateway": TransportMode.GATEWAY,
                    "direct": TransportMode.DIRECT,
                    "auto": TransportMode.AUTO,
                },
                TransportMode.AUTO,
            ),
            model_routes=_as_str_dict(stored.get("model_routes")),
        )
        values.update(_stored_web_access(stored))

        return cls(**values), stored

    @property
    def is_hpc(self) -> bool:
        """Check if running in HPC environment."""
        return self.slurm_job_id is not None

    # --- ZMQ defaultdetection helpers (round-4 T6) ------------------------

    @property
    def effective_endpoint(self) -> str:
        """Get the effective API endpoint.

        When ``endpoint_url`` is configured (via ``R4_ENDPOINT_URL`` env-var,
        profile, or config file), it is used directly — this lets r4 talk to
        any public OpenAI-compatible API without going through the platform
        gateway.

        When ``endpoint_provider`` is ``"bedrock"``, Bedrock adapter handles
        routing internally and the URL is a placeholder (the actual URL is
        irrelevant because BedrockHTTPAdapter intercepts requests before they
        reach httpx).

        Otherwise falls back to ``{gateway_url}{prefix}/llm`` — ``/gw/llm``
        behind the edge, ``/umrs/llm`` on a direct-UMRS base (2026-10-20,
        plan H-10.2; the probe memoizes per base URL in r4._http).
        """
        if self.endpoint_url:
            url = self.endpoint_url.rstrip("/")
            # Avoid double /v1 — _request() already appends /v1/chat/completions
            if url.endswith("/v1"):
                url = url[:-3]
            return url
        if self.endpoint_provider == "bedrock":
            # Bedrock adapter intercepts requests; URL is not used for HTTP,
            # but a non-empty value prevents other code from treating this as
            # an unconfigured state.
            return "https://bedrock.placeholder.internal"
        from ._http import probe_api_prefix

        return f"{self.gateway_url}{probe_api_prefix(self)}/llm"

    def validate(self) -> list[str]:
        """
        Validate configuration.

        Returns:
            List of validation errors (empty if valid).
        """
        errors = []

        if not self.gateway_url:
            errors.append("gateway_url is required")

        if self.timeout < 1:
            errors.append("Timeout must be positive")

        return errors


# Global config singleton
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration singleton."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def set_config(config: Config):
    """Set the global configuration."""
    global _config
    _config = config


def reload_config(profile: Optional[str] = None) -> Config:
    """Reload the global configuration from env, profile, and active project state."""
    config = Config.from_env(profile=profile)
    set_config(config)
    return config


# --- ZMQ default-detection helpers (round-4 T6) ------------------------

_DEFAULT_UMRS_HOST = "127.0.0.1"
_DEFAULT_UMRS_PORT = 5555


def _umrs_host_is_default_env(config: "Config", stored: dict[str, object]) -> bool:
    """True when the resolved host is the built-in default AND nothing explicit
    drove it: no env var, and the stored config has no such key at all.
    Presence, not value — an explicit stored ``127.0.0.1`` is a real choice
    (tunnel-era deployments hit that as the correct endpoint)."""
    if os.getenv("UMRS_SERVER_HOST"):
        return False
    if stored.get("umrs_server_host") is not None:
        return False
    return getattr(config, "umrs_server_host", _DEFAULT_UMRS_HOST) == _DEFAULT_UMRS_HOST


def _umrs_port_is_default_env(config: "Config", stored: dict[str, object]) -> bool:
    """True when the resolved port is the built-in default AND nothing explicit
    drove it (same presence-not-value rule as the host)."""
    if os.getenv("UMRS_CONTROL_PORT"):
        return False
    if stored.get("umrs_control_port") is not None:
        return False
    return getattr(config, "umrs_control_port", _DEFAULT_UMRS_PORT) == _DEFAULT_UMRS_PORT


def _resolve_active_source_api_key(active_src: object, config: "Config") -> object:
    """Best-effort api_key fill from the active source (or its auth profile).

    Returns None on any failure — auth lookup is opportunistic and must not
    break Config resolution.
    """
    if getattr(active_src, "api_key", None):
        return active_src.api_key
    if config.api_key:
        return None
    try:
        from .auth import get_auth_manager

        mgr = get_auth_manager(active_src.name)
        return mgr.api_key_manager.get_api_key(active_src.name)
    except Exception:
        return None


def _active_source_zmq_overrides(
    active_src: object, config: "Config", stored: dict[str, object]
) -> dict[str, object]:
    """ZMQ data-plane host/port fills from the active source (round-4 T6).

    Fills only when the default (env-unset AND stored-absent — presence,
    not value) holds (round-5 task 1). Empty dict when umrs_tunnel_mode is
    True: 127.0.0.1 is the correct tunnel-local endpoint.
    """
    if config.umrs_tunnel_mode:
        return {}
    overrides: dict[str, object] = {}
    if _umrs_host_is_default_env(config, stored) and getattr(active_src, "zmq_host", None):
        overrides["umrs_server_host"] = active_src.zmq_host
    if _umrs_port_is_default_env(config, stored) and getattr(active_src, "zmq_control_port", None):
        overrides["umrs_control_port"] = active_src.zmq_control_port
    return overrides


# --- Per-source isolation (onboarding plan 2026-10-15, S1) ----------------


#: The gateway a Config has when nothing configures one (not evidence of a login's home).
_BUILTIN_GATEWAY_URL = "http://localhost:8080"


def _jwt_gateway_claim(token: object) -> Optional[str]:
    """The ``gateway_url`` claim of a JWT credential (unverified), else None."""
    if not token:
        return None
    from .auth import decode_jwt_claims

    claim = (decode_jwt_claims(str(token)) or {}).get("gateway_url")
    return str(claim) if claim else None


def _recorded_profile_home(stored: dict[str, object]) -> Optional[str]:
    """Where the login in use records it was made, as the login itself says.

    ``R4_API_KEY`` (this process's key): its ``gateway_url`` claim. The
    stored login: the gateway ``r4 login`` recorded (``login_gateway_url``),
    else the claim of its key. The profile's own ``gateway_url`` is not this
    record: before 2026-10-15 it was often a stale prompt default.
    """
    env_key = os.getenv("R4_API_KEY")
    if env_key:
        return _jwt_gateway_claim(env_key)
    recorded = _first_non_empty(
        stored.get("login_gateway_url"), _jwt_gateway_claim(stored.get("api_key"))
    )
    return str(recorded) if recorded else None


def _registered_platform_deployment(url: object) -> bool:
    """Whether ``url`` is the deployment of a registered platform source."""
    if not url:
        return False
    from .source import get_platform_sources
    from .source_context import same_deployment

    return any(same_deployment(url, src.url) for src in get_platform_sources())


def _stored_active_platform_source() -> bool:
    """Whether ``sources.json`` names an active platform source (``R4_SOURCE`` aside)."""
    from .source import get_source_store

    store = get_source_store()
    name = store._load().get("active")
    src = store.get(name) if name else None
    return src is not None and src.kind == "platform"


def _legacy_home_beside_env_gateway(stored: dict[str, object]) -> Optional[str]:
    """A login that records nothing, under ``R4_GATEWAY_URL`` (A1-A-R6(c)).

    The profile's ``gateway_url`` is that login's home when it is a
    registered platform deployment and ``R4_GATEWAY_URL`` is another
    registered one: r4 then knows the two are different deployments. Not
    when ``R4_GATEWAY_URL`` is unregistered (it may be the login's own
    deployment by another address, an SSH forward), nor when the profile
    names only the built-in ``http://localhost:8080`` (often the old prompt
    default, e.g. next to a site's ``rccs.env``), nor with ``R4_API_KEY``.
    """
    from .source_context import same_deployment

    env_gateway, legacy = os.getenv("R4_GATEWAY_URL"), stored.get("gateway_url")
    if not env_gateway or not legacy or os.getenv("R4_API_KEY"):
        return None
    if same_deployment(legacy, _BUILTIN_GATEWAY_URL) or same_deployment(legacy, env_gateway):
        return None
    known = _registered_platform_deployment(legacy) and _registered_platform_deployment(env_gateway)
    return str(legacy) if known else None


def _login_home(config: "Config", stored: dict[str, object]) -> Optional[str]:
    """The deployment the profile's login (or ``R4_API_KEY``) belongs to; None = unknown.

    In order: what the login records (:func:`_recorded_profile_home`);
    with ``R4_GATEWAY_URL``, the profile's ``gateway_url`` when both are
    registered platform deployments (:func:`_legacy_home_beside_env_gateway`);
    ``R4_GATEWAY_URL`` (the login is used against it); the profile's
    ``gateway_url`` when a registered platform source is that deployment;
    and, when no active source is stored, the gateway the profile uses with
    no source selected (``config.gateway_url`` before any overlay, unless it
    is only the built-in default). A repo TOML ``gateway_url`` never decides
    it while a source is active (onboarding plan S1).
    """
    try:
        recorded = _recorded_profile_home(stored) or _legacy_home_beside_env_gateway(stored)
        if recorded:
            return recorded
        if os.getenv("R4_GATEWAY_URL"):
            return os.getenv("R4_GATEWAY_URL")
        legacy = None if os.getenv("R4_API_KEY") else stored.get("gateway_url")
        if _registered_platform_deployment(legacy):
            return str(legacy)
        if _stored_active_platform_source() or config.gateway_url == _BUILTIN_GATEWAY_URL:
            return None
        return config.gateway_url
    except Exception:  # noqa: BLE001 — Config.from_env never fails on the source system
        return None


def _platform_deployment_count() -> int:
    """How many distinct deployments the registered platform sources name."""
    from .source import get_platform_sources
    from .source_context import same_deployment

    seen: list[str] = []
    for src in get_platform_sources():
        if not any(same_deployment(src.url, url) for url in seen):
            seen.append(src.url)
    return len(seen)


def _binds_active_source(active_src: object, home: Optional[str]) -> bool:
    """Whether the active source is NOT the profile login's deployment.

    Known home: compare. Unknown home: the profile's login is kept only in
    the single-deployment case (one platform deployment registered, as
    before 2026-10-15); with several, nothing says the login is this one's,
    so none of it moves (fail closed, onboarding plan S1).
    """
    from .source_context import same_deployment

    if home is not None:
        return not same_deployment(home, active_src.url)
    return _platform_deployment_count() > 1


def _active_source_credential(active_src: object, config: "Config") -> dict[str, object]:
    """api_key (and, for another deployment, identity) overrides for the active source.

    Onboarding plan S1: the profile's login, project and user are kept only
    when they belong to the active source's deployment (``config.login_home``,
    :func:`_binds_active_source`). Otherwise the source is bound
    (:func:`_bound_active_source`).
    """
    if _binds_active_source(active_src, config.login_home):
        return _bound_active_source(active_src)
    if getattr(active_src, "api_key", None):
        return {"api_key": active_src.api_key}
    api_key = _resolve_active_source_api_key(active_src, config)
    return {"api_key": api_key} if api_key else {}


def _bound_active_source(active_src: object) -> dict[str, object]:
    """Overrides for an active source that is not the profile's deployment.

    Nothing of the profile's deployment moves to it: its own credential (or
    none: ``bound_source`` stops HTTPCore and the ZMQ session falling back to
    the profile's login), the project this process names (``R4_PROJECT_ID``)
    or one ``r4 project switch`` recorded for this deployment or its own
    ``project``, the user this process names (``UMRS_USERNAME``) or its own
    token names, and its own ZMQ endpoint unless an env variable names one.
    """
    from .source_context import active_source_project, credential_for, identity_of, zmq_fields

    key = credential_for(active_src)
    return {
        "api_key": key,
        "bound_source": active_src.name,
        "project_id": active_source_project(active_src, None),
        "umrs_username": os.getenv("UMRS_USERNAME") or identity_of(key),
        **zmq_fields(active_src),
    }


def _active_source_defaults(active_src: object, config: "Config") -> dict[str, object]:
    """The active source's own default model and project (Phase 4-C), when set."""
    from .source_context import active_source_project, source_model

    return {
        "model": source_model(active_src, config.model),
        "project_id": active_source_project(active_src, config.project_id),
    }


def _apply_active_source_overlay(config: "Config", stored: dict[str, object]) -> "Config":
    """Overlay the active platform source onto a partially-built Config.

    Fills gateway_url, the ZMQ data-plane endpoint (via
    _active_source_zmq_overrides), the credential (via
    _active_source_credential) and the source's own default model and
    project (via _active_source_defaults). A login, project or user made for
    another deployment never moves to the active source (onboarding plan
    S1). Returns the input config unchanged on any exception (missing source
    registry, non-platform active source, import problems — Config.from_env
    must never fail on the presence-absence of the source system).
    """
    try:
        from .source import resolve_active_source

        active_src = resolve_active_source()
        if active_src is None or active_src.kind != "platform":
            return config

        from dataclasses import replace as _dc_replace

        overrides: dict[str, object] = {"gateway_url": active_src.url}
        overrides.update(_active_source_zmq_overrides(active_src, config, stored))
        overrides.update(_active_source_credential(active_src, config))
        config = _dc_replace(config, **overrides)
        return _dc_replace(config, **_active_source_defaults(active_src, config))
    except Exception:
        return config  # Source system unavailable — use profile-only config


def _registered_platform_source_at(url: object) -> Optional[object]:
    from .source import get_platform_sources
    from .source_context import same_deployment

    return next((src for src in get_platform_sources() if same_deployment(src.url, url)), None)


def _apply_env_gateway_binding(config: "Config", stored: dict[str, object]) -> "Config":
    """``R4_GATEWAY_URL`` (no ``R4_SOURCE``): the gateway is the env's, the login stays home.

    When the profile's login belongs to another deployment
    (``config.login_home``: what the login records, or a legacy profile
    ``gateway_url`` naming another registered platform deployment —
    :func:`_legacy_home_beside_env_gateway`), neither it nor the profile's
    project and user are sent to ``R4_GATEWAY_URL``: the config is bound to
    the registered platform source at that URL (its own credential), or to
    the URL itself. ``R4_API_KEY`` set with it is an explicit pair and is
    kept.
    """
    try:
        from .source_context import credential_for, identity_of, same_deployment

        home = config.login_home
        if os.getenv("R4_API_KEY") or not home:
            return config
        if same_deployment(home, config.gateway_url):
            return config
        src = _registered_platform_source_at(config.gateway_url)
        key = credential_for(src) if src is not None else None
        from dataclasses import replace as _dc_replace

        return _dc_replace(
            config,
            api_key=key,
            bound_source=getattr(src, "name", None) or config.gateway_url,
            project_id=os.getenv("R4_PROJECT_ID") or getattr(src, "project", None),
            umrs_username=os.getenv("UMRS_USERNAME") or identity_of(key),
        )
    except Exception:  # noqa: BLE001 — Config.from_env never fails on the source system
        return config


def home_source_config(source: object, base: "Config") -> "Config":
    """``base`` re-targeted at the profile login's own deployment (``source``).

    Used while the process targets another source (``base`` is bound
    there): the profile's own login (or, with none stored as a key, the
    HTTPCore fallback to its token store), its project and its user go to
    their own deployment, as they do with no source selected. The source's
    own key and project win when it names them.
    """
    from dataclasses import replace as _dc_replace

    from .source_context import source_model, source_project

    own, stored = Config._profile_config(base.profile)
    zmq = _active_source_zmq_overrides(source, own, stored)
    return _dc_replace(
        base,
        gateway_url=source.url,
        endpoint_url=None,
        endpoint_provider=None,
        api_key=source.api_key or own.api_key,
        bound_source=None,
        project_id=source_project(source, own.project_id),
        umrs_username=own.umrs_username,
        model=source_model(source, base.model),
        umrs_server_host=zmq.get("umrs_server_host", own.umrs_server_host),
        umrs_control_port=zmq.get("umrs_control_port", own.umrs_control_port),
        umrs_tunnel_mode=own.umrs_tunnel_mode,
    )
