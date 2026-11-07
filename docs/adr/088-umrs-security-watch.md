# ADR-088: UMRS Security Watch — AI triage beside deterministic enforcement

**Status:** Accepted (2026-10-08; agent decisions under maintainer delegation, see the Decisions block in the rollout status)
**Date:** 2026-10-08
**Relates to:** ADR-076 (decision model: evidence, never permission), ADR-062 (model locality / data class), ADR-039 (untrusted-content taint), ADR-061 (sandbox profiles), ADR-074 (sandboxed step code), ADR-080 / ADR-086 (access guard, circles), ADR-046 (remote-session credentials), ADR-060 (topology); gap row **D-61** (new), D-52 (observability), D-57; STATE `SEC-20260925-001`; dgx-spark-fleet-plan-2026-10-08.md

## Context

The maintainer asked on 2026-10-08 for "a security enforcement AI agent for
UMRS services". They chose the shape on the same day: an out-of-band watch
first, then an inline admission reviewer.

What is true today (verified 2026-10-08 against parent `6ad5ee5b` / umrs `e9127d09`):

- **Enforcement is deterministic and spread across the boundary.**
  - Edge JWT (`auth_request`, `gateway/sidecar/main.py`, table `gateway_audit_log`).
  - ZeroMQ session-CURVE + ZAP (`umrs/src/umrs/core/session_curve_provider.py`).
  - EgressGuard (ADR-040).
  - Sandbox profiles (ADR-061) and the step lane's refusals (ADR-074).
  - The access guard (ADR-080/086) and MCP trust classes.
  - The knowledge decision gate (`umrs/src/umrs/api/_knowledge_mutation_guard.py`, ADR-050).
  - The registration-time recipe/skill scanner (`umrs/src/umrs/security/scanner.py`).
    (dated note 2026-10-19, cleanup C-3b(iv): nothing under umrs `src`
    calls `RecipeScanner`/`SkillScanner`, so this lever exists as code but
    fires nowhere — the r4-contracts client-side pre-flight scan is the
    only one wired; recorded on gap row D-61.)
- **The structured audit stream is thin.** `emit_audit` (`umrs/src/umrs/api/audit_events.py`, schema v1, persisted by `audit_store.py` into Postgres `audit_events`) has 28 call sites. They cover `endpoint.*`, `governance.*`, `*.access_changed`, `mcp.registered/unregistered`, `rag.upload` and `knowledge.export.raw`. Several kinds of event never reach it: auth failures, ZeroMQ session mints and revocations, egress denials, MCP call-time trust refusals, step-lane refusals and scanner findings. They live only in process logs, which Promtail ships to Loki on umrs-front (`deploy/ansible/roles/front_end/tasks/observability.yaml`), if at all.
- **Containment levers already exist as code, but nothing calls them from a detection.**
  - `SessionCurveProvider.revoke` / `revoke_user`
  - `SecretBroker.revoke_lease` (`jobs/secret_broker.py`)
  - `tenants.suspend`
  - `companion_trust` revoke
  - `grants` revoke
  - `scheduler.triggers.disable`
  - endpoint access change (`PUT …/access`) and deprovision
- **No component watches the stream.** Grafana alerting is the only signal path, and no security rules are defined in the repo. `SEC-20260925-001` (uploaded step code reached the server process, found by a human review) is the kind of finding nothing would have raised by itself.
- **In-site models now exist.** Bespoke-Nimble-9B serves fast typed judgments (ADR-076; benchmark p50 about 230 ms), and the GB10 fleet plan proposes an always-on in-site chat model. Audit data is sensitive by default, so only in-site models may read it (ADR-062).

Three forces shape the decision:
- A national-facility platform needs detection and a fast response, not only prevention.
- An AI component in the enforcement path would make availability and integrity depend on a model that can be prompt-injected through the very logs it reads.
- An agent with admin levers is a new high-value identity to protect.

Without a decision, a contributor could put a model in front of requests (latency, availability coupling, injection) or give an agent broad admin rights.

