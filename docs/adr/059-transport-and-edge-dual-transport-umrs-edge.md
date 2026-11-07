# ADR-059: Transport and edge — dual transport, the gateway as UMRS's edge process

**Status:** Accepted
**Date:** 2026-09-22 (maintainer decision, design reset wave)
**Relates to:** ADR-001 (dual transport), ADR-003 (three-component gateway), ADR-014 (edge consolidation), [docs/R4-UMRS-DESIGN.md](../R4-UMRS-DESIGN.md), gap rows D-01 / D-02 / D-50 in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

The adopted design (2026-09-22) stated one transport: "All client–server
traffic over ZeroMQ with CurveZMQ + ZAP; no other channel", UMRS as the single
trusted gateway, and the workstation's only outbound connection being the
ZeroMQ channel. The tree has never matched that: ADR-001 (Accepted
2026-05-13) records the deployed dual transport — ZeroMQ for files, jobs,
models, workflows and embedding export; HTTPS through the NGINX + auth-sidecar
gateway for chat, MCP, knowledge, sessions, catalog, projects, venue and
every other API family — and the gateway, not the UMRS server process, is the
authentication edge.

The gap analysis named this the largest open decision (D-01/D-02/D-50, all
**Contradicts**), with two options on the table:

- (a) Amend the design: ZeroMQ data plane + HTTPS control plane, both
  terminated by UMRS, the NGINX + sidecar edge declared "UMRS's edge process".
- (b) Build a versioned ZeroMQ RPC surface for the control-plane families and
  retire the HTTP edge.

## Decision

**Option (a).** The design is amended, not the tree:

1. The platform's client–server transport is the **dual transport of
   ADR-001**: ZeroMQ with CurveZMQ + ZAP (session-CURVE, ADR: `zmq_sessions`)
   for the data plane, and HTTPS for the control plane. Both are terminated
   by UMRS — the HTTPS side terminates at the **NGINX + auth-sidecar edge,
   which is part of UMRS** ("UMRS's edge process"), not a separate trusted
   component beside it.
2. "UMRS is the single trusted gateway" is satisfied by composition: edge
   process + UMRS server together are the one gateway an institution's
   security team reasons about.
3. The workstation's outbound connections are the ZeroMQ channel (data0
   50110–50118, direct TCP, CURVE-encrypted) and HTTPS to the edge (443).
   Both are confidential and authenticated; there is no plaintext channel.
4. Option (b) is rejected: it re-builds what HTTP already gives (routing,
   SSE/streaming for chat, LiteLLM and NGINX integration) for no security
   gain, and reverses ADR-001 without cause.

## Consequences

- The design document's transport, topology-table and security-table wording
  is amended in the same wave as this ADR; gap rows D-01, D-02, D-50 move
  from **Contradicts** to **Implemented (by design amendment)**.
- What remains genuinely open in this area moves to D-04 (a versioned,
  contract-tested R4–UMRS protocol document — now covering *both* lanes),
  D-03 (UMRS account record as the ZAP key authority), and D-05 (internal
  loopback-only boundary statement per profile).
- No code changes. Every diagram that draws "ZeroMQ · Curve · ZAP" as the
  only transport is corrected to the two-lane picture.
- Future work must not introduce a third client–server transport lane; new
  API families join the HTTPS control plane or, for bulk data, the
  ZeroMQ data plane.
