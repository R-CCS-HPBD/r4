# Gateway / Sidecar Enforcement Boundary

> Audience: **operators** deploying R4 Platform.
> For invariant definitions see [PLATFORM_CONTRACT.md](../PLATFORM_CONTRACT.md).

> **⚠ ADR-014 (2026-09-10):** every mention of the **Python Gateway**
> (`gateway/gateway/`, FastAPI :8000) below describes the *retired* edge.
> Production traffic is **NGINX + Auth Sidecar** only; the Python Gateway
> layer exists for development compatibility until its scheduled removal
> (**not before 2026-12-06**, per ADR-014 phase 3). All "belt-and-suspenders"
> rows that cite it apply to deployments that still opt into it; operators
> on the production profile should read them as UMRS-owned responsibilities
> (the NGINX path enforces auth at the edge; UMRS routes enforce scope
> server-side).

## Purpose

The R4 Platform Gateway has two authentication paths that can be
deployed together or separately. This document explains where each
responsibility lives so operators can verify their deployment enforces
the full security posture.

Omitting the sidecar in a deployment that uses NGINX must cause a
**loud startup failure**, not silent security degradation.

---

## Components

| Component | Process | Typical Port | Source |
|---|---|---|---|
| **NGINX** | Reverse proxy | 80 / 443 | `gateway/nginx/` |
| **Auth Sidecar** | FastAPI service | 8401 | `gateway/sidecar/main.py` |
| **Python Gateway** | FastAPI service | 8000 | `gateway/gateway/` |

---

## Deployment Topology

```
Client
  │
  ▼
┌────────────────────────────────────────────────────────────┐
│  NGINX (TLS termination, rate limiting, static files)      │
│  ┌──────────────────┐                                      │
│  │  auth_request    │──────▶ Auth Sidecar (:8401)          │
│  │  /validate       │        ├─ JWT validation             │
│  └──────────────────┘        ├─ Audit logging (Postgres)   │
│         │                    └─ Presigned URL generation   │
│         │ (X-User-ID, X-Project-ID headers set)            │
│         ▼                                                  │
│  Python Gateway (:8000)                                    │
│  ├─ Route-level auth (reads X-User-ID/X-Project-ID)        │
│  ├─ Policy enforcement (scope, ownership checks)           │
│  ├─ UMRS proxy (job, model, workflow, endpoint APIs)       │
│  └─ LLM proxy (endpoint discovery → LiteLLM)               │
└────────────────────────────────────────────────────────────┘
         │
         ▼
      UMRS (:8000)
         │
         ▼
    Mini-RiVault
```

---

## Responsibility Matrix

| Concern | Where enforced | Fail mode if missing |
|---|---|---|
| **JWT validation** | Auth Sidecar (`/validate`) | NGINX returns 401 for every request (auth_request fails). |
| **JWT secret safety** | Auth Sidecar (`validate_auth_config()`) | Sidecar refuses to start if dev secret is used in non-dev environment on non-loopback host. |
| **Token expiry check** | Auth Sidecar (`JWT_REQUIRE_EXP`) | Expired tokens are rejected with 401. |
| **Subject claim check** | Auth Sidecar (`JWT_REQUIRE_SUB`) | Tokens without `sub` are rejected with 401. |
| **Project-ID extraction** | Auth Sidecar (`_resolve_project_id`) | Tokens without `project_id`/`tenant_id` are rejected with 401. |
| **User identity headers** | Auth Sidecar → NGINX → Gateway | NGINX sets `X-User-ID`, `X-Project-ID` via `auth_request_set`. Gateway reads these for downstream authorization. |
| **OIDC CLI callback trampoline** | Apache → NGINX (`/auth/cli-callback`) → Auth Sidecar (`/auth/cli-callback`) | OIDC browser login cannot complete; the IdP callback has no HTTPS+FQDN landing route. |
| **Audit logging** | Auth Sidecar (Postgres `gateway_audit_log`) | If database is unreachable, sidecar continues without audit (logged as warning). |
| **Download authorization** | Auth Sidecar (`/validate-download`) → UMRS (`/umrs/files/presign`) | Project-scoped path validation, then UMRS ACL check. Path traversal (`..`) and cross-project access are rejected. |
| **Presigned URL generation** | Auth Sidecar (local S3 signing) or UMRS (delegated) | Presigned URLs are never exposed to external clients. |
| **Route-level scope checks** | Python Gateway (`_umrs_proxy.py`) | `resolve_user_id()`, `verified_project_headers()`, `is_admin_claim()` enforce per-route ownership and scope. |
| **LLM endpoint discovery** | Python Gateway (`routes/llm.py`) | Queries UMRS for active endpoints; returns 503 if none are ready. |
| **LLM response guard** | Python Gateway (`security/llm_response_guard.py`) | Content scanning on LLM responses (both streaming and complete). |
| **Egress policy** | Python Gateway (`routes/egress.py`) | Domain allowlist, content scanning, SSRF protection, rate limiting, audit. |
| **Rate limiting** | NGINX (`limit_req`) and Gateway config | NGINX provides connection-level limits; Gateway adds per-user/per-route limits. |
| **TLS termination** | NGINX (if configured) | Internal traffic is HTTP only. No TLS between Gateway and UMRS. |

---

## Auth Flow Detail

### Standard API Request

```
1. Client sends: Authorization: Bearer <JWT>
2. NGINX intercepts → auth_request to Sidecar /validate
3. Sidecar validates JWT locally (no UMRS call)
4. Sidecar returns 200 + X-User-ID, X-Project-ID headers
   (or 401 on failure)
5. NGINX captures headers via auth_request_set
6. NGINX forwards request + identity headers to Python Gateway
7. Gateway reads X-User-ID, X-Project-ID for scope enforcement
```

