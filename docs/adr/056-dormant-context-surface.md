# ADR-056: Keep `/umrs/context` dormant as the ONE named unfinished capability

**Status:** Accepted
- **Date:** 2026-09-15
- **Related:** [ADR-053](053-umrs-import-discipline.md) (router retirement wave), production release plan esc #3 (internal planning record, §11)

## Context

`umrs/src/umrs/api/context.py` is the last surviving **unmounted** router
after the ADR-053 wave deleted the twelve baseline dormant routers. Its
corresponding NGINX location (`/gw/context`) never existed in git history
(release-plan esc #3), and its cousin modules — `checkpoints.py`,
`namespaces.py`, `workflow_fork.py`, `workflow_outcomes.py` — were retired as
structural dead surface in the same wave.

The one route it carries that is **actually used** is `POST /umrs/context/pack`
(the `pack_context` invocation behind `r4` bundles and the companion's
context plans), an implementation path clients currently reach by wrapping the
module, not by calling this router. Nothing else in the tree depends on the
router surface; nothing plans to consume it in-advance.

## Decision

**Keep `api/context.py` unmounted as the sanctioned, single named dormant
family.** Specifically:

1. The file STAYS in the tree (unlike its retired twelve siblings) because
   `pack_context` is live code exercised for every knowledge bundle both
   through the shipped clients and by the local training/catalogiers.
2. The router stays UNMOUNTED — `http_sidecar.py` must not include it and
   NGINX must not gain a `/gw/context` location — until a *product* wave
   decides the surface deserves the lane-name (`v1beta` + `auth_request` +
   a route test).
3. `DORMANT_FAMILIES == {"context"}` is pinned in `test_surface_characterization.py`;
   any new dormant name appearing in that set is a deliberate, review-visible
   decision (hard-fail if it slips in by accident).

Any later mount executes a fresh ADR describing the capability it ships
(likely a `chat/context-pack` v1beta lane with rate-limit surface), not a
silent `include_router`.

## Consequences

- ADR-053's retirement table remains the disposition of exactly twelve
  modules; the thirteenth is **this and only this one**, with its reason
  recorded above.
- `test_surface_characterization` guards the parking slot: `{"context"}`
  may only shrink (never sprout siblings) without a deliberate mutation in
  the same commit as a rationale.
- No live user-impact: `pack_context` keeps flowing through exactly the same
  code path it already executes today (`umrs.api.context.pack_context`,
  called from the knowledge builds), independently of whether the router
  ever mounts.

## Alternatives considered

- **Now mount it and ship it** — rejected (same reason as the twelve: no
  product evidence; a mount is a lane-owned surface).
- **Also delete it** — rejected because `pack_context` is live code here,
  and the batch3 pins + brandings actually reference it; its surface is
  unfinished, not dead.
