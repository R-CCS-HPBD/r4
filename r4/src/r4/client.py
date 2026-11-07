"""R4 main client.

The primary interface for interacting with the RiVault AI platform.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import dataclasses
import json
import logging
import os
import threading
import time
from typing import Any, Iterator, List, Mapping, Optional, Union, cast

from ._http import HTTPCore, raise_for_status, raise_if_web_login, wrap_non_json
from .chat_types import ChatMessage, ChatResponse, Usage, coerce_chat_message
from .config import Config, get_config
from .cost import SessionCost
from .dashboard import DashboardClient
from .data import DataClient
from .data_catalog import DataCatalogClient
from .errors import APIError, ModelRouteRefusedError, R4Error, RateLimitError
from .jobs import JobClient
from .mcp import MCPClient
from .model_routing import (
    gateway_llm_endpoint,
    has_public_endpoint,
    parse_model_ref,
    public_endpoint,
    resolve_model_route,
)
from .rag import RAGClient
from .stream_assembly import ChatStreamAssembler, RoundGuard, RoundLimits, StreamError
from .vision import VisionClient
from .workflows import WorkflowClient

logger = logging.getLogger(__name__)

# Back-compat alias retained for callers that imported the private helper.
_coerce_chat_message = coerce_chat_message


def _build_routing_headers(
    *, task: Optional[str] = None, agent: Optional[str] = None
) -> Optional[dict[str, str]]:
    """Build MM-4-MA routing headers (X-R4-Task / X-R4-Agent).

    Returns ``None`` when neither hint is set, so callers can pass the
    result straight to the HTTP layer without forcing an empty dict.
    """
    headers: dict[str, str] = {}
    if task:
        headers["X-R4-Task"] = task
    if agent:
        headers["X-R4-Agent"] = agent
    return headers or None


def _merge_tool_call_delta(acc: list[dict[str, Any]], delta: dict[str, Any]) -> None:
    """Merge one OpenAI-style streamed ``tool_calls`` delta into ``acc``.

    Deltas arrive fragmented (index-addressed; id/name once, arguments in
    pieces). Concatenating per index reconstructs the wire-shaped tool call
    the blocking, non-streamed response would have carried.
    """
    index = delta.get("index")
    if not isinstance(index, int) or index < 0:
        index = 0
    while len(acc) <= index:
        acc.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    entry = acc[index]
    if delta.get("id"):
        entry["id"] += str(delta["id"])
    if delta.get("type"):
        entry["type"] = str(delta["type"])
    fn = delta.get("function") or {}
    if fn.get("name"):
        entry["function"]["name"] += str(fn["name"])
    if fn.get("arguments"):
        entry["function"]["arguments"] += str(fn["arguments"])


def _merge_stream_chunk_delta(
    chunk: dict[str, Any],
    tool_calls_acc: list[dict[str, Any]],
    *,
    reasoning_acc: Optional[list] = None,
) -> str:
    """Merge one SSE chunk's delta (tool calls + reasoning); return text."""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    for tc_delta in delta.get("tool_calls") or []:
        _merge_tool_call_delta(tool_calls_acc, tc_delta)
    _collect_reasoning(delta, reasoning_acc)
    return str(delta.get("content") or "")


def _read_sse_into(response: Any, assembler: ChatStreamAssembler, cancel_check: Any) -> None:
    """Feed SSE lines to ``assembler`` until ``[DONE]`` or a round limit."""
    for line in response.iter_lines():
        if cancel_check is not None:
            cancel_check()
        if not assembler.feed_line(line):
            return
        reason = assembler.stop_reason()
        if reason:
            assembler.finish_reason = reason
            return


def _args_chars(tool_calls: list[dict[str, Any]]) -> int:
    return sum(len(str((c.get("function") or {}).get("arguments") or "")) for c in tool_calls)


# A collected stream that ended with neither a finish_reason nor ``[DONE]``:
# the provider stopped sending mid-reply, so the text may be cut
# (stabilization sweep 2026-10-21, chat#11). Read as ``_last_stream_finish``.
STREAM_CUT_FINISH = "stream_cut"


class _RoundWatch:
    """Per-stream state of a collected round: usage, finish reason, and its
    reasoning / tool-call argument sizes against the round limits."""

    def __init__(self, limits: Optional[RoundLimits], reasoning_acc: Optional[list]) -> None:
        self.guard = RoundGuard(limits)
        self.reasoning = reasoning_acc if reasoning_acc is not None else []
        self.usage: Optional[dict[str, Any]] = None
        self.finish: Optional[str] = None

    def absorb(self, chunk: dict[str, Any], tool_calls_acc: list[dict[str, Any]]) -> str:
        """Merge one chunk; return its content text."""
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = usage
        seen, args_before = len(self.reasoning), _args_chars(tool_calls_acc)
        content = _merge_stream_chunk_delta(chunk, tool_calls_acc, reasoning_acc=self.reasoning)
        self.guard.add(
            reasoning=sum(len(part) for part in self.reasoning[seen:]),
            args=_args_chars(tool_calls_acc) - args_before,
            text=len(content),
            called=bool(tool_calls_acc),
        )
        self.finish = _chunk_finish(chunk) or self.finish
        return content

    def stop_reason(self, tool_calls_acc: list[dict[str, Any]]) -> Optional[str]:
        return self.guard.stop_reason(
            lambda: "".join(
                str((c.get("function") or {}).get("arguments") or "") for c in tool_calls_acc
            )
        )


def _absorb_round(
    chunks: Iterator[dict[str, Any]], watch: _RoundWatch, tool_calls_acc: list[dict[str, Any]]
) -> Iterator[str]:
    """Yield a round's content deltas; a round limit ends the stream and
    becomes ``watch.finish``."""
    for chunk in chunks:
        content = watch.absorb(chunk, tool_calls_acc)
        if content:
            yield content
        reason = watch.stop_reason(tool_calls_acc)
        if reason:
            watch.finish = reason
            return


def _chunk_finish(chunk: dict[str, Any]) -> Optional[str]:
    """A stream chunk's ``finish_reason`` (``None`` on all but the last)."""
    choices = chunk.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    return first.get("finish_reason") or None


def _collect_reasoning(delta: dict[str, Any], reasoning_acc: Optional[list]) -> None:
    """Append a delta's reasoning text under either field name (ADR-087)."""
    if reasoning_acc is None:
        return
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning:
        reasoning_acc.append(reasoning)


def _stream_blocking_enabled() -> bool:
    """ADR-087: blocking chat calls go upstream as streams unless R4_STREAM_BLOCKING=0."""
    return os.environ.get("R4_STREAM_BLOCKING", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _json_body(response: Any) -> dict[str, Any]:
    """Parse a non-SSE reply to a streamed request as a full JSON response."""
    raw = response.read()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return wrap_non_json(response, text)
    return data if isinstance(data, dict) else {"_raw": data}


def _model_ids(url: str, data: dict[str, Any]) -> List[str]:
    """Model ids of a ``/v1/models`` reply. A reply that is not a model list
    (plain text the HTTP layer kept as ``_raw``) is an error: read as an
    empty list it printed "No models found" with exit 0 (cli lane C09)."""
    if "_raw" in data and "data" not in data:
        raise APIError(
            f"{url} did not answer with a model list (non-JSON reply)",
            response_body=str(data["_raw"])[:400],
        )
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]


# /v1/models entries (model limits, W5/U3) are re-read at most this often.
_MODEL_ENTRIES_TTL_SEC = 300.0

# Why the data-class gate read a route as public, when the route's URL alone
# does not say so (Client._public_route_reason, 2026-10-16).
_USERINFO_REASON = (
    " Its address carries a user name or password (user:pass@host), which "
    "always counts as public; put the key in the source's API key instead."
)
_OWN_ENDPOINT_REASON = (
    " Its provider chooses its own endpoint (a cloud SDK such as Bedrock's), "
    "so the source URL does not decide where the request goes: it counts as public."
)
_ELSEWHERE_REASON = (
    " The provider serving it dials a public address other than the source URL "
    "(a source changed after r4 started: restart r4)."
)


def denial_status(exc: BaseException) -> int:
    """403 or 404 when ``exc`` is the platform refusing a project row (an
    ``AuthenticationError`` carries the status in ``details``, an
    ``APIError`` on ``status_code``); 0 for anything else (H-1, 2026-10-18)."""
    details = getattr(exc, "details", None)
    status = getattr(exc, "status_code", None) or (
        details.get("status_code") if isinstance(details, dict) else None
    )
    return status if status in (403, 404) else 0


#: The project the ADR-062 data-class gate asks about for the calls made in
#: this context ("" = the console's current project). ADR-108 D-9: a loop's
#: model calls are gated on the LOOP's project; the gate used to ask the
#: console's current project, so a loop on a sensitive project started from
#: another one could reach a public endpoint (2026-11-03).
_GATE_PROJECT: contextvars.ContextVar[str] = contextvars.ContextVar("r4_gate_project", default="")


@contextlib.contextmanager
def gate_project(project_id: str | None) -> Iterator[None]:
    """Gate the model calls made inside on ``project_id``'s data class."""
    token = _GATE_PROJECT.set(str(project_id or "").strip())
    try:
        yield
    finally:
        _GATE_PROJECT.reset(token)


def project_expected_on_platform(project_id: str) -> bool:
    """Whether the platform's 404 for ``project_id`` is a DENIAL rather than
    "no row" (H-1 review fix, 2026-10-18).

    The server answers 404 both for a foreign project and for one it never
    heard of. r4 never creates a server row for ``r4 project create``, the
    companion's ``project_ensure`` or ACP: such a local-only project (a
    local record with no gateway) has no row, and the ADR-062 amendment
    admits it ("no row"). A project recorded against a gateway
    (``r4 config --project``, ``r4 init``, ``--gateway``) or with no local
    record at all (``R4_PROJECT_ID``, a login claim) is expected there; a
    failed lookup counts as expected (fail closed). A 403 is a denial for
    every project: the row exists.
    """
    try:
        from .project import get_project_manager

        record = get_project_manager().get(project_id)
    except Exception:  # noqa: BLE001 - unknown means expected: fail closed
        return True
    return record is None or bool(getattr(record, "gateway_url", None))


_DENIED_ROW_REMEDY = (
    "; the platform denied or does not know project {project} (HTTP {status}), so "
    "its class cannot be read: switch to a project you are a member of "
    "(r4 project switch <project>) or register this one on the platform"
)
_WIDEN_CLASS_REMEDY = (
    "; or ask the project owner to widen the class: "
    "r4 project data-class set {project} non_sensitive"
)

_ENDPOINT_SERVING_OPTIONS = {
    "serving_backend",
    "service_kind",
    "launch_profiles",
    "co_locate_vllm",
    "container_image",
    "service_port",
    "readiness_probe",
    "readiness_path",
    "invoke_base_path",
    "vision_adapter",
    "vision_task",
    "output_format",
}


def _apply_endpoint_serving_options(
    payload: dict[str, Any],
    options: dict[str, Any],
) -> None:
    """Forward optional endpoint serving metadata into a provision payload."""
    unknown = sorted(set(options) - _ENDPOINT_SERVING_OPTIONS)
    if unknown:
        raise TypeError(f"Unexpected endpoint option(s): {', '.join(unknown)}")
    for key, value in options.items():
        if key == "co_locate_vllm":
            if value:
                payload[key] = True
            continue
        if value not in (None, "", []):
            payload[key] = value


def _str_or_empty(value: Any) -> str:
    """Coerce *value* to a string, treating falsy values as empty."""
    return str(value or "")


def _lower_str(value: Any) -> str:
    """Coerce *value* to a lowercased string, treating falsy values as empty."""
    return str(value or "").lower()


def _decode_gateway_readiness(response: Any) -> Optional[dict[str, Any]]:
    """Decode a Gateway readiness response; return None to request /health fallback."""
    if response.status_code == 200:
        return response.json()
    if response.status_code == 503:
        try:
            data = response.json()
        except ValueError:
            return {
                "ready": False,
                "message": "Gateway returned HTTP 503 Service Unavailable",
            }
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, dict):
            return detail
        return {"ready": False, "message": str(detail or "Service not ready")}
    if response.status_code == 404:
        return None
    raise_for_status(response)
    raise R4Error(f"Unexpected health response: {response.status_code}")


# Re-exports kept for public API stability.
__all__ = [
    "ChatMessage",
    "ChatResponse",
    "Usage",
    "Client",
    "R4Client",
]


def _config_with(base: Config, **overrides: Optional[str]) -> Config:
    """``base`` with the given non-empty fields replaced, as a new object.

    ``base`` itself is never modified (onboarding plan S1). Returns ``base``
    unchanged when there is nothing to override.
    """
    changes = {name: value for name, value in overrides.items() if value}
    if not changes:
        return base
    if dataclasses.is_dataclass(base):
        return dataclasses.replace(base, **changes)
    clone = copy.copy(base)
    for name, value in changes.items():
        setattr(clone, name, value)
    return clone


def _provision_failure_detail(e: R4Error) -> str:
    """The server's explanation of a failed provision ("" when none)."""
    body = getattr(e, "response_body", None) or e.details.get("response_body")
    if not body:
        return e.message or ""
    try:
        detail = json.loads(body).get("detail", body)
    except Exception:  # noqa: BLE001 - a non-JSON body is the explanation itself
        return str(body)
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    return str(detail)


