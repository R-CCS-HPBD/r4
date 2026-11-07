# ADR-062 amendment (2026-10-15): institutional providers, local models, and who sets the class

**Status:** Accepted (2026-10-15, maintainer decision D-2 of the onboarding plan)
**Amends:** [ADR-062](062-model-locality-data-class.md) Decision 1 and Consequences
**Relates to:** ADR-046 (public-kind sources), ADR-051 (durable projects), ADR-090 D-3 (stand-alone model backends), gap rows D-51, D-37

## Context

ADR-062 sorts inference into two classes. "Self-deployed inside the institution" is allowed for `sensitive` projects; "public" is allowed only for `non_sensitive` ones. Three cases did not fit.

- **Rikyu.** RIKEN's AI4S inference service (`api.rikyu.r-ccs.riken.jp`) is run by the institution but not by UMRS. Under ADR-062 every user-pushable provider kind is public (`umrs/src/umrs/shared/project_data_class.py`), so a sensitive project could not use the institution's own frontier models (kimi-k3, glm-5.3).
- **Local models.** A model served on the scientist's own machine, such as ollama on loopback beside the stand-alone stack, sends nothing off the machine. As an r4 `public` source, however, it would be refused for a sensitive project.
- **Who sets the class.** A stand-alone deployment has one human, who is both owner and admin. The site default `sensitive`, plus a widening step with no r4 command, put a hidden refusal in a single-user setup.

The workstation's gate also never fired (it read the wrong field; see the onboarding plan §1, S2), so these cases went unnoticed.

## Decision

1. **A third provider class, `institutional`.** It covers an inference service operated by the user's own institution outside UMRS. Rikyu is the first member.
   - A `sensitive` project may use institutional providers.
   - Commercial and other public providers remain `non_sensitive` only.
2. **Membership comes from an allowlist the site keeps, never from a user's label.**
   - A source counts as institutional only when its URL host is on that allowlist.
   - r4 ships a packaged default (`api.rikyu.r-ccs.riken.jp`). A UMRS site can replace it through `connect-info` and `UMRS_INSTITUTIONAL_PROVIDER_HOSTS`.
   - A user cannot label OpenAI or Anthropic as institutional, and a source added with an arbitrary URL is public.
