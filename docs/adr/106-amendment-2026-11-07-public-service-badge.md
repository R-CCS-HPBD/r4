# ADR-106 amendment (2026-11-07): a public service-model badge per site

**Status:** Accepted (2026-11-10; merged in #829 and deployed at `34be4f31`; anonymous route and address-free publication verified in ledger 264)
**Amends:** [ADR-106](106-service-model-tier.md) Consequences (a public view of D-1 role liveness); records an anonymous exception to [CONSTITUTION](../../CONSTITUTION.md) §2 ("the edge validates the JWT on every request")
**Relates to:** ADR-106 D-9 refinement (`connect-info.default_models` lists only live roles), [ADR-109](109-vision-service-and-routed-endpoints-for-steps.md) D-2 (co-served rows) and D-4 (`service.replicas: 0` is on demand), ADR-076 D-4 (the `keep_alive` walltime relay), issue #634 (`/health/backend`)

## Context

The public README shows one health badge per connected R-CCS site. It says
whether the gateway answers, not whether the site's service models
(`svc-chat`, `svc-fast`, `svc-decision`, …) are up. The detailed per-role
view (`tools/service_status.py`, the private `ops-status` branch) depends on
the single data0 runner and is not public.

A badge that shields.io can fetch must be anonymous. Two anonymous routes to
UMRS already exist: `/health/backend` (issue #634; fixed 200/503 from a probe
of UMRS `/health`) and `/gw/ops/ping` (login handshake probe). Both disclose
nothing beyond "up". The public `/gw/ops/connect-info` already discloses more
than this badge will (role names, model names, ZMQ host and ports).

The security review of 2026-11-07 found, among others:
1. `health_backend` was one 5 r/s bucket per gateway for every caller (behind
   Apache every client is 127.0.0.1), so any public caller could 429 the
   converge's "Platform serving" probe; a badge on the same zone would add a
   second target and could be flipped red by a flood.
2. A plain `proxy_pass` relays UMRS's error body (with its `request_id`).
3. Clearing only `Authorization` and `Cookie` still forwards `X-User-ID`,
   `X-Verified-*`, `X-Request-ID` and the query string.
4. The route auth tests covered `/gw/*` only.
5. "Kept up" had to include `keep_alive` roles (`svc-decision` on rccs).
6. Counting any READY endpoint of a roster model counts a user's private
   endpoint as the service being up.

## Decision

**D-1. Route.** UMRS serves `GET /umrs/status/service-badge` in its own
family `status` (`umrs.shared.lanes.FAMILY_LANES`: v1-stable, because the
public README promises its shape), not the internal `ops` lane. It is
anonymous, takes no parameters, reads no request header and makes at most
one registry read.

**D-2. What counts.**
- *Roles:* the `SERVICE_ROLES` whose `svc-<role>` alias resolves on this
  site's roster.
- *Kept up:* a roster entry with a `service` block with `replicas >= 1`, or a
  `keep_alive` block. `service.replicas: 0` or neither block is *on demand*.
- *Live:* a READY endpoint the service tier owns: a `service` replica carries
  the relay's recipe digest (`service_controller.DIGEST_KEY`); a `keep_alive`
  successor is a system-tier row of the policy's project and submitter (the
  relay lineage; system tier is admin-only to provision). A `service` replica
  started before the job-id fix (401fa3a8, 2026-11-04) has no digest; the
  service controller still treats it as its own, so it counts through the
  same relay lineage;
  ADR-109 D-2 co-served rows count through their owned primary. A user's own
  endpoint of the same model, in any project, never counts. DEGRADED is
  tracked separately from READY.

**D-3. Output contract.** UMRS answers exactly
`{"schemaVersion":1,"label":"models","message":…,"color":…,"cacheSeconds":300}`.
`message` is built from integers and fixed words only, `color` from a fixed
set:

| Site state | `message` | `color` |
|---|---|---|
| every kept-up role has a READY replica | `N/N up` | `brightgreen` |
| some kept-up roles missing, or only DEGRADED | `k/N up` | `yellow` |
| kept-up roles exist, none READY or DEGRADED | `0/N up` | `red` |
| no kept-up role, some on-demand role READY | `k up` | `brightgreen` |
| roster set, no kept-up role, nothing live | `idle` | `lightgrey` |
| no roster configured | `none configured` | `lightgrey` |
| roster readable, no `svc-*` role on it | `none configured` | `lightgrey` |
| roster set but unreadable | `unknown` | `lightgrey` |
| registry unreadable (UMRS answers HTTP 503) | `down` | `red` |
| an error after the registry read (a roster surprise) | `unknown` | `lightgrey` |

