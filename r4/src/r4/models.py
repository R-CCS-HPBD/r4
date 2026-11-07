"""
Model Registry Client for R4 CLI (A-601 - A-605).

Provides client methods for interacting with the UMRS Model Registry API:
- List models (with filters)
- Get model details
- Stage model download
- Register existing model
- Publish model (promote scope)

See DEVELOPMENT_OBJECTIVES.md Section 4.7 for design details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from .config import Config, get_config
from .errors import APIError, R4ConnectionError, R4Error

# =============================================================================
# Data Models
# =============================================================================


class ModelScope(Enum):
    """Model visibility scope."""

    SYSTEM = "system"
    PROJECT = "project"
    USER = "user"


class ModelType(Enum):
    """Type of ML model (mirrors UMRS ``umrs.model_registry.models.ModelType``).

    ``vision`` is a BentoML vision service (SAM3, HMAE). ``vlm`` is a vLLM
    vision-language chat model, ``reranker`` a pooling-runner scorer and
    ``ocr`` a document OCR model (added 2026-11-02).
    """

    LLM = "llm"
    EMBEDDING = "embedding"
    VISION = "vision"
    RERANKER = "reranker"
    OCR = "ocr"
    VLM = "vlm"


#: Page size for name lookups (``ModelClient.get_model_by_name``).
_NAME_LOOKUP_PAGE = 200

#: Every ``--type`` value the CLI accepts.
MODEL_TYPE_CHOICES: tuple[str, ...] = tuple(t.value for t in ModelType)


class ModelStatus(Enum):
    """Model availability status."""

    STAGING = "staging"
    AVAILABLE = "available"
    DEPRECATED = "deprecated"


@dataclass
class ServingProfile:
    """Serving configuration for vLLM/LiteLLM or BentoML vision."""

    gpu_memory_utilization: float = 0.9
    max_model_len: int = 4096
    tensor_parallel_size: int = 1
    pipeline_parallel_size: int = 1
    quantization: Optional[str] = None
    serving_backend: Optional[str] = None
    service_kind: Optional[str] = None
    launch_profiles: List[str] = field(default_factory=list)
    container_image: Optional[str] = None
    service_port: Optional[int] = None
    readiness_probe: Optional[str] = None
    readiness_path: Optional[str] = None
    invoke_base_path: Optional[str] = None
    vision_adapter: Optional[str] = None
    vision_task: Optional[str] = None
    output_format: Optional[str] = None
    co_located: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "max_model_len": self.max_model_len,
            "tensor_parallel_size": self.tensor_parallel_size,
            "pipeline_parallel_size": self.pipeline_parallel_size,
        }
        if self.quantization:
            d["quantization"] = self.quantization
        optional_fields = {
            "serving_backend": self.serving_backend,
            "service_kind": self.service_kind,
            "launch_profiles": self.launch_profiles,
            "container_image": self.container_image,
            "service_port": self.service_port,
            "readiness_probe": self.readiness_probe,
            "readiness_path": self.readiness_path,
            "invoke_base_path": self.invoke_base_path,
            "vision_adapter": self.vision_adapter,
            "vision_task": self.vision_task,
            "output_format": self.output_format,
            "co_located": self.co_located,
        }
        for key, value in optional_fields.items():
            if value not in (None, "", []):
                d[key] = value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServingProfile":
        return cls(
            gpu_memory_utilization=data.get("gpu_memory_utilization", 0.9),
            max_model_len=data.get("max_model_len", 4096),
            tensor_parallel_size=data.get("tensor_parallel_size", 1),
            pipeline_parallel_size=data.get("pipeline_parallel_size", 1),
            quantization=data.get("quantization"),
            serving_backend=data.get("serving_backend"),
            service_kind=data.get("service_kind"),
            launch_profiles=list(data.get("launch_profiles") or []),
            container_image=data.get("container_image"),
            service_port=data.get("service_port"),
            readiness_probe=data.get("readiness_probe"),
            readiness_path=data.get("readiness_path"),
            invoke_base_path=data.get("invoke_base_path"),
            vision_adapter=data.get("vision_adapter"),
            vision_task=data.get("vision_task"),
            output_format=data.get("output_format"),
            co_located=data.get("co_located"),
        )


@dataclass
class ModelCapabilities:
    """Model capability metadata."""

    supports_tool_calling: bool = False
    supports_vision: bool = False
    supports_structured_output: bool = False
    max_output_tokens: Optional[int] = None
    max_position_embeddings: Optional[int] = None
    model_family: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "supports_tool_calling": self.supports_tool_calling,
            "supports_vision": self.supports_vision,
            "supports_structured_output": self.supports_structured_output,
        }
        if self.max_output_tokens is not None:
            d["max_output_tokens"] = self.max_output_tokens
        if self.max_position_embeddings is not None:
            d["max_position_embeddings"] = self.max_position_embeddings
        if self.model_family is not None:
            d["model_family"] = self.model_family
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelCapabilities":
        if not data:
            return cls()
        return cls(
            supports_tool_calling=data.get("supports_tool_calling", False),
            supports_vision=data.get("supports_vision", False),
            supports_structured_output=data.get("supports_structured_output", False),
            max_output_tokens=data.get("max_output_tokens"),
            max_position_embeddings=data.get("max_position_embeddings"),
            model_family=data.get("model_family"),
        )


@dataclass
class ModelInfo:
    """Model information from the registry."""

    model_id: str
    name: str
    scope: ModelScope
    model_type: ModelType
    storage_path: str
    status: ModelStatus = ModelStatus.AVAILABLE
    source_uri: Optional[str] = None
    huggingface_id: Optional[str] = None
    owner_id: Optional[str] = None
    project_id: Optional[str] = None
    description: str = ""
    tags: List[str] = field(default_factory=list)
    serving_profile: Optional[ServingProfile] = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelInfo":
        """Create ModelInfo from API response."""
        serving = data.get("serving_profile")
        caps = data.get("capabilities")
        return cls(
            model_id=data["model_id"],
            name=data["name"],
            scope=ModelScope(data["scope"]),
            model_type=ModelType(data["model_type"]),
            storage_path=data.get("storage_path") or data.get("storage_ref", ""),
            status=ModelStatus(data.get("status", "available")),
            source_uri=data.get("source_uri"),
            huggingface_id=data.get("huggingface_id"),
            owner_id=data.get("owner_id"),
            project_id=data.get("project_id"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            serving_profile=(ServingProfile.from_dict(serving) if serving else None),
            capabilities=(ModelCapabilities.from_dict(caps) if caps else ModelCapabilities()),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
        )


class DownloadJobStatus(Enum):
    """Status of a download job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DownloadJobResponse:
    """Response from a download job request (A-606)."""

    job_id: str
    status: DownloadJobStatus
    model_name: str
    message: Optional[str] = None
    progress: Optional[float] = None
    target_path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadJobResponse":
        return cls(
            job_id=data["job_id"],
            status=DownloadJobStatus(data.get("status", "pending")),
            model_name=data.get("model_name", ""),
            message=data.get("message"),
            progress=data.get("progress"),
            target_path=data.get("target_path"),
        )


