# 02 — goose

> Source: [tmp/goose/README.md](../../../tmp/goose/README.md),
> [tmp/goose/AGENTS.md](../../../tmp/goose/AGENTS.md),
> [tmp/goose/CONTRIBUTING_RECIPES.md](../../../tmp/goose/CONTRIBUTING_RECIPES.md),
> directory walk of `crates/`, `extensions/` (in `goose-mcp`), and
> `documentation/`.

## What it is

`goose` (originally Block, now `aaif-goose`) is an **on-machine open-source AI
agent** for engineering tasks. CLI + Electron desktop app + a backend daemon
called `goosed`. Multi-LLM, multi-MCP, recipe-driven workflows.

## Philosophy

- **Local-first**: runs on the user's machine, not a SaaS.
- **Provider-neutral**: pluggable `Provider` trait; multi-model configuration
  is encouraged ("optimize for performance and cost").
- **Extensions over core**: capability ships as MCP extensions in
  `crates/goose-mcp` rather than bloating core.
- **Recipes = portable workflows**: declarative YAML files the user can share,
  fork, and run (`goose run --recipe foo.yaml`).
- **DCO sign-off** required (`git commit -s`).
- **Simplicity invariants** in `AGENTS.md`: no comments restating what code
  does; no "optional booleans" when default-false suffices; no over-defensive
  code; no error context that doesn't add information.
- **Custom distributions** are first-class (CUSTOM_DISTROS.md): you can ship
  your own goose with preconfigured providers, extensions, and branding.

## Architecture (Rust workspace)

```
crates/
├── goose              # core: agent loop, providers, recipes, security,
│                      #       sessions, scheduler, OAuth, OTel, permissions,
│                      #       context_mgmt (compaction), download_manager
├── goose-acp          # Agent Client Protocol (ACP) bindings
├── goose-acp-macros   # ACP proc macros
├── goose-cli          # CLI entry point
├── goose-server       # `goosed` HTTP backend (binary)
├── goose-sdk          # client SDK
├── goose-mcp          # bundled MCP servers / extensions
├── goose-test         # test utilities
└── goose-test-support # test helpers

ui/desktop/            # Electron app (talks to goosed)
evals/open-model-gym/  # benchmark harness
recipe-scanner/        # recipe security scanner
oidc-proxy/            # OIDC auth bridge
services/              # auxiliary services
goose-self-test.yaml   # self-test recipe (run in CI)
```

Notable internal modules in `crates/goose/src/agents/`:

- `agent.rs`, `extension.rs`, `extension_manager.rs`,
  `extension_malware_check.rs`, `validate_extensions.rs`
- `mcp_client.rs`, `platform_tools.rs`, `platform_extensions/`,
  `builtin_skills/`
- `subagent_handler.rs`, `subagent_task_config.rs`,
  `subagent_execution_tool/`
- `tool_confirmation_router.rs`, `tool_execution.rs`,
  `tool_inspection.rs`, `tool_monitor.rs`
- `final_output_tool.rs`, `large_response_handler.rs`, `retry.rs`
- `prompt_manager.rs`, `reply_parts.rs`, `schedule_tool.rs`

Other strong cross-cutting modules under `crates/goose/src/`:

- `oauth/`, `recipe/`, `recipe_deeplink.rs`,
- `scheduler.rs`, `scheduler_trait.rs`,
- `permission/`, `security/`, `tool_inspection.rs`,
- `context_mgmt/` (compaction), `token_counter.rs`,
- `gateway/`, `goose_apps/`, `dictation/`,
- `otel/`, `tracing/`, `posthog.rs`, `logging.rs`.

## Provider / model abstraction

- `Provider` trait in `crates/goose/src/providers/base.rs`. Plugging in a new
  provider = implement the trait.
- Multi-model configuration is documented as a first-class feature
  (different models for planner vs. worker vs. coder).
- Recipes can pin a provider/model.

## Recipes (key idea)

YAML workflows in `documentation/src/pages/recipes/data/recipes/`:

```yaml
version: 1.0.0
title: Web Scraper
description: ...
instructions: ...
extensions:
  - type: builtin
    name: developer
activities: [...]
prompt: |
  Detailed prompt with {{ parameter_name }}
parameters:
  - key: parameter_name
    input_type: string
    requirement: required
```

- Recipes are auto-validated (YAML, required fields, structure, **security
  scanned** by `recipe-scanner/`).
- `goose-self-test.yaml` lives at the repo root and is used as the
  end-to-end smoke test for the agent itself.
- "Recipe deeplinks" (`recipe_deeplink.rs`) — a recipe URL the user can
  share that opens directly in the desktop app.

## Security & sandboxing

- Per-tool **permission system** (`crates/goose/src/permission/`).
- **Tool confirmation router** (`tool_confirmation_router.rs`) — interactive
  approval flow.
- **Extension malware check** (`extension_malware_check.rs`) and
  `validate_extensions.rs` before loading any MCP extension.
- **Recipe scanner** as a separate top-level crate.
- OIDC proxy as a sidecar for auth.

## Observability

- OTel + Prometheus + Loki are first-class (matches our existing
  `observability/` setup).
- PostHog telemetry (opt-in).
- ACP (Agent Client Protocol) for editor / IDE integration.

## Standout ideas worth borrowing

1. **Recipes as YAML, validated, security-scanned, deeplinkable** — much
   more shareable than ad-hoc prompts. r4 has pipeline definitions but no
   user-shareable recipe model.
2. **Custom distributions** as a documented release path (`CUSTOM_DISTROS.md`)
   — institutions could ship a preconfigured "RCCS-r4" with pinned providers
   and extensions.
3. **Subagent execution** as a first-class subsystem
   (`subagent_handler.rs`, `subagent_execution_tool/`) — distinct from r4's
   `teamup` (which is HPC-tuning specific). Generic subagent fan-out belongs
   in core.
4. **Tool confirmation router** — interactive permission prompt before
   risky tool calls (we currently have policy at gateway, but no in-flow
   user prompt).
5. **`tool_inspection.rs` + `tool_monitor.rs`** — explicit observability of
   the agent's tool use, separate from prompt logs.
6. **`large_response_handler.rs`** — prevents context blowup by spilling
   huge tool outputs to disk and returning a reference. We need this on HPC
   where outputs can be enormous.
7. **`final_output_tool.rs`** — agent must commit its final answer through
   a structured tool, simplifying transport contract.
8. **Self-test recipe in repo root** validated in CI — better than scattered
   ad-hoc smoke tests.
9. **ACP (Agent Client Protocol)** — open protocol for editor integrations.
   Worth tracking as the cross-tool standard.
10. **`Provider` trait + multi-model config** with explicit cost/latency
    knobs — our current `model_detect.py` is thin compared to this.
11. **Scheduler** (`scheduler.rs`, `scheduler_trait.rs`) for cron-style
    recurring agent runs.
12. **OIDC proxy as separate sidecar** — clean separation from `goosed`,
    similar to our gateway/sidecar boundary.
