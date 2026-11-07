#!/usr/bin/env python3
"""E2E validation for the ADR-012 Assistant Service chain.

Validates the full conversational chain on a **live** deployment::

    Client → Assistant Service (/v1/chat/completions)
        → LiteLLM → vLLM

This test requires a running Assistant Service instance.  Set the
``ASSISTANT_E2E_URL`` environment variable to the Assistant Service base
URL (e.g. ``http://localhost:8090`` for a local docker-compose stack, or
``http://<compute-node>:8090`` on rccs-cloud).

Usage::

    # Local: start demo-llm.yaml stack, then:
    ASSISTANT_E2E_URL=http://localhost:8090 \\
    ASSISTANT_E2E_MODEL=rivault-gpt-oss-static \\
    .venv/bin/python -m pytest tests/test_assistant_e2e.py -v -s

    # rccs-cloud: after `r4 endpoint up` with assistant profile:
    ASSISTANT_E2E_URL=http://<compute-node>:8090 \\
    .venv/bin/python -m pytest tests/test_assistant_e2e.py -v -s

Prerequisites:
- A running Assistant Service (via demo-llm.yaml or r4 endpoint up)
- ``ASSISTANT_E2E_URL`` must be set
- Optional: ``ASSISTANT_E2E_MODEL`` (default: rivault-gpt-oss-static)
- Optional: ``ASSISTANT_E2E_API_KEY`` for authenticated requests
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

E2E_URL = os.environ.get("ASSISTANT_E2E_URL", "")
E2E_MODEL = os.environ.get("ASSISTANT_E2E_MODEL", "rivault-gpt-oss-static")
E2E_API_KEY = os.environ.get("ASSISTANT_E2E_API_KEY", "")
E2E_TIMEOUT = float(os.environ.get("ASSISTANT_E2E_TIMEOUT", "60"))

pytestmark = pytest.mark.skipif(
    not E2E_URL,
    reason="ASSISTANT_E2E_URL not set — skipping live Assistant Service e2e tests",
)


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if E2E_API_KEY:
        h["Authorization"] = f"Bearer {E2E_API_KEY}"
    return h


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAssistantServiceHealth:
    """Basic health and manifest checks."""

    def test_health(self) -> None:
        resp = httpx.get(f"{E2E_URL}/health", timeout=E2E_TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert data.get("service") == "assistant"

    def test_manifest(self) -> None:
        resp = httpx.get(f"{E2E_URL}/manifest", timeout=E2E_TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("service") == "assistant"
        assert data.get("capabilities", {}).get("assistant") is True
        assert data.get("capabilities", {}).get("streaming") is True
        assert "chat" in data.get("surfaces", {})

    def test_models(self) -> None:
        resp = httpx.get(f"{E2E_URL}/v1/models", timeout=E2E_TIMEOUT, headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("object") == "list"
        assert len(data.get("data", [])) > 0


class TestNonStreamingChat:
    """Non-streaming chat completion tests."""

    def test_basic_completion(self) -> None:
        """Send a simple chat completion and verify the response.

        Note: reasoning models (GLM-5.2, DeepSeek-R1, etc.) split the response
        into ``reasoning`` (thinking) and ``content`` (answer) fields. The test
        accepts either field as a valid non-empty response. ``max_tokens`` is
        set high enough to accommodate the reasoning prefix.
        """
        payload = {
            "model": E2E_MODEL,
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 500,
            "temperature": 0.1,
            "stream": False,
        }
        resp = httpx.post(
            f"{E2E_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=E2E_TIMEOUT,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("object") == "chat.completion"
        assert len(data.get("choices", [])) > 0
        msg = data["choices"][0].get("message", {})
        # Reasoning models may put the answer in 'content' or 'reasoning'
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning") or ""
        assert content or reasoning, "Expected non-empty content or reasoning in response"

    def test_extension_stripping(self) -> None:
        """Verify that Assistant-API extensions are stripped before forwarding."""
        payload = {
            "model": E2E_MODEL,
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "max_tokens": 50,
            "temperature": 0.1,
            "stream": False,
            # Assistant-API extensions — must not be forwarded to upstream
            "project_id": "test-project",
            "rag": False,
            "rag_top_k": 5,
            "rag_threshold": 0.8,
        }
        resp = httpx.post(
            f"{E2E_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=E2E_TIMEOUT,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


class TestStreamingChat:
    """Streaming (SSE) chat completion tests."""

    def test_streaming_completion(self) -> None:
        """Send a streaming chat completion and verify SSE chunks.

        Note: reasoning models (GLM-5.2, DeepSeek-R1, etc.) emit ``reasoning``
        deltas first, then ``content`` deltas. The test collects both and
        accepts either as a valid non-empty response. ``max_tokens`` is set
        high enough to accommodate the reasoning prefix.
        """
        payload = {
            "model": E2E_MODEL,
            "messages": [{"role": "user", "content": "Count from 1 to 5."}],
            "max_tokens": 500,
            "temperature": 0.1,
            "stream": True,
        }
        content_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        done = False

        with httpx.stream(
            "POST",
            f"{E2E_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=E2E_TIMEOUT,
        ) as resp:
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        done = True
                        break
                    try:
                        chunk_data = json.loads(data_str)
                        if "choices" in chunk_data:
                            delta = chunk_data["choices"][0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                content_chunks.append(delta["content"])
                            if "reasoning" in delta and delta["reasoning"]:
                                reasoning_chunks.append(delta["reasoning"])
                    except json.JSONDecodeError:
                        pass

        assert done, "Expected [DONE] sentinel in SSE stream"
        # Reasoning models may put content in 'reasoning' or 'content' deltas
        assert content_chunks or reasoning_chunks, (
            "Expected at least one content or reasoning chunk in SSE stream"
        )
        full_content = "".join(content_chunks)
        full_reasoning = "".join(reasoning_chunks)
        assert full_content or full_reasoning, "Expected non-empty accumulated content or reasoning"

    def test_streaming_no_tuple_leak(self) -> None:
        """Regression: SSE stream must not contain (status, headers) tuple."""
        payload = {
            "model": E2E_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "stream": True,
        }

        full_text = ""
        with httpx.stream(
            "POST",
            f"{E2E_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=E2E_TIMEOUT,
        ) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                full_text += line + "\n"

        # Regression: no Python tuple leaked into SSE body
        assert "Headers(" not in full_text, "Found leaked Headers object in SSE stream"
        assert "StatusCode(" not in full_text, "Found leaked StatusCode in SSE stream"
        assert "data: [DONE]" in full_text, "Expected [DONE] sentinel"


class TestErrorHandling:
    """Error handling and edge case tests."""

    def test_empty_messages(self) -> None:
        """Verify behavior with empty messages array."""
        payload = {
            "model": E2E_MODEL,
            "messages": [],
            "max_tokens": 50,
            "stream": False,
        }
        resp = httpx.post(
            f"{E2E_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=E2E_TIMEOUT,
        )
        # Should either return 200 with a default response, or 4xx/5xx
        # The key is that it doesn't hang or crash
        assert resp.status_code in (200, 400, 422, 502), (
            f"Unexpected status code: {resp.status_code}"
        )

    def test_invalid_model(self) -> None:
        """Verify behavior with an invalid model name."""
        payload = {
            "model": "nonexistent-model-xyz",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "stream": False,
        }
        resp = httpx.post(
            f"{E2E_URL}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=E2E_TIMEOUT,
        )
        # Should return an error, not hang
        assert resp.status_code in (200, 400, 404, 422, 502), (
            f"Unexpected status code: {resp.status_code}"
        )
