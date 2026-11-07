#!/usr/bin/env python3
"""
vLLM Tool Calling (Function Calling) Tests

Tests function/tool calling capabilities of vLLM + LiteLLM stack.

Tool calling allows LLMs to:
1. Recognize when a function should be called
2. Generate structured arguments for the function
3. Process function results and continue the conversation

Usage:
    # Run tool calling tests
    pytest tests/test_tool_calling.py -v

    # Run against local Mini-RiVault
    LITELLM_URL=http://localhost:11434 pytest tests/test_tool_calling.py -v

    # Run against E2E environment
    E2E_TEST_MODE=true pytest tests/test_tool_calling.py -v -m e2e

Requirements:
    - Mini-RiVault with tool calling enabled (VLLM_ENABLE_TOOL_CALLING=true)
    - Model that supports tool calling (e.g., Hermes, Llama 3, Mistral)
    - httpx library

Configuration:
    LITELLM_URL - LiteLLM endpoint (default: http://localhost:11434)
    LITELLM_API_KEY - API key (default: sk-test-e2e)
    E2E_TEST_MODE - Enable E2E tests (default: false)
"""

import json
import os

import pytest

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Configuration
LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:11434")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-test-e2e")
MODEL_NAME = os.environ.get("MODEL_NAME", "mock-model")


def is_e2e_mode() -> bool:
    """Check if running in E2E test mode."""
    return os.environ.get("E2E_TEST_MODE", "").lower() == "true"


# =============================================================================
# Sample Tool Definitions
# =============================================================================

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, e.g. San Francisco, CA",
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "The temperature unit",
                },
            },
            "required": ["location"],
        },
    },
}

CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Perform a mathematical calculation",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to evaluate",
                }
            },
            "required": ["expression"],
        },
    },
}

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for information",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                },
            },
            "required": ["query"],
        },
    },
}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def e2e_mode():
    """Skip if not in E2E mode."""
    if not is_e2e_mode():
        pytest.skip("Not in E2E test mode. Set E2E_TEST_MODE=true")


