# ADR-027: Workflow upload authorization — role-gated uploads, system scope admin-only, single enforcement point

**Status:** Accepted (implemented P0+P1, 2026-08-17)
- **Date:** 2026-08-17
- **Related:** ADR-024 (knowledge module boundary), GAP-WF-1 in wave-companion-any-client-2026-08-16.md, Wave-6 F1/F1b (verified-identity HMAC chain)

> **Implementation note (2026-08-17).** P0+P1 landed as specified, with two
> refinements recorded for future readers:
> 1. **Claim carrier kept, canonical basis split.** The `workflow_author`
>   claim rides the same gateway-issued verified-header channel under HMAC
>   cover, but on its **own canonical signature basis**
>   (`sign/verify_workflow_author_claim`, `v1wf\n…`) rather than extending
>   the identity tuple. A shared 5-line tuple would have desynchronized the
>   signature on every route that never forwards the author header during
>   rolling deploys. The trust requirement (claim under signature cover,
>   single claim carrier) is unchanged.
> 2. **P1 compression: hard deprecation immediately.** The ZMQ
>   `UPLOAD_WORKFLOW` handler fails fast with guidance instead of a
>   warning-first window — that path carries no verified identity at all,
>   and softening prolongs the unauthenticated-code-upload window this ADR
>   exists to close. The active r4 client already uploads over Gateway HTTP.
> IdP role minting stays parked (below); admins pass today.

## Context

The workflow upload surface lets any authenticated caller upload and
**auto-register** executable step scripts that run server-side. Verified against
current code (2026-08-17):

| Fact | Evidence |
|---|---|
| `POST /umrs/workflows/upload` requires a verified identity only (`_require_verified_workflow_identity` — HMAC-signed identity tuple, strict mode after Wave-6 F1). **No role check anywhere in the handler.** | [umrs/src/umrs/api/workflows.py](../../umrs/src/umrs/api/workflows.py) |
| Caller-supplied `scope: str = Form("user")` is passed verbatim into `register_workflow(..., scope=scope)` + `record_resource_created(tier=scope)`. Nothing validates it. | [umrs/src/umrs/api/workflows.py](../../umrs/src/umrs/api/workflows.py) |
| A caller can register `scope=system` — a platform-shared workflow visible to all users — with no admin requirement. Meanwhile `ModelScope.SYSTEM` and `SkillScope.SYSTEM` already require admin (inconsistent trust boundaries on the same platform). | [models.py](../../umrs/src/umrs/api/models.py), [skills.py](../../umrs/src/umrs/api/skills.py), [scopes.py](../../umrs/src/umrs/api/scopes.py) |
| Workflows are executable: upload extracts step scripts into the server's workflow storage and `RUN_WORKFLOW` executes them. Impact is server-side code execution by any authenticated user. | [file_handling.py](../../umrs/src/umrs/server/file_handling.py), [workflows.py](../../umrs/src/umrs/api/workflows.py) |
| The ZMQ `UploadWorkflowHandler` runs behind hardcoded `scope="user"` (system scope unreachable via ZMQ) but trusts only self-asserted `client_id` — no verified identity at all. | [upload_workflow.py](../../umrs/src/umrs/server/commands/upload_workflow.py) |
| The companion "workflow upload (privileged)" tool description is convention, not enforcement — no server-side permission gate corresponds to it. | [workflow_tools.py](../../r4/src/r4/companion/workflow_tools.py) |
| `_identity_verified` execution-access handshakes already exist on the ZMQ registry (`workflow_registry_handler`) — the upload path simply never inherited them. | [workflow_registry_handler.py](../../umrs/src/umrs/server/workflow_registry_handler.py) |

The platform constitution mandates zero-trust: authenticated ≠ authorized.
Untrammelled upload is a P1 authorization gap and joins GAP-WF-1 in the wave ledger.

## Decision

1. **Role-gated uploads.** `POST /umrs/workflows/upload` requires a
   `workflow_author` claim inside the HMAC-signed identity tuple (the same
   tuple `_verified_headers.verified_headers_trusted` verifies today — not a
   new authorization channel). Two tiers:
   - `workflow_author` (or broader admin) → user-scope uploads allowed.
   - `admin` → additionally `scope=system` permitted; non-admin callers sending
     `scope=system` → `403` with a descriptive error (aligning with the
     ModelScope/SkillScope enforcement already in production).

2. **Single claim carrier.** Authorization derives exclusively from the
   **gateway-signed identity** (IdP/JWT → sidecar headers). No UMRS-local role
   configuration file. Rationale: a second source of truth drifts from the IdP
   and prices in dual revocation paths — rejected alternative A below.

