# 06 — openclaw

> Source: [tmp/openclaw/README.md](../../../tmp/openclaw/README.md),
> [tmp/openclaw/VISION.md](../../../tmp/openclaw/VISION.md),
> [tmp/openclaw/AGENTS.md](../../../tmp/openclaw/AGENTS.md),
> directory walk of `packages/`, `extensions/`, `skills/`, `apps/`,
> `src/`, `deploy/`, `security/`.

## What it is

OpenClaw is a **personal multi-channel AI assistant** that runs on the user's
own devices. Its tagline is "the AI that actually does things." The product
is the assistant — the **Gateway** is "just the control plane." It speaks on
~25 messaging channels (WhatsApp, Telegram, Slack, Discord, iMessage, Matrix,
Teams, Signal, etc.) plus macOS / iOS / Android companion apps with voice
and a live "Canvas" UI.

## Philosophy

- **Local-first, single-user**. Runs on your devices, with your rules.
- **Core stays lean** — most capability ships as **plugins** (`extensions/`).
- **Two plugin styles**: *bundle-style* (skills + MCP + config — preferred)
  and *code plugins* (deeper runtime hooks — used when bundle-style is
  insufficient).
- **Plugin-SDK is the only legal cross-boundary surface**. Core does not
  reach into extension internals; extensions do not reach into other
  extensions.
- **ClawHub** is the external plugin / skill registry. The bar to bundle
  anything in core is intentionally high — promotion goes through ClawHub
  with vetted publishers.
- **Treat inbound DMs as untrusted input**. DM pairing is the default
  posture — unknown senders see a pairing code, not the bot.
- **Sandboxes for non-`main` sessions** (Docker / SSH / OpenShell backends),
  with per-tool allow/deny lists.
- **Telegraph-style root rules** in `AGENTS.md`: extension-owned behaviour
  belongs in the extension; only generic seams go to core; "no agent-
  hierarchy frameworks" listed under "What we will not merge."
- **Tooling discipline**: pnpm only, `oxfmt` (not Prettier), `oxlint`,
  `tsgo` typecheck (no `tsc --noEmit`), Vitest only, deterministic prompt
  cache ordering, dynamic-import lint rule.

## Architecture (TypeScript pnpm monorepo)

| Path | Role |
|------|------|
| `src/` | Core: gateway, agent, channels, plugins loader, plugin-sdk-internal |
| `src/plugin-sdk/*` | Public SDK extensions consume |
| `src/channels/*` | Channel implementations (private; SDK exposes seams) |
| `src/gateway/protocol/*` | Gateway RPC protocol |
| `src/agents/*` | Agent runtime |
| `packages/` | `plugin-sdk`, `sdk`, `memory-host-sdk`, `plugin-package-contract` |
| `extensions/` | ~120 plugins: every channel, every provider, browser, file-transfer, document-extract, image/video/music generation, voice, MCP servers, knowledge stores (`memory-lancedb`, `memory-wiki`), cron, webhooks, …  |
| `skills/` | ~50 user-style "bundle skills": `coding-agent`, `clawhub`, `notion`, `obsidian`, `tmux`, `weather`, `summarize`, `taskflow`, `gh-issues`, … |
| `apps/` | macOS app, iOS node, Android node |
| `ui/` | Control UI (web) |
| `deploy/`, `security/`, `qa/`, `git-hooks/` | Ops/test/security |
| `docs/` | All concept / channel / tool / platform docs |

### Channel families (extensions/)

Every messaging surface is its own extension: `telegram/`, `whatsapp/`,
`slack/`, `discord/`, `signal/`, `imessage/`, `bluebubbles/`,
`matrix/`, `msteams/`, `googlechat/`, `feishu/`, `line/`, `nostr/`,
`mattermost/`, `nextcloud-talk/`, `irc/`, `tlon/`, `qqbot/`, `wechat`/
`zalo` … plus `webhooks/`, `qa-channel/`, `qa-lab/`, `qa-matrix/`.

### Provider families (extensions/)

`openai/`, `anthropic/`, `google/`, `mistral/`, `groq/`, `together/`,
`deepseek/`, `qwen/`, `kimi-coding/`, `moonshot/`, `xai/`, `cerebras/`,
`fireworks/`, `deepinfra/`, `litellm/`, `vllm/`, `sglang/`, `ollama/`,
`lmstudio/`, `vercel-ai-gateway/`, `cloudflare-ai-gateway/`, `byteplus/`,
`amazon-bedrock/`, `amazon-bedrock-mantle/`, `azure-speech/`, `arcee/`,
`copilot-proxy/`, `github-copilot/`, `microsoft-foundry/`, `nvidia/`,
`openrouter/`, `perplexity/`, `tencent/`, `qianfan/`, `volcengine/`,
`venice/`, `voyage/`, `synthetic/`, `chutes/`, `kilocode/`, `tokenjuice/`,
… ~50 in total.

### Memory / skills / MCP

- `extensions/active-memory/`, `memory-core/`, `memory-lancedb/`,
  `memory-wiki/`. **One memory plugin active at a time** by design.
- `extensions/skill-workshop/` — author skills.
- `extensions/acpx/` — Agent Client Protocol-X.
- `extensions/file-transfer/`, `document-extract/`, `web-readability/`,
  `firecrawl/`, `tavily/`, `exa/`, `searxng/`, `brave/`, `duckduckgo/`.
