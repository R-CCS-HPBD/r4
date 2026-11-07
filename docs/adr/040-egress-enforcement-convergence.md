# ADR-040: Egress enforcement convergence — fail-closed allowlists and the EgressGuard venue reality

**Status:** Proposed (with implementation in WP-01 round 1, same PR; venue model decision open)
- **Date:** 2026-09-06
- **Related:** ADR-006 (offline inference + egress model), ADR-015 (knowledge
  ingest is the only non-model job class with egress), ADR-014 (Python gateway
  retirement), ADR-035 (enforcement-point matrix), CONSTITUTION invariant
  "never add network/egress access to inference containers".

## Context

The platform names one egress control, **EgressGuard**, and relies on it in
three places: the mini-RiVault compose stack (`egress-guard` service under the
`egress` profile), the Kubernetes NetworkPolicy (egress allowed only to DNS,
an `egress-guard` pod, and optional site CIDRs), and the per-job proxy
variables (`HTTP(S)_PROXY=http://egress-guard:8888` when the profile is
active). Phase 1 could not verify venue coverage read-only (G-P5-1). WP-01
step 0 was ratified as a read-only existence check before any venue decision.

### Step-0 result (read-only, 2026-09-06, verbatim)

```text
$ gh repo view riken-rccs/egress-guard --json name,visibility
GraphQL: Could not resolve to a Repository with the name 'riken-rccs/egress-guard'. (repository)
$ gh api /orgs/riken-rccs -i | head -1
HTTP/2.0 200 OK
$ gh api "/orgs/riken-rccs/packages?package_type=container" --jq '.[].name'
(empty)            # member token, scopes include read:org, repo, write:packages
$ curl -s "https://ghcr.io/token?scope=repository:riken-rccs/egress-guard:pull"
(no token issued)  # anonymous tokens are issued for public packages only
$ curl -sI -H "Authorization: Bearer <none>" https://ghcr.io/v2/riken-rccs/egress-guard/manifests/latest
HTTP 403 {"errors":[{"code":"DENIED","message":"invalid token"}]}
$ grep -rn "egress-guard" umrs/deploy deploy   # in-repo Dockerfile/def/source?
(only env.example comments)
```

Reading: the GitHub organisation exists; no repository and no container
package named `egress-guard` is visible to an organisation member whose token
carries package scopes; the image is not publicly pullable; the repository
contains no source for it. The compose service
`image: ghcr.io/riken-rccs/egress-guard:${EGRESS_GUARD_DOCKER_TAG:-latest}`
therefore cannot be pulled from any registry reachable from this account.
Whether a Slurm node holds a pre-cached copy is **not verifiable read-only**.

### Venue matrix (verified in code)

| Venue | What asks for the proxy | What runs |
|---|---|---|
| Slurm / compose (mini-RiVault) | provisioning adds the `egress` profile when a request carries `egress_domains` or a custom MCP mount (`endpoint_mounts.py:54,67`) and sets `EGRESS_DOMAIN_ALLOWLIST`; `serve-model.slurm:418` points every container's proxy variables at `egress-guard:8888` | the `egress-guard` service from an unresolvable image; the stack also runs an in-stack nginx forward proxy (`nginx_fs/out-nginx.conf`) that allows only the rendered S3 bucket host |
| Kubernetes | `kubernetes_backend.py:100-106` sets the proxy variables; NetworkPolicy allows DNS + an `egress-guard` pod + optional CIDRs | no `egress-guard` Deployment/Service manifest exists in the repository |
| Compose e2e | `docker-compose.e2e.yaml:98-99,434-439` sets `HTTP_PROXY=http://gateway:8080` on the compute mock and custom MCP | the Python gateway is a JSON API, not a forward proxy (no CONNECT/absolute-URI handler); direct egress from those containers fails by accident |
| Production NGINX | `/gw/egress/*` is rewritten to `/umrs/egress/*` (`server.conf:921`) | UMRS serves only `/umrs/egress/audit*`; `POST /gw/egress/proxy` is unreachable in production and has **no in-repo client** (only `/gw/egress/audit` is called, from `r4/_cli/agent.py:964`) |

### Three allowlists, two default semantics

| Allowlist | Empty means | Source |
|---|---|---|
| `knowledge_ingest.allowed_domains` (`egress_fetch.py`) | deny all (fail-closed) | ADR-015 FR-3 |
| `EGRESS_DOMAIN_ALLOWLIST` for the EgressGuard container | "fully offline" per the Slurm wrapper comment; unverifiable (image absent) | `serve-model.slurm:577` |
| `EGRESS_DOMAIN_ALLOWLIST` for the gateway proxy route | **allow every non-private domain** (was fail-open: `if allowlist and domain not in allowlist`) | `gateway/gateway/routes/egress.py` before WP-01 |

### Offline assertion is duplicated and blind

