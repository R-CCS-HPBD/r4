# Assistant API v1 Contract (ADR-012)

**Status:** Stable
**Version:** 1
**Date:** 2026-07-04

The Assistant API is the platform-native conversational surface.  It is
OpenAI-compatible in request/response shape but adds project-aware
augmentation: RAG context injection, tool routing, and MCP.

## Base URL

```
/gw/chat/v1
```

All requests pass through the NGINX gateway (auth, rate-limit, SSE) and
the UMRS chat proxy (endpoint discovery) before reaching the Assistant
Service inside mini-RiVault.

## Authentication

All endpoints require a valid API key in the `Authorization` header:

```
Authorization: Bearer <api-key>
```

The gateway validates the key and injects `X-User-ID` and `X-Project-ID`
headers that downstream services use for authorization and scoping.

## Endpoints

### `POST /v1/chat/completions`

OpenAI-compatible chat completion with optional project-aware RAG.

**Request body** (standard OpenAI fields plus Assistant-API extensions):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | (required) | Model name or alias |
| `messages` | array | (required) | OpenAI-format messages |
| `stream` | boolean | `false` | Enable SSE streaming |
| `project_id` | string | `null` | Project scope for RAG retrieval |
| `rag` | boolean | `true` | Enable/disable RAG augmentation |
| `rag_top_k` | integer | `3` | Number of documents to retrieve |
| `rag_threshold` | float | `0.7` | Similarity threshold for RAG |

**Response**: Standard OpenAI `chat.completion` or `chat.completion.chunk`
objects.

**Fail-open behavior**: If RAG retrieval fails, the Assistant Service
forwards the original messages to LiteLLM without augmentation.  The
request never fails due to RAG being unavailable.

### `GET /v1/models`

Returns the list of available models.

**Response**: Standard OpenAI `list` response with model objects.

### `GET /manifest`

Returns the service manifest with capability flags.

**Response**:
```json
{
  "service": "assistant",
  "version": "0.1.0",
  "surfaces": {
    "chat": "/v1/chat/completions",
    "models": "/v1/models",
    "manifest": "/manifest",
    "health": "/health"
  },
  "capabilities": {
    "assistant": true,
    "rag": true,
    "project_aware": true,
    "streaming": true,
    "vision": false,
    "mcp": false,
    "tools": false
  },
  "default_model": "rivault-gpt-oss-static"
}
```

### `GET /health`

Returns service health status.

**Response**:
```json
{
  "status": "ok",
  "service": "assistant"
}
```

## Error Responses

| Status | Condition |
|--------|-----------|
| 401 | Missing or invalid authentication |
| 502 | Upstream service (LiteLLM/Assistant) unreachable |
| 503 | No active endpoint for the requested model/project |

## Streaming

When `stream: true` is set, the response is Server-Sent Events (SSE)
with `text/event-stream` content type.  Each chunk is a standard OpenAI
`chat.completion.chunk` object.

The gateway and UMRS proxy both support SSE passthrough with
`proxy_buffering off` and long read timeouts (600s).

## Relationship to OpenAI API

The Assistant API (`/gw/chat/*`) and the OpenAI API (`/gw/llm/v1/*`)
are **parallel products** with distinct contracts:

- **OpenAI API**: stateless, no platform semantics, stable interoperability
- **Assistant API**: project-aware, tool-aware, MCP-aware, platform-native

Neither is "legacy" or "the default."  Clients choose based on their
needs: benchmarking and SDK compatibility use the OpenAI API; OpenWebUI,
`r4 chat`, and IDE integrations that want project intelligence use the
Assistant API.
