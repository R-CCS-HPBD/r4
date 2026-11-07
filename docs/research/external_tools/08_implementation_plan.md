# 08 — Implementation Plan: Folding Surveyed Capabilities into r4 + UMRS

This plan sequences the gaps identified in
[07_gap_analysis.md](07_gap_analysis.md) into phases. The ordering optimises
for: **(1) low-risk UX wins first, (2) integration-policy compliance
(UMRS owns durable / orchestration / RAG; r4 owns the integration
layer), (3) HPC-first defaults, and (4) keeping core lean per the
openclaw "what we will not merge" pattern.**

> Each item lists **owner**, **gap id** from §07, **dependencies**, and a
> rough **effort tier** (S / M / L / XL).
>
> **No item is allowed to** add a parallel job store, parallel workflow
> registry, parallel endpoint provisioner, or parallel RAG runtime in
> `r4/` or `gateway/`. UMRS is the implementation owner per
> `.github/instructions/development.instructions.md`.

---

## Phase 0 — Foundations (must precede everything else)

### 0.1 Anti-roadmap doc (B10)
- **Owner**: docs.
- **Effort**: S.
- Publish `docs/r4/ANTI_ROADMAP.md` mirroring openclaw `VISION.md` "what
  we will not merge". List: parallel job stores in r4/gateway, agent
  hierarchies, wrapper providers without security/policy gain, MCP
  duplication, etc.

### 0.2 Provider interface (A13, B4) and structured-IO contract (A34)
- **Owner**: r4.
- **Effort**: M.
- Define `r4.providers.Provider` ABC: `health()`, `chat()`, `stream()`,
  `cost_per_1k()`, `latency_ms()`, `model_capabilities()`. Existing
  LiteLLM client becomes one implementation; `MiniRivaultProvider`
  (delegating to UMRS endpoints) becomes another; `OllamaProvider`,
  `OpenAICompatibleProvider` round it out.
- Define `r4.transport.JsonMode` contract: when `--json` is set, **only**
  JSON tokens may go to stdout; everything else routes to stderr or a
  log file. Mirrors claw-code's `structuredIO`/`remoteIO` lessons.

### 0.3 Repo + user config split (A2)
- **Owner**: r4.
- **Effort**: S.
- Add `.r4/config.toml` (repo, committed) + `.r4/local.toml` (gitignored)
  precedence layered above `~/.config/r4/config.toml`. Document keys
  that may live in each layer.

### 0.4 Per-repo agent briefing (`R4.md`) (A1)
- **Owner**: r4.
- **Effort**: S.
- `r4 init` (under `r4 config init` for back-compat) scaffolds an
  `R4.md` with detected language stack, recommended models, allowed
  MCPs. The companion / agent / chat read it on session start.

---

## Phase 1 — UX & operator polish

### 1.1 `r4 doctor` (A21)
- **Owner**: r4.
- **Effort**: S.
- Probes: gateway health, UMRS health, provider reachability (per the
  Provider interface), active mini-rivault status, auth state, S3
  egress, EgressGuard policy summary.
- JSON mode: `r4 doctor --json` for CI / scripts.

### 1.2 `r4 onboard` (A22)
- **Owner**: r4.
- **Effort**: M.
- Guided flow: gateway URL → auth (OIDC / API-key / PAT) → project →
  default provider → optional `R4.md` scaffold → optional MCP
  registration → final `r4 doctor` run.

### 1.3 Cost / token surface (A24)
- **Owner**: r4 + gateway.
- **Effort**: S.
- Gateway already has LiteLLM token counts. Surface per-turn tokens +
  estimated cost in TUI footer; `/usage off|tokens|full` slash command.

### 1.4 Mode cycling (A25)
- **Owner**: r4.
- **Effort**: S.
- `Shift+Tab` in TUI cycles `interactive | autopilot | plan | review`.
  Modes are pure prompt+permission presets; no new orchestration.

### 1.5 Single-shot install (A32)
- **Owner**: r4 packaging.
- **Effort**: S.
- `curl https://<host>/install.sh | bash` downloads a wheel, creates a
  venv under `~/.local/share/r4/venv`, drops `r4` on `$PATH`. Document
  brew tap separately (deferred).

---

## Phase 2 — Agent harness completeness

All in `r4/`. None of these belong in UMRS or the gateway.

### 2.1 Tool confirmation router (A4)
- **Effort**: M.
- Adopt goose's per-tool-call interactive prompt. Risk levels
  (low/med/high) come from MCP manifest; default policy in
  `.r4/local.toml`.

### 2.2 PreToolUse / PostToolUse hooks (A5)
- **Effort**: M.
- Hook runner in r4 chat layer. Hooks can mutate the request, deny it,
  rewrite the response, or attach a result note. Audit emission goes
  through gateway → UMRS so the audit trail stays centralised.

