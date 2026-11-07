# ADR-076: A decision-model role beside the chat model; always-on endpoints relay across walltime

**Status:** Accepted (2026-10-01, agent decision under maintainer delegation)
**Date:** 2026-10-01
**Relates to:** ADR-068 (one internal inference API — the decision model is a catalog name like any other), ADR-062 (model locality — the decision path rides the same gate), ADR-066 (gates — a decision is evidence for a gate, never the gate), ADR-072 (per-role models), gap rows D-10, D-29, **D-57** (new)

## Context

The maintainer asked (2026-10-01) to run a *Jev-like* model —
[`bespokelabs/Bespoke-Nimble-9B`](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B)
— permanently on RCCS `ng-dgx-s` and to use it from R4 as a decision model,
after a survey of the model class (survey + sources:
docs/development/decision-model-nimble-2026-10-01.md).

A *decision model* (TypeSafe's Jev, OpenClaw's `decisionModel` role since
2026.9.6) answers bounded questions about a state — **choice**, **score**,
**noul** (P(true)) — with full distributions, from candidate-token logits
instead of generated text. Agents use it for gates, routing, triage and
filtering before (or instead of) a full model turn. Nimble is an open
reproduction: Qwen3.5-9B + a LoRA (Apache-2.0) trained to answer a schema
field with one letter; its scorer softmaxes the candidate letters' logits.

Verified facts that shape the decision (2026-10-01):

- R4 had no decision mechanism: `SecondaryLLM` (`r4/src/r4/providers/secondary.py`)
  is built but never called; no judge/router/classifier model exists.
- vLLM exposes `logprob_token_ids` on the OpenAI `/v1/completions` and chat
  routes since **v0.26.0** (vllm#43463, 2026-07-13): logprobs for an explicit
  token set — exactly Nimble's scoring primitive. `vllm-openai:v0.30.0` has an
  arm64 manifest. LiteLLM (the per-job proxy, `drop_params: true`) forwards
  `logprob_token_ids` / `add_special_tokens` unchanged (checked against a mock
  backend with the shipped `litellm_fs/config.yaml`); UMRS `/umrs/llm` only
  rewrites `model`.
- Nimble's training prompt is ChatML with an empty `<think>` block; rendered
  text re-tokenizes to the exact training ids, and the candidates A..Z are
  token ids 32..57 (release `serving_config.json`). So the client needs no
  tokenizer.
- Every RCCS endpoint is a Slurm job (`serve-model.slurm`); `ng-dgx-*`
  partitions cap jobs at `1-00:00:00` and rccs-front submits with
  `time_limit: 23:00:00`. Nothing re-provisions an endpoint after walltime;
  the unwired `PoolAutoscaler` only emits decisions.
- `ng-dgx-s` (8 GB10 nodes) was valid only in the compute-side site config;
  the production server's `rccs-front/site.yaml` did not list it, so
  `_validate_partition` refused it.

## Decision

**D-1 — A decision-model role.** R4 gains `config.decision_model` (catalog
name; env `R4_DECISION_MODEL`, profile/project key `decision_model`; empty =
off) and `r4.decisions`: Jev-shaped questions (choice / score / noul, ≤64 per
call, 2–26 options), typed answers with probabilities and confidence
(1 − normalized entropy), and the agent tool `decision_evaluate` (skill
`decisions`), registered only when a decision model is configured.

**D-2 — Scored client-side over the one inference API.** R4 renders the
model's training prompt as text (`r4.decisions.nimble`, pinned to the release's
prompt-code sha `a0a0f94d…`, byte-for-byte golden test against the upstream
compiler) and sends one `/gw/llm/v1/completions` request per question with
`max_tokens=1`, `logprob_token_ids=[A..]`, `add_special_tokens=false`; it
renormalizes the candidate logprobs (`softmax(logit/T)`, release T = 1.0).
The request goes through `Client.text_completion`, which resolves the same
gated route as chat (ADR-062). No new transport, port, or server-side scoring
service; the model is served by the standard vLLM lane under its catalog name
(ADR-068).