3. **Single enforcement point.** ZMQ workflow upload is deprecated: all uploads
   route through Gateway HTTP. Rationale: the ZMQ handler trusts `client_id`
   with no signature coverage, requiring dual hardening across two stacks for
   marginal in-cluster convenience; `.api/workflows.py` is the only new
   enforcement point needed. Transition: P1 window — requests over ZMQ return
   `410 Gone` with a pointer to the HTTP route after the deprecation notice
   period. Existing in-cluster clients unaffected on every other ZMQ verb —
   only `UPLOAD_WORKFLOW` goes away.

4. **Audit every decision.** Upload authorization decisions (allow + deny)
   append an audit event typed `workflow_upload` carrying actor, requested
   scope, decision, matched claim, and request id (closes the GAP-03 adjacency
   — authority decisions without durable record must never exist for
   mutating code surfaces).

5. **Grandfathering.** Workflows already registered before this ADR lands keep
   their current scope (user or system) and continue to execute for their
   existing callers — authorization gates the *upload* surface, not existing
   registrations. Immediately after the role gate ships, admins may relabel
   existing system-scope uploads after review (runbook operation, not code).

### Parked (explicitly unresolved)

- Whether `workflow_author` is a **new IdP role** minted by the identity
  provider, or a **UMRS-side grant keyed off an existing role** (e.g. project
  owner), which the sidecar maps into the identity tuple. The requirement is
  fixed: the claim must be inside the HMAC cover. The provisioning mechanism is
  an IdP-capacity decision for the Wave-6 auth-hardening track and does not
  gate P0.

> **Provisioning decision (2026-08-18).** Until the IdP can mint
> `workflow_author`, the claim is derived **from the admin role only** (admins
> are implied authors) — today the gate is effectively `admin`, with the
> chain ready for the real role. UMRS-side grants (registry of
> user→workflow_author mappings) stay rejected: a second claim-carrier
> source of truth drifts and prices in dual revocation — the exact failure
> mode from the Context table. Granting non-admin upload power is deferred
> to the IdP (Privilege-elevation via `r4 admin grant` or the IdP console).
> No other path is accepted; do not add a local override key file.

## Consequences

### Phasing

- **P0 (this wave, blocking hardening ship):** `workflow_author` + admin gates
  on `POST /upload` incl. `scope=system`; `403` semantics with explicit error
  bodies matching the Model/Skill pattern; P0 carries the P2 audit-event
  emission as a tail-item if cheap.
- **P1 (next):** ZMQ deprecation window (warning returns first, then 410) +
  client-side docs in `r4 workflow upload --help` noting HTTP-only.
- **P2:** `workflow_upload` audit-event schema + retention (90 days, matching
  knowledge_ingest precedent) + periodic report of denied attempts.

### Operational constraints

- No change to run/action lifecycle of pre-existing registered workflows.
- NGINX `/gw/workflow/upload` must forward `X-Verified-Is-Admin` (already
  forwarded) and propagate the new claim once the IdP mints it — coordination
  checklist in the rollout note.
- The companion `workflow_upload` tool gains a real preflight: permission
  check inside `PermissionManager` fails closed when the role claim is absent
  at the signed tuple (the docstring already promised "(privileged)" — now it
  means something).

### Rejected alternatives

- **A — UMRS-local role configuration** (`site.yaml: workflows.author_roles`):
  creates a second source of truth, silently forks revocation semantics from
  the IdP, and drifts under site-config migrations. Also fails the project's
  "policy as configuration at the IdP edge" precedence (identity claims belong
  at the IdP edge, not per-service reg).
- **B — Keep ZMQ upload with verified-identity handshake** (`zmq_caller_from_payload`
  inheritance): keeps dual enforcement surfaces alive for marginal in-cluster
  convenience; the registry already proved identity verification works, but the
  CVE surface is "one forgot-to-inherit handler" and HTTP-only has exactly one.

## References

- GAP-WF-1 registration + verification date
- [workflows.py](../../umrs/src/umrs/api/workflows.py) — upload path with no role gate today
- [_verified_headers.py](../../umrs/src/umrs/api/_verified_headers.py) — HMAC-signed identity tuple (wave-6 F1/F1b hardening target surface)
- [upload_workflow.py](../../umrs/src/umrs/server/commands/upload_workflow.py) — ZMQ handler trusting self-asserted client_id
- [workflow_registry_handler.py](../../umrs/src/umrs/server/workflow_registry_handler.py) — `_identity_verified` execution handshake precedent
- [models.py](../../umrs/src/umrs/api/models.py) / [skills.py](../../umrs/src/umrs/api/skills.py) — existing `admin`-only system-scope pattern this ADR applies to workflows
