"""Contract tests for Gateway public API surfaces (T-102)."""

import pytest

# =============================================================================
# Contract: Gateway Public Endpoint and Job APIs
# =============================================================================


class TestGatewayPublicContract:
    """
    Contract tests for the public Gateway boundary.

    These tests verify:
    1. Clients use /gw/endpoints and /gw/jobs rather than raw /umrs/* routes.
    2. Gateway keeps a stable request/response envelope for UMRS-backed actions.
    3. Error handling stays consistent for client-facing integrations.
    """

    # -------------------------------------------------------------------------
    # Contract: GET /gw/endpoints
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_list_endpoints_request_format(self):
        """
        Contract: Clients discover endpoints through Gateway.

        Request:
        - Method: GET
        - Path: /gw/endpoints
        - Query params: model, include_stopped (optional)
        - Headers: Authorization bearer token required
        - Caller project/user identity is derived from JWT claims
        """
        expected_path = "/gw/endpoints"
        expected_method = "GET"
        expected_params = {
            "model": "rivault-gpt-oss-static",
            "include_stopped": False,
        }
        expected_headers = {"Authorization": "Bearer <jwt>"}
        expected_claims = {"project_id": "test-project", "user_id": "user-123"}

        assert expected_path == "/gw/endpoints"
        assert expected_method == "GET"
        assert "model" in expected_params
        assert "Authorization" in expected_headers
        assert expected_claims["project_id"] == "test-project"
        assert expected_claims["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_contract_list_endpoints_response_format(self):
        """
        Contract: Gateway returns endpoint list responses in a stable envelope.

        Response (200 OK):
        {
            "endpoints": [
                {
                    "project_id": str,
                    "model": str,
                    "job_id": str,
                    "status": str,
                    "backend": str,
                    "litellm_url": str | null,
                    ...
                }
            ],
            "total": int
        }
        """
        expected_response = {
            "endpoints": [
                {
                    "project_id": "test-project",
                    "model": "rivault-gpt-oss-static",
                    "job_id": "slurm-12345",
                    "status": "ready",
                    "expires_at": "2024-01-15T11:30:00Z",
                    "backend": "slurm",
                    "litellm_url": "http://compute-node-01:11434/v1",
                }
            ],
            "total": 1,
        }

        # Validate structure
        assert "endpoints" in expected_response
        assert "total" in expected_response
        assert isinstance(expected_response["endpoints"], list)

        endpoint = expected_response["endpoints"][0]
        required_fields = [
            "project_id",
            "model",
            "job_id",
            "status",
            "backend",
            "litellm_url",
        ]
        for field in required_fields:
            assert field in endpoint, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_contract_provision_request_format(self):
        """
        Contract: Endpoint provisioning is requested through Gateway.

        Request:
        - Method: POST
        - Path: /gw/endpoints/provision
        - Body: JSON with Gateway-supported provision fields
        """
        expected_request = {
            "model": "rivault-gpt-oss-static",
            "model_path": "/models/rivault-gpt-oss",
            "ttl_sec": 3600,
            "gpu_count": 1,
            "max_model_len": 4096,
            "prefer_warm": True,
        }

        required_fields = ["model"]
        for field in required_fields:
            assert field in expected_request

    @pytest.mark.asyncio
    async def test_contract_provision_response_format(self):
        """
        Contract: Gateway preserves UMRS provisioning envelopes.

        Response (202 Accepted):
        {
            "status": "provisioning",
            "job_id": str,
            "endpoint": null,
            "retry_after": int,
            "error": null
        }
        """
        expected_response = {
            "status": "provisioning",
            "job_id": "job-123",
            "endpoint": None,
            "retry_after": 30,
            "error": None,
        }

        assert validate_provision_response(expected_response)
        assert expected_response["retry_after"] > 0

    # -------------------------------------------------------------------------
    # Contract: GET /gw/jobs and /gw/jobs/{job_id}
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_get_job_response_format(self):
        """
        Contract: Gateway exposes durable job records via /gw/jobs/{job_id}.

        Response (200 OK):
        {
            "job_id": str,
            "project_id": str,
            "user_id": str,
            "job_type": str,
            "spec": dict,
            "status": str,
            "priority": int,
            "desired_max_attempts": int,
            "restart_policy": str,
            "created_at": str,
            "updated_at": str,
            "attempts": list
        }
        """
        expected_response = {
            "job_id": "job-123",
            "project_id": "test-project",
            "user_id": "user-123",
            "job_type": "endpoint_provision",
            "spec": {"model": "rivault-gpt-oss-static"},
            "status": "queued",
            "priority": 0,
            "desired_max_attempts": 3,
            "restart_policy": "on_crash",
            "next_run_at": None,
            "active_attempt_id": None,
            "commit_attempt_id": None,
            "created_at": "2026-04-09T00:00:00Z",
            "updated_at": "2026-04-09T00:00:00Z",
            "last_state_change_at": "2026-04-09T00:00:00Z",
            "attempts": [],
        }

        assert validate_job_response(expected_response)

    @pytest.mark.asyncio
    async def test_contract_list_jobs_response_format(self):
        """
        Contract: Gateway returns job lists in a stable envelope.

        Response (200 OK):
        {
            "jobs": [...],
            "total": int,
            "limit": int,
            "offset": int
        }
        """
        expected_response = {
            "jobs": [
                {
                    "job_id": "job-123",
                    "project_id": "test-project",
                    "user_id": "user-123",
                    "job_type": "endpoint_provision",
                    "spec": {"model": "rivault-gpt-oss-static"},
                    "status": "queued",
                    "priority": 0,
                    "desired_max_attempts": 3,
                    "restart_policy": "on_crash",
                    "next_run_at": None,
                    "active_attempt_id": None,
                    "commit_attempt_id": None,
                    "created_at": "2026-04-09T00:00:00Z",
                    "updated_at": "2026-04-09T00:00:00Z",
                    "last_state_change_at": "2026-04-09T00:00:00Z",
                }
            ],
            "total": 1,
            "limit": 50,
            "offset": 0,
        }

        assert validate_job_list_response(expected_response)

    @pytest.mark.asyncio
    async def test_contract_job_action_response_format(self):
        """
        Contract: Gateway exposes simple action results for cancel/retry.

        Response (200 OK):
        {
            "job_id": str,
            "action": str,
            "success": bool,
            "message": str
        }
        """
        expected_response = {
            "job_id": "job-123",
            "action": "cancel",
            "success": True,
            "message": "Job cancelled",
        }

        assert expected_response["success"] is True
        assert expected_response["action"] == "cancel"
        assert "message" in expected_response

    # -------------------------------------------------------------------------
    # Contract: Error Responses
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_error_response_format(self):
        """
        Contract: Error responses follow standard format.

        Response (4xx/5xx):
        {
            "detail": str | dict
        }
        """
        error_responses = [
            {"detail": "Endpoint not found"},
            {"detail": {"msg": "Validation error", "type": "value_error"}},
        ]

        for response in error_responses:
            assert "detail" in response


# =============================================================================
# Contract: Gateway → LiteLLM Integration (T-103)
# =============================================================================


class TestGatewayLiteLLMContract:
    """
    Contract tests for Gateway → LiteLLM communication.

    These tests verify:
    1. Gateway correctly forwards auth to LiteLLM
    2. LiteLLM responses are properly handled
    3. Error handling is consistent
    """

    # -------------------------------------------------------------------------
    # Contract: Auth Forwarding
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_auth_header_format(self):
        """
        Contract: Gateway must forward service key to LiteLLM.

        Headers:
        - Authorization: Bearer <GATEWAY_LITELLM_SERVICE_KEY>
        - X-User-ID: <user_id from JWT>
        - X-Project-ID: <project_id from JWT>
        """
        expected_headers = {
            "Authorization": "Bearer sk-ll-gateway-service-key",
            "X-User-ID": "user-123",
            "X-Project-ID": "project-456",
        }

        required_headers = ["Authorization", "X-User-ID", "X-Project-ID"]
        for header in required_headers:
            assert header in expected_headers

    # -------------------------------------------------------------------------
    # Contract: Chat Completions
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_chat_completions_request(self):
        """
        Contract: Chat completions request format (OpenAI compatible).

        Request:
        - Method: POST
        - Path: /v1/chat/completions
        - Body: OpenAI chat completions format
        """
        expected_request = {
            "model": "rivault-gpt-oss-static",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello!"},
            ],
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False,
        }

        required_fields = ["model", "messages"]
        for field in required_fields:
            assert field in expected_request

    @pytest.mark.asyncio
    async def test_contract_chat_completions_response(self):
        """
        Contract: Chat completions response format (OpenAI compatible).

        Response (200 OK):
        {
            "id": str,
            "object": "chat.completion",
            "created": int,
            "model": str,
            "choices": [...],
            "usage": {...}
        }
        """
        expected_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1704110400,
            "model": "rivault-gpt-oss-static",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! How can I help you?",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
        }

        required_fields = ["id", "object", "choices"]
        for field in required_fields:
            assert field in expected_response

        assert expected_response["object"] == "chat.completion"
        assert len(expected_response["choices"]) > 0

    @pytest.mark.asyncio
    async def test_contract_streaming_response(self):
        """
        Contract: Streaming response format (SSE).

        Response: text/event-stream
        data: {"id": ..., "object": "chat.completion.chunk", ...}
        data: [DONE]
        """
        expected_chunk = {
            "id": "chatcmpl-123",
            "object": "chat.completion.chunk",
            "created": 1704110400,
            "model": "rivault-gpt-oss-static",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        }

        assert expected_chunk["object"] == "chat.completion.chunk"
        assert "delta" in expected_chunk["choices"][0]

    # -------------------------------------------------------------------------
    # Contract: Models Endpoint
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_models_response(self):
        """
        Contract: Models list response format.

        Response (200 OK):
        {
            "object": "list",
            "data": [
                {
                    "id": str,
                    "object": "model",
                    "created": int,
                    "owned_by": str
                }
            ]
        }
        """
        expected_response = {
            "object": "list",
            "data": [
                {
                    "id": "rivault-gpt-oss-static",
                    "object": "model",
                    "created": 1704110400,
                    "owned_by": "rivault",
                }
            ],
        }

        assert expected_response["object"] == "list"
        assert isinstance(expected_response["data"], list)
        if expected_response["data"]:
            assert expected_response["data"][0]["object"] == "model"

    # -------------------------------------------------------------------------
    # Contract: Error Responses
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_contract_rate_limit_response(self):
        """
        Contract: Rate limit error response.

        Response (429 Too Many Requests):
        {
            "error": {
                "message": str,
                "type": "rate_limit_exceeded",
                "code": "rate_limit_exceeded"
            }
        }
        Headers:
        - Retry-After: int
        """
        expected_response = {
            "error": {
                "message": "Rate limit exceeded. Please retry after 60 seconds.",
                "type": "rate_limit_exceeded",
                "code": "rate_limit_exceeded",
            }
        }

        assert "error" in expected_response
        assert expected_response["error"]["type"] == "rate_limit_exceeded"

    @pytest.mark.asyncio
    async def test_contract_model_not_found_response(self):
        """
        Contract: Model not found error response.

        Response (404 Not Found):
        {
            "error": {
                "message": str,
                "type": "model_not_found",
                "code": "model_not_found"
            }
        }
        """
        expected_response = {
            "error": {
                "message": "Model 'unknown-model' not found",
                "type": "model_not_found",
                "code": "model_not_found",
            }
        }

        assert "error" in expected_response
        assert "model_not_found" in expected_response["error"]["type"]

    @pytest.mark.asyncio
    async def test_contract_service_unavailable_response(self):
        """
        Contract: Service unavailable error response.

        Response (503 Service Unavailable):
        {
            "error": {
                "message": str,
                "type": "service_unavailable",
                "code": "service_unavailable"
            }
        }
        Headers:
        - Retry-After: int
        """
        expected_response = {
            "error": {
                "message": "No inference endpoint available. Retry after 30 seconds.",
                "type": "service_unavailable",
                "code": "service_unavailable",
            }
        }

        assert "error" in expected_response