## Decision

**D-1 Ownership and placement.** Security Watch is a UMRS service.
- Its rule engine, triage planner and response planner are a foundation module, `umrs/src/umrs/security_watch/` (no `umrs.api` / `umrs.server` imports).
- Its executor runs beside the janitor and the endpoint relay in the server lifespan, or as a separate process in the `cpu-agent` sandbox profile on the front-end.
- Today there is one instance per venue server; it moves to the hub when D-47 converges.
- R4 and the edge carry no watch logic. R4 shows incidents and approvals through the existing surfaces.

**D-2 Enforcement stays deterministic; the watch is monotone.**
- No request waits on the watch, and its absence or failure changes no admission decision.
- Its outputs may only **add** restriction: notify, contain, or hold for a human. It never loosens a policy, reverses a revocation, approves, or lowers the severity a rule assigned.
- Its failure mode is "watch degraded" (one notification), never blocked traffic.

**D-3 Telemetry first.** No detector is written for an event the audit stream does not carry.
- New `emit_audit` families:
  - `auth.failed` (edge and UMRS)
  - `zmq.session_minted` / `zmq.session_revoked`
  - `egress.denied`
  - `mcp.invoke_refused`
  - `step.refused`
  - `scanner.finding`
  - `export.*`
  - `admin.*` (role, quota and `access → system` changes)
- Every watch action emits `security.*`.
- The watch reads the stream (`audit_events`, `gateway_audit_log`, and Loki for process logs) through a **read-only** database role.

**D-4 Detection pipeline: rules, then triage, then investigation.**
1. **Rules (the floor).** A versioned policy file (`security_watch/rules/*.yaml`, loaded as configuration, not code) defines deterministic SQL/LogQL detectors with a severity each. Examples: auth-failure bursts per actor/IP; egress-deny spikes; `access → system` outside admin hours; raw exports; GPU hoarding per actor; repeated `step.refused`; scanner findings. Rules alone satisfy D-5 tier T0.
2. **Triage.** The decision model scores each rule hit with fixed questions: misuse yes/no, severity choice, "needs human" yes/no. These scores can raise severity or attach a recommendation; never the reverse (D-2).
3. **Investigation.** Above a threshold, the in-site chat model receives the incident with **read-only** tools only: audit query, job/endpoint status and logs, actor history. It writes a structured incident report (what, who, evidence ids, recommended tier).
4. **Input handling.** Every model input is untrusted content (ADR-039). File names, prompts and tool arguments copied from audit rows are quoted data, never instructions. Secrets are redacted before any prompt. No string a model produces becomes an argument to a lever; levers take only ids taken from the triggering audit rows.

**D-5 Response tiers.**

| Tier | Who triggers | Actions |
|---|---|---|
| T0 Notify | always | Grafana alert; one `security`-labelled issue per incident (deduplicated; ids and counts only, see Q2 below); incident row |
| T1 Contain | the watch, automatically, only for actions on the site allowlist | reversible, scoped, tightening-only: revoke one ZeroMQ session; revoke one job's secret leases; disable one scheduler trigger; narrow one endpoint's access to its owner. Rate-limited (per action, per actor, per hour). Each carries an undo recorded for a human. |
| T2 Hold for a human | the watch proposes; a recorded human decision executes | suspend a tenant; cancel jobs; deprovision; revoke grants or tokens; egress-policy changes |
| T3 Never | — | loosen any policy; edit config or deploy; delete data; mint or rotate credentials; act on the watch's own policy |

T1 ships **disabled**: the watch starts in **shadow mode** (T0 plus a record of the T1 actions it would have taken). An action is switched on per site in config only after its measured precision is accepted by the maintainer.

**D-6 The watch's own identity.**
- It runs as its own service principal: read-only on the audit tables, plus a `security:contain` scope that only the T1 endpoints accept.
- It holds no user JWT and no provider key (ADR-046 rule).
- Its every call is audited with rule id, scores, model ids and evidence ids (no raw user content).
- Kill switch: `UMRS_SECURITY_WATCH=off`. A separate `UMRS_SECURITY_WATCH_CONTAIN=off` stops T1 only.

