# ADR-066 Amendment 3: Reserved emitters + maturity-stage presets (D-33)

**Status:** Accepted (2026-09-24, maintainer proceed authority)
**Amends:** [ADR-066](066-ask-scientist-policy-pause-profile.md) (with
[amendment 1](066-amendment-2026-09-24-deferred-continuation.md) and
[amendment 2](066-amendment-2026-09-24b-parallel-hypothesis-tracks.md)); gap row D-33

## Context

ADR-066 named four trigger classes but emitted two (`cost`,
`competing_hypotheses`); `physical_action` and `direction_change` were
reserved. The design also asks that pause frequency be a per-project setting
that falls as the catalog matures (Bootstrapped → Learning → Competent →
Autonomous), and the tree only had a free-form persisted profile.

## Decision

1. **An action carries a SET of trigger classes**; the profile gate pauses
   when any of them is in the pause profile. `workflow_trigger` →
   `{cost}`, plus `physical_action` when the experiment declares
   `metadata.physical_action: true` (it drives an instrument/robot). A
   physical action is still a cost: a profile naming either pauses it.
2. **`direction_change` is emitted** by the single-branch loop when the
   hypothesis it selects differs from the one its previous completed
   iteration pursued (the loop is about to change research direction).
   A decided "no" keeps the loop on its previous branch for that iteration
   — the scientist vetoed the switch, not the loop; deferral follows
   amendment 1. Parallel tracks (amendment 2) never switch, so they never
   emit it. The trusted-host MCP rail (`mcp_trusted_call` → `physical_action`)
   stays reserved until that rail exists (D-21/D-49).
3. **Maturity stages are named presets of the persisted policy**, set by the
   project owner with `set_ask_policy` + `maturity_stage`:

   | stage | mode | pause triggers |
   |---|---|---|
   | `bootstrapped` | `ask` | every gated step |
   | `learning` | `profile` | cost, competing_hypotheses, direction_change, physical_action |
   | `competent` | `profile` | competing_hypotheses, direction_change, physical_action |
   | `autonomous` | `profile` | physical_action |

   The stored mode + triggers remain the single truth (no schema change);
   the stage shown on the project is recovered by matching the stored
   policy against the presets (`custom` otherwise). Giving a stage together
   with an explicit mode/triggers is refused as ambiguous. Stages are
   chosen by the owner, never derived automatically from catalog metrics —
   an automatic suggestion would need validated thresholds (later leg).

## Consequences

- All four ADR-066 classes have live emitters except the trusted-host MCP
  lane (no rail yet).
- `physical_action` is safety-first: every preset above `bootstrapped` keeps
  it, so hardware actions keep asking even for an autonomous project.
- Loops inherit the project policy exactly as before (D-33 persisted
  default), so a stage change applies to the next loop start.

## Verification pointers

- `umrs/src/umrs/knowledge/ask_policy.py` — classes per action, stage presets
- `umrs/src/umrs/knowledge/engine_loop_runner.py` — set-based profile gate
- `umrs/src/umrs/knowledge/loop.py` — `direction_change` emitter, physical flag
- `umrs/src/umrs/api/projects.py` — `maturity_stage` on `set_ask_policy`
- `umrs/tests/test_ask_policy_emitters.py`