`assert-offline.sh` and the inline `_assert_offline_invariants` in
`serve-model.slurm:145-165` are two copies of the same environment-variable
check (the inline one already dropped the `no_proxy` check). Neither observes
the network; both were executed only by the Slurm wrapper, never by a test.
The HPC harness already carries a direct-connect probe
(`tests/run_hpc_tests.sh --slurm-egress-smoke`) that expects public egress to
be blocked.

## Decision

1. **Every domain allowlist in the platform is fail-closed when empty.** One
   explicit, logged opt-in exists per surface. For the gateway route it is
   `EGRESS_ALLOW_ALL_DOMAINS=true`; a non-empty allowlist always wins over the
   opt-in; every request allowed under the opt-in logs a warning; every
   denial is audited with the reason
   `No egress domain allowlist configured (fail-closed)`. Landed in WP-01
   round 1, pinned by `TestFailClosedAllowlist`.
2. **The gateway proxy route is kept fail-closed and scheduled for
   retirement.** It is production-unreachable and clientless. It leaves with
   the Python gateway under ADR-014 (WP-13) unless a client appears first;
   until then it stays as the compose-profile egress path and gets an
   enforcement-matrix row (ADR-035 has none for it today).
3. **Offline containers observe the network.** `assert-offline.sh` gains a
   direct-connect probe (`OFFLINE_PROBE=1`, `OFFLINE_PROBE_HOST/PORT/TIMEOUT_SEC`)
   that fails the launch when a direct connect succeeds. It is executed by
   unit tests on both outcomes (WP-01 round 1). The default flips to on, and
   the inline Slurm copy is replaced by a call to the script, in WP-01 round 2
   after `--slurm-egress-smoke` has run on each venue.
4. **Provisioning fails closed when the proxy cannot exist.** A request that
   needs the `egress` profile on a venue without a pinned, resolvable
   egress-guard image must be refused at provision time with a named error,
   instead of launching a stack whose proxy is absent. Round 2, after the
   venue decision below.
5. **Venue model: open, maintainer decision (brief §2.5 stop).**
   - **A. Build EgressGuard in-repo**: a small allowlisting forward proxy
     with audit, pinned by digest, deployed on every venue including a
     Kubernetes Deployment. Largest scope; restores the design as drawn.
   - **B. Ratify "offline env + site firewall" (P5(d))**: drop the external
     proxy, turn the `egress` profile into a hard refusal, and confine
     domain allowlists to the two job classes that legally egress (model
     download, `KNOWLEDGE_INGEST` via `egress_fetch`). Smallest scope; MCP
     servers inside mini-RiVault lose any sanctioned egress.
   - **C. Extend the in-stack nginx forward proxy** (`out-nginx.conf`,
     already running and already host-allowlisting the S3 bucket) with the
     rendered `EGRESS_DOMAIN_ALLOWLIST` and an access log shipped to the
     egress audit sink. Reuses a component that exists today; cannot do body
     scanning or per-tool rate limits.
   - **Recommendation:** C for the Slurm/compose venue plus B's refusal
     semantics everywhere the proxy is absent, and A only if MCP egress needs
     body scanning that the nginx map cannot express.
6. **Severity:** G-P5-1 returns to S1 under the ratified condition (the
   Slurm venue asks for the proxy and never gets a working one).

## Consequences

- Behavior change on `POST /gw/egress/proxy` (named in the WP-01 PR, pinned
  by tests). No repository compose file or deploy inventory sets
  `EGRESS_DOMAIN_ALLOWLIST` for the gateway, so no known deployment relied on
  the fail-open default.
- `deploy/*/env.example` now names the profile `egress` (it was documented as
  `egress-guard`, which no compose file defines).
- Remaining for WP-01 round 2 after the venue decision: items 3 (default on,
  inline dedup, HPC leg), 4 (provision-time refusal), the digest pin or
  removal of the image reference, the Kubernetes proxy manifest or its
  removal, and the enforcement-matrix row.

## Addendum (2026-09-06, WP-01 round 2) — D5 decided, D4 implemented

