# Appendix D — Security Model

## Security Invariants

These are non-negotiable rules enforced at all times:

| Invariant | Rule |
|-----------|------|
| **Model download egress** | Model download jobs are the ONLY jobs with public egress. Inference containers must fail fast if network access is detected (`TRANSFORMERS_OFFLINE=1`). |
| **Download initiation** | Model downloads are always initiated via UMRS jobs, never automatically during inference provisioning. |
| **Gateway file fetch** | Gateway performs presigned URL fetches to centralize auditing, size enforcement, and PII control. |
| **Execution authority** | Every job has exactly one `execution_authority` (slurm\|k8s\|compose), chosen once, never changes. |
| **RAG inside mini-RiVault** | All RAG indexing and retrieval runs inside mini-RiVault containers. |
| **MCP inside mini-RiVault** | All MCP servers run inside mini-RiVault containers. |
| **Presigned URLs** | Never exposed to external clients — they exist solely for compute-node S3 access. |
| **HPC account custody** *(added 2026-11-08, ADR-112)* | UMRS holds nothing that can act in a user's own HPC account without that user's signature: the restricted key UMRS holds reaches only the gate in the user's HPC home. No lane ever retries under another identity. Platform lanes run only under the site's platform login. See [HPC account custody](#hpc-account-custody-adr-112). |

## EgressGuard

EgressGuard is a mandatory proxy layer for any mini-RiVault instance requiring network access.

### Architecture

```
MCP Server (inside mini-RV) ──▶ EgressGuard Proxy ──▶ Allowlisted Internet
                                 │
                                 ├── Content Scanner
                                 ├── Domain Allowlist
                                 ├── Rate Limiter
                                 └── Audit Logger
```

### Default Policies by Tier

| Tier | Default Egress | Admin Override | User Override |
|------|---------------|----------------|---------------|
| System | None (offline) | Can add domains | No |
| Project | None (offline) | Can add domains | Request via approval |
| User | None (offline) | Can add domains | Self-service with audit |

### Audit Log Format

```json
{
  "timestamp": "2026-02-15T10:30:00Z",
  "instance_id": "user-alice-abc123",
  "tier": "user",
  "owner_id": "alice",
  "request": {
    "method": "GET",
    "url": "https://api.example.com/data",
    "body_hash": "sha256:..."
  },
  "response": {
    "status": 200,
    "size_bytes": 1024
  },
  "action": "allowed"
}
```

## Container Isolation

Each Mini-RiVault instance is a self-contained security boundary:

| Mount | Path | Access |
|-------|------|--------|
| Models | `/models/` | Read-only |
| Project data | `/project/` | Read-only |
| MCP servers | `/mcp-servers/` | Read-only |
| RAG index | `/rag-index/` | Read-only |
| Scratch | `/scratch/` | Read-write (ephemeral) |

## Local tool sandbox (on your workstation)

*Added 2026-10-30.* Mini-RiVault covers what UMRS runs. A **local** agent session runs on
your own machine, and the processes its agent starts (shell commands,
Python, compile/run/test tools, language servers) and your local stdio MCP
servers run with your full permissions unless you turn on the local tool
sandbox. It is not a Mini-RiVault: it is r4's own wrapper, using
bubblewrap on Linux and Seatbelt (`sandbox-exec`) on macOS.

**Off by default, always announced.** Each local session says so in one line
and how to turn it on: `/sandbox on` in the console, `r4 --sandbox on` at
launch, `R4_SANDBOX=on`, or `r4 config sandbox on` to make it your default
(`r4 -p` and the REPL have no `/sandbox` command: use one of the other three).
Turning it on runs a self-test; if the backend is missing or the test fails,
it stays off (or, if your default is on, commands are refused) and the
message names the fix. It never quietly runs a command unconfined.

**What it confines, while on.** Only the processes the agent starts and
the local MCP servers and hatch runtimes listed below; r4 itself, including
its built-in file tools, is not inside it.

| Area | Rule |
|------|------|
| Writes | Only the workspace (the agent's workspace in a team plan), a per-session temp directory, and `sandbox_policy.extra_write_paths`. `.git` hooks and config, `.r4/config.toml`, `.r4/local.toml`, `.r4/hooks.json`, `.r4/mcp.json`, `.r4/mcp-servers.json`, `.github/mcp.json` and r4's own installation stay read-only even inside the workspace (`.r4/hooks.json` since 2026-10-30). A workspace that is or contains your home directory or `/tmp` is refused |
| Reads | Everything except the r4 config directory and credential stores: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`, `~/.config/gcloud`, `~/.kube`, `~/.docker`, `~/.config/gh`, `~/.netrc`, `~/.git-credentials`, `~/.pypirc`, `~/.npmrc`, the macOS keychains, X11 auth files, wherever `GH_CONFIG_DIR`, `DOCKER_CONFIG`, `KUBECONFIG`, `AWS_*_FILE`, `CLOUDSDK_CONFIG` or `GNUPGHOME` point, and `sandbox_policy.extra_deny_read_paths`. Nothing else is hidden |
| Environment | API keys, passwords, tokens, the SSH and GPG agent sockets, `DISPLAY` and the host's proxy variables are removed; `R4_GATEWAY_URL`/`R4_GATEWAY_TOKEN` stay; `sandbox_policy.env_keep` keeps more |
| Network | Closed, except r4's loopback proxy, which follows the session's web access (`/web`, `r4 --web`): Off = no network, Allowlist = the listed domains, Public = any public host. Private, LAN, loopback and cloud-metadata addresses are always refused; ports 80 and 443 only |
| Host sockets (Linux) | D-Bus, container engines (docker, podman), Kerberos, smartcard, libvirt and screen sockets and `/run/user/<uid>` are hidden; `/tmp` is private to the session |
| Local MCP servers | Wrapped by the r4 MCP daemon when any of your profiles has the sandbox on by default or any session turned it on. Each server's network comes from its own `"sandbox": {"web": "none" \| "allowlist" \| "public", "domains": [...], "write": [...]}` entry in `~/.config/r4/mcp-servers.json` (default `none`); `"sandbox": false` there exempts a server (ignored in a project's files) |
| Hatch runtimes | Filesystem and environment confined; they keep the host network because they must accept calls on loopback |

`sandbox_policy` lives under the profile in `config.json`, for example
`{"profiles": {"default": {"sandbox_default": "on", "sandbox_policy":
{"extra_write_paths": ["~/data/scratch"], "env_keep":
["TOKENIZERS_PARALLELISM"]}}}}` (the `TOKEN` rule removes that harmless
name too); keys at the top level of the file are ignored. `r4 config
sandbox on` sets `sandbox_default` for you. A repository's `.r4/`
files cannot set either key.

**What it does not do:**
- It approves nothing: the permission ladder asks as before.
- It does not confine r4 itself: the built-in file tools (`file_read`,
  `file_write`, `file_edit`, `apply_patch`, grep and glob search) run in
  r4 and are governed by the permission ladder, not by the sandbox.
- Project hooks (`.r4/hooks.json`, read when
  `R4_COMPANION_PROJECT_HOOKS=1`) run inside the sandbox while it is on,
  and an existing `.r4/hooks.json` is read-only (2026-10-30). Your own
  hooks (`~/.config/r4/hooks.json`) run outside it, as in Claude Code; a
  hook script they name by a workspace path stays writable by the agent.
- r4's own git calls (directory listings, the session probe) switch off a
  repository's hooks and fsmonitor, so a repository the agent made with
  `git init` cannot run code on your machine through them (2026-10-30). A
  git command you run yourself in such a repository runs its config: check
  `git config --list --local` first.
- It hides only the list above. Still readable, for example:
  `~/.cache/huggingface/token`, `.env` files in the workspace, the file
  `GOOGLE_APPLICATION_CREDENTIALS` names, and the rest of your home
  directory; environment names that match none of the rules stay too (a
  `DATABASE_URL` with a password in it). Add paths to
  `sandbox_policy.extra_deny_read_paths`.
- It does not stop processes that started before you turned it on, and a
  process started with web access Off stays offline after `/web` opens.
- Git over SSH, LAN and cluster hosts, and a corporate upstream proxy do not
  work with it on; turn it off for that work.
- `/sandbox off` in one session does not take local MCP servers out of the
  sandbox; `/sandbox default off` or `r4 config sandbox off` does.
- No seccomp filter yet; on macOS the profile is allow-by-default with
  targeted denies, and the macOS backend has not yet been run live.
- Server (frontend) sessions do not use it: they run in the server's own
  sandbox.

## HPC account custody (ADR-112)

*Added 2026-11-08.* How UMRS reaches a researcher's **own** login at an HPC site.
The full user guide is [Chapter 31](31-own-hpc-accounts.md); the decision is
[ADR-112](../adr/112-user-hpc-accounts-gated-delegation.md).

| Rule | What it means |
|------|---------------|
| The transport key reaches only the gate | For each person and site, UMRS creates one Ed25519 **restricted key** and keeps its private half on the UMRS server, outside the database (the database holds only its id and fingerprint). The user's `authorized_keys` line pins it with `restrict`, an optional `from=` and `command=` to the **gate**, a small stdlib-only program in the user's HPC home. Before an account is activated, UMRS proves the gate itself answers: its `probe` must come back refused by the gate, by name |
| Signatures come from a dedicated user key | Anything that asks the gate to change state (the gate's `revoke` and `rotate` of the restricted key, installed in wave B1; job requests from wave B2) must be signed with the user's **signing key**: dedicated, never the login key, never forwarded, kept on the user's computer, a FIDO2 security key with a PIN where possible. The gate checks the OpenSSH signature, the security key's touch flag (and the PIN flag where required), strict canonical JSON, the site, the user, a 10-minute expiry for control requests (with two minutes of clock difference allowed at each end) and a single-use nonce, then writes a hash-chained log in the user's HPC home. Signing and account commands refuse in an agent's companion shell (the CLI and r4's client both), signing also without a terminal; UMRS refuses requests an agent marked; chat/plan asks before such a command runs through the shell; autopilot auto-answers only that local prompt, while the signature and companion-shell guards still refuse it (2026-11-09, ADR-104 amendment 3). These guards catch mistakes and ordinary misuse, not a determined program, which is why the gate trusts only the signature *(2026-11-08 review: this cell said agent tools cannot run them)* |
| UMRS's call to the gate offers only the restricted key | UMRS calls the gate with `ssh -F none`: its own SSH configuration never adds a platform identity to the call (only the host's address, port, known-hosts files and jump host are taken from it), with no agent, no forwarding and a minimal environment. A gate session ends after 60 seconds; requests without a valid signature are logged with a short fixed entry, and no longer once the log reaches 8 MiB *(added 2026-11-08, review)* |
| No fallback | Each site declares its access modes (`service_account`, `user_account`). A request that cannot be served under its resolved identity is refused with an `hpcaccess.*` name. UMRS never quietly runs it under another account |
| Platform lanes stay on the platform login | Serving endpoints, the service relay, tunnels, the janitor, the reaper and health checks run only under the site's platform login, never under a user's account, because their job environments carry operator secrets |
| A grant is bounded, and the gate counts it *(added 2026-11-09, waves C and D)* | Unattended work runs only under a **grant** the user signed for one site: exact workflow versions (code and step-graph digests), the images, parameter bounds that refuse code, paths and commands, an input rule (`listed`, `chained` or `any`), limits on jobs, GPU-hours, core-hours and concurrent steps, and at most 30 days. At every step the gate checks the run against the grant and reserves the step's largest cost against counters in the user's HPC home, under a lock; it settles from `sacct` for the actual measured allocation; Slurm over-allocation can exceed the reservation, while a completed job `sacct` cannot report charges its reservation. UMRS cannot widen, extend or pass on a grant. A signed `grant-cancel` ends one; revoking the account ends all of them, and the gate refuses grants issued before a later reconnect. Under a grant UMRS still chooses when runs start, parameter values within bounds, the data by the input rule, and each step's resources within the site profile; every grant summary lists this. The gate's log checkpoint is witnessed by r4, which reports a fork loudly and then refuses to sign anything but a cancel or a revoke |
| Usage is recorded append-only *(added 2026-11-09, wave D)* | Every job of user work on an HPC site, under a user's account or the platform login, is written to a usage ledger before submit (an estimate) and settled after it (from the gate's `usage` verb or `sacct`). If the ledger cannot be written, nothing is submitted. The database refuses updates and deletes to it; corrections are new rows. A project's GPU-hour budget is enforced before each step's submit (lifetime, across all sites). Members see their own rows and the project totals; owners see other members' rows without their HPC login, job ids or audit columns |

**Honest scope.** UMRS stores nothing that can act as you without your
signature. Under a standing permission you give, UMRS chooses the data your
approved workflows run on. Outputs that pass through UMRS stay visible to its
operators, and the same team publishes r4 and the gate (pin the gate's hash,
Chapter 31 §31.5).

**Status (2026-11-08).** Wave B1 (connect, verify, link, revoke, rotate) is
being built and comes first on bdrt; in B1 nothing runs under a user's account,
and `r4 hpc revoke` / `rotate` edit the user's `authorized_keys` over the user's
own SSH (no UMRS route sends a signed request to the gate yet).
Running jobs under your account comes with wave B2.

**Status (2026-11-09).** Grants (wave C) and the usage ledger with budgets
(wave D) are being built; neither is merged or deployed. Until they are, the
two rows above added on 2026-11-09 describe the design that waves C and D
implement, not running behaviour. Chapter 31 §31.7 and §31.8 give the details.

## Custom MCP Server Security

Custom MCP servers must declare a `config.json` manifest. Hard-fail validations:

- `sandbox.network` must be `egress_guard`
- `sandbox.filesystem` must be `read_only`
- Zero/negative memory or runtime limits are rejected
- Entry point may NOT invoke a shell
- Entry point script path must stay within the mounted directory
- Missing `capabilities` or empty `tools` list causes rejection

## Authentication & Authorization

- Gateway validates JWT on every request
- Per-user Slurm accounting for resource tracking
- Project-scoped access control for data and models
- Admin tokens for UMRS direct operations
- OIDC support for enterprise identity providers

## Network Security

- HTTP only inside platform (no internal SSL)
- No general internet access from inference containers
- Compute nodes may require explicit port opening (>1024)
- ZMQ binding restricted to `127.0.0.1` in tunnel mode