It never outputs a project, user, host, port, partition, job id, digest or
model name. A site whose roles are all on demand (bdrt today) reads `idle`,
not red, when nothing is live: an on-demand role being down is its normal
state.

**D-4. Edge.** Each gateway serves `location = /status/models.json`
(`gateway/nginx/conf.d/server.conf`), reached as
`https://<fqdn>/<site>/status/models.json` through the existing Apache
pass-through. It:
- runs no `auth_request` and serves `GET`/`HEAD` only (`limit_except`);
- forwards nothing from the client: `proxy_pass_request_headers off`,
  `proxy_pass_request_body off`, `Host $proxy_host`,
  `X-Request-ID $request_id`, and `rewrite ^ /umrs/status/service-badge? break`
  (the trailing `?` drops the query string); UMRS's `X-Request-ID` response
  header is hidden (`proxy_hide_header`);
- caps anonymous request bodies at 16 KiB (`client_max_body_size 16k`) rather
  than inheriting the server's unlimited size; a larger body becomes the fixed
  413/`unknown` answer instead of spooling to disk before proxying;
- has its own zone `status_badge_limit` (5 r/s, burst 20 per gateway), never
  `health_backend`;
- micro-caches 200s for 30 s under one fixed `proxy_cache_key`, with
  `proxy_cache_lock on` and `proxy_cache_use_stale updating` only (never
  `error` or `timeout`, so it cannot stick on green); upstream
  `Cache-Control`/`Expires`/`Set-Cookie` are ignored and hidden, and success
  carries `Cache-Control: max-age=300`;
- intercepts every upstream error (`proxy_intercept_errors on`, with an
  `error_page` for every code 300–308 and 400–599 but 499, which nginx
  refuses as its own client-closed code, pinned by
  `test_status_badge_maps_every_upstream_error`) and answers fixed JSON,
  HTTP 200 so shields renders it, `Cache-Control: no-store`: any 5xx or no
  answer → `{"schemaVersion":1,"label":"models","message":"down","color":"red"}`;
  429 (the zone) → `"rate limited"`, `lightgrey` (a flood is not an outage);
  a redirect or any other 4xx (for example a UMRS without the route) →
  `"unknown"`, `lightgrey`; 403 (another method) keeps the JSON `@error403`.

**D-5. The deploy probes keep their own bucket.** `health_backend` is keyed
on `$server_port:$health_backend_lane`: `edge` when the request carries
`X-Forwarded-For` (Apache always adds it), `direct` otherwise (the converge's
"Platform serving" probe on `127.0.0.1:<port>`). A public flood spends only
the edge bucket. A client cannot reach the direct bucket through the edge: it
cannot remove the header Apache adds, and a forged one lands it in `edge`.
The direct bucket is not private to the converge, though: anyone who reaches
the gateway port without passing Apache shares it, namely local users on the
front-end host (`rccs_gateway_listen_addr` is `127.0.0.1` once the Apache
edge is ready, all interfaces before, `front_end/tasks/umrs-front.yaml`) and,
for a login-node gateway (`--network host`) with an empty or non-loopback
`rccs_gateway_listen_addr`, anyone who can reach that address.
They can still 429 the converge probe (fail-closed, rerun); keeping them out
would need a network control, not an nginx key.

**D-6. The anonymous exception, recorded.** CONSTITUTION §2 requires the edge
to authenticate every request. Its exceptions that reach an upstream are now
pinned by `tests/test_gateway_route_characterization.py`
(`ANONYMOUS_UPSTREAM_LOCATIONS`; a route with only `auth_request
/auth/validate-optional` counts as anonymous, and the `app.d` fragments the
deploy roles include, today `r4-web.conf` from the `r4_web` role, are scanned
with `server.conf`): `/health/backend`, `/health/ready`,
`/ready`, the login flows (`/saml/`, `/auth/cli-callback`, `/auth/web-login`)
and `/status/models.json`, beside the `/gw/*` exceptions already in
`GW_AUTH_EXCEPTIONS` (`/gw/ops/ping` …). Adding one fails that test until it
is listed in a reviewed change. The badge qualifies because, like
`/health/backend` and `/gw/ops/ping`, its whole answer is a fixed shape built
from counts, and it returns less than the public `connect-info` already does.

