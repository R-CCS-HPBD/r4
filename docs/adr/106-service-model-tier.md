# ADR-106: Service-model tier: role aliases, a replica controller and rolling restarts

**Status:** Accepted (2026-11-01, maintainer: "lets use DeepSeek-V4.1-Flash-1017. others are ok as it is. proceed with your recommendations"; sub-decisions drafted by the agent in the service-model fleet plan, accepted with M-1…M-7; `wave/service-model-fleet-plan`, wave ledger (142)–(143))
**Date:** 2026-11-01
**Relates to:**
- [ADR-060](060-topology-one-umrs-hub.md): one hub. D-6 here is a step toward it, not a second hub.
- [ADR-062](062-model-locality-data-class.md): service endpoints are `local`.
- [ADR-068](068-one-internal-inference-api.md): aliases ride the one inference API.
- [ADR-076](076-decision-model-role.md): D-4 there, the walltime relay, is generalized by D-3 here.
- [ADR-078](078-endpoint-access-scopes.md): `access: [system]`.
- [ADR-083](083-embedding-spaces.md): an embedding change is a space change, not a roll.
- [ADR-089](089-ops-watch-issue-reporter.md): the role probes run hourly from Ops Watch.
- Gap rows **D-67** (new), D-10, D-11, D-14, D-47, D-57, D-58.

## Context

Verified 2026-11-01 at parent `9379ed826` and umrs pin `9b5b8ea2`. File and line references are in plan §2.

UMRS serves models one vLLM per Slurm job (nginx + LiteLLM + vLLM,
`umrs/src/umrs/mini-rivault-llm/serve-model.{slurm,yaml}`). The only lifecycle
that keeps a model alive is the ADR-076 walltime relay
(`umrs/src/umrs/orchestrator/endpoint_relay.py`). For **one** model it submits
**one** successor `renew_before_sec` (7200) before the Slurm deadline, waits
for it to be READY for `handover_grace_sec` (300), and then retires the old job.
Several gaps follow from that:
- Nothing keeps N replicas.
- No status stops new requests from reaching a job that is about to stop. `EndpointStatus` has PENDING…TERMINATED and no DRAINING.
- `/umrs/llm` (`umrs/src/umrs/api/llm.py:222-270`) picks one endpoint by least connections and never retries another one.
- A recipe change (new model, image or flags) has no rolling path.
- `PoolAutoscaler` is exported without a caller.

R4's built-in default chat model `rivault-gpt-oss-static` is served by nothing.
Clients name concrete model ids, so every model change is a client change.

Each front on umrs-front (`umrs-server-{rccs,ai4s,bdrt,octopus}`) runs with
`network_mode: host`, but keeps its **own registry database**
(`UMRS_REGISTRY_POSTGRES_DSN` → `front_rccs`, `front_bdrt`, …,
`umrs/deploy/umrs-front/docker-compose.yaml`). An endpoint is therefore visible
only to the front that provisioned it. The bdrt front's models cannot serve
rccs callers.

The maintainer asked for default service models (a 2×DGX-Spark LLM, vision,
decision, OCR, embedding, …) on rccs GB10 and bdrt that are restarted in a
rolling way so that the service never drops. The maintainer chose
**DeepSeek-V4.1-Flash-1017** for the main LLM. Its only 2×Spark route is the
MiaAI recipe: the TensorFold engine with an EXL3 2.9-bpw quant. It needs at
least 112 GiB free per Spark, so it fits bdrt and not rccs's 0.78 cap.

## Decision

**D-1. Role aliases.**
- UMRS serves a fixed set of role aliases: `svc-chat`, `svc-fast`, `svc-decision`, `svc-vision`, `svc-ocr`, `svc-embed`, `svc-rerank`, and later `svc-asr`.
- An alias is a roster `aliases:` entry on a `service` entry. `/umrs/llm` resolves it like a model name.
- `GET /v1/models` lists each alias together with its backing model id. Provenance (session logs, catalog) always records the backing id.
- Clients call aliases. A model change is a roster change plus a rolling upgrade (D-4), never a client release.

**D-2. Initial bindings** (plan §3; any change is a dated block here):

| Alias | Binding |
|---|---|
| `svc-chat` | **DeepSeek-V4.1-Flash-1017** (TensorFold, bdrt, two replicas, one per pair). Until its go-live checks G1/G2/G6 pass, GLM-5.3-Flash NVFP4 (vLLM, bdrt `edge-double`) keeps the alias. |
| `svc-fast` | Qwen3.8-27B-NVFP4 |
| `svc-decision` | Bespoke-Nimble-9B |
| `svc-vision` | Qwen3.6-35B-A3B-NVFP4. In the Lean profile it is an alias of `svc-fast`. |
| `svc-ocr` | PaddleOCR-VL-1.6 |
| `svc-embed` | Qwen3-Embedding-0.6B, as a new ADR-083 space with a CPU copy. It becomes the default per project after backfill, only if it wins the plan §8 retrieval set. |
| `svc-rerank` | Qwen3-Reranker-0.6B |

