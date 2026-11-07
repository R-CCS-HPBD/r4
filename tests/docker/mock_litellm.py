#!/usr/bin/env python3
"""
Mock LiteLLM Server for E2E Testing

Provides a lightweight OpenAI-compatible API that doesn't require
actual model weights or GPU. Returns deterministic mock responses.

Endpoints:
- GET /health - Health check
- GET /v1/models - List available models
- POST /v1/chat/completions - Chat completion (mock response)
"""

import json
import os
import time
import uuid

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="Mock LiteLLM", version="1.0.0")

# Configuration
LITELLM_PORT = int(os.getenv("LITELLM_PORT", "11434"))
LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-test-e2e")
MODEL_NAME = os.getenv("MODEL_NAME", "rivault-gpt-oss-static")


def verify_auth(authorization: str | None) -> bool:
    """Verify API key authentication."""
    if not authorization:
        return False
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        return token == LITELLM_MASTER_KEY
    return False


@app.get("/health")
@app.get("/healthz")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "mock": True}


@app.get("/v1/models")
async def list_models(authorization: str | None = Header(None)):
    """List available models."""
    if not verify_auth(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "rivault",
                "permission": [],
                "root": MODEL_NAME,
                "parent": None,
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(None)):
    """Handle chat completion requests."""
    if not verify_auth(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()

    model = body.get("model", MODEL_NAME)
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice", "auto")

    # Extract the last user message
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    # Generate mock response
    response_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    # Check if this looks like a tool calling request
    tool_calls = None
    if tools and tool_choice != "none":
        # Detect weather-related queries
        weather_keywords = ["weather", "temperature", "forecast", "rain", "sunny", "cold", "hot"]
        calc_keywords = ["calculate", "compute", "math", "sum", "multiply", "divide", "add"]

        user_lower = user_message.lower()

        # Check if we should simulate a tool call
        if any(kw in user_lower for kw in weather_keywords):
            # Find weather tool
            for tool in tools:
                if tool.get("function", {}).get("name") == "get_current_weather":
                    # Extract location from message (simple heuristic)
                    location = "Unknown"
                    for word in ["Tokyo", "Paris", "London", "New York", "San Francisco"]:
                        if word.lower() in user_lower:
                            location = word
                            break

                    tool_calls = [
                        {
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": "get_current_weather",
                                "arguments": json.dumps({"location": location, "unit": "celsius"}),
                            },
                        }
                    ]
                    break

        elif any(kw in user_lower for kw in calc_keywords):
            # Find calculator tool
            for tool in tools:
                if tool.get("function", {}).get("name") == "calculate":
                    tool_calls = [
                        {
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": "calculate",
                                "arguments": json.dumps({"expression": "25 * 4"}),
                            },
                        }
                    ]
                    break

    # Mock response content based on input
    if tool_calls:
        content = None  # Tool calls have no content
        finish_reason = "tool_calls"
    elif "hello" in user_message.lower():
        content = "Hello! I'm a mock LiteLLM server for testing. How can I help you today?"
        finish_reason = "stop"
    elif "test" in user_message.lower():
        content = "This is a test response from the mock LiteLLM server. All systems operational!"
        finish_reason = "stop"
    elif "model" in user_message.lower():
        content = f"I am {model}, a mock model for E2E testing purposes."
        finish_reason = "stop"
    else:
        content = f"Mock response to: {user_message[:100]}..."
        finish_reason = "stop"

    if stream:
        # Streaming response
        async def generate():
            # Send chunks
            words = content.split()
            for i, word in enumerate(words):
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": word + (" " if i < len(words) - 1 else "")},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.05)  # Simulate token generation delay

            # Send final chunk
            final_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        import asyncio

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        # Non-streaming response
        message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": len(user_message.split()),
                "completion_tokens": len(content.split()) if content else 20,
                "total_tokens": len(user_message.split())
                + (len(content.split()) if content else 20),
            },
        }


@app.post("/v1/completions")
async def completions(request: Request, authorization: str | None = Header(None)):
    """Handle legacy completion requests."""
    if not verify_auth(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()
    prompt = body.get("prompt", "")

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:8]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": body.get("model", MODEL_NAME),
        "choices": [
            {"text": f"Mock completion for: {prompt[:50]}...", "index": 0, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": len(prompt.split()),
            "completion_tokens": 10,
            "total_tokens": len(prompt.split()) + 10,
        },
    }


if __name__ == "__main__":
    print(f"Starting Mock LiteLLM server on port {LITELLM_PORT}")
    print(f"Model: {MODEL_NAME}")
    print(f"API Key: {LITELLM_MASTER_KEY[:10]}...")
    uvicorn.run(app, host="0.0.0.0", port=LITELLM_PORT)
