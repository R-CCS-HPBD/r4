# ADR-102: A local SearXNG for `web_search`, started by `r4 search-server`

**Status:** Accepted (2026-10-30, maintainer request "could we also include local SearXNG server deploy if necessary in R4?"; open choices decided by the agent under the 2026-09-24 delegation; wave ledger (112), `wave/web-tools-public-mode`)
**Date:** 2026-10-30
**Relates to:** ADR-099 D-9 (local enforcement of the web choice), wave ledger (111) (web tools in public mode), CONSTITUTION §1.3 (ownership), ADR-090 (stand-alone stack)

## Context

Until wave (111), `web_search` had no backend anywhere. Wave (111) added a
SearXNG client (`r4/src/r4/agent/web_search.py`, `R4_WEB_SEARCH_URL`) and
kept the tool unoffered without one. No SearXNG instance exists on the fronts
or in the stand-alone stack (verified 2026-10-30: no `searx` in `deploy/`).
So a researcher who chose Public web access still had no `web_search` unless
they ran and configured a search server themselves.

A keyless default was rejected in wave (111). DuckDuckGo's HTML page answers
automated clients with a challenge, and working around it is bot-check
circumvention. SearXNG is the standard self-hosted metasearch engine. It has a
JSON API meant for programs, and a one-user instance is a single stateless
container. On 2026-10-30 the pinned image (below) ran rootless under podman 5
on Fedora 44 with all capabilities dropped, `no-new-privileges` and a
read-only config mount, and answered `web_search` with real results.

The maintainer pointed at `ihor-sokoliuk/mcp-searxng`. It does not deploy
SearXNG: it is an MCP wrapper (Node 22, MIT) around an instance named by
`SEARXNG_URL`. It also ships "browser solver" integrations (FlareSolverr,
Byparr) for getting past bot challenges.

No R4 Python code started a container before this ADR. Containers were driven
only by `deploy/standalone/standalone.sh`. The closest Python precedent is
`companion/mcp_exposure.ensure_proxy_running`, which starts a local helper
process, probes it and returns a dict with a reason.

## Decision

- **D-1.** `r4 search-server up|down|status` (`r4/src/r4/_cli/search_server.py`,
  logic in `r4/src/r4/agent/searxng_local.py`) runs one SearXNG container for
  the user:
  - name `r4-searxng`, label `r4.managed=searxng`
  - published on `127.0.0.1` only (`R4_SEARXNG_PORT`, default 8888)
  - every capability dropped, `no-new-privileges`, config mounted read-only
  - the engine is podman, else docker (`R4_CONTAINER_ENGINE` overrides)
  - the image is pinned (`R4_SEARXNG_IMAGE` overrides) and pulled only when missing
  - `up` is a no-op while the instance answers `/healthz`; `down` removes the
    container and keeps the settings
- **D-2.** R4 writes `<config dir>/searxng/settings.yml` (directory 0700, file
  0600) only when it is missing, so the user's edits stay. It sets:
  - JSON format on (`web_search` needs it)
  - limiter and image proxy off (one user, loopback)
  - a fresh per-user `secret_key`

  `up` records the URL in `instance.json` next to it.
- **D-3.** `web_search` uses `R4_WEB_SEARCH_URL` when set, else the recorded
  local instance while it answers. The probe is cached for 30 s. With neither,
  the availability gate does not offer the tool and its reason names both
  ways. The ADR-099 guard is unchanged: queries leave the host only under
  Public.
- **D-4.** Choosing Public (`session.web_access_set`) with no backend answers
  with an optional `notice`, which the console shows as a system line: run
  `r4 search-server up` or set `R4_WEB_SEARCH_URL`. R4 never starts the
  container by itself. Pulling about 200 MB mid-turn and running a container
  the user did not ask for are both surprises.
- **D-5.** R4 does not adopt `mcp-searxng`. The built-in client already covers
  its search tool, and it runs behind the session's web-access guard. MCP
  tools are not filtered locally (ADR-099 D-9), so the wrapper would bypass
  the user's choice. The wrapper's challenge-solver integrations are out of
  bounds for the same reason as the DuckDuckGo scraper.

### Why

- **Ownership allows a client-side helper.** CONSTITUTION §1.3 forbids R4 a
  scheduler, a job store, a bucket or database, or a storage credential. A
  stateless search helper on loopback is none of these. The `scheduling on …
  Podman` UMRS owns is job scheduling, not a user's local helper.
- **A client command reaches everyone.** Most users connect to a cluster front
  and never run the stand-alone stack, so a compose profile alone would reach
  only stand-alone users.
- **Explicit start, visible state.** A user command, an `instance.json` the
  user can delete, and a notice instead of auto-start keep the container the
  user's own decision.

## Consequences

- Positive: Public sessions get a working `web_search` after one command, with
  no account, key or third-party scraping. The search traffic is SearXNG's
  own, to its default engines.
- Cost: R4 now drives a container engine (one module, `_run` with a fixed argv,
  no shell). A new image pin has to be refreshed: monthly, with the ADR-100
  dependency refresh, by bumping `DEFAULT_IMAGE` to a tag at least 7 days old.
- NOT in scope:
  - **Remote sessions.** The worker on umrs-front cannot reach the user's
    loopback instance. A front-side instance, which squid would have to allow,
    is a separate deploy decision.
  - **The stand-alone compose profile.**
  - **A `doctor` row.**