- Voice & media: `elevenlabs/`, `inworld/`, `senseaudio/`, `talk-voice/`,
  `voice-call/`, `tts-local-cli/`, `image-generation-core/`, `comfy/`,
  `runway/`, `fal/`, `media-understanding-core/`, `video-generation-core/`.

### Skills (`skills/`)

User-facing bundle plugins, each with a `SKILL.md`:

> e.g. `coding-agent/SKILL.md` — a single-file declaration of a coding
> agent skill (no compiled code).

Skills include integrations like `notion/`, `obsidian/`, `things-mac/`,
`tmux/`, `taskflow/`, `taskflow-inbox-triage/`, `summarize/`, `gh-issues/`,
`github/`, `apple-notes/`, `apple-reminders/`, `bear-notes/`, `weather/`,
`spotify-player/`, `sonoscli/`, `peekaboo/`, `nano-pdf/`, `video-frames/`,
`session-logs/`, `model-usage/`, `clawhub/`, `coding-agent/`,
`skill-creator/`.

### Security / sandboxing

- Default: `main` session is unsandboxed for the user's own host (single-
  user trust assumption).
- Non-`main` sessions: `agents.defaults.sandbox.mode: "non-main"` enables
  Docker / SSH / OpenShell sandbox backends.
- Default allow list: `bash`, `process`, `read`, `write`, `edit`,
  `sessions_*`. Default deny: `browser`, `canvas`, `nodes`, `cron`,
  `discord`, `gateway`.
- DM pairing flow on every social channel; `openclaw doctor` surfaces
  misconfigured DM policies.
- Treat all GitHub titles / comments / branch names as **untrusted data**
  (relevant to our prompt-injection defenses).

### Operator UX

- `/status`, `/new`, `/reset`, `/compact`, `/think <level>`,
  `/verbose on|off`, `/trace on|off`, `/usage off|tokens|full`,
  `/restart`, `/activation mention|always`.
- `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn`.
- `openclaw onboard` — guided installer.
- `openclaw doctor` — misconfig surfacing.
- `openclaw devices` — node pairing.
- `openclaw pairing approve <channel> <code>` — DM allowlist.

### Observability

`extensions/diagnostics-otel/`, `extensions/diagnostics-prometheus/`.

## Standout ideas worth borrowing

1. **Bundle-style skills** as the *preferred* way to extend an agent —
   user writes `SKILL.md` (no code), with optional MCP + config
   bundled. Lower than a Python plugin, higher than a system prompt.
   Maps onto our existing user MCP path but with a much cleaner author
   surface.
2. **Strict plugin-SDK boundary**: core never imports extension internals,
   extensions never reach into other extensions, only `plugin-sdk` is
   public. We have a similar policy for UMRS-vs-r4-vs-gateway in the
   integration policy; codifying it for *r4 plugins* / user MCPs would
   prevent drift.
3. **One-active-memory-plugin invariant** — clear UX rule for memory
   stores. Avoids fragmented context. We currently have one memory
   subsystem; documenting the invariant prevents future bloat.
4. **DM pairing as default for any external channel** + `openclaw doctor`
   to surface risky configs. Our equivalent is "external HTTP clients
   never get presigned URLs"; the *pattern* of an interactive auth
   bootstrap is valuable for n8n / Slack / Teams integrations.
5. **External plugin registry (ClawHub)** with vetted publishers,
   provenance, and security review — separate from core. We could host
   r4 pipelines / MCP servers / RAG retrievers in an analogous registry
   instead of bundling everything.
6. **`onboard` + `doctor` CLIs** as polished, opinionated guided flows.
7. **Provider-as-extension layout** — every provider is its own package
   under `extensions/`. Our provider abstraction lives behind LiteLLM;
   exposing it as a pluggable surface (Slurm vLLM / external OpenAI /
   Ollama / Bedrock / OpenRouter) would let users add providers without
   editing core.
8. **Untrusted-data discipline** in operator instructions — they call
   out that GitHub comments / commit messages / branch names are
   untrusted. Our prompt-injection defense should explicitly enumerate
   such surfaces.
9. **Sandbox backends** (Docker / SSH / OpenShell) abstracted behind a
   `sandbox.mode` config — this matches our `RuntimeWrapper` pattern
   and reinforces it as the right shape.
10. **Hard "what we will not merge" list** in `VISION.md` — explicit
    no-bundle scope (no agent-hierarchy frameworks, no full-doc
    translations, no commercial wrapper integrations, no MCP that
    duplicates existing paths). We should publish an equivalent for r4
    so contributors know what *won't* be accepted.
11. **Live Canvas / agent-driven UI** — agent renders to a structured UI
    surface (A2UI). For r4, the analog is structured Jupyter outputs;
    formalising "agent emits structured artifacts the client renders"
    would make the SDK / Companion / Jupyter surfaces more uniform.
12. **Tooling discipline as ratchet**: pnpm-only, oxfmt-only, tsgo-only,
    Vitest-only, no Jest flags, no `tsc --noEmit`. We should pick one
    and stick to it for r4 (we already lean on `ruff` + `mypy`/`pylance`,
    but the discipline is uneven).
