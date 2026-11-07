# ADR-064: One server loop driver — the engine runner; agent runner deprecated in place

**Status:** Accepted (consolidation direction; fold lands in waves)
**Date:** 2026-09-22 (ROADMAP 2.1, under maintainer proceed authority)
**Relates to:** ADR-036 (loop hosting), ADR-009 (closes), gap row D-32, [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

The server-hosted research loop (`research_loop_job`, ADR-036) runs with two
drivers, selected at start by `agentic` (`api/loop.py`, spec `driver: agent |
engine`):

- **EngineLoopRunner** — drives `AutonomousResearchLoop`
  (`umrs/knowledge/loop.py`) with the durable approval-gate hook, cancel
  checks between iterations, findings ingest; the **cluster venue exists for
  this driver only** (`research_loop_job` on `cluster:<site>`; the agentic
  path's cluster wiring is parked).
- **AgentLoopRunner** — agentic kicks with an LLM executor, thread-hosted on
  the frontend only (venue validation refuses `cluster:` for `agent`).

Gap row D-32 names the fragmentation (one server engine + client-side
mechanisms) and ROADMAP 2.1 instructs: decide one server driver.

## Decision

**The engine runner is the one server driver of record.**

1. `AgentLoopRunner` is **deprecated in place**: no new features, no cluster
   support, existing `agentic: true` loops keep running to completion; the
   start-route keeps accepting `agentic: true` (pinned API) but marks the
   loop's metadata `driver_note: agent-deprecated`.
2. The agentic executor's distinct value (LLM-driven iterations) folds into
   the engine driver as an iteration executor where the engine already owns
   gating/cancel/venue: the follow-up wire point is `EngineLoopRunner`'s
   engine factory, not a second driver class.
3. Client-side consolidation stands as already done (D-32): the client
   `ResearchRuntime` SPI (`r4/src/r4/runtime/`) is the client seam;
   `companion/science_loop.py` was folded into `r4/runtime/science_tracker.py`
   (TUI read-model, not a driver) 2026-09-22; the test-only
   `research_loop/orchestrator.py` and `agent/research_director.py` were
   deleted (V-10). **ADR-009 closes** (superseded by ADR-036 + this split).
4. Retirement of `AgentLoopRunner` happens when the engine driver hosts the
   agentic executor and one release of deprecation has passed; the class is
   deleted in that wave, and the `agentic: true` start field is mapped onto
   the engine-driven agentic mode.

## Consequences

- One driver to secure, gate and venue-probe; the parked agentic-cluster
  wiring is never built — agentic capability arrives via the engine driver.
- Venue gate text stays true ("engine driver only today") until the fold;
  the refuse-by-name behavior for `agentic + cluster:` is unchanged.
- Gap row D-32 advances: loop=engine driver + ResearchRuntime SPI + TUI
  read-model; remaining D-32 legs are D-45 output (own row) and D-29 team
  engine SPI, not this ADR.

## Rollout (2026-09-22, fold leg)

- **AgenticEngine adapter** (`umrs/knowledge/agentic_engine.py`): the agent
  executor rides the engine driver's surface (`run(loop)` +
  `_iteration_hook` / `_cancel_check` / `_approval_gate` — cancel + ask-mode
  gate parity with the engine path). Both host paths register the engine
  runner now: thread hosting (`api/loop.py`) and durable-job hosting
  (`knowledge/loop_hosting._register_driver`); the host name on the loop
  record stays for API parity.
- Start-route marker from leg 1 stands (`driver_note: agent-deprecated` on
  agentic starts); `AgentLoopRunner`/its registry stay for in-flight loops
  and synchronous `kick()` tests — no NEW registrations happen anywhere.
- Pins: 3 adapter tests + the start-wiring test rewired onto the engine
  registration (agent runner no longer asserted).
