# ADR-039: Untrusted-content provenance — taint → never-auto

**Status:** Proposed (with implementation in WP-02, same PR)
- **Date:** 2026-09-06
- **Related:** ADR-030 (scoped tool pinning), ADR-035 (enforcement-point
  matrix), agent safety instructions (content fences). *(038 deliberately
  left free for the ADR-027 renumber in the docs pass.)*

## Context

Tool output from untrusted content surfaces — knowledge/RAG chunks,
`lab_file_read`, MCP server responses, web search, ingest documents —
contains text written by users and processes outside the current session's
trust boundary. When the LLM later builds a tool call whose arguments
contain that text verbatim (or near-verbatim), nothing currently
distinguishes "the human said `rm -rf /tmp/x`" from "a document the agent
read said `rm -rf /tmp/x`". Both flow through the same permission ladder
and get the same trust treatment. In the first case shape an attacker
controlling the document drives side effects through the model — the
classic indirect-injection channel, an OWASP agentic risk.

The platform's content fences today are **argument-shape** fences
(credential regexes, shell-keyguard patterns). They do not know *where an
argument came from*.

*(2026-11-05, [ADR-104](104-one-mode-one-permission-decision.md) amendment 2, maintainer: in autopilot a tainted call runs and is noted in the transcript; in chat and plan the fence still asks per call.)*

## Decision

**Any argument carrying a registered untrusted excerpt is tainted, and
tainted arguments never auto-approve — they require explicit, per-call,
human-visible approval in every permission mode.**

1. **Provenance registration.** The companion session registers excerpts of
   results from provenance-bearing tools, declared in one contract map
   (`TOOL_TAINT_CLASSES` in `r4-contracts`): knowledge query/graph reads,
   lab catalog/file reads, MCP tool invocations/listings, web search. Tools
   not on the map never taint.

2. **Matching (minimal round).** Arguments are scanned for normalized
   substring presence of registered excerpts (first 120 chars, ≥24).
   Numbers, booleans, and structured references are not tainted (only
   strings match).

3. **Enforcement placement.** The fence runs inside
   `PermissionManager.check()`, immediately after the existing
   `NEVER_AUTO_APPROVE` handling and before trust/session/remembered
   short-circuits — it cannot be overruled by TRUST mode, session
   approvals, or remember-pair approvals, matching the credential fences'
   strength profile.

4. **Approval, not denial.** Tainted arguments follow the same approval
   surface as other risky calls (`_require_explicit_approval`) so legit
   RAG-grounded work stays possible with one human look — this is a
   visibility and consent change, not a general prohibition.

5. **Unwired behavior.** Without a registry, the gate is open-by-default
   for this feature (taint is a *feature* of a session, not a safety
   class like credentials) — the fail-closed classes (`NEVER_AUTO`,
   credential fences) are unaffected.

## Consequences

- Registered untrusted excerpts are bounded per turn (≤32) and per
  session (≤256, TTL 2h) — bounded memory and bounded undesirable reuse
  of stale excerpts.
- False positives are possible for common short paragraphs of real RAG
  text; essays were avoided by design (min 24 chars + normalization).
- The injection corpus and end-to-end harness sweep are the declared
  follow-on (WP-19 tail), explicitly out of this round per maintainer
  split (ADR + minimal fence first).
- ACP/web surfaces benefit automatically: they already drive the same
  `PermissionManager.check` via the agent loop.

## Addendum (2026-09-06, WP-19 landing — wiring + knobs + coverage)

- **No self-disable path (fourth review, ratified P8).** Every
  `ResearchCompanion` session ALWAYS constructs a `TaintRegistry`
  (`_new_taint_registry` removed; the exception-swallowing factory was
  deleted), `PermissionManager` starts with an attached (empty) registry,
  and `set_taint_registry(None)` now explicitly *resets* rather than
  disables. The lazy `_permission_gate` wire always shares one registry
  between agent and manager.
- **Short-argument floor, decided.** `MIN_TAINT_EXCERPT_LEN` (24) stays
  the default (false-positive budget for short shared phrases); the floor
  is now overridable via `R4_TAINT_MIN_LEN` for callers that know their
  corpus accepts more FP for more coverage.
- **UMRS-hosted loops remain out of scope.** Server-side loop drivers
  (`EngineLoopRunner`/`AgentLoopRunner` inside UMRS) do NOT pass through
  r4's `PermissionManager`; taint enforcement for those turns is unwired
  until the ADR-036 hosting round picks it up. This is a declared, named
  gap — not a silent partial.
  *Follow-through (2026-09-07, WP-05 round 1, ADR-036 Amendment 2 §8):* the
  hosting round found no taint path to fence — the only gated action of a
  UMRS engine loop (`workflow_trigger`) takes its arguments from the plan's
  experiment parameters, never from retrieved content or tool output, and
  the agentic runner has no tool-grain actions. Residual rule, named: a
  future loop action fed by retrieval/tool output must pass a taint check
  before its approval gate (WP-19 follow-on).
- **Corpus status.** The agent-loop wiring proof is test-covered at the
  session level (Wiring suite); the full live-stack injection corpus
  remains the later, separate task alongside the producer-map
  completeness sweep.
