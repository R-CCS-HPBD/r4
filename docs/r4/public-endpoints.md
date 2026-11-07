# Public Endpoints

R4 can connect to any OpenAI-compatible cloud API — no UMRS gateway
required. This is useful for quick experiments, for teams that already
have API keys, or when HPC infrastructure is unavailable.

## Quick Start

```bash
# OpenAI
r4 up --public openai --api-key sk-xxxx --model gpt-4o

# Google Gemini
r4 up --public gemini --api-key AIza-xxxx --model gemini-2.0-flash

# AWS Bedrock (bearer token)
r4 up --public bedrock --api-key "$AWS_BEARER_TOKEN_BEDROCK" \
      --aws-region us-east-2 --model anthropic.claude-sonnet-4-6

# AWS Bedrock (SigV4 — uses ~/.aws/credentials)
r4 up --public bedrock --model anthropic.claude-sonnet-4-6

# Then use normally
r4 chat "Hello!"
r4                     # opens companion TUI
```

## Supported Providers

All providers below expose an OpenAI-compatible `/v1/chat/completions`
endpoint and accept a `Bearer` API key.

| Shortcut     | Base URL                                                            | Get an API key |
|-------------|---------------------------------------------------------------------|----------------|
| `openai`    | `https://api.openai.com`                                            | [platform.openai.com](https://platform.openai.com/api-keys) |
| `gemini`    | `https://generativelanguage.googleapis.com/v1beta/openai`           | [aistudio.google.com](https://aistudio.google.com/apikey) |
| `groq`      | `https://api.groq.com/openai`                                       | [console.groq.com](https://console.groq.com/keys) |
| `together`  | `https://api.together.xyz`                                           | [api.together.ai](https://api.together.ai/settings/api-keys) |
| `openrouter`| `https://openrouter.ai/api`                                          | [openrouter.ai](https://openrouter.ai/keys) |
| `deepseek`  | `https://api.deepseek.com`                                           | [platform.deepseek.com](https://platform.deepseek.com/api_keys) |
| `fireworks` | `https://api.fireworks.ai/inference`                                 | [fireworks.ai](https://fireworks.ai/account/api-keys) |
| `mistral`   | `https://api.mistral.ai`                                             | [console.mistral.ai](https://console.mistral.ai/api-keys) |
| `bedrock`   | (native Converse API — not OpenAI-compat)                            | AWS Console / `aws configure` |

You can also pass any full URL directly. A site-run endpoint (an
institute's own LiteLLM, vLLM or Ollama server) has no shortcut; pass its
URL:

```bash
r4 up --public https://my-vllm-server.example.com:8000 --model my-model
```

## Provider Examples

### OpenAI

```bash
r4 up --public openai --api-key sk-proj-xxxx --model gpt-4o
r4 chat "Explain quantum computing in one sentence."
r4 down --public
```

Popular models: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`, `o1`, `o1-mini`

### Google Gemini

```bash
r4 up --public gemini --api-key AIza-xxxx --model gemini-2.0-flash
r4 chat "What is the capital of France?"
r4 down --public
```

Popular models: `gemini-2.0-flash`, `gemini-2.5-pro`, `gemini-2.5-flash`

### AWS Bedrock

Bedrock uses the native Converse API, not the OpenAI endpoint. Two auth
modes:

```bash
# Bearer token (e.g. from AWS SSO / presigned credentials)
r4 up --public bedrock --api-key "$TOKEN" --aws-region us-east-2 \
      --model anthropic.claude-sonnet-4-6

# SigV4 (standard AWS credentials — requires boto3)
r4 up --public bedrock --aws-region us-east-1 \
      --model anthropic.claude-3-5-sonnet-20241022-v2:0
```

Bare model IDs like `anthropic.claude-sonnet-4-6` are automatically
resolved to inference profile IDs (`us.anthropic.claude-sonnet-4-6`).

### Groq

```bash
r4 up --public groq --api-key gsk_xxxx --model llama-3.3-70b-versatile
r4 chat "Hello!"
r4 down --public
```

### DeepSeek

```bash
r4 up --public deepseek --api-key sk-xxxx --model deepseek-chat
r4 chat "Hello!"
r4 down --public
```

### Together AI

```bash
r4 up --public together --api-key xxxx \
      --model meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
r4 chat "Hello!"
r4 down --public
```

### Custom / Self-Hosted (vLLM, Ollama, etc.)

Any OpenAI-compatible server works:

```bash
r4 up --public https://my-vllm.internal:8000 --model my-model
r4 chat "Hello!"
r4 down --public
```

## Removing a Public Endpoint

```bash
r4 down --public
```

This clears the endpoint URL, provider settings, **and the stored API
key** from the active profile. If you were previously authenticated with
a gateway JWT, you will need to re-authenticate:

```bash
r4 auth login --api-key <gateway-jwt>
```

## Interactive Mode

Running `r4 up` without arguments enters interactive mode. Select `p`
for public:

```
Provision [s]ingle, [m]ultiple, or [p]ublic endpoint? [s/m/p]: p

Supported provider shortcuts:
  openai      https://api.openai.com
  gemini      https://generativelanguage.googleapis.com/v1beta/openai
  groq        https://api.groq.com/openai
  ...

Provider or URL: openai
API key (bearer token, leave blank if none): sk-xxxx
Model name (e.g. gpt-4): gpt-4o
```

## Checking Your Configuration

```bash
r4 config show
```

When a public endpoint is active, the output shows the endpoint instead
of the gateway:

```
Active runtime context:
  Profile: default
  Project: (not set)
  Endpoint: https://api.openai.com (public)
  Model: gpt-4o
  Auth: configured
```

## Security Notes

- **API keys are stored** in `~/.config/r4/config.json` with file
  permissions `0600` (owner-read/write only).
- **Endpoint keys are isolated from gateway auth.** The public endpoint
  API key (`endpoint_api_key`) is stored separately from the gateway JWT
  (`api_key`). It is sent only to the public endpoint, never to the
  gateway. Switching between public endpoints and the gateway does not
  overwrite or leak credentials.
- **API keys are never logged** in debug output or error messages.
- **Display is masked**: `r4 up --public` shows only the first 8
  characters of the key.
- **`r4 down --public` clears the endpoint key** but leaves gateway
  authentication intact.
- **Use environment variables** (`R4_ENDPOINT_API_KEY`) to avoid
  persisting keys to disk. The env var takes precedence over the stored
  value.
- **Do not commit** `.r4/local.toml` files containing API keys.
  The `.r4/local.toml` file is intended to be git-ignored.

## Environment Variables

You can configure a public endpoint entirely via environment variables
without persisting anything:

```bash
export R4_ENDPOINT_URL="https://api.openai.com"
export R4_ENDPOINT_API_KEY="sk-xxxx"
export R4_MODEL="gpt-4o"

r4 chat "Hello!"
```

For Bedrock:

```bash
export R4_ENDPOINT_PROVIDER="bedrock"
export AWS_REGION="us-east-1"
export R4_MODEL="anthropic.claude-sonnet-4-6"
# AWS credentials via standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
# or via AWS_PROFILE

r4 chat "Hello!"
```