class ProvisionStatus(Enum):
    """Status of endpoint provisioning (A-607, A-608)."""

    PENDING = "pending"
    PROVISIONING = "provisioning"
    READY = "ready"
    FAILED = "failed"
    WARM_REUSE = "warm_reuse"  # System endpoint being reused


class EndpointType(Enum):
    """Type of endpoint being used."""

    SYSTEM = "system"
    PROJECT = "project"
    USER = "user"


@dataclass
class ProvisionResponse:
    """Response from endpoint provisioning request (A-607)."""

    model_name: str
    status: ProvisionStatus
    endpoint_url: Optional[str] = None
    endpoint_type: Optional[EndpointType] = None
    message: Optional[str] = None
    estimated_wait: Optional[int] = None  # seconds

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProvisionResponse":
        endpoint_type = None
        if data.get("endpoint_type"):
            endpoint_type = EndpointType(data["endpoint_type"])

        return cls(
            model_name=data.get("model_name", ""),
            status=ProvisionStatus(data.get("status", "pending")),
            endpoint_url=data.get("endpoint_url"),
            endpoint_type=endpoint_type,
            message=data.get("message"),
            estimated_wait=data.get("estimated_wait"),
        )


class PolicyDeniedError(R4Error):
    """
    Error raised when access is denied due to policy (A-609).

    Provides clear messages to users about why access was denied.
    """

    def __init__(self, reason: str, policy_reason: str = ""):
        self.reason = reason
        self.policy_reason = policy_reason
        message = reason
        if policy_reason:
            message = f"{reason} - {policy_reason}"
        super().__init__(message)


