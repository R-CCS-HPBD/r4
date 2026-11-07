# 07 — Gap Analysis: r4 + UMRS vs. the Surveyed Projects

This document compares the current capability surface of **r4 + UMRS**
against the six surveyed tools and lists:

- **A.** Functions r4+UMRS does *not* have today.
- **B.** Functions r4+UMRS has, but where another project's
  implementation is meaningfully better.
- **C.** Functions r4+UMRS already covers well (no action).

Baseline drawn from `r4/README.md`, `r4/src/r4/_cli/`, `r4/src/r4/`,
`umrs/README.md`, `umrs/src/umrs/`, and the integration policy in
`.github/instructions/development.instructions.md`.

> **Constraint reminder.** UMRS is the implementation owner for durable
> jobs, endpoint provisioning, workflow registration & execution, and RAG
> indexing/retrieval. r4 owns the integration layer (CLI / SDK / TUI /
> companion / chat / Jupyter / search / teamup / agent). All gaps below
> are tagged with the **owner** that should implement them.

---

## A. Missing in r4 + UMRS

### A1. Per-repo agent briefing file (`R4.md` / `AGENTS.md`)

- **Seen in**: claw-code (`CLAW.md`), goose (`AGENTS.md` + `.goosehints`),
  openclaw (`AGENTS.md`), copilot-cli (`.github/lsp.json`).
- **Today**: r4 has no per-repo file the agent reads on startup. The
  Companion has a workspace concept but it's not a file convention.
- **Owner**: r4. Add a discovery rule in `r4 ask companion` /
  `r4 ask agent` / `r4 chat` for `R4.md` (and `.r4/` for overrides).

### A2. Repo-level + user-level layered config split

- **Seen in**: claw-code (`.claw.json` shared, `.claw/settings.local.json`
  local), copilot-cli (user `~/.copilot/lsp-config.json`, repo
  `.github/lsp.json`).
- **Today**: r4 has a single profile file (`~/.config/r4/config.toml`).
  No repo-level overrides for model / pinned MCPs / pinned mini-rivault.
- **Owner**: r4.

### A3. LSP integration

- **Seen in**: copilot-cli (`/lsp`, dedicated config), claw-code Rust
  port (`crates/lsp`), claude-code-leak tooling.
- **Today**: r4 has nothing. The agent has zero language-server context
  on edit operations.
- **Owner**: r4.

### A4. Tool confirmation router (interactive permissions)

- **Seen in**: goose (`tool_confirmation_router.rs`), copilot-cli
  ("preview every action"), claw-code TS (`/permissions`).
- **Today**: r4 enforces policy at the gateway, but there is no
  in-conversation user prompt before risky tool calls.
- **Owner**: r4 (TUI / chat layer) + UMRS (policy decision).

### A5. PreToolUse / PostToolUse hooks

- **Seen in**: claw-code TS (parsed *and* executed). Rust port parses
  but doesn't execute → known gap they want to close.
- **Today**: r4 has no per-tool hook pipeline (only egress audit).
- **Owner**: r4 (hook contract) + UMRS (audit emission).

### A6. Recipes / shareable workflow YAML

- **Seen in**: goose (full recipe system + scanner + deeplinks +
  contributor flow + self-test recipe).
- **Today**: r4 has `r4 ask agent create / run` and UMRS workflow
  upload, but there is no portable "user-shareable agent recipe" format
  with parameters, validated structure, and a registry.
- **Owner**: UMRS (workflow definition + scanner) + r4 (CLI surface).

### A7. Sub-agent / Task / Team primitives

- **Seen in**: claw-code TS (`Task*` and `Team*` tool families), goose
  (`subagent_handler.rs`, `subagent_execution_tool/`).
- **Today**: r4 `teamup` is HPC-tuning specific. There is no generic
  "agent spawns sub-agent task" primitive callable as a tool.
- **Owner**: r4 (orchestration) + UMRS (job tracking for child tasks).

### A8. `ToolSearch` tool

- **Seen in**: claw-code TS (model searches the tool catalogue by
  description before binding).