3. **Loopback models are local.**
   - A model served on the same machine as the client counts as self-deployed: an http(s) URL whose host is `localhost`, `127.0.0.0/8` or `::1`, or the stand-alone stack's own registry rows.
   - It is allowed for `sensitive` projects. It never counts as local for a *remote* gateway's project, because a remote site cannot see the user's machine (ADR-046).
   - *Clarified 2026-10-16 (wave-2 review; agent decision under the maintainer's 2026-09-24 delegation).* The remote-gateway clause binds the **server** gates: the venue-start gate and the `umrs-infer` lease gate never treat a loopback host as local, because the loopback they would see is the site's, not the user's. The **workstation** gate admits a loopback model for any project, a remote gateway's included.
     - **Reason:** the workstation is the machine the data is already on, so a request to its own loopback sends nothing off it. Refusing it would block the ollama-beside-the-workstation case for every site project without protecting any data.
     - **Rejected:** refusing loopback at the workstation for a remote gateway's project (the literal reading of the clause, which the wave-2 review reported as a deviation). The data is already on the user's machine, and a local relay or tunnel to a public API is the owner's deliberate act on their own machine, not something the platform does to them.
     - **Limit:** a host check cannot see what a loopback listener forwards to, so a local relay or an `ssh -L` tunnel to a commercial API also classifies `local`. The user manual tells users not to point a `sensitive` project at one.
     - **Fail closed:** a URL that carries a user name or password (`user:pass@host`), a non-http(s) scheme or a host that is not one plain name is public at every gate, and a source whose provider picks its own endpoint (Bedrock) is public at the workstation gate whatever URL it was registered with.
4. **Stand-alone projects default to `non_sensitive`.** The quickstart says so once, and the owner may narrow the project to `sensitive`. Site deployments keep `sensitive` as their default.
5. **Owners set the class from r4.**
   - The command is `r4 project data-class get|set <project> <class>`.
   - Widening needs a typed confirmation that names the project and the classes the change allows.
   - It is attributable to the owner: the server already restricts the change to owner or admin and records it.
   - Narrowing needs no confirmation.
   - *Clarified 2026-10-16 (wave-2 review; agent decision under the maintainer's 2026-09-24 delegation).* "Records it" was not true when this amendment was accepted: the server enforced owner-or-admin and then updated only `data_class` and `updated_at`, and the gateway's access log holds the method and path but not the class. The server now emits the audit event `project.data_class_changed` on every successful change, narrowing and no-op included. It records the actor, the project, `from_class`, `to_class`, `widening`, `owner_id` and `by_admin`, and goes to the `umrs.audit` log, the in-memory audit buffer and, where the HTTP sidecar has its database pool, the persistent audit store. A refused request (403 or 400) emits nothing.
     - **Reason:** ADR-062 makes widening "an explicit per-project act attributable to its owner", and an admin may also widen another owner's project; only a server-side record shows who acted.
     - **Rejected:** dropping the "records it" claim from this decision and from the r4 confirmation text. That would have made the text true by leaving an admin's widening unattributable.
6. **Enforcement points stay as ADR-062 set them,** with the third class added in each:
   - the workstation gate (r4 client, model routing);
   - the venue-start gate;
   - the `umrs-infer` lease gate.

## Consequences

- r4: sources gain a derived `locality` (`local` | `institutional` | `public`). It is computed from the URL against the allowlist and never stored as user input.
- The client gate refuses only `public` for sensitive projects. The wizard (`r4 setup model`) explains a refusal and offers `r4 project data-class set` to the owner.
- UMRS: `project_data_class.py` gains the institutional host allowlist. The venue and lease gates admit institutional sources for sensitive projects.
- Pushing a source to a venue (ADR-046) still goes through the proxy; this amendment does not let keys reach sandboxes.
- Open, unchanged by this amendment: gap D-37 still records that workstation sessions call public providers directly with the user's own key, not through the front-end proxy. Whether to keep that for `non_sensitive` projects is a separate decision.
- Gap row D-51 advances when the corrected workstation gate, the third class and the `data-class` command land together.

## Rollout-status (2026-10-16)

Accepted (unchanged). Implemented on branch `feat/easy-onboarding`, draft PR #465 in the development repository. *Updated 2026-10-16 (evening):* **merged into `main` as `d5334944` and deployed the same day to rccs, ai4s and bdrt by converge run 38897348299; the octopus front's gateway did not start (STATE OPS-20261016-001)** (public `/health` 200 on the three). Gap row D-51 records the same state.

- **Wave 2, 2026-10-16** (umrs `ce78dd2b`, parent `afe67126`; wave ledger 2026-10-16 (22)):
  - Decisions 1 and 2: the allowlist `UMRS_INSTITUTIONAL_PROVIDER_HOSTS` (packaged default `api.rikyu.r-ccs.riken.jp`) at the venue-start and `umrs-infer` lease gates; `connect-info` advertises it, and `r4 login` stores it on the deployment's platform source.
  - Decision 3: the workstation derives `local | institutional | public` from the URL (`r4/src/r4/source_locality.py`); the workstation gate itself first had to be repaired (it read the wrong field; onboarding plan §1 S2).
  - Decision 4: the stand-alone compose sets `UMRS_PROJECT_DEFAULT_DATA_CLASS=non_sensitive`; site deployments leave it unset (`sensitive`).
  - Decision 5: `r4 project data-class get|set` with the typed widening confirmation, on the existing owner-or-admin server verb.
- **Review fix wave, 2026-10-16** (same branch; wave ledger (23)). An adversarial review of wave 2 found these defects, now fixed with fail-before tests:
  - Decision 2, server: the `umrs-infer` container, which runs the lease gate, never received the site's list, so a site that changed it would have had its gates disagree. On umrs-front the deploy role now writes the one `.env` line from the inventory variable `umrs_front_institutional_provider_hosts` and passes that line alone to `umrs-infer` (never the whole `.env`, which holds credentials). An undefined variable beside an existing `.env` line stops the converge instead of deleting the line. List entries are normalized to bare hosts; an entry that is not a host is ignored with a warning that never echoes a credential.
  - Decision 2, client: the workstation took the list from whichever source was active. It now reads the list stored on the platform sources of the gated project's own deployment: several lists, only hosts on every list count; none, the packaged default.
  - Decision 3: the workstation classified a host that the HTTP client could dial differently (`%2e`, userinfo and backslash spellings). It now classifies the host actually dialled, under a strict host grammar, and a disagreement between parsers counts as public. The clarification under Decision 3 records the loopback reading.
  - Decision 4: the stand-alone stack never created a server project row, so the default applied to nothing and the printed narrowing command failed. `register` now creates the row through the existing project-create route and reports the class the server returned. This step has not yet run against a live stack.
  - Decision 5: the audit event `project.data_class_changed` (the clarification under Decision 5); `--json` output of the verb is exactly one JSON document.
- **Known limits, not changed by these waves:**
  - The server carries the same packaged default as r4, so every UMRS deployment treats Rikyu as institutional until its operator sets the variable; a site outside RIKEN must set it (empty for none). Operator docs say so.
  - A stand-alone stack cannot change its list yet (the compose file does not pass the variable).
  - The workstation reads the list cached at the last `r4 login`; a changed site list reaches it only after the next login.
  - All three gates admit when the project's class cannot be read (no project claim, no row, no database, a lookup failure), as before this amendment.
  - 2026-10-18 (platform hardening H-1): the workstation gate now refuses public routes when the platform *denies* the active project's row. That means HTTP 403, or a 404 for a project expected on the platform: no local record, a recorded gateway, or a bound source's project. A 404 for a project recorded only locally (no gateway) still reads as "no row" and admits, as do 401 and 5xx. The research loop refuses to start uncapped when the budget row is denied (`r4/src/r4/client.py` `denial_status`, `research_loop_tools._project_wall_clock_hours`; tests `r4/tests/test_h1r_r4_client_fail_closed.py`). Residual: a real server project recorded locally without a gateway and reachable only through unsigned memberships still admits; `r4 project create <id> --gateway <url>` closes it for that project.
  - The wizard named under Consequences (`r4 setup model`, onboarding plan §7 order 4) and the locality column in the source listing (order 5) are not built.
- **Owner legs:** merge PR #465 and converge umrs-front, both done 2026-10-16 (above); the converge note still holds for later runs (with the variable left undefined nothing changes, unless `.env` already carries the line, for example one set by hand: then the converge stops until the value moves into `umrs_front_institutional_provider_hosts`; with it defined, the servers restart and `umrs-infer` is recreated).