- **D5 decided.** The maintainer accepted the recommendation (C for the
  Slurm/compose venue plus B's refusal semantics). Verified before
  implementing: the in-stack forward proxy (`nginx_fs/out-nginx.conf`) only
  does `proxy_pass http://$host`; stock `docker.io/nginx` has no CONNECT
  support (the file's "forward proxy for CONNECT request" comment sits above
  `proxy_connect_timeout`, which is the upstream connect timeout, not the
  CONNECT method). HTTPS egress cannot tunnel through it, so C as worded
  cannot serve MCP egress. C's intent — no external image, reuse what the
  stack already ships — is kept by implementing the proxy as
  `r4_mcp.egress_guard` inside the platform's own `r4-mcp` image, which the
  `mcp-custom` service already references. It is CONNECT-capable, allowlists
  by exact host, fails closed on an empty allowlist, denies private
  destinations, and writes one JSON audit line per decision. It never
  terminates TLS, so there is no body scanning; option A stays available if
  that is ever required.
- **D4 implemented in three layers.** API: `POST /umrs/endpoints/provision`
  answers 400 with the text `egress requested (<why>) but this site declares
  no egress proxy image (site.yaml network.egress_proxy_image /
  EGRESS_GUARD_IMAGE); refusing per ADR-040 D4` when `egress_domains` or a
  custom MCP mount is requested and no image is declared. Orchestrator:
  `build_mount_environment` raises the same `ValueError` on the durable
  path. Slurm wrapper: when the `egress` profile is active and the image is
  neither present (`podman image exists`) nor pullable, the job exits 1 with
  a `[rivault] FATAL ... (ADR-040 D4); refusing to launch` line. A site
  declares the proxy with `EGRESS_GUARD_IMAGE` or `site.yaml
  network.egress_proxy_image`; the value travels with the job env.
- **Compose wiring.** The `egress-guard` service now uses
  `${EGRESS_GUARD_IMAGE:-localhost/r4-mcp:latest}` with
  `python -m r4_mcp.egress_guard --port ${EGRESS_PROXY_PORT:-8888}`; the
  `ghcr.io/riken-rccs/egress-guard` reference and `EGRESS_GUARD_DOCKER_TAG`
  are gone.
- **Still open after round 2.** The HPC leg: one job with `egress_domains`
  on a Slurm venue that declares `EGRESS_GUARD_IMAGE`, confirming the proxy
  answers `/healthz` and tunnels a CONNECT to an allowlisted host, then D3's
  probe default-on and the inline Slurm dedup. A Kubernetes proxy manifest
  (today the NetworkPolicy denies egress there because no `egress-guard` pod
  exists: fail-closed, no sanctioned egress). `docker-compose.e2e.yaml`
  still points containers at the gateway as if it were a forward proxy
  (switch it to an `egress-guard` service built from `mcp-servers/`). The
  enforcement-matrix row and route retirement (WP-13). Digest pinning of the
  `r4-mcp` image where a site pulls it from a registry.
- **Severity.** G-P5-1 stays S1 until the HPC leg shows the proxy answering
  on a real job; the code no longer asks for an image that cannot exist.

## Addendum (2026-09-07, WP-01 round 3 — HPC leg, rccs venue)

- **Cluster access granted.** Client onboarded to `rccs-cloud` (direct mode,
  ZMQ session, 365-day token) on the Slurm venue.
- **Declared (repo):** `umrs/deploy/rccs-front/site.yaml` (the file the
  umrs-front rccs server container binds as `/etc/umrs/site.yaml`) **and**
  `umrs/deploy/rccs/site.yaml` now carry
  `network.egress_proxy_image: localhost/r4-mcp:latest` — the in-repo proxy
  entrypoint of rounds 1-2, never the unreachable external reference
  (pinned by `umrs/tests/test_egress_site_declarations.py`). Every OTHER
  venue (ai4s, bdrt, octopus, their fronts) keeps default-deny.
- **Gate-cycle footnote:** the declaration was first written to
  `deploy/rccs/site.yaml` only; the deployed server's container mounts its
  OWN front file (`umrs/deploy/rccs-front/site.yaml`), which was proven
  inside the live container before declaring there (distinguishing file
  roles: deploy/ tree = local-cluster deployment, deploy/umrs-front
  tree = front-hosted clusters).
- **Matrix row:** `tools/enforcement_matrix.yaml` endpoints row notes the
  refuse-before-launch chain (provision-time 400 unless the site declares).
- **Kubernetes explicit refusal.** No venue in this engagement runs
  Kubernetes on its egress path (Slurm+podman on rccs, Slurm+apptainer on
  ai4s). The K8s manifest therefore does not exist by decision — any future
  K8s venue with `egress_domains` provisions maps the same D4 rule: no
  provision without a declared proxy workload. `egress-guard` pods, when
  added, must follow the same declared-image contract.
- **Round-3 live sequence (this addendum's evidence):** (1) provision with
  `egress_domains` → 400 when the live site has not yet loaded the declared
  image (pre-converge refuse proof); (2) after the site's image declaration
  is live, provision one egress endpoint and probe `/healthz` + CONNECT to
  an allowlisted host inside the venue; (3) inline Slurm dedup verification
  (single guard pod per job).
- **Digest pinning stays open** — `localhost/r4-mcp:latest` moves; where a
  site pulls from a registry, digest pinning is still a follow-up (sites
  that build locally are pinned by the builder state, which is why the
  round-3 venue's build/e2e story is named).
- **Round-3b venue fix (2026-09-07, ADR-045).** The HPC leg's three attempts
  died before any guard activity in `podman network create`: three dead jobs'
  CNI configs in the shared `$HOME` occupied the wrapper's three fixed
  subnets, and the declared image could not exist on any node (node-local
  stores, no registry, never built). ADR-045 replaces the fixed candidates
  with a pool + garbage collection, moves podman configuration off the shared
  HOME, and stages `localhost/r4-mcp:latest` as a shared-filesystem archive
  that the wrapper loads (`rivault_ensure_image`) — the D4 refusal is now the
  last step of present → archive → pull. The round-3 live sequence above runs
  unchanged after the converge.