- **Today**: r4 lists MCP tools via `r4 mcp list` but the model itself
  cannot search; we ship the entire catalogue every turn.
- **Owner**: r4 (MCP client) — meaningful token savings on HPC.

### A9. `AskUserQuestion` structured-prompt tool

- **Seen in**: claw-code TS, openclaw `sessions_*`.
- **Today**: r4 only supports free-text turns; agents cannot pause and
  emit a structured "I need answer X" event.
- **Owner**: r4.

### A10. Large response handler / output spill

- **Seen in**: goose (`large_response_handler.rs`).
- **Today**: r4 returns full tool outputs into context, even when those
  outputs are GB-scale (HPC reality). No spill-to-disk + reference.
- **Owner**: r4 + UMRS (S3 spill is already available, just unused for
  tool outputs).

### A11. Final-output tool

- **Seen in**: goose (`final_output_tool.rs`).
- **Today**: r4 transports answers as the last assistant message;
  there's no enforced "structured commit of final answer" tool.
- **Owner**: r4.

### A12. Smart router (latency + cost + error scoring, auto-fallback)

- **Seen in**: openclaude (`smart_router.py`).
- **Today**: r4 picks *one* mini-rivault; falls through to LiteLLM if
  configured. No per-request scoring across providers, no fallback on
  failure, no rolling latency tracking.
- **Owner**: r4 (decision) + gateway (routing). UMRS exposes mini-rivault
  health.

### A13. Provider-as-plugin layout

- **Seen in**: openclaw (~50 provider extensions), goose (`Provider`
  trait).
- **Today**: r4 / UMRS tightly couple "model = mini-rivault on Slurm".
  There is no first-class adapter for "use external Bedrock / Vertex /
  OpenRouter / Ollama on the user's laptop".
- **Owner**: r4 (provider abstraction) + UMRS (only authoritative for
  mini-rivault provider).

### A14. Connector framework (Slack / Notion / Jira / GDrive / GitHub …)

- **Seen in**: onyx (~55 connectors with a registry & factory).
- **Today**: UMRS RAG only ingests user-uploaded files via HTTP / ZeroMQ.
- **Owner**: UMRS (RAG ingestion plane).

### A15. Deep-Research bounded loop

- **Seen in**: onyx (`backend/onyx/deep_research/dr_loop.py`).
- **Today**: `r4 ask search` exists but is single-pass agentic search.
  There is no multi-step research loop with depth budget that emits a
  structured research report artifact.
- **Owner**: r4 (loop) + UMRS (artifact storage).

### A16. Knowledge-graph layer over RAG

- **Seen in**: onyx (`backend/onyx/kg/`, KG worker).
- **Today**: UMRS RAG is flat embeddings; no entity / relation graph.
- **Owner**: UMRS.

### A17. Hybrid (vector + keyword) retrieval

- **Seen in**: onyx (Vespa).
- **Today**: UMRS uses pgvector only; no BM25 / keyword side.
- **Owner**: UMRS.

### A18. Federated / live search connectors

- **Seen in**: onyx (`federated_connectors/`).
- **Today**: r4 has no concept of "search a remote system live without
  indexing it" (relevant for NDA / PHI corpora).
- **Owner**: UMRS RAG + gateway.

### A19. Voice mode (server-side TTS / STT)

- **Seen in**: onyx (`voice/`), openclaw (talk-voice, voice-call,
  elevenlabs, sherpa-onnx-tts), goose (`dictation/`).
- **Today**: r4 has no voice surface.
- **Owner**: r4 SDK (client) + UMRS (server-side STT/TTS as another
  mini-rivault adapter).

### A20. Cron / scheduled agent runs

- **Seen in**: claw-code TS (`ScheduleCronTool`), goose
  (`scheduler.rs`), openclaw (cron tool, gmail-pubsub, webhooks).
- **Today**: UMRS has durable jobs but no user-facing scheduled agent
  invocation primitive.
- **Owner**: UMRS (scheduler) + r4 (`r4 schedule`).

### A21. Doctor / runtime diagnostic command

- **Seen in**: openclaude (`doctor:runtime`, `doctor:report`), openclaw
  (`openclaw doctor`), goose (diagnostics & reporting page).