**D-7. Address-free publication (added 2026-11-10, after #796).** The public
README does not link shields to a site's gateway. The existing `service-status`
runner reads `/<site>/status/models.json` from each site's private/public
address, validates the fixed badge shape again, and publishes
`status/<site>-models.json` on the data-only `status` branch of
`R-CCS-HPBD/r4` through #796's existing publisher. Its `STATUS.md` adds only
the fixed model message. The public overlay references those raw files, never
the gateway URL. Validation also enforces the message/colour/count semantics:
full kept-up roles are green, partial or degraded kept-up roles yellow,
on-demand counts green, and the fixed idle/unknown/rate-limited/down states
use their documented colours. Only those three fixed error answers may omit
`cacheSeconds`; a successful badge may not. Malformed or inconsistent payloads
become grey `unknown`; only transport/unreachable failures are red `down`.
Model-message changes participate in the existing
status digest, so they trigger a status write without bypassing #796's
change/heartbeat policy. No second publisher, credential, or public endpoint
is introduced.

## Consequences

- The README can show `<site> models` beside each site's health badge from
  the address-free `status/<site>-models.json` documents. The overlay edit is
  repo-side here; converge, the first live curl, and the scheduled public
  status publication remain maintainer legs.
- Freshness: badge truth is the registry status. If the health monitor stops,
  READY rows stay READY; the 30 s micro-cache plus shields' 300 s cache can
  hide a new outage for about 5–10 minutes. Both are accepted.
- Residual: the canary (`scripts/e2e-canary.sh`) reaches `/health/backend`
  through the public edge, so a sustained public flood can still 429 it and
  fail a stage gate (fail-closed, rerun). The converge's own verdict no longer
  can (D-5).
- No new credential, job store, scheduler or storage-credential path
  (CONSTITUTION §1.3).

**Open question Q-1 (maintainer).** "Expected" means kept up (D-2). The
alternative, every roster alias, would show rccs `6/7` yellow while the
on-demand `svc-chat` is not running.

## Rollout-status (2026-11-07)

Repo-side on `wave/service-model-badge`, not merged, not deployed: the UMRS
route and tests (`umrs/src/umrs/api/status_badge.py`,
`umrs/src/umrs/orchestrator/service_badge.py`, `umrs/tests/test_status_badge.py`),
the gateway location, zones and cache (`gateway/nginx/nginx.conf`,
`gateway/nginx/conf.d/server.conf`), tests
(`gateway/tests/test_nginx_config.py`,
`gateway/tests/test_nginx_audit_runtime.py` under podman,
`tests/test_gateway_route_characterization.py`), and the route inventory row
(`tools/route_inventory.base.json`). Deploy needs a converge (UMRS and the
gateway image both change); then curl `/<site>/status/models.json` on every
site before the README overlay changes.

## Rollout-status (2026-11-10)

The earlier rollout paragraph is superseded on publication only: #796's
address-free publisher now carries the model badge too (D-7), so shields no
longer needs the gateway address in the public overlay. The implementation merged
in #829 at `e7cc6d45` and deployed at `34be4f31` on all fronts; every staged
apply and strict canary passed (42762870001/42764935142/42766695394/42770053708):
`tools/service_status.py` strictly normalizes the gateway answer and writes
`<site>-models.json`, the public overlay consumes those files, and the public
status page includes the fixed model message. Fail-first publisher tests
(12 failed / 23 passed on the old code) now pass 46/46; the inherited
UMRS/gateway/lane set passed 85/85 and the combined focused set, including the
R4 bridge environment check, passed 234 / 1 skipped. Independent-review body,
type, semantic and malformed-JSON regressions all pass, and real nginx runtime
passed 14/14. Full-gate and live-verification evidence is tracked in wave ledger
(258) and deployment/publication receipt (264). Anonymous post-apply run
42758708289 verified rccs `6/6 up`, bdrt `idle`, ai4s/octopus
`none configured`. The public README's model badges use only the address-free
`raw.githubusercontent.com/R-CCS-HPBD/r4/status` documents; no per-site
gateway address is exposed.