@pytest.fixture
def auth_headers():
    """Return authentication headers."""
    return {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def client():
    """Create HTTP client."""
    if not HTTPX_AVAILABLE:
        pytest.skip("httpx not installed")
    return httpx.Client(timeout=60)


# =============================================================================
# Tool Definition Tests
# =============================================================================


class TestToolDefinitions:
    """Test tool/function definition structures."""

    def test_weather_tool_is_valid(self):
        """Test weather tool has valid OpenAI function schema."""
        assert WEATHER_TOOL["type"] == "function"
        assert "function" in WEATHER_TOOL
        func = WEATHER_TOOL["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"
        assert "properties" in func["parameters"]

    def test_calculator_tool_is_valid(self):
        """Test calculator tool has valid schema."""
        assert CALCULATOR_TOOL["type"] == "function"
        func = CALCULATOR_TOOL["function"]
        assert func["name"] == "calculate"
        assert "expression" in func["parameters"]["properties"]

    def test_search_tool_is_valid(self):
        """Test search tool has valid schema."""
        assert SEARCH_TOOL["type"] == "function"
        func = SEARCH_TOOL["function"]
        assert func["name"] == "web_search"


# =============================================================================
# Mock Tool Calling Tests (always run)
# =============================================================================


class TestMockToolCalling:
    """Tests using mock responses to validate tool calling flow."""

    def test_tool_call_request_format(self, auth_headers):
        """Test that tool call request format is correct."""
        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
            "tools": [WEATHER_TOOL],
            "tool_choice": "auto",
        }

        # Validate request structure
        assert "model" in payload
        assert "messages" in payload
        assert "tools" in payload
        assert isinstance(payload["tools"], list)
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["type"] == "function"

    def test_tool_call_response_parsing(self):
        """Test parsing a simulated tool call response."""
        # Simulated response with tool call
        mock_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "model": MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_current_weather",
                                    "arguments": '{"location": "Tokyo", "unit": "celsius"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        }

        # Parse the response
        choice = mock_response["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        assert "tool_calls" in choice["message"]

        tool_call = choice["message"]["tool_calls"][0]
        assert tool_call["type"] == "function"
        assert tool_call["function"]["name"] == "get_current_weather"

        # Parse arguments
        args = json.loads(tool_call["function"]["arguments"])
        assert args["location"] == "Tokyo"
        assert args["unit"] == "celsius"

    def test_tool_result_message_format(self):
        """Test the format for returning tool results."""
        tool_result_message = {
            "role": "tool",
            "tool_call_id": "call_abc123",
            "content": json.dumps(
                {"temperature": 22, "unit": "celsius", "description": "Partly cloudy"}
            ),
        }

        assert tool_result_message["role"] == "tool"
        assert "tool_call_id" in tool_result_message
        assert "content" in tool_result_message


# =============================================================================
# E2E Tool Calling Tests (require running environment)
# =============================================================================


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestE2EToolCalling:
    """E2E tests for tool calling against live LiteLLM."""

    @pytest.mark.e2e
    def test_litellm_health(self, e2e_mode, client, auth_headers):
        """Test LiteLLM is healthy."""
        response = client.get(f"{LITELLM_URL}/health")
        assert response.status_code == 200

    @pytest.mark.e2e
    def test_models_endpoint(self, e2e_mode, client, auth_headers):
        """Test models endpoint."""
        response = client.get(f"{LITELLM_URL}/v1/models", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @pytest.mark.e2e
    def test_chat_completion_with_tools(self, e2e_mode, client, auth_headers):
        """Test chat completion with tool definitions.

        Note: The mock LiteLLM may not actually invoke tools, but should
        accept the request format without error.
        """
        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": "What's the weather like in Tokyo?"}],
            "tools": [WEATHER_TOOL],
            "tool_choice": "auto",
            "max_tokens": 100,
        }

        response = client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should accept request (200) even if mock doesn't actually call tools
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data

    @pytest.mark.e2e
    def test_chat_with_multiple_tools(self, e2e_mode, client, auth_headers):
        """Test chat completion with multiple tool definitions."""
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "user", "content": "Calculate 25 * 4 and tell me the weather in Paris"}
            ],
            "tools": [WEATHER_TOOL, CALCULATOR_TOOL],
            "tool_choice": "auto",
            "max_tokens": 150,
        }

        response = client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200

    @pytest.mark.e2e
    def test_forced_tool_choice(self, e2e_mode, client, auth_headers):
        """Test forcing a specific tool to be called."""
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "user", "content": "Hello"}  # Generic message
            ],
            "tools": [WEATHER_TOOL],
            "tool_choice": {"type": "function", "function": {"name": "get_current_weather"}},
            "max_tokens": 100,
        }

        response = client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return 200 or 400 depending on model support
        assert response.status_code in (200, 400, 422)


# =============================================================================
# Tool Calling Flow Tests
# =============================================================================


class TestToolCallingFlow:
    """Test complete tool calling conversation flows."""

    def test_full_tool_calling_conversation_structure(self):
        """Test the structure of a full tool calling conversation."""
        # Step 1: User asks question
        messages = [{"role": "user", "content": "What's the weather in Tokyo?"}]

        # Step 2: Assistant responds with tool call
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_current_weather",
                            "arguments": '{"location": "Tokyo", "unit": "celsius"}',
                        },
                    }
                ],
            }
        )

        # Step 3: Tool result
        messages.append(
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "content": '{"temperature": 22, "unit": "celsius", "description": "Sunny"}',
            }
        )

        # Step 4: Final assistant response (would come from another API call)
        messages.append({"role": "assistant", "content": "The weather in Tokyo is 22°C and sunny."})

        # Validate conversation structure
        assert len(messages) == 4
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert "tool_calls" in messages[1]
        assert messages[2]["role"] == "tool"
        assert messages[3]["role"] == "assistant"
        assert messages[3]["content"] is not None


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
