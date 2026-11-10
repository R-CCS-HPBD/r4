# The edge — NGINX + auth sidecar

**Status (2026-09-22, corrected 2026-10-18):** this directory is the
external HTTP entry point in front of UMRS. ADR-059 (Accepted 2026-09-22)
declared it **UMRS's edge process**: edge and server together are the single
trusted gateway, and the design carries HTTPS control plane + ZeroMQ data
plane (gap rows D-01/D-02 Implemented by design amendment in
[../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md)).
It is deployed on every profile and never bypassed. It must remain stateless
(CONSTITUTION §1.5). (This paragraph said the edge was not a design component
and its future was the open decision D-01/D-02.)

## What it does

- **Authentication**: NGINX `auth_request` to the sidecar, which validates
  bearer JWTs locally (admin-issued HS256 or OIDC RS256), returns
  `X-User-ID` / `X-Project-ID`, and refuses the dev secret outside
  development/test.
- **Routing**: `/gw/<family>/*` → UMRS `/umrs/<family>/*` (llm, rag, mcp,
  skills, endpoints, jobs, models, knowledge, catalog, companion, sessions,
  workflows, loops, …). UMRS owns endpoint discovery and the LLM proxy.
- **Rate limiting** per route class (LLM, upload-heavy, general).
- **Files**: `/gw/files/upload` streams to UMRS; `/gw/files/download/*` is
  validated by the sidecar and served via X-Accel-Redirect — presigned S3
  URLs never leave the platform.
- **ZeroMQ session bootstrap**: on JWT login the client obtains a
  session-CURVE credential through UMRS (`/umrs/zmq/session`) so direct-TCP
  ZeroMQ traffic is authenticated (ZAP) and encrypted.
- **Audit** to Postgres (`gateway_audit_log`).
- **Health**: `/health` is the gateway's own liveness (static 200, never
  asks the backend); `/health/backend` is 200 only when this gateway's UMRS
  answers its `/health`, else 503 — the probe for "is the platform
  serving"; `/ready` is aggregate readiness (sidecar + UMRS `/readiness`);
  `/health/ready` is the sidecar's own liveness. *(2026-10-30, issue #634:
  this line called `/health/ready` the aggregate; the rows and the reasoning
  are in [docs/architecture/design/apis.md §2](../docs/architecture/design/apis.md).)*
- **Public service-model badge** (2026-11-07): `/status/models.json` is a
  shields.io endpoint JSON (`label` "models", e.g. `6/6 up`), anonymous,
  GET/HEAD only, its own rate-limit zone, a 30 s micro-cache, and no client
  header, body or query string forwarded to UMRS
  (`/umrs/status/service-badge`). Contract and the anonymous exception:
  [ADR-106 amendment 2026-11-07](../docs/adr/106-amendment-2026-11-07-public-service-badge.md).
  The public README consumes only #796's address-free `<site>-models.json`
  publication, never this gateway's URL (D-7, 2026-11-10).

```
r4 ──HTTPS + JWT──▶ NGINX :8080 ──auth_request──▶ sidecar :8081
                       │
                       └──/gw/* → UMRS HTTP sidecar (/umrs/*) → mini-RiVault (LiteLLM → vLLM), S3, Postgres
```

The standalone A2L service (`:8002`) no longer exists — A2L is the bounded
module `umrs.a2l` (ADR-034). The Python gateway package was removed on
2026-09-11 (ADR-014).

## Layout

```
gateway/
├── nginx/           # nginx.conf, conf.d/server.conf (route table), Dockerfile, entrypoint
├── sidecar/         # main.py (FastAPI: /validate, /validate-download, /audit, /health), Dockerfile
├── tests/           # sidecar contract + security tests (30)
└── k8s/             # Kubernetes manifests
```

The R-CCS sites deploy the gateway with Ansible, which is kept in the
development repository and is not part of the public tree (there an Apache
HTTPS reverse proxy on port 443 fronts NGINX). With the public repository,
deploy with [deploy/standalone](../deploy/standalone/README.md) or your own
tooling, and build the images from `gateway/nginx/Dockerfile` and
`gateway/sidecar/Dockerfile`.

## Environment

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_PORT` | `8080` | NGINX listen port |
| `UMRS_HOST` / `UMRS_PORT` | `umrs` / `8000` | UMRS HTTP sidecar upstream (per-cluster ports on umrs-front) |
| `SIDECAR_HOST` / `SIDECAR_PORT` | `sidecar` / `8081` | Auth sidecar |
| `JWT_SECRET`, `JWT_ALGORITHM` | dev secret / `HS256` | Must be set to a real secret when `GATEWAY_ENVIRONMENT` is `staging` or `production` |
| `GATEWAY_ENVIRONMENT` | `development` | Sidecar mode |
| `GATEWAY_ALLOW_INSECURE_DEV_AUTH` | unset | Dev/test only |
| `DATABASE_URL` | — | Audit log database |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | — | Internal download validation |

## Tests

```bash
cd gateway && pip install -r sidecar/requirements.txt && pytest tests/
```

See also [ADR-003](../docs/adr/003-three-component-gateway.md),
[ADR-014](../docs/adr/014-python-gateway-retirement.md),
[GATEWAY_SIDECAR_BOUNDARY.md](../docs/architecture/boundaries/GATEWAY_SIDECAR_BOUNDARY.md).