### 2.3 First-class built-in tools (B7, A8, A9)
- **Effort**: M.
- Add `AskUserQuestion`, `ToolSearch`, `TodoWrite`, `WebFetch`,
  `WebSearch`, `FinalOutput`, `Notebook*` to `r4.agent` tool registry.
- `ToolSearch` queries the MCP catalogue by description so we stop
  serialising the entire MCP catalogue every turn.

### 2.4 Sub-agent / Task primitives (A7)
- **Effort**: L.
- `Task*` tool family. Each child task is a UMRS durable job; r4
  orchestrates the parent loop. Reuse durable-job tracking — no
  parallel store.

### 2.5 Large response handler / spill (A10)
- **Effort**: M.
- When a tool result exceeds a configurable threshold (default 256 KB),
  spill to S3 via UMRS, return a presigned reference + summary, and
  let the model fetch on demand through `WebFetch`.

### 2.6 Final-output tool (A11)
- **Effort**: S.
- Required terminator for `--json` mode; optional for streaming TUI.

### 2.7 Slash command registry (A34, B5)
- **Effort**: M.
- Pull existing TUI commands into a registry module. Add:
  `/login` / `/logout` already exist via `r4 auth`; surface them.
  `/model`, `/cost`, `/usage`, `/permissions`, `/compact`, `/diff`,
  `/export`, `/session`, `/restart`, `/think <level>`.

---

## Phase 3 — Provider routing & model agnosticism

### 3.1 Smart router (A12)
- **Owner**: r4 (decision) + gateway (routing).
- **Effort**: L.
- Direct port of openclaude `smart_router.py` strategy on top of the
  Provider interface from §0.2. Strategies: `latency | cost | balanced |
  hpc-first`. Auto-fallback on provider failure. Rolling latency /
  error tracking persisted in UMRS context store.
- HPC-aware addition: respect `execution_authority` invariant — once a
  job is bound to a Slurm mini-rivault it stays there.

### 3.2 External provider adapters (A13)
- **Effort**: M each.
- `OllamaProvider`, `OpenAICompatibleProvider` (Bedrock / Vertex /
  OpenRouter / DeepSeek / together / Groq …). Live behind the
  Provider ABC. Mini-rivault remains the default and only HPC-bound
  provider.

### 3.3 Secondary LLM flows (A29)
- **Effort**: M.
- Per-flow model selection: `intent_model`, `format_model`,
  `guard_model`. Default to a small Ollama or LiteLLM-routed cheap
  model; fall back to the primary if unavailable.

---

## Phase 4 — RAG & ingestion (UMRS-owned)

### 4.1 Connector framework (A14)
- **Owner**: UMRS.
- **Effort**: XL (multi-quarter; ship connector-by-connector).
- Borrow Onyx's `interfaces.py` + `factory.py` + `registry.py` shape.
  Implement first connectors: GitHub, Confluence, Notion, Slack,
  Google Drive. Each connector emits durable UMRS jobs (docfetching →
  docprocessing).
- Worker shape: split UMRS workers into named lanes
  `docfetching` / `docprocessing` / `light` / `heavy` / `monitoring` /
  `kg` / `beat`. Adopt onyx's mandatory `expires=` rule for every
  enqueued job.

### 4.2 Hybrid retrieval (A17)
- **Owner**: UMRS.
- **Effort**: L.
- Add BM25 alongside pgvector (PostgreSQL `pg_trgm` + `tsvector`, no
  Vespa). Hybrid score in retrieval profile.

### 4.3 Knowledge graph (A16)
- **Owner**: UMRS.
- **Effort**: L.
- Background `kg` worker that clusters indexed docs and builds entity /
  relation tables.

### 4.4 Federated connectors (A18)
- **Owner**: UMRS + gateway.
- **Effort**: M.
- "Search-without-indexing" path for NDA/PHI sources.

### 4.5 Deep-Research loop (A15)
- **Owner**: r4 (loop) + UMRS (artifact).
- **Effort**: L.
- Bounded multi-step loop with depth budget; emits a structured
  research-report artifact stored in UMRS.

---

## Phase 5 — Skills, recipes, and registry

### 5.1 Bundle-style skills (A26)
- **Owner**: r4 (loader) + UMRS (registry).
- **Effort**: M.
- A *skill* is a directory with `SKILL.md` (declaration), optional
  `mcp.json` (MCP servers it brings), optional `config.json`. Loaded by
  `r4 ask companion` / `r4 chat` and registered with UMRS for project /
  user / system scoping.
- Honour the openclaw discipline: skills go to a registry first; only
  rare cases ship in core.

### 5.2 Recipes (A6)
- **Owner**: UMRS (definition) + r4 (CLI).
- **Effort**: L.
- Adopt goose's YAML recipe format. Add `umrs/src/umrs/recipes/`:
  validation, malware/security scanner (per A31), execution as durable
  workflow, deeplink format. `r4 recipe run path/to/foo.yaml`.