class Client:
    """
    Main R4 client.

    Example:
        from r4 import Client

        client = Client()
        response = client.chat("Hello!")
        print(response.content)

        # With conversation history
        messages = [
            ChatMessage.system("You are a helpful assistant."),
            ChatMessage.user("What is Python?"),
        ]
        response = client.chat_completion(messages)

        # Streaming
        for chunk in client.chat("Tell me a story", stream=True):
            print(chunk, end="", flush=True)
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        api_key: Optional[str] = None,
        gateway_url: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        """
        Initialize the client.

        Args:
            config: Full configuration object. If not provided, loaded from env.
            api_key: API key (overrides config).
            gateway_url: Gateway URL (overrides config).
            project_id: Default project ID (overrides config).
        """
        base = config or get_config()

        # Explicit parameters apply to this client's own copy: the config
        # passed in (often the process-wide singleton) is never modified
        # (onboarding plan S1 — a companion tool's per-source Client used to
        # re-point the TUI bridge's global config).
        self._config = _config_with(
            base, api_key=api_key, gateway_url=gateway_url, project_id=project_id
        )

        # Lazy-init HTTP session
        self._session = None
        self._http_core: Optional[HTTPCore] = None
        self._platform_http_core: Optional[HTTPCore] = None
        # ADR-062 (D-37 client seam): {project_id: (monotonic_ts, data_class,
        # denial status: 403/404 when the row was denied, else 0)}
        self._data_class_cache: dict[str, tuple[float, str, int]] = {}

        # Shared UMRS ZMQ transport (lazy-init, used by sub-clients)
        self._umrs_transport = None
        # D-52: the companion session driving this client (bound per turn by
        # CompanionSession); sent as x-r4-session-id on loop calls so UMRS
        # can join the loop trace with the session that started it.
        self.companion_session_id: Optional[str] = None

        # Sub-clients
        self._data: Optional[DataClient] = None
        self._rag: Optional[RAGClient] = None
        self._workflows: Optional[WorkflowClient] = None
        self._mcp: Optional[MCPClient] = None
        self._jobs: Optional[JobClient] = None
        self._vision: Optional[VisionClient] = None
        self._knowledge: Optional["KnowledgeClient"] = None  # noqa: F821
        self._data_catalog: Optional[DataCatalogClient] = None
        self._artifacts: Optional["ArtifactClient"] = None  # noqa: F821

        # Cost tracking
        self._session_cost: Optional["SessionCost"] = None
        # Real usage captured from the last completed SSE stream (OpenAI
        # ``stream_options.include_usage`` final chunk); consumed by the
        # companion agent so streamed rounds record measured tokens instead
        # of estimates. ``None`` when the server sent no usage chunk.
        self._last_stream_usage: Optional[dict[str, Any]] = None

        # UMRS-direct mode detection (lazy; None = not yet probed)
        self._is_umrs_direct: Optional[bool] = None

    @classmethod
    def from_env(cls) -> "Client":
        """Create a client from environment variables.

        This is a convenience factory that reads R4_* environment
        variables (or the active profile) and returns a ready-to-use
        client.  Equivalent to ``Client()`` with no arguments.
        """
        return cls()

    def _stream_stash(self) -> threading.local:
        """This client's per-thread stash of the last stream's measurements.

        ADR-092 D-7: parallel agents share one ``Client``; one attribute
        swapped or lost their measured usage (consume-on-read by the wrong
        agent). Created lazily so clients built with ``__new__`` work too.
        """
        stash = self.__dict__.get("_stream_tls")
        if stash is None:
            stash = self.__dict__.setdefault("_stream_tls", threading.local())
        return stash

    @property
    def _last_stream_usage(self) -> Optional[dict[str, Any]]:
        """Measured usage of this thread's last collected stream, or None."""
        return getattr(self._stream_stash(), "usage", None)

    @_last_stream_usage.setter
    def _last_stream_usage(self, value: Optional[dict[str, Any]]) -> None:
        self._stream_stash().usage = value

    @property
    def _last_stream_finish(self) -> Optional[str]:
        """Finish reason of this thread's last collected stream, or None.

        Per thread for the same reason as ``_last_stream_usage``: the
        companion reads it to pick a length or think-budget nudge.
        """
        return getattr(self._stream_stash(), "finish", None)

    @_last_stream_finish.setter
    def _last_stream_finish(self, value: Optional[str]) -> None:
        self._stream_stash().finish = value

    def honours_response_format(self, model: Optional[str], *, cancellable: bool = True) -> bool:
        """Whether a ``response_format`` would reach ``model``'s endpoint
        (ADR-092 slice 9): the OpenAI-compatible HTTP path posts the payload
        verbatim; a provider object (e.g. Anthropic, Bedrock) forwards a fixed
        set of keys and drops it. ``cancellable``: the call carries a turn
        cancel token, which sends an OpenAI-compatible provider's call over
        the HTTP path too. ``R4_RESPONSE_FORMAT=off`` turns it off."""
        if os.environ.get("R4_RESPONSE_FORMAT", "").strip().lower() in ("off", "0", "false"):
            return False
        route = self._resolve_gated_route(str(model or self._config.model))
        provider = self._provider_for_route(route)
        if provider is None:
            return True
        return cancellable and self._should_bypass_provider_for_cancel(provider, True)

    def preflight_model_route(self, model: Optional[str]) -> None:
        """Resolve ``model`` through the ADR-062 gate without sending anything.

        ADR-092 D-6: a plan preflights every distinct model before any spend,
        so a public route for a sensitive project is refused by name at
        submit time, not mid-run. ``None`` checks the configured default.
        The gate's ``R4Error`` propagates.
        """
        self._resolve_gated_route(str(model or self._config.model))

    @property
    def _api_prefix(self) -> str:
        """Return the API path prefix: '/gw' for Gateway, '/umrs' for direct UMRS."""
        if getattr(self, "_is_umrs_direct", None) is None:
            # Probe: if /ready returns 404 but /health returns ok, it's direct UMRS
            from ._http import probe_api_prefix

            self._is_umrs_direct = probe_api_prefix(self._config) == "/umrs"
        return "/umrs" if self._is_umrs_direct else "/gw"

    @property
    def transport(self):
        """Get shared UMRS ZMQ transport (lazy initialization).

        Returns the UMRSTransport instance or None if unavailable.
        """
        if self._umrs_transport is None and self._config.use_umrs_transport:
            try:
                from .source_context import transport_session_fields
                from .umrs_transport import UMRSTransport, UMRSTransportConfig

                config = UMRSTransportConfig.for_server(
                    server_host=self._config.umrs_server_host,
                    control_port=self._config.umrs_control_port,
                    tunnel_mode=self._config.umrs_tunnel_mode,
                    **transport_session_fields(self._config),
                )
                self._umrs_transport = UMRSTransport(config)
            except ImportError:
                logger.debug("UMRS transport not available (pyzmq not installed)")
                self._umrs_transport = False  # Mark as unavailable
        return self._umrs_transport if self._umrs_transport else None

    def _ensure_transport_connected(self) -> bool:
        """Ensure the shared UMRS transport is connected.

        Returns:
            True if connected, False if transport unavailable.
        """
        transport = self.transport
        if transport is None:
            return False
        if not transport.is_connected:
            return transport.connect()
        return True

    def _get_username(self) -> str:
        """Get username for UMRS operations."""
        return self._config.umrs_username or self._config.project_id or "default"

    @property
    def data(self) -> DataClient:
        """Get the Data client for file upload/download (UMRS ZeroMQ)."""
        if self._data is None:
            self._data = DataClient(self)
        return self._data

    @property
    def workflows(self) -> WorkflowClient:
        """Get the Workflows client for n8n pipeline management."""
        if self._workflows is None:
            self._workflows = WorkflowClient(self)
        return self._workflows

    @property
    def dashboard(self) -> DashboardClient:
        """Get the Dashboard client (UMRS web dashboard overview)."""
        if getattr(self, "_dashboard", None) is None:
            self._dashboard = DashboardClient(self)
        return self._dashboard

    @property
    def jobs(self) -> JobClient:
        """Get the Jobs client for job management."""
        if self._jobs is None:
            self._jobs = JobClient(self)
        return self._jobs

    @property
    def rag(self) -> RAGClient:
        """Get the RAG client for retrieval-augmented generation."""
        if self._rag is None:
            self._rag = RAGClient(self)
        return self._rag

    @property
    def mcp(self) -> MCPClient:
        """Get the MCP client for tool integration."""
        if self._mcp is None:
            self._mcp = MCPClient(self)
        return self._mcp

    @property
    def vision(self) -> VisionClient:
        """Get the Gateway-backed vision inference client."""
        if self._vision is None:
            self._vision = VisionClient(self._config)
        return self._vision

    @property
    def data_catalog(self) -> DataCatalogClient:
        """Get the data catalog client for lab upload metadata search/read."""
        if self._data_catalog is None:
            self._data_catalog = DataCatalogClient(self)
        return self._data_catalog

    @property
    def artifacts(self) -> "ArtifactClient":  # noqa: F821
        """Get the Artifact client for registry queries."""
        if self._artifacts is None:
            from .artifacts import ArtifactClient

            self._artifacts = ArtifactClient(self)
        return self._artifacts

    @property
    def knowledge(self) -> "KnowledgeClient":  # noqa: F821
        """Get the Knowledge client for scientific knowledge modules (Phases 3-12).

        Provides methods for:
        - Ingest artifacts (Phase 3)
        - Extract knowledge (Phase 4)
        - Link knowledge (Phase 5)
        - Verify knowledge (Phase 6)
        - Coverage dashboard (Phase 7)
        - External knowledge (Phase 8)
        - Scientific memory (Phase 9)
        - Readiness gate (Phase 10)
        - Research planner (Phase 11)
        - Autonomous research loop (Phase 12)
        """
        if self._knowledge is None:
            from .knowledge import KnowledgeClient

            self._knowledge = KnowledgeClient(self)
        return self._knowledge

    @property
    def config(self) -> Config:
        """Get the client configuration."""
        return self._config

    @property
    def session_cost(self):
        """Cumulative cost tracker for this client session (lazy)."""
        if self._session_cost is None:
            from .cost import SessionCost

            self._session_cost = SessionCost()
        return self._session_cost

    def _record_cost(self, response: "ChatResponse", route=None) -> None:
        """Record token usage from a chat response."""
        usage = response.usage
        if usage.total_tokens == 0:
            return
        cost_per_1k = 0.0
        if route and route.source_name:
            provider = self.provider_registry.get(route.source_name)
            if provider is not None:
                cost_per_1k = provider.cost_per_1k(response.model)
        self.session_cost.record(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_per_1k=cost_per_1k,
            model=response.model,
        )

    def _record_stream_usage(self, usage: dict[str, Any], model: str, route=None) -> None:
        """Record real token usage captured from a streamed (SSE) response.

        Streaming rounds never build a ``ChatResponse``, so ``_record_cost``
        cannot see them; without this the session-cost tracker stayed empty
        for every streamed turn and the companion's cost/ctx chip never
        rendered. ``usage`` is the OpenAI-shaped dict from the stream's
        final chunk (``stream_options.include_usage``).
        """
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            return
        if prompt_tokens + completion_tokens <= 0:
            return
        cost_per_1k = 0.0
        if route is not None and getattr(route, "source_name", None):
            provider = self.provider_registry.get(route.source_name)
            if provider is not None:
                cost_per_1k = provider.cost_per_1k(model)
        self.session_cost.record(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_per_1k=cost_per_1k,
            model=model,
        )

    @property
    def http(self) -> HTTPCore:
        """Shared HTTP core (lazy-initialised).

        Sub-clients and tests may reuse this instead of instantiating
        ``httpx.Client`` directly. The core handles auth headers, correlation
        ids, and error mapping uniformly.

        When ``endpoint_provider`` is ``"bedrock"``, a
        :class:`BedrockHTTPAdapter` wraps the real HTTPCore so chat
        requests are routed through the Bedrock Converse API while
        gateway calls (endpoint management, etc.) use the normal path.
        """
        if self._http_core is None:
            core = HTTPCore(self._config)
            provider = getattr(self._config, "endpoint_provider", None)
            if provider == "bedrock":
                from .providers.bedrock import BedrockHTTPAdapter

                self._http_core = BedrockHTTPAdapter(
                    region=getattr(self._config, "aws_region", None),
                    default_model=self._config.model,
                    aws_profile=getattr(self._config, "aws_profile", None),
                    api_key=self._config.endpoint_api_key,
                    fallback_http=core,
                )
            else:
                self._http_core = core
        return self._http_core

    @property
    def platform_http(self) -> HTTPCore:
        """HTTP core for platform Gateway requests, never provider-adapted."""
        if self._platform_http_core is None:
            self._platform_http_core = HTTPCore(self._config)
        return self._platform_http_core

    # --- Generic Gateway HTTP helpers (Phase 2k) -----------------------------
    # Tiny conveniences for CLI handlers that hit ``/gw/...`` paths
    # directly. Path may be absolute or relative; relative paths are
    # joined to ``config.gateway_url``.

    def _abs_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        base = (self._config.gateway_url or "").rstrip("/")
        return f"{base}{path if path.startswith('/') else '/' + path}"

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.http.get(self._abs_url(path), **kwargs)

    def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self.http.post(self._abs_url(path), json_body=json, **kwargs)

    def patch(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self.http.patch(self._abs_url(path), json_body=json, **kwargs)

    def put(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self.http.put(self._abs_url(path), json_body=json, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.http.delete(self._abs_url(path), **kwargs)

    def upload_file_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
        purpose: str = "uploads",
        key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Store bytes in project-scoped object storage (POST /gw/files/upload).

        Returns the storage receipt (key, bucket, uri, size, content_type).
        Multipart body — the JSON request helper cannot carry file parts, so
        this mirrors the vision-client upload pattern (httpx direct).
        """
        data: dict[str, Any] = {"purpose": purpose}
        if key:
            data["key"] = key
        return self.post_multipart(
            f"{self._api_prefix}/files/upload",
            filename=filename,
            content=content,
            content_type=content_type,
            data=data,
            timeout=timeout,
        )

    def post_multipart(
        self,
        path: str,
        *,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """POST one file part (``file``) to a gateway path; returns parsed JSON.

        The JSON request helper cannot carry file parts, so this goes to
        httpx directly with the shared auth headers (vision-client pattern).
        """
        import httpx

        url = f"{self._config.gateway_url}{path}"
        merged = self.http.build_headers()
        merged.pop("Content-Type", None)
        merged.update(headers or {})
        try:
            with self.http._client(timeout) as client:  # noqa: SLF001 - shared HTTP config
                response = client.post(
                    url,
                    files={"file": (filename, content, content_type)},
                    data=data or {},
                    params=params or {},
                    headers=merged,
                )
            raise_for_status(response)
            raise_if_web_login(response)
            parsed = response.json() if response.content else {}
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        except httpx.RequestError as exc:
            from ._http import map_httpx_error

            raise map_httpx_error(exc, gateway_url=self._config.gateway_url) from exc

    @property
    def router(self):
        """Shared transport router (lazy-initialised).

        Encapsulates the ZMQ-vs-HTTP decision previously duplicated across
        sub-clients.  See :mod:`r4._transport_router`.
        """
        from ._transport_router import TransportRouter

        if getattr(self, "_router", None) is None:
            self._router = TransportRouter(self._config)
        return self._router

    @property
    def provider_registry(self):
        """Provider registry built from configured sources (lazy)."""
        if getattr(self, "_provider_registry", None) is None:
            from .providers.factory import build_registry

            self._provider_registry = build_registry(self._config)
        return self._provider_registry

    def _provider_for_route(self, route):
        """Look up a Provider for the given ModelRoute, or return None."""
        if route.source_name:
            provider = self.provider_registry.get(route.source_name)
            if provider is not None:
                return provider
        return None

    @staticmethod
    def _should_bypass_provider_for_cancel(provider, turn_cancel) -> bool:
        """Whether to skip the provider transport in favour of the HTTP path.

        The legacy HTTP path supports cancellable requests (``cancel_check`` /
        ``register_abort``); the generic :class:`Provider` transport does not.
        For an OpenAI-compatible provider the two are functionally equivalent
        (same ``/v1/chat/completions`` endpoint and auth), so when a request is
        cancellable we fall through to the HTTP path to keep cancellation
        working. Genuinely non-abortable providers (e.g. Bedrock) keep raising
        via the caller's guard.
        """
        if turn_cancel is None:
            return False
        from .providers.openai_compat import OpenAICompatibleProvider

        return isinstance(provider, OpenAICompatibleProvider)

    @property
    def secondary_llm(self):
        """A cheap/fast LLM for internal tasks (title gen, classification).

        Picks the lowest-cost provider from the registry. Returns None if
        no providers are available.
        """
        if getattr(self, "_secondary_llm", None) is None:
            from .providers.secondary import SecondaryLLM

            provider = self.provider_registry.best(strategy="cost")
            if provider is not None:
                self._secondary_llm = SecondaryLLM(provider)
            else:
                self._secondary_llm = False  # Mark as unavailable
        return self._secondary_llm if self._secondary_llm else None

    @property
    def smart_router(self):
        """SmartRouter that scores and selects providers per-request (lazy)."""
        if getattr(self, "_smart_router", None) is None:
            from .providers.router import SmartRouter

            self._smart_router = SmartRouter(self.provider_registry)
        return self._smart_router

    def chat(
        self,
        message: Optional[str] = None,
        *,
        messages: Optional[List[Any]] = None,
        model: Optional[str] = None,
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        task: Optional[str] = None,
        agent: Optional[str] = None,
        _turn_cancel: Any | None = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> Union[ChatResponse, Iterator[str]]:
        """
        Simple chat interface.

        ``response_format`` (ADR-092 slice 9) is forwarded only on the
        OpenAI-compatible HTTP path (see :meth:`honours_response_format`); it
        is an optimisation, never a contract: the caller validates the reply.

        Args:
            message: User message.
            messages: Optional pre-built message history.
            model: Model to use (defaults to config).
            stream: Stream the response.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            system: System prompt.

        Returns:
            ChatResponse or iterator of chunks if streaming.
        """
        normalized_messages: List[ChatMessage] = []
        if system:
            normalized_messages.append(ChatMessage.system(system))

        if messages is not None:
            normalized_messages.extend(_coerce_chat_message(item) for item in messages)
        elif message is not None:
            normalized_messages.append(ChatMessage.user(message))
        else:
            raise TypeError("chat() requires either message or messages")

        return self.chat_completion(
            normalized_messages,
            model=model,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            task=task,
            agent=agent,
            _turn_cancel=_turn_cancel,
            response_format=response_format,
        )

    def chat_stream(
        self,
        message: Optional[str] = None,
        *,
        messages: Optional[List[Any]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        task: Optional[str] = None,
        agent: Optional[str] = None,
        _turn_cancel: Any | None = None,
    ) -> Iterator[str]:
        """Convenience wrapper for streaming chat responses."""
        return self.chat(
            message,
            messages=messages,
            model=model,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            task=task,
            agent=agent,
            _turn_cancel=_turn_cancel,
        )

    def chat_completion(
        self,
        messages: List[ChatMessage],
        *,
        model: Optional[str] = None,
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        task: Optional[str] = None,
        agent: Optional[str] = None,
        _turn_cancel: Any | None = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> Union[ChatResponse, Iterator[str]]:
        """
        Create a chat completion.

        This is the full OpenAI-compatible chat completions API.

        Args:
            messages: List of chat messages.
            model: Model to use (defaults to config).
            stream: Stream the response.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            tools: Function calling tools.
            tool_choice: Tool choice strategy.

        Returns:
            ChatResponse or iterator of chunks if streaming.
        """
        model = model or self._config.model

        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format

        extra_headers = _build_routing_headers(task=task, agent=agent)

        if stream:
            return self._stream_request(
                payload,
                extra_headers=extra_headers,
                turn_cancel=_turn_cancel,
            )
        else:
            return self._request(
                payload,
                extra_headers=extra_headers,
                turn_cancel=_turn_cancel,
            )

    def _endpoint_auth_headers(self) -> Optional[dict[str, str]]:
        """Build auth header override for public endpoint requests.

        When a public endpoint is configured with ``endpoint_api_key``, the
        key must be sent to the public endpoint — NOT to the gateway.
        Returns the override header dict, or ``None`` when no override needed.
        """
        key = getattr(self._config, "endpoint_api_key", None)
        if key and (self._config.endpoint_url or self._config.endpoint_provider):
            return {"Authorization": f"Bearer {key}"}
        return None

    def _public_auth_headers(self, route) -> Optional[dict[str, str]]:
        """Auth headers for a resolved public route.

        Source-bound routes (``model@source`` or a ``model_routes`` entry
        naming a public source) use that source's own credential
        (:func:`r4.source_context.credential_for`). The legacy profile
        ``endpoint_api_key`` is used for routes that name no source, or for a
        key-less source whose URL is that same endpoint.
        """
        src = self._route_source(route)
        if src is not None:
            from .source_context import credential_for, same_deployment

            key = credential_for(src)
            if key:
                return {"Authorization": f"Bearer {key}"}
            # Onboarding plan S1: a key-less source gets the profile's endpoint
            # key only when it IS that endpoint — never another provider's key.
            if not same_deployment(src.url, getattr(self._config, "endpoint_url", None)):
                return None
        return self._endpoint_auth_headers()

    @staticmethod
    def _route_source(route):
        """The registered source a route names, or None."""
        if not route.source_name:
            return None
        try:
            from .source import get_source_store

            return get_source_store().get(route.source_name)
        except Exception:  # noqa: BLE001 — an unreadable store routes as before
            return None

    def _route_source_http(self, route) -> Optional[HTTPCore]:
        """HTTP core for a platform route to ANOTHER deployment, else None.

        ``model@<platform source>`` on a deployment that is not this client's
        own gets that source's own credential and none of this client's
        project or user headers (onboarding plan S1; see
        :func:`r4.source_context.config_for_source`).
        """
        if route.kind != "platform":
            return None
        src = self._route_source(route)
        if src is None or src.kind != "platform":
            return None
        from .source_context import config_for_source, same_deployment

        if same_deployment(src.url, self._config.gateway_url):
            return None
        return HTTPCore(config_for_source(src, self._config))

    def _http_for_route(self, route):
        routed = self._route_source_http(route)
        if routed is not None:
            return routed
        if route.kind == "public":
            # Source-bound BYOK routes call the resolved URL directly; bypass
            # BedrockHTTPAdapter when the profile default provider is Bedrock.
            if route.source_name and getattr(self._config, "endpoint_provider", None) == "bedrock":
                return self.platform_http
            return self.http
        if getattr(self._config, "endpoint_provider", None) == "bedrock":
            return self.platform_http
        return self.http

    def _headers_for_route(
        self,
        route,
        *,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> Optional[dict[str, str]]:
        if route.kind == "public":
            return self._public_auth_headers(route)
        return extra_headers

    def _request(
        self,
        payload: dict[str, Any],
        *,
        extra_headers: Optional[dict[str, str]] = None,
        turn_cancel: Any | None = None,
        limits: Optional[RoundLimits] = None,
    ) -> ChatResponse:
        """Make a non-streaming request.

        ``limits`` stops a streamed agent round at a round limit (ADR-087 D-5/D-6):
        reasoning past ``think_chars`` with no answer or tool call yet
        (``finish_reason="think_budget"``), or tool-call arguments past
        ``args_chars`` or mostly comments (``finish_reason="args_budget"``).
        """
        payload, route = self._normalize_route(payload)

        # Try provider-based path first
        provider = self._provider_for_route(route)
        if provider is not None and not self._should_bypass_provider_for_cancel(
            provider, turn_cancel
        ):
            return self._request_via_provider(provider, payload, route, turn_cancel)

        data = self._request_via_http(route, payload, extra_headers, turn_cancel, limits=limits)

        result = ChatResponse.from_api_response(data)
        self._record_cost(result, route)
        return result

    _DATA_CLASS_TTL_SECONDS = 60.0  # matches the server's keystore sweep cadence

    def _gate_project_id(self) -> Optional[str]:
        """The project the ADR-062 gate asks about: the project of the work
        the call is for when a caller named one (:func:`gate_project`; a
        loop iteration, ADR-108 D-9), else ``r4 project switch``'s current
        project — or, for a client bound to another deployment's source,
        that source's own project: the active project is never sent there
        (onboarding plan S1, 2026-10-15)."""
        named = _GATE_PROJECT.get()
        if named:
            return named
        if getattr(self._config, "bound_source", None):
            return self._config.project_id or None
        from .project import get_project_manager

        return get_project_manager().get_current()

    def _gate_project_label(self) -> str:
        """The gated project's id for a refusal message; a placeholder if unknown."""
        try:
            return self._gate_project_id() or "<project>"
        except Exception:  # noqa: BLE001 — a message must never fail the refusal
            return "<project>"

    def _project_data_class(self) -> str:
        """ADR-062: the ACTIVE project's data_class from the platform row.

        ``""`` when no project is active, the platform is unreachable, or
        the project has no row (an empty class, or a 404 for a project
        recorded only locally: ``project_expected_on_platform``): the gate
        then admits and never invents a class. A row the platform DENIES
        (403, or 404 for a project expected there: a foreign project, or
        one reachable only through an unsigned membership since H-1) reads
        as ``"sensitive"``, ADR-062's default: the gate fails closed
        (H-1, 2026-10-18). Cached per project for one TTL window (the
        companion makes many calls per turn; one GET per minute is enough).
        """
        try:
            project_id = self._gate_project_id()
            if not project_id:
                return ""
            now = time.monotonic()
            cached = self._data_class_cache.get(project_id)
            if cached is not None and now - cached[0] < self._DATA_CLASS_TTL_SECONDS:
                return cached[1]
            data_class, denied = self._fetch_project_data_class(project_id)
            self._data_class_cache[project_id] = (now, data_class, denied)
            return data_class
        except Exception:
            return ""

    def _fetch_project_data_class(self, project_id: str) -> tuple[str, int]:
        """``(data_class, denial status)`` from one GET of the project row; a
        denied row answers ``("sensitive", 403|404)``."""
        try:
            row = self.get(f"{self._api_prefix}/projects/{project_id}", timeout=10)
        except R4Error as exc:
            status = denial_status(exc)
            if not status:
                raise
            # A bound source's project is sent to that deployment: expected there.
            expected = bool(getattr(self._config, "bound_source", None))
            if status == 404 and not (expected or project_expected_on_platform(project_id)):
                return "", 0  # a local-only project: no row, the gate admits
            logger.warning(
                "project %s: the platform denied its row (HTTP %s); treating it "
                "as data_class=sensitive (ADR-062 fail-closed)",
                project_id,
                status,
            )
            return "sensitive", status
        # The server's envelope is {"status": "ok", "project": {...}}
        # (S2, 2026-10-15: a top-level read never fires — fixed).
        project = row.get("project") if isinstance(row, dict) else None
        data_class = str(project.get("data_class", "") or "") if isinstance(project, dict) else ""
        return data_class, 0

    def _data_class_remedy(self, project: str) -> str:
        """The refusal's last clause: widening the class helps only for a row
        the caller can read; a denied row cannot be widened by this caller."""
        cached = self._data_class_cache.get(project)
        status = cached[2] if cached is not None and len(cached) > 2 else 0
        if status:
            return _DENIED_ROW_REMEDY.format(project=project, status=status)
        return _WIDEN_CLASS_REMEDY.format(project=project)

    def _refuse_public_route_for_sensitive_project(self, route: Any) -> None:
        """ADR-062 + the 2026-10-15 amendment (D-37 client seam): a
        SENSITIVE active project never dials a PUBLIC inference route —
        refuse by name, fail fast. Institutional providers (Rikyu-class,
        the site's allowlist) and loopback models are admitted. The
        authoritative gates live server-side (venue start + the lease
        proxy); this stops the workstation from sending the request at
        all. No active project / unknown class → no gate."""
        if self._project_data_class() != "sensitive":
            return
        why = self._public_route_reason(route)
        if why is None:
            return
        from .source_locality import display_url

        target = route.source_name or display_url(route.base_url)
        project = self._gate_project_label()
        raise ModelRouteRefusedError(
            f"Public inference route '{target}' refused: the active project is "
            "data_class=sensitive (ADR-062) — research data may not leave the "
            f"site for inference.{why} Institutional providers (the site's "
            "allowlist, Rikyu by default), loopback models and platform sources "
            f"(model@<platform-source>) are allowed{self._data_class_remedy(project)}"
        )

    def _public_route_reason(self, route: Any) -> Optional[str]:
        """``None`` when a sensitive project may use ``route``; else why it is
        public (``""`` when the route's URL is plainly a public host).

        Every URL the request can reach is classified (2026-10-16): the
        route's URL (the HTTP path) and the URL the provider object serving
        the route dials — they can differ, the provider registry is cached
        per client. A provider with no URL (Bedrock: the AWS SDK and its
        config pick the endpoint) is public whatever its source URL says.
        """
        from .source_locality import carries_userinfo, classify_url, effective_institutional_hosts

        # The allowlist is the one the gated project's deployment advertised
        # (this client's gateway), never the active source's (2026-10-16).
        hosts = effective_institutional_hosts(getattr(self._config, "gateway_url", None))
        # Loopback is local here for ANY project, a remote gateway's too: the
        # amendment's "never local for a remote gateway's project" binds the
        # server gates, which cannot see this machine (ADR-062 amendment,
        # Decision 3 note of 2026-10-16).
        if classify_url(route.base_url, hosts=hosts) == "public":
            return _USERINFO_REASON if carries_userinfo(route.base_url) else ""
        provider = self._provider_for_route(route)
        if provider is None:
            return None
        dial_url = getattr(provider, "dial_url", None)
        if not isinstance(dial_url, str) or not dial_url:
            return _OWN_ENDPOINT_REASON
        if classify_url(dial_url, hosts=hosts) == "public":
            return _ELSEWHERE_REASON
        return None

    def _resolve_gated_route(self, raw_model: str) -> Any:
        """Resolve the model route through the ADR-062 gate.

        Every chat path (blocking, streaming, collecting, raw SSE) resolves
        here so no path dials a public route for a sensitive project.
        """
        route = resolve_model_route(self._config, raw_model)
        if route.kind == "public":
            self._refuse_public_route_for_sensitive_project(route)
        return route

    def _normalize_route(self, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Resolve the gated model route; strip ``@source`` from the payload model."""
        raw_model = str(payload.get("model", self._config.model))
        route = self._resolve_gated_route(raw_model)
        ref = parse_model_ref(raw_model)
        if ref.source is not None:
            payload = {**payload, "model": ref.model}
        return payload, route

    def _request_via_provider(
        self, provider: Any, payload: dict[str, Any], route: Any, turn_cancel: Any
    ) -> ChatResponse:
        """Provider call with best-effort cancellation checks around it."""
        # The provider call itself cannot be interrupted mid-flight, but the
        # pre/post checks cover cancels issued before the request starts or
        # while waiting for a slow provider response.
        if turn_cancel is not None:
            turn_cancel.check()
        data = provider.chat(
            messages=payload["messages"],
            model=payload.get("model"),
            temperature=payload.get("temperature", 0.7),
            max_tokens=payload.get("max_tokens"),
            tools=payload.get("tools"),
        )
        if turn_cancel is not None:
            turn_cancel.check()
        result = ChatResponse.from_api_response(data)
        self._record_cost(result, route)
        return result

    def text_completion(
        self, payload: dict[str, Any], *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Raw OpenAI ``/v1/completions`` call on the resolved, gated route.

        Same route resolution and ADR-062 gate as chat. Used by the decision
        model (ADR-076), which needs prompt text and ``logprob_token_ids``
        rather than chat messages; the response is returned unparsed.
        """
        payload, route = self._normalize_route(payload)
        return cast(
            dict[str, Any],
            self._http_for_route(route).post(
                f"{route.base_url}/v1/completions",
                json_body=payload,
                headers=self._headers_for_route(route),
                provider_request=route.kind == "public",
                timeout=timeout,
            ),
        )

    def _request_via_http(
        self,
        route: Any,
        payload: dict[str, Any],
        extra_headers: Optional[dict[str, str]],
        turn_cancel: Any,
        *,
        limits: Optional[RoundLimits] = None,
    ) -> dict[str, Any]:
        """HTTP chat-completions call + non-OpenAI error detection.

        ADR-087: an agent-loop round (it carries a turn-cancel token) is sent
        upstream as a stream and assembled (``R4_STREAM_BLOCKING=0`` restores the
        plain POST). A server that answers the stream with an HTTP 400 is
        retried once without it. Other blocking calls are unchanged.
        """
        url = f"{route.base_url}/v1/chat/completions"
        cancel_check = turn_cancel.check if turn_cancel is not None else None
        register_abort = turn_cancel.register_close if turn_cancel is not None else None
        headers = self._headers_for_route(route, extra_headers=extra_headers)
        data = None
        # Scope: the agent loop's rounds (they carry a turn-cancel token).
        if turn_cancel is not None and _stream_blocking_enabled() and not payload.get("stream"):
            data = self._post_streamed(
                route,
                url,
                payload,
                headers,
                (cancel_check, register_abort),
                limits=limits,
            )
        if data is None:
            data = self._http_for_route(route).post(
                url,
                json_body=payload,
                headers=headers,
                provider_request=route.kind == "public",
                cancel_check=cancel_check,
                register_abort=register_abort,
            )

        # Detect non-OpenAI responses (e.g., Bedrock Coral errors returned as HTTP 200).
        if "choices" not in data and data.get("Output", {}).get("__type"):
            error_type = data["Output"]["__type"].rsplit("#", 1)[-1]
            raise APIError(
                f"Endpoint returned an error: {error_type}. "
                f"The endpoint may not be OpenAI-compatible.",
                response_body=data,
            )
        return data

    def _post_streamed(
        self,
        route: Any,
        url: str,
        payload: dict[str, Any],
        headers: Optional[dict[str, str]],
        cancel: tuple[Any, Any],
        *,
        limits: Optional[RoundLimits] = None,
    ) -> Optional[dict[str, Any]]:
        """Stream a blocking chat call and assemble the response (ADR-087).

        Returns ``None`` when the server rejected the streamed form with an
        HTTP 400, so the caller falls back to the plain POST. A JSON (not SSE)
        reply is parsed as the full response. At a round limit (``limits``)
        reading stops (the connection closes, the server aborts the generation)
        and the assembled reply carries that limit as its ``finish_reason``.
        """
        cancel_check, register_abort = cancel
        body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        assembler = ChatStreamAssembler(limits)
        try:
            with self._http_for_route(route).stream(
                "POST",
                url,
                json_body=body,
                headers=headers,
                provider_request=route.kind == "public",
                cancel_check=cancel_check,
                register_abort=register_abort,
            ) as response:
                if "text/event-stream" not in (response.headers.get("content-type") or ""):
                    return _json_body(response)
                _read_sse_into(response, assembler, cancel_check)
        except StreamError as exc:
            raise APIError(f"Model stream error: {exc}", response_body=exc.error) from exc
        except APIError as exc:
            if getattr(exc, "status_code", None) == 400:
                logger.info("Streamed chat refused with HTTP 400; retrying as a plain POST")
                return None
            raise
        return assembler.assemble()

    def _stream_request(
        self,
        payload: dict[str, Any],
        *,
        extra_headers: Optional[dict[str, str]] = None,
        turn_cancel: Any | None = None,
    ) -> Iterator[str]:
        """Make a streaming request."""
        # This path does not capture usage chunks — clear any stash from a
        # previous collecting stream so consumers never read stale values.
        self._last_stream_usage = None
        # Gated resolve + strip @source from the model name sent to the backend
        payload, route = self._normalize_route(payload)

        # Try provider-based path first
        provider = self._provider_for_route(route)
        if provider is not None and not self._should_bypass_provider_for_cancel(
            provider, turn_cancel
        ):
            # Best-effort cancellation: check before starting, then between
            # each yielded chunk. The provider's stream() is a synchronous
            # generator, so we can cancel between chunks but not mid-chunk.
            if turn_cancel is not None:
                turn_cancel.check()
            for chunk in provider.stream(
                messages=payload["messages"],
                model=payload.get("model"),
                temperature=payload.get("temperature", 0.7),
                max_tokens=payload.get("max_tokens"),
                tools=payload.get("tools"),
            ):
                if turn_cancel is not None:
                    turn_cancel.check()
                yield chunk
            return

        # Legacy HTTP path
        url = f"{route.base_url}/v1/chat/completions"
        yield from self._http_for_route(route).iter_sse_content(
            url,
            json_body=payload,
            headers=self._headers_for_route(route, extra_headers=extra_headers),
            provider_request=route.kind == "public",
            cancel_check=turn_cancel.check if turn_cancel is not None else None,
            register_abort=turn_cancel.register_close if turn_cancel is not None else None,
        )

    def _stream_request_collect(
        self,
        payload: dict[str, Any],
        tool_calls_acc: list[dict[str, Any]],
        *,
        extra_headers: Optional[dict[str, str]] = None,
        reasoning_acc: Optional[list] = None,
        turn_cancel: Any | None = None,
        limits: Optional[RoundLimits] = None,
    ) -> Optional[Iterator[str]]:
        """Stream content deltas while reconstructing tool-call deltas.

        Agentic callers need BOTH: the text (for live rendering) and any
        tool calls the model chose in the same round. ``_stream_request``
        only surfaces content, so rounds mixing a preamble with tool calls
        silently dropped the tools and the agent loop ended after the
        preamble. This variant merges ``tool_calls`` deltas into
        ``tool_calls_acc`` in wire shape (``message.tool_calls``).

        Returns ``None`` only when the route speaks a non-OpenAI wire
        protocol (Bedrock Converse), where tool-call deltas have no known
        shape — callers must then fall back to a blocking request so
        structured tool calls are never lost. OpenAI-compatible providers
        (LiteLLM, OpenAI-compatible public endpoints, Ollama) are served
        through the same SSE endpoint the text-only path uses, so this
        collects straight from the HTTP stream and keeps tool-call deltas.
        """
        payload, route = self._normalize_route(payload)
        provider = self._provider_for_route(route)
        if provider is not None and type(provider).__name__ == "BedrockProvider":
            return None
        url = f"{route.base_url}/v1/chat/completions"
        headers = self._headers_for_route(route, extra_headers=extra_headers)
        return self._collect_sse_deltas(
            url,
            route,
            payload,
            headers,
            tool_calls_acc,
            reasoning_acc=reasoning_acc,
            turn_cancel=turn_cancel,
            limits=limits,
        )

    def _collect_sse_deltas(
        self,
        url: str,
        route: Any,
        payload: dict[str, Any],
        headers: dict[str, str],
        tool_calls_acc: list[dict[str, Any]],
        *,
        reasoning_acc: Optional[list] = None,
        turn_cancel: Any | None = None,
        limits: Optional[RoundLimits] = None,
    ) -> Iterator[str]:
        """Yield content deltas from raw SSE chunks; merge tool-call deltas.

        When ``reasoning_acc`` is provided, ``reasoning_content`` deltas
        (deepseek/Kimi families) are collected so the caller can echo the
        model's reasoning alongside the assistant message — preserving
        self-consistency across tool-call chains (pattern: goose providers).

        Also captures the stream's real token usage (servers send a final
        ``usage`` chunk when the request sets
        ``stream_options.include_usage``) into ``session_cost`` and
        ``_last_stream_usage``; servers that send none leave both untouched
        and callers fall back to estimates.

        The round's ``finish_reason`` lands in ``_last_stream_finish``; a round
        limit (``limits``) ends the stream and becomes that finish reason.
        """
        self._last_stream_usage = None
        self._last_stream_finish = None
        watch = _RoundWatch(limits, reasoning_acc)
        done: list[bool] = []
        chunks = self._http_for_route(route).iter_sse_chunks(
            url,
            json_body=payload,
            headers=headers,
            provider_request=route.kind == "public",
            cancel_check=turn_cancel.check if turn_cancel is not None else None,
            register_abort=turn_cancel.register_close if turn_cancel is not None else None,
            on_done=lambda: done.append(True),
        )
        yield from _absorb_round(chunks, watch, tool_calls_acc)
        if watch.finish is None and not done:
            watch.finish = STREAM_CUT_FINISH
        self._last_stream_finish = watch.finish
        if watch.usage is not None:
            model = str(payload.get("model") or "")
            self._last_stream_usage = {**watch.usage, "model": model}
            self._record_stream_usage(watch.usage, model, route)

    def stream_chat_chunks(
        self,
        messages: List[ChatMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        task: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> Iterator[dict]:
        """Stream chat completion as raw SSE chunks (preserves usage data)."""
        model = model or self._config.model
        ref = parse_model_ref(model)
        route = self._resolve_gated_route(model)
        # Use the bare model name (without @source) in the payload
        wire_model = ref.model
        payload = {
            "model": wire_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        extra_headers = _build_routing_headers(task=task, agent=agent)
        url = f"{route.base_url}/v1/chat/completions"
        yield from self._http_for_route(route).iter_sse_chunks(
            url,
            json_body=payload,
            headers=self._headers_for_route(route, extra_headers=extra_headers),
            provider_request=route.kind == "public",
        )

    def _build_headers(self) -> dict[str, str]:
        """Build request headers (compat shim; prefer :attr:`http`)."""
        return self.http.build_headers()

    def _handle_response_errors(self, response) -> None:
        """Handle HTTP response errors (compat shim; prefer :attr:`http`)."""
        raise_for_status(response)

    def list_models(self) -> List[str]:
        """List available models."""
        route = resolve_model_route(self._config, self._config.model)
        if route.kind == "public":
            return self.list_public_models()
        return self.list_gateway_models()

    def model_entries(self, *, timeout: float = 10) -> List[dict[str, Any]]:
        """Raw ``/v1/models`` entries of the active route.

        Model limits ride on them (W5/U3, 2026-10-05): ``context_window``,
        ``max_output_tokens`` and the reasoning flags from the UMRS LLM proxy;
        a vLLM-style source publishes ``max_model_len``. Cached per route for
        ``_MODEL_ENTRIES_TTL_SEC`` (the agent asks before every turn).
        """
        route = resolve_model_route(self._config, self._config.model)
        key = (route.kind, self._config.model)
        cached = getattr(self, "_model_entries_cache", None)
        if cached and cached[0] == key and time.monotonic() - cached[1] < _MODEL_ENTRIES_TTL_SEC:
            return cached[2]
        if route.kind == "public":
            if not has_public_endpoint(self._config):
                return []
            data = self.http.get(
                f"{public_endpoint(self._config)}/v1/models",
                headers=self._endpoint_auth_headers(),
                timeout=timeout,
                provider_request=True,
            )
        else:
            data = self.platform_http.get(
                f"{gateway_llm_endpoint(self._config)}/v1/models", timeout=timeout
            )
        entries = data.get("data", []) if isinstance(data, dict) else []
        result = [m for m in entries if isinstance(m, dict)]
        self._model_entries_cache = (key, time.monotonic(), result)
        return result

    def list_public_models(self, *, timeout: float = 30) -> List[str]:
        """List models from the configured public endpoint."""
        if not has_public_endpoint(self._config):
            return []
        url = f"{public_endpoint(self._config)}/v1/models"
        data = self.http.get(
            url,
            headers=self._endpoint_auth_headers(),
            timeout=timeout,
            provider_request=True,
        )
        return _model_ids(url, data)

    def list_gateway_models(self, *, timeout: float = 30) -> List[str]:
        """List models served through the platform Gateway LLM route."""
        url = f"{gateway_llm_endpoint(self._config)}/v1/models"
        data = self.platform_http.get(url, timeout=timeout)
        return _model_ids(url, data)

    # ── Research loop control (/gw/loops/* → /umrs/loops/*) ───────────────

    def start_loop(
        self,
        project_id: str,
        research_goal: str,
        *,
        max_iterations: int = 10,
        approved: bool = False,
        session_id: Optional[str] = None,
        agentic: bool = False,
        workflow_name: Optional[str] = None,
        max_tracks: int = 1,
        timeout: Optional[float] = None,
        plan: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Start an autonomous research loop on the platform.

        ``plan`` (ADR-092 slice 8b) carries a plan loop's fields
        (``iteration_plan``, its resolved template, args, venue sources, mode):
        UMRS runs each iteration as one venue-hosted plan run.

        ``session_id`` (optional, ADR-025) registers the loop under a durable
        research session so its membership survives gateway restarts.
        ``agentic`` selects the W9 agent executor on UMRS (bounded one-turn
        iteration decisions matching the local runtime executor semantics).
        ``workflow_name`` names the registered UMRS workflow each real-mode
        experiment runs; omitted, UMRS uses the single registered workflow
        or fails closed listing the candidates (G-P1-3).
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/loops"
        payload = {
            "project_id": project_id,
            "research_goal": research_goal,
            "max_iterations": max_iterations,
            "approved": approved,
        }
        if session_id:
            payload["session_id"] = session_id
        if agentic:
            payload["agentic"] = True
        if workflow_name:
            payload["workflow_name"] = workflow_name
        if max_tracks > 1:
            payload["max_tracks"] = max_tracks  # ADR-066 amendment 2 (D-34)
        if plan:
            payload.update(dict(plan))
        return self.http.post(
            url, json_body=payload, timeout=timeout or 30, headers=self._companion_headers()
        )

    def _companion_headers(self) -> Optional[dict[str, str]]:
        """``x-r4-session-id`` of the bound companion session (D-52), if any."""
        sid = (self.companion_session_id or "").strip()
        return {"x-r4-session-id": sid} if sid else None

    def get_platform_project(self, project_id: str) -> dict[str, Any]:
        """The durable project row (GET /gw/projects/{id} → ``{"project": …}``)."""
        return self.get(f"{self._api_prefix}/projects/{project_id}", timeout=15)

    def create_platform_project(self, project_id: str, *, name: str = "") -> dict[str, Any]:
        """Register ``project_id`` on the platform (POST /gw/projects, ADR-051
        D-1): any verified user; the caller becomes the owner; idempotent for
        that owner, ``conflict`` when another user holds the id.

        ``X-Project-ID`` goes out empty: the gateway (ADR-097) checks the
        request's project against the caller's live memberships, and the
        project being created is not one yet — empty means the token's own
        project (2026-11-03, ``r4 project register``).
        """
        reply: dict[str, Any] = self.post(
            f"{self._api_prefix}/projects",
            json={"project_id": project_id, "name": name},
            headers={"X-Project-ID": ""},
            timeout=15,
        )
        return reply

    def list_platform_projects(self, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Every durable project row the caller may list (owned rows; all rows
        for a platform admin; GET /gw/projects → ``{"projects": …}``)."""
        return self.get(f"{self._api_prefix}/projects", timeout=timeout or 10)

    def set_project_ask_policy(
        self,
        project_id: str,
        *,
        maturity_stage: str = "",
        ask_mode: str = "",
        pause_triggers: Optional[List[str]] = None,
    ) -> dict[str, Any]:
        """Persist the project's ask-the-scientist default (D-33; ADR-066
        amendment 3): a maturity stage preset, or explicit mode + triggers
        (all empty clears it). Owner or admin only (server-enforced)."""
        body: dict[str, Any] = {"action": "set_ask_policy"}
        if maturity_stage:
            body["maturity_stage"] = maturity_stage
        else:
            body["ask_mode"] = ask_mode
            body["pause_triggers"] = list(pause_triggers or [])
        return self.patch(f"{self._api_prefix}/projects/{project_id}", json=body)

    def set_project_data_class(self, project_id: str, data_class: str) -> dict[str, Any]:
        """ADR-062 amendment D-5: set the project's data class
        ('sensitive' | 'non_sensitive'). Owner or admin only (server
        enforces); the widening typed-confirmation lives in the CLI —
        this method sends the verb."""
        return self.patch(
            f"{self._api_prefix}/projects/{project_id}",
            json={"action": "set_data_class", "data_class": data_class},
        )

    # -- external storage binding (ADR-077) ---------------------------------
    # The locators carry credentials; they are sent once to UMRS (which seals
    # them) and never stored client-side. Responses are redacted server-side.

    def get_project_storage(self, project_id: str) -> dict[str, Any]:
        """The project's storage binding, redacted (owner or admin)."""
        return self.get(f"{self._api_prefix}/projects/{project_id}/storage", timeout=15)

    def set_project_storage(
        self,
        project_id: str,
        *,
        s3_url: Optional[str] = None,
        postgres_uri: Optional[str] = None,
        check: bool = True,
    ) -> dict[str, Any]:
        """Bind the project's stores (None keeps a store, "" unbinds it).

        With ``check`` the server probes the result before saving.
        """
        body: dict[str, Any] = {"check": check}
        if s3_url is not None:
            body["s3_url"] = s3_url
        if postgres_uri is not None:
            body["postgres_uri"] = postgres_uri
        return self.http.put(
            self._abs_url(f"{self._api_prefix}/projects/{project_id}/storage"),
            json_body=body,
            timeout=120,
        )

    def check_project_storage(self, project_id: str) -> dict[str, Any]:
        """Probe the saved binding from the server."""
        return self.post(
            f"{self._api_prefix}/projects/{project_id}/storage/check", json={}, timeout=120
        )

    def clear_project_storage(self, project_id: str) -> dict[str, Any]:
        """Unbind both stores (the project returns to the server's stores)."""
        return self.delete(f"{self._api_prefix}/projects/{project_id}/storage", timeout=15)

    def list_loops(
        self, *, companion_session_id: str = "", timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """List the caller's research loops (id, status, phase); with
        ``companion_session_id`` only the loops that session started (D-52)."""
        url = f"{self._config.gateway_url}{self._api_prefix}/loops"
        params = {"companion_session_id": companion_session_id} if companion_session_id else None
        return self.http.get(url, params=params, timeout=timeout or 30)

    def get_loop_status(self, loop_id: str, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Status snapshot of a research loop."""
        url = f"{self._config.gateway_url}{self._api_prefix}/loops/{loop_id}"
        return self.http.get(url, timeout=timeout or 30)

    def list_loop_iterations(
        self, loop_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """List iterations of a research loop."""
        url = f"{self._config.gateway_url}{self._api_prefix}/loops/{loop_id}/iterations"
        return self.http.get(url, timeout=timeout or 30)

    def stop_loop(self, loop_id: str, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Stop a running research loop."""
        url = f"{self._config.gateway_url}{self._api_prefix}/loops/{loop_id}/stop"
        return self.http.post(url, timeout=timeout or 30)

    def pause_loop(self, loop_id: str, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Pause a running research loop."""
        url = f"{self._config.gateway_url}{self._api_prefix}/loops/{loop_id}/pause"
        return self.http.post(url, timeout=timeout or 30)

    def resume_loop(self, loop_id: str, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Resume a paused research loop."""
        url = f"{self._config.gateway_url}{self._api_prefix}/loops/{loop_id}/resume"
        return self.http.post(url, timeout=timeout or 30)

    # ── Loop approval inbox (/gw/companion/approvals* → /umrs/companion/approvals*) ─

    def list_loop_approvals(
        self, loop_id: Optional[str] = None, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Pending tool-grain approvals of the caller's server-hosted loops.

        Durable inbox (ADR-036 (a)): rows survive UMRS restarts and are
        answerable from any client of the loop owner. The response carries
        ``durability`` (postgres | sqlite | process) so callers can badge it.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/companion/approvals/pending"
        params = {"loop_id": loop_id} if loop_id else {}
        return self.http.get(url, params=params, timeout=timeout or 10)

    def decide_loop_approval(
        self, approval_id: str, decision: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Record a decision (``once`` | ``always`` | ``no``) on one pending approval."""
        url = (
            f"{self._config.gateway_url}{self._api_prefix}"
            f"/companion/approvals/{approval_id}/decision"
        )
        return self.http.post(
            url,
            json_body={"decision": decision},
            timeout=timeout or 10,
            headers=self._companion_headers(),
        )

    # ── Asset grants (/gw/grants* → /umrs/grants*) ─

    def _grants_url(self, suffix: str = "") -> str:
        return f"{self._config.gateway_url}{self._api_prefix}/grants{suffix}"

    # ── Circles + membership (ADR-086; /gw/circles* → /umrs/circles*) ─────────

    def _circles_url(self, suffix: str = "") -> str:
        return f"{self._config.gateway_url}{self._api_prefix}/circles{suffix}"

    def create_circle(
        self,
        project_id: str,
        circle_id: str,
        *,
        name: str = "",
        for_user: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Ensuring-shape circle creation (idempotent; creator is first member,
        role admin). ``circle_id == your user id`` is the personal cell — the
        casual way to enter a project."""
        payload: dict[str, Any] = {
            "project_id": project_id,
            "circle_id": circle_id,
            "name": name,
        }
        if for_user:
            payload["for_user"] = for_user
        return self.http.post(self._circles_url(), json_body=payload, timeout=timeout or 10)

    def list_my_circles(
        self, *, include_retired: bool = False, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Every circle the caller owns or belongs to, plus the derived
        project list (projects = projects holding at least one of my circles)."""
        return self.http.get(
            self._circles_url("/mine"),
            params={"include_retired": "true" if include_retired else "false"},
            timeout=timeout or 10,
        )

    def list_circles(
        self,
        project_id: str,
        *,
        include_retired: bool = False,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Circles of one project (roster for affiliated callers, the project
        owner and admins; unaffiliated callers see only their own rows)."""
        return self.http.get(
            self._circles_url(f"/{project_id}"),
            params={"include_retired": "true" if include_retired else "false"},
            timeout=timeout or 10,
        )

    def get_circle(
        self, project_id: str, circle_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        return self.http.get(self._circles_url(f"/{project_id}/{circle_id}"), timeout=timeout or 10)

    def retire_circle(
        self, project_id: str, circle_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Tombstone a circle (owner-or-admin; retired circles stop resolving)."""
        return self.http.patch(
            self._circles_url(f"/{project_id}/{circle_id}"),
            json_body={"action": "retire"},
            timeout=timeout or 10,
        )

    def list_circle_members(
        self, project_id: str, circle_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        return self.http.get(
            self._circles_url(f"/{project_id}/{circle_id}/members"),
            timeout=timeout or 10,
        )

    def add_circle_member(
        self,
        project_id: str,
        circle_id: str,
        user_id: str,
        *,
        role: str = "member",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Roster add — the four-rung ladder: circle owner, circle admin,
        project owner, or platform admin (ADR-086 amendment 2026-10-19)."""
        return self.http.post(
            self._circles_url(f"/{project_id}/{circle_id}/members"),
            json_body={"user_id": user_id, "role": role},
            timeout=timeout or 10,
        )

    def remove_circle_member(
        self,
        project_id: str,
        circle_id: str,
        user_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Roster removal — same ladder; the circle owner is never removed."""
        return self.http.delete(
            self._circles_url(f"/{project_id}/{circle_id}/members/{user_id}"),
            timeout=timeout or 10,
        )

    # ── Scientific Catalogs (D-39; /gw/scientific-catalogs* → /umrs/*) ────────

    def _scat_url(self, suffix: str = "") -> str:
        return f"{self._config.gateway_url}{self._api_prefix}/scientific-catalogs{suffix}"

    def create_scientific_catalog(
        self,
        *,
        project_id: str,
        name: str,
        version: str = "0.1.0",
        description: str = "",
        ontology: str = "",
        member_graph_ids: Optional[list] = None,
        skill_ids: Optional[list] = None,
        mcp_server_ids: Optional[list] = None,
        embedding_model_ids: Optional[list] = None,
        contributors: Optional[list] = None,
        licence: str = "",
        provenance: Optional[dict] = None,
        change_note: str = "",
        redaction_rules: Optional[list] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Create one catalog version (idempotent on the project/name/version triple)."""
        payload: dict[str, Any] = {
            "project_id": project_id,
            "name": name,
            "version": version,
            "description": description,
            "ontology": ontology,
            "member_graph_ids": member_graph_ids or [],
            "skill_ids": skill_ids or [],
            "mcp_server_ids": mcp_server_ids or [],
            "embedding_model_ids": embedding_model_ids or [],
            "contributors": contributors or [],
            "licence": licence,
            "provenance": provenance or {},
            "change_note": change_note,
            "redaction_rules": redaction_rules or [],
        }
        return self.http.post(self._scat_url(), json_body=payload, timeout=timeout or 10)

    def list_scientific_catalogs(
        self, *, project_id: Optional[str] = None, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """List active catalogs (project-scoped for non-admin callers)."""
        params = {"project_id": project_id} if project_id else None
        return self.http.get(self._scat_url(), params=params, timeout=timeout or 10)

    def get_scientific_catalog(
        self, catalog_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Fetch one catalog row (404 for unknown ids)."""
        return self.http.get(self._scat_url(f"/{catalog_id}"), timeout=timeout or 10)

    def patch_scientific_catalog(
        self, catalog_id: str, *, patch: dict, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Metadata-only update (description/ontology/contributors/licence/provenance)."""
        return self.http.patch(
            self._scat_url(f"/{catalog_id}"), json_body=patch, timeout=timeout or 10
        )

    def retire_scientific_catalog(
        self, catalog_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Tombstone a catalog version (retire — never a DELETE)."""
        return self.http.delete(self._scat_url(f"/{catalog_id}"), timeout=timeout or 10)

    def list_catalog_versions(
        self,
        *,
        project_id: str,
        name: str,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """One catalog's version chain (changelog view, oldest first — D-43)."""
        return self.http.get(
            self._scat_url("/versions/list"),
            params={"project_id": project_id, "name": name},
            timeout=timeout or 10,
        )

    def add_catalog_pin(
        self,
        catalog_id: str,
        *,
        kind: str,
        ref: str,
        note: str = "",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Append one pin (job/result/finding/paper ref) to a catalog version."""
        return self.http.post(
            self._scat_url(f"/{catalog_id}/pins"),
            json_body={"kind": kind, "ref": ref, "note": note},
            timeout=timeout or 10,
        )

    def catalog_pins_by_ref(
        self,
        ref: str,
        *,
        project_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Inverse pin index (D-45): which catalog versions pinned this ref."""
        params = {"project_id": project_id} if project_id else None
        return self.http.get(
            self._scat_url(f"/pins/by-ref/{ref}"),
            params=params,
            timeout=timeout or 10,
        )

    # ── Uniform resource access lists (ADR-080) ─────────────────────────────
    #
    # Every family shares the grammar: ``user:<id> | project:<id> |
    # circle:<proj>/<id> | system`` (max 16 entries); ``[]`` restores the family's
    # default tier (private again). ``system`` entries are admin-only.

    def set_graph_access(self, graph_id: str, access: List[str]) -> dict[str, Any]:
        """Replace who may read a knowledge graph beyond its tier ladder (ADR-080).

        ``[]`` restores the default tier (owning project only). Write-tier
        owners or admins may change it; ``system`` is admin-only. Governs
        READS — lifecycle writes are unaffected.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/knowledge/graphs/{graph_id}/access"
        return self.http.put(url, json_body={"access": list(access)})

    def get_graph_access(self, graph_id: str) -> dict[str, Any]:
        """Fetch a knowledge graph's current access list (ADR-080)."""
        url = f"{self._config.gateway_url}{self._api_prefix}/knowledge/graphs/{graph_id}/access"
        return self.http.get(url)

    def set_catalog_access(self, catalog_id: str, access: List[str]) -> dict[str, Any]:
        """Replace who may read a Scientific Catalog row (ADR-080).

        ``[]`` restores the default (owning project only); ``system`` is
        admin-only. Governs READS — lifecycle writes stay project-scoped.
        """
        return self.http.put(
            self._scat_url(f"/{catalog_id}/access"), json_body={"access": list(access)}
        )

    def get_catalog_access(self, catalog_id: str) -> dict[str, Any]:
        """Fetch a Scientific Catalog row's current access list (ADR-080)."""
        return self.http.get(self._scat_url(f"/{catalog_id}/access"))

    def set_workflow_access(self, workflow_name: str, access: List[str]) -> dict[str, Any]:
        """Replace who may run/describe a workflow beyond its owner (ADR-080).

        ``[]`` restores owner-only access; ``system`` is admin-only (and
        only admins may re-scope system workflows). Lifecycle (re-upload,
        delete) is unchanged.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/workflows/{workflow_name}/access"
        return self.http.put(url, json_body={"access": list(access)})

    def get_workflow_access(self, workflow_name: str) -> dict[str, Any]:
        """Fetch a workflow's current access list (ADR-080)."""
        url = f"{self._config.gateway_url}{self._api_prefix}/workflows/{workflow_name}/access"
        return self.http.get(url)

    def set_bucket_access(self, bucket: str, access: List[str]) -> dict[str, Any]:
        """Replace who may read/write objects in a personal bucket (ADR-080).

        ``[]`` restores owner-only access. Owner or admin may change it;
        ``system`` is admin-only. Bucket lifecycle stays owner-only.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/buckets/{bucket}/access"
        return self.http.put(url, json_body={"access": list(access)})

    def get_bucket_access(self, bucket: str) -> dict[str, Any]:
        """Fetch a bucket's current access list (ADR-080)."""
        url = f"{self._config.gateway_url}{self._api_prefix}/buckets/{bucket}/access"
        return self.http.get(url)

    def set_rag_corpus_access(self, project_id: str, access: List[str]) -> dict[str, Any]:
        """Replace who may read a project's RAG corpus beyond the project (ADR-080).

        ``[]`` restores the default (owning project only); ``system`` is
        admin-only. Governs READS (query/documents/stats) — ingest and
        delete stay with the owning project.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/rag/corpus-access"
        body = {"project_id": project_id, "access": list(access)}
        return self.http.put(url, json_body=body)

    def get_rag_corpus_access(self, project_id: str) -> dict[str, Any]:
        """Fetch a project's RAG corpus read-sharing list (ADR-080)."""
        url = f"{self._config.gateway_url}{self._api_prefix}/rag/corpus-access"
        return self.http.get(url, params={"project_id": project_id})

    # ── Evidence plane (D-45; /gw/evidence* → /umrs/evidence*) ───────────────

    def get_evidence_claim(
        self, claim_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """One claim row with its evidence map (first client caller of the
        claims route — D-45)."""
        url = f"{self._config.gateway_url}{self._api_prefix}/evidence/claims/{claim_id}"
        return self.http.get(url, timeout=timeout or 10)

    def _evidence_url(self, suffix: str = "") -> str:
        return f"{self._config.gateway_url}{self._api_prefix}/evidence{suffix}"

    def list_evidence(
        self,
        *,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        claim_id: Optional[str] = None,
        limit: int = 100,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """List evidence with filters (V-07: retraction/negative results plane)."""
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status
        if source_type:
            params["source_type"] = source_type
        if claim_id:
            params["claim_id"] = claim_id
        return self.http.get(self._evidence_url(), params=params, timeout=timeout or 10)

    def update_evidence_status(
        self, evidence_id: str, status: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Retract/supersede one evidence row (append-only status write — V-07)."""
        return self.http.put(
            self._evidence_url(f"/{evidence_id}/status"),
            json_body={"status": status},
            timeout=timeout or 10,
        )

    # ── Scientific memory (V-07; /gw/scientific-memory* → /umrs/scientific-memory*) ──

    def _scimem_url(self, suffix: str = "") -> str:
        return f"{self._config.gateway_url}{self._api_prefix}/scientific-memory{suffix}"

    def list_memory_entries(
        self,
        *,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """List scientific-memory entries (failed experiments, retractions…)."""
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status
        return self.http.get(self._scimem_url("/entries"), params=params, timeout=timeout or 10)

    def search_memory(
        self, query: str, *, project_id: Optional[str] = None, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Full-text search over scientific-memory entries."""
        params: dict[str, Any] = {"query": query}
        if project_id:
            params["project_id"] = project_id
        return self.http.get(self._scimem_url("/search"), params=params, timeout=timeout or 10)

    def update_memory_status(
        self, entry_id: str, status: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Retract/reactivate one scientific-memory entry."""
        return self.http.put(
            self._scimem_url(f"/entries/{entry_id}/status"),
            json_body={"status": status},
            timeout=timeout or 10,
        )

    # ── Quota gauge (ADR-051 read model; /gw/quota/units) ─────────────────────

    def quota_units(self, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """The caller's two resource poles, exactly as the gates enforce them."""
        return self.http.get(
            f"{self._config.gateway_url}{self._api_prefix}/quota/units",
            timeout=timeout or 10,
        )

    def site_me(self, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """The caller's own view of this site (multi-HPC plan S2): declared
        access mode, own account row, capabilities. 404 = not deployed yet."""
        return self.http.get(
            f"{self._config.gateway_url}{self._api_prefix}/sites/me",
            timeout=timeout or 10,
        )

    def create_grant(
        self,
        asset_kind: str,
        asset_id: str,
        grantee_kind: str,
        grantee_id: str,
        *,
        version_pin: Optional[str] = None,
        expires_at: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Share one asset by a (version-pinned) grant — ADR-041.

        The caller must hold static write authority on the asset; grants
        authorize READS only. ``version_pin=None`` follows the current asset.
        """
        payload: dict[str, Any] = {
            "asset_kind": asset_kind,
            "asset_id": asset_id,
            "grantee_kind": grantee_kind,
            "grantee_id": grantee_id,
        }
        if version_pin:
            payload["version_pin"] = version_pin
        if expires_at:
            payload["expires_at"] = expires_at
        return self.http.post(self._grants_url(), json_body=payload, timeout=timeout or 30)

    def list_grants(
        self, asset_kind: str, asset_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Live + tombstoned grants on one asset (asset writers only)."""
        return self.http.get(
            self._grants_url(),
            params={"asset_kind": asset_kind, "asset_id": asset_id},
            timeout=timeout or 10,
        )

    def list_my_grants(self, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Live grants received by the caller (user + verified project)."""
        return self.http.get(self._grants_url("/mine"), timeout=timeout or 10)

    def revoke_grant(self, grant_id: str, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Tombstone a grant (never DELETE — the audit trail survives)."""
        return self.http.post(self._grants_url(f"/{grant_id}/revoke"), timeout=timeout or 10)

    # ── Research sessions (/gw/research/sessions/* → /umrs/research/sessions/*) ─

    def _sessions_url(self, session_id: Optional[str] = None) -> str:
        base = f"{self._config.gateway_url}{self._api_prefix}/research/sessions"
        return f"{base}/{session_id}" if session_id else base

    def create_research_session(
        self,
        project_id: str,
        research_goal: str,
        *,
        title: str = "",
        authoritative_runtime: str = "umrs",
        session_id: str = "",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Create a durable research session (ADR-025)."""
        payload: dict[str, Any] = {
            "project_id": project_id,
            "research_goal": research_goal,
            "title": title,
            "authoritative_runtime": authoritative_runtime,
        }
        if session_id:
            payload["session_id"] = session_id
        return self.http.post(self._sessions_url(), json_body=payload, timeout=timeout or 30)

    def list_research_sessions(
        self,
        project_id: Optional[str] = None,
        *,
        status: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """List durable research sessions."""
        params = {}
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status
        return self.http.get(self._sessions_url(), params=params, timeout=timeout or 30)

    def get_research_session(
        self, session_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Fetch one session with its linked loops."""
        return self.http.get(self._sessions_url(session_id), timeout=timeout or 30)

    def update_research_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        status: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Update a session (title/status lifecycle)."""
        payload = {k: v for k, v in {"title": title, "status": status}.items() if v is not None}
        return self.http.patch(
            self._sessions_url(session_id), json_body=payload, timeout=timeout or 30
        )

    def attach_loop_to_session(
        self,
        session_id: str,
        loop_id: str,
        *,
        venue: str = "umrs",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Link an existing loop to a session (ADR-025 D4)."""
        return self.http.post(
            f"{self._sessions_url(session_id)}/loops",
            json_body={"loop_id": loop_id, "venue": venue},
            timeout=timeout or 30,
        )

    def list_session_loops(
        self, session_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """List loops linked to a session."""
        return self.http.get(f"{self._sessions_url(session_id)}/loops", timeout=timeout or 30)

    def list_session_turns(
        self,
        session_id: str,
        turn_id: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """List turn payloads of one companion session (level-detail).

        Caller-passed ``turn_id`` returns that turn only (``{entry}``);
        otherwise the session's turn index (``{entries, count}``). The routes
        live on the companion sessions family, not research sessions (TUI
        audit #58, 2026-10-17).
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/companion/sessions/{session_id}/turns"
        if turn_id:
            url = f"{url}/{turn_id}"
        return self.http.get(url, timeout=timeout or 30)

    # ── Asset index (/gw/assets → /umrs/assets) ───────────────────────────

    def list_assets(
        self,
        kinds: str = "",
        project_id: str = "",
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Cross-subsystem asset index snapshot (read-model, Review F4)."""
        params: dict[str, Any] = {}
        if kinds:
            params["kinds"] = kinds
        if project_id:
            params["project_id"] = project_id
        url = f"{self._config.gateway_url}{self._api_prefix}/assets"
        return self.http.get(url, params=params, timeout=timeout or 30)

    def get_asset(
        self, kind: str, asset_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Fetch one asset row by kind+id."""
        url = f"{self._config.gateway_url}{self._api_prefix}/assets/{kind}/{asset_id}"
        return self.http.get(url, timeout=timeout or 30)

    def assets_patch(
        self,
        base: dict[str, str],
        kinds: str = "",
        project_id: str = "",
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """POST /umrs/assets/patch — caller-held shape-hash snapshot, server
        answers the diff (rows upserted + tombstoned kinds since `base`).

        body: {base: {"<kind>:<id>": "<sha1-shape-hash>"}, kinds: ..., project_id: ...}
        response: {upserts: AssetRow[], tombstones: ["<kind>:<id>", ...], snapshot_hash,
        same, warnings, count}

        Contract: callers keep exactly one base map per session; never mutate it
        mid-poll. Owner-scoped rows only (caller identity) — never admin broadcast.
        """
        payload: dict[str, Any] = {"base": base}
        if kinds:
            payload["kinds"] = kinds
        if project_id:
            payload["project_id"] = project_id
        # No trailing slash: "/patch/" draws a 307 to an /umrs/ path the edge
        # does not route (TUI audit #85).
        url = f"{self._config.gateway_url}{self._api_prefix}/assets/patch"
        return self.http.post(url, json_body=payload, timeout=timeout or 30)

    # ── Companion trust allowlist (/gw/companion/trust*) ─────────────────

    def _trust_url(self, suffix: str = "") -> str:
        base = f"{self._config.gateway_url}{self._api_prefix}/companion/trust"
        return f"{base}{suffix}"

    def trust_check(
        self, tool_name: str, scope: str = "", *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Ask the server whether tool_name is allowlisted for the caller.

        Fails closed on transport errors? No — callers decide the policy
        (autopilot treats unreachable as deny; interactive modes treat it
        as "fall back to process-local checks").
        """
        params = {"tool_name": tool_name}
        if scope:
            params["scope"] = scope
        return self.http.get(self._trust_url("/check"), params=params, timeout=timeout or 10)

    def trust_grant(
        self,
        tool_name: str,
        scope: str = "global",
        ttl_sec: int = 1800,
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Grant a server-side trust row for the caller."""
        return self.http.put(
            self._trust_url(),
            json_body={"tool_name": tool_name, "scope": scope, "ttl_sec": ttl_sec},
            timeout=timeout or 10,
        )

    def trust_list(self, scope: str = "", *, timeout: Optional[float] = None) -> dict[str, Any]:
        """List the caller's unexpired trust rows."""
        params = {"scope": scope} if scope else {}
        return self.http.get(self._trust_url(), params=params, timeout=timeout or 10)

    def governance_ownership(self, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Resource ownership table (who owns which endpoint/model/server)."""
        return self.http.get(
            self._abs_url(f"{self._api_prefix}/ops/governance/ownership"),
            timeout=timeout or 10,
        )

    def governance_quotas(self, *, timeout: Optional[float] = None) -> dict[str, Any]:
        """Per-project quota limits vs current usage."""
        return self.http.get(
            self._abs_url(f"{self._api_prefix}/ops/governance/quotas"),
            timeout=timeout or 10,
        )

    def audit_trace(self, *, limit: int = 50, timeout: Optional[float] = None) -> dict[str, Any]:
        """Recent audit trace rows (incident review; newest first)."""
        return self.http.get(
            self._abs_url(f"{self._api_prefix}/ops/audit/trace"),
            params={"limit": max(1, int(limit))},
            timeout=timeout or 15,
        )

    def trust_revoke(
        self, tool_name: str, scope: str = "global", *, timeout: Optional[float] = None
    ) -> None:
        """Revoke a server-side trust row."""
        self.http.delete(
            self._trust_url(f"/{tool_name}"),
            params={"scope": scope},
            timeout=timeout or 10,
        )

    def companion_session_archive(
        self, session_id: str, *, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Archive a companion session server-side (ADR-035 item-8 cascade).

        The server tombstones the session, revokes its session-scope trust
        rows, and writes one audit row per revoked grant in ONE transaction.
        Client-side stores must still mark their local records archived;
        this call is the durable lifecycle hook, best-effort at call sites
        (the 86400s TTL remains the crash bound).
        """
        url = (
            f"{self._config.gateway_url}{self._api_prefix}/companion/sessions/{session_id}/archive"
        )
        return self.http.post(url, timeout=timeout or 10)

    def readiness(self) -> dict[str, Any]:
        """Check whether the Gateway is ready to serve requests.

        Uses a direct ``httpx.Client`` because the readiness endpoint maps the
        503 response to a structured payload rather than an error — going
        through :class:`HTTPCore` would wrap it in an ``APIError``.

        Falls back to ``/health`` when ``/ready`` returns 404 (direct UMRS
        sidecar mode without a gateway in front).
        """
        import httpx

        url = f"{self._config.gateway_url}/ready"
        headers = self.http.build_headers()

        try:
            with httpx.Client(
                timeout=httpx.Timeout(10, connect=5),
                verify=self._config.verify_ssl,
            ) as client:
                response = client.get(url, headers=headers)
                readiness = _decode_gateway_readiness(response)
                if readiness is not None:
                    return readiness
                # Fallback: UMRS sidecar exposes /health instead of /ready
                health_url = f"{self._config.gateway_url}/health"
                h_resp = client.get(health_url, headers=headers)
                if h_resp.status_code == 200:
                    h_data = h_resp.json()
                    if isinstance(h_data, dict) and h_data.get("status") == "ok":
                        return {"ready": True, "mode": "umrs-direct"}
                    return {"ready": True, **h_data}
                return {"ready": False, "message": "Neither /ready nor /health responded"}
        except (httpx.TimeoutException, httpx.RequestError) as e:
            from .errors import gateway_unreachable_hint

            raise R4Error(
                f"Failed to reach gateway: {e}. "
                f"{gateway_unreachable_hint(self._config.gateway_url)}"
            )

    def health(self) -> bool:
        """Check if the API is healthy enough to accept traffic."""
        try:
            return bool(self.readiness().get("ready"))
        except R4Error:
            return False

    def site_capabilities(self) -> dict[str, Any]:
        """Fetch site deployment capabilities from the UMRS /health endpoint.

        Returns a dict with keys like ``scheduler_kind``, ``slurm_delegation``,
        ``site_name``, and (when delegation is ``per_user_ssh``)
        ``service_pubkey_path`` and ``slurm_proxy_path``.

        Returns an empty dict on failure.
        """
        import httpx

        url = f"{self._config.gateway_url}/health"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(10, connect=5),
                verify=self._config.verify_ssl,
            ) as client:
                response = client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict):
                        return data.get("site_capabilities", {})
        except Exception:
            pass
        return {}

    def _normalize_endpoint_record(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize UMRS endpoint payloads for the endpoint CLI."""
        from ._endpoint_helpers import normalize_endpoint_record

        return normalize_endpoint_record(data)

    def _normalize_endpoint_provision_response(
        self,
        requested_model: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize UMRS provision responses for endpoint commands."""
        from ._endpoint_helpers import normalize_endpoint_provision_response

        return normalize_endpoint_provision_response(requested_model, data)

    def _build_endpoint_log_summary(self, job: dict[str, Any]) -> dict[str, Any]:
        """Build a human-readable endpoint log summary from UMRS job attempts."""
        from ._endpoint_helpers import build_endpoint_log_summary

        return build_endpoint_log_summary(job)

    @staticmethod
    def _select_active_attempt(job) -> Any | None:
        """Return the active attempt for a job, or the newest known attempt."""
        if not getattr(job, "attempts", None):
            return None

        active_attempt_id = getattr(job, "active_attempt_id", None)
        if active_attempt_id:
            for attempt in job.attempts:
                if attempt.attempt_id == active_attempt_id:
                    return attempt

        return max(job.attempts, key=lambda attempt: attempt.attempt_no)

    @staticmethod
    def _describe_endpoint_progress(
        endpoint: dict[str, Any],
        *,
        job: Any,
        attempt: Any | None,
    ) -> str | None:
        """Map durable-job progress into user-facing endpoint wait detail."""
        status = _lower_str(endpoint.get("status"))
        host = _str_or_empty(endpoint.get("host"))
        job_status = _lower_str(getattr(job, "status", ""))

        # Prefer the live detail from UMRS endpoint registry when it
        # contains real readiness-probe progress (updated during polling).
        ep_detail = _str_or_empty(endpoint.get("detail")).strip()
        if ep_detail and (ep_detail.startswith("probe #") or "readiness" in ep_detail.lower()):
            return ep_detail

        raw_detail = getattr(attempt, "status_detail", None)
        if isinstance(raw_detail, str) and raw_detail.strip():
            return Client._describe_attempt_detail(raw_detail.strip(), ep_detail, status, host)

        return Client._describe_status_fallback(status, host, ep_detail, job_status)

    @staticmethod
    def _readiness_phase_detail(ep_detail: str, status: str, host: str, default: str) -> str:
        """Pick the best wait phrase while a container is coming up."""
        if ep_detail:
            return ep_detail
        if status == "starting" or (host and host != "pending"):
            return "starting container / loading model"
        return default

    @staticmethod
    def _describe_attempt_detail(detail: str, ep_detail: str, status: str, host: str) -> str:
        """Translate a raw attempt status detail into user-facing wait text."""
        lowered = detail.lower()
        if lowered in {"submitting to backend", "submitted to backend"}:
            return "submitting job to backend"
        if "waiting for backend registration" in lowered:
            return "waiting for endpoint registration"
        if "waiting for backend allocation" in lowered:
            return "waiting for node allocation"
        if lowered.startswith("polling readiness"):
            return Client._readiness_phase_detail(
                ep_detail, status, host, "waiting for service readiness"
            )
        if lowered == "running":
            return Client._readiness_phase_detail(ep_detail, status, host, detail)
        return detail

    @staticmethod
    def _describe_status_fallback(
        status: str, host: str, ep_detail: str, job_status: str
    ) -> str | None:
        """Fall back to endpoint/job status when no attempt detail is present."""
        if job_status in {"planned", "queued", "created"}:
            return "waiting in UMRS queue"
        if status == "starting":
            return ep_detail or "starting container / loading model"
        if status == "provisioning":
            if not host or host == "pending":
                return "waiting for node allocation"
            return ep_detail or "starting container / loading model"
        return None

    def _endpoint_progress_fields(self, endpoint: dict[str, Any], job) -> dict[str, Any]:
        """Merge active durable-job attempt progress into endpoint status."""
        attempt = self._select_active_attempt(job)
        progress: dict[str, Any] = {
            "job_status": getattr(job, "status", None),
        }
        if attempt is not None:
            progress.update(
                {
                    "active_attempt_status": attempt.status,
                    "active_attempt_status_detail": attempt.status_detail,
                    "active_attempt_backend": attempt.backend,
                    "active_attempt_no": attempt.attempt_no,
                }
            )

        detail = self._describe_endpoint_progress(endpoint, job=job, attempt=attempt)
        if detail:
            progress["detail"] = detail
        return progress

    # =========================================================================
    # Endpoint Management (Multi-tenant Mini-RiVault)
    # =========================================================================

    def provision_endpoint(
        self,
        model: str,
        tier: str = "user",
        mounts: Optional[dict[str, Any]] = None,
        egress_domains: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        models: Optional[List[str]] = None,
        serving_mode: str = "single",
        loras: Optional[List[dict[str, Any]]] = None,
        max_model_len: Optional[int] = None,
        gpu_count: Optional[int] = None,
        node_count: Optional[int] = None,
        partition: Optional[str] = None,
        nodelist: Optional[str] = None,
        parallel_strategy: Optional[str] = None,
        ttl_sec: Optional[int] = None,
        variant: Optional[str] = None,
        env_vars: Optional[dict[str, str]] = None,
        wait: bool = True,
        access: Optional[List[str]] = None,
        **serving_options: Any,
    ) -> dict[str, Any]:
        """Provision a mini-rivault endpoint.

        ``model`` is the primary served model (back-compat). ``models`` is
        the full list of base models for multi-model endpoints; if omitted,
        defaults to ``[model]``. ``serving_mode`` is one of ``single``,
        ``multi-lora`` or ``co-located`` — NOTE: ``multi-lora`` and
        ``co-located`` are shape-supported in the request contract but the
        serving runtime does not implement them yet; the server rejects them
        with HTTP 501 (audit: dead input). Use ``single``.
        ``loras`` is a list of ``{"name": str, "source": str}`` dicts
        (multi-lora only — currently 501 server-side).

        ``tier``, ``mounts`` and ``egress_domains`` are forwarded to UMRS so
        mini-RiVault provisioning can enable user/project MCP and RAG mounts.

        ``ttl_sec`` sets the endpoint lifetime in seconds; ``0`` requests a
        non-expiring endpoint (never auto-recycled). It is distinct from
        ``timeout``, which only bounds the provisioning HTTP request. When
        ``ttl_sec`` is ``None`` the server applies its default TTL.

        ``access`` (ADR-078) lists who else may use the endpoint:
        ``user:<id>``, ``project:<id>``, ``circle:<proj>/<id>`` or ``system``
        (admin only). ``None`` = the roster recipe's default / owner only.
        """
        # Build payload. Always send `model` (== models[0]) for back-compat
        # with older Gateway/UMRS that only know the single-model schema.
        effective_models = list(models) if models else [model]
        payload = self._build_provision_payload(
            effective_models=effective_models,
            serving_mode=serving_mode,
            tier=tier,
            mounts=mounts,
            egress_domains=egress_domains,
            loras=loras,
            env_vars=env_vars,
            max_model_len=max_model_len,
            gpu_count=gpu_count,
            node_count=node_count,
            partition=partition,
            nodelist=nodelist,
            parallel_strategy=parallel_strategy,
            ttl_sec=ttl_sec,
            variant=variant,
            serving_options=serving_options,
            wait=wait,
        )
        if access is not None:
            payload["access"] = list(access)
        effective_timeout = self._resolve_provision_timeout(wait, timeout)

        url = f"{self._config.gateway_url}{self._api_prefix}/endpoints/provision"
        try:
            data = self.http.post(url, json_body=payload, timeout=effective_timeout)
            return self._normalize_endpoint_provision_response(effective_models[0], data)
        except R4Error as e:
            raise self._provision_error(e) from e

    @staticmethod
    def _build_provision_payload(
        *,
        effective_models: List[str],
        serving_mode: str,
        tier: str,
        mounts: Optional[dict[str, Any]],
        egress_domains: Optional[List[str]],
        loras: Optional[List[dict[str, Any]]],
        env_vars: Optional[dict[str, str]],
        max_model_len: Optional[int],
        gpu_count: Optional[int],
        node_count: Optional[int],
        partition: Optional[str],
        nodelist: Optional[str],
        parallel_strategy: Optional[str],
        ttl_sec: Optional[int],
        variant: Optional[str],
        serving_options: dict[str, Any],
        wait: bool,
    ) -> dict[str, Any]:
        """Assemble the provision request payload from optional parameters."""
        payload: dict[str, Any] = {
            "model": effective_models[0],
            "models": effective_models,
            "serving_mode": serving_mode,
        }
        if tier != "user":
            payload["tier"] = tier
        # Included only when a truthy value is supplied.
        for key, value in (
            ("mounts", mounts),
            ("egress_domains", egress_domains),
            ("loras", loras),
            ("env_vars", env_vars),
        ):
            if value:
                payload[key] = value
        # Included whenever explicitly set (``ttl_sec=0`` means "no expiry").
        for key, value in (
            ("max_model_len", max_model_len),
            ("gpu_count", gpu_count),
            ("node_count", node_count),
            ("partition", partition),
            ("nodelist", nodelist),
            ("parallel_strategy", parallel_strategy),
            ("ttl_sec", ttl_sec),
            ("variant", variant),
        ):
            if value is not None:
                payload[key] = value
        _apply_endpoint_serving_options(payload, serving_options)
        if not wait:
            payload["wait"] = False
        return payload

    @staticmethod
    def _resolve_provision_timeout(wait: bool, timeout: Optional[float]) -> Optional[float]:
        """Resolve the HTTP timeout for a provision request.

        When ``wait`` is False we are only submitting the job; the gateway →
        UMRS sync segment (resolve model entry, allocate port, sbatch,
        register endpoint, schedule finalize task) can still take a while
        under parallel load. Default to 300s, overridable via env so cluster
        operators can tune for slow Slurm controllers.
        """
        if wait:
            return timeout
        try:
            submit_default = float(os.environ.get("R4_PROVISION_SUBMIT_TIMEOUT_SEC", "300"))
        except (TypeError, ValueError):
            submit_default = 300.0
        return timeout if timeout is not None else submit_default

    @staticmethod
    def _provision_error(e: R4Error) -> R4Error:
        """Build a provision failure error, preserving server-side detail.

        A 429 (the failing re-provision cap, 2026-10-23) arrives as a
        RateLimitError with no body: its message carries the server's
        explanation and ``retry_after`` the wait, so both are kept.
        """
        detail = _provision_failure_detail(e)
        message = f"Failed to provision endpoint{f': {detail}' if detail else ''}"
        if isinstance(e, RateLimitError):
            return RateLimitError(message, retry_after=e.retry_after)
        return R4Error(message)

    def upgrade_endpoint(
        self,
        endpoint_id: str,
        model_path: str,
        wait: bool = False,
        timeout: int = 900,
    ) -> dict[str, Any]:
        """Rolling model-version upgrade of a running endpoint.

        Provisions replacement, waits for readiness, flips the endpoint
        registry record, and retires the old instance. Concurrency-gate
        lands in a follow-up wave.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/endpoints/{endpoint_id}/upgrade"
        payload: dict[str, Any] = {"model_path": model_path, "wait": wait}
        try:
            return self.http.post(url, json=payload, timeout=timeout)
        except Exception as exc:
            raise R4Error(f"Failed to upgrade endpoint: {exc}") from exc

    def set_endpoint_access(self, instance_id: str, access: List[str]) -> dict[str, Any]:
        """Replace who may use an endpoint beyond its owning project (ADR-078).

        ``[]`` makes it private to the owning project again. Only the owning
        project or an admin may change it; ``system`` is admin-only.
        """
        url = f"{self._config.gateway_url}{self._api_prefix}/endpoints/{instance_id}/access"
        return self.http.put(url, json_body={"access": list(access)})

    def stop_endpoint(
        self,
        instance_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Stop a mini-rivault endpoint.

        Retries up to 3 times on HTTP 404 with a short backoff: a job
        that was just accepted by UMRS may not yet be visible to the
        deprovision route while registration completes (race seen when
        ``r4 down`` runs immediately after a parallel ``r4 up``).
        """
        import time as _time

        url = f"{self._config.gateway_url}{self._api_prefix}/endpoints/{instance_id}/deprovision"
        params = {"force": "true"} if force else {}

        last_exc: R4Error | None = None
        for attempt in range(3):
            try:
                data = self.http.delete(url, params=params)
                if not data:
                    return {
                        "instance_id": instance_id,
                        "job_id": instance_id,
                        "status": "deprovisioned",
                    }
                return data
            except R4Error as e:
                last_exc = e
                status_code = e.details.get("status_code") if isinstance(e.details, dict) else None
                if status_code == 404 and attempt < 2:
                    _time.sleep(2.0)
                    continue
                raise R4Error(f"Failed to stop endpoint: {e.message}", details=e.details)
        # Unreachable in practice; loop either returns or raises.
        if last_exc is not None:
            raise R4Error(f"Failed to stop endpoint: {last_exc.message}", details=last_exc.details)
        raise R4Error("Failed to stop endpoint")

    def list_endpoints(
        self,
        tier: Optional[str] = None,
        include_stopped: bool = False,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """List mini-rivault endpoints."""
        base = f"{self._config.gateway_url}{self._api_prefix}/endpoints"
        if include_stopped:
            url = base
            params: dict[str, Any] = {}
        else:
            # NGINX rewrites GET /gw/endpoints -> /umrs/endpoints and ignores
            # include_stopped.  /gw/endpoints/active -> /umrs/endpoints/active.
            url = f"{base}/active"
            params = {}

        try:
            data = self.http.get(url, params=params, timeout=timeout)
            endpoints = [
                self._normalize_endpoint_record(endpoint) for endpoint in data.get("endpoints", [])
            ]
            if tier:
                logger.warning("Current UMRS endpoint listing ignores the tier filter")
            return {"endpoints": endpoints, "total": len(endpoints)}
        except R4Error as e:
            raise R4Error(f"Failed to list endpoints: {e.message}", details=e.details)

    def get_endpoint_status(
        self,
        instance_id: str,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Get status of a mini-rivault endpoint."""
        url = f"{self._config.gateway_url}{self._api_prefix}/endpoints/{instance_id}"
        try:
            data = self.http.get(url, timeout=timeout)
            normalized = self._normalize_endpoint_record(data)

            status = str(normalized.get("status") or "").lower()
            if status not in {"ready", "degraded", "terminated", "expired"}:
                try:
                    job = self.jobs.get(instance_id)
                except R4Error as job_exc:
                    logger.debug(
                        "Endpoint job detail lookup failed for %s: %s",
                        instance_id,
                        job_exc,
                    )
                else:
                    normalized.update(self._endpoint_progress_fields(normalized, job))

            return normalized
        except R4Error as e:
            raise R4Error(f"Failed to get endpoint status: {e.message}", details=e.details)

    def get_endpoint_logs(
        self,
        instance_id: str,
        tail: int = 100,
        follow: bool = False,
    ) -> Union[dict[str, Any], Iterator[str]]:
        """Get logs from a mini-rivault endpoint."""
        if follow:
            raise R4Error(
                "Follow mode is not supported by the current UMRS job API. "
                "Use the non-follow endpoint logs command to inspect job attempts and log URIs."
            )

        job = self.jobs.get(instance_id)
        job_data = {
            "job_id": job.job_id,
            "status": job.status,
            "job_type": job.job_type,
            "attempts": [
                {
                    "attempt_no": attempt.attempt_no,
                    "status": attempt.status,
                    "backend": attempt.backend,
                    "status_detail": attempt.status_detail,
                    "exit_reason": attempt.exit_reason,
                    "log_uri": attempt.log_uri,
                }
                for attempt in job.attempts[:tail]
            ],
        }
        return self._build_endpoint_log_summary(job_data)

    def close(self):
        """Close the client and release resources."""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


# Explicit branded alias for callers that prefer `R4Client`.
R4Client = Client
