"""AWS Bedrock provider — native access to Bedrock models.

Supports two authentication modes:

1. **SigV4** (default): Uses ``boto3`` and standard AWS credentials
   (env vars, ~/.aws/credentials, IAM role, etc.).
2. **Bearer token**: When ``api_key`` is provided, calls the Bedrock
   Converse API directly with ``Authorization: Bearer <token>``.

Uses the Bedrock *Converse* API, translating between the OpenAI-style
message format used by r4 and the Bedrock native format.

Usage via CLI::

    # SigV4 mode (requires boto3 + AWS credentials)
    r4 up --public bedrock --model anthropic.claude-sonnet-4-6

    # Bearer token mode
    r4 up --public bedrock --model anthropic.claude-sonnet-4-6 \\
          --api-key "$AWS_BEARER_TOKEN_BEDROCK" --aws-region us-east-2

The ``--public bedrock`` form sets ``endpoint_provider=bedrock`` in the
active profile so subsequent ``r4 chat`` calls route through this provider.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterator, List, Optional

from . import (
    ModelCapabilities,
    Provider,
    ProviderHealth,
    ProviderStatus,
)

logger = logging.getLogger(__name__)

# Default region when neither config nor environment specifies one.
_DEFAULT_REGION = "us-east-1"

_COMMON_BEDROCK_MODEL_IDS: tuple[str, ...] = (
    "anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-sonnet-4-6",
    "anthropic.claude-3-sonnet-20240229-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-pro-v1:0",
    "meta.llama3-1-70b-instruct-v1:0",
)


def common_bedrock_model_ids(default_model: str | None = None) -> list[str]:
    """Return common Bedrock model IDs, keeping the configured model first."""
    models: list[str] = []
    if default_model:
        models.append(default_model)
    for model_id in _COMMON_BEDROCK_MODEL_IDS:
        if model_id not in models:
            models.append(model_id)
    return models


def _get_bedrock_client(region: str | None = None):
    """Create a ``bedrock-runtime`` boto3 client."""
    import boto3

    return boto3.client("bedrock-runtime", region_name=region or _DEFAULT_REGION)


# ---------------------------------------------------------------------------
# Bearer-token HTTP helpers
# ---------------------------------------------------------------------------


def _bedrock_base_url(region: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com"


def _resolve_model_id(model_id: str) -> str:
    """Resolve a bare foundation model ID to an inference profile ID.

    Newer Bedrock models (e.g. ``anthropic.claude-sonnet-4-6``) cannot be
    invoked on-demand with the bare model ID — they require an inference
    profile such as ``us.anthropic.claude-sonnet-4-6``.

    This helper adds the ``us.`` prefix when the model ID looks like a bare
    foundation model.  Model IDs that already carry a region prefix
    (``us.``, ``eu.``, ``global.``) or are ARNs are returned unchanged.
    """
    if model_id.startswith(("us.", "eu.", "ap.", "global.", "arn:")):
        return model_id
    return f"us.{model_id}"


def _bearer_converse(
    region: str,
    model_id: str,
    api_key: str,
    converse_messages: list[dict],
    system_prompt: str | None,
    inference_config: dict[str, Any],
) -> dict:
    """Call the Bedrock Converse API via direct HTTPS with bearer token."""
    import httpx

    resolved = _resolve_model_id(model_id)
    url = f"{_bedrock_base_url(region)}/model/{resolved}/converse"

    body: dict[str, Any] = {
        "messages": converse_messages,
        "inferenceConfig": inference_config,
    }
    if system_prompt:
        body["system"] = [{"text": system_prompt}]

    response = httpx.post(
        url,
        json=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=300,
    )
    if response.status_code != 200:
        error_body = response.text
        try:
            err = response.json()
            msg = err.get("message", err.get("Message", error_body))
        except Exception:
            msg = error_body
        raise RuntimeError(f"Bedrock Converse API error (HTTP {response.status_code}): {msg}")
    return response.json()


def _bearer_converse_stream(
    region: str,
    model_id: str,
    api_key: str,
    converse_messages: list[dict],
    system_prompt: str | None,
    inference_config: dict[str, Any],
) -> Iterator[str]:
    """Stream from the Bedrock ConverseStream API with bearer token.

    The response uses the AWS EventStream binary protocol.  We parse it
    with ``botocore.eventstream`` when available, falling back to a
    non-streaming call otherwise.
    """
    import httpx

    resolved = _resolve_model_id(model_id)
    url = f"{_bedrock_base_url(region)}/model/{resolved}/converse-stream"

    body: dict[str, Any] = {
        "messages": converse_messages,
        "inferenceConfig": inference_config,
    }
    if system_prompt:
        body["system"] = [{"text": system_prompt}]

    with httpx.stream(
        "POST",
        url,
        json=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.amazon.eventstream",
        },
        timeout=300,
    ) as response:
        if response.status_code != 200:
            error_body = response.read().decode()
            raise RuntimeError(
                f"Bedrock ConverseStream API error (HTTP {response.status_code}): {error_body}"
            )

        try:
            from botocore.eventstream import EventStreamBuffer
        except ImportError:
            # Without botocore we cannot parse event-stream; fall back.
            logger.warning("botocore not available — falling back to non-streaming")
            result = _bearer_converse(
                region,
                model_id,
                api_key,
                converse_messages,
                system_prompt,
                inference_config,
            )
            output = result.get("output", {})
            message = output.get("message", {})
            for part in message.get("content", []):
                text = part.get("text", "")
                if text:
                    yield text
            return

        buf = EventStreamBuffer()
        for chunk in response.iter_bytes():
            buf.add_data(chunk)
            for event in buf:
                payload = json.loads(event.payload)
                delta = payload.get("contentBlockDelta", {}).get("delta", {}).get("text", "")
                if delta:
                    yield delta


# ---------------------------------------------------------------------------
# Message format translation
# ---------------------------------------------------------------------------


def _openai_messages_to_converse(
    messages: List[Dict[str, Any]],
) -> tuple[list[dict], str | None]:
    """Translate OpenAI-style messages to Bedrock Converse format.

    Returns ``(converse_messages, system_prompt)``.
    """
    system_prompt: str | None = None
    converse_messages: list[dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_prompt = content if isinstance(content, str) else str(content)
            continue

        # Map assistant → assistant, everything else → user.
        converse_role = "assistant" if role == "assistant" else "user"

        # Handle multimodal content (list of parts).
        if isinstance(content, list):
            parts: list[dict] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append({"text": part.get("text", "")})
                elif isinstance(part, str):
                    parts.append({"text": part})
                # Skip unsupported part types gracefully.
            if parts:
                converse_messages.append({"role": converse_role, "content": parts})
        elif isinstance(content, str) and content:
            converse_messages.append(
                {
                    "role": converse_role,
                    "content": [{"text": content}],
                }
            )

    return converse_messages, system_prompt


def _converse_response_to_openai(
    response: dict,
    model: str,
) -> Dict[str, Any]:
    """Translate a Bedrock Converse response to OpenAI chat completions format."""
    output = response.get("output", {})
    message = output.get("message", {})
    content_parts = message.get("content", [])

    # Concatenate text parts.
    text = "".join(part.get("text", "") for part in content_parts if "text" in part)

    usage = response.get("usage", {})
    stop_reason = response.get("stopReason", "end_turn")

    # Map Bedrock stop reasons to OpenAI finish reasons.
    finish_reason_map = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "content_filtered": "content_filter",
    }

    return {
        "id": f"bedrock-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": finish_reason_map.get(stop_reason, "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("inputTokens", 0),
            "completion_tokens": usage.get("outputTokens", 0),
            "total_tokens": usage.get("inputTokens", 0) + usage.get("outputTokens", 0),
        },
    }


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------


class BedrockProvider(Provider):
    """AWS Bedrock provider using the Converse API.

    When *api_key* is given, requests are sent as direct HTTPS with a
    ``Bearer`` header.  Otherwise the standard boto3 SigV4 path is used.
    """

    def __init__(
        self,
        region: str | None = None,
        default_model: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        profile_name: str | None = None,
        api_key: str | None = None,
    ):
        self._region = region or _DEFAULT_REGION
        self._default_model = default_model
        self._profile_name = profile_name
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3

            session_kwargs: dict[str, Any] = {}
            if self._profile_name:
                session_kwargs["profile_name"] = self._profile_name
            session = boto3.Session(**session_kwargs)
            self._client = session.client(
                "bedrock-runtime",
                region_name=self._region,
            )
        return self._client

    @property
    def name(self) -> str:
        return "bedrock"

    @property
    def is_local(self) -> bool:
        return False

    def health(self) -> ProviderHealth:
        t0 = time.time()
        try:
            if self._api_key:
                # Bearer token mode — verify by listing foundation models.
                import httpx

                url = f"https://bedrock.{self._region}.amazonaws.com/foundation-models"
                resp = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    params={"byOutputModality": "TEXT"},
                    timeout=30,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                models = [m.get("modelId", "") for m in data.get("modelSummaries", [])]
            else:
                import boto3

                session = boto3.Session()
                bedrock = session.client("bedrock", region_name=self._region)
                response = bedrock.list_foundation_models(byOutputModality="TEXT")
                models = [m.get("modelId", "") for m in response.get("modelSummaries", [])]
            latency = (time.time() - t0) * 1000
            return ProviderHealth(
                status=ProviderStatus.HEALTHY,
                latency_ms=latency,
                models_available=models[:20],
            )
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                latency_ms=latency,
                error=str(exc),
            )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        model_id = model or self._default_model
        converse_messages, system_prompt = _openai_messages_to_converse(messages)

        inference_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            inference_config["maxTokens"] = max_tokens

        if self._api_key:
            response = _bearer_converse(
                self._region,
                model_id,
                self._api_key,
                converse_messages,
                system_prompt,
                inference_config,
            )
        else:
            client = self._get_client()
            resolved = _resolve_model_id(model_id)
            converse_kwargs: dict[str, Any] = {
                "modelId": resolved,
                "messages": converse_messages,
                "inferenceConfig": inference_config,
            }
            if system_prompt:
                converse_kwargs["system"] = [{"text": system_prompt}]
            response = client.converse(**converse_kwargs)

        return _converse_response_to_openai(response, model_id)

    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        model_id = model or self._default_model
        converse_messages, system_prompt = _openai_messages_to_converse(messages)

        inference_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            inference_config["maxTokens"] = max_tokens

        if self._api_key:
            yield from _bearer_converse_stream(
                self._region,
                model_id,
                self._api_key,
                converse_messages,
                system_prompt,
                inference_config,
            )
            return

        client = self._get_client()
        resolved = _resolve_model_id(model_id)
        converse_kwargs: dict[str, Any] = {
            "modelId": resolved,
            "messages": converse_messages,
            "inferenceConfig": inference_config,
        }
        if system_prompt:
            converse_kwargs["system"] = [{"text": system_prompt}]

        response = client.converse_stream(**converse_kwargs)
        stream = response.get("stream", [])
        for event in stream:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                text = delta.get("text", "")
                if text:
                    yield text

    def cost_per_1k(self, model: Optional[str] = None) -> float:
        return 0.0

    def model_capabilities(self, model: Optional[str] = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_tool_calling=True,
            supports_streaming=True,
            supports_vision=True,
        )


# ---------------------------------------------------------------------------
# HTTP adapter — plugs into r4's HTTPCore to intercept requests and
# route them through the Bedrock Converse API transparently.
# ---------------------------------------------------------------------------


class BedrockHTTPAdapter:
    """Intercept OpenAI-format HTTP requests and route via Bedrock Converse.

    This adapter quacks like :class:`r4._http.HTTPCore` for the chat
    completions endpoint only. It translates on the fly so the rest of
    the r4 client code doesn't need any changes.
    """

    def __init__(
        self,
        *,
        region: str | None = None,
        default_model: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        aws_profile: str | None = None,
        api_key: str | None = None,
        fallback_http: Any = None,
    ):
        self._provider = BedrockProvider(
            region=region,
            default_model=default_model,
            profile_name=aws_profile,
            api_key=api_key,
        )
        self._fallback = fallback_http

    def _is_chat_request(self, url: str) -> bool:
        return "/v1/chat/completions" in url

    def _is_models_request(self, url: str) -> bool:
        return url.rstrip("/").endswith("/v1/models")

    # --- public HTTPCore-compatible surface ----------------------------------

    def build_headers(self, *args, **kwargs):
        if self._fallback:
            return self._fallback.build_headers(*args, **kwargs)
        return {"Content-Type": "application/json"}

    def post(self, url: str, json_body: Any = None, **kwargs) -> dict:
        if self._is_chat_request(url) and json_body:
            return self._chat(json_body)
        if self._fallback:
            return self._fallback.post(url, json_body=json_body, **kwargs)
        raise RuntimeError(f"BedrockHTTPAdapter: unsupported POST to {url}")

    def get(self, url: str, **kwargs) -> dict:
        if self._is_models_request(url):
            return self._list_models()
        if self._fallback:
            return self._fallback.get(url, **kwargs)
        raise RuntimeError(f"BedrockHTTPAdapter: unsupported GET to {url}")

    def delete(self, url: str, **kwargs) -> dict:
        if self._fallback:
            return self._fallback.delete(url, **kwargs)
        return {}

    def patch(self, url: str, **kwargs) -> dict:
        if self._fallback:
            return self._fallback.patch(url, **kwargs)
        return {}

    def iter_sse_content(self, url: str, *, json_body: Any = None, **kwargs) -> Iterator[str]:
        """Stream chat completion as text fragments."""
        if self._is_chat_request(url) and json_body:
            messages = json_body.get("messages", [])
            model = json_body.get("model")
            temperature = json_body.get("temperature", 0.7)
            max_tokens = json_body.get("max_tokens")
            yield from self._provider.stream(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        elif self._fallback:
            yield from self._fallback.iter_sse_content(url, json_body=json_body, **kwargs)

    def iter_sse_chunks(self, url: str, *, json_body: Any = None, **kwargs) -> Iterator[dict]:
        """Stream chat completion as raw SSE-style chunk dicts."""
        if self._is_chat_request(url) and json_body:
            model = json_body.get("model", self._provider._default_model)
            messages = json_body.get("messages", [])
            temperature = json_body.get("temperature", 0.7)
            max_tokens = json_body.get("max_tokens")

            for text in self._provider.stream(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text},
                        }
                    ],
                    "model": model,
                }
        elif self._fallback:
            yield from self._fallback.iter_sse_chunks(url, json_body=json_body, **kwargs)

    def stream(self, *args, **kwargs):
        """Pass through to fallback for non-chat streaming."""
        if self._fallback:
            return self._fallback.stream(*args, **kwargs)
        raise RuntimeError("BedrockHTTPAdapter: stream() requires a fallback HTTP core")

    # --- internal helpers ----------------------------------------------------

    def _chat(self, body: dict) -> dict:
        messages = body.get("messages", [])
        model = body.get("model")
        temperature = body.get("temperature", 0.7)
        max_tokens = body.get("max_tokens")
        return self._provider.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _list_models(self) -> dict:
        health = self._provider.health()
        models = health.models_available or common_bedrock_model_ids(self._provider._default_model)
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "aws"} for m in models],
        }