**D-7 Data locality.**
- Watch prompts go only to in-site models through `/umrs/llm` (ADR-062).
- When those are unavailable (for example during a GB10 reservation), the watch runs rules-only and says so in one notification. It never falls back to a public provider.

**D-8 Inline admission reviewer (second phase).**
- Rare high-risk admissions get a review after their deterministic checks pass:
  - workflow / step-code upload
  - MCP registration and Hatch publish
  - egress-allowlist request
  - model-download request
  - `access → system` grant
- The reviewer can only turn "admit" into "hold for a human" (an approval in the existing decision surface).
- If the reviewer is unavailable, the deterministic decision stands and the watch reviews the admission after the fact. A site may opt into "hold when unavailable" (`security_watch.admission.strict: true`).
- The reviewer is never asked about ordinary requests (chat, reads, job status).

### Why

- Deterministic guards give the guarantees; the model adds coverage and context that would cost a human hours per incident. Mixing the two roles would let an injected log line, or a model outage, decide admission.
- Monotone outputs (D-2) bound the damage of a wrong or manipulated model to friction (an extra question, a revoked session), never to a granted permission. This is the same rule as ADR-076 D-3.
- Telemetry first (D-3) keeps the watch honest: a detector over an event that is never emitted is a false sense of safety.
- Shadow mode and per-action enablement (D-5) stop false positives from becoming outages for working scientists.

## Consequences

- **Positive:**
  - The first detection-and-response path for UMRS.
  - Containment levers that already exist get a governed caller.
  - Audit coverage grows for every consumer (Grafana, an external SIEM, human review).
  - The decision model and the in-site chat model gain a second consumer (fleet-plan §3).
- **Cost:**
  - New audit call sites across UMRS and the edge.
  - A rules file to curate.
  - A service principal and a read-only role to provision (owner legs).
  - Some GB10 capacity for triage and investigation.
  - An incident surface to staff.
- **NOT in scope:**
  - Replacing any existing guard.
  - Automatic loosening.
  - Inline review of ordinary traffic.
  - Training a security model.
  - Host-level IDS on cluster nodes (operator domain).
  - Exporting to an institutional SIEM (compatible via JSON lines; a later site decision).

## Alternatives Considered

- **Rules + Grafana alerts only.** This is the floor of D-4 and stays. Alone it gives no triage or context, so every alert costs a human investigation.
- **An LLM gate in front of every request.** Rejected: it adds latency to every call, couples platform availability to model availability, and puts injection-prone input on the allow path.
- **An autonomous security agent with admin rights.** Rejected: it becomes a new top-value identity, and an injected or wrong decision becomes an outage or a breach.
- **An external SIEM (Wazuh, Elastic) instead.** Complementary, not a replacement: it does not know UMRS semantics or levers. D-3's audit stream is exportable to one.

## Rollout-status (2026-10-08)

Nothing landed; proposed. Waves:

| Wave | Content | Sub-decisions |
|---|---|---|
| W0 | audit families of D-3 + `security.*` schema; read-only role | D-3, D-6 |
| W1 | rules engine + first rule set; T0 notifications; shadow T1 log | D-1, D-2, D-4.1, D-5 |
| W2 | Nimble triage + in-site investigator with read-only tools; redaction; incident report | D-4.2–D-4.4, D-7 |
| W3 | T1 actions switched on one by one after measured shadow precision; T2 proposals through the decision surface | D-5, D-6 |
| W4 | admission reviewer on the five admission kinds | D-8 |

Maintainer decisions:
1. The initial T1 allowlist.
2. The notification channel and who receives incidents (maintainer only, or the RCCS security contact).
3. Whether a strict site holds admissions while the reviewer is unavailable.
4. Which in-site chat model investigates (fleet-plan §7 Q2).

## Rollout-status (2026-10-08, later) — decisions taken; status Accepted