## Alternatives Considered

- **Keyless DuckDuckGo HTML scraping.** Rejected in wave (111): bot-check
  circumvention.
- **Adopt `mcp-searxng`.** Rejected (D-5): it still needs a SearXNG instance,
  and it bypasses the web-access guard.
- **Auto-start on the first `web_search`, or when Public is chosen.** Rejected
  (D-4): an image pull mid-turn, and a container the user did not ask for.
- **Only a `web` profile in `deploy/standalone/compose.yaml`.** It does not
  reach users of a cluster front. It is still a fit as a follow-up for
  stand-alone sites.
- **A hosted search API (Brave, Tavily, Exa).** Each needs a per-user key and
  sends every query to a vendor. It remains possible through
  `set_web_search_callback`.

## Amendment 1 (2026-11-01): a guided start from the console

The maintainer asked for `r4 search-server up` to be reachable from the TUI
"in a more intuitive/guided way". D-4 is amended; R4 still never starts the
container without the user's answer.

- **D-4a.** When choosing Public leaves `web_search` without a backend and a
  container engine is installed, `session.web_access_set` adds
  `search_server_offer: true`. In a local session the console then asks:
  "web_search needs a search server … Start a local one now?" Enter starts
  it; Esc leaves it and says `/search-server up` starts it any time. A remote
  session, or a machine without podman or docker, gets the D-4 notice only.
- **D-4b.** `/search-server [status|up|down]` in the console, over three
  bridge verbs:
  - `search_server.status`: READ.
  - `search_server.up`: CONTROL. It only sets a flag and starts its own
    thread, so the offer after a first-submit `/web` answer is not parked
    behind the turn that answer releases.
  - `search_server.down`: MUTATE.

  The start reports each step (download on the first start, container start,
  wait for `/healthz`) and its end as `search_server_event` lines, which the
  console posts on the tab that asked, ending with "ready at …" or the reason
  and hint. `searxng_local.up()` takes an optional progress callback for this.

## Rollout-status (2026-10-30)

- **D-1…D-4 landed** on `wave/web-tools-public-mode`, not merged.
  - Tests: `r4/tests/test_searxng_local.py`;
    `r4/tests/test_web_access_surfaces.py` (notice);
    `packages/r4-tui-v3/tests/web-access.test.tsx` (notice line).
  - Contract: `contracts/bridge/v1/session.web_access_set.schema.json` gains
    the optional `notice`.
- **Live leg (workstation, podman 5.8.9 rootless):** `r4 search-server up`
  → `started at http://127.0.0.1:8888`. A public-mode companion `web_search`
  with `R4_WEB_SEARCH_URL` unset returned SearXNG results; allowlist and off
  were refused by the guard. `down` removed the container.
- **Known environment issue:** a stale `docker.io` login in podman's
  `auth.json` makes even public pulls fail. `up` reports it with a
  `podman logout docker.io` hint and never edits the credentials.

## Rollout-status (2026-11-01, Amendment 1)

- **D-4a and D-4b landed** on `wave/search-server-tui`, not merged.
  - Bridge: `r4/src/r4/companion/ui/search_server_bridge.py`.
  - Console: `src/commands/search-server-command.ts` and
    `src/components/search-server-offer-overlay.tsx`.
  - Contracts: `contracts/bridge/v1/search_server.{status,up,down}.schema.json`;
    `session.web_access_set` gains the optional `search_server_offer`.
- **Tests:**
  - `r4/tests/test_search_server_bridge.py`
  - `r4/tests/test_web_access_surfaces.py` (the offer)
  - `r4/tests/test_searxng_local.py` (progress)
  - `r4/tests/test_bridge_rpc_contract.py` (four cases)
  - `packages/r4-tui-v3/tests/search-server.test.tsx` (12)

## Rollout-status (2026-11-01, engine check)

- **Live report.** The maintainer's console: `/web` → Public → start ended on
  "Cannot connect to Podman …", cut off, after a "downloading …" line.
  - Cause: podman could not reach its machine or service. `image inspect`
    failed on that dead connection, which `up` read as "image missing".
  - Reproduced on the workstation with `CONTAINER_HOST` pointing at a socket
    nobody serves: `podman info` and `podman image inspect` both exit 125
    with that message.
- **Fix** (`wave/search-server-engine-check`):
  - `up` first runs `<engine> info` (`engine_unready`) and stops there with a
    short reason and the next step:
    - podman on macOS/Windows: `podman machine start` (first time:
      `podman machine init`)
    - podman on Linux: `podman system connection list`
    - docker: "start Docker Desktop"
  - Errors are explained once (`_explain`), and the registry-login hint now
    appears only for auth errors.
  - The console joins reason and hint as "…; <next step>".
- **Live:** `r4 search-server up` against the dead socket prints that reason
  and hint and runs nothing else.
- **Tests:** `r4/tests/test_searxng_local.py`, 5 new (4 fail on the old code);
  `packages/r4-tui-v3/tests/search-server.test.tsx`.

## Related

- [ADR-099](099-session-policy-and-quotas.md): the web-access choice and its local guard
- [ADR-100](100-dependency-locks-cooldown-refresh.md): the refresh cadence the image pin follows
- [ADR-090](090-standalone-single-host-deployment.md): the stand-alone stack (possible `web` profile)
- `.github/instructions/companion.instructions.md`
