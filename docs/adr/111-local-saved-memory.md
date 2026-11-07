# ADR-111: Local saved memory: one private file per fact, apart from instructions and knowledge

**Status:** Accepted (2026-11-07, maintainer: "go for implementation with your plan"; the project-scope key was agent-decided under the 2026-09-24 delegation)
**Date:** 2026-11-07
**Relates to:** ADR-039 (taint fence: D-4 reuses its registry), ADR-104 (permission ladder: D-4 adds one rung), ADR-108 (worktrees share their repository's memory, D-2), ADR-036 F2 (the session journal mirror no longer carries memory, D-2), ADR-076 (lesson check, D-1), gap row D-28 (context control); TUI audit 2026-10-17 (`/memory` retirement)

## Context

What was true on `main` at `b3dc97a6` (verified 2026-11-07):

- **Local memory was one global JSON file with no writer a person could reach.**
  `ContextManager` loaded `~/.config/r4/companion/memory.json` into a dict and
  put every entry into every turn as `key: value` lines, uncapped and the same
  for every project. Its only writer was the Jupyter magic
  `%companion_remember`. The console's `/remember` and `/forget` went away
  with the Python TUI in `57e25b80` (2026-05-26), a side effect of the port,
  not a decision.
- **The console's `/memory` never worked.** It called `/gw/reflective-memory/*`
  and `/gw/context/memory`; the edge has no location for either and
  `/umrs/context` is not mounted. The TUI audit retired it on 2026-10-17. The
  UMRS reflective-memory store behind it is platform-global (no project field)
  and admin-only since H-1. That retirement was right and stands.
- **Session rows brought deleted memory back.** Every save copied the memory
  dict into the row (and through ADR-036 F2 into the server journal); every
  load wrote it over the live dict. A resumed session therefore restored facts
  deleted since, and its next `remember` wrote them to disk for every session.
  `r4/tests/test_companion/test_session_memory_snapshot.py` reproduces it on
  the old code (2 failed).
- **Lessons are automatic, invisible memory.** `SteeringLoop.observe_turn`
  records a lesson when a reply looks like a correction (the decision model
  may veto it, ADR-076), keeps 50 in `lessons.json` and puts the last 10 into
  every turn. Nobody could list or delete them.
- **Practice elsewhere** (survey of Claude Code, Codex CLI, Gemini CLI, Cursor,
  Windsurf, Goose, OpenCode, Aider, ChatGPT, Letta, LangGraph/LangMem, mem0,
  Zep and the Claude memory tool, 2026-11-07): tiers loaded in a fixed order;
  instructions written by people and reviewed (`AGENTS.md`, `CLAUDE.md`) kept
  apart from learned memory, which stays private; a small always-loaded index
  with details read on demand; one fact per file with type, source and date;
  optimistic concurrency for shared stores; view, edit, delete and off
  switches for the person; no secrets. Memory is an attack surface: a prompt
  injection that saves itself is read in later sessions as trusted text
  (SpAIware against ChatGPT and Windsurf; OWASP Agentic Top 10 ASI06). No
  vendor promotes memory into shared knowledge without review.

## Decision

**D-1 Five kinds of context, five owners.**

| Kind | Holds | Written by | Shared with |
|---|---|---|---|
| Instructions: `R4.md` → `AGENTS.md` → … `CLAUDE.md` (existing chain, `r4/src/r4/briefing.py`) | rules every session follows | people, through review (git) | the team |
| Saved memory (this ADR) | who the researcher is, their corrections, a project's working notes, pointers | the person; the lead agent with permission | nobody: private, on the user's machine |
| Lessons (`lessons.json`, existing) | rules taken from the researcher's corrections | the steering loop, automatically | nobody |
| Session facts (`set_session_context`, existing) | facts for this session | the session | not persisted |
| Knowledge (UMRS RAG and knowledge graphs, evidence and scientific memory) | domain facts, data, results, claims with evidence | ingest and reviewed proposals | the project |

A domain fact a colleague on the project would need, and that can be cited or
checked, is knowledge, never memory. A fact that can be derived from the code,
the docs or the knowledge base is not saved at all.

**D-2 Storage.** `R4_MEMORY_DIR`, else `~/.config/r4/memory`, read at each call
(`r4/src/r4/companion/memory_store.py`):

- `user/`: all projects;
- `projects/<key>/`: `<gateway host>--<project_id>` for a UMRS project, so one
  project id on two gateways stays apart; without a project,
  `workspace--<repo name>-<hash>` of the main checkout, so a linked worktree
  (ADR-108) shares its repository's memory. The key follows the client's
  current project at each call: a project switch reaches the next turn.

One `<name>.md` per fact: front matter `name` (`[a-z0-9._-]`, at most 64),
`type` (`user`, `feedback`, `project`, `reference`), `description` (one line,
at most 200), `created`, `updated`, `source`, `source_session`, optional
`expires` (`YYYY-MM-DD`); body at most 4000 characters. Files are 0600 and
directories 0700. `MEMORY.md` per scope is a view for people, rewritten after
every change and never read back. No copy leaves the machine: a session row's
`memory` column is always `{}` and an older row's snapshot is ignored on
restore, so the journal mirror carries none.

**D-3 Reading.** Each turn carries an index, one line per live fact
(`- name [type]: description`; expired facts dropped), capped at
`R4_MEMORY_INDEX_CHARS` (default 6000 characters) with a marker that says so.
It rides the per-turn context part of the user message (wave 80), never the
system message. Bodies come from `memory_read`.

**D-4 Writing.** People: `/memory` in the console, `r4 memory` in the shell,
`%companion_remember` in Jupyter. The agent: `memory_write` and
`memory_forget`, attached by the lead `CompanionSession` only, never through
`get_builtin_tools` (team roles, plan agents, recipes and sub-agents read the
index the lead's turn carries and never write). Concurrency: replacing or
deleting a fact takes the sha the writer read (`expected_sha`); a changed file
is a conflict and the writer reads again; each scope takes a file lock around
check-and-write; a write that looks like a credential is refused (the
scanner's secret patterns, minus its 40-character row, which matches git
shas). Permissions: `memory_read` is read-only (`SAFE_TOOLS`); writes ask in
chat and plan modes; a new rung, `_rung_memory_taint`, asks for them in
**every** mode once untrusted content (RAG, document, lab, MCP or web results:
the ADR-039 registry) has entered the session, fenced, so no "always" and no
rule answers it. That mark is sticky: `TaintRegistry.ever_tainted` has no TTL
and travels in the session metadata (`untrusted_seen`) across a save and a
resume. The rung also covers the other ways to a memory file: any call that
is not read-only whose arguments name the memory folder, `R4_MEMORY_DIR`,
`r4 memory` or the Jupyter magic (a `file_write` there, a shell `r4 memory
add`). Names are `[a-z0-9._-]`; text with other characters gets an 8-hex
digest of the original, so two Japanese keys never collapse into one fact.
The memory tools are always on: a skill filter never hides them.

**D-5 Promotion is a person's act with its own review.**
`/memory promote <name> briefing` appends the fact to the current checkout's
`R4.md` (a linked worktree's own) under `## From saved memory`, for review
with git; `/memory promote <name>
knowledge <graph>` stages a knowledge-graph proposal that changes nothing
until someone runs `r4 knowledge review approve <proposal>`. Nothing is
promoted automatically.

**D-6 The person's controls.** `/memory [list | show | add | forget | lessons
[clear | forget <n>] | promote | path | off | on]`; `r4 memory` has the same
verbs except `off` and `on`. `/memory off` holds for the session (kept in its
metadata): no index, no lessons shown or recorded, and the memory tools
refuse.

**D-7 Legacy.** `memory.json` is copied once into the user scope (`type:
project`, `source: import`; secret-shaped entries stay behind), marked by
`.imported-memory-json`; the old file is kept. The UMRS reflective-memory store
stays admin-only with no edge location. Memory shared across devices or with a
team would need its own ADR (a per-user, per-project namespace on UMRS with
versioned, sha-checked writes); team rules already have `R4.md`.

### Why

- Instructions that change silently are a security and review problem; private
  notes that need review are friction. Keeping them apart is what every
  harness surveyed converged on.
- One file per fact, an index rebuilt from the files, and sha preconditions
  remove the lost-update and resurrection classes the single JSON file and the
  session snapshot had.
- The taint rung makes the one dangerous path (untrusted text that saves
  itself for later sessions) a person's decision without asking for every
  harmless note in autopilot.
- Keying by UMRS project matches how researchers work (a project spans
  repositories and HPC sites); the repository fallback covers local work.

## Consequences

- Positive: a person can see, edit, delete and switch off everything the agent
  remembers; memory follows the project; deleted facts stay deleted;
  concurrent tabs do not overwrite each other; injected text cannot persist
  itself in autopilot.
- Cost: memory stays on one machine. Remote venue sessions (ADR-046) and team
  roles see no saved memory of their own. The taint rung is session-wide: after
  any untrusted result, every memory write in that session asks. Lessons stay
  a separate file (folding 50 automatic lessons into the index would cost
  prompt budget every turn), visible through `/memory lessons`. The rung
  matches argument text, so an obfuscated path (built in a script) can still
  reach the folder in autopilot; the same class already exists for `R4.md` and
  `AGENTS.md`, which any file tool can write, and closing it for every
  instruction file is a separate decision.
- NOT in scope: `companion/knowledge_base.py` (session-scoped research-loop
  patterns over the bridge), cross-device sync, retrieval over memory bodies
  (the index is small by design).

## Alternatives Considered

- **A memory store on UMRS.** Rejected for now: a second server store with its
  own authorization (AGENTS.md rule 10 asks for an ADR before any such store),
  cross-tenant risk, and no offline use. D-7 names what it would take.
- **Key project memory by git repository only** (as Claude Code does). Rejected
  as the primary key: an r4 project spans repositories and sites; it is the
  fallback.
- **Keep one JSON file.** Rejected: no metadata, lost updates between tabs, and
  the resurrection defect.
- **Fold lessons into fact files.** Rejected: 50 automatic entries in the index
  would crowd it every turn; listing and deleting them through `/memory`
  solves the visibility problem.
- **Promote automatically when a fact recurs.** Rejected: an injected fact would
  reach the team's instructions or the project's knowledge.

## Rollout-status (2026-11-07)

All seven sub-decisions landed on `wave/local-memory` (ledger (231)); client
only, nothing to deploy: it ships with the next r4 release.

| Leg | Evidence |
|---|---|
| M0, the session-row resurrection (D-2) | `test_session_memory_snapshot.py` (2): both failed on the old code ("deleted since" came back), pass now |
| Store, scopes, sha checks, secrets, index, legacy import (D-2, D-3, D-7) | `test_memory_store.py` (29), including 16 concurrent writers losing nothing, a worktree sharing its repository's key, a non-UTF-8 file skipped |
| Agent tools and the rung (D-4) | `test_memory_tools.py` (15): read-only `memory_read`; chat asks; autopilot asks after untrusted content, also once the excerpts expired, after a resume, and for `file_write` / shell `r4 memory`; lead session only; `/memory off` keeps lessons out |
| Person's verbs and promotion (D-5, D-6) | `test_memory_commands.py` (9); `test_tui_audit_commands.py` (`/memory` uses the local store, calls no gateway) |
| Prompt | both default-prompt snapshots regenerated: the only change is the three tool names |

An independent review (2026-11-07) found four P1s before merge: the taint
mark expired with the excerpts and was lost on resume; other tools could
reach the memory folder; a non-UTF-8 file broke every turn; non-ASCII names
collapsed into one. It also found six P2s. All are fixed above with tests.

## Related

- [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-104](104-one-mode-one-permission-decision.md),
  [ADR-108](108-level-hierarchy-worktrees-umrs-scopes.md),
  tui-function-audit-2026-10-17.md
- `.github/instructions/companion.instructions.md` (storage paths)
- Survey sources: code.claude.com/docs/en/memory; learn.chatgpt.com/docs/customization/memories;
  geminicli.com/docs/tools/memory; cursor.com/docs/context/rules;
  platform.claude.com/docs/en/managed-agents/memory;
  platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool;
  docs.letta.com/guides/agents/memory-blocks; langchain-ai.github.io/langmem;
  genai.owasp.org (Agentic Top 10, ASI06); arXiv 2309.02427 (CoALA)
