"""
Unit tests for r4 Client module (A-101, A-102, A-103).

Tests:
- ChatMessage creation and serialization
- Usage tracking
- ChatResponse parsing
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    """Keep auth-backed client tests isolated from host config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


# =============================================================================
# ChatMessage Tests
# =============================================================================


class TestChatMessage:
    """Tests for ChatMessage dataclass."""

    def test_create_user_message(self):
        """Test creating a user message."""
        from r4.client import ChatMessage

        msg = ChatMessage.user("Hello, world!")
        assert msg.role == "user"
        assert msg.content == "Hello, world!"
        assert msg.name is None

    def test_create_assistant_message(self):
        """Test creating an assistant message."""
        from r4.client import ChatMessage

        msg = ChatMessage.assistant("I can help with that.")
        assert msg.role == "assistant"
        assert msg.content == "I can help with that."

    def test_create_system_message(self):
        """Test creating a system message."""
        from r4.client import ChatMessage

        msg = ChatMessage.system("You are a helpful assistant.")
        assert msg.role == "system"
        assert msg.content == "You are a helpful assistant."

    def test_to_dict_basic(self):
        """Test converting message to dict format."""
        from r4.client import ChatMessage

        msg = ChatMessage(role="user", content="Test message")
        result = msg.to_dict()

        assert result == {"role": "user", "content": "Test message"}

    def test_to_dict_with_name(self):
        """Test converting message with name to dict."""
        from r4.client import ChatMessage

        msg = ChatMessage(role="user", content="Test", name="Alice")
        result = msg.to_dict()

        assert result == {"role": "user", "content": "Test", "name": "Alice"}


# =============================================================================
# Usage Tests
# =============================================================================


class TestUsage:
    """Tests for Usage dataclass."""

    def test_default_values(self):
        """Test default usage values."""
        from r4.client import Usage

        usage = Usage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_custom_values(self):
        """Test usage with custom values."""
        from r4.client import Usage

        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150


# =============================================================================
# ChatResponse Tests
# =============================================================================