**D-3. Replica controller.**
- A roster entry may carry a `service:` block with these fields:
  - `replicas`, `min_available`, `max_surge`;
  - an ordered `placements` list (venue, partition);
  - `stagger`;
  - `restart.planned` / `restart.max_age`;
  - `readiness` (probe, warm-up, timeout);
  - `drain.timeout_sec`.
- The existing relay planner is extended into the controller. It stays a pure function from (policy, rows, now) to actions, plus an executor. A `keep_alive` entry is `service.replicas: 1`.
- No second scheduler or job store (CONSTITUTION ownership, AGENTS rule 10).

**D-4. Rolls.** Every restart is a roll that keeps at least `min_available` READY replicas:
- **surge**: start the successor, gate it on readiness, drain the old replica, retire it;
- **no-surge**: drain one replica, restart it in place. Allowed only when `READY − 1 ≥ min_available`.

The kinds of roll:
- **walltime**: the relay's case;
- **planned**: a schedule or `max_age`;
- **upgrade**: the recipe digest changes. The first replica is the canary. A failed probe stops the roll and keeps the old replicas;
- **failure respawn**.

An embedding model change is **not** a roll (ADR-083 space migration), and a decision model change is an ADR-076 change.

**D-5. DRAINING and failover.**
- `EndpointStatus.DRAINING`: selection skips it for new requests, and requests already in flight finish.
- A replica counts as drained when the engine reports no running requests (vLLM `/metrics`, or the engine adapter's equivalent) or `drain.timeout_sec` has passed.
- `/umrs/llm` retries **once**, on another READY replica of the same model or alias, when the upstream fails **before the first response byte**. It never retries after bytes have streamed.

**D-6. Cross-front service forwarding.**
- A front with no READY local replica of a service alias forwards the call to the **owning front's** `/umrs/llm` over host loopback, with a front-to-front service credential.
- Each endpoint keeps one owner: its own front's registry, selection, drain accounting and tunnel.
- Fronts never read each other's registry databases.
- The peer list is site configuration. The credential is an owner-generated secret delivered by the converge.

**D-7. Readiness means useful.** An endpoint is READY only after its role probe passes, after warm-up. The probes:

| Alias | Probe |
|---|---|
| chat, fast | a forced tool call that parses |
| decision | the golden prompts |
| vision | a fixture plot |
| OCR | a fixture page |
| embed | the dimension, plus cosine against pinned vectors |
| rerank | a pinned order |

The same probes run hourly from Ops Watch and in the R7 image canary.

**D-8. Engine adapters.**
- An engine other than vLLM may serve a service entry only through an adapter in the serving wrapper. It must:
  - start under rootless podman per node from `srun`;
  - answer `/v1/models`;
  - require an API key;
  - stop gracefully on TERM;
  - expose a running-request count;
  - run behind the same nginx + LiteLLM.
- TensorFold is the first adapter (`engine: tensorfold`). Its image and weights are pinned by digest or revision and mirrored.

**D-9. Defaults.**
- `connect-info` returns `default_models`, with:
  - one entry per role: `chat`, `fast`, `decision`, `vision`, `ocr`, `embed`, `rerank`;
  - `fallback`, the ordered list filtered by data class.
- With no explicit choice, R4 uses those defaults. The explicit choices are `R4_MODEL`, the source's `default_model`, and `R4_DECISION_MODEL`.
- The dead `rivault-gpt-oss-static` default is removed.

### Why

- Aliases decouple clients from model churn. They are also the only way to swap a model without telling every user.
- Generalizing the relay reuses the one lifecycle that already works live, with its reservation awareness and `--time-min`. A separate autoscaler would be a second owner of endpoint lifetime.
- Drain plus pre-first-byte retry are the two pieces that make a roll invisible to a streaming client. Neither is safe without the other: retrying mid-stream would duplicate output.
- Forwarding keeps one owner per endpoint. Reading peer registries would split drain accounting and tunnel ownership across processes.
- Useful-readiness matters because GB10 cold start includes JIT and autotune, which can take 60+ minutes. An endpoint that answers `/health` but has not compiled its kernels would stall the first user.

## Consequences

- **Positive:**
  - zero-downtime walltime, planned and upgrade restarts;
  - one place to change a default model;
  - sensitive projects get in-site defaults (ADR-062 `local`);
  - the TensorFold engine is contained behind the same edge.
- **Cost:**
  - more GB10 hours (surge windows, two replicas of `svc-chat` hold all of bdrt);
  - a new engine adapter to maintain;
  - a front-to-front credential (owner leg).
- **Risk:**
  - TensorFold is a community engine whose authors have not tested multi-day uptime. This is mitigated by the go-live checks, `restart.max_age`, and GLM-5.3-Flash as the fallback until they pass.
- **Not in scope:**
  - Rikyu serving (later venue);
  - x86 rccs partitions;
  - a global LiteLLM router;
  - autoscaling on load (`PoolAutoscaler` stays unwired).

## Alternatives Considered

- **A global LiteLLM router with several deployments per model name.** Rejected: it duplicates UMRS selection, knows nothing about drain or Slurm deadlines, and would be a second routing owner.
- **Reading peer fronts' registry databases** (the fronts share one Postgres instance). Rejected: it splits ownership of drain accounting and SSH tunnels, and a restart of the owning front would orphan the readers' view.
- **Wiring `PoolAutoscaler`.** Rejected for now: load-based scaling is not the ask, and min/max replicas without drain or rolls would not keep service up.
- **DeepSeek-V4.1-Flash via vLLM.** Not possible on 2×GB10: the FP8 checkpoint is about 510 GB, and no NVFP4 or 4-bit 2×Spark vLLM/SGLang route exists as of 2026-11-01.
- **Bake-off before choosing `svc-chat`** (the plan's original §4.3). Superseded by the maintainer's choice. The gates remain as go-live checks.

## Rollout-status (2026-11-01)

| Sub-decision | Status | Evidence |
|---|---|---|
| D-1 … D-9 | Accepted | Repo work tracked in plan §9 (S1–S13) and §16 |
| Live legs | Owner | Plan §11 owner legs |

## Rollout-status (2026-11-01, later)

| Sub-decision | Status | Evidence |
|---|---|---|
| D-1 role aliases | Landed repo-side | umrs `dc414588`: `provision_roster.is_role_alias` / `role_alias_models`; `/umrs/llm` strategy list resolves `svc-*` across every entry that carries the alias; `tests/test_service_tier_routing.py` |
| D-5 DRAINING + failover | Landed repo-side | umrs `dc414588`: `EndpointStatus.DRAINING` (one-way), `mark_draining`, `in_flight`; one retry before the first byte on 502/503/504 or connect error; leak fix for unopened streams |
| D-8 engine adapters | Landed repo-side (TensorFold) | umrs `4715c825`: `tensorfold_runner.sh`, `tests/test_tensorfold_runner.py` |
| D-3 controller, D-4 rolls | Landed repo-side | umrs `696a4831`: `orchestrator/service_controller.plan_service` (the `service:` block; surge / no-surge rolls; walltime, upgrade (digest), `max_age`, `roll_requested` and stagger as due reasons; FAILED canary holds the upgrade); executor `drain` action; `POST /umrs/endpoints/{job_id}/roll`; `tests/test_service_controller.py`. Not implemented: the `restart.planned` cron form (`max_age` covers planned rolls) and `placements` across venues (needs D-6) |
| D-7 readiness | Partly | `service.readiness_probe` selects the provision probe (default `chat` = a real completion after warm-up; `models` for embed/rerank/OCR). Dedicated tool-call, cosine, OCR and vision probes are open |
| D-9 defaults | Landed repo-side | umrs `68b792fa` (`connect-info.default_models`); R4 login stores the map and fills an unset source `default_model` (`r4/tests/test_login_default_models.py`) |
| D-2 bindings | Partly | umrs `baa76896`: routing aliases on staged entries (rccs `svc-decision`, `svc-fast`; bdrt `svc-chat` → GLM until DeepSeek's go-live checks pass, `svc-fast`). `service:` blocks follow staging (plan §10.3) |
| D-6 forwarding | Open (plan P4); lower priority | Since the 2026-11-01 placement change every role is served on rccs, so rccs users need no forwarding |

## Amendment (2026-11-01, later; maintainer)

**D-2 placement:** the whole service tier runs on **rccs**.
- `svc-chat` = DeepSeek-V4.1-Flash-1017 on the QSFP `ng-dgx-m` pairs (replicas `m0`/`m2`, spare `m1`; `m3` stays the ADR-074 step lane).
- `svc-decision`, `svc-fast`, `svc-vision`, `svc-embed`, `svc-rerank` and `svc-ocr` run on `ng-dgx-s` (spill to `m1`).
- **bdrt** serves `svc-chat` and `svc-fast` only.
- **The memory precondition:** DeepSeek's TensorFold recipe needs ≥ 112 GiB `MemAvailable` per node, so rccs `svc-chat` depends on probe V-1 on an exclusive `m`-pair job. If it fails, rccs `svc-chat` falls back to `svc-fast` (`default_models.fallback`).

**D-2 refinement (2026-11-01, later; maintainer, after measuring):** rccs `svc-chat` is **DeepSeek-V4-Flash** (vLLM TP=2; umrs `94ff77de`), because an idle rccs GB10 offers only ~100 GiB (kernel slab from the Lustre client) against TensorFold's 112 GiB floor. DeepSeek-V4.1-Flash-1017 serves bdrt's `svc-chat` (116 GiB free there).

**D-9 refinement:** `connect-info.default_models` advertises a role only while one of its models has a READY/DEGRADED replica on that front (umrs `f6e1bf08`). This means:
- a roster entry that is not yet staged never becomes a login default;
- neither does a model that is down.
| Live legs | Owner | plan §10.1, §10.2, §11 |

## Amendment (2026-11-03; maintainer): D-10 implicit roles

**Context.** Users talk to R4 in natural language from the TUI or the VS Code
app. Their chat model may come from a user-registered public endpoint, but the
work a prompt needs besides chat (decisions, sub-agents, image description,
retrieval, OCR) must still use the platform's service models, without the user
choosing any of them. Before this amendment R4 read `default_models` only at
login and only for `chat`, so with a public chat model every client-side role
followed the chat source: decisions went to the public endpoint and failed,
sub-agents ran on the public model, and VS Code found no vision endpoint.

**D-10. Implicit roles.** R4 resolves each role it needs, every time, in this
order:
1. an explicit choice: `R4_<ROLE>_MODEL` (for decisions, the existing
   `decision_model` settings once they differ from the packaged default);
2. the platform's live role alias, routed to the platform source:
   `svc-<role>@<platform source>`;
3. otherwise the old behaviour: the chat model, or the feature off.

- **The platform source** is the active source when it is a platform source;
  else the platform source on the deployment `gateway_url` points at (a public
  source keeps the gateway on the platform); else the only platform source.
- **Liveness** is `connect-info.default_models`. It lists a role only while it
  has a READY/DEGRADED replica (D-9 refinement), so a role alias that resolves
  is served by definition. The aggregate `/v1/models` never lists aliases, so
  it is not the test. R4 reads the map live (unauthenticated) with a 5-minute
  cache, and falls back to the copy stored at login.
- **Where each role runs:**

  | Role | Runs in | Resolved by |
  |---|---|---|
  | `decision` | R4 (decision gates, `decision_evaluate`) | R4, D-10 |
  | `fast` | R4 sub-agents (`agent_spawn` without an explicit model) | R4, D-10 |
  | `vision` | VS Code image description for a text-only chat model | the platform source's client when chat is public |
  | `embed`, `rerank`, `ocr` | UMRS jobs (RAG, knowledge ingest) | the front's configuration (`UMRS_RAG_EMBEDDING_MODEL`, `UMRS_RAG_RERANKER_MODEL`, `KNOWLEDGE_OCR_MODEL` → `svc-*` once those models serve; plan §7.1) |
  | `chat` | the user's choice | unchanged |

- **Data class (ADR-062).** Routing auxiliary work to the platform never widens
  where project data goes: the platform is allowed for every data class. A
  sensitive project still may not use a public chat route (unchanged).
- **The user sees which model did the work** (maintainer, 2026-11-03).
  `connect-info.role_models` names the served models behind each live role
  (umrs `4ec704ba`); R4's `model_label` turns `svc-fast@rccs-cloud` into
  `Qwen3.8-27B-NVFP4 (svc-fast, rccs-cloud)`. The label appears in:
  - sub-agent start lines ("Delegating to ...", "Background delegation to ...");
  - the decision gate's approval reason and the plan-reply question
    ("Read by ...");
  - the `decision_evaluate` tool result (`model`);
  - VS Code's "R4: image described by ..." notice.
- **Not changed:** compaction keeps the chat model, because wave 80 sends it on
  the turn's prefix for the prefix cache. The TUI has no image-description path
  yet.

**Rollout-status (2026-11-03):** landed repo-side: `r4/service_roles.py`,
decision settings (`configured_decision_model`), sub-agents
(`_fast_role_model`) and VS Code `pickVisionClient`. The `embed`, `rerank` and
`ocr` front settings follow once those models serve on rccs.

**Rollout-status (2026-11-04): ADR-109 extends D-1 and D-2.**
- **New alias.** `svc-vision2` = SAM3.1 (BentoML vision, adapter `sam3`). It is co-served in the `svc-vision` job: one Slurm job, two endpoint rows with their own role probes, and lifecycle actions on the job ([ADR-109](109-vision-service-and-routed-endpoints-for-steps.md) D-1/D-2).
- **New partition.** `ai-pro6000` (aarch64, 2× RTX PRO 6000 96 GB per node, exclusive) is a later candidate for `svc-vision`, after its first-launch receipt (ADR-109 D-3).
- Not landed.

## Related

- Service-model fleet plan
- DGX Spark fleet plan
- Decision model runbook
