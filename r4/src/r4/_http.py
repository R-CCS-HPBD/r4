"""Shared HTTP core for the R4 SDK.

Consolidates the ``httpx`` request/response/error plumbing that was previously
duplicated across :mod:`r4.client`, :mod:`r4.jobs`, :mod:`r4.rag`,
:mod:`r4.workflows`, :mod:`r4.models` and :mod:`r4.mcp`.

Public surface (intentionally tiny):

- :class:`HTTPCore`  – lazy :class:`httpx.Client`, unified error mapping,
  auth header injection, per-request correlation ID.
- :func:`map_httpx_error` – convert httpx exceptions to :mod:`r4.errors` types.

This module has **no** dependency on any other r4 sub-client; it takes
:class:`r4.config.Config` and stays purely transport-level so there are no
import cycles.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, NoReturn, Optional

from .errors import (
    APIError,
    AuthenticationError,
    R4ConnectionError,
    R4Error,
    R4TimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

_USER_AGENT = "r4-sdk"


#: Memo for probe_api_prefix, keyed by exact base URL — one probe per
#: deployment per process (tests: reset between mode flips).
_API_PREFIX_CACHE: dict[str, str] = {}


def probe_api_prefix(config_or_url: Any) -> str:
    """``/gw`` behind the edge gateway, ``/umrs`` when pointed at UMRS directly.

    A direct UMRS sidecar answers 404 on ``/ready`` and 200 on ``/health``;
    the edge answers its own readiness route. Any failure defaults to the
    edge prefix (the historical default). Extracted from
    ``Client._api_prefix`` (2026-10-20, plan H-10.2) so HTTP-level clients
    without a full :class:`~r4.client.Client` (FilesClient, VisionClient,
    model-routing helpers, the LiteLLM provider) agree on the mode instead
    of hard-coding ``/gw``. Memoized per base URL in ``_API_PREFIX_CACHE``.
    """
    gateway = str(getattr(config_or_url, "gateway_url", "") or config_or_url or "").rstrip("/")
    if not gateway:
        return "/gw"
    cached = _API_PREFIX_CACHE.get(gateway)
    if cached is not None:
        return cached
    prefix = _probe_api_prefix_uncached(gateway, getattr(config_or_url, "verify_ssl", True))
    _API_PREFIX_CACHE[gateway] = prefix
    return prefix


def _probe_api_prefix_uncached(gateway: str, verify: Any) -> str:
    """One readiness/health probe of *gateway* (transport for probe_api_prefix)."""
    try:
        import httpx

        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), verify=verify) as c:
            r = c.get(f"{gateway}/ready")
            if r.status_code == 404:
                h = c.get(f"{gateway}/health")
                if h.status_code == 200:
                    return "/umrs"
    except Exception:  # noqa: BLE001 - unreachable means the edge default
        pass
    return "/gw"


_WEB_LOGIN_PATH = "/auth/web-login"


def _landed_on_web_login(response: Any) -> bool:
    """True when redirects ended on the browser login page.

    A redirect that leaves the TLS edge (e.g. an absolute ``http://host:port``
    Location from the gateway's own listener) drops the Authorization header;
    the gateway then answers the browser login form with 200. Treating that
    HTML as a successful API reply made ``r4 catalog list`` print "No
    scientific catalogs." (production enablement 2026-09-25).
    """
    if not getattr(response, "history", None):
        return False  # no redirect: a path that merely contains the text is data
    url = getattr(response, "url", None)
    return str(getattr(url, "path", "") or "").rstrip("/").endswith(_WEB_LOGIN_PATH)


def raise_if_web_login(response: Any) -> None:
    """Refuse a response whose redirects ended on the browser login page."""
    if _landed_on_web_login(response):
        raise AuthenticationError(
            "the gateway answered with its browser login page (a redirect "
            "dropped the credentials) — no API result was returned",
            status_code=401,
        )


#: A public model provider (an OpenAI-compatible source) refused its API key.
PROVIDER_AUTH_HINT = (
    "the model provider rejected this source's API key (your r4 login is not "
    "involved) — set a valid key: printf '%s' \"$KEY\" | r4 source set <source> --api-key-stdin"
)


def _looks_like_html(response: Any, text: str) -> bool:
    headers = getattr(response, "headers", None)
    try:
        ctype = str(headers.get("content-type") or "") if headers is not None else ""
    except Exception:  # noqa: BLE001 — header access is best-effort
        ctype = ""
    head = text.lstrip()[:15].lower()
    return "text/html" in ctype.lower() or head.startswith(("<!doctype html", "<html"))


def wrap_non_json(response: Any, text: str) -> dict[str, Any]:
    """A 2xx body that is not JSON: plain text stays data (``{"_raw": text}``),
    an HTML page is an error.

    A proxy or single-sign-on portal in front of the gateway answers every
    path with a 200 HTML page; wrapped as data, about 20 commands printed
    "No ... found" with exit 0 and ``--json`` printed ``{"_raw": "<html..."}``
    (cli lane H1, 2026-10-21).
    """
    if not text:
        return {}
    if _looks_like_html(response, text):
        raise APIError(
            "the server answered with an HTML page, not an API result (a proxy or "
            "sign-in portal in front of the service?)",
            status_code=getattr(response, "status_code", None),
            response_body=text[:400],
        )
    return {"_raw": text}


def _new_correlation_id() -> str:
    """Return a short, human-friendly correlation id for a request."""
    return uuid.uuid4().hex[:12]


def raise_for_status(response: Any) -> None:
    """Map an ``httpx.Response`` status code to the r4 error hierarchy.

    The server's own explanation rides on every error, auth and rate limits
    included ("Authentication failed: Invalid API key").
    """
    status = getattr(response, "status_code", 0)
    if status < 400:
        return
    body = _error_body(response)
    if status in (401, 403, 429):
        raise _auth_or_rate_error(status, response, _server_detail(body))
    raise APIError(
        _api_error_message(status, body),
        status_code=status,
        response_body=body,
        error_code=_platform_error_code(response),
    )


def _error_body(response: Any) -> Optional[str]:
    """An error response's body text. A streamed response is read first: its
    ``.text`` raised before the read, so every streamed error (the chat
    path) lost the provider's reason (companion sweep 2026-10-21)."""
    try:
        read = getattr(response, "read", None)
        if callable(read):
            read()
        text = response.text
    except Exception:  # noqa: BLE001 — the reason is best-effort
        return None
    return text if isinstance(text, str) else None


def _with_detail(base: str, detail: str) -> str:
    return f"{base}: {detail}" if detail else base


def _auth_or_rate_error(status: int, response: Any, detail: str) -> R4Error:
    if status == 401:
        return AuthenticationError(_with_detail("Authentication failed", detail), status_code=401)
    if status == 403:
        return AuthenticationError(_with_detail("Access denied (403)", detail), status_code=403)
    retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    try:
        retry_int = int(retry_after) if retry_after else None
    except (TypeError, ValueError):
        retry_int = None
    return RateLimitError(_with_detail("Rate limit exceeded", detail), retry_after=retry_int)


def _platform_error_code(response: Any) -> Optional[str]:
    """Typed taxonomy code from the ``x-platform-error-code`` header (best-effort)."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("x-platform-error-code")
    except Exception:  # noqa: BLE001 — header access is best-effort
        return None


def _api_error_message(status: int, body: Optional[str]) -> str:
    """``API error: <status>`` plus the server's own explanation when it sent one.

    UMRS/Gateway error bodies carry ``message`` (structured errors) or
    ``detail`` (FastAPI); surfacing it is what turns "API error: 400" into an
    actionable line (WP-07: the loop-start guidance was invisible to CLI users).
    """
    return _with_detail(f"API error: {status}", _server_detail(body))


def _server_detail(body: Optional[str]) -> str:
    """The explanation in a JSON error body ("" when there is none).

    UMRS/Gateway bodies carry ``message`` or ``detail``; OpenAI-compatible
    providers (OpenAI, vLLM, LiteLLM) carry ``{"error": {"message": ...}}``,
    which was dropped, so "maximum context length is 8192 tokens" read as a
    bare "API error: 400".
    """
    if not body:
        return ""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    detail = data.get("message") or data.get("detail") or _openai_error_message(data)
    return _detail_text(detail).strip()[:400]


def _detail_text(detail: Any) -> str:
    """One error detail as text. A structured UMRS detail
    (``{"message": ..., <fields>}``) reads as its message, not as JSON."""
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    if isinstance(detail, (dict, list)):
        return json.dumps(detail)
    return str(detail or "")


def _openai_error_message(data: dict[str, Any]) -> Any:
    error = data.get("error")
    return error.get("message") if isinstance(error, dict) else error


def map_httpx_error(exc: Exception, *, gateway_url: Optional[str] = None) -> R4Error:
    """Translate an httpx exception into the r4 error hierarchy.

    When ``gateway_url`` is provided, connect/timeout errors are decorated
    with an actionable hint (bug.memo H1, 2026-04-25).
    """
    import httpx

    from .errors import gateway_unreachable_hint

    hint = f" {gateway_unreachable_hint(gateway_url)}" if gateway_url else ""

    if isinstance(exc, httpx.TimeoutException):
        return R4TimeoutError(f"Request timed out: {exc}.{hint}")
    if isinstance(exc, httpx.ConnectError):
        return R4ConnectionError(f"Connection failed: {exc}.{hint}")
    if isinstance(exc, httpx.RequestError):
        return R4Error(f"Request failed: {exc}.{hint}")
    return R4Error(str(exc))


def _chunk_content(payload: str) -> str:
    """The text delta of one SSE ``data:`` payload ("" when it carries none).

    The usage chunk ``stream_options.include_usage`` asks for has
    ``"choices": []``; indexing it raised IndexError, which surfaced as the
    turn error "list index out of range" (companion sweep 2026-10-21).
    """
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    delta = first.get("delta") if isinstance(first, dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) else ""


class HTTPCore:
    """Thin, reusable HTTP layer shared by every sub-client.

    Intentionally stateless across requests: a fresh ``httpx.Client`` is opened
    per call to keep connection lifecycles simple and compatible with the
    existing streaming patterns. If/when callers profile this as a hotspot,
    a pooled client can be added behind the same surface.
    """

    def __init__(self, config: Any, *, sdk_version: str = "0.1.0") -> None:
        self._config = config
        self._sdk_version = sdk_version

    # --- header construction -------------------------------------------------

    def build_headers(
        self,
        extra: Optional[Mapping[str, str]] = None,
        *,
        correlation_id: Optional[str] = None,
    ) -> dict[str, str]:
        """Assemble request headers (auth + tracing + per-call extras)."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            # The gateway answers a missing/expired credential with a JSON 401
            # only when the client accepts JSON; otherwise it 302s to the
            # browser login page (2026-09-25). Callers may override.
            "Accept": "application/json",
            "User-Agent": f"{_USER_AGENT}/{self._sdk_version}",
            "X-Correlation-ID": correlation_id or _new_correlation_id(),
        }

        cfg = self._config
        if getattr(cfg, "api_key", None):
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        elif not getattr(cfg, "bound_source", None):
            # Fall back to stored OIDC tokens etc. — only for the profile's own
            # deployment: a config bound to another source never borrows the
            # profile's login (onboarding plan S1). This import is local so the
            # module stays importable without the auth extras.
            try:
                from .auth import get_auth_manager

                auth_header = get_auth_manager(getattr(cfg, "profile", "default")).get_auth_header()
                if auth_header:
                    headers.update(auth_header)
            except Exception:  # pragma: no cover - best-effort
                logger.debug("auth manager unavailable; sending unauthenticated request")

        if getattr(cfg, "project_id", None):
            headers["X-Project-ID"] = cfg.project_id
        if getattr(cfg, "umrs_username", None):
            headers["X-User-ID"] = cfg.umrs_username
        # ADR-108 D-3: a work level's scope token, inside acting_in_scope.
        from .scopes import scope_headers

        headers.update(scope_headers())

        if extra:
            headers.update(extra)
        return headers

    def build_provider_headers(
        self,
        extra: Optional[Mapping[str, str]] = None,
        *,
        correlation_id: Optional[str] = None,
    ) -> dict[str, str]:
        """Assemble headers for direct public-provider requests.

        Unlike :meth:`build_headers`, this intentionally omits Gateway auth,
        stored OIDC/API-key fallback, project IDs, and UMRS usernames.
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"{_USER_AGENT}/{self._sdk_version}",
            "X-Correlation-ID": correlation_id or _new_correlation_id(),
        }
        if extra:
            headers.update(extra)
        return headers

    def _add_login_hint(self, err: R4Error, *, provider_request: bool = False) -> None:
        """A 401/403 from a bound source says how to log in to THAT source.

        The generic hint (``r4 auth login``) would replace the active login;
        a config bound to another source (onboarding plan S1) gets
        :func:`r4.source_context.login_hint` instead. A public model
        provider's 401/403 is about the source's own API key: the gateway
        sign-in remedy (/connect, ``r4 auth login``) cannot fix it.
        """
        if not isinstance(err, AuthenticationError):
            return
        if provider_request:
            err.details["hint"] = err.details["tui_hint"] = PROVIDER_AUTH_HINT
            return
        if not getattr(self._config, "bound_source", None):
            return
        try:
            from .source_context import bound_login_hint

            err.details["hint"] = bound_login_hint(self._config)
        except Exception:  # noqa: BLE001 — the hint is advisory
            return

    # --- request primitives --------------------------------------------------

    def _client(self, timeout: Optional[float] = None):
        import httpx

        read_timeout = timeout if timeout is not None else getattr(self._config, "timeout", 300)
        # Short connect timeout (bug.memo H4, 2026-04-25): fail fast when the
        # gateway is unreachable instead of waiting for the read timeout.
        connect_timeout = min(
            getattr(self._config, "connect_timeout", 5.0),
            float(read_timeout),
        )
        return httpx.Client(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            verify=getattr(self._config, "verify_ssl", True),
            # NGINX same-host redirects (API migration waves / trailing-slash
            # moves) are routinely safe to follow for API JSON; the live feed
            # no longer freezes after reading a 302 + HTML wrapper.
            follow_redirects=True,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        correlation_id: Optional[str] = None,
        provider_request: bool = False,
        cancel_check: Optional[Callable[[], None]] = None,
        register_abort: Optional[Callable[[Callable[[], None]], None]] = None,
    ) -> dict[str, Any]:
        """Issue an HTTP request and return parsed JSON (or ``{}`` on empty).

        Raises a subclass of :class:`R4Error` on failure. Non-JSON 2xx bodies
        are wrapped in ``{"_raw": text}`` so callers always get a dict.

        Uses method-specific ``httpx.Client`` calls (``client.get``,
        ``client.post`` and so on) rather than the generic ``client.request``
        so that existing tests which mock the per-method entry points keep
        working.
        """
        cid = correlation_id or _new_correlation_id()
        header_builder = self.build_provider_headers if provider_request else self.build_headers
        hdrs = header_builder(headers, correlation_id=cid)
        method_up = method.upper()

        try:
            with self._client(timeout) as client:
                if cancel_check:
                    return self._request_cancellable(
                        client,
                        method_up,
                        url,
                        json_body,
                        params,
                        hdrs,
                        cancel_check,
                        register_abort,
                    )
                response = self._dispatch_request(client, method_up, url, json_body, params, hdrs)
                raise_for_status(response)
                return self._parse_response_json(response)
        except R4Error as err:
            self._add_login_hint(err, provider_request=provider_request)
            raise
        except Exception as exc:
            self._raise_mapped_error(
                exc, cid=cid, provider_request=provider_request, cancel_check=cancel_check
            )

    def _dispatch_request(
        self,
        client: Any,
        method_up: str,
        url: str,
        json_body: Any,
        params: Optional[Mapping[str, Any]],
        hdrs: Mapping[str, str],
    ) -> Any:
        """Call the method-specific httpx entry point so mocked clients keep working."""
        if method_up == "GET":
            return client.get(url, params=params, headers=hdrs)
        if method_up == "POST":
            return client.post(url, json=json_body, params=params, headers=hdrs)
        if method_up == "PUT":
            return client.put(url, json=json_body, params=params, headers=hdrs)
        if method_up == "DELETE":
            return client.delete(url, params=params, headers=hdrs)
        if method_up == "PATCH":
            return client.patch(url, json=json_body, params=params, headers=hdrs)
        return client.request(method_up, url, json=json_body, params=params, headers=hdrs)

    def _parse_response_json(self, response: Any) -> dict[str, Any]:
        """Parse a non-streaming response body into a dict."""
        # 204 No Content: no body to parse.
        if getattr(response, "status_code", 200) == 204:
            return {}
        raise_if_web_login(response)
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            text = getattr(response, "text", "") or ""
            return wrap_non_json(response, text)

    def _request_cancellable(
        self,
        client: Any,
        method_up: str,
        url: str,
        json_body: Any,
        params: Optional[Mapping[str, Any]],
        hdrs: Mapping[str, str],
        cancel_check: Callable[[], None],
        register_abort: Optional[Callable[[Callable[[], None]], None]],
    ) -> dict[str, Any]:
        """Issue a request whose body is streamed so cancellation can interrupt it."""
        with client.stream(
            method_up,
            url,
            json=json_body,
            params=params,
            headers=hdrs,
        ) as response:
            cancel_check()
            raise_for_status(response)
            raise_if_web_login(response)
            if register_abort is not None:
                register_abort(response.close)
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                cancel_check()
                if chunk:
                    body.extend(chunk)
        if not body:
            return {}
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return wrap_non_json(response, body.decode("utf-8", errors="replace"))

    def _raise_mapped_error(
        self,
        exc: Exception,
        *,
        cid: str,
        provider_request: bool,
        cancel_check: Optional[Callable[[], None]],
    ) -> NoReturn:
        """Map an httpx error to the r4 hierarchy, decorate it, and raise."""
        if cancel_check is not None:
            cancel_check()
        # Decorate the error with the correlation id so logs can be joined.
        # Only attach the gateway hint when the request was NOT aimed at a
        # public provider endpoint — otherwise the hint is misleading.
        gw_url = None if provider_request else getattr(self._config, "gateway_url", None)
        err = map_httpx_error(exc, gateway_url=gw_url)
        err.details.setdefault("correlation_id", cid)
        raise err

    def get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, json_body: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", url, json_body=json_body, **kwargs)

    def patch(self, url: str, json_body: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.request("PATCH", url, json_body=json_body, **kwargs)

    def put(self, url: str, json_body: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.request("PUT", url, json_body=json_body, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("DELETE", url, **kwargs)

    # --- streaming -----------------------------------------------------------

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        correlation_id: Optional[str] = None,
        provider_request: bool = False,
        cancel_check: Optional[Callable[[], None]] = None,
        register_abort: Optional[Callable[[Callable[[], None]], None]] = None,
    ):
        """Open an SSE/streaming HTTP request.

        Yields the raw ``httpx.Response`` so callers can iterate lines or
        bytes as appropriate. Errors are mapped to the r4 hierarchy.
        """
        cid = correlation_id or _new_correlation_id()
        header_builder = self.build_provider_headers if provider_request else self.build_headers
        hdrs = header_builder(headers, correlation_id=cid)

        try:
            with self._client(timeout) as client:
                with client.stream(method, url, json=json_body, headers=hdrs) as response:
                    raise_for_status(response)
                    raise_if_web_login(response)
                    if register_abort is not None:
                        register_abort(response.close)
                    if cancel_check is not None:
                        cancel_check()
                    yield response
        except R4Error as err:
            self._add_login_hint(err, provider_request=provider_request)
            raise
        except Exception as exc:
            if cancel_check is not None:
                cancel_check()
            gw_url = None if provider_request else getattr(self._config, "gateway_url", None)
            err = map_httpx_error(exc, gateway_url=gw_url)
            err.details.setdefault("correlation_id", cid)
            raise err

    def iter_sse_content(
        self,
        url: str,
        *,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        correlation_id: Optional[str] = None,
        provider_request: bool = False,
        cancel_check: Optional[Callable[[], None]] = None,
        register_abort: Optional[Callable[[Callable[[], None]], None]] = None,
    ) -> Iterator[str]:
        """Iterate the ``content`` deltas from an OpenAI-compatible SSE stream.

        Emits empty strings for keep-alives/non-content events so callers may
        decide whether to display them. Terminates on ``data: [DONE]``.
        """
        with self.stream(
            "POST",
            url,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
            correlation_id=correlation_id,
            provider_request=provider_request,
            cancel_check=cancel_check,
            register_abort=register_abort,
        ) as response:
            for line in response.iter_lines():
                if cancel_check is not None:
                    cancel_check()
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    return
                content = _chunk_content(payload)
                if content:
                    yield content

    def iter_sse_chunks(
        self,
        url: str,
        *,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        correlation_id: Optional[str] = None,
        provider_request: bool = False,
        cancel_check: Optional[Callable[[], None]] = None,
        register_abort: Optional[Callable[[Callable[[], None]], None]] = None,
        on_done: Optional[Callable[[], None]] = None,
    ) -> Iterator[dict]:
        """Iterate raw parsed SSE chunks from an OpenAI-compatible stream.

        Each yielded dict has the full chunk structure. The final chunk
        typically carries ``usage`` data. Terminates on ``data: [DONE]``.

        ``cancel_check`` runs between chunks; ``register_abort`` gets the
        response-close callable so an external token can break mid-stream.
        ``cancel_check`` also reaches :meth:`stream`: the read error a
        cancel-closed response raises is then a turn cancel, not an
        ``R4Error("Request failed")`` that skipped the stopped-turn record
        (stabilization sweep 2026-10-21, chat#9). ``on_done`` runs when the
        ``data: [DONE]`` terminator arrives, so a caller can tell a finished
        stream from one the server just stopped sending (chat#11).
        """
        with self.stream(
            "POST",
            url,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
            correlation_id=correlation_id,
            provider_request=provider_request,
            cancel_check=cancel_check,
        ) as response:
            if register_abort is not None:
                register_abort(response.close)
            for line in response.iter_lines():
                if cancel_check is not None:
                    cancel_check()
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    if on_done is not None:
                        on_done()
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                yield chunk


__all__ = [
    "HTTPCore",
    "map_httpx_error",
    "raise_for_status",
]