class TestChatResponse:
    """Tests for ChatResponse dataclass."""

    def test_from_api_response_basic(self):
        """Test creating ChatResponse from API response."""
        from r4.chat_types import ChatResponse

        api_data = {
            "choices": [
                {
                    "message": {"content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "model": "test-model",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        response = ChatResponse.from_api_response(api_data)

        assert response.content == "Hello!"
        assert response.model == "test-model"
        assert response.finish_reason == "stop"
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.usage.total_tokens == 15
        assert response.raw_response == api_data

    def test_from_api_response_empty_choices(self):
        """Empty choices should surface as APIError."""
        import pytest

        from r4.chat_types import ChatResponse
        from r4.errors import APIError

        api_data = {"choices": [], "model": "test-model"}

        with pytest.raises(APIError, match="no choices"):
            ChatResponse.from_api_response(api_data)

    def test_from_api_response_empty_payload_raises(self):
        """Empty completion payloads should surface as APIError."""
        import pytest

        from r4.chat_types import ChatResponse
        from r4.errors import APIError

        with pytest.raises(APIError, match="Empty response"):
            ChatResponse.from_api_response({})


# =============================================================================
# Client Tests
# =============================================================================


class TestClient:
    """Tests for Client class."""

    def test_client_creation_default(self):
        """Test creating client with default config."""
        from r4.client import Client

        with patch("r4.client.get_config") as mock_config:
            mock_config.return_value = MagicMock(
                gateway_url="http://test:8080",
                api_key="test-key",
                project_id="test-project",
                model="test-model",
                timeout=300,
            )
            client = Client()
            assert client is not None

    def test_client_creation_with_custom_config(self):
        """Test creating client with custom config."""
        from r4.client import Client
        from r4.config import Config

        config = Config(
            gateway_url="http://custom:8080",
            api_key="custom-key",
            project_id="custom-project",
        )
        client = Client(config=config)
        assert client._config.gateway_url == "http://custom:8080"

    def test_client_has_sub_clients(self):
        """Test that client exposes sub-clients."""
        from r4.client import Client

        with patch("r4.client.get_config") as mock_config:
            mock_config.return_value = MagicMock(
                gateway_url="http://test:8080",
                api_key="test-key",
                project_id=None,
                model="test-model",
                timeout=300,
            )
            client = Client()
            # Sub-clients should be accessible
            assert hasattr(client, "rag")
            assert hasattr(client, "jobs")
            assert hasattr(client, "mcp")

    def test_build_headers_includes_umrs_username(self):
        """Test X-User-ID forwarding for UMRS-backed routes."""
        from r4.client import Client
        from r4.config import Config

        client = Client(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="custom-project",
                umrs_username="alice",
            )
        )

        headers = client._build_headers()

        assert headers["X-Project-ID"] == "custom-project"
        assert headers["X-User-ID"] == "alice"

    def test_build_headers_uses_stored_profile_auth(self):
        """Stored auth should be picked up when config.api_key is absent."""
        from r4.auth import get_auth_manager
        from r4.client import Client
        from r4.config import Config

        get_auth_manager("research").save_api_key("stored-key")

        client = Client(
            config=Config(
                profile="research",
                gateway_url="http://custom:8080",
                project_id="custom-project",
            )
        )

        headers = client._build_headers()

        assert headers["Authorization"] == "Bearer stored-key"

    def test_provider_headers_exclude_gateway_auth_and_context(self):
        """Direct public-provider calls must not leak Gateway auth or context."""
        from r4.client import Client
        from r4.config import Config

        client = Client(
            config=Config(
                gateway_url="http://gw:8080",
                api_key="gateway-key",
                project_id="proj",
                umrs_username="alice",
                endpoint_url="https://api.openai.com",
                endpoint_api_key="provider-key",
            )
        )

        headers = client.http.build_provider_headers(client._endpoint_auth_headers())

        assert headers["Authorization"] == "Bearer provider-key"
        assert "X-Project-ID" not in headers
        assert "X-User-ID" not in headers

    def test_chat_routes_platform_model_to_gateway_when_public_endpoint_exists(self):
        """A platform route should use Gateway even when a public endpoint is configured."""
        from r4.client import ChatMessage, Client
        from r4.config import Config

        client = Client(
            config=Config(
                gateway_url="http://gw:8080",
                api_key="gateway-key",
                endpoint_url="https://api.openai.com",
                endpoint_api_key="provider-key",
                model="rivault-gpt",
                model_routes={"rivault-gpt": "platform"},
            )
        )
        client.http.post = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]})

        response = client.chat_completion([ChatMessage.user("hello")])

        assert response.content == "ok"
        client.http.post.assert_called_once()
        assert client.http.post.call_args.args[0] == "http://gw:8080/gw/llm/v1/chat/completions"
        assert client.http.post.call_args.kwargs["headers"] is None
        assert client.http.post.call_args.kwargs["provider_request"] is False

    def test_chat_routes_public_model_direct_to_provider(self):
        """A public route should call the provider endpoint with provider-only headers."""
        from r4.client import ChatMessage, Client
        from r4.config import Config

        client = Client(
            config=Config(
                gateway_url="http://gw:8080",
                api_key="gateway-key",
                project_id="proj",
                umrs_username="alice",
                endpoint_url="https://api.openai.com",
                endpoint_api_key="provider-key",
                model="gpt-4o",
                model_routes={"gpt-4o": "public"},
            )
        )
        client.http.post = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]})

        response = client.chat_completion([ChatMessage.user("hello")])

        assert response.content == "ok"
        client.http.post.assert_called_once()
        call = client.http.post.call_args
        assert call.args[0] == "https://api.openai.com/v1/chat/completions"
        assert call.kwargs["headers"] == {"Authorization": "Bearer provider-key"}
        assert call.kwargs["provider_request"] is True

    def test_cancellable_agent_request_checks_cancellation_for_provider(self):
        """Provider routes support best-effort cancellation via pre/post checks."""
        from r4.agent.core import AgentCancellation
        from r4.client import ChatMessage, Client
        from r4.config import Config

        client = Client(config=Config(model="provider-model"))
        provider = MagicMock()
        provider.chat.return_value = {"choices": [{"message": {"content": "ok"}}]}
        client._provider_for_route = MagicMock(return_value=provider)

        # Not cancelled — provider call should succeed
        response = client.chat_completion(
            [ChatMessage.user("hello")],
            _turn_cancel=AgentCancellation(),
        )
        assert response.content == "ok"
        provider.chat.assert_called_once()

    def test_cancellable_openai_compat_provider_falls_through_to_http(self, monkeypatch):
        """An OpenAI-compatible public source must stay cancellable via the HTTP path.

        Regression for the companion error "Provider route does not support
        cancellable agent requests" when chatting with a BYOK model@source.
        """
        monkeypatch.setenv("R4_STREAM_BLOCKING", "0")  # pins the plain-POST path (ADR-087)
        from r4.agent.core import AgentCancellation
        from r4.client import ChatMessage, Client
        from r4.config import Config
        from r4.providers.openai_compat import OpenAICompatibleProvider

        client = Client(
            config=Config(
                gateway_url="http://gw:8080",
                endpoint_url="https://api.rikyu.test",
                endpoint_api_key="rikyu-key",
                model="kimi-k3",
                model_routes={"kimi-k3": "public"},
            )
        )
        provider = OpenAICompatibleProvider(
            provider_name="rikyu",
            base_url="https://api.rikyu.test",
            api_key="rikyu-key",
        )
        client._provider_for_route = MagicMock(return_value=provider)
        client.http.post = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]})

        response = client.chat_completion(
            [ChatMessage.user("hello")],
            _turn_cancel=AgentCancellation(),
        )

        assert response.content == "ok"
        # Went through the cancellable HTTP path, not the provider transport.
        client.http.post.assert_called_once()
        call = client.http.post.call_args
        assert call.args[0] == "https://api.rikyu.test/v1/chat/completions"
        assert call.kwargs["provider_request"] is True
        assert call.kwargs["cancel_check"] is not None

    def test_cancellable_model_at_source_uses_source_api_key(self, monkeypatch):
        """model@<public-source> HTTP fallback must auth with the source key."""
        monkeypatch.setenv("R4_STREAM_BLOCKING", "0")  # pins the plain-POST path (ADR-087)
        from unittest.mock import patch

        from r4.agent.core import AgentCancellation
        from r4.client import ChatMessage, Client
        from r4.config import Config
        from r4.providers.openai_compat import OpenAICompatibleProvider
        from r4.source import Source

        client = Client(
            config=Config(
                gateway_url="http://gw:8080",
                api_key="gateway-jwt",
                model="kimi-k3@rikyu",
            )
        )
        provider = OpenAICompatibleProvider(
            provider_name="rikyu",
            base_url="https://api.rikyu.test",
            api_key="rikyu-source-key",
        )
        client._provider_for_route = MagicMock(return_value=provider)
        client.http.post = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]})

        rikyu = Source(
            name="rikyu",
            kind="public",
            url="https://api.rikyu.test",
            api_key="rikyu-source-key",
        )
        with patch("r4.source.get_source_store") as mock_store:
            mock_store.return_value.get.return_value = rikyu
            response = client.chat_completion(
                [ChatMessage.user("hello")],
                _turn_cancel=AgentCancellation(),
            )

        assert response.content == "ok"
        call = client.http.post.call_args
        assert call.args[0] == "https://api.rikyu.test/v1/chat/completions"
        assert call.kwargs["headers"] == {"Authorization": "Bearer rikyu-source-key"}
        assert call.kwargs["provider_request"] is True

    def test_cancellable_model_at_source_bypasses_bedrock_adapter(self, monkeypatch):
        """Source-bound BYOK HTTP fallback must not be hijacked by BedrockHTTPAdapter."""
        monkeypatch.setenv("R4_STREAM_BLOCKING", "0")  # pins the plain-POST path (ADR-087)
        from unittest.mock import patch

        from r4.agent.core import AgentCancellation
        from r4.client import ChatMessage, Client
        from r4.config import Config
        from r4.providers.bedrock import BedrockHTTPAdapter
        from r4.providers.openai_compat import OpenAICompatibleProvider
        from r4.source import Source

        client = Client(
            config=Config(
                gateway_url="http://gw:8080",
                endpoint_provider="bedrock",
                model="kimi-k3@rikyu",
            )
        )
        client._http_core = BedrockHTTPAdapter(default_model="anthropic.claude")
        provider = OpenAICompatibleProvider(
            provider_name="rikyu",
            base_url="https://api.rikyu.test",
            api_key="rikyu-source-key",
        )
        client._provider_for_route = MagicMock(return_value=provider)
        client.platform_http.post = MagicMock(
            return_value={"choices": [{"message": {"content": "ok"}}]}
        )

        rikyu = Source(
            name="rikyu",
            kind="public",
            url="https://api.rikyu.test",
            api_key="rikyu-source-key",
        )
        with patch("r4.source.get_source_store") as mock_store:
            mock_store.return_value.get.return_value = rikyu
            response = client.chat_completion(
                [ChatMessage.user("hello")],
                _turn_cancel=AgentCancellation(),
            )

        assert response.content == "ok"
        client.platform_http.post.assert_called_once()
        call = client.platform_http.post.call_args
        assert call.args[0] == "https://api.rikyu.test/v1/chat/completions"
        assert call.kwargs["headers"] == {"Authorization": "Bearer rikyu-source-key"}

    def test_bedrock_model_list_falls_back_when_health_unavailable(self):
        """Bedrock public models should not disappear if live listing is unavailable."""
        from r4.providers import ProviderHealth, ProviderStatus
        from r4.providers.bedrock import BedrockHTTPAdapter

        adapter = BedrockHTTPAdapter(default_model="anthropic.custom-model")
        adapter._provider.health = MagicMock(
            return_value=ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                error="Unable to locate credentials",
            )
        )

        data = adapter.get("https://bedrock.placeholder.internal/v1/models")
        model_ids = [item["id"] for item in data["data"]]

        assert model_ids[0] == "anthropic.custom-model"
        assert "anthropic.claude-sonnet-4-6" in model_ids

    def test_readiness_returns_gateway_detail_on_unready(self):
        """Gateway readiness details should be surfaced for CLI reporting."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(status_code=503)
        response.json.return_value = {
            "detail": {
                "ready": False,
                "checks": {"umrs": "unavailable"},
                "message": "Service not ready - some dependencies unavailable",
            }
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.get.return_value = response

            client = Client(config=Config(gateway_url="http://custom:8080", verify_ssl=False))
            readiness = client.readiness()

        assert readiness["ready"] is False
        assert readiness["checks"]["umrs"] == "unavailable"

    def test_readiness_handles_html_503_response(self):
        """Proxy-generated HTML 503 responses produce actionable readiness state."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(
            status_code=503,
            text="<html><h1>503 Service Unavailable</h1></html>",
        )
        response.json.side_effect = ValueError("not JSON")

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.get.return_value = response

            client = Client(config=Config(gateway_url="http://custom:8080", verify_ssl=False))
            readiness = client.readiness()

        assert readiness == {
            "ready": False,
            "message": "Gateway returned HTTP 503 Service Unavailable",
        }

    def test_provision_endpoint_forwards_mount_payload(self):
        """Endpoint provisioning forwards mini-RiVault mount fields."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(status_code=202, headers={})
        response.json.return_value = {
            "status": "provisioning",
            "job_id": "job-123",
            "endpoint": None,
            "retry_after": 30,
            "error": None,
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            client = Client(
                config=Config(
                    gateway_url="http://custom:8080",
                    project_id="custom-project",
                    verify_ssl=False,
                )
            )
            result = client.provision_endpoint(
                model="rivault-gpt-oss-static",
                tier="system",
                mounts={"project_data": {"source": "/data", "target": "/project"}},
                egress_domains=["example.com"],
                timeout=900,
            )

        assert mock_http.post.call_args.kwargs["json"] == {
            "model": "rivault-gpt-oss-static",
            "models": ["rivault-gpt-oss-static"],
            "serving_mode": "single",
            "tier": "system",
            "mounts": {"project_data": {"source": "/data", "target": "/project"}},
            "egress_domains": ["example.com"],
        }
        assert mock_http_client.call_args.kwargs["timeout"].read == 900
        assert result["instance_id"] == "job-123"
        assert result["status"] == "provisioning"

    def test_provision_endpoint_multi_lora_payload(self):
        """Multi-lora request includes the loras list and serving_mode."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(status_code=202, headers={})
        response.json.return_value = {
            "status": "provisioning",
            "job_id": "job-ml",
            "endpoint": None,
            "retry_after": 30,
            "error": None,
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            client = Client(
                config=Config(gateway_url="http://gw:8080", project_id="p", verify_ssl=False)
            )
            client.provision_endpoint(
                model="base",
                serving_mode="multi-lora",
                loras=[
                    {"name": "tune-a", "source": "/loras/a"},
                    {"name": "tune-b", "source": "/loras/b"},
                ],
            )

        assert mock_http.post.call_args.kwargs["json"] == {
            "model": "base",
            "models": ["base"],
            "serving_mode": "multi-lora",
            "loras": [
                {"name": "tune-a", "source": "/loras/a"},
                {"name": "tune-b", "source": "/loras/b"},
            ],
        }

    def test_provision_endpoint_co_located_payload(self):
        """Co-located request sends the full models list and serving_mode."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(status_code=202, headers={})
        response.json.return_value = {
            "status": "provisioning",
            "job_id": "job-cl",
            "endpoint": None,
            "retry_after": 30,
            "error": None,
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            client = Client(
                config=Config(gateway_url="http://gw:8080", project_id="p", verify_ssl=False)
            )
            client.provision_endpoint(
                model="m1",
                models=["m1", "m2"],
                serving_mode="co-located",
            )

        assert mock_http.post.call_args.kwargs["json"] == {
            "model": "m1",
            "models": ["m1", "m2"],
            "serving_mode": "co-located",
        }

    def test_provision_endpoint_forwards_bentoml_vision_payload(self):
        """Vision endpoint requests include backend/service metadata."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(status_code=202, headers={})
        response.json.return_value = {
            "status": "provisioning",
            "job_id": "job-vision",
            "endpoint": None,
            "retry_after": 30,
            "error": None,
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            client = Client(
                config=Config(gateway_url="http://gw:8080", project_id="p", verify_ssl=False)
            )
            client.provision_endpoint(
                model="sam3",
                serving_backend="bentoml",
                service_kind="bentoml_vision",
                launch_profiles=["harness", "vision-bentoml"],
                vision_adapter="sam3",
                co_locate_vllm=True,
            )

        assert mock_http.post.call_args.kwargs["json"] == {
            "model": "sam3",
            "models": ["sam3"],
            "serving_mode": "single",
            "serving_backend": "bentoml",
            "service_kind": "bentoml_vision",
            "launch_profiles": ["harness", "vision-bentoml"],
            "co_locate_vllm": True,
            "vision_adapter": "sam3",
        }

    def test_endpoint_normalization_prefers_generic_service_url(self):
        """Non-OpenAI endpoints can expose service_url without losing endpoint_url."""
        from r4._endpoint_helpers import normalize_endpoint_record

        normalized = normalize_endpoint_record(
            {
                "job_id": "job-vision",
                "model": "sam3",
                "status": "ready",
                "litellm_url": "http://node:8001",
                "service_url": "http://node:3001",
                "model_type": "vision",
                "serving_backend": "bentoml",
                "service_kind": "bentoml_vision",
            }
        )

        assert normalized["endpoint_url"] == "http://node:3001"
        assert normalized["model_type"] == "vision"
        assert normalized["serving_backend"] == "bentoml"
        assert normalized["service_kind"] == "bentoml_vision"

    def test_get_endpoint_status_enriches_detail_from_active_attempt(self):
        """Test endpoint status uses durable-job detail for user-facing progress."""
        from datetime import datetime

        from r4.client import Client
        from r4.config import Config
        from r4.jobs import Job, JobAttempt

        client = Client(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="custom-project",
                verify_ssl=False,
            )
        )

        endpoint_payload = {
            "job_id": "job-123",
            "project_id": "custom-project",
            "model": "bonsai-8b",
            "status": "starting",
            "host": "ng-dgx-m0-08",
            "port": 11434,
            "backend": "slurm",
            "created_at": "2026-04-28T00:00:00Z",
            "ready_at": None,
            "last_used_at": None,
            "expires_at": None,
            "error_message": None,
            "litellm_url": "http://ng-dgx-m0-08:11434",
        }
        job = Job(
            job_id="job-123",
            project_id="custom-project",
            user_id="mnagaso",
            job_type="endpoint_provision",
            spec={"model": "bonsai-8b"},
            status="running",
            priority=0,
            max_attempts=3,
            restart_policy="on_crash",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            active_attempt_id="att-1",
            attempts=[
                JobAttempt(
                    attempt_id="att-1",
                    job_id="job-123",
                    attempt_no=1,
                    backend="slurm",
                    backend_ref="174075",
                    status="running",
                    started_at=datetime.utcnow(),
                    ended_at=None,
                    exit_reason=None,
                    log_uri=None,
                    status_detail="polling readiness (submitted to backend)",
                    metrics={},
                    created_at=datetime.utcnow(),
                )
            ],
        )

        with (
            patch.object(client.http, "get", return_value=endpoint_payload),
            patch.object(
                client.jobs,
                "get",
                return_value=job,
            ),
        ):
            result = client.get_endpoint_status("job-123")

        assert result["job_status"] == "running"
        assert result["active_attempt_status"] == "running"
        assert result["active_attempt_status_detail"] == "polling readiness (submitted to backend)"
        assert result["detail"] == "starting container / loading model"
        assert result["host"] == "ng-dgx-m0-08"

    def test_stop_endpoint_handles_no_content_response(self):
        """Test endpoint stop handles UMRS deprovision 204 responses."""
        from r4.client import Client
        from r4.config import Config

        response = MagicMock(status_code=204, headers={}, text="")

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.delete.return_value = response

            client = Client(
                config=Config(
                    gateway_url="http://custom:8080",
                    project_id="custom-project",
                    verify_ssl=False,
                )
            )
            result = client.stop_endpoint("job-123")

        mock_http.delete.assert_called_once()
        assert (
            mock_http.delete.call_args.args[0]
            == "http://custom:8080/gw/endpoints/job-123/deprovision"
        )
        assert result == {
            "instance_id": "job-123",
            "job_id": "job-123",
            "status": "deprovisioned",
        }

    def test_stop_endpoint_preserves_http_error_details(self):
        """Test endpoint stop preserves wrapped HTTP error details for CLI hints."""
        from r4.client import Client
        from r4.config import Config
        from r4.errors import R4Error

        response = MagicMock(status_code=404, headers={}, text='{"detail": "not found"}')

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.delete.return_value = response

            client = Client(
                config=Config(
                    gateway_url="http://custom:8080",
                    project_id="custom-project",
                    verify_ssl=False,
                )
            )
            with pytest.raises(R4Error) as exc_info:
                client.stop_endpoint("1")

        assert exc_info.value.details["status_code"] == 404

    def test_get_endpoint_logs_summarizes_job_attempts(self):
        """Test endpoint logs are summarized from UMRS job attempts."""
        from datetime import datetime

        from r4.client import Client
        from r4.config import Config
        from r4.jobs import Job, JobAttempt

        client = Client(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="custom-project",
            )
        )

        job = Job(
            job_id="job-123",
            project_id="custom-project",
            user_id="alice",
            job_type="endpoint_provision",
            spec={"model": "rivault-gpt-oss-static"},
            status="failed",
            priority=0,
            max_attempts=3,
            restart_policy="on_crash",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            attempts=[
                JobAttempt(
                    attempt_id="att-1",
                    job_id="job-123",
                    attempt_no=1,
                    backend="slurm",
                    backend_ref="slurm-123",
                    status="failed",
                    started_at=None,
                    ended_at=None,
                    exit_reason="backend_error",
                    log_uri="s3://logs/job-123/attempt-1.log",
                    status_detail="Provision failed",
                )
            ],
        )

        with patch.object(client.jobs, "get", return_value=job):
            result = client.get_endpoint_logs("job-123")

        assert result["job_id"] == "job-123"
        assert any("log_uri: s3://logs/job-123/attempt-1.log" in line for line in result["logs"])


