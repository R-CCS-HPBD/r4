# 31. Your Own HPC Accounts (Gated Delegation)

> **Audience:** researchers who have their own login at one or more HPC sites connected to UMRS, and the operators of those sites.
> **Status (2026-11-08):** design accepted ([ADR-112](../adr/112-user-hpc-accounts-gated-delegation.md)); being built in stages (see the ADR's rollout status). Each section below says what works today. Commands marked *(planned)* do not exist yet.
> **Status (2026-11-09, B1):** connecting a site (§31.5), linking a project to an allocation (§31.6), and revoking or rotating UMRS's key (§31.9) are implemented and **merged (#811)**. Repo availability is not a claim that your site is already enrolled or that a live run has passed.
> **Status (2026-11-09, B2):** one signed workflow run at a time under your own account is **merged through #815**. It still needs the site's correct gate, profile and staged platform image, plus a maintainer's staged converge and first-run receipt where those have not already happened.
> **Status (2026-11-09, waves C and D):** unattended work under a **grant** (§31.7, *Unattended work*) and the **usage ledger, reports and project GPU-hour budget** (§31.8) are **on `wave/gated-cd-grants-ledger` (UMRS `22dffbd1`), pending review, unmerged and undeployed**. Grants need gate version 3 (§31.5). Before a site can be called live, its maintainer must complete staged converge and obtain the first non-maintainer grant-backed run with a verified cancel and witness receipt. The ledger and budget check cover user work under the platform login too.
> **Background:** the design follows established practice: OpenSSH signatures as used for signed git commits, forced-command keys, and capability-style limits. No surveyed science gateway checks a user-signed job at the HPC site, which is the part this design adds.

---

## 31.1 Why this exists

When you run a workflow step on an HPC site through UMRS, the job needs a Unix account at that site.

| | Today | With your own account |
|---|---|---|
| **Login the job runs under** | the platform maintainer's account at that site | your own login |
| **Allocation charged** | an allocation the maintainer chose | your own allocation, as the site lists it for you |
| **Who the centre sees** | the maintainer | you |
| **Who may run there** | projects the maintainer is responsible for | anyone with their own account at that site |

Two reasons drive the change:

1. **Centre rules.** National centres tie each account to one screened person, often for export-control reasons, and forbid letting a third party use it. Fugaku's rules say so explicitly. Running many people's work under one account is acceptable only where the operator agrees in writing.
2. **One person, many HPCs.** If you hold accounts at several sites, you register each once. Then one session or one workflow can use all of them, each under your own identity, and each centre bills your own allocation.

---

## 31.2 The idea in one paragraph

UMRS never holds your password or your real SSH key. When you connect a site, you install a small, open program, the **gate**, in your HPC home. You also authorise one **restricted key** for UMRS, which can do nothing except talk to the gate. The gate submits a job only if you approved it with your **signing key**. That key stays on your computer and needs your touch (a FIDO2 security key) or your confirmation for every signature. You approve either one run (an **intent**) or a limited permission (a **grant**: which workflow versions, how many jobs, how many GPU-hours, until when). The gate writes the batch script itself, checks that the code, image and inputs match what you signed, runs the step in its container, and logs every action in your HPC home.

```
 your computer                 UMRS                         your HPC home
 signing key  ── signed ──►   restricted key  ── SSH ──►   gate: verifies your signature,
 (touch to approve)           (reaches only the gate)       builds and submits the job, logs
```

---

## 31.3 What the people who run UMRS can and cannot do

This section is the security promise. It applies to everyone with access to the UMRS servers, administrators included.

| They **cannot** | They **can** |
|---|---|
| log in as you or get a shell in your account | run the workflow versions you approved, within the limits of your grant |
| read or change your files outside a job's own work directory | choose the input data for a grant, **if** your grant allows "any data UMRS stages" |
| run code, a container image or a step you did not sign | see results that pass through UMRS (its object store) |
| extend, enlarge or forge a grant, or add a signing key | cancel or watch jobs that UMRS submitted for you |
| use the restricted key for anything but the gate | refuse service |
| hide what they did from the gate's log in your HPC home | |

**What this does not cover:**
- **Data you put into UMRS.** Buckets and outputs on the UMRS object store remain readable by its operators. This chapter protects your **HPC account**, not data you hand to UMRS.
- **The software itself.** The same team publishes r4 and the gate. The gate is small enough to read, and its hash is published with each release. Pin the hash you reviewed, or ask your centre to (§31.5).
- **Your own computer and the centre itself.** If either is compromised, so is everything else.

The short form: **UMRS stores nothing that can act as you without your signature. Under a standing permission you give, UMRS chooses the data your approved workflows run on.**

*(2026-11-09, waves C and D.)* Under a grant, the right-hand column is spelled out exactly in §31.7, *What UMRS still decides under a grant*: besides the data, UMRS chooses when runs start, the parameter values within the bounds you signed, and each step's resources within the site profile. r4 shows that list in every grant summary.

---

## 31.4 Before you start

| You need | Why |
|---|---|
| Your own login at the site, and an allocation there | jobs run and are billed under it |
| A site that supports this mode | see §31.10; the site's operator must have agreed |
| OpenSSH 8.1 or newer on the site's login node and on your computer (8.2 for FIDO2 keys) | the gate verifies signatures with `ssh-keygen -Y verify`, which first appeared in 8.1. *(2026-11-08: this row said 8.0; `r4 hpc add` refuses below 8.1.)* |
| Python 3.9 or newer at `/usr/bin/python3` on the site's login node *(added 2026-11-08)* | the gate is one Python file that uses only the standard library |
| `sacctmgr` on the login node *(added 2026-11-08)* | the gate reads your Slurm allocations with it. Without it the site lists no allocation for you, and §31.6 cannot link one |
| The site must honour options on keys in `~/.ssh/authorized_keys` (`restrict`, `command=`) | the restriction on UMRS's key lives there; some centres register keys in a web portal instead (§31.10) |
| The site must offer `user_account` *(added 2026-11-08)* | elsewhere `r4 hpc add` is refused with `hpcaccess.mode_undeclared`. bdrt is the first site to offer it, from the deploy of wave B1 (§31.10) |
| r4 on your computer, logged in to UMRS | [Chapter 2](02-quickstart.md), [Chapter 3](03-authentication.md) |
| A FIDO2 security key with a PIN (recommended), or a dedicated SSH key protected by a passphrase | your signing key; never your login key. With a FIDO key, the gate itself checks that you touched it, and that you entered the PIN where your site asks for that |

---

## 31.5 Connect a site *(available from wave B1 on sites that offer `user_account` (bdrt first); running jobs under your account comes with wave B2)*

> **Status (2026-11-08):** this section was marked *(planned)*. From wave B1, `r4 hpc add`, `r4 hpc verify` and `r4 hpc status` exist on sites that offer `user_account`. Connecting a site installs and checks the gate; **in B1 nothing runs under your account yet.** The file names and the key line below follow the wave B1 contract and replace the earlier draft (`umrs-gate`, comment `umrs-transport-<site>-<you>`).

```bash
r4 hpc add bdrt                               # name of the UMRS site, as in `r4 sites`
r4 hpc add bdrt --ssh <ssh-destination>       # how r4 logs in to the site as you
r4 hpc add bdrt --key-type ed25519            # passphrase-protected signing key instead of a security key
r4 hpc add bdrt --manual --home <your home directory at the site>   # print the files; you install them yourself
r4 hpc verify bdrt --login <your login at the site>                 # ask UMRS to check the gate again
```

| Option | Meaning |
|---|---|
| `--ssh <ssh-destination>` | the SSH destination r4 uses to log in **as you**: a host alias from your own SSH configuration, or `<login>@<login host>`. Without it, r4 uses the site name as the destination. r4 runs your own `ssh` with it, so your usual keys, passwords, one-time codes and jump hosts work |
| `--key-type sk` (the default) | the recommended signing key: an `ed25519-sk` key created on your FIDO2 security key, with its PIN required for every signature |
| `--key-type ed25519` | a signing key stored as a file under `~/.config/r4/hpc/signing/`, protected by a passphrase. Use it only when you have no security key. The gate then accepts signatures from a key that is not a security key, for you only |
| `--manual --home <dir>` | r4 does not log in for you; it prints the files and the `authorized_keys` line instead (see *Install by hand* below). `--home` is your home directory at the site, which the line's forced command names |

`r4 hpc add`, `r4 hpc rotate` and `r4 hpc revoke` need you at a terminal. Every account command except `status` refuses inside an agent's companion shell, and so does r4's client underneath it; UMRS also refuses a request that an agent marked as its own. When an agent tries to run one of these commands through its shell, chat/plan asks first; autopilot logs and continues the local permission step, but the companion-shell, client and UMRS signature barriers still refuse it (2026-11-09, ADR-104 amendment 3). These guards stop mistakes and ordinary misuse. They are not a sandbox: a program written to hide what it runs can get past a pattern check, which is why the gate trusts only your signature. *(2026-11-08 review: this paragraph said agent tools cannot run them at all.)*

r4 remembers which signing key it installed at each site (in `~/.config/r4/hpc/sites/<site>.json`), and `r4 hpc verify` and `r4 hpc rotate` use that key. If you have both a security key and a passphrase key and r4 has no record for the site, it asks you to pass `--key-type`.

The record also binds the **physical SSH destination** and the site's **venue**:
- Re-running `add` against the same record and destination is an upgrade. Moving that signer to another physical gate is refused rather than silently charging a different gate's counters. Register a genuinely different gate under a different site and venue; `grant-cancel` and `revoke` stay available through the old record.
- Two site records cannot resolve to the same venue. One grant or intent signed for that venue would otherwise work at both gates, each with its own counters, so the second enrolment is refused. Older records without a proven destination cannot be moved either; they keep only their existing cancel/revoke path until resolved explicitly.

What happens:

1. **Check.** r4 checks that your local OpenSSH is 8.1 or newer (8.2 for a security key).
2. **Signing key.** r4 creates a dedicated signing key, or asks you to touch your security key to create one, or reuses the one it made before. It is stored on your computer only. It is never your login key, and r4 never hands it to an SSH agent.
3. **Restricted key.** r4 asks UMRS for a fresh restricted key for you at this site. UMRS creates a new Ed25519 key, keeps the private half on the UMRS server in a key directory outside its database (the database holds only the key's id and fingerprint), and sends r4 the public half, the key id (`tk-` plus 12 hexadecimal characters), the site's signing domain, and the gate's version and SHA-256 hash. r4 checks that the public key is exactly one Ed25519 key (`ssh-ed25519` and 68 base64 characters that decode to a 32-byte key), so nothing can be smuggled into your `authorized_keys`.
4. **Gate install.** r4 shows you what it will add, then logs in to the site **as you** with your normal SSH setup. It installs:
   - `~/.umrs/gate/umrs_gate.py`: the gate, byte for byte the copy shipped with your r4 release. r4 checks that its hash matches what UMRS reports and shows it to you;
   - `~/.umrs/gate/gate.json`: the site, the signing domain, your UMRS user name, whether a PIN is required, whether a non-security key is allowed, and the gate version;
   - `~/.umrs/gate/allowed_signers`: your public signing key. It is valid only for the three message types (`umrs-intent-v1`, `umrs-grant-v1` and `umrs-control-v1`, each followed by `@` and the site's signing domain), so a signature you make for anything else, such as a git commit, never counts here;
   - one line in `~/.ssh/authorized_keys`, shown to you before it is added:
     ```
     restrict,from="<UMRS address>",command="/usr/bin/python3 -I /home/<you>/.umrs/gate/umrs_gate.py --site <site>" ssh-ed25519 AAAA… umrs-transport-<site>-tk-<12 hex>
     ```
     `<site>` is the site's configured name, which can differ from the name you type after `r4 hpc add` (bdrt's is `bdrt-front`); r4 shows you the exact line. The `from=` option appears only where the site declares the address UMRS connects from. *(2026-11-08 review: the example showed `bdrt` here.)*

   The gate creates its own state as it works: `~/.umrs/gate/state/nonces/` (one file per signed request already used) and `~/.umrs/gate/log/gate.jsonl` (its log; each entry carries the hash of the one before, so a removed or changed entry shows).
5. **Verification** (`r4 hpc verify bdrt --login <your login at the site>` repeats this step on its own). First, UMRS sends the gate `probe`, which the gate must refuse with `gate_probe_refusal`. That exact answer proves that the gate, and not a site-wide SSH wrapper, answers for the restricted key; a site whose SSH setup bypasses the gate cannot be used. Then UMRS calls `hello`. The gate reports your login name, the gate's version and hash, the Python and OpenSSH versions, the fingerprints of your signing keys, the head of its log, and the allocations the site lists for you (from `sacctmgr`). UMRS checks that the login is the one you gave, that the gate is configured for this site, that the hash equals its own copy of the gate, that the site's OpenSSH is 8.1 or newer (8.2 when your signing key is a security key), and, where the site requires a PIN, that the gate's own configuration requires it too. Then it marks your account at this site `active`. You never type in your allocations, so UMRS cannot be told you hold one you do not. If a later verify fails on an active account, the account becomes `suspended` until a verify succeeds again, and UMRS runs one verify per person at a time. *(2026-11-08 review: the site, OpenSSH and PIN checks and the suspension were added.)*

**Install by hand.** If you prefer not to let r4 log in for you, `r4 hpc add bdrt --manual --home <your home directory at the site>` prints the files under `~/.umrs/gate/` and the exact `authorized_keys` line. You copy them yourself, then run `r4 hpc verify bdrt --login <your login at the site>`.

**Pin the gate.** At every verify the gate hashes itself in its `hello` answer, and UMRS stores that hash. `r4 hpc status bdrt` compares the stored hash with the gate shipped in your r4 release and warns when they differ. Compare it also with the hash published in the r4 release notes, or with the hash your centre reviewed.

**Signed gate releases (wave E, repo-side only).** When your operator supplies the release payload/signature and a maintainer public trust identity, `r4 hpc add` verifies the maintainer FIDO signature, exact gate version/bytes, minimum r4 version and expiry before installing. Name the trust with `R4_HPC_RELEASE_TRUST`, or leave it unset to use `~/.config/r4/hpc/release-trust.json` after explicitly selecting `R4_HPC_RELEASE_MANIFEST` and `R4_HPC_RELEASE_SIGNATURE`; the explicit trust env takes precedence. The default trust file alone does not enable release mode. `r4 hpc status <site> --live` also refuses a live hello hash that differs, binding the check to the recorded enrollment domain. JSON live status performs the same check; `--witness --live` is refused as ambiguous. Any missing pin, recorded release domain, or other partial configuration fails closed; never generate a replacement production key or invent a domain. With **none** of the variables set, pre-wave-E behavior remains as an explicitly labelled, unauthenticated legacy compatibility envelope. There is no production release public pin or signed production manifest yet, so this does not assert an authenticated live release or first non-maintainer acceptance. The maintainer procedure is in GATED_DELEGATION.md.

**What `r4 hpc status` shows.** For one site (your home site unless you name one): your account's state (`pending`, `active`, `suspended` or `revoked`), the probe result (`refused` means the gate answered; `bypassed` means something else answered, and the site cannot be used; `unknown`), your verified login and allocations and when they were checked, the gate's version and hash, the restricted key's id and fingerprint, your signing keys' fingerprints, and your project links (§31.6). It shows public information only, never a private key.

**What B1 does not do.** Connecting a site records your account and checks the gate. It does not move any job to your account: until wave B2, jobs run as §31.10 describes, and the gate in B1 knows only `hello`, `probe`, `revoke` and `rotate`.

**Gate version 2 and the site profile (wave B2, added 2026-11-08).** Running jobs (§31.7) needs gate version 2 and a site profile you have reviewed:
- **Upgrade.** Run `r4 hpc add <site>` again. It is safe to repeat. It replaces the gate and `gate.json`, writes the site profile, and keeps your `allowed_signers`, the gate's `state/` and its log.
- **The site profile** is `~/.umrs/gate/site.json`. It lists what the gate may use at this site:
  - the partitions, with their architecture and their CPU, GPU and time maxima;
  - the directories it takes Slurm commands from;
  - the directory that holds the platform's image archives;
  - the container settings and the node-local container storage paths;
  - the size limits for code, inputs, outputs and logs.

  r4 fetches it from UMRS and shows it to you, with the differences from what is installed. It then writes it with mode 0600 and records its hash. Only r4 writes it, over your own SSH session; no gate verb changes it.
- **`r4 hpc profile <site>`** reinstalls only the site profile, after showing it, and then verifies the gate. Run it when UMRS reports `site_profile_stale`.
- **What the gate checks itself.** The profile comes from the site's operator, so the gate does not take it on trust:
  - each directory it runs Slurm commands from must be owned by root (or by an owner the profile lists and the install shows you), must not be a symbolic link, and must not be writable by group or others. Otherwise the gate refuses with `profile_unsafe`;
  - each directory above those, up to `/`, and the directories of each Slurm command itself (after following any link) must be ones that only root or a listed owner can change. A directory that others can write is allowed only if root owns it and it has the sticky bit, like `/tmp`. The gate runs the command it checked, not the link. *(2026-11-08 review: before this, the gate checked only the listed directories, so an operator who could write a parent directory could have put a fake `sbatch` in place.)* The install shows each listed owner only as a number; check that it is root or the site's Slurm administrator, not the account UMRS itself uses;
  - the job checks that the node-local container directories (venue, storage, network settings, runtime, and the job's own configuration) are yours, have mode 0700, and sit under directories that only root or you can change, and checks the same of the work directory before it mounts anything. When the compute node gives no runtime directory of yours, the job keeps one under its own venue directory, not under a fixed name in `/tmp`. Otherwise the job stops with exit 78 *(2026-11-08 review)*;
  - image archives may sit in a directory the operator owns, because only their content counts (§31.7).
- **Verify.** `hello` now also reports the hash of your installed profile. `r4 hpc verify` refuses with `hpcaccess.gate_version` (`site_profile_missing`) when there is no profile. When your profile differs from the one UMRS publishes, your account stays `active`, but runs are refused until you run `r4 hpc profile <site>`.
- **Witness the gate's log.** `r4 hpc status <site> --witness` reads `~/.umrs/gate/log/gate.jsonl` over your own SSH session. It checks the hash chain, checks that the log extends the head r4 stored last time, stores the new head, and warns if the log was rewritten. It lists the jobs the gate submitted since then, each with its full intent id, and flags any intent that r4 did not sign itself (r4 keeps the ids it signed under `~/.config/r4/hpc/signed/`). It flags such an intent again on every witness, and exits 1, until you check it and clear it with `--ack <intent id>` *(2026-11-08 review: the warning was shown only once)*. r4 still never runs a Slurm command.

**Gate version 3 (waves C and D, added 2026-11-09).** Grants (§31.7, *Unattended work*) need gate version 3:
- **Upgrade.** Run `r4 hpc add <site>` again, as for version 2. UMRS refuses to store a grant for a gate older than version 3 with `hpcaccess.gate_version` (`grants_need_gate_3`). Upgrading an active account ends none of your grants.
- **Reconnecting after a revoke.** When `r4 hpc add` reconnects an account that UMRS reports as `revoked` (or no longer has), it also writes the time of the reconnect into the gate's state (`~/.umrs/gate/state/grants/epoch`), over your own SSH session. A signed `revoke` (§31.9) writes it too. Every grant issued before that time is refused from then on (`grant_cancelled`, detail `account re-enrolled`), so a grant you gave before a revoke never comes back to life with the new restricted key.
- **Two explicit image roles.** Step images are listed under `images`; the helper/posture images are independently listed under `trusted_images`, and every helper must also be in `images`. A step image never substitutes for the helper automatically. Older one-image grants remain readable/cancellable; older multi-image grants without `trusted_images` must be revoked and reissued.
- **`hello`** also answers with a **checkpoint** of its log: the site, your login, the number of log entries and the hash of the last one, in the plain-text checkpoint format used by public transparency logs.
- **The witness covers grants.** `r4 hpc status <site> --witness` also:
  - lists each grant submit (grant id, run id, step) and each grant cancel since the head it stored last time;
  - flags a grant that r4 did not sign itself (r4 keeps a record of each grant it signed under `~/.config/r4/hpc/grants/`), on every witness and with exit 1, until you clear it with `--ack <grant id>`, as for intents;
  - counts each grant's submits over the whole log and flags `✗ OVER CAP` when there are more than the grant's job limit. It shows the GPU- and core-seconds each grant reserved, but does not compare them with the hour limits, because the gate may charge less than it reserved (§31.7);
  - compares the log with the head UMRS reports for your gate, and reports a **split view** when UMRS's head is not an entry of the log you read.
- **Forks, loudly.** When the log does not extend the head r4 stored, or UMRS shows a split view, r4 prints `✗ FORK` lines, exits 1, and records the fork for that site. While a fork is recorded, r4 refuses to sign an intent, a grant or a `rotate` for the site. It still signs `r4 hpc grant-cancel` and `r4 hpc revoke`, which take authority away, and prints the fork beside them. After you have looked into it, `r4 hpc status <site> --witness --clear-fork` stores the new head and clears the record.
- **`r4 hpc status <site> --live`** compares the gate's current head with the stored one without reading the log. A shorter log, or the same length with another hash, is the same loud fork.

---

## 31.6 Link a project to an allocation *(available from wave B1 on sites that offer `user_account` (bdrt first); running jobs under your account comes with wave B2)*

> **Status (2026-11-08):** this section was marked *(planned)*. From wave B1, `r4 hpc link` and `r4 hpc unlink` exist. A link records which of your allocations a project's runs will be charged to; **in B1 it charges nothing, because nothing runs under your account until wave B2.**

```bash
r4 hpc link --project my-project --site bdrt --account <allocation>
r4 hpc unlink --project my-project --site bdrt
r4 hpc status            # every site, its gate, your allocations and links
```

- UMRS accepts the link only if your account at the site is `active` (§31.5) and you are a current member of the project; membership is checked live.
- UMRS refuses the link with `hpcaccess.allocation_not_at_site` if the site does not list that allocation for you. The list is the one the gate reported at your last `r4 hpc verify`.
- A project has one link per member per site. Two members of one project may charge different allocations, or the same group allocation.
- If an allocation disappears at the site (for example because you left that HPC project), the link is suspended and new runs to it are refused by name. *(2026-11-08: the periodic re-check that does this automatically is planned; in B1, a link is suspended at your next `r4 hpc verify`, within a minute of your UMRS account being disabled, and, where the site enforces live project membership, within a minute of your leaving the UMRS project.)*

---

## 31.7 Run work

> **Status (2026-11-09, B2):** one signed run (an intent) is **merged through #815**. A site's first live gated run is still a separate acceptance step; it must not be inferred from the merge. Grants (*Unattended work* below) are now built and reviewed repo-side with wave C/D, but have no C/D commit, merge or deploy yet. *(The earlier draft of this section showed `--site ai4s` and a version inside the workflow name; the text below follows the wave B2 contract.)*

**Which account a run uses.** On a site that offers `user_account`, a workflow run uses your own account when both hold:
- your account at the site is `active` (§31.5);
- you have an active link for the run's project there (§31.6).

The project is the one your gateway verified. An account or link that exists but is not active refuses the run with `not_enrolled` or `allocation_not_linked`. Without an account or link, the site's default mode decides, as before (§31.10). UMRS decides this before anything runs, and never switches to another account after a refusal.

While your own account is used for a project at a site, only workflow steps run there. A one-off script (`hpc_submit`) or a cluster loop for that project is refused with `hpcaccess.lane_not_gated`. It is not run under the maintainer's account instead.

### Per run (an intent) *(wave B2)*

```bash
r4 workflow trigger my-workflow --project my-project --site bdrt --params-file params.json
r4 workflow trigger my-workflow --project my-project --site bdrt --show-inputs   # list every input in the summary
```

- `--site` names the site as `r4 hpc status` does. Without it, r4 starts the same steps when UMRS answers a plain trigger with `hpcaccess.signature_required`.
- Signing needs you at a terminal. The command refuses inside an agent's companion shell and without a terminal. When an agent tries `r4 workflow trigger … --site` through its shell, chat/plan asks first; autopilot logs and continues the local permission step, but signing and the companion-shell refusal still require you (2026-11-09, ADR-104 amendment 3).

**What r4 does before you sign.**
1. **It asks UMRS for a proposal.** The site answers with the intent it proposes for this run.
2. **It reads the code itself.** r4 downloads the export of the exact workflow version the proposal names and hashes every file itself. It reads the archive in memory and refuses one with directories, duplicate names, links or unsafe paths. It never takes a digest from UMRS or from the archive's own `EXPORT.json`.
3. **It recomputes the rest.** From the archive's `workflow.json` and your parameters, r4 recomputes the step names and order, which steps each one waits for, which earlier outputs each one reads, and each step's configuration. It uses the same shared code as the server.
4. **It compares, and refuses with a diff, signing nothing,** when any of these differ:
   - the code files, the entrypoints, the steps, their order or their configuration;
   - a step's declared input prefixes;
   - the site, or your UMRS user name;
   - your login and allocation, compared with the site's record and with what r4 recorded at `r4 hpc add`;
   - the parameters, which must be exactly what you passed plus the project id and the input files r4 sent;
   - an input you listed yourself, changed or relabelled;
   - the site profile, compared with the one you installed (the message tells you to run `r4 hpc profile <site>`).

   r4 also refuses an intent valid for more than one hour, an issue time more than five minutes from your computer's clock, or a total run time longer than the steps' time limits plus the site's queue allowance.
5. **It shows the summary** (below) and asks `y/N`. `--yes` skips only this question. Your key still needs your touch, or your PIN or passphrase. A plain `r4 workflow trigger` (without `--site`) that learns from the site that the run needs your signature asks this question even with `--yes` *(2026-11-08 review)*.
6. **It signs the intent** with your signing key and triggers the run with the intent and the signature. It prints the execution id and the intent id.

**What r4 shows.**

| Part | Shown |
|---|---|
| **The run** | site, your login, allocation, project, `workflow@version`, run id, when the intent expires (your local time), and how long after the first step starts later steps may still start |
| **The trusted platform image** | its reference and the first 12 characters of its image id. It prepares the container's system-call filter and, for a step that uses a model endpoint, runs the forwarder |
| **Each step** | entrypoint; image reference and image id (12 characters); partition, CPUs, memory, GPUs and time limit; model endpoint (host, port and kind, marked *chosen by UMRS, answers not pinned*) or "no network"; the configuration keys that differ from the defaults in `workflow.json`; and its inputs, each with who chose it |

Each input carries one of three labels:

| Label | What it means |
|---|---|
| **chosen by you** | a file you named in this command; r4 computed its digest from bytes it read itself |
| **data chosen by UMRS** | an object the workflow's own input plan names that you did not list, including every object under a declared prefix at that moment. UMRS hashed it when it proposed the intent, and the gate refuses it if it changes before staging |
| **outputs of step `<p>`** | the outputs of an earlier step of this run, exactly as the gate recorded them when that step ended |

With more than 20 inputs, the summary shows counts and bytes per label; `--show-inputs` lists every one. The summary ends with: "UMRS stores nothing that can act as you without your signature. Signing approves this one run as shown: the code and every listed input are pinned by digest, outputs of earlier steps are pinned by your gate when it collects them, and inputs marked 'data chosen by UMRS' were chosen by UMRS, not by you. Outputs that pass through UMRS are visible to its operators, and the same team publishes r4 and the gate." When a step reads a model endpoint, it adds: "Steps marked 'endpoint' also read answers from a model endpoint UMRS chose; those answers are not pinned." *(2026-11-08 review: the sentence claimed more than is pinned.)* For a single run, nothing is standing: the data UMRS chose is the list shown above, each object by its digest.

**What you sign.** One intent covers one workflow run at one site. It holds:
- the site, your UMRS user name, your login, the allocation and the project;
- the workflow name, its version and the version's registered digest; the run id; the run's input and output buckets; the parameters;
- the hash of the site profile it was proposed against, and the trusted platform image;
- the exact list of code files, each with its SHA-256 and size, and each step's entrypoint;
- each step: its name, the steps it waits for, its configuration, its image, partition, CPUs, memory, GPUs and time limit, its inputs by digest, and its model endpoint if it has one;
- a random nonce, the issue and expiry times, and the longest time the run may take.

**Time limits.** The first step must be submitted within one hour of the proposal (the gate allows two minutes of clock difference). Later steps may still be submitted until the run's time limit, counted from the first submit, has passed; that limit is at most seven days. A running job is never stopped because the intent expired.

**What the gate checks before each step.** It submits nothing unless all of these hold:
- the signature is yours, for this message type and site, with your touch (and your PIN where required);
- the site, your user name, your login and the run match;
- the intent has not expired;
- the step's partition and resources are within the site profile;
- Slurm lists the allocation for your login;
- every earlier step it waits for has finished and its reply and outputs are recorded;
- the code files in the work directory are exactly the signed list, with nothing extra;
- the input files are exactly the signed digests and sizes;
- each image archive has the signed digest, image id and platform labels;
- a model endpoint is offered only when the step names one.

Then it writes the step's input file and the batch script itself, seals the work directory, and submits. Each step of an intent can be submitted once; a second attempt is refused with `replayed`. Every submit is a line in the gate's log, with the full intent id.

**How the step runs.** The job runs under your login and is charged to your allocation. The step runs in the same locked-down container as on the platform's own lane: no network (except a socket to the named model endpoint), code and inputs read-only, and nothing of the login session's environment. The job copies the image archive to a private file, checks its digest and loads that same copy, so nobody can swap the image between the check and the load.

**When a step fails.** UMRS does not retry a step on your account automatically. If the connection drops, UMRS first asks the gate whether the step was already submitted, and follows that job instead of submitting again. To run again, trigger again; that signs a new intent.

**Which workflows can run this way.** Every step must run on the HPC step lane. A workflow with steps on another lane, or with a manual approval step, is refused with `hpcaccess.intent_invalid` (`mixed_lanes`). In wave B2, a step that reads a model endpoint (it declares `requires` or `model_type`) is refused with `endpoint_not_supported`, and a run whose steps need partitions of different CPU architectures is refused with `mixed_arch`, both before anything runs. A step may not be named `intent`. *(2026-11-08 review: what this section says about model endpoints and the forwarder applies once a later wave proposes endpoints.)* In a project that requires plan approval, your signed intent is the approval: the signed trigger runs without a separate plan. Parameters and step settings must be plain JSON values: text, whole numbers, decimal numbers, true or false, null, lists and objects. If UMRS cannot hash the inputs it chose within five minutes, it refuses with `inputs_too_slow`; list the files yourself (`--listed-input STEP s3://BUCKET/KEY FILE`: r4 hashes your local copy and the summary labels it *chosen by you*), or split the data.

**Where the files go.** The gate keeps each step's work directory under `~/.umrs/gate/work/` in your HPC home, and removes it after the job ends. Inputs can be large. You may make `~/.umrs/gate/work` a symbolic link to a directory you own on a scratch filesystem, with mode 0700; the gate follows that one link and no other. Every directory above that target must be one that only root or you can change. A shared group directory without the sticky bit is refused, because another member could rename your directory and put theirs in its place: make your own directory in the group space first (mode 0700) and point `work` at a directory inside it. *(2026-11-08 review)* The gate keeps a copy of each signed intent at `~/.umrs/gate/state/intents/<intent id>/intent.json`, for your own audit.

### Unattended work (a grant) *(built and reviewed repo-side in waves C and D; not deployed)*

> **Status (2026-11-09, waves C and D):** implemented and reviewed for sites that offer `user_account` (bdrt first), but **pending review on `wave/gated-cd-grants-ledger` (UMRS `22dffbd1`), unmerged and undeployed**. It needs gate version 3 (§31.5). It counts as working on a site only after the first non-maintainer grant-backed run there has a verified cancel and witness receipt. *(The earlier draft of this section showed `--site ai4s`, a version joined to the workflow name with a dash (`my-workflow-1.0`) and an `--any-input` option. The text below follows the waves C and D contract: versions are written `NAME@VERSION`, and the input rule is chosen with `--inputs`.)*

Agent loops, chained workflows and sweeps run while you are away. A **grant** is a bounded permission you sign once, for one site:

```bash
r4 hpc grant --site bdrt --project my-project --workflow my-workflow@1.0 \
   --max-jobs 20 --max-gpu-hours 40 --days 14
r4 hpc grant --site bdrt --project my-project --workflow my-workflow@1.0 \
   --max-jobs 50 --max-gpu-hours 100 --max-core-hours 2000 --max-concurrent 8 --days 7 \
   --inputs listed --listed-input data/a.h5 --listed-input data/b.h5
r4 hpc grants                     # list, with what each has used
r4 hpc grant-cancel <grant-id>    # end one early
```

| Option | Meaning |
|---|---|
| `--site S` | the site. **One site per grant** |
| `--project P` | the project; you need an active account at the site and an active link for `P` there (§31.5, §31.6). The grant's allocation is that link's allocation |
| `--workflow NAME[@VERSION]` | an exact workflow version; repeat it for up to 16. Without `@VERSION`, r4 uses the version UMRS proposes and shows it. A new version needs a new grant |
| `--max-jobs N` | 1 to 10 000. Every step the gate submits counts as one job, whether it succeeds or not |
| `--max-gpu-hours H` | 0 to 1 000 000 GPU-hours. With 0, no step that asks for a GPU can run |
| `--max-core-hours C` | CPU core-hours. The default 0 means **no** core-hour limit, and the summary says so |
| `--max-concurrent K` | how many steps may be unfinished at once; default 4, at most `N` and at most 64 |
| `--days D` | how long the grant lasts, 1 to 30 days |
| `--inputs listed\|chained\|any` | which stored data a run may read (below); default `chained` |
| `--listed-input PATH` | a file on your computer; r4 reads it and records its SHA-256 and size. Required with `listed`, optional with `chained`, refused with `any` |
| `--yes` | skips only the final question; your key still needs your touch, PIN or passphrase |

**Inputs.**

| `--inputs` | A run may read |
|---|---|
| `listed` | only the files you listed, by digest and size. UMRS picks which of them each run reads |
| `chained` (the default) | the files you listed, if any, and the outputs of runs under this grant, exactly as your gate recorded them when it collected them. UMRS picks among them. With nothing listed, the first run of the grant can read no stored data at all, so a loop that starts from existing data needs `--listed-input` |
| `any` | any data UMRS stages. Choose this only if you accept that UMRS chooses the data; r4 then shows the sentence from §31.3 |

In every mode, a step may read the outputs of earlier steps of the same run. A parameter named `input_files` only names data; the gate checks the bytes actually staged against the rule above.

**What r4 does before you sign.**
1. **It refuses inside an agent's companion shell and without a terminal.** When an agent tries `r4 hpc grant` or `r4 hpc grant-cancel` through its shell, r4 always asks you first and never approves it automatically.
2. **It checks your account and link** for the project at the site.
3. **It reads the code itself.** r4 asks UMRS for a proposal, downloads the export of each exact workflow version, and recomputes the code manifest's digest and the digest of the step graph and step configurations, as for an intent. It refuses with a diff, signing nothing, when either differs from the proposal.
4. **It checks the images.** Each image UMRS proposes must be one a step declares in the exported `workflow.json`, or the trusted platform image. Otherwise r4 refuses.
5. **It builds the parameter bounds** from the parameters each workflow declares, and refuses to sign, naming the parameter, when a workflow could pass code, paths or commands through one:
   - a type other than text, whole number, decimal number or true/false (objects, lists, null, a missing type or several types are refused);
   - a schema keyword other than `type`, `enum`, `minimum`, `maximum`, `maxLength`, `description`, `title` and `default` (for example `format`, `pattern`, `items`, `$ref` or an `x-…` marking);
   - a name that contains, as a word between underscores, `code`, `script`, `source`, `cmd`, `command`, `exec`, `shell`, `eval`, `path`, `file`, `filename`, `dir`, `directory`, `url`, `uri`, `endpoint`, `host`, `prompt`, `template`, `completion`, `response`, `model_output`, `entrypoint`, `module`, `import` or `plugin`; the names `api_base`, `base_url` and `image_param`; and any name that starts with `_`;
   - a decimal number with a list of allowed decimal values. Decimal bounds are narrowed to whole numbers (the minimum up, the maximum down) and shown; r4 refuses when nothing is left between them;
   - two workflows of the grant that declare the same parameter differently (give them separate grants).

   A text parameter without a declared maximum length gets 1024 characters, and the summary shows it. Each workflow in the grant receives only the parameters it declares itself.
6. **It warns about the budget.** When the project has a GPU-hour budget (§31.8) and your `--max-gpu-hours` is more than what remains, r4 warns that UMRS will refuse the grant.
7. **It shows the summary** and asks `y/N`:
   - the site, your login, the allocation and the project;
   - each `workflow@version` with its two digests, and the images (reference and the first 12 characters of the image id);
   - each parameter with its bounds; each text parameter without a list of allowed values is marked *free text UMRS chooses*;
   - the limits in hours, the job count and the concurrency, and when the grant expires (your local time);
   - the input rule, worded per mode: "only the N files you listed" (`listed`); "only the N files you listed and outputs of runs under this permission, which UMRS picks" (`chained`; without the first part when you listed nothing); the sentence "Under a standing permission you give, UMRS chooses the data your approved workflows run on." (`any`);
   - in every mode: "UMRS stores nothing that can act as you without your signature.", the two limits of that promise (outputs that pass through UMRS are visible to its operators; the same team publishes r4 and the gate), the *UMRS decides* and *UMRS cannot* lists below, and that a run still going when the grant expires stops there.
8. **It signs the grant** (setting its times again just after you answer, so a slow answer does not make it stale), keeps a record of it under `~/.config/r4/hpc/grants/<grant id>.json` (site, hash, expiry and limits, which the witness uses, §31.5), and sends it to UMRS.

**What you sign.** One grant holds:
- the site, your login, your UMRS user name, the allocations (1 to 16) and the projects (1 to 16) it may be used for;
- each workflow's name and version, the digest of its code manifest, the digest of its step graph and step configurations, and the names of the parameters it declares;
- `images`: the step images (reference, image id and archive digest), plus the helpers for the common allow-list; and `trusted_images`: the independently pinned helper/posture images. Each helper appears in both, so no role substitution is possible;
- the parameter bounds;
- the input rule and, for `listed` and `chained`, the digests and sizes of the files you listed;
- the limits: jobs, GPU-hours, core-hours and concurrent steps;
- when it was issued, when it starts and when it expires (at most 30 days later), that it cannot be passed on (`delegable: false`), a random nonce, and the grant id, which is also what a cancel names.

**What UMRS checks when it stores the grant.** It refuses with `hpcaccess.grant_invalid`, naming the reason, unless: the grant is well formed and was issued within five minutes of UMRS's clock; it names this site, you and your verified login; your gate is version 3 or later; every project has an active link of yours and you are a current member; every allocation is one of those links'; every workflow version is registered, visible to you, and has the digests r4 signed; every image is one the site publishes; and you have at most 32 active grants at the site. It checks the project budget (§31.8) and then asks your gate to check the grant (`grant-check`) before it keeps it.

**How a run uses a grant.** When a run needs your account (*Which account a run uses*, above) and carries no signed intent, UMRS looks for an active grant of yours at the site that covers it: the run's project, its workflow version with matching digests, and your link's allocation, and that still covers what the site publishes now.
- With exactly one, UMRS uses it.
- With none, the run is refused with `hpcaccess.signature_required`, as in wave B2: sign the run (`r4 workflow trigger … --site`) or give a grant.
- With two or more, the run is also refused with `hpcaccess.signature_required`; the message lists their ids, and the trigger must name one (its `grant_id`). UMRS never picks one for you and never falls back to another account.
- For each run, UMRS composes a **run spec**: the same content as an intent (§31.7, *What you sign*), but unsigned. The gate checks it against your grant at every step. Once a run's first step is submitted, its run spec never changes.
- An agent loop or a scheduled run definition chooses its grant once and keeps it. When that grant ends, the loop stops with `grant_ended:<grant id>`; it does not move to another grant.
- In a project that requires plan approval, a run under a grant still needs its plan and commit: a grant is not an approval of each run, unlike a signed intent.
- A grant covers workflow steps on the SSH step lane only. A one-off script or a cluster loop for a project that uses your account is still refused with `hpcaccess.lane_not_gated`. Grants do not cover steps that read a model endpoint.

**What the gate checks at each step under a grant.** Everything §31.7 lists for an intent (resources, allocation, earlier steps, code files, inputs, images), and also:
- the grant's signature is yours, for grants at this site, with your touch (and your PIN where required); the site, your user name and your login match;
- the run's allocation and project are in the grant; its workflow version, code and step graph have the signed digests; each step's configuration is what the workflow defines for these parameters; every image is in the grant; no step reads a model endpoint; the parameters are within their bounds; the stored data each step reads passes the input rule;
- the grant has not been cancelled, was not issued before a reconnect (§31.5), and is within its time window;
- the limits, counted by the gate itself in your HPC home under a lock, so two submits at the same moment cannot both take the last job or the last GPU-hour:
  - **jobs:** each submitted step counts once;
  - **concurrent:** the steps that the gate has not yet settled;
  - **GPU-hours and core-hours:** at submit, each step reserves the most it can use: its time limit rounded up to whole minutes, as Slurm enforces it, times its GPUs (or CPUs); on a partition that gives each job a whole node, the node's GPUs and CPUs. When the job has ended, the gate reads its elapsed time and allocation from `sacct` and charges the actual measured usage, including allocation above the reservation when Slurm gave the job more CPUs or GPUs. A job that `sacct` cannot report is charged its reservation, once its time limits guarantee it has ended;
- each step of a run can be submitted once (`replayed`), even after the gate has tidied that run's files away.

When the concurrency limit refuses a step, UMRS waits and tries again on the same work directory (after 30 seconds, doubling up to 5 minutes) until the step's queue timeout. The other limits end the run.

**Time.** A grant lasts at most 30 days. The gate refuses a grant issued more than two minutes ahead of its clock. After the grant expires, no further step starts: a run still going at expiry stops there, though a job that is already running finishes. The run's own time limit is at most the grant's length.

**What UMRS still decides under a grant.** This list is part of every grant summary.

| UMRS decides | UMRS cannot |
|---|---|
| whether and when to start runs, up to your limits | run code other than the listed workflow versions, or images other than the listed ones |
| the parameter values, within the bounds shown | change the pinned step graph or step configurations |
| the data, by the input rule you chose | call a model endpoint (none under a grant in this version) |
| each step's partition, CPUs, memory, GPUs and time limit, within the site profile you installed; only the hour limits bound them | exceed the limits on jobs, hours or concurrent steps |
| which of the listed allocations and projects a run uses | run anything after the grant expires, after a cancel, or after you reconnect a revoked account |

UMRS also sees every output that passes through it.

**`r4 hpc grants [--site S] [--json]`** asks each site (or `S`) to check your active grants with your gate and lists, for each: its id, status, workflows, limits against what it has used (jobs; GPU-hours used and reserved; core-hours), expiry and input rule. A grant that no longer covers anything (a workflow version whose digests changed, an image the site no longer publishes, or a link that is no longer active) is shown with the reason and a hint to sign a new grant. It is read only and may run in an agent's shell.

**`r4 hpc grant-cancel ID [--site S] [--direct] [--ssh HOST]`** signs a five-minute cancel request that names the grant. Without `--site`, r4 takes the site from its record of the grant; `--ssh` defaults to that site's name.
- UMRS stops using the grant at once, and the gate refuses it from then on. The gate keeps the cancel for 400 days, longer than any grant can last. Jobs that are already running are not stopped; cancel them with `r4 hpc cancel`.
- A relay completes only after the exact cancel it claims is found at the same head of the gate log read over your own SSH. If that evidence is missing, `--direct` delivers a fresh control directly to your gate and requires its receipt; `--ssh HOST` selects the physical destination when necessary.
- With neither proof, the command exits nonzero and says whether UMRS's mirror is pending or unconfirmed (`r4 hpc grants` can show `gate_cancel: pending`). Retry, or use `--direct`, rather than assuming an acknowledgement alone made the gate cancel durable.

**A grant also ends** when it expires; when you revoke your account at the site (UMRS ends all your grants there, and a later reconnect makes them invalid at the gate too, §31.5); and when you replace your signing key in `allowed_signers`, because the gate checks the grant's signature at every step.

**The agent cannot sign for you.** `r4 hpc sign` and `r4 hpc grant` are not available as agent tools, and they always need your touch or confirmation. *(2026-11-08, wave B2: a single run is signed by `r4 workflow trigger … --site`, which refuses in the agent's companion shell and without a terminal; through the shell, chat/plan asks first while autopilot only auto-answers the local prompt; it never signs for you. `r4 hpc sign` and `r4 hpc grant` are planned.)* *(2026-11-09, waves C and D: `r4 hpc grant` and `r4 hpc grant-cancel` are built repo-side on `wave/gated-cd-grants-ledger` (UMRS `22dffbd1`), pending review and unmerged/undeployed, with the same guards as above. A grant is never widened or extended: a larger or longer permission is a new grant, which needs your signature, and so your touch, PIN or passphrase. There is no `r4 hpc sign`: a single run is signed by `r4 workflow trigger … --site`.)*
## 31.8 Accounting

- The centre bills **your** allocation, and its own accounting (`sacct`) shows **you**.
- UMRS records which UMRS project each run belongs to, plus the run's allocation, Slurm job id, intent or grant, and the gate's log position. `r4 hpc usage --project my-project` sums usage per site, allocation and member in the repo-side wave-D implementation. The gate reconciles the figures from `sacct` for your own jobs.
- A project's UMRS GPU-hour budget, where one is set, is checked before submit. A grant can never exceed what remains.
- *(2026-11-08, wave B2.)* From wave B2, each step that runs under your account records its intent id, its Slurm job id and the gate's log position. UMRS reads the step's elapsed time and GPUs through the gate's `usage` verb, which answers only for jobs the gate itself submitted. The usage ledger, `r4 hpc usage` and the budget check before submit come with wave D.
- *(2026-11-09, wave D: the second and third points above are made precise below. The figures come from the gate's `usage` verb for jobs under your account and from `sacct` on the platform login for the others. A grant is checked against what remains of the budget when you give it, but it does not reserve budget; see *Budget*.)*

> **Status (2026-11-09, wave D):** the ledger, reports and budget check below are built and reviewed repo-side, but **pending review on `wave/gated-cd-grants-ledger` (UMRS `22dffbd1`), unmerged and undeployed**. The examples describe the implemented interface, not a live deployment claim.

### The usage ledger *(wave D)*

UMRS keeps an **append-only usage ledger** of the user work it submits to HPC sites: workflow steps, one-off scripts (`hpc_submit`) and cluster loops, both under your own account and under a site's platform login. Platform services (serving endpoints, the service relay, the janitor, the reaper, health probes) are not user work and are not recorded.
- **A row per change.** Each row holds the person, the project, the site, the login, the Slurm allocation, the access mode and method, the lane, the intent or grant id, the Slurm job id and job name, the gate's log position, the times, the CPUs, GPUs and elapsed seconds, the core- and GPU-hours, and the source of the figures (`estimate`, `gate_usage` or `sacct`). No row is ever changed or deleted; a correction is a new row, and reports read the latest row of each attempt. Your rows stay when your account is revoked or your UMRS account is removed.
- **Two phases.** Before a job is submitted, UMRS writes a `pending` row with an **estimate**, computed the way a grant reserves (the time limit rounded up to whole minutes, the whole node on a partition that gives each job one). If required pricing is missing or malformed, or UMRS cannot write that row, nothing is submitted: the run fails with `hpcaccess.ledger_unavailable`, never a lower non-exclusive estimate. After the submit, it adds the Slurm job id and, under your account, the gate's log position. When the job has ended, it records the elapsed time and the allocated CPUs and GPUs: through your gate's `usage` verb for jobs under your account, from `sacct` on the platform login for the others. Amounts are counted in whole GPU-seconds and core-seconds; the hours shown are those divided by 3600.
- **One-off script estimates.** For `hpc_script`, resources come from its `sbatch_args`: Slurm walltime syntax, CPU/GPU count and multiplicities, and the selected partition profile for exclusive-node K4. UMRS does not read the shell script's body to infer resources. If the arguments leave GPU-time unbounded, a capped project refuses with `hpcaccess.budget_exceeded`; naming valid time and GPU flags is the fix.
- **Recovery.** Every five minutes UMRS looks at this site's open rows. A row still `pending` after 15 minutes is resolved: the gate (or `sacct`, by job name) says whether a job was submitted. Rows of jobs that have ended are settled. If your gate cannot be reached, or your account was revoked, the estimate stays, keeps counting as reserved, and is shown as an estimate. A row still not settled seven days after its time limit gets a final `unsettled` row at its estimate, which counts as used; a later reading from the site may still correct it.
- **What it is not.** The centre's own accounting is what bills your allocation. The ledger is UMRS's own record, used for reports and budgets; it can differ from the centre's figures, for example where an estimate stands in for a reading the site could not give.

### Usage reports *(wave D)*

```bash
r4 hpc usage                                  # your own jobs at every site
r4 hpc usage --site bdrt --since 2026-11-01 --witness [--ssh HOST]   # one site plus gate-log evidence
r4 hpc usage --project my-project --site bdrt --witness [--ssh HOST]  # project rows plus evidence
r4 hpc usage --project my-project --json
```

- Without `--project`, r4 merges your own rows from each site (or from `--site`), with totals: jobs, GPU-hours, core-hours, and what is still reserved.
- With `--project`, r4 shows the project's report from `--site` or your default gateway, including the budget line: the budget, used, reserved and remaining GPU-hours. The budget figures are lifetime and cover all sites; `--since` filters only the rows and totals.
- On a personal usage endpoint, `limit` caps only the **rows** returned. Totals continue to cover every matching current row under the same site, person, project and time filters, so a page is never mistaken for the whole usage total.
- `--witness` reads the gate log directly (or through `--ssh HOST`) and refuses missing/under-reported submits or charges in the returned ledger rows. With `--project`, name `--site` explicitly so the evidence comes from the same site.
- **Who sees what.** Members see their own rows and the project's totals (the budget is shared, so the totals are not private). The project's owners also see each member's totals and rows, but without the other member's login, Slurm job id, job name, intent id, grant id and gate log position, which identify that member's own HPC account and audit trail. Platform administrators see every column. A former member sees nothing of the project.
- `r4 hpc usage` is read only, and may run in an agent's shell.
- Administrators: `umrs-server admin site-accounts usage [--venue V] [--project P] [--person U] [--since ISO] [--json]` (§31.12).

### Budget *(wave D)*

A project may have a GPU-hour budget (`max_gpu_hours`), set by its owner or an administrator; 0 means no budget. Before wave D the budget was only recorded; from wave D, UMRS enforces it for user work on HPC sites.
- **Lifetime, across sites.** The budget has no period: it counts everything the project has used since the ledger started. All sites of this UMRS share one database, so usage at every site counts against the same budget.
- **What counts.** *Used* is the GPU-hours of settled jobs, plus `unsettled` jobs at their estimate. *Reserved* is the estimates of jobs that are pending or submitted and not yet settled. A step may start only if used + reserved + its own estimate ≤ the budget. UMRS compares whole GPU-seconds, so a budget of 0.1 GPU-hours admits exactly 360 GPU-seconds.
- **Where it binds.** UMRS checks the whole run's estimate when the run is admitted, but that check reserves nothing, so two runs admitted at the same moment can both pass it. The check that binds is the one before each step's submit, made under a lock with the `pending` row: a step refused there fails with `hpcaccess.budget_exceeded` (the detail shows used, reserved, this step and the budget), is not retried, and its run stops.
- **Grants.** UMRS refuses to store a grant whose GPU-hour limit is more than what remains of the budget at that moment (`hpcaccess.budget_exceeded`), and r4 warns before you sign. A grant does not reserve budget: two grants that each fit can together exceed what remains, and then the per-step check is what stops the work.
- Only GPU-hours are budgeted. Work that belongs to no project is not checked.

---

## 31.9 Revoke, rotate, leave

| You want to | Do |
|---|---|
| Stop UMRS from reaching a site | `r4 hpc revoke bdrt` *(available from wave B1 on sites that offer `user_account` (bdrt first); running jobs under your account comes with wave B2)*. UMRS destroys its restricted key for you at that site and marks your account there `revoked`; r4 then names the `umrs-transport-<site>-<key id>` line to delete, or with `--remove-line --ssh <ssh-destination>` deletes it for you over your own SSH. Deleting the `umrs-transport-…` line from `~/.ssh/authorized_keys` yourself always works, with no help from UMRS. In wave B2, before the key is destroyed, UMRS asks your gate to cancel your jobs there that are still queued or running, and says how many it cancelled; if the gate cannot be reached, they end at their time limit *(2026-11-08 review)*. *(2026-11-08: this row said that in B1 you approve a signed `revoke` request; in B1 no UMRS route carries a signed request to the gate yet, see below)* |
| End one grant | `r4 hpc grant-cancel <grant-id>`. *(2026-11-09, waves C and D: UMRS stops using the grant at once and relays your signed cancel to the gate. If the gate cannot be reached, run the command again later until `r4 hpc grants` no longer shows `gate_cancel: pending`. Running jobs are not stopped (§31.7))* |
| End all grants at a site *(added 2026-11-09)* | `r4 hpc revoke <site>`: UMRS ends every grant of yours there. If you reconnect later, `r4 hpc add` makes the gate refuse every grant issued before the reconnect (§31.5). Replacing your signing key in `allowed_signers` also ends every grant signed with the old key |
| Replace UMRS's restricted key | `r4 hpc rotate bdrt` *(available from wave B1 on sites that offer `user_account` (bdrt first); running jobs under your account comes with wave B2)*. r4 asks you first: nothing changes until you say yes. UMRS then creates a new restricted key (your account waits for it); r4 shows the new line, replaces the old line over your own SSH (or prints both with `--manual --home <dir>`), and asks UMRS to verify the gate on the new key, after which UMRS drops the old one. If the SSH edit fails after UMRS made the new key, r4 prints the exact swap to make by hand. UMRS keeps at most two restricted keys for you at a site: the current one and the one it replaces. *(2026-11-08: in wave B1, `rotate` replaces UMRS's restricted key, not your signing key, and it is done over your own SSH rather than by a signed request)* |
| Replace a lost or new signing key | in wave B1: run `r4 hpc add bdrt` again, which logs in as you and rewrites `allowed_signers`, or edit `~/.umrs/gate/allowed_signers` yourself. A signed change of the signing key, made with the old key, is planned |
| See everything that happened | read `~/.umrs/gate/log/` at the site. UMRS cannot change it. *(2026-11-08, wave B2: `r4 hpc status <site> --witness` checks that log for you and flags any intent r4 did not sign (§31.5); the gate keeps each signed intent under `~/.umrs/gate/state/intents/`)* |

When your UMRS account is disabled, UMRS destroys its restricted keys for you at each site within about a minute, even when nothing of yours is running, and marks your accounts there `revoked`. The lines in your `authorized_keys` then do nothing, but you can delete them too. *(2026-11-08, wave B2: before it destroys a site's keys, UMRS asks your gate there to cancel your jobs that are still queued or running. If the gate cannot be reached, those jobs still end at their time limit.)* *(2026-11-08 review: before this fix, the server only noticed a disabled account that still had something running.)*

**Signed requests in wave B1** *(added 2026-11-08)*. The gate installed in B1 already acts on two signed requests, `revoke` and `rotate`, and refuses them unsigned; no UMRS route sends them yet, so in B1 `r4 hpc revoke` and `r4 hpc rotate` change your `authorized_keys` over your own SSH instead. Each one names the site, your UMRS user name and the key id, is valid for at most 10 minutes (the gate allows two minutes of clock difference at each end, and judges the time when the request arrives), and can be used once: the gate keeps a file for every request it has accepted and refuses a second use with `replayed`. The gate refuses a request whose key id matches no line in your `authorized_keys` with `no_matching_key`, and a `rotate` to a key that another line or your `allowed_signers` already holds with `bad_schema`. The gate also protects your HPC home: it ends any session after 60 seconds, waits at most 10 seconds for its log, logs a request without a valid signature as one short fixed entry (and no longer once the log reaches 8 MiB), and never leaves a half-written log entry behind.

---

## 31.10 Sites

| Site | Your own account | Notes |
|---|---|---|
| rccs | waiting for the operator's agreement | jobs run under the maintainer's account, for listed projects only |
| ai4s (RIKYU) | waiting for R-CCS | keys are registered in the RIKYU Portal; whether `command=` options survive is being asked |
| bdrt | first site to be built and tested | maintainer's own account first. *(2026-11-08: wave B1 turns `user_account` on in this site's configuration; you can connect here once the maintainer deploys it. Jobs still run under the maintainer's account until wave B2)*. *(2026-11-08, wave B2: once B2 is deployed and a first run has passed here, workflow runs of a project you linked here run under your own account (§31.7); other runs keep the site's default mode)* |
| octopus | waiting for the operator's agreement | — |
| Fugaku | not possible today | third-party use of accounts is prohibited; a centre API may come later |
| Sites with a one-time code on every SSH login | only while r4 is connected (`client_relay`, planned) | unattended runs are impossible there |

> **Status (2026-11-08):** the project allow-list is enforced in code from wave A of [ADR-112](../adr/112-user-hpc-accounts-gated-delegation.md) (merged, not yet deployed: it takes effect on each site when the maintainer deploys it). Until then the old behaviour holds. After it: on rccs and ai4s, user work under the maintainer's account runs only for the listed projects; on bdrt and octopus the list is empty, so only admins can run user work there. Platform services are unaffected.

---

## 31.11 When a run is refused

The gate and UMRS refuse by name. UMRS never quietly runs your job under someone else's account instead.

| Name | Meaning | What to do |
|---|---|---|
| `not_enrolled` | you have not connected this site | `r4 hpc add <site>` |
| `allocation_not_linked` | the project has no allocation of yours at this site | `r4 hpc link …` |
| `allocation_not_at_site` | the site no longer lists that allocation for you | check with the site; relink |
| `project_not_allowed` | the site's maintainer-account lane is limited to listed projects. The check uses the project your gateway verified. A run submitted over the ZeroMQ channel carries no verified project, so it is refused there; use `r4 workflow trigger` (HTTPS) | connect your own account, or ask the maintainer |
| `not_signed` / `bad_signature` | the request was not signed by your key | trigger again from r4; check `r4 hpc status`. *(2026-11-08, wave B2: an unsigned run is refused by UMRS as `hpcaccess.signature_required`; see the wave B2 table below)* |
| `digest_mismatch` / `manifest_mismatch` | what UMRS staged is not what you signed | report it: this is the gate doing its job. *(2026-11-08, wave B2: the gate's names are `digest_mismatch`, `src_mismatch` for code and `input_mismatch` for inputs; there is no `manifest_mismatch`)* |
| `grant_exhausted` / `expired` | the grant's limits or time ran out | create a new grant. *(2026-11-09: the gate's name for an expired grant is `grant_expired`; see the waves C and D tables below)* |
| `replayed` | the same signed request was used twice | trigger again |
| `sealed_workdir` | something tried to change a submitted job's files | report it. *(2026-11-08, wave B2: the gate's name is `sealed`)* |
| `gate_version` | the installed gate is not the release UMRS serves, or is older than it needs | `r4 hpc add <site>` again (logs in as you and installs the current gate). *(2026-11-08: this row named an `--upgrade` option, which does not exist)* |

### Refusals while connecting, revoking or rotating (wave B1, added 2026-11-08)

UMRS reports a refusal from the gate as `hpcaccess.gate_refused`, with the gate's own name in the detail.

| Name | Meaning | What to do |
|---|---|---|
| `hpcaccess.mode_undeclared` | this site does not offer `user_account` | nothing on your side; see §31.10 |
| `hpcaccess.allocation_not_at_site` | the site did not list that allocation for you at the last check | `r4 hpc verify <site> --login <your login at the site>`, then link again; or check with the site |
| `hpcaccess.person_required` *(added 2026-11-08)* | the request came marked as an agent's, or from inside a loop or teamup work level; changes to your account are your own act | run the `r4 hpc` command yourself, in your own terminal |
| `busy` (in `hpcaccess.gate_refused`) *(added 2026-11-08)* | another verify of your account is still running | wait for it, then verify again |
| `wrong_site` (in `hpcaccess.gate_refused`, at verify) *(added 2026-11-08)* | the gate's `gate.json` names another site | `r4 hpc add <site>` again |
| `gate_probe_refusal` | the expected answer to UMRS's `probe`. Any other answer means the gate is not the program answering for UMRS's key, and the account is not activated | check that the `authorized_keys` line is the one r4 showed you; ask the site whether a site-wide SSH command overrides it |
| `presence_required` / `verification_required` | the signature carries no proof that you touched your security key, or no proof of your PIN where the site or you require one | sign again, touching the key and entering the PIN |
| `key_type_not_allowed` | the request was signed with a key that is not a security key, and you did not choose `--key-type ed25519` at connect time | sign with your security key |
| `wrong_site` / `wrong_principal` | the request names another site or another user | sign the request again from r4 |
| `expired` / `replayed` | the request is older than 10 minutes (two minutes of clock difference allowed), or was already used | sign the request again from r4 |
| `no_matching_key` | your `authorized_keys` has no line for that key id | nothing to revoke; check `r4 hpc status` |
| `too_large` / `bad_envelope` / `not_canonical` / `bad_schema` | the request is malformed | report it, with the r4 version |
| `unknown_verb` / `no_command` | something asked the gate for a command it does not have | report it if it was not you testing |
| `log_unwritable` | the gate could not write its log, so it did nothing; with the detail `busy`, another request held the log for more than 10 seconds | free space or fix permissions in `~/.umrs/gate/log/`; for `busy`, retry |

### Refusals while running under your account (wave B2, added 2026-11-08)

UMRS's own refusals:

| Name | Meaning | What to do |
|---|---|---|
| `hpcaccess.signature_required` | this run uses your own account, so it needs your signature | run `r4 workflow trigger … --site <site>` yourself, at a terminal (§31.7) |
| `hpcaccess.intent_invalid` | the intent does not match the request, the run or the site, or it has expired. The detail names the cause, for example `mixed_lanes`, `endpoint_not_supported`, `mixed_arch`, `non_json_value`, `inputs_too_slow`, `input_not_in_plan`, `input_changed`, `input_unreadable`, `run_id_not_issued`, `no_image_digests` or `too_large` | for `inputs_too_slow`, list the inputs yourself or split the data; for `mixed_lanes`, `endpoint_not_supported` and `mixed_arch`, the workflow cannot run this way yet (§31.7); for `no_image_digests`, ask the site's operator; otherwise trigger again |
| `hpcaccess.intent_used` | that intent or run id already has a run | trigger again; that signs a new intent |
| `hpcaccess.lane_not_gated` | a one-off script or a cluster loop for a project that uses your own account at this site, or a workflow on a step lane that submits from the UMRS host itself; in wave B2 only workflow steps on the SSH step lane run through the gate | nothing to do yet. UMRS does not run it under the maintainer's account instead |
| `hpcaccess.gate_version` (`gate_stale`) | the installed gate is older than the one UMRS serves | `r4 hpc add <site>` again |
| `hpcaccess.gate_version` (`site_profile_stale`, `site_profile_missing`) | your installed site profile differs from the one UMRS publishes, or is missing | `r4 hpc profile <site>` |
| `hpcaccess.gate_refused` (`submit_unknown`) | the gate could not tell whether Slurm accepted a step, so UMRS did not submit it again | check at the site that no job of that run is still running, then trigger again |
| `hpcaccess.gate_refused` (`unreachable`) | UMRS could not reach your gate while proposing the run (the connection dropped, or Slurm did not answer) | try again later |

The gate's refusals, reported as `hpcaccess.gate_refused` with the gate's name in the detail:

| Name | Meaning | What to do |
|---|---|---|
| `profile_mismatch` | the intent was proposed against another site profile than the one installed | `r4 hpc profile <site>`, then trigger again |
| `profile_unsafe` | a directory the profile names for Slurm commands, a directory above it, or a Slurm command itself is not owned by root (or a listed owner), is a link, or is writable by others | ask the site's operator; the gate will not run commands from it |
| `resource_not_allowed` | the step's partition, CPUs, GPUs, memory or time are outside the site profile | ask the workflow's author or the site's operator |
| `account_not_held` | Slurm no longer lists the allocation for your login | `r4 hpc verify <site> --login <your login at the site>`, then link again |
| `src_mismatch` | the code files in the work directory are not exactly the signed list | report it: this is the gate doing its job |
| `input_mismatch` / `digest_mismatch` | an input or an output is not the signed or recorded file. This also happens when data UMRS chose changed after you signed | trigger again; report it if it repeats |
| `parent_not_collected` | an earlier step's reply or outputs were not recorded by the gate | report it |
| `image_absent` / `image_mismatch` / `image_label` | the site's image archive is missing, differs from the signed image id, or is not a platform step image that takes a JSON payload | ask the site's operator (the image may not be staged yet) |
| `wrong_login` / `wrong_intent` / `wrong_step` | the request does not match your login, or the work directory it names | report it |
| `expired` | the first step was not submitted within an hour, or a later step came after the run's time limit | trigger again |
| `replayed` | that step of that intent was already submitted | trigger again |
| `sealed` / `broken` | the work directory was already submitted, or its staging failed; UMRS starts a fresh one after a failed staging | report it if a run fails with it |
| `busy` | another request held the work directory | UMRS retries; report it if a run fails with it |
| `too_many_workdirs` / `low_space` / `log_full` | too many work directories, too little free space where `~/.umrs/gate/work` lives, or the gate's log reached 64 MiB | free space or move `work` to scratch (§31.7); for `log_full`, report it |
| `not_finished` / `job_live` | the job has not finished, or is still running | wait |
| `bad_reply` | the step's reply is not a valid JSON object within the size limit | fix the step's code |
| `not_mine` | the job id is not one the gate submitted | report it |

### Refusals under a grant and the budget (waves C and D, added 2026-11-09)

UMRS's own refusals:

| Name | Meaning | What to do |
|---|---|---|
| `hpcaccess.grant_invalid` | UMRS will not store or use the grant. The detail names the reason: `bad_envelope`, `too_large`, `bad_schema` (malformed, or issued more than five minutes from UMRS's clock), `wrong_site`, `wrong_principal`, `wrong_login`, `not_linked` (a project without an active link of yours, or an allocation outside your links), `workflow_unknown`, `workflow_mismatch` (the version's digests differ from what you signed), `image_unknown`, `expired`, `cancelled`, `too_many` (32 active grants at the site), `conflict` (the same grant id with other content), or `not_covered: <field>` (the grant you named does not cover this run; *2026-11-09:* `not_covered: inputs: …` means UMRS could not read one of the run's inputs) | for `not_covered: inputs`, check the input's name and that the object exists; for `workflow_mismatch`, `image_unknown` and other `not_covered` details, sign a new grant (`r4 hpc grants` shows why an old one no longer covers); for `bad_schema` on time, check your computer's clock; for `too_many`, cancel grants you no longer need |
| `hpcaccess.grant_not_found` | the run names a grant that is not yours at this site | check the id with `r4 hpc grants` |
| `hpcaccess.signature_required` | no active grant covers this run, or more than one does (the message lists their ids) | sign the run (`r4 workflow trigger … --site`), give a grant, or name one of the listed grants |
| `hpcaccess.gate_version` (`grants_need_gate_3`) | your gate is older than version 3 | `r4 hpc add <site>` again (§31.5) |
| `hpcaccess.budget_exceeded` | the project's GPU-hour budget does not leave room for this step, run or grant | ask the project's owner to raise the budget, or wait for reserved work to settle (`r4 hpc usage --project …`) |
| `hpcaccess.ledger_unavailable` | UMRS could not safely price the job from required profile inputs, or could not write its pending ledger row; it did not submit rather than undercharging | for a site-profile problem, reinstall/report the profile; otherwise retry later and report if it repeats |
| `grant_ended:<grant id>` (a loop's stop reason) | the grant the loop was started under has ended | sign a new grant and start the loop again |

The gate's refusals, reported as `hpcaccess.gate_refused` with the gate's name in the detail:

| Name | Meaning | What to do |
|---|---|---|
| `grant_mismatch` | the run is not covered by the grant; the detail names the field: an allocation, project, workflow digest, step configuration, image, model endpoint, a parameter (`params: <name>: <why>`) or an input (`inputs: <bucket>/<key> was not produced under this grant`) | report it if you did not change the workflow: this is the gate doing its job |
| `grant_exhausted` | a limit would be exceeded; the detail is `jobs`, `concurrent`, `gpu_hours` or `core_hours` | for `concurrent`, nothing: UMRS waits and tries again. For the others, give a new grant |
| `grant_expired` / `grant_not_yet_valid` | the grant's time window has ended, or has not started (or was issued more than two minutes ahead of the gate's clock) | give a new grant; check your computer's clock |
| `grant_cancelled` | you cancelled the grant, or (detail `account re-enrolled`) it was issued before you reconnected your account | give a new grant |
| `grant_conflict` | the gate already holds other content under that grant id | report it |
| `run_spec_changed` | a later step of a run came with other run content than its first step | report it: this is the gate doing its job |

---

## 31.12 For site operators and administrators

- **Declared mode.** Each site declares `scheduler.hpc_access` in its `site.yaml`:
  - `user_account`: users' own accounts, through `gated_key`;
  - `service_account`: one account for everyone. This needs `consent_ref`, a dedicated login, and per-project limits.

  There is no silent fallback between them ([ADR-112](../adr/112-user-hpc-accounts-gated-delegation.md)).
- **Offering `user_account` (wave B1, added 2026-11-08).** Add `user_account` to `modes_allowed` and fill its block:
  ```yaml
  scheduler:
    hpc_access:
      modes_allowed: [service_account, user_account]
      default_mode: service_account        # stays so in B1: registration opens, nothing changes who runs what
      user_account:
        methods: [gated_key]
        signing_domain: <platform DNS name> # required; every signature namespace ends in @<signing_domain>
        from_address: "<address the site sees>"  # optional; comma-separated sshd from= patterns, no spaces or empty items; empty means no from= option
        uv_required: false                  # true requires the security-key PIN on every signature
        gate_min_version: 1
  ```
  The registration routes (`/umrs/sites/me/hpc…`) answer only where `user_account` is in `modes_allowed`; elsewhere they refuse with `hpcaccess.mode_undeclared`. They also refuse it when the site's `name` is not 1 to 32 lowercase letters, digits or dashes, because the name becomes the gate's `--site` and part of the key line, and the account tables hold at most 32 characters. *(2026-11-08 review: this said 1 to 40.)* UMRS keeps each person's restricted private key under `UMRS_TRANSPORT_KEY_DIR` (default `/var/lib/umrs/transport-keys`; directories 0700, files 0600), outside the database. It reaches the gate with `ssh -F none`: the host name, port, known-hosts files and jump host come from its SSH configuration for `extra.ssh_target`, but no identity does, so only the restricted key is offered (`IdentitiesOnly=yes`, `IdentityAgent=none`). The call uses `BatchMode=yes`, no shared connection, no agent or forwarding, and an environment of `PATH`, `HOME` and `LANG` only. A jump host is reached through the platform's own SSH, which forwards the connection and nothing else.
- **Interim allow-list.** On a site still using the maintainer's account, `hpc_access.service_account.allowed_projects` limits every user-work lane (staged steps, `hpc_script`, cluster loops) to admins and the projects listed; an empty list means admins only. *(Status 2026-11-08: enforced in code from wave A; merged, not yet deployed until the maintainer deploys it.)*
- **Running jobs under users' accounts (wave B2, added 2026-11-08).** A site that offers `user_account` also needs:
  - **A site profile.** UMRS publishes it from the site file: the step partitions and their profiles (architecture, and CPU, GPU and time maxima), the GPU and container settings, the runtime storage paths, the queue timeout, the stage size limits and the endpoint limits. An optional `hpc_access.user_account.profile:` block overrides matching keys. It must name `image_archive_dir`, a directory every user's login can read. Users review and install the profile with `r4 hpc add` or `r4 hpc profile`.
  - **Image digests.** The new optional key `step_executor.image_digests` names a server-local JSON file mapping each step image reference and architecture to its image id and archive SHA-256. The converge's image stage writes it and places each archive as `<image_archive_dir>/<archive_sha256>.tar`. Without it, intents are refused with `hpcaccess.intent_invalid` (`no_image_digests`).
  - **A rebuilt platform step image.** It carries the label `org.umrs.step-payload="pickle,json"`; the gate refuses an image without `json` (`image_label`).
  - **Slurm command directories owned by root.** Each directory in the profile's Slurm path must be owned by root (or by an owner the profile lists), must not be a link, and must not be writable by group or others; otherwise users' gates refuse with `profile_unsafe`.

  A person with an active account and an active link for the run's project runs workflow steps under their own login; everyone else follows `default_mode` as before. For such a person, the other user-work lanes (`hpc_script`, cluster loops) refuse with `hpcaccess.lane_not_gated` and never fall back to the platform login. Platform lanes (endpoints, relay, janitor, reaper, health) still run only under the platform login. The reaper and janitor reach gated jobs only through that person's gate, with the unsigned `status`, `cancel`, `rmwd` and `sweep` verbs, which act on that person's gate records alone.
- **Grants, the ledger and budgets (waves C and D, added 2026-11-09).**
  - Grants need gate version 3 on the person's account; nothing in the site file changes. UMRS keeps a mirror of each grant (its signed bytes, status and last reported usage) for admission and display; the gate's own counters are authoritative.
  - The usage ledger records every job of user work on the site, under users' accounts and under the platform login alike, in two phases around submit (§31.8). The table is append-only: the database itself refuses updates and deletes. Platform lanes are not recorded.
  - `umrs-server admin site-accounts usage [--venue V] [--project P] [--person U] [--since ISO] [--json]` lists ledger rows and totals with every column. It needs the database and says so clearly without it.
  - A project's `max_gpu_hours` is enforced for user work on HPC sites, lifetime and summed across all sites (§31.8). A project with 0 is not limited.
- **What the operator of an HPC centre is asked to accept.** A key held by UMRS, unique to one user, pinned by `restrict,command=…,from=…` to a gate in that user's account. The gate runs only containerised jobs whose code, image and files match a request the account holder signed. Under a standing permission, UMRS may choose the input data for the approved code. Every job runs under the holder's account and allocation, and the centre's own accounting attributes it.
- **Admin view.** `umrs-server admin site-accounts list|show [--venue <site>] [--json]` *(from wave B1; 2026-11-08: this line said `umrs admin site-accounts`, planned)* lists each person's site accounts, probe results, gate versions and hashes, and links. It shows public information only: no admin command can display or export a usable credential.

See also [Appendix D: Security Model](appendix-d-security.md) and [Chapter 20: User & Access Management](20-user-management.md).

---

## 31.13 Questions

**Does UMRS store my password or my SSH key?** No. It stores a restricted key that can only reach the gate, sealed at rest. Your login key and your signing key never leave your computer.

**If someone steals UMRS's restricted key, what can they do?** On their own, nothing beyond what an operator can do (§31.3). They also need the UMRS host's network address, because of `from=`, and your signatures for anything new.

**I lost my security key.** Your grants keep working until they expire. To make new ones, register a new key: in wave B1, run `r4 hpc add <site>` again, which logs in as you and rewrites `allowed_signers` (§31.9). *(2026-11-08: this answer named `r4 hpc rotate`; in B1 that command replaces UMRS's restricted key instead.)* *(2026-11-09, waves C and D: more precisely, your grants keep working only while the lost key stays in `allowed_signers`, because the gate checks each grant's signature at every step. Registering the new key in its place ends every grant signed with the old one. If the key may have been stolen, do that at once, or revoke the account (§31.9): whoever holds it could sign new grants.)*

**Can my agent run jobs while I sleep?** Yes, within a grant you gave it. It cannot create or widen a grant itself. *(2026-11-08: grants come with wave C. In wave B2, every run under your account needs your own signature at a terminal, §31.7.)* *(2026-11-09: grants are built and reviewed repo-side in waves C and D, but are not committed, merged or deployed. Within a grant, UMRS still decides when runs start, the parameter values within their bounds, the data by your input rule, and each step's resources; see §31.7, *What UMRS still decides under a grant*.)*

**Why not just use one shared account for everyone?** Most national centres forbid it, and it hides who did what. Where a centre does allow it, it is a separate mode with its own written consent.