# UMRS endpoint status -> provisioning status (umrs EndpointStatus).
_ENDPOINT_PROVISION_STATUS: Dict[str, ProvisionStatus] = {
    "ready": ProvisionStatus.READY,
    "degraded": ProvisionStatus.READY,
    "starting": ProvisionStatus.PROVISIONING,
    "provisioning": ProvisionStatus.PROVISIONING,
    "pending": ProvisionStatus.PENDING,
    "failed": ProvisionStatus.FAILED,
}
_IN_FLIGHT_STATUS_FILTER = "pending,provisioning,starting,failed"
_PROVISION_RANK = (
    ProvisionStatus.READY,
    ProvisionStatus.PROVISIONING,
    ProvisionStatus.PENDING,
    ProvisionStatus.FAILED,
)


def _raise_policy_denied(response: httpx.Response) -> None:
    """A 403 from the endpoints API is a policy denial (A-609)."""
    if response.status_code != 403:
        return
    try:
        data = response.json()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    reason = data.get("detail") or data.get("message") or "Access denied"
    raise PolicyDeniedError(str(reason), str(data.get("policy_reason") or ""))


def _endpoint_status(endpoint: Dict[str, Any]) -> Optional[ProvisionStatus]:
    return _ENDPOINT_PROVISION_STATUS.get(str(endpoint.get("status") or "").lower())


def _endpoint_type(tier: Any) -> Optional[EndpointType]:
    try:
        return EndpointType(tier) if tier else None
    except ValueError:
        return None