class TestListEndpoints:
    """Regression tests for active vs full endpoint listing URLs."""

    def _client(self):
        from r4.client import Client
        from r4.config import Config

        client = Client(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="custom-project",
                verify_ssl=False,
            )
        )
        client._is_umrs_direct = False
        return client

    def test_list_endpoints_active_uses_active_subpath(self):
        client = self._client()
        with patch.object(client.http, "get", return_value={"endpoints": []}) as mock_get:
            client.list_endpoints(include_stopped=False)

        mock_get.assert_called_once_with(
            "http://custom:8080/gw/endpoints/active",
            params={},
            timeout=None,
        )

    def test_list_endpoints_all_uses_base_path(self):
        client = self._client()
        with patch.object(client.http, "get", return_value={"endpoints": []}) as mock_get:
            client.list_endpoints(include_stopped=True)

        mock_get.assert_called_once_with(
            "http://custom:8080/gw/endpoints",
            params={},
            timeout=None,
        )

    def test_list_endpoints_direct_umrs_active_path(self):
        from r4.client import Client
        from r4.config import Config

        client = Client(
            config=Config(
                gateway_url="http://umrs:8000",
                project_id="custom-project",
                verify_ssl=False,
            )
        )
        client._is_umrs_direct = True
        with patch.object(client.http, "get", return_value={"endpoints": []}) as mock_get:
            client.list_endpoints(include_stopped=False)

        mock_get.assert_called_once_with(
            "http://umrs:8000/umrs/endpoints/active",
            params={},
            timeout=None,
        )


