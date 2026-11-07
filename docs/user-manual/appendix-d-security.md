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