- Include an `r4-self-test.yaml` smoke recipe and run it in CI.

### 5.3 Recipe / extension scanner (A31)
- **Owner**: UMRS.
- **Effort**: M.
- Static scan of recipe YAML and any MCP server code at registration
  time. Borrow goose's `recipe-scanner/` shape.

### 5.4 External skill / recipe registry (A27)
- **Owner**: r4 client + a hosted service.
- **Effort**: L (hosted side); M (client).
- Out of scope for monorepo work but reserve the CLI shape:
  `r4 skill install <name>`, `r4 recipe install <name>`.

### 5.5 Custom distributions (A23)
- **Owner**: r4.
- **Effort**: M.
- Document how a site can ship a preconfigured r4 wheel with pinned
  models / MCPs / branding. RCCS-r4 is the reference distribution.

---

## Phase 6 — Editor & headless integration

### 6.1 LSP integration (A3)
- **Owner**: r4.
- **Effort**: L.
- Adopt copilot-cli's config shape: `~/.r4/lsp.json` user-level,
  `.r4/lsp.json` repo-level. r4 starts language servers per the file,
  exposes go-to-def / hover / diagnostics as agent tools.

### 6.2 Agent Client Protocol (A30)
- **Owner**: r4.
- **Effort**: L.
- Implement an ACP server in r4 so VS Code / Cursor / Zed can drive
  r4. Track goose's `goose-acp` and openclaw `acpx` for protocol
  decisions.

---

## Phase 7 — Scheduling & automation

### 7.1 Cron / scheduled runs (A20)
- **Owner**: UMRS (scheduler) + r4 (CLI).
- **Effort**: M.
- `r4 schedule create --cron "0 9 * * 1" --recipe daily-summary.yaml`.
  Reuses durable jobs.

### 7.2 Webhooks / inbound triggers
- **Owner**: gateway + UMRS.
- **Effort**: M.
- Generic inbound trigger surface (n8n already covers most cases; this
  is the missing direct hook).

---

## Phase 8 — Voice & multi-modal UX

### 8.1 Server-side voice (A19)
- **Owner**: UMRS (vision_service-style adapter for STT/TTS) + r4 SDK.
- **Effort**: L.
- Reuse the BentoML adapter pattern. ElevenLabs / sherpa-onnx / whisper
  adapters as additional vision_service plugins (rename or generalise
  to `inference_service`).

---

## Phase 9 — Multi-tenant hardening

### 9.1 Per-project schema isolation (A28)
- **Owner**: UMRS.
- **Effort**: XL.
- Borrow onyx's `alembic_tenants/` shape and `DynamicTenantScheduler`.
  Migrate gradually; row-level today → schema-level for new projects.

### 9.2 Untrusted-input enumeration (A33)
- **Owner**: docs.
- **Effort**: S.
- `docs/SECURITY_UNTRUSTED_SURFACES.md`: n8n payloads, Slack/Discord
  inbound, GitHub issue/PR text, RAG-retrieved docs, MCP tool returns.
  Encode handling rules in `EgressGuard` policy.

---

## Cross-cutting non-goals (per anti-roadmap)

- ❌ Implement a job store, scheduler, or workflow registry inside `r4/`
  or `gateway/`. Always delegate to UMRS.
- ❌ Add a separate vector index in r4. Hybrid retrieval lives in UMRS.
- ❌ Re-implement MCP transport in r4. Use the Hatchling runtime
  (already in UMRS).
- ❌ Add agent-hierarchy frameworks (manager-of-managers planners) as a
  default. Sub-agents (§2.4) are flat tasks, not nested planners.
- ❌ Bundle 50+ provider adapters into core. Provider interface
  (§0.2 / §3.2) lets users add their own; only mini-rivault, LiteLLM,
  Ollama, OpenAI-compatible ship by default.

---

## Suggested execution order

1. **Sprint 1 (Phase 0)** — foundations. Provider interface, JSON mode,
   config split, `R4.md`, anti-roadmap.
2. **Sprint 2 (Phase 1)** — `r4 doctor`, `r4 onboard`, cost surface,
   mode cycling, install script.
3. **Sprint 3 (Phase 2)** — agent harness completeness. Hooks, tool
   confirmation, built-in tools, sub-agent tasks, spill.
4. **Sprint 4 (Phase 3)** — smart router + external providers +
   secondary LLM flows.
5. **Sprint 5+ (Phase 4)** — RAG ingestion / connectors / hybrid /
   KG. Long-running, deliver per-connector.
6. **Parallel** — Phase 5 (skills/recipes/scanner), Phase 6 (LSP/ACP),
   Phase 7 (cron), Phase 8 (voice), Phase 9 (tenants) can begin once
   Phase 0 lands.

Each phase ends with an updated end-to-end test in
`tests/run_e2e_tests.py` and (where it touches HPC) a green
`tests/run_hpc_tests.sh` on RCCS-cloud.