class TestStreamRequestCollect:
    """_stream_request_collect: content deltas + tool-call delta merging."""

    def _client(self):
        from r4.client import Client
        from r4.config import Config

        return Client(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="custom-project",
                verify_ssl=False,
            )
        )

    def test_collects_content_and_tool_call_deltas(self):
        """SSE chunks yield content while tool_calls deltas merge by index."""
        client = self._client()
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "Let me check. "}}]},
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "list_dir", "arguments": ""},
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": '{"path":'}}]
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"."}'}}]},
                    }
                ]
            },
        ]
        acc = []
        with (
            patch.object(client, "_provider_for_route", return_value=MagicMock()),
            patch.object(client.http, "iter_sse_chunks", return_value=iter(chunks)),
        ):
            stream = client._stream_request_collect({"model": "m", "messages": []}, acc)
            assert stream is not None
            text = "".join(stream)
        assert text == "Let me check. "
        assert acc == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path":"."}'},
            }
        ]

    def test_bedrock_provider_route_returns_none(self):
        """Bedrock Converse has no OpenAI tool-delta shape — caller falls back."""
        from r4.providers.bedrock import BedrockProvider

        client = self._client()
        bedrock = BedrockProvider(region="us-east-1")
        with patch.object(client, "_provider_for_route", return_value=bedrock):
            assert client._stream_request_collect({"model": "m", "messages": []}, []) is None