*(2026-11-05, [ADR-104](104-one-mode-one-permission-decision.md) amendment 2: in autopilot the gate's reason is recorded as a transcript note and the call runs; elsewhere it still adds a question.)*

**D-3 — A decision is evidence, never permission.** `decision_evaluate`
performs no action; its results never satisfy a permission rung or an
ADR-066 gate by themselves. Callers set thresholds from their own labelled
cases; ~0.5 is uncertainty, not a coin flip to act on.

**D-4 — Always-on endpoints relay across walltime (UMRS).** A roster entry
may carry `keep_alive: {project_id, user?, renew_before_sec, handover_grace_sec,
walltime?}`. The server runs a relay beside the janitor
(`umrs.orchestrator.endpoint_relay` plans; `umrs.server.endpoint_relay`
executes through the same provision/deprovision handlers `r4 endpoint up/down`
reach): respawn when nothing is live or pending; provision a successor when
every live endpoint is within `renew_before_sec` of `created_at + walltime`
(submit time, so never late); retire the old endpoint once the successor has
been READY for `handover_grace_sec` (busy ones drain until the last 5 min).
Successors are system-tier, `ttl_sec: 0`, `prefer_warm: false`.
`UMRS_ENDPOINT_RELAY=false` disables the loop.

**D-5 — First tenant.** `pool-rccs.yaml` entry `bespoke-nimble-9b`
(`Bespoke-Nimble-9B`, `partition: ng-dgx-s`, vLLM `v0.30.0`, util 0.30, 8k
context, language-model-only, keep_alive under project `default`).
`rccs-front/site.yaml` lists `ng-dgx-s` as a partition profile only — not in
`scheduler.partition`, so generic provisions stay on `ng-dgx-m*`.

### Why

- One inference API (ADR-068) and one gate (ADR-062): the decision model
  reaches the same `/gw/llm` path, catalog, registry and audit as every
  other model, so locality, quota and ops stay one system. A bespoke
  `/v1/systemone` server (upstream's SGLang + openjev stack) would be a
  second serving lane on GB10 (unproven SGLang/sm_121, new gateway route).
- Client-side scoring needs no tokenizer and no server code, and the golden
  test makes drift from the trained prompt a red test, not a silent accuracy
  loss.
- The relay belongs to UMRS: it owns scheduling and endpoint lifecycle
  (design ownership rule); a client-side "re-up when it fails" loop would be
  a second scheduler.

## Consequences

- Positive: agents get millisecond-class bounded judgments with real
  probabilities; any OpenAI-compatible logprob-capable model can serve the
  role later (the role is a catalog name, the prompt contract is per model).
- Positive: any roster endpoint can be made always-on with three lines.
- Cost: one GB10 node per 23 h, plus a second for the handover window
  (~2 h/day). The relay submits under `keep_alive.user` (per-user Slurm
  delegation).
- Cost: endpoints are per project in the registry (`_find_candidates`), so the
  always-on endpoint serves callers whose `X-Project-ID` is the policy's
  project (`default`, the rccs-cloud profile). Cross-project sharing of
  system-tier endpoints is a separate routing decision (D-57 tail).
- NOT in scope: wiring decisions into the permission ladder, the loop driver
  or teamup as automatic gates; calibration data; a server-side
  `/v1/systemone` facade; the dead `SecondaryLLM`/`SmartRouter` scaffolds
  (left as-is).

## Alternatives Considered

- **Upstream serving stack (SGLang 0.5.19 + openjev `/v1/systemone`)** —
  exact upstream parity, but a second engine lane and a non-OpenAI route
  through the gateway; vLLM `logprob_token_ids` gives the same numbers on
  the proven lane.
- **vLLM LoRA serving of the adapter on the base** — the adapter targets the
  gated-delta-net projections (`in_proj_qkv/z/a/b`, `out_proj`); not a
  supported LoRA surface. Merge to BF16 instead (offline, once).
- **Natural top-k logprobs** — candidates outside top-20 would silently
  score zero; explicit token ids are exact.
- **Slurm self-requeue inside `serve-model.slurm`** — the job id changes
  behind the registry's back and the endpoint disappears for a whole cold
  start every day.

## Rollout-status (2026-10-01)

- D-1/D-2 landed in the working tree: `r4/src/r4/decisions/`,
  `r4/src/r4/companion/decision_tools.py`, skill `builtin/decisions`,
  `Client.text_completion`, `Config.decision_model`; tests
  `r4/tests/test_decisions_evaluator.py` + `test_decisions_nimble_golden.py`
  (28 + golden fixture from upstream `NimbleCompiler`); full r4 suite 3777
  passed.
- D-4/D-5 landed in the working tree: `umrs/tests/test_endpoint_relay.py` (26).
- Live legs open (owner/host): stage + merge weights on Lustre, `r4 model
  register`, converge apply, first endpoint up, live decision receipt, first
  relay handover receipt (~21 h after the first READY). Runbook in the
  development doc.

## Rollout-status (2026-10-01, later)

- Correction: the caller's project is the gateway-verified JWT claim
  (OIDC tokens without one fall back to `JWT_DEFAULT_PROJECT=default`), not
  the client's `X-Project-ID`. Users work in their own projects, so the
  per-project limit above was real: **closed by [ADR-078](078-endpoint-access-scopes.md)** —
  `bespoke-nimble-9b` carries `access: [system]`, every authenticated caller
  routes to it from any project.

## Rollout-status (2026-10-02): the harness asks the decision model too

Until now only the agent could consult the model, through `decision_evaluate`.
From 2026-10-02 the harness itself asks it four fixed questions at fixed
points (`r4/src/r4/decisions/harness.py`), while `/decision-model` is on.
Each answer can only add caution or a sharper question; D-3 holds.

| Purpose (rubric) | Where | What a confident answer changes |
| --- | --- | --- |
| `plan_reply/1` (choice) | a reply to a pending plan that the rules read as revise/unclear (`companion/session._answer_pending_plan`) | at ≥ 0.8 for run or cancel, a one-word confirmation question instead of a re-plan or a generic one. It never runs or cancels by itself |
| `tool_gate/1` (noul) | a RISKY call the policy would auto-approve: trust allowlist, session approval, remembered call (`PermissionManager._auto_approve`) | at P(harm) ≥ 0.5, an explicit approval with the reason. It never removes a question; ask-always mode is untouched |
| `lesson_check/1` (noul) | a reply the steering rules read as a correction (`harness/steering.observe_turn`) | at P(correction) < 0.5, no lesson is saved. It never creates one |
| `loop_decision/1` (choice) | `ResearchOrchestrator._replan_decision` | at ≥ 0.8, done / continue / failed is decided; otherwise the chat model is asked, as before |

Every call, including failures, is appended to the session's
`metadata["decision_log"]` (last 50; the orchestrator keeps its own
`decision_log`) with purpose, rubric, model, probabilities and latency. A
failure, or the model being off, leaves the earlier behaviour unchanged.
Harness calls time out after 5 s. The thresholds are first guesses; refit
them on the logged calls.

The 2026-10-02 audit also found, and this wave fixed:

- **Plan mode could run a plan the user had refused.** The reply was matched
  on "yes / run it / do it / continue" before "no / cancel", so "no, don't
  do it" ran the plan, "yes, but use 4 GPUs" ran it without the change, and
  "continue planning" ran it. `companion/plan_reply.classify_plan_reply` now
  reads four ways:
  - only a short reply made of confirmation words executes;
  - a short refusal cancels;
  - hesitation or mixed signals ask again;
  - anything longer revises.
  This needs no decision model.
- Steering saved "No, that's great" and "no problem" as lessons; the
  agreement forms are excluded now.
- `decision_evaluate` asked for approval on every call; it is now a
  SAFE_TOOL (one scoring call, no effect).
- A new session auto-selected a decision model as its chat model when that
  was the only ready endpoint, as at rccs today.
  `model_resolution._chat_candidates` skips the known decision models and
  the configured one; an explicit or saved choice still wins.
- `_replan_decision` read "not done yet" as done; the parser now respects
  negation.

Not done here: the server-side research-loop driver (UMRS) cannot import
`r4.decisions`. Its DECIDING phase would need UMRS to call the decision
endpoint itself, which is a follow-up for this ADR's owner legs. Tool-family
filtering stays out because of ADR-037 Rule 1.

## Rollout-status (2026-10-03): on by default for every user

Maintainer ask (2026-10-03): run the decision model for all UMRS users.
Decided by the agent under the 2026-09-24 delegation, on the measurement
in decision-model-benchmark-2026-10-03.md.
On 152 labelled cases, against the same agent without a decision model:

- plan replies: right next step 61% → 87%, wasted re-plan turns 14 → 4;
- auto-approved harmful calls stopped: 0 → 15 of 20, with 0 of 20 safe calls interrupted;
- false lessons: 18 → 3 of 18;
- loop: equal accuracy with 2 chat calls instead of 30.

Each decision took about 0.23–0.34 s and one output token. A chat model
judging the same points was more accurate but 3–9× slower and spent 70–390
output tokens per decision. It also could not serve sensitive projects if
it ran on a public provider (ADR-062).

- `Config.decision_model` defaults to `Bespoke-Nimble-9B`
  (`r4/src/r4/config.py`, mirrored by `r4.decisions.settings.DEFAULT_DECISION_MODEL`).
  An explicit `off` (profile, `R4_DECISION_MODEL`, `.r4/*.toml`) still wins.
- It is used only where the platform serves it: the gates and the
  `decision_evaluate` tool first ask `decision_model_available`, which reads
  the gateway's `/v1/models` and caches the answer per client for 300 s. "Could not
  tell" counts as available. So a site without the model makes no
  failing call at each gate.
- Reach: with the shared endpoint registry (ADR-060 U-1), fronts other than
  rccs route to the one rccs endpoint (bdrt receipt 2026-10-03: a plan-reply
  decision in 374 ms). The endpoint is `access: [system]` (ADR-078).
  - *Correction 2026-10-03 (U-1 follow-up review):* the code contradicts
    this attribution. Every `PostgresBackend` read is venue-filtered, so
    bdrt-front's registry cannot see the rccs-front Nimble row. U-1 shares
    storage, not visibility. The 374 ms receipt reached Nimble some other
    way, not yet verified; re-check it before citing cross-front reach
    (convergence plan, section "U-1 follow-ups").
- Thresholds stay at their first guesses. The benchmark suggests
  `ESCALATE_AT` 0.25 and no "execute" notice after a hold word. Both
  were fitted on the benchmark's own cases, so they wait for the refit
  on logged calls.
- Capacity: one GB10 endpoint (`max_num_seqs` 32) now serves every
  session's gates. Watch its queue in the decision-log latencies; a second
  relay pool entry is the scale-out.

## Rollout-status (2026-10-08): venues and reservations

- **Partition.** The Nimble pool entry is back on `ng-dgx-s` (umrs `e9127d09`). The temporary 10-01 `ng-dgx-m1` pin had made relay successors queue behind another user's job: a gap of about 6 min at 04:17 JST 10-08, then job 395814 on m1 again.
- **Second venue: withdrawn the same day.** The maintainer chose rccs-cloud only. After the converge the bdrt relay submitted a failing job every cycle (jobs 7843, 7844, …; no model on bdrt), and the entry was removed (umrs `fa0e08d3`, guard test `test_nimble_is_kept_alive_on_rccs_only`). The zero-walltime fix stays. Original note: a bdrt replica entry (`pool-bdrt-step.yaml`, respawn only, `access: [system]`). Registry reads are venue-filtered, so it serves bdrt callers. `parse_slurm_duration("0")` is now `None`: bdrt's `time_limit: "0"` had parsed to a 0 s walltime, which would have relayed every cycle.
- **Reservations** (umrs `79095052`). The remote Slurm backend submits `--time-min` (`scheduler.time_min`; rccs-front `03:00:00`), so Slurm can start a successor with a limit cut to end before a reservation instead of leaving it pending. The relay records each READY endpoint's Slurm-granted end once (`squeue -t RUNNING -o %L` → `metadata.slurm_deadline`) and relays by it; it falls back to `created_at + walltime` when that is unknown. Expected effect for a 7-day user reservation of all 16 GB10 nodes (10-09 13:00 → 10-16): Nimble serves until 13:00 instead of until about 03:17.
- **Open.** Fallback partitions in `keep_alive` (fleet plan R3); client fallback across sources (R4b). During a reservation rccs has no decision model; R4 takes the no-decision path (verified, fleet plan R5).

## Related

- [ADR-068](068-one-internal-inference-api.md), [ADR-062](062-model-locality-data-class.md),
  [ADR-066](066-ask-scientist-policy-pause-profile.md), [ADR-072](072-one-team-seam-research-roles.md)
- docs/development/decision-model-nimble-2026-10-01.md
