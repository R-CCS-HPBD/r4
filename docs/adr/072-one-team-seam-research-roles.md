# ADR-072: One team seam — the Team Engine SPI; the research roles run through it

**Status:** Accepted (2026-09-24, agent decision under the maintainer's delegation of the same date; closes gap row D-29's escalated consolidation)
**Date:** 2026-09-24
**Relates to:** ADR-020 (Team Engine SPI — accepted by this ADR, narrowed as below), ADR-066 (ask policy), D-29 (multi-agent team-up: planner, experimenter, analyst, reviewer)

## Context

Gap row D-29 read "four overlapping mechanisms; maintainer: pick one
engine, map the four roles, delete the rest". Reading the code shows three
layers of one stack plus one dead end, not four competitors:

| Piece | What it actually is |
|---|---|
| `r4-contracts/team_engine` + `packages/r4-teamengine-spi` | ADR-020's seam: `TeamSpec` IR, engine SPI, brokers |
| `r4/src/r4/teams/` | registry + resolver + the **native** adapter, which compiles a `TeamSpec` into the `teamup` runtime |
| `r4/src/r4/teamup/` | the runtime the native engine drives (PM/SE/PG/CD roles, file-based comms, crew packs) |
| `engines/r4-engine-langgraph` | an out-of-tree adapter — the SPI's purpose, not a duplicate |
| `r4/src/r4/research_loop/team.py` | the design's research roles (RL/ED/DA/PR) — role catalogue only, **no consumer** ("no agent spawning yet") |
| companion sub-agents | in-turn delegation inside one chat turn — a different job, not a team engine |

The real defect: the design's four roles could not run through the seam.
The native compile dropped every agent's `instructions`, so a `TeamSpec`
ran with teamup's built-in *HPC code-optimisation* prompts. Nothing built a
research `TeamSpec`, and nothing exposed one.

## Decision

1. **The Team Engine SPI is the one team seam** (ADR-020 → Accepted). The
   native engine over the teamup runtime is the default. Other engines
   (LangGraph today) are external plugins, resolved by the registry.
   Nothing new orchestrates a team outside the SPI.
2. **The research roles run through it.** `research_team_spec()` compiles
   planner (RL → pm), experimenter (ED → pg), analyst (DA → se) and
   reviewer (PR → cd) into a `TeamSpec`. Each agent carries its research
   prompt and an optional model; for example an independent reviewer
   model (suggestion 10) is `models={"reviewer": …}`. The native engine now
   lowers each agent's `instructions` into per-role system prompts
   (`TeamupSessionConfig.role_prompts`), exactly as it already lowered
   `model_ref`.
3. **Entry points:** `r4 team launch NAME --team research --goal …` (SPI
   path), and `r4 team create NAME --crew research` (crew pack, same
   prompts). `research_loop/team.py` is the single source of the role
   prompts.
4. **Nothing is deleted.** Every layer has a job, and deleting the
   langgraph adapter would undo ADR-020's point. The consolidation
   removes the *bypass risk*, not code.

## Rejected options

- **Delete teamup and keep only the SPI:** the native engine *is* teamup;
  there would be nothing left to run.
- **Keep research roles as a separate engine:** that is a fifth path; a
  role set is data (`TeamSpec`), not an engine.

## Consequences

- D-29 is Implemented: planner, experimenter, analyst and reviewer can be
  composed for one task through one seam, each with its own prompt and
  optionally its own model.
- A found defect is fixed too: `TeamupSessionConfig.from_dict` dropped
  `role_models`, so per-role models were lost when a session reloaded
  from its snapshot. It now restores `role_models` and `role_prompts`.
- The research loop's engine (D-32) does not spawn this team per
  iteration. Wiring the team *inside* a loop iteration (for example
  the reviewer gating a finding) is a later leg, bounded by ADR-066's ask
  policy.

## Verification pointers

- `r4/src/r4/research_loop/team.py` — `research_team_spec`, `research_role_prompts`
- `r4/src/r4/teams/native.py` — `_role_prompts` (instructions → role prompts)
- `r4/src/r4/teamup/session.py` — `role_prompts` (chat `system=`), snapshot round trip
- `r4/src/r4/teamup/crews.py` — `research` pack; `r4/src/r4/_cli/teamup.py` — `launch --team research`
- `r4/tests/test_research_team_seam.py`