class TestStreamUsageCapture:
    """SSE streams record REAL usage (stream_options.include_usage) into
    session_cost + _last_stream_usage — the data path behind the companion's
    cost/ctx chip, which never rendered while streamed turns went unrecorded.
    """

    def _client(self):
        from r4.client import Client
        from r4.config import Config

        return Client(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="custom-project",
                verify_ssl=False,
            )
        )

    def _run_usage_stream(self, client):
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "Hello"}}]},
            {"choices": [{"index": 0, "delta": {"content": " world"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 150, "completion_tokens": 25, "total_tokens": 175},
            },
        ]
        with patch.object(client.http, "iter_sse_chunks", return_value=iter(chunks)):
            stream = client._stream_request_collect({"model": "kimi-k3", "messages": []}, [])
            assert stream is not None
            assert "".join(stream) == "Hello world"

    def test_usage_chunk_records_session_cost(self):
        client = self._client()
        self._run_usage_stream(client)
        turns = client.session_cost.turns
        assert len(turns) == 1
        assert turns[0].prompt_tokens == 150
        assert turns[0].completion_tokens == 25
        assert turns[0].total_tokens == 175
        assert turns[0].model == "kimi-k3"

    def test_usage_chunk_stashes_last_stream_usage(self):
        client = self._client()
        self._run_usage_stream(client)
        stash = client._last_stream_usage
        assert stash is not None
        assert stash["prompt_tokens"] == 150
        assert stash["completion_tokens"] == 25
        assert stash["model"] == "kimi-k3"

    def test_no_usage_chunk_leaves_stash_none_and_no_cost(self):
        client = self._client()
        chunks = [{"choices": [{"index": 0, "delta": {"content": "hi"}}]}]
        with patch.object(client.http, "iter_sse_chunks", return_value=iter(chunks)):
            stream = client._stream_request_collect({"model": "m", "messages": []}, [])
            assert stream is not None
            assert "".join(stream) == "hi"

        assert client._last_stream_usage is None
        assert client.session_cost.turns == []

    def test_zero_usage_chunk_is_not_recorded(self):
        client = self._client()
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "hi"}}]},
            {"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}},
        ]
        with patch.object(client.http, "iter_sse_chunks", return_value=iter(chunks)):
            stream = client._stream_request_collect({"model": "m", "messages": []}, [])
            assert stream is not None
            assert "".join(stream) == "hi"

        assert client.session_cost.turns == []

    def test_stream_request_clears_stale_stash(self):
        """The text-only stream path must not let a later consumer read a
        previous round's usage (consume-on-read pairing)."""
        client = self._client()
        client._last_stream_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        with patch.object(client.http, "iter_sse_content", return_value=iter(["hi"])):
            list(client._stream_request({"model": "m", "messages": []}))
        assert client._last_stream_usage is None
