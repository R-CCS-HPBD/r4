# ADR-112: User-registered HPC accounts through gated delegation; declared access modes with no identity fallback

**Status:** Accepted (2026-11-08, maintainer on the investigation's recommendations G-1…G-8: "sounds good. please make a detailed plan for realizing this, then proceed")
**Date:** 2026-11-08
**Relates to:**
- ADR-069 (identity mapping per target; its Slurm half is replaced here, D-1, D-2)
- ADR-022 §5 (HPC credential custody: "UMRS-mediated per-user delegation", no broker)
- ADR-060 (one hub per institution; this makes holding many users' accounts in one hub safe)
- ADR-074 (sandboxed step code; D-6 is extended to the payload direction, D-6 here)
- ADR-110 D-8 (user step images need a non-operator step identity; a gated lane is one way to meet it)
- ADR-097 (live membership: links check it)
- ADR-104 (agent permission ladder: signing is outside it)
- CONSTITUTION §1.3 (R4 never talks to a scheduler)
- gap rows D-55 (re-opened) and D-68 (new)

## Context

Verified at parent `b747f279`, umrs `2f678503`. Evidence is in the investigation §1 and the plan §1.

- **Every job on every site runs under one person's login.**
  - Each site declares `slurm_delegation: per_user_ssh`. The per-user path logs in with a key that no longer exists: the shared delegation keys were destroyed on 2026-10-24.
  - It then falls back, with only a warning, to the site's single login (`orchestrator/remote_slurm_backend.py:215-216,236-249`). That login is a personal account of the operator (`<operator-account>`).
  - Cancel, status, cleanup, `hpc_script`, cluster loops and the staged step lane never tried per-user identity at all.
  - The staged step lanes run any verified user's uploaded code there. Nothing in the lane checks a user or a project.
- **National centres forbid this.** They tie each account to one screened person, for export-control reasons, and forbid third-party use (Fugaku Rules Art. 6.5; the 2025-06-12 briefing calls one SSH key registered across users "unauthorized account sharing"). Many users also hold their own accounts at several sites and want to use them from one UMRS account, in one workflow.
- **Storing users' real credentials does not solve it.** A server that uses a secret unattended can always be made to use it by whoever controls the server. Encryption at rest, the sealed envelope (`security/seal.py`), an HSM or an enclave each narrow copying, but not use.
- **What the prior-art survey found** (survey):
  - none of 13 gateways checks a user-signed job spec at the resource;
  - every one lets a central service or an operator-held key act as the user;
  - OpenSSH's own signature format, forced commands and capability-style caveats are each long in production.
- **The staged payload is code.**
  - The server pickles `{entrypoint, config, ctx}` and the worker imports the named entrypoint (`server/remote_slurm_step_executor.py:704-736`).
  - The batch script sources a `venue.sh` from an operator-updated checkout (`server/slurm_step_executor.py:803-826`).
  - Wrapping today's lane would therefore leave the operator able to run code as the user.

## Decision

**D-1. Declared access modes; no identity fallback.**
- Each site declares `scheduler.hpc_access` with:
  - `operator`;
  - `modes_allowed` ⊆ {`service_account`, `user_account`};
  - `default_mode`;
  - a `service_account` block (`login`, `personal`, `consent_ref`, `allowed_projects`);
  - a `user_account` block (`methods`).
- The loader parses the block explicitly and refuses invalid values.
- A lane that cannot be served under its resolved identity is refused with a typed `hpcaccess.*` code. **No lane ever retries under another identity.**
- `slurm_delegation: per_user_ssh` and the shared-key enrolment are retired. ADR-069's service-connection default and per-user-SSH mode are replaced.

**D-2. Two lane classes, one resolver.**
- `resolve_hpc_identity(site, lane, user, verified project, is_admin)` serves every lane that reaches a cluster.
- **Platform lanes** run only under the site's platform login, never under a user's identity. Their job environments carry operator secrets. They are: endpoints and serving, the service relay, tunnels, janitor, reaper and health.
- **User-work lanes** resolve per person and project: staged steps, `hpc_script`, cluster loops, and later user batch jobs.
- On a `service_account` site whose `consent_ref` is empty (the interim state of every site today), user work runs only for admins and for `allowed_projects`.
- Every attempt records its access mode, login, Slurm account and lane.

**D-3. User accounts through gated delegation.**
- A user registers a site account once.
- UMRS generates a per-person, per-site **transport key** and keeps the private half on the server, outside the database.
- The user installs a **gate** in their own HPC home and one `authorized_keys` line that pins the transport key: `restrict,command="<gate> --site <id>",from="<front>"`.
- The gate runs a job only on a request signed with the user's **signing key**:
  - the key is dedicated, held on the user's machine, never forwarded, and a FIDO2 `sk-` key with a PIN where possible;
  - the request is either an **intent** (one run) or a **grant** (bounded unattended work).
- The claim is about **authority, not secrecy**: UMRS stores nothing that can act as the user without the user's signature. The transport key's secrecy is defence in depth.

**D-4. Signatures and encoding.**
- **Signing.** OpenSSH SSHSIG (`ssh-keygen -Y sign/verify`), with one namespace per message type (`umrs-intent-v1`, `umrs-grant-v1`, `umrs-control-v1`, each `@<platform domain>`). They are listed in `allowed_signers namespaces=` and passed on every verify.
- **Flags.** The gate reads the `sk-` flags byte itself and requires user presence, plus user verification where configured. `ssh-keygen -Y verify` does not check them.
- **JSON.** Messages are strict canonical JSON:
  - the producer uses sorted keys, compact separators and restricted I-JSON values;
  - the verifier rejects duplicate keys, NaN and Infinity, and any input whose re-encoding differs from the received bytes;
  - signatures cover the exact bytes.
- **Replay.** Replay is keyed on the nonce or the intent hash, with `O_EXCL` nonce files in the user's HPC home. Grant counters are updated under `flock` and charged at submit.
- **Libraries.** The gate is one stdlib-only file. It lives in `r4-contracts` (already stdlib-only), so the gate, UMRS and r4 share one implementation of the encoding.

**D-5. What the gate enforces.**
- It composes the batch script from its own template and from signed fields only:
  - no caller script, `--wrap`, `--array`, `--dependency` or caller paths;
  - `env -i`;
  - `sbatch --export=NONE`;
  - `venue.sh` logic inlined in the gate.
- It writes the step payload as JSON from signed fields. The entrypoint comes from the signed workflow manifest.
- It accepts only an exhaustive signed file manifest, and seals the workdir at submit.
- It pins the image ID as a literal and keeps archives in a gate-owned directory. The batch script re-checks digests when the job starts.
- It offers the model-endpoint forwarder only for an endpoint named in the signed request.
- Its unsigned verbs (`hello`, `probe`, staging `put`, status, tail, collect, cancel) act only on gate-created workdirs and on jobs in its own log.
- Its log is hash-chained, and its head is witnessed by r4.
- It has no verb that changes the gate, `allowed_signers` or `authorized_keys` without the user's signature.

**D-6. No pickle on the gated path.** ADR-074 D-6 ("the server never unpickles step-controlled bytes") gains its mirror image: on a gated lane, step code never unpickles server-supplied bytes. Pool and ZMQ worker modes are unchanged.

**D-7. Registration verified by the site.**
- A site account becomes `active` only after the server has reached the gate.
- The `probe` verb must be refused by the gate itself, not by a site `ForceCommand`.
- The site's own report of the login and the Slurm associations is stored as `verified_*`.
- A project may be linked to an allocation only if the user is a live member of the project (ADR-097) and the site lists that allocation for the user.
- Links are re-verified periodically and suspended when the allocation disappears.

**D-8. R4's role.**
- `r4 hpc add` installs the gate over the user's own SSH session, interactively. `r4 hpc` signs intents and grants with the user's key.
- r4 still never submits to, queries or holds credentials for a scheduler (CONSTITUTION §1.3): UMRS composes and owns every job. This is the same boundary as D-37.
- Signing is outside the agent:
  - the command refuses in a companion shell and without a TTY;
  - it is fenced as never-auto;
  - the key requires a touch or a confirmation.
- r4 computes the code-manifest digests itself, from an export of the exact workflow version. It never signs a digest it has only been told.

**D-9. Accounting.**
- Jobs under `user_account` are charged to the user's own allocation and attributed by the centre's own accounting.
- UMRS keeps an append-only usage ledger: person, project, site, login, account, intent or grant, Slurm job id, resources. It is written in two phases around submit and reconciled from `sacct` through the gate's `usage` verb.
- `project_budgets.max_gpu_hours` is enforced before submit, and no grant may exceed what remains.

**D-10. Honest wording.**
- User-facing text says: "UMRS stores nothing that can act as you without your signature. Under a standing permission you give, UMRS chooses the data your approved workflows run on."
- It also states the residuals:
  - outputs that pass through UMRS are visible to its operators;
  - the same team publishes r4 and the gate.
- It never says "UMRS cannot read your credentials" or "cannot run anything you did not approve" without that qualifier.

### Why

- **D-1 and D-2:** a silent fallback is how the platform came to run everyone's work under one person's account without anyone deciding it. Naming the mode and refusing by name makes the state visible. Pinning platform lanes stops operator secrets from reaching users' jobs.
- **D-3 to D-6:** these are the only way found to let a central service act for users while they are offline without becoming their custodian. Every other surveyed system either holds usable credentials (Tapis, Airavata, HTCondor credmon, Slurm JWT proxies) or runs whatever the service sends (Tower Agent). Each part is mature: OpenSSH signatures, forced commands, caveats. The gate is small and readable, and the user installs it.
- **D-7:** "users register their own accounts" must not mean "users type in what they hold". The site is the source of truth.
- **D-8:** this keeps the R4/UMRS boundary that D-37 restored.
- **D-9:** user-owned allocations make attribution native. That is simpler than per-person accounting inside a shared account.
- **D-10:** the earlier draft's claim failed an adversarial review. The wording is part of the decision because centres and users will rely on it.

## Consequences

- **Positive.**
  - An honest identity on every attempt.
  - Users run under their own accounts and allocations at sites that agree.
  - One UMRS account reaches many HPC accounts.
  - The platform no longer depends on one person's personal logins for user work.
  - It meets ADR-110 D-8's second bullet for sites where the gated lane runs, which unblocks self-service images there after a lane receipt.
  - Centres get a request they can evaluate: a restricted key that carries only signed requests.
- **Costs.**
  - Five implementation waves (plan §0).
  - A rebuild of the platform step image for the JSON payload.
  - A new gate file to maintain and release with a signed manifest.
  - Users need a signing key, ideally a FIDO2 key, and OpenSSH ≥ 8.1 on the login node.
  - Until centres answer, the interim `allowed_projects` lists limit who can run user work on personal-login sites.
- **NOT in scope:**
  - Fugaku, which forbids third-party use today;
  - centre APIs (`site_api`), until offered;
  - `client_relay`, which keeps the AM design and is built only for an OTP-only site;
  - non-Slurm schedulers;
  - merging the four servers into one process (MS W4);
  - protecting data placed in UMRS from its operators.

## Alternatives Considered

- **Store users' SSH keys or passwords, encrypted (`held_key`).** Rejected. The operator can always use what the server can use. This is prohibited at Fugaku-, CSCS- and JSC-style centres.
- **Keep one service account per site with UMRS-side accounting.** Kept only as a declared mode with written consent (`consent_ref`). It is not the default: most national centres forbid it, and it hides who did what from the centre.
- **A user-run agent on the login node with signed specs (`user_agent`).** It has the same trust argument as the gate, but needs a resident process that many centres restrict. It remains an option for sites without `authorized_keys`.
- **`client_relay` (lend the user's agent per session).** It holds nothing, but cannot work while the user is offline. While the relay is open, the operator can use the key for anything it reaches. Kept for OTP-only sites.
- **Centre-issued short-lived credentials (CA certificates, Slurm JWT, opkssh, Vault).** Whoever holds or mints them acts as the user. Used only where the centre runs the service and the user holds the token.
- **A capability-token library (Biscuit, macaroons, UCAN).** No stdlib implementation can run in the gate, and macaroons' symmetric root key would let the operator mint. Biscuit is the reserve format if delegation becomes multi-hop.
- **An enclave or HSM for server-held keys.** These stop copying, not use, and keep the code publisher trusted. A vTPM transport key stays an optional hardening.

## Rollout-status (2026-11-08)

- Decision accepted.
- The plan is gated-delegation-plan-2026-11-08.md:
  - wave A covers D-1 and D-2;
  - B1 covers D-3, D-4 and D-7;
  - B2 covers D-5, D-6 and D-8;
  - C covers grants (D-3, D-4);
  - D covers D-9;
  - E covers release integrity and the first non-maintainer user.
- Nothing is implemented yet.
- User documentation: [chapter 31](../user-manual/31-own-hpc-accounts.md).

## Rollout-status (2026-11-08, waves B1 and B2)

**Wave B1 (D-3, D-4, D-7): landed repo-side, not merged, not deployed.** It is stacked on wave A; evidence is wave-ledger entry (247).
- **The gate** is one stdlib-only Python file in `r4-contracts` (`r4_contracts/hpc_gate.py`), tested on Python 3.9 and 3.11. It is both the forced command and the shared encoding (D-4): strict canonical JSON, an SSHSIG parse with the `sk-` presence and verification flags checked by the gate, `ssh-keygen -Y verify` per namespace, `O_EXCL` nonces, a 60 s session deadline and a hash-chained log. Its verbs are `hello`, `probe` and the signed `revoke` and `rotate`.
- **The server** keeps site accounts and project-to-allocation links in two new tables keyed on the venue, generates the per-person transport keys outside the database (at most two per person and site), and reaches the gate with only that key offered. An account becomes `active` only after the gate itself refused `probe` and answered `hello` with the expected login, site, gate hash and OpenSSH version (D-7). A link needs live project membership and an allocation the site itself reported.
- **r4** has `r4 hpc add / verify / status / link / unlink / revoke / rotate`. The signing key is dedicated and file-based, never handed to an agent; account commands refuse in the companion shell and without a terminal, and the server refuses requests an agent marked as its own (D-8).
- **One site offers `user_account`** in its configuration (the first test site); its default mode is unchanged. Nothing runs under a user's account in B1.
- **Deferred to B2:** a server route that carries signed controls to the gate, and r4 witnessing the gate's log head.

**Wave B2 (D-5, D-6, D-8): in progress.** Built in lanes against the binding wire contract in plan §5 B2.0. What it delivers:
- **Intents.** `r4 workflow trigger <name> --site <site>` asks the site for a proposed intent, recomputes the code manifest, step graph and step configuration from its own export of the exact workflow version, refuses with a diff on any mismatch, shows the run, and signs one intent for one workflow run at one site (D-8).
- **The gate runs steps** (gate version `"2"`). It composes the batch script from its own template, the signed intent and a site profile that the user installed and reviewed (`~/.umrs/gate/site.json`); it writes the step input as JSON, seals the workdir, keeps one `O_EXCL` nonce per (intent, step), and submits with `--export=NONE` under a cleaned environment (D-5).
- **No pickle on the gated path (D-6).** The step worker gains a JSON input mode; the platform step image is relabelled for it and must be rebuilt and re-staged.
- **The server** resolves user work to `user_account` when the person has an active account and an active link for the verified project, refuses other user-work lanes there by name (`hpcaccess.lane_not_gated`) rather than running them under the platform login, stores the signed intent with the run, and drives each step through the person's gate. The reaper, janitor and account cascade reach gated jobs through the same gate.

**Amendment to D-5 (2026-11-08, plan §5 B2.0).** Two phrases of D-5 are superseded by the binding wire contract:
- *"keeps archives in a gate-owned directory"*: the gate reads image archives from a directory named in the site profile, which the operator may own. Only content counts: the gate checks each archive's sha256 and image id at submit, and the job copies the archive to a private file, hashes that copy and loads the same copy, so the operator can supply an archive but cannot swap it.
- *"staging `put`"*: there is no unsigned `put`. The gate writes every staged file itself from one tar stream (`stage-in`), accepts only regular files under `src/` and `in/blobs/<sha256>`, and checks them against the signed manifest at submit.

Also superseded: the plan's earlier wording that r4 takes image ids from an allow-list shipped with r4. r4 shows each image's reference and id to the user before signing; the signed release manifest of wave E pins them.

**Review fixes and deviations (2026-11-08, plan §5 B2.0 "fixer amendments").**
- **Ancestors.** The gate and the job script now check every ancestor of each path they trust, not only the final directory: the Slurm directories and tools, the work directory, the venue, storage, network and runtime directories, and the workdir. Each ancestor must be one that only root or the person's login can change. When the node gives no runtime directory, the job keeps its runtime directory below its own venue root.
- **Lanes.** A run under the person's account never reaches a lane without a gated path. A local `sbatch` lane refuses with `hpcaccess.lane_not_gated`.
- **Run ids.** The server issues each run id from a server-only key, so no person can choose another run's id.
- **Plan approval.** In a project that requires plan approval, a signed intent counts as the explicit approval.
- **D-10.** The text before signing says exactly what is pinned.
- **Deviation: endpoints.** B2 proposes no model endpoint. A step that would read one is refused at proposal (`endpoint_not_supported`), before anything runs. Signed endpoints, the forwarder and their checks are in place for a later wave that proposes them.
- **Deviation: partition caps.** Per-partition caps in the published profile come from the site-wide step-lane values. The site's `user_account.profile` block can override them.
- **Deviation: step name.** The gate reserves the step name `intent`.

**Not done yet.** B2 is not merged or deployed. D-8's second bullet of ADR-110 is met on a site only after a gated lane receipt there; the first receipt (one step under the maintainer's own login through the gate) is a maintainer leg after a staged converge with the rebuilt step image. Grants (wave C) and the usage ledger (wave D) are not started.

## Rollout-status (2026-11-09, waves C and D)

**Waves C (grants; D-3, D-4, D-10) and D (ledger and audit; D-9, D-5's witnessed log): implemented on `wave/gated-cd-grants-ledger`, merged in #829 at `e7cc6d45` and deployed at `34be4f31` on all fronts with strict canaries passing.** Both are built as one change, stacked on B2, in lanes against the binding wire contract in plan §6 "C/D.0", including the critic's amendments K1–K20 of 2026-11-09. The earlier lines that waves C and D are "not started" are superseded by this block. What the contract delivers:
- **Grants (gate version `"3"`).** A grant is signed with namespace `umrs-grant-v1@<domain>`, names one site, and binds the login, the allocations, the projects, exact workflow versions (code-manifest digest, step-graph-and-configuration digest, and each workflow's own parameter names), the images, a JSON-Schema subset for parameters, an input rule (`listed`, `chained` or `any`), caps (jobs, GPU-hours, core-hours, concurrent steps), a window of at most 30 days and `delegable: false`. For each run, the server composes a run spec, which is an unsigned B2 intent body. At every step the gate checks grant ∩ run spec and reserves the step's cost against counters in the user's HPC home under `flock`. New gate verbs are `grant-check` and the signed control `grant-cancel`.
- **Server.** Migration `0137_hpc_grants` adds a mirror table; the gate's state stays authoritative. Admission uses a named grant, or the one covering grant, and refuses `signature_required` otherwise; it never falls back (D-1). Loops keep the grant they were started under. New errors are `hpcaccess.grant_invalid`, `grant_not_found`, `budget_exceeded` and `ledger_unavailable`.
- **Ledger (D-9).** Migration `0138_hpc_usage_ledger` adds an append-only table (a trigger refuses UPDATE and DELETE) and a current-row view. It is written in two phases around every user-work submit, in both access modes, and settled through the gate's `usage` verb or `sacct`; a timer recovers what a crash left open. Reports come from `r4 hpc usage`, a per-project route and `umrs-server admin site-accounts usage`.
- **Witness (D-5).** `hello` returns a checkpoint of the gate log in the C2SP tlog-checkpoint text form. `r4 hpc status --witness` also covers grant entries, counts jobs against each grant's cap, and compares the server's view of the log head; a fork is reported loudly and blocks signing.

**Refinements of the decision text** (the contract makes these precise; none changes a decision):
- **D-4, "charged at submit".** The gate *reserves* at submit the most a step can cost: its walltime rounded up to whole minutes, as Slurm enforces it, and the whole node on an exclusive partition. It *settles* from `sacct` after the job ends, charging the actual measured allocation, including CPU/GPU over-allocation above the reservation. A job `sacct` never reports is charged its reservation once its deadline guarantees it ended. Counters are integer seconds. They are guarded by a separate lock file, because the counters file is replaced by rename, and `sacct` never runs under that lock.
- **D-3, revocation ids.** The `grant_id` is the revocation id. `grant-cancel` is a signed control relayed by the server. If the relay fails in transport, the server still stops using the grant and marks the gate's record `pending`. The gate keeps a cancel for 400 days, longer than any grant. The server's account revoke ends a person's grants in its mirror. Because that revoke destroys only the transport key, the gate learns of it through an epoch file: the signed `revoke` writes it, and so does `r4 hpc add` when it re-enrols a revoked account. Grants issued before the epoch refuse.
- **D-9, "no grant may exceed what remains".** This is checked when a grant is stored. A grant reserves no budget. The budget binds at each step's pending ledger write, under an advisory lock, and the admission check is advisory. `project_budgets` has no period and no venue key, so the budget is lifetime and summed across all venues of the shared database. It is compared in integer GPU-seconds.
- **D-9, who sees usage.** Members see their own rows and the project totals. Owners see other members' rows without the columns that identify those members' HPC accounts and audit trails. Platform admins see everything.
- **D-10, wording under a grant.** Every grant summary carries the first D-10 sentence, both residuals, and an explicit list of what UMRS still decides (when to run, parameter values within bounds, the data by the input rule, each step's resources within the installed site profile) and what it cannot do. The sentence "UMRS chooses the data your approved workflows run on" is shown verbatim for `any`, and in a scoped form for `chained`.
- **D-8.** While a witness fork is recorded, r4 refuses to sign intents, grants and `rotate`, but still signs `grant-cancel` and `revoke`, the controls that take authority away.

**Merged and deployed; personal-grant acceptance remains.** Waves C and D merged in #829 at `e7cc6d45` and deployed at `34be4f31` on all fronts with strict canaries passing (ledger 264). A grant still counts as working on a site only after a gate-v3 grant-backed lane receipt there. Wave E (release integrity, the first non-maintainer user) is not started.

## C/D implementation and hardening status (2026-11-09, final repo-side review)

This block supersedes the earlier "B1/B2 not merged" and "C/D in progress" status wording without changing the Accepted decision. **A and B1 are merged (#808/#811). B2 and the explicit default-project rule are merged through #815 at `31471aa8`; #820 and #817 are also merged. C and D are implemented on `wave/gated-cd-grants-ledger`, merged in #829 at `e7cc6d45` and deployed at `34be4f31` on all fronts with strict canaries passing.** The prerequisites #810, #820 and #817 do not constitute C/D acceptance.

The implementation carries the contract's grant mirror (migration `0137_hpc_grants`), gate v3 verification and counters, append-only usage ledger (migration `0138_hpc_usage_ledger`), two-phase submit accounting, all-venue lifetime budgets, reports and witness. Review hardened it in the following ways, which are part of D-3, D-4, D-5, D-8 and D-9 rather than a new decision or second authority:
- **Role separation.** `images` allows a step image; `trusted_images` independently allows the helper/posture image, and every helper must also be listed in `images`. No fallback may substitute a step image for the helper. Legacy single-image grants stay readable and cancellable; an older multi-image grant without explicit roles must be revoked and reissued.
- **Signing destination and direction.** A local site record binds its physical SSH destination and venue. Re-enrolment cannot redirect one signer to another physical gate, and two site records cannot sign for one venue, because that would create two independently charged authorities for the same signature scope. Cancelling or revoking under the existing record remains available. These operations refuse different destinations, never choose a new route on their own.
- **Cancellation and witness evidence, not acknowledgement by assertion.** A relayed cancel completes only after its claimed entry is found at that exact head of the chain read over the person's SSH. Otherwise `grant-cancel --direct [--ssh HOST]` can submit the fresh signed control directly and require the gate's receipt. An unverified answer exits nonzero and names the pending or unconfirmed mirror. `r4 hpc usage --witness [--ssh HOST]` independently compares the returned ledger rows with gate submits and settlement charges; a broken chain, missing submit, missing row or under-reported charge is a failure.
- **Atomic, replay-safe settlement.** Scheduler observation happens outside the grant lock. Terminal counter changes and the fixed-form hash-chain receipt converge idempotently after either a log or counter write failure; retry keeps one charge and one receipt. A charge whose accounting record has vanished is bounded by its reservation after its deadline proves it ended. Cancellation and settlement do not cancel a running job.
- **One canonical scheduler owner.** A fresh executor dispatch has an opaque id, distinct from the Slurm integer attempt. The earliest append-only scheduler-job association remains the owner, with an advisory job lock closing the first-association race. Repeated settlement by the live lane and the recovery sweep therefore charges once. Recovery names include the job, person, project and attempt, and active run specs are not evicted merely because another parallel run refreshed the cache.
- **Exclusive-node and service budget closure.** Gate K4 (`hpc_gate.grant_cost`) binds rounded Slurm walltime and, on an exclusive partition, the whole node rather than the bare request. Gated steps, remote and local service steps, `hpc_script`, cluster loops and research-loop admission use their real pricing inputs. Missing or malformed required pricing becomes `hpcaccess.ledger_unavailable`; incomplete GPU estimates cannot pass a capped project. The whole-run service check is advisory, while each pending write remains binding. The script estimate is derived from `sbatch_args`, not by reading a user's script.
- **Report and audit correctness.** Current-row reads use base-table latest-row logic rather than globally materializing the append history in binding paths. Report row pages are bounded by `limit`, but totals cover all matching current rows with the same venue, person, project and time filters; public responses retain the same fields and privacy rules.

Verified repository evidence is a consolidated full `bash scripts/ci-lint-local.sh` reporting **ALL CHECKS PASSED (3 warnings)**, full UMRS **11,843 passed / 327 skipped**, gate HPC **718 passed / 1 skipped**, isolated real pg15 **136 passed** (including migration, concurrency and canonical-settlement cases), and the full UMRS changed-files gate reporting **PASSED**. Earlier fail-before suites exercised direction/role substitution, cancellation and settlement evidence, K4 underpricing, scheduler races and recovery holes.

**Rollout remains open.** There is no C/D production deployment and no live grant-backed Slurm receipt. Staged converge `check` then `apply`, gate-v3 installation and any needed per-site re-enrolment remain maintainer actions. A site does not count as having accepted this wave until the first non-maintainer grant-backed run has a direct-verified cancel and usage-witness receipt. Older multi-image grants also need reissue before continued use. Wave E release integrity and complete first-non-maintainer acceptance are not started.

## Related

- [ADR-069](069-identity-mapping-per-target.md): its Slurm decision is replaced by D-1 and D-2. Its S3 and k8s sections stand.
- [ADR-074](074-uploaded-step-code-runs-sandboxed.md): D-6 here extends its D-6.
- [ADR-110](110-self-service-step-images.md): D-8 there is met per site by a gated lane receipt.
- [ADR-060](060-topology-one-umrs-hub.md), [ADR-097](097-live-project-membership.md), [ADR-104](104-one-mode-one-permission-decision.md)
- Investigation, survey and plan (linked above). User manual chapter 31.