- **Today**: r4 has `r4 health` (single ping). No comprehensive doctor.
- **Owner**: r4.

### A22. Onboard / guided installer

- **Seen in**: openclaw (`openclaw onboard --install-daemon`),
  copilot-cli (single curl-bash).
- **Today**: r4 has `r4 config init` (interactive but minimal).
- **Owner**: r4.

### A23. Custom distributions

- **Seen in**: goose (`CUSTOM_DISTROS.md`).
- **Today**: r4 has no documented path to ship "RCCS-r4" with pinned
  providers, branding, and preinstalled MCPs.
- **Owner**: r4.

### A24. Cost / token surface in product

- **Seen in**: claw-code TS (`/cost`, `costHook`, `cost-tracker`),
  copilot-cli (premium-request meter), openclaw (`/usage`,
  `model-usage` skill).
- **Today**: r4 has no per-turn cost / token surfacing.
- **Owner**: r4 (UI) + gateway (token counts already in LiteLLM).

### A25. Mode-cycling UI (interactive / autopilot / plan / review)

- **Seen in**: copilot-cli (Shift+Tab), claw-code TS (`/plan`,
  `/review`).
- **Today**: r4 TUI has only single-shot vs. interactive.
- **Owner**: r4.

### A26. Skills as bundle plugins (`SKILL.md` + optional MCP + config)

- **Seen in**: openclaw `skills/`, claw-code TS `skills/`, claude-code
  bundled-skills registry.
- **Today**: r4 has MCP servers and pipelines but no minimal
  *skill-bundle* author surface (a single Markdown file declaring the
  capability).
- **Owner**: r4 (skill loader) + UMRS (skill registry).

### A27. External plugin/skill registry (analog to ClawHub / Onyx
connectors registry)

- **Seen in**: openclaw (ClawHub), goose (recipe cookbook).
- **Today**: r4 has no public registry.
- **Owner**: r4 (CLI client) + a separate hosted registry.

### A28. Multi-tenant schema isolation

- **Seen in**: onyx (`alembic_tenants/`, `DynamicTenantScheduler`).
- **Today**: UMRS is single-schema. Project isolation is row-level.
- **Owner**: UMRS.

### A29. Secondary LLM flows (cheaper model for guard / format / intent)

- **Seen in**: onyx (`secondary_llm_flows/`).
- **Today**: r4 + UMRS use the same model for everything.
- **Owner**: r4 (router) + gateway (LiteLLM model selection).

### A30. Agent Client Protocol (ACP) editor surface

- **Seen in**: goose (`goose-acp`), openclaw (`extensions/acpx`).
- **Today**: r4 has no ACP-style editor protocol. VS Code / Cursor /
  Zed cannot drive r4 through a standard wire protocol.
- **Owner**: r4.

### A31. Recipe / extension malware/security scanner

- **Seen in**: goose (`recipe-scanner/`, `extension_malware_check.rs`).
- **Today**: r4 + UMRS validate manifests but do no static-scan of
  user-supplied MCP code or pipeline definitions.
- **Owner**: UMRS (registration boundary).

### A32. Single-shot install (curl / brew / winget / npm)

- **Seen in**: copilot-cli, openclaw, onyx (`curl … | bash`), goose.
- **Today**: r4 ships only as `pip install -e ./r4`.
- **Owner**: r4 packaging.

### A33. Untrusted-input discipline list

- **Seen in**: openclaw (DM pairing, untrusted GitHub data list).
- **Today**: r4 has prompt-injection mentions but no enumerated list of
  untrusted surfaces (n8n inputs, Slack DMs, pipeline outputs from
  unscanned MCPs).
- **Owner**: docs (this repo).

### A34. Structured / remote I/O transport

- **Seen in**: claw-code TS (`structuredIO.ts`, `remoteIO.ts`,
  `transports/`).
- **Today**: r4 mixes streaming text with status lines. No formal
  contract that JSON mode emits *only* JSON.
- **Owner**: r4.

### A35. ZeroMQ / file-transfer extension as a separate plugin