def _first_endpoint(listing: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    endpoints = listing.get("endpoints") or []
    return endpoints[0] if endpoints else None


def _pick_model_endpoint(listing: Dict[str, Any], model_name: str) -> Optional[Dict[str, Any]]:
    """The model's most advanced live endpoint (ready > provisioning > pending > failed)."""
    mine = [
        ep
        for ep in listing.get("endpoints") or []
        if model_name in (ep.get("model"), ep.get("served_model_name"))
        and _endpoint_status(ep) is not None
    ]
    return min(mine, key=lambda ep: _PROVISION_RANK.index(_endpoint_status(ep)), default=None)


def _provision_from_endpoint(
    model_name: str,
    endpoint: Dict[str, Any],
    status: Optional[ProvisionStatus] = None,
) -> ProvisionResponse:
    """ProvisionResponse from a UMRS EndpointResponse record."""
    return ProvisionResponse(
        model_name=model_name,
        status=status or _endpoint_status(endpoint) or ProvisionStatus.PENDING,
        endpoint_url=endpoint.get("litellm_url") or None,
        endpoint_type=_endpoint_type(endpoint.get("tier")),
        message=endpoint.get("error_message") or endpoint.get("detail"),
    )


def _provision_from_umrs(model_name: str, data: Dict[str, Any]) -> ProvisionResponse:
    """ProvisionResponse from UMRS ``POST /endpoints/provision`` (status,
    endpoint, retry_after, error)."""
    try:
        status = ProvisionStatus(str(data.get("status") or "pending").lower())
    except ValueError:
        status = ProvisionStatus.PENDING
    result = _provision_from_endpoint(model_name, data.get("endpoint") or {}, status)
    result.message = data.get("error") or result.message
    result.estimated_wait = data.get("retry_after")
    return result


@dataclass
class StageRequest:
    """Request to stage (download) a model."""

    source: str = "huggingface"
    source_id: str = ""  # HuggingFace repo ID or S3 path
    model_name: str = ""
    scope: ModelScope = ModelScope.USER
    project_id: Optional[str] = None
    model_type: ModelType = ModelType.LLM
    hf_token: Optional[str] = None
    revision: Optional[str] = None
    description: str = ""
    tags: List[str] = field(default_factory=list)
    serving_profile: Optional[ServingProfile] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API request payload."""
        data = {
            "huggingface_id": self.source_id,
            "name": self.model_name or self.source_id.split("/")[-1],
            "scope": self.scope.value,
            "model_type": self.model_type.value,
        }
        if self.project_id:
            data["project_id"] = self.project_id
        if self.hf_token:
            data["hf_token"] = self.hf_token
        if self.revision:
            data["revision"] = self.revision
        if self.description:
            data["description"] = self.description
        if self.tags:
            data["tags"] = self.tags
        if self.serving_profile:
            data["serving_profile"] = self.serving_profile.to_dict()
        return data


@dataclass
class RegisterRequest:
    """Request to register an existing model."""

    name: str
    storage_path: str
    scope: ModelScope = ModelScope.USER
    model_type: ModelType = ModelType.LLM
    project_id: Optional[str] = None
    source_uri: Optional[str] = None
    huggingface_id: Optional[str] = None
    description: str = ""
    tags: List[str] = field(default_factory=list)
    serving_profile: Optional[ServingProfile] = None
    capabilities: Optional[ModelCapabilities] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API request payload."""
        data = {
            "name": self.name,
            "storage_ref": self.storage_path,
            "scope": self.scope.value,
            "model_type": self.model_type.value,
        }
        if self.project_id:
            data["project_id"] = self.project_id
        if self.source_uri:
            data["source_uri"] = self.source_uri
        if self.huggingface_id:
            data["huggingface_id"] = self.huggingface_id
        if self.description:
            data["description"] = self.description
        if self.tags:
            data["tags"] = self.tags
        if self.serving_profile:
            data["serving_profile"] = self.serving_profile.to_dict()
        if self.capabilities:
            data["capabilities"] = self.capabilities.to_dict()
        return data


# =============================================================================
# Model Registry Client
# =============================================================================


class ModelClient:
    """
    Client for UMRS Model Registry API.

    Provides high-level methods for model management operations.
    Supports both HTTP Gateway and direct ZMQ transport.
    """

    def __init__(self, config: Optional[Config] = None):
        """
        Initialize model client.

        Args:
            config: Configuration (uses default if not provided)
        """
        self._config = config or get_config()
        self._umrs_transport = None  # Lazy-init
        self._http_core = None  # Lazy-init
        self._is_umrs_direct: Optional[bool] = None

    @property
    def _http(self):
        """Shared :class:`r4._http.HTTPCore` for this ModelClient."""
        if self._http_core is None:
            from ._http import HTTPCore

            self._http_core = HTTPCore(self._config)
        return self._http_core

    @property
    def _transport(self):
        """Get UMRS transport (lazy initialization)."""
        if self._umrs_transport is None and self._config.use_umrs_transport:
            try:
                from .source_context import transport_session_fields
                from .umrs_transport import UMRSTransport, UMRSTransportConfig

                transport_config = UMRSTransportConfig.for_server(
                    server_host=self._config.umrs_server_host,
                    control_port=self._config.umrs_control_port,
                    tunnel_mode=self._config.umrs_tunnel_mode,
                    **transport_session_fields(self._config),
                )
                self._umrs_transport = UMRSTransport(transport_config)
            except ImportError:
                self._umrs_transport = False
        return self._umrs_transport if self._umrs_transport else None

    def _ensure_transport_connected(self) -> bool:
        """Ensure ZMQ transport is connected."""
        transport = self._transport
        if transport is None:
            return False
        if not transport.is_connected:
            return transport.connect()
        return True

    def _get_username(self) -> str:
        """Get username for UMRS operations."""
        return self._config.umrs_username or self._config.project_id or "default"

    @property
    def _router(self):
        from ._transport_router import TransportRouter

        if getattr(self, "_transport_router", None) is None:
            self._transport_router = TransportRouter(self._config)
        return self._transport_router

    @property
    def _api_prefix(self) -> str:
        """Return '/gw' for Gateway, '/umrs' for direct UMRS."""
        if getattr(self, "_is_umrs_direct", None) is None:
            from ._http import probe_api_prefix

            self._is_umrs_direct = probe_api_prefix(self._config) == "/umrs"
        return "/umrs" if self._is_umrs_direct else "/gw"

    @property
    def _base_url(self) -> str:
        """Get base URL for model registry API."""
        return f"{self._config.gateway_url}{self._api_prefix}/models"

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        from .auth import get_auth_manager

        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        elif not getattr(self._config, "bound_source", None):
            # The profile's login only for the profile's own deployment: a
            # config bound to another source never borrows it (onboarding
            # plan S1, r4.source_context).
            auth_header = get_auth_manager(self._config.profile).get_auth_header()
            if auth_header:
                headers.update(auth_header)
        if self._config.project_id:
            headers["X-Project-ID"] = self._config.project_id
        if self._config.umrs_username:
            headers["X-User-ID"] = self._config.umrs_username
        return headers

    def supports_download_tracking(self, job_id: str) -> bool:
        """Return whether the job ID can be queried through the Gateway jobs API."""
        return bool(job_id)

    @staticmethod
    def _map_download_status(raw_status: Optional[str]) -> DownloadJobStatus:
        status_map = {
            "pending": DownloadJobStatus.PENDING,
            "planned": DownloadJobStatus.PENDING,
            "queued": DownloadJobStatus.PENDING,
            "running": DownloadJobStatus.RUNNING,
            "completed": DownloadJobStatus.COMPLETED,
            "failed": DownloadJobStatus.FAILED,
            "cancelled": DownloadJobStatus.FAILED,
        }
        return status_map.get((raw_status or "").lower(), DownloadJobStatus.PENDING)

    @classmethod
    def _normalize_download_job_response(
        cls,
        data: Dict[str, Any],
    ) -> DownloadJobResponse:
        if "job_type" not in data:
            return DownloadJobResponse.from_dict(data)

        attempts = data.get("attempts", [])
        latest_attempt = attempts[-1] if attempts else {}
        metrics = latest_attempt.get("metrics") or {}
        raw_status = str(data.get("status", "")).lower()
        spec = data.get("spec") or {}

        detail = latest_attempt.get("status_detail") or latest_attempt.get("exit_reason")
        if detail:
            message = detail
        elif raw_status in {"planned", "queued"}:
            message = "Job queued"
        elif raw_status == "running":
            message = "Job running"
        elif raw_status == "completed":
            message = "Job completed"
        elif raw_status == "cancelled":
            message = "Job cancelled"
        elif raw_status == "failed":
            message = "Job failed"
        else:
            message = data.get("message")

        progress = data.get("progress")
        if progress is None:
            if metrics.get("progress") is not None:
                progress = metrics.get("progress")
            elif metrics.get("progress_percent") is not None:
                progress = float(metrics["progress_percent"]) / 100.0

        return DownloadJobResponse(
            job_id=data["job_id"],
            status=cls._map_download_status(raw_status),
            model_name=(
                spec.get("model_name")
                or spec.get("target_name")
                or spec.get("model")
                or spec.get("name")
                or data.get("model_name", "")
            ),
            message=message,
            progress=progress,
            target_path=(
                spec.get("target_path") or spec.get("storage_ref") or data.get("target_path")
            ),
        )

    def _handle_response(self, response: httpx.Response) -> None:
        """Handle HTTP response errors."""
        if response.status_code == 401:
            raise APIError("Unauthorized - check your API key", status_code=401)
        elif response.status_code == 403:
            raise APIError(
                "Forbidden - insufficient permissions",
                status_code=403,
                response_body=response.text,
            )
        elif response.status_code == 404:
            raise APIError(
                "Not found",
                status_code=404,
                response_body=response.text,
            )
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise APIError(
                f"Rate limited - retry after {retry_after}s",
                status_code=429,
            )
        elif response.status_code >= 400:
            raise APIError(
                f"API error: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )

    # -------------------------------------------------------------------------
    # API Methods
    # -------------------------------------------------------------------------

    def list_models(
        self,
        scope: Optional[ModelScope] = None,
        model_type: Optional[ModelType] = None,
        project_id: Optional[str] = None,
        status: Optional[ModelStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ModelInfo]:
        """
        List models visible to the current user (A-601).

        Args:
            scope: Filter by scope (system, project, user)
            model_type: Filter by type (llm, embedding)
            project_id: Filter by project ID
            status: Filter by status
            limit: Max results
            offset: Pagination offset

        Returns:
            List of ModelInfo objects
        """
        params = {"limit": limit, "offset": offset}
        if scope:
            params["scope"] = scope.value
        if model_type:
            params["model_type"] = model_type.value
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status.value

        models = self._router.execute(
            "model_list",
            http_fn=lambda: self._list_models_via_http(params),
            zmq_fn=lambda: self._list_models_via_zmq(params),
        )
        return self._matching_models(models, scope, model_type, status)

    @staticmethod
    def _matching_models(
        models: List[ModelInfo],
        scope: Optional[ModelScope],
        model_type: Optional[ModelType],
        status: Optional[ModelStatus],
    ) -> List[ModelInfo]:
        """Rows that match the requested scope, type and status.

        UMRS's grant-aware listing and the ZMQ MODEL_LIST handler ignore these
        filters, so a picker asking for available LLMs got embedding and
        staging rows too (TUI audit #93); the client enforces them. Project
        filtering stays with the server: grant rows make it ambiguous here.
        """
        return [
            m
            for m in models
            if (scope is None or m.scope == scope)
            and (model_type is None or m.model_type == model_type)
            and (status is None or m.status == status)
        ]

    def _list_models_via_http(self, params: Dict[str, Any]) -> List[ModelInfo]:
        try:
            data = self._http.get(self._base_url, params=params, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to list models: {e}")
        return [ModelInfo.from_dict(item) for item in data.get("models", [])]

    @staticmethod
    def _normalize_zmq_model_info(data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize compact UMRS ZMQ model records to the ModelInfo schema."""
        normalized = dict(data)
        normalized.setdefault("model_id", data.get("name", ""))
        normalized.setdefault("storage_path", data.get("storage_ref", ""))
        normalized.setdefault("status", "available")

        registered_at = data.get("registered_at")
        if registered_at:
            normalized.setdefault("created_at", registered_at)
            normalized.setdefault("updated_at", registered_at)

        return normalized

    def _list_models_via_zmq(self, filters: Dict[str, Any]) -> List[ModelInfo]:
        """List models via ZMQ transport."""
        if not self._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        data = self._transport.request_model_list(self._get_username(), filters)
        if data.get("status") == "error":
            raise R4Error(data.get("message", "Model list failed via ZMQ"))

        return [
            ModelInfo.from_dict(self._normalize_zmq_model_info(model))
            for model in data.get("models", [])
        ]

    def get_model(self, model_id: str) -> ModelInfo:
        """
        Get model details by ID (A-602).

        Args:
            model_id: Model ID or name

        Returns:
            ModelInfo with full details
        """
        try:
            data = self._http.get(f"{self._base_url}/{model_id}", timeout=30)
            return ModelInfo.from_dict(data)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to get model: {e}")

    def get_model_by_name(
        self,
        name: str,
        scope: Optional[ModelScope] = None,
        project_id: Optional[str] = None,
    ) -> Optional[ModelInfo]:
        """
        Find a model by name.

        Args:
            name: Model name to search for
            scope: Scope to search in
            project_id: Project to search in

        Returns:
            ModelInfo if found, None otherwise
        """
        # Page through every result: the first page alone (50) missed models
        # registered after the registry grew past it (2026-11-02).
        offset = 0
        while True:
            page = self.list_models(
                scope=scope, project_id=project_id, limit=_NAME_LOOKUP_PAGE, offset=offset
            )
            match = next((model for model in page if model.name == name), None)
            if match is not None or len(page) < _NAME_LOOKUP_PAGE:
                return match
            offset += _NAME_LOOKUP_PAGE

    def stage_model(self, request: StageRequest) -> str:
        """
        Stage (download) a model (A-603).

        Submits a model download job and returns the job ID.

        Args:
            request: Stage request details

        Returns:
            Job ID for tracking the download
        """
        try:
            data = self._http.post(
                f"{self._base_url}/stage", json_body=request.to_dict(), timeout=30
            )
            return data["job_id"]
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to stage model: {e}")

    def register_model(self, request: RegisterRequest) -> ModelInfo:
        """
        Register an existing model (A-604).

        Args:
            request: Registration request

        Returns:
            ModelInfo for the registered model
        """
        return self._router.execute(
            "model_register",
            http_fn=lambda: self._register_model_via_http(request),
            zmq_fn=lambda: self._register_model_via_zmq(request),
        )

    def _register_model_via_http(self, request: RegisterRequest) -> ModelInfo:
        try:
            data = self._http.post(self._base_url, json_body=request.to_dict(), timeout=30)
            return ModelInfo.from_dict(data)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to register model: {e}")

    def _register_model_via_zmq(self, request: RegisterRequest) -> ModelInfo:
        """Register a model via ZMQ transport."""
        if not self._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        data = self._transport.request_model_register(self._get_username(), request.to_dict())
        if data.get("status") == "error":
            raise R4Error(data.get("message", "Model register failed via ZMQ"))

        return ModelInfo.from_dict(data)

    def publish_model(
        self,
        model_id: str,
        target_project_id: str,
    ) -> ModelInfo:
        """
        Publish (promote) a model to project scope (A-605).

        Args:
            model_id: Model to publish
            target_project_id: Target project ID

        Returns:
            ModelInfo for the published model
        """
        try:
            data = self._http.post(
                f"{self._base_url}/{model_id}/publish",
                json_body={"target_project_id": target_project_id},
                timeout=30,
            )
            return ModelInfo.from_dict(data)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to publish model: {e}")

    def delete_model(self, model_id: str, *, delete_files: bool = False) -> None:
        """
        Delete (deprecate) a model.

        Args:
            model_id: Model to delete
            delete_files: Also remove model files from disk
        """
        try:
            params = {"delete_files": "true"} if delete_files else None
            self._http.delete(f"{self._base_url}/{model_id}", params=params, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to delete model: {e}")

    def update_model_capabilities(
        self, model_id: str, capabilities: ModelCapabilities
    ) -> ModelInfo:
        """
        Update model capabilities.

        Args:
            model_id: Model to update
            capabilities: New capabilities

        Returns:
            Updated ModelInfo
        """
        try:
            data = self._http.patch(
                f"{self._base_url}/{model_id}",
                json_body={
                    "capabilities": {
                        "supports_tool_calling": capabilities.supports_tool_calling,
                        "supports_vision": capabilities.supports_vision,
                        "supports_structured_output": capabilities.supports_structured_output,
                        "max_output_tokens": capabilities.max_output_tokens,
                        "max_position_embeddings": capabilities.max_position_embeddings,
                        "model_family": capabilities.model_family,
                    }
                },
                timeout=30,
            )
            return ModelInfo.from_dict(data)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to update model capabilities: {e}")

    def detect_model_capabilities(self, model_id: str, dry_run: bool = False) -> dict:
        """
        Detect model capabilities on the server side.

        The server reads config files from the model's storage_path
        and optionally updates the model entry.

        Args:
            model_id: Model ID
            dry_run: If True, detect without saving

        Returns:
            Detection result dict with keys: model_id, name, detected,
            updated, message
        """
        try:
            params = {"dry_run": "true"} if dry_run else {}
            data = self._http.post(
                f"{self._base_url}/{model_id}/detect",
                params=params,
                timeout=30,
            )
            return data
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to detect capabilities: {e}")

    def download_model(
        self,
        model_name: str,
        target_path: Optional[str] = None,
    ) -> "DownloadJobResponse":
        """
        Request a model download via UMRS job (A-606).

        Args:
            model_name: Model name to download
            target_path: Optional target directory for download

        Returns:
            DownloadJobResponse with job_id and status
        """
        return self._router.execute(
            "model_download",
            http_fn=lambda: self._download_model_via_http(model_name, target_path),
            zmq_fn=lambda: self._download_model_via_zmq(model_name, target_path),
        )

    def _download_model_via_http(
        self,
        model_name: str,
        target_path: Optional[str],
    ) -> "DownloadJobResponse":
        try:
            payload = {"target_path": target_path} if target_path else None
            data = self._http.post(
                f"{self._base_url}/{model_name}/download",
                json_body=payload,
                timeout=30,
            )
            return DownloadJobResponse.from_dict(data)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to request model download: {e}")

    def _download_model_via_zmq(
        self, model_name: str, target_path: Optional[str]
    ) -> "DownloadJobResponse":
        """Request model download via ZMQ transport."""
        if not self._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        spec = {"model_name": model_name}
        if target_path:
            spec["target_path"] = target_path

        data = self._transport.request_model_download(self._get_username(), spec)
        if data.get("status") == "error":
            raise R4Error(data.get("message", "Model download failed via ZMQ"))

        return DownloadJobResponse.from_dict(data)

    def get_download_status(self, job_id: str) -> "DownloadJobResponse":
        """
        Get status of a download job.

        Args:
            job_id: Job ID returned from download_model

        Returns:
            DownloadJobResponse with current status
        """
        try:
            url = f"{self._config.gateway_url}{self._api_prefix}/jobs/{job_id}"
            data = self._http.get(url, timeout=30)
            return self._normalize_download_job_response(data)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to get download status: {e}")

    def provision_endpoint(
        self,
        model_name: str,
        prefer_system: bool = False,
    ) -> "ProvisionResponse":
        """
        Request endpoint provisioning for a model (A-607).

        Calls ``POST {api}/endpoints/provision`` with the model in the body
        (2026-10-18, cleanup C-2(v): the old ``/models/{name}/provision``
        path has no route in either mode). The server reuses a warm endpoint
        first, including endpoints shared with the caller (ADR-078) such as
        system ones, so ``prefer_system`` is kept for signature compatibility
        only.

        Args:
            model_name: Model name to provision
            prefer_system: Kept for compatibility; warm reuse is server-side

        Returns:
            ProvisionResponse with endpoint URL and status
        """
        del prefer_system  # warm/shared reuse is decided by UMRS
        url = f"{self._config.gateway_url}{self._api_prefix}/endpoints/provision"
        data = self._endpoint_request(
            url,
            "provision endpoint",
            method="POST",
            json={"model": model_name, "prefer_warm": True},
            timeout=60,  # Provisioning can take longer
        )
        return _provision_from_umrs(model_name, data)

    def get_provision_status(self, model_name: str) -> "ProvisionResponse":
        """
        Get current provisioning status for a model.

        Reads ``GET {api}/endpoints/active?model=…`` (ready endpoints of the
        project and those shared with it); when none is ready, the project's
        endpoint list ``GET {api}/endpoints`` shows one still provisioning or
        failed (2026-10-18, cleanup C-2(v)).

        Args:
            model_name: Model name to check

        Returns:
            ProvisionResponse with current status
        """
        base = f"{self._config.gateway_url}{self._api_prefix}/endpoints"
        what = "get provisioning status"
        active = self._endpoint_request(
            f"{base}/active", what, method="GET", params={"model": model_name}, timeout=30
        )
        endpoint = _first_endpoint(active) or self._in_flight_endpoint(base, model_name)
        if endpoint is None:
            return ProvisionResponse(
                model_name=model_name,
                status=ProvisionStatus.PENDING,
                message=f"No endpoint for {model_name}; start one with "
                f"`r4 endpoint up --model {model_name}`",
            )
        return _provision_from_endpoint(model_name, endpoint)

    def _in_flight_endpoint(self, base: str, model_name: str) -> Optional[Dict[str, Any]]:
        """The model's not-ready endpoint from the project's list, if any.

        Ready rows were already answered by ``/active``, so the list asks only
        for in-flight and failed rows (no ghost-ready probing server-side).
        UMRS refuses a list without a project for non-admins (403); that
        means "nothing to show", not a policy denial of the status read.
        """
        try:
            listed = self._endpoint_request(
                base,
                "get provisioning status",
                method="GET",
                params={"status_filter": _IN_FLIGHT_STATUS_FILTER},
                timeout=30,
            )
        except PolicyDeniedError:
            return None
        return _pick_model_endpoint(listed, model_name)

    def _endpoint_request(
        self, url: str, what: str, *, method: str, **kwargs: Any
    ) -> Dict[str, Any]:
        """One endpoints-API call: policy denial (A-609), errors, JSON body."""
        timeout = kwargs.pop("timeout", 30)
        try:
            with httpx.Client(timeout=timeout, verify=self._config.verify_ssl) as client:
                response = client.request(method, url, headers=self._build_headers(), **kwargs)
        except httpx.RequestError as e:
            raise R4Error(f"Failed to {what}: {e}")
        _raise_policy_denied(response)
        self._handle_response(response)
        data = response.json()
        return data if isinstance(data, dict) else {}

    def resolve_path(self, model_id: str) -> str:
        """
        Resolve model ID to storage path.

        Args:
            model_id: Model ID

        Returns:
            Validated storage path
        """
        try:
            data = self._http.get(f"{self._base_url}/{model_id}/path", timeout=30)
            return data["path"]
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to resolve model path: {e}")


# =============================================================================
# Convenience Functions
# =============================================================================


def get_model_client(config: Optional[Config] = None) -> ModelClient:
    """Get a configured model client."""
    return ModelClient(config)
