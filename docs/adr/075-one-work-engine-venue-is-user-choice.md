# ADR-075: One work engine; venue (local|server) is the user's choice

**Status:** Accepted
**Date:** 2026-09-30
**Relates to:** ADR-036 (research spine), ADR-064/032-row (one server loop driver), ADR-066 (ask policy), ADR-072 (one team seam), plan plan-unified-work-engine-2026-09-30, rows U-01…U-10

## Context

Three user-visible mechanisms implement one concept at three granularities
(verified 2026-09-30, rules comparison shipped in `4d52b88c`'s commit context):

- `/teamup` — local multi-agent sessions: bounded, manual iterations, filestore.
- `/loops` — server-hosted autonomous research loops: engine ticks, PG store.
- workflow executions — the durable compute substrate both of the above depend on.

Users and agents keep asking which system their work lives in; the verb
families rhyme (pause/resume/stop) but the stores, drivers, and grammars are
three. The maintainer's simplification directive: **one engine — whether the
agent works on the local workstation or the server is only the user's
choice**, captured as the canonical 7-step flow:

1. open r4 → 2. start session → 3. pick remote/local → 4. `workflow on` if
wanted → 5. `teamup on` if wanted → 6. send prompt, kick off → 7. keep
iterating.

## Decision

D-1. **The chat session IS the work session.** There is no new user-facing
command family. A session carries a venue (`local|frontend|cluster:<name>`,
existing grammar), optional arms (`teamup on`, `workflow on`), and one
driver setting (`manual|autonomous`). `/teamup` and `/loops` become presets:
local-manual and server-autonomous.

D-2. **One driver of record, two venue hosts** (amends the D-32 wording "one
server driver of record"). The autonomous driver is ONE class; it runs
UMRS-hosted (today's behavior) or embedded in the console process (local
venue: ticks while the console runs; `parked(host_exit)` on exit, resumed
honestly on start). Extending that driver is the only legal way to add
autonomy — no fifth mechanism.

D-3. **The role-turn runtime is the per-iteration executor** in every
venue/driver combination (crews and pipeline specs both lower to ordered
role turns). Workflow executions are *step rows of the session*, not a
separate browsing universe; workflow authoring stays untouched.

D-4. **Observe hooks attach memory and output layers to the spec**
(U-10): knowledge-graph extraction per iteration (proposed-only, humans
establish — D-35 unchanged), and Scientific Catalog candidacy at each
conclusion (review promotes — D-39's construction path).

D-5. **Migration is one-time** into the unified store with receipts and a
compat view; old stores retire behind it, never silently dropped.

## Consequences

- Positive: one mental model; venue stops being a product line; agents stop
  hallucinating which store to read; the D-29 tail (loop spawns team) has a
  single home to land in.
- Cost: an embedded tick *host* to maintain (driver class unchanged); a
  live-data migration wave with receipts; ADR-032-row wording updated.
- NOT in scope: tool approval ladders (per surface by design), the SPI
  native engine, the workflow authoring DSL, any new store engine.

### Wave-0 landing (2026-09-30)

This wave ships the G1/G2/G3 surface gaps named by the 7-step flow:
G1 venue pick at start (already present — picker + status chip, pinned),
G2 the `workflow on|off` arm (armed sessions refuse the non-UMRS submit lane
by name), G3 the session/driver field (manual honored; `autonomous` is
refused by name until the embedded driver lands in U-03 — `/loops` today),
in each case with honest named limits rather than faked behavior.