The maintainer delegated the four open decisions to the agent on 2026-10-08
("please decide by your decision"). Nothing is landed yet; W0 is next.

**Q1 — Initial T1 allowlist.** Five actions. Each one affects one object, is
tightening-only, and is undone by an ordinary user or admin action.

| # | Action | Lever | Undo |
|---|---|---|---|
| T1-a | revoke one ZeroMQ session | `SessionCurveProvider.revoke(session_id)` | the user logs in again |
| T1-b | revoke one job's secret-broker leases | `SecretBroker` / `revoke_job_leases(job_id)` | a resubmit gets new leases |
| T1-c | disable one scheduler trigger | `scheduler.triggers.disable(trigger_id)` | the owner re-enables it |
| T1-d | narrow one endpoint's access to its owner | `PUT /umrs/endpoints/{id}/access` | the owner or an admin sets it back |
| T1-e | revoke one companion "always allow" row (one user, one tool) | `companion_trust` revoke | the user grants it again at the next prompt |

Rules for these actions:
- **Not on T1:** `revoke_user` (all of a user's sessions), `tenants.suspend`, grant or token revocation, job cancel, deprovision. All of these go to T2.
- **Rate limits:** at most 3 T1 actions per actor per hour and 20 per venue per hour. Past either limit, further actions become T2 proposals.
- **Protected principals:** T1 never acts on an admin principal, the watch's own principal, or service principals (relay, janitor, venue workers). Those are T2 only, so a wrong verdict cannot lock out the people who would correct it.
- **When an action may be switched on:** after at least 14 days of shadow mode, at least 10 would-be actions of that type, and at least 0.9 precision against human labels on the incident rows. Switching on is a site-config change through a converge (owner leg).

**Q2 — Incident channel and recipients.**
- The incident of record is a row in UMRS (Postgres, beside `audit_events`), with evidence ids and model scores. Only admins can read it.
- Notification:
  - a Grafana alert (loopback UI, as today);
  - one `security`-labelled issue per incident in this repository, which is private (`gh repo view`: PRIVATE, 2026-10-08), deduplicated per rule × actor × day.
- Issue bodies carry only the incident id, rule id, severity, venue, counts and tier. They never include user content, prompts, file names, tokens or IP addresses; those stay in the UMRS row.
- Recipient: the maintainer only until W3. Adding an RCCS security contact is an owner step: the maintainer names the person and the channel. It happens once T1 precision is measured, so that false positives do not reach an outside team.

**Q3 — Strict admission hold.** No site is strict. Every venue runs
`security_watch.admission.strict: false`: when the reviewer is unavailable,
the deterministic decision stands and the watch reviews the admission
afterwards. Reason: the reviewer's model runs on GB10. On 2026-10-08 a
single user reservation takes every rccs GB10 node for 7 days, so
a strict hold would stop all uploads and registrations for a week. Revisit
when the reviewer has two venues (fleet-plan R4/R4b) or for a site with a
stricter regime (Rikyu, FugakuNEXT).

**Q4 — Investigator model.** `Qwen3.8-27B-NVFP4`: the rccs entry has
landed, as has a bdrt entry. Compared with Qwen-AgentWorld-35B-A3B BF16:
- one GB10 at 0.70 util versus 0.78, so it shares the in-site slot with the fleet plan;
- 262K context with fp8 KV for long audit windows;
- `qwen3_xml` tool calling;
- no source patch, where AgentWorld needs `VLLM_PATCH_QWEN3_5`.

The same model is the fleet plan's in-site chat model (§7 Q2), so the watch adds no GB10 node of its own.

## Related

- [ADR-076](076-decision-model-role.md), [ADR-062](062-model-locality-data-class.md), [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-061](061-mini-rivault-sandbox-profiles.md), [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-080](080-one-access-guard-shareable-resources.md), [ADR-046](046-remote-session-credential-egress.md)
- dgx-spark-fleet-plan-2026-10-08.md — where triage and investigation run
- `.github/instructions/mutation-safety.instructions.md` (security invariants this ADR must not weaken)
