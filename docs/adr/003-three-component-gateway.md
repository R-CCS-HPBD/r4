# ADR-003: Three-Component Gateway Architecture

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** INV-06 (no silent auth degradation), INV-08 (presigned URL scope), INV-11 (file fetch centralization)

## Context

The Gateway serves as the API edge between clients (r4, Jupyter, n8n) and the
platform (UMRS, mini-RiVault endpoints). It was historically a single Python
FastAPI application. The architecture evolved to a three-component model:

1. **NGINX** (data plane) — TLS termination, rate limiting, large file proxying
2. **Auth Sidecar** (auth plane) — JWT validation, audit logging, presigned URLs
3. **Policy Gateway** (policy plane) — content scanning, scope checks, UMRS proxying

The Python Gateway code contains a `# DEPRECATED` comment that misleads
contributors into thinking it can be removed. It cannot — it serves active
policy functions.

## Decision

The Gateway is a **three-component system**. Each component has a distinct role:

| Component | Port | Role | Can Be Removed? |
|-----------|------|------|----------------|
| NGINX | 443/80 | Data plane (TLS, rate limit, `auth_request`) | No |
| Auth Sidecar | 8401 | Auth plane (JWT, audit, presigned URLs) | No |
| Policy Gateway | 8000 | Policy plane (content scan, scope check, UMRS proxy) | No |

The `# DEPRECATED` label is removed. The Python Gateway is renamed "Policy
Gateway" in documentation to distinguish it from the NGINX and Sidecar
components.

## Consequences

- NGINX `auth_request` directive calls the Auth Sidecar for every request
- If the Auth Sidecar is down, all requests fail (500/502) — no silent degradation
- The Policy Gateway applies content scanning and LLM response validation
- Gateway routes are pure proxies to UMRS — no durable state in Gateway
- n8n, Jupyter, r4, and all clients talk to NGINX; never to UMRS directly
- `/gw/ops/ping` and `/gw/ops/connect-info` bypass `auth_request` — they are
  unauthenticated introspection endpoints used for client auto-discovery

## Alternatives Considered

- **Single Python Gateway**: Cannot handle large file uploads (NGINX is needed
  for streaming). Cannot do TLS termination efficiently in Python.
- **NGINX + Auth only (no Policy Gateway)**: Loses content scanning, LLM
  response validation, and route-level scope checks. These cannot be done in
  NGINX alone.
- **API Gateway service (Kong, Envoy)**: Adds operational complexity. The
  three-component model is simpler to deploy on HPC where network configuration
  is constrained.