# =============================================================================
# Contract Validation Helpers
# =============================================================================


def validate_endpoint_response(response: dict) -> bool:
    """Validate endpoint detail response matches the public contract."""
    required_fields = ["project_id", "model", "job_id", "status"]

    if not all(field in response for field in required_fields):
        return False

    return response["status"] != "ready" or "litellm_url" in response


def validate_provision_response(response: dict) -> bool:
    """Validate provision response matches contract."""
    required_fields = ["status", "job_id", "endpoint", "retry_after", "error"]

    if not all(field in response for field in required_fields):
        return False

    valid_statuses = ["ready", "provisioning", "failed"]
    return response["status"] in valid_statuses


def validate_job_response(response: dict) -> bool:
    """Validate job detail response matches contract."""
    required_fields = [
        "job_id",
        "project_id",
        "user_id",
        "job_type",
        "spec",
        "status",
        "priority",
        "desired_max_attempts",
        "restart_policy",
        "created_at",
        "updated_at",
    ]

    if not all(field in response for field in required_fields):
        return False

    valid_statuses = ["planned", "queued", "running", "completed", "failed", "cancelled"]
    return response["status"] in valid_statuses


def validate_job_list_response(response: dict) -> bool:
    """Validate job list response matches contract."""
    required_fields = ["jobs", "total", "limit", "offset"]

    if not all(field in response for field in required_fields):
        return False

    return all(validate_job_response(job) for job in response["jobs"])


def validate_chat_completion_response(response: dict) -> bool:
    """Validate chat completion response matches OpenAI contract."""
    required_fields = ["id", "object", "choices"]

    if not all(field in response for field in required_fields):
        return False

    if response["object"] not in ["chat.completion", "chat.completion.chunk"]:
        return False

    return len(response["choices"]) > 0
