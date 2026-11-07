# ADR-033: TUI-First Agentic UMRS Setup with Default Skills and Knowledge Buildup

**Status:** Proposed
- Date: 2026-08-28
- Supersedes the lane-simplification discussion toward a TUI-main engagement
  model; complements ADR-030 (companion scoped-tool pinning) and ADR-031
  (knowledge-plane ownership).

## Context

The UMRS onboarding tax for a human is legible in the
`umrs experience add` ritual: a hand-written ~20-field JSON
(`xp_name`, `sql_username`, `slurm.partition/nodelist`, `s3.bucket_alias`,
`data.type/mime_type`, `model.vector_size`, Rivault-internal
`model_type` URIs with `{LOCAL_PORT}` placeholders, `sql.*` plumbing),
followed by `umrs reload` and `umrs data upload <alias>`. The user only
wants: a searchable / workflow-consumable bucket for their data.

Analysis confirmed the CLI simplification angles (auto-derived
experiences, one-verb `r4 files upload`, project-scoped default
experience), but a CLI-first direction has a structural cap: every
platform concept still needs its own command surface, and every user
still has to learn the right command for the right concept.

The companion TUI already does the setup work users actually perform in
practice ("upload the clip", "list workflows", "make the DOCX"). An
agent path has the right shape for the setup ritual too:

1. The user states intent in natural language
2. The agent synthesizes the UMRS call (experience JSON derivation,
   bucket alias binding, upload, catalog registration) against the
   platform's always-up-to-date guide/tools

What the TUI today lacks is canonical, authoritative knowledge of UMRS
setup procedures, so the agent invents plausible-but-wrong flows
(experience JSON structures, wrong bucket lane choices, ghost commands).

## Decision

**TUI-first:** the r4 TUI (companion) is the principal human entry for
UMRS setup. The r4 CLI remains the **back-management** surface
(auth/profile/project lifecycle, endpoint provisioning, admin
inspection, recovery from breakage) — and stays documented for that
purpose, but the "new user wants to upload their data and run
something" narrative goes through the TUI.

**Skills-loaded-by-default:** the companion ships a set of default
skills that codify canonical UMRS setup flows. Skills are loaded at
session start from the built-in skill directory, so the agent's
behavior is derived from reviewed, versioned documents (not from
chat hygiene / system prompt length). Skills live in the package,
ship with the wheel, and are visible to users
(`/skills` → "platform-setup" group).

Proposed initial skill set:

| Skill | Teaches the agent to |
|---|---|
| `umrs-bucket-upload` | Right-lane choice: r4 files (workflow I/O) vs umrs experience (knowledge/RAG); create minimal experience via derivation (sql_username slug, alias from name+modality, defaults from site.yaml); upload via correct lane. |
| `umrs-experience-derive` | Generate a minimal experience spec from only: name, modality, model choice (validated against `model list`). Everything else is platform default. |
| `umrs-workflow-run` | Register → trigger → watch → fetch. Bucket layout gotcha (input vs output prefix) encoded. Avoid curl fallbacks. |
| `scientific-catalogue-build` | Register a project's corpus as a catalogue: dedupe-aware ingest, catalog scope bindings, knowledge-graph spine buildup (subjects/relations from uses). |
| `knowledge-graph-build` | When the user's data lands, run the ADR-024/027 knowledge-plane verbs to build a KG subgraph around the user's subjects; keep it project-scoped; emit a `.kb` export on demand. |

**Knowledge buildup as side effect:** when the agent does any
upload/setup, it also emits the scientific catalogue + knowledge graph
additions as **durably-scoped work items**, not as conversational
mentions. Concretely: the `scientific-catalogue-build` /
`knowledge-graph-build` skills teach the agent to call the K-plane
verbs (`knowledge ingest`, `lab catalog ingest`, `kg graph-*`) with the
active project as the scope binder, so by the end of a setup
interaction the user gets a first-class knowledge artifact they can
view (`r4 console`, `r4 knowledge ...`) — not just a bucket and a
transcript of what the agent did.

## TUI affordance: hidden trace details, user-expandable

During long agentic reasoning the TUI previously destroyed two things:
`<think>` reasoning content (replaced by a `reasoning collapsed`
placeholder) and code blocks longer than `MAX_CODE_LINES=28` (clipped
`… N more lines`). Users reported needing to read the full lines during
agentic setup/demos — especially in tmux, where the legacy terminal
scrollback is unavailable.

Landed 2026-08-28 in the Ink TUI tree (commit `42c10b1a`; that tree was
removed 2026-09-11 per ADR-014 — the behaviour lives on in the OpenTUI v3
console, `packages/r4-tui-v3/`):

- `Ctrl+O` toggles a **details mode**: `expandThinking` keeps the full
  reasoning as `│`-prefixed dim quote lines, and code blocks drop the
  28-line clip. Code-masked literal `<think>` tags are never expanded
  by accident.
- `R4_TUI_EXPAND_DETAILS=1` env var starts in details mode.
- Toggle is idle AND busy active, doesn't gate on modal dialogs, and
  composes with the scrollable transcript (Ctrl+E page + wheel scroll
  work in tmux).
- Also flips the `Ctrl+T` mouse-capture toggle from an always-on
  surprise to a user-visible toggle with the copy-hint in the scroll
  indicator.

## Consequences

- The CLI keeps `r4 files upload/download`, `r4 knowledge ...`, `r4 mcp ...`
  — all of which the agent calls internally — but **user-facing
  documentation teaches the TUI narrative first**, and the CLI surface
  only as the back-management layer.
- Skills become part of the release artifact: the set is versioned
  with the wheel and changes via PR review like other platform
  contracts.
- Knowledge-graph + catalog are durable side effects of agentic
  setup, not a post-hoc cataloging step the user has to remember to run.
- Details-mode keeps the principal path legible during setup debugging,
  and un-clips long agent reasoning (humans-reviewing-the-agent mode).

## Future work (not now)

- Wire `umrs experience add` server-side auto-provisioning behind the
  `umrs-bucket-upload` skill (so agent never has to author the long
  JSON when "upload for workflow" intent is clear).
- Experience ✗ project conceptual merge (E3 from the earlier analysis)
  as a server-side projection, not a user burden.
- Agent-driven `kg graph-*` bindings keyed off project metadata so the
  KG spine persists across sessions without manual curation.