### OIDC CLI Browser Login

> **Clarification:** This flow uses **Authorization Code + PKCE**, not the
> Device Authorization Grant (RFC 8628).  The CLI opens the user's browser
> directly — there is no device code or polling.  oauth2-proxy is NOT
> involved; the CLI exchanges the authorization code with Okta by itself.
>
> **Okta application type:** Native Application (public client, no
> client_secret).  See [oauth2-proxy/README.md](../deploy/oauth2-proxy/README.md#step-5-r4-cli--oidc-login-server-side-callback) for setup.

This deployment supports browser-based login for the `r4` CLI without
registering a localhost redirect URI in the IdP.

External callback route registered with the IdP:

```
https://<host>/<cluster>/auth/cli-callback
```

Examples:

```
https://data0.ai.r-ccs.riken.jp/rccs/auth/cli-callback
https://data0.ai.r-ccs.riken.jp/ai4s/auth/cli-callback
```

Internal hop inside the gateway stack:

```
NGINX route:   /auth/cli-callback
Sidecar route: /auth/cli-callback
```

Flow:

```
1. r4 CLI starts a temporary localhost HTTP listener on the user's machine.
2. r4 opens the browser to the IdP authorization endpoint with:
  redirect_uri=https://<host>/<cluster>/auth/cli-callback
3. The IdP redirects the browser to the public HTTPS callback route.
4. Apache forwards `/<cluster>/auth/cli-callback` to the cluster-local NGINX.
5. NGINX proxies `/auth/cli-callback` to the auth sidecar with no auth_request.
6. The sidecar serves a small HTML/JS trampoline page.
7. That page navigates the browser to:
  http://localhost:<cli_port>/callback?code=...&state=...
8. The CLI captures the code and exchanges it directly at the IdP token endpoint
  using PKCE.
```

Security properties:

- The IdP-facing redirect URI is always HTTPS + FQDN.
- The localhost hop is only a browser-local relay after the IdP callback.
- The sidecar does not mint tokens; it only relays the authorization code.
- The CLI uses a public PKCE client; no client secret is present on the user machine.
- PKCE binds the code exchange to the original CLI session.

### File Download

```
1. Client sends: GET /gw/files/download/<key>
2. NGINX → Sidecar /validate-download/<key>
3. Sidecar validates JWT + checks key is within caller's project scope
4. Sidecar asks UMRS /umrs/files/presign for ACL + presigned URL
5. Sidecar returns X-Accel-Redirect header
6. NGINX fetches file from S3 via internal redirect
7. Client receives file (never sees presigned URL)
```

---

## What Happens Without the Sidecar

If the auth sidecar is not running:

1. **NGINX `auth_request` fails** → all requests get 500/502.
2. **No silent degradation** — the deployment does not fall back to
   unauthenticated access.
3. **Python Gateway** also validates JWT independently (belt-and-suspenders),
   but identity headers from NGINX would be missing, causing scope
   enforcement to fail.

This is by design. The sidecar is not optional in a production deployment.

---

## Python Gateway Auth (Belt-and-Suspenders)

The Python Gateway has its own JWT validation in `gateway/auth.py`:

| Feature | Gateway auth | Sidecar auth |
|---|---|---|
| HS256 validation | Yes | Yes |
| RS256/JWKS validation | Yes (Okta/OIDC) | Yes (Okta/OIDC) |
| Audit logging | No (delegates to sidecar) | Yes (Postgres) |
| Presigned URLs | No | Yes |
| Dev-secret safety check | No (trusts NGINX/sidecar boundary) | Yes (refuses to start) |

When both are deployed, the sidecar handles auth at the NGINX layer and
the Gateway re-validates as defense-in-depth. The sidecar is the
**primary** auth boundary; the Gateway is the **secondary**.

---

## Operator Verification

### Minimum viable production deployment

- [ ] Auth sidecar is running and reachable by NGINX on the `auth_request` endpoint.
- [ ] `JWT_SECRET` is set to a strong value (not the dev default) on **both** sidecar and Gateway.
- [ ] `GATEWAY_ENVIRONMENT=production` on the sidecar.
- [ ] Sidecar Postgres connection is configured for audit logging.
- [ ] NGINX `auth_request` directive points to sidecar `/validate`.
- [ ] NGINX `auth_request_set` captures `X-User-ID` and `X-Project-ID`.
- [ ] The IdP redirect URI is registered as `https://<host>/<cluster>/auth/cli-callback`.
- [ ] The sidecar `JWT_JWKS_URL` / `JWT_ISSUER` settings match the IdP when OIDC is enabled.

### Testing auth enforcement

```bash
# Should return 401 (no token)
curl -s -o /dev/null -w "%{http_code}" http://gateway/gw/llm/v1/models

# Should return 200 (valid token)
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer <valid-jwt>" \
  http://gateway/gw/llm/v1/models

# Should return 401 (expired token)
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer <expired-jwt>" \
  http://gateway/gw/llm/v1/models
```

---

## Related Documentation

| Document | Purpose |
|---|---|
| [PLATFORM_CONTRACT.md](../PLATFORM_CONTRACT.md) | Unconditional vs. site-dependent guarantees. |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Platform topology and invariants. |
| [EGRESS_INFRASTRUCTURE.md](EGRESS_INFRASTRUCTURE.md) | Egress containment for site operators. |