- **Seen in**: openclaw `file-transfer/`.
- **Today**: r4 has `client.data` (ZeroMQ via `r4[umrs]`). The plugin
  shape would let users add e.g. `globus-transfer` themselves.
- **Owner**: r4 transport.

---

## B. Implemented in r4+UMRS, but better elsewhere

### B1. RAG ingestion → onyx connectors are dramatically broader

UMRS today: file upload + workflow registration. Onyx: ~55 connectors
with a `factory.py` + `registry.py` and `connector_runner.py` plus
docfetching/docprocessing watchdogs. *Borrow the registry pattern,
keep our existing UMRS-as-owner stance.*

### B2. Job orchestration → goose subagent + scheduler are richer

UMRS durable jobs are crash-safe but flat. goose has `subagent_handler.rs`,
`subagent_execution_tool/`, `scheduler.rs`, `tool_inspection.rs`, and a
`tool_monitor.rs` for cross-cutting observability. *Borrow the
sub-agent + scheduler abstractions; keep our durable-tracking core.*

### B3. MCP integration → claw-code TS + onyx are deeper

r4 has an MCP client and `r4 mcp register`. claw-code TS adds
`MCPTool`, `McpAuthTool`, `ListMcpResourcesTool`, `ReadMcpResourceTool`,
`mcpSkillBuilders` (skills can register MCP tools on demand). Onyx
embeds an MCP server directly in `backend/onyx/mcp_server/`. *Adopt
on-demand MCP skill builders.*

### B4. Provider abstraction → goose `Provider` trait + openclaw
provider-as-extension is cleaner

r4 routes through LiteLLM only. *Lift our LiteLLM mapping into a
`Provider` interface so the Slurm mini-rivault, an external Bedrock,
and a local Ollama all look the same.*

### B5. CLI ergonomics → openclaw + copilot-cli are more polished

openclaw `onboard` / `doctor`, copilot-cli `/login` / `/model` /
`/lsp` / `/feedback` / mode-cycling, claw-code slash command crate.
*Reorganise our slash commands and add `r4 doctor` + `r4 onboard`.*

### B6. Memory / context — openclaw "one active memory plugin"
invariant + claw-code TS session/team memory

r4's `chat_core/` has session memory but no enforced single-source
discipline and no "team memory". *Adopt the invariant; document it.*

### B7. Tool surface — claw-code TS has ~30 first-class tools

r4's agent ships fewer built-in tools (file/bash/RAG). *Add
`AskUserQuestion`, `ToolSearch`, `TodoWrite`, `WebFetch`, `WebSearch`,
`Schedule`, `Notebook*` as first-class.*

### B8. Settings split — claw-code's two-file model is cleaner

r4 has a single profile. *Adopt shared/local split.*

### B9. Distribution — copilot-cli's brew/winget/npm matrix is stronger

*Add a curl install + brew tap.*

### B10. Documentation discipline — openclaw `VISION.md` "what we will
not merge" list

We have `INTEGRATION_POLICY.md` but no "anti-roadmap." *Publish one.*

---

## C. Already covered (no action)

- Durable jobs / attempts / commit pointer (UMRS) — better than goose's
  flat jobs.
- Multi-runtime (Slurm / K8s / Compose) via ExecutionTarget +
  RuntimeWrapper — closest external analog is openclaw sandbox modes
  but ours is HPC-aware.
- EgressGuard + audit logs — only project that codifies this.
- Mini-RiVault as security boundary — unique among surveyed projects.
- Presigned-URL file access from gateway — no surveyed project goes
  this far in keeping inference offline.
- Project / RBAC / API-key OIDC auth (gateway).
- Jupyter magics (`r4[jupyter]`) — only goose's recipes come close.
- HPC orchestration semantics — none of the surveyed projects target
  HPC; this is r4 + UMRS's defensible moat.
- Vision serving with BentoML adapter pattern — unique.
- Hatchling MCP runtime + per-MCP isolation — already aligned with
  openclaw's plugin SDK boundary discipline.
- Multi-agent teamup for HPC tuning — domain-specific, unmatched.
