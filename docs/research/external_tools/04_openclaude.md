# 04 — openclaude

> Source: [tmp/openclaude/README.md](../../../tmp/openclaude/README.md),
> [tmp/openclaude/PLAYBOOK.md](../../../tmp/openclaude/PLAYBOOK.md),
> [tmp/openclaude/smart_router.py](../../../tmp/openclaude/smart_router.py),
> [tmp/openclaude/ollama_provider.py](../../../tmp/openclaude/ollama_provider.py),
> directory walk of `tmp/openclaude/src/`.

## What it is

`openclaude` is a **fork of the leaked Claude Code** TypeScript source with one
focused addition: an **OpenAI-compatible provider shim** so you can drive the
full Claude Code tool set with any model (GPT-4o, DeepSeek, Gemini via
OpenRouter, Llama via Ollama / LM Studio / Together / Groq, Mistral, Azure,
ChatGPT Codex backend …).

It is essentially "Claude Code, but model-agnostic", with two helper Python
scripts (`smart_router.py`, `ollama_provider.py`) that demonstrate provider
auto-routing.

## Philosophy

- **One env-var contract** to swap models:
  `CLAUDE_CODE_USE_OPENAI=1`, `OPENAI_API_KEY=…`, `OPENAI_BASE_URL=…`,
  `OPENAI_MODEL=…`. That's it.
- **Local-first is a feature, not an afterthought**: `PLAYBOOK.md` walks the
  user through Ollama as the default backend.
- **Profile system** (`profile:init`, `dev:profile`) — provider config is
  saved to `.openclaude-profile.json`, with named presets (`dev:fast`,
  `dev:code`).
- **Doctor commands** (`doctor:runtime`, `doctor:report`) for validating
  reachability + key + base URL before launching.
- **Hardening checks** (`hardening:check`, `hardening:strict`) integrate
  smoke + typecheck.
- **Codex auth re-use** — reads `~/.codex/auth.json` automatically so users
  who already use Codex CLI don't have to rotate keys.

## Architecture

The TypeScript tree under `src/` mirrors the leaked Claude Code shape almost
1-for-1 (and is the same shape `claw-code/src/` (Python) ports). Key dirs:

| Dir | Role |
|-----|------|
| `tools/` | Built-in tool implementations (Bash, FileRead/Write/Edit, Glob, Grep, LSP, MCP family, Notebook, REPL, Schedule/Cron, Skill, Task*, Team*, TodoWrite, ToolSearch, WebFetch/Search, …) |
| `commands/` | Slash commands |
| `assistant/` | Agentic loop, streaming |
| `cli/` | Entry, `structuredIO`, `remoteIO`, transports |
| `services/` | API client, MCP, OAuth, plugin ops, settings sync |
| `plugins/` | Plugin loader and bundled plugins |
| `skills/` | Skill registry, loader, bundled skills, MCP skill builders |
| `hooks/` | PreToolUse / PostToolUse hooks |
| `query/` | Query engine |
| `tasks/`, `coordinator/` | Task / sub-agent execution |
| `voice/`, `vim/` | Optional surfaces |
| `remote/`, `upstreamproxy/` | Remote upstream-proxy mode |

### `smart_router.py` (the interesting bit)

A standalone async router that:

1. Loads a `Provider` catalogue (OpenAI, Gemini, Ollama, …).
2. Pings each on startup, scoring by **latency + cost + error rate**.
3. Routes each request to the lowest-score provider, with **strategy**
   `latency | cost | balanced`.
4. Tracks rolling latency / errors per provider; updates scores over time.
5. **Auto-fallback** to the next-best provider on failure
   (`ROUTER_FALLBACK=true`).
6. Treats Ollama as always-configured (no API key needed) and zero-cost.

```python
class Provider:
    name, ping_url, api_key_env, cost_per_1k_tokens
    big_model, small_model
    latency_ms, healthy, request_count, error_count, avg_latency_ms

    def score(self, strategy: str = "balanced") -> float:
        # error_penalty = error_rate * 500
        # 'latency' | 'cost' | 'balanced'
```

## Standout ideas worth borrowing

1. **One-env-var provider shim** so users can point r4 chat at any
   OpenAI-compatible endpoint (GPT, Gemini-via-OpenRouter, DeepSeek, local
   Ollama) without changing code paths. Map onto our LiteLLM, but expose a
   user-facing flag.
2. **Smart Router with latency/cost/error scoring + auto-fallback**. Today
   r4 + UMRS picks one mini-rivault per request; a router that understands
   "this small turn can go to Ollama for free; that big one needs the GPU
   mini-rivault" is a major cost win on HPC.
3. **Provider profiles in a JSON file** with named presets
   (`dev:fast`, `dev:code`) — analogous to our `r4 config init`, but
   user-switchable per-shell.
4. **`r4 doctor` command** — health check that probes gateway, UMRS, the
   active model, the active mini-rivault, and the user's auth state.
   `openclaude doctor:runtime` is a great UX template.
5. **Codex auth file re-use** — the equivalent for us is automatically
   picking up an existing `gh` / `git` token or a `~/.cache/huggingface/`
   token when staging models, instead of asking again.
6. **Hardening sweep** as a single command (`hardening:strict`) that
   bundles type + smoke + provider reachability — would simplify CI here.
7. **Goal-driven profile init** (`profile:init --goal coding`) — choose a
   model by *intent* not by name; we could surface this as
   `r4 config init --goal {chat,coding,research,vision}`.
8. **Treat Ollama / LM Studio / vLLM / LiteLLM as interchangeable** at the
   provider layer — proves that "rivault static model name" can coexist
   with "any external OpenAI-compatible endpoint" without code changes.
