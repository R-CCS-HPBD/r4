# ADR-101: Local tool sandbox — opt-in OS containment for the processes a local session starts

**Status:** Accepted (2026-10-30, maintainer: "yes I accept ADR-101. merge it please"; first Proposed 2026-10-30 with decisions D-1, D-3 and D-4 recorded verbatim below, the rest decided by the agent under the 2026-09-24 delegation, reasons recorded; text revised 2026-10-30 to the built and reviewed behaviour, see the note under Decision and Rollout-status)
**Date:** 2026-10-30
**Relates to:** [ADR-099](099-session-policy-and-quotas.md) (D-1/D-2/D-9: the session's web access, amended here for shell, Python and MCP); [ADR-092](092-team-plans-declarative-orchestration.md) (D-6 writers, D-11 workstation posture, slice 5); [ADR-046](046-remote-session-credential-egress.md) (P3/P4/P6: the remote sandbox, the parity reference, not reused); [ADR-061](061-mini-rivault-sandbox-profiles.md) (not a sixth profile); [ADR-075](075-one-work-engine-venue-is-user-choice.md) (venue `local`); [ADR-023](023-default-mcp-hardening-contract.md) (C1/C6/C7/C9); [ADR-062](062-amendment-2026-10-15-institutional-and-local.md) (workstation loopback); [ADR-094](094-constitution-amendment-numbered-principles.md) (INV-09, CC-3); gap rows D-12, D-13, D-24, D-30, new D-65

## Context

A local R4 session runs its agent loop in the `r4` process on the
scientist's workstation. Verified on `main` `6f83d70b7`, 2026-10-30:

- **Every process the agent starts runs with the user's full rights.**
  `shell_exec` and `python_exec` go through `subprocess.Popen` in
  `r4/src/r4/companion/tools.py` (`_run_captured`), `shell_detach.py`
  (`run_to_files`) and `streaming_shell.py` (`_spawn`); `compile_code` /
  `run_code` / `test_code` through `coding.py` `_run_command`;
  `test_fix_loop` through `test_fix_loop.py` `_run`; language servers through
  `lsp.py`. `coding.py` and `test_fix_loop.py` even inherit the whole
  `os.environ`.
- **The env scrub is a venue-only rule.** `venue.sanitize_tool_spawn_env`
  returns the environment unchanged outside a remote worker, so locally every
  provider key and session token reaches tool children.
- **Web access binds only two tools.** `web_access.py` says so: "Shell,
  Python and MCP tools are not filtered here"; ADR-099 D-9 records it as a
  known limit and lists "network filtering of local shell commands" as not
  in scope. A session that chose `none` can still `curl` from a shell.
- **ADR-092's workspace is not a sandbox** (`agent_workspace.py`: "Isolation
  keeps parallel agents off one another's files; it is not a sandbox").
  ADR-092 slice 5 deferred containing command text "to the sandboxed server
  venue, slice 8".
- **Local MCP servers are not started by the agent.** A stdio entry in
  `~/.config/r4/mcp-servers.json` runs as a child of the `r4-mcp
  remote-proxy` daemon the bridge starts when the user toggles it in `/mcp`
  (`mcp_exposure.py`, `mcp-servers/src/r4_mcp/runners/remote_proxy.py`
  `StdioUpstreamClient._spawn`). That daemon is detached, outlives the
  session, is reused by every later session, and imports nothing from `r4`.
  Hatch runtimes (`integrations/hatchling.py` `_launch_runtime`) run hatch
  packages in process and must accept inbound loopback connections.
- **The remote session already has a sandbox** (ADR-046 Amendment 2: podman
  on umrs-front, internal network, squid for web, a fail-closed self-test).
  It is bound to UMRS (rail, `umrs-infer`, leases); running it on a laptop
  needs the whole stand-alone stack (D-64).

The maintainer asked (2026-10-30) for a sandbox for local sessions "as the
remote sessions", and answered the three open questions: default off as in
Claude Code with a TUI notice that says how to turn it on mid-session; macOS
in the first wave; MCP included.

## Decision

R4 gets a **local tool sandbox**: an opt-in, OS-level wrapper around every
process a local session's agent starts. It is not a mini-RiVault and not a
sixth ADR-061 profile: it is client-side, R4-parented, and its backends are
bubblewrap and Seatbelt, not podman.

*2026-10-30, after the build and its review rounds (core `65a4188a4`,
wiring `3f12f5781`):* D-2, D-4, D-5, D-6, D-8, D-9, D-11 and D-12 below
describe what was built. Where the first draft (`0dbfef490`) said something
else, the paragraph is marked **as built**; the decisions themselves are
unchanged.

**D-1 Default off, announced.** Maintainer, 2026-10-30, verbatim: "default
-> off as claude code but r4 tui needs to notify users and how to make this
on during a session at first". When a local session opens or resumes with
the sandbox off, the TUI shows one keyed notice row: the sandbox is off, what
that means, and `/sandbox on`. `r4 -p` and the Rich REPL print the same line
on stderr (the ADR-099 D-2 "says so in one line" precedent). A session whose
backend is unavailable is told why and the fix command instead. The line
(`SANDBOX_NOTICE_OFF`): "Sandbox off: shell, Python and MCP servers run with
your full permissions. Turn it on with /sandbox on (this session) or r4
--sandbox on (at launch)." *Note 2026-10-30 (final review):* `r4 -p` and the
REPL have no `/sandbox`, so they print `SANDBOX_NOTICE_OFF_CLI` instead,
which names `r4 --sandbox on`, `R4_SANDBOX=on` and `r4 config sandbox on`.

**D-2 Turning it on and off.** One setting, `sandbox: {mode: on|off,
chosen_at, source}` in session metadata (beside `web_access`). First match
wins:

1. the launch flag `r4 --sandbox on|off` (travels as `R4_LAUNCH_SANDBOX`,
   popped by its consumer so nothing the agent starts inherits it);
2. the session's own earlier choice (a resume keeps it);
3. `R4_SANDBOX=on|off`;
4. the profile default `sandbox_default` in `config.json` (written only by
   `/sandbox default on|off` or `r4 config sandbox on|off`);
5. off, `source: default_off`.

Mid-session: `/sandbox on|off|status` in the TUI. It applies from the agent's
next process start; it never kills what already runs. Sources: `flag`, `env`,
`session`, `profile`, `slash`, `default_off`, plus `venue` and `inherited`
(D-11). Repo layers (`.r4/config.toml`, `.r4/local.toml`) cannot set it: a
cloned repository must not switch a user's sandbox off.

**As built, the resume rule is strictest-wins.** A session with no choice of
its own takes the launch flag, else pins an `on` that came from `R4_SANDBOX`
or the profile default into its metadata (keeping `source: env|profile`), so
a later resume without them stays on; an `off` is never pinned. A resumed
session keeps its own choice; a launch `on` tightens an `off` session, but a
launch `off` never loosens an `on` one: only `/sandbox off` does. This
departs from "launch flag first" for the loosening case only. The profile
default is read once, when the session is built.

**D-3 Backends, both in the first wave.** Maintainer, verbatim: "macOS yes".
Linux: `bwrap` (bubblewrap, unprivileged user namespaces). macOS:
`/usr/bin/sandbox-exec` with an allow-default SBPL profile plus targeted
denies (writes, credential reads, network). Fail closed: `/sandbox on` is
refused with `sandbox.unavailable` (no backend; the reason and the fix
command, e.g. the Ubuntu 24.04 AppArmor profile) or
`sandbox.self_test_failed` (D-8); a spawn whose wrapper cannot be built while
the setting is on is refused, never run unconfined. A third code,
`sandbox.refused`, names a policy the wrapper will not build (D-5: a write
root at or above `$HOME`, a hidden or missing `cwd`).

**D-4 What runs inside.** Maintainer, verbatim: "MCP yes".

| Wrapped | Where |
|---|---|
| `shell_exec` (captured, detached, streamed) and `python_exec` | `tools.py` `_run_captured`, `shell_detach.py` `run_to_files` (wraps before the spill files exist), `streaming_shell.py` `_spawn` |
| the `python_exec` interpreter probe | `tool_python.py` (through `wrap_spawn`, own process group; its cache is keyed by path and sandbox state, and a refusal is not cached) |
| `compile_code`, `run_code`, `test_code`, `test_fix_loop` | `coding.py`, `test_fix_loop.py`, both now through `tools._run_captured` |
| language servers | `lsp.py` (`start_new_session`; stopping one kills its whole process group) |
| local stdio MCP servers | the `r4-mcp remote-proxy` daemon prefixes each stdio child with the R4 launcher (D-9) |
| hatch runtimes | `hatchling.py` `_launch_runtime`, filesystem and env only, host network (they must accept loopback calls) |
| project hooks (`<workspace>/.r4/hooks.json`, with `R4_COMPANION_PROJECT_HOOKS=1`) | `user_hooks.py` `_hook_spawn` (added 2026-10-30, final review; a refusal runs nothing and the hook allows, like any failing hook) |
| the legacy `r4 agent` (`SciWorkflowAgent`) `SHELL_EXEC` steps | `agent/core.py` `_run_shell_step` (added 2026-10-30, review ESC-5; a `SandboxError` fails the step). Its `PYTHON_EXEC` steps run inside r4 (`exec`), which no sandbox confines: they are refused while the sandbox is on |

**As built, also with the sandbox off:** `compile_code`, `run_code`,
`test_code` and `test_fix_loop` now get the same tool env as `shell_exec`
(`_tool_spawn_env`: `R4_COMPANION_SESSION=1`; credentials are removed only
with the sandbox on or in a venue worker, a local session with it off
passes `os.environ` through), their own process group (a timeout kills the
whole group), and a working directory that must lie inside the ADR-092
agent workspace when one is set; they used to inherit `os.environ` and the
process's directory. A `SandboxError` at any site becomes the tool result
`Error: <code>: <reason>`.

Not wrapped: the companion process and its model calls (ADR-062 workstation
loopback must keep working); the bridge; user-level hooks
(`~/.config/r4/hooks.json`) and the user's own `!` shell (the user's code,
as in Claude Code); `r4` CLI internals; the remote-proxy daemon itself (it
serves UMRS and calls HTTP upstreams).

*Host-side git (2026-10-30, final review ESC-1).* r4 runs git on the host,
unconfined, in directories the agent can write: `list_dir`'s
`git check-ignore`, the session probe's `rev-parse`/`branch`/`log`, the v2
TUI's `/code diff`. In a repository the sandboxed agent made (`git init` in
the workspace) a plain call ran that repository's `core.fsmonitor` command
with the user's permissions: a full escape, reproduced. Every such call now
goes through `r4/src/r4/safe_git.py` (`-c core.hooksPath=/dev/null -c
core.fsmonitor=false -c core.untrackedCache=false -c
log.showSignature=false`, no system config, no `GIT_*` overrides; a diff
adds `--no-ext-diff --no-textconv`).

**D-5 Filesystem and environment** (as built).

*Write roots.* One root: the ADR-092 agent workspace when one is set, else
the session's workspace root (the bridge's directory when the session was
built), else the directory the context was created in; plus the per-session
temp directory and the user's `extra_write_paths`. A per-call `cwd` never
widens the writes (it may be read-only; writes there fail with `EROFS`); an
explicit `cwd` that is hidden, masked or missing is `sandbox.refused`.
Refused roots (`sandbox.refused`): `$HOME` or any directory above it; a root
that is or contains `/tmp`, the system temp directory or the sandbox base, or
that lies inside the base. Starting `r4` in `$HOME` with no workspace
therefore refuses every sandboxed spawn, and the message says why.

*Temp.* `<tempdir>/r4-sandbox-<uid>/s-XXXX/{tmp,proxy,empty}`, all 0700.
Inside, the per-user base is covered by a tmpfs, so one session cannot reach
another session's proxy socket. A session directory records its owner's pid;
one whose owner is gone is removed when the next is made; one removed from
under a running session is rebuilt. On Linux `/tmp` inside is the session's
`tmp` (also bound at its real path; `TMPDIR` points there).

*What is denied or pinned.*

| Kind | Items | Inside the sandbox |
|---|---|---|
| Credential stores | the R4 config directory (`_resolve_config_dir()`); `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`, `~/.config/gcloud`, `~/.kube`, `~/.docker`, `~/.config/gh`, `~/.netrc`, `~/.git-credentials`, `~/.pypirc`, `~/.npmrc`, `~/Library/Keychains`; the user's `extra_deny_read_paths` | unreadable |
| Relocated stores | the paths named by `GH_CONFIG_DIR`, `DOCKER_CONFIG`, each `KUBECONFIG` entry, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, `CLOUDSDK_CONFIG`, `GNUPGHOME`; `$XDG_CONFIG_HOME/gh` and `$XDG_CONFIG_HOME/gcloud` | unreadable |
| X11 auth | `~/.Xauthority`, `~/.ICEauthority`, `$XAUTHORITY` | unreadable |
| Repository control files in a write root | `.git/hooks`, `.git/config`, `.git/config.worktree`, `.r4/config.toml`, `.r4/local.toml`, `.r4/hooks.json` (added 2026-10-30), `.r4/mcp.json`, `.r4/mcp-servers.json`, `.github/mcp.json`, when they exist (a missing `.git/hooks` inside an existing `.git` is pinned as an empty read-only directory) | read-only |
| Git metadata in a write root | a linked worktree's `.git` file; every module git dir under `.git/modules` (nested and slash-named too) with its `config`, `config.worktree` and `hooks`; each linked worktree's `commondir`, `gitdir` and `config.worktree` under `.git/worktrees`; submodule `.git` files found from `.gitmodules` | read-only |
| Rename guards | `.git`, `.r4`, `.github` and every directory between a write root and a protected or denied path, each bound onto itself | a rename gives `EBUSY`; writes inside still work |
| r4 itself | `sys.prefix`, `sys.base_prefix`, the import roots of `r4` and `r4_contracts`, when they lie inside a write root | read-only |
| Host sockets (Linux) | `/run/user/$UID`; the system D-Bus; docker, podman, containerd and cri-o sockets; Kerberos KCM and gssproxy; pcscd; libvirt; screen. When the network is isolated, also the resolver sockets (systemd-resolved, nscd, avahi) | masked |

Never a copied or re-bound config directory (wave ledger 2026-10-03 (2):
binding a copy of `~/.config/r4` into bwrap was refused as credential
leakage). macOS: Seatbelt denies unix-socket connects (D-6), so the temp
directories stay writable; a rename guard there is a write deny on the
directory itself.

*Environment.* An allow-by-default copy with credentials removed: the venue
list (`venue._TOKEN_ENV_NAMES`, `*SESSION_TOKEN*`), names ending `_API_KEY`
(and `API_KEY` itself), names containing `SECRET`, `PASSWORD` or `TOKEN`,
the AWS key names, `SSH_AUTH_SOCK`, `GPG_AGENT_INFO`, `DISPLAY`,
`XAUTHORITY`, `R4_LAUNCH_SANDBOX`, and the host's own proxy variables
(`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, both cases). Kept
regardless: `R4_GATEWAY_TOKEN`, `R4_GATEWAY_URL` (hatch is essential) and
the user's `env_keep` (which can also bring `DISPLAY` back). Set:
`R4_SANDBOXED=<backend>`, `TMPDIR`, and R4's proxy variables when the web
access allows anything. The `TOKEN` rule also drops harmless names such as
`TOKENIZERS_PARALLELISM`; `env_keep` restores them.

**D-6 Network.** With the sandbox on, the network is isolated (Linux
`--unshare-net`; macOS `(deny network*)`), and the only way out is R4's
loopback forward proxy. As built, per web access mode:
- `none` or unchosen (`default_off` reads as `none`, ADR-099): no proxy is
  started and no proxy variable is set, on either platform. A process
  started under `none` stays offline even if `/web` opens later; restart it.
- `allowlist`: the proxy starts lazily on the first such spawn, after the
  availability check, and decides per request from the session's live web
  access: the CONNECT/Host name must pass `web_access.host_allowed` (squid
  `dstdomain`, the same function the web tools use). A process started under
  `allowlist` or `public` follows `/web` live, a switch to `none` included.
- `public`: any name.

Ports 80 and 443 only. Literal IPs are refused unless `public`. Refusals
answer 403 with the same text the web tools return. Linux reaches the proxy
over a unix socket in a 0700 directory, forwarded to `127.0.0.1:3128` inside
the namespace by a small stdlib script; macOS uses an ephemeral loopback TCP
port with a per-session `Proxy-Authorization` nonce. The proxy binds
loopback only, so the workstation still has no new outbound channel
(design §6 workstation row, ADR-059). A corporate upstream proxy is not
chained: the host's proxy variables are dropped inside and the proxy
connects directly. Hatch runtimes keep host networking (D-9).

**D-7 Infrastructure ranges denied.** After resolving a name the proxy
refuses any address that is private (RFC 1918, ULA), loopback, link-local
(cloud metadata), CGNAT, multicast, reserved or unspecified, IPv4-mapped
forms included, and connects to the vetted address (no rebinding window).
Parity with squid on umrs-front (remote-session plan §1). A user who needs a
LAN or cluster host from a shell turns the sandbox off for that work; a
narrow `allow_private_hosts` is a follow-up, not this wave.

**D-8 Fail-closed self-test.** The ADR-046 P4 analogue. Before `/sandbox on`
reports `on`, and on the first sandboxed spawn per process and policy, R4
runs a probe inside the sandbox with the policy about to be used. As built:
a write to `~/.r4-sandbox-probe-<hex>` must not reach the host; a sentinel
`.sandbox-selftest-<hex>` created in the R4 config directory must be
unreadable; the network must be isolated (Linux: no interface but `lo`;
macOS: a loopback connect to closed port 9 must be denied by the profile,
not refused by the kernel). The probe also fails when there is no write
target outside the roots or the sentinel cannot be written. Both files are
removed afterwards. Passes are cached per (backend, policy digest);
failures are not, and neither is a failed backend probe or a timed-out
`bwrap --help`: a transient timeout, or the fix command the notice named,
is retried at the next spawn or `/sandbox on` (2026-10-30, review LC-4; a
timeout's reason is one short line, never the probe's argv). Any failure:
`sandbox.self_test_failed`, the setting stays off, the notice says why. A proxy that cannot start or a session directory that cannot be made
is `sandbox.unavailable`.

**D-9 Local MCP servers.** The remote-proxy daemon is one per user and
outlives sessions, so its children cannot follow one session's toggle. As
built:
- **Launcher.** The daemon takes a launcher prefix (`--stdio-wrap`, a JSON
  argv) and a `--sandbox-digest`, and starts each stdio child (and each
  respawn) as `[*wrap, "--name", <server>, "--", <command>]` with `cwd="/"`.
  The prefix is `python -c LAUNCH_BOOTSTRAP`: it drops `''` and relative
  entries from `sys.path` before any import, then runs
  `r4.companion.sandbox.launch` with `runpy`, so a module planted in the
  daemon's directory is never imported. The launcher is a small unsandboxed
  supervisor: it serves that child's proxy, runs it under the backend with
  `--die-with-parent`, forwards SIGTERM, SIGINT and SIGHUP, and exits 78 on
  a `SandboxError` (fail closed). A malformed `--stdio-wrap` makes the
  daemon exit rather than start children unwrapped. `r4_mcp` still imports
  nothing from `r4`.
- **Strictest wins, per user, no registry.** MCP sandboxing is on when any
  stored profile has `sandbox_default: on`, or when the ratchet file
  `mcp-sandbox.json` in the R4 config directory is set; a session writes the
  ratchet when it turns `/sandbox on` or opens with the sandbox on
  (`require_mcp_sandbox`). The MCP policy combines every source strictly:
  only the extra writes and kept env names that all of them share, plus
  every denied read. A session turning the sandbox off does not weaken MCP:
  it goes back off only with `/sandbox default off` or `r4 config sandbox
  off`, which write that profile's default off and clear the ratchet, and
  only once no stored profile default is on.
- **Identified-daemon restart under a lock.** The daemon reports its
  `sandbox_digest` on `GET /`. When it differs from the desired one, R4
  restarts the daemon only when the `GET /` identity, the pid file
  `logs/remote-proxy.pid` and the process command line agree. Start and stop
  hold an `fcntl` lock (`logs/remote-proxy.lock`); the pid file is written
  only once the new process answers with its own pid. A daemon from before
  this change is recognised by its command line under `/proc` (same user,
  same module, same `--port`, no `--stdio-wrap`, exactly one match) and
  restarted; on macOS it stays stale. An unidentified listener is never
  killed: the result says `sandbox: "stale"` with a reason, the `/mcp` row
  reads "sandbox pending restart", and while the sandbox is wanted, new
  registrations with UMRS are refused.
- **LAN callers see no sandbox state.** `GET /` gives non-loopback callers
  only `{status, service, upstreams}`; the pid, the digest and the
  per-server `sandboxed`/`exempt` fields (and the health endpoint's
  `sandboxed` and `sandbox_digest`) go to loopback callers only.
- **Per-server env and exemptions.** The env names declared by the user
  file's non-exempt stdio entries are kept for MCP, each only for its own
  server: a wrapped child gets the daemon's environment minus the names
  other entries declare. Network per server comes from the user-level
  entry, `"sandbox": {"web": "none" | "public" | "allowlist", "domains":
  [...], "write": [...]}`, default `none`. `"sandbox": false` exempts a
  server, honoured only from the user-level file or `$R4_MCP_CONFIG`; in a
  project file the key is ignored entirely. Exempt names are part of the
  digest, so editing an exemption restarts the daemon.
- **Hatch runtimes** get filesystem and env confinement with host
  networking, under their own `hatch-sandbox-policy.json` (the user policy
  plus `env_keep: R4_HATCH_RUNTIME_TOKEN`, so a runtime bound beyond
  loopback still checks its bearer); `R4_GATEWAY_URL`/`R4_GATEWAY_TOKEN`
  pass through and their package directory is readable. A runtime started
  before the sandbox went on is recorded `sandboxed: false` and reported
  (`unsandboxed_hatch_runtimes` in `mcp_sandbox_status()`), not restarted.

**D-10 Ownership and the ladder.** The sandbox is a wrapper around processes
the harness already owns: no job store, queue, retry state, registry or
scheduler (CONSTITUTION §6), no storage credential, nothing that talks to S3,
Postgres or a scheduler. The permission ladder is unchanged: nothing is
auto-approved because it is sandboxed; ADR-092 D-6 parallel-writer refusals
and D-11 (no agent-authored plan scripts) stand. Taint (ADR-039) unchanged.

**D-11 Venues.** The setting applies to venue `local` only. Inside a
frontend worker (`venue.in_venue()`) it resolves to `mode: off, source:
venue` ("remote podman sandbox, ADR-046") and no notice is shown; the
setting is not sent with a remote start. A process that already carries
`R4_SANDBOXED` (a nested `r4`) reports `on (inherited)` with the outer
backend and does not wrap again. As built, the bridge reports every frontend
session, live or stopped, as `mode: "remote"` and refuses
`session.sandbox_set` on it with `sandbox.remote`.

**D-12 Surfaces** (as built; the TUI part lands in the same wave).
- **Bridge.** Snapshot key `session.sandbox` (`SandboxState`: `mode`
  `off|on|remote`, `source`, `backend`, `available`, `reason`, `tmp_dir`,
  `write_roots`, `network`, `notice`, and since 2026-10-30 `mcp_error` and
  `detail`, the lines the console renders as sent; optional, so older
  bridges stay valid). `session.sandbox_get` returns `{sandbox}`; `session.sandbox_set
  {mode, default?}` returns `{sandbox, restarted_mcp, still_running,
  mcp_error, state}`. Errors carry `error.data = {code, reason}` with `code`
  one of `sandbox.unavailable`, `sandbox.self_test_failed`,
  `sandbox.refused`, `sandbox.remote`. With `default: true` the profile
  default is written first, and a failed save rolls the session's metadata
  back and puts the previous default back (2026-10-30, review LC-6). An MCP
  failure (an exception, a missing hook, or a daemon still out of sync
  after the call) comes back in `mcp_error`, in the reply and in the
  snapshot, at `sandbox_set` and at session open (2026-10-30: no longer an
  "MCP: ..." tail on the notice). The MCP hooks get the session's profile;
  `release_mcp_sandbox_default` no longer writes the default itself.
- **Notice rule.** Off and local: `SANDBOX_NOTICE_OFF`. No usable backend:
  the unavailable line, worded by mode (on: commands are refused until a
  backend works or the sandbox is turned off; off: they run with full
  permissions and turning it on is refused). Sources `venue`, `inherited`
  and `remote`: no line.
- **Which context is current** (rewritten 2026-10-30, final review
  ESC-2, ESC-3, ESC-4, LC-5). A spawn reads the context pinned for its
  thread or task (`pin_sandbox`, a context variable), else the
  process-global installed one, else an ambient one. Every
  `CompanionSession.send` pins its own session's context unless one is
  already pinned, so ACP's many sessions in one process each turn under
  their own setting, web access and write root. A background subagent
  (`AsyncAgentPool`) and a team plan run (`team_plan_tools._spawn`) pin, at
  start, the context they started under (`bind_sandbox`); their tasks and
  `to_thread` calls inherit it. A session switch, an on-to-on switch or a
  later `/sandbox off` on another session therefore never reroutes them;
  `/sandbox off` on their own session still applies to their next command.
  The process-global context only serves spawns outside any turn: the
  bridge installs the active session's when it configures it, `r4 -p` and
  the REPL in `apply_local_sandbox`, and a session built later never
  replaces it. The former team-plan hold, and its side effect (an `off`
  session running under a held `on` context), are gone. An ACP session
  that opens with the sandbox on moves local MCP servers under it, as the
  console does.
- **Session directories outlive their context** (2026-10-30, review LC-3).
  The directory is the bind-mounted `/tmp` of sandboxed processes that may
  outlive a session switch (a detached shell, a language server), so it is
  removed at `close()` or process exit, never when the context object is
  collected (the text before said "not closed until process exit"; in fact
  the GC removed it). A context that started a proxy keeps it until then.
- **TUI.** The D-1 notice row (on init, new and resume), a status chip
  `sbx:on` / `sbx:off` / `sbx:n/a` (none for frontend sessions),
  `/sandbox on|off|status|default on|off`, the web-access dialog's local
  note made conditional. `on|off` is refused while an answer streams
  (2026-10-30, review LC-1): the bridge parks a mutating verb until the turn
  ends, so the change would not have reached the rest of that turn, and a
  long turn timed the request out while it still applied afterwards. A set
  that times out is never reported as "not changed": the console reads the
  state back first.
- **CLI.** `r4 --sandbox on|off` (the bridge also accepts `--sandbox`);
  `R4_SANDBOX`; `r4 config sandbox [on|off|show]` (`on` and `off` also move
  MCP, print the servers restarted, and exit 1 when MCP does not follow;
  since 2026-10-30 `on` first runs the backend probe and the self-test on a
  scratch workspace and, like `/sandbox default on`, is refused with exit 1
  and nothing written when they fail, review LC-8);
  an informational `r4 doctor` check nested at `local.checks.sandbox`
  (`r4 doctor --json`; its `message` is `sandbox: bwrap available` or
  `sandbox: unavailable (<reason>)`; since 2026-10-30 the plain text output
  also prints it, with the profile default). `r4 -p` and the REPL settle the
  sandbox before the web question, whose local note depends on it; team
  plan, teamup and recipe print a line only when the sandbox is on and
  cannot work.

**D-13 Amendments recorded.** Dated notes (2026-10-30) on `R4-UMRS-DESIGN.md`
(workstation row, "nothing runs outside a sandbox", execution isolation row,
"one sandbox type", glossary: a new "local tool sandbox" entry), ADR-099 D-9
and its NOT-in-scope line, ADR-092 D-11 and slice 5, and
`SANDBOX-PROFILES-MAP.md`; gap row D-65 added. They landed in the wave's
docs commit, not in `0dbfef490`.

### Why

- **Opt-in matches the maintainer's call and the tools users know.** Claude
  Code and Codex ship OS sandboxes that users switch on; the user is told
  every time it is off, so nobody believes a protection they do not have.
- **Wrapping children, not the agent,** keeps model routing, credentials and
  the bridge where they are and needs no daemon, no container engine and no
  UMRS: it works on a laptop with no platform.
- **One web policy, two enforcers.** Reusing `host_allowed` makes the shell
  and the web tools give the same answer for the same host; a second
  matcher would drift.
- **Native Python, not `srt`.** Anthropic's sandbox-runtime (Apache-2.0) is
  the closest prior art, and its mount order, mandatory write denies and
  proxy bridge are followed here, but it needs node, is 0.0.x, and carries
  its own domain matcher. R4 is a pip package.
- **Strictest wins on resume and for MCP.** A shared daemon and a resumed
  session must never be loosened behind the user's back by another
  session's launch flag; loosening is always an explicit `/sandbox off` or
  `default off`.

## Consequences

- Positive: `web_access` now binds shell, Python and MCP traffic when the
  sandbox is on; for the processes the agent starts, the listed credential
  stores and the scrubbed env names are out of reach and writes are
  confined to the workspace, with the repository's control files and r4's
  own code read-only inside it; the user always knows the state. r4's own
  file tools are not inside the sandbox (D-4: the companion process is not
  wrapped); the permission ladder alone governs them.
- Cost:
  - sandboxed commands start a little slower (bwrap ~10 ms; with web
    allowed, one extra Python start for the in-namespace forwarder); each
    MCP launcher imports the `r4.companion` package; each new policy costs
    one self-test (~100 ms);
  - `/tmp` inside is private on Linux (a literal `/tmp/x` written in a shell
    is at `<session tmp>/x` on the host), so host paths under `/tmp`,
    tools and virtual environments included, are invisible inside;
  - a detached sandboxed shell's spill files sit under the host `/tmp` and
    its handle holds the host pid, neither visible from inside another
    sandboxed shell (the handle says so under bwrap);
  - a nested `r4` CLI inside a sandboxed shell cannot read `~/.config/r4`
    and runs unauthenticated (follow-up: a scoped token); git over SSH and
    LAN hosts do not work with the sandbox on;
  - with r4's venv or source inside the workspace (a developer checkout),
    both are read-only inside: the agent cannot edit r4 or `pip install`
    into r4's venv;
  - starting `r4` in `$HOME` without a workspace refuses every sandboxed
    spawn;
  - language servers start in the first write root and are not restarted
    by `/sandbox on`;
  - the self-test briefly writes a sentinel in the real R4 config directory;
  - wrapped MCP children keep `stderr=DEVNULL`, so a launcher refusal shows
    only as a failed MCP call; a server that relies on a relative `cwd`
    fails when wrapped (`cwd="/"`).
- Residual risks, stated:
  - `R4_GATEWAY_TOKEN` stays in the child env (hatch);
  - the macOS profile is allow-default, so the IPC surface is open; macOS
    has not been run (the Seatbelt profile is checked as text on Linux; the
    rename guard assumes Seatbelt checks the source path; blocking
    `com.apple.SecurityServer` may break some tools);
  - no seccomp filter in this wave: unix sockets are reachable only through
    paths that are not masked, and abstract sockets are cut only when the
    network is isolated; a hatch runtime on the host network can reach the
    X server's abstract socket, but X11 auth is denied (`.Xauthority`
    masked, `DISPLAY` and `XAUTHORITY` scrubbed);
  - domain fronting through an allowed host;
  - processes started before `/sandbox on` keep running unconfined until
    they exit (they are listed in `still_running`, not killed); hatch
    runtimes started before it are reported, not restarted; a server
    registered with UMRS before its daemon went stale keeps routing to it;
  - nested repositories not listed in `.gitmodules` are not protected, and
    a missing `.git` is not pinned (the agent can `git init` there). That
    is more than repository tampering: any unconfined host-side git call in
    such a repository runs its config (`core.fsmonitor`, hooks, diff and
    filter drivers). r4's own calls are hardened (D-4, `safe_git`); clean
    and smudge filter drivers named by that repository's config are not
    switched off by it, and a git the user runs there by hand runs the
    agent's config;
  - hook scripts that a user-level hook names by a workspace path are
    writable by the agent and run unconfined; project hooks run inside the
    sandbox and an existing `.r4/hooks.json` is read-only (2026-10-30), but
    with the sandbox off a project hook file the agent wrote earlier runs
    with full permissions;
  - the read deny list is fixed: other secrets under `$HOME` (for example
    `~/.cache/huggingface/token`), workspace `.env` files and env names
    that match none of the scrub rules (a `DATABASE_URL` with a password)
    stay visible unless the user adds them (`extra_deny_read_paths`);
  - the MCP policy drops an `env_keep` or extra write set on only one of
    several profiles (strictest combination); the daemon does not reload its
    config, so a new server with no env names is not picked up until the
    next restart;
  - the bwrap tests skip on hosts without unprivileged user namespaces
    (possibly the CI runner); `--remount-ro` was checked on bwrap 0.12 only;
    Python 3.10 compatibility rests on ruff's `py310` target.
- NOT in scope: a podman local venue (D-64, ADR-090/091); seccomp; an
  `allow_private_hosts` knob; chaining a corporate upstream proxy; Windows;
  auto-approval under sandbox; lifting ADR-092 D-6/D-11.

## Alternatives Considered

- **Run the ADR-046 podman worker locally.** Needs the rail, `umrs-infer`,
  sealed sources and the stand-alone stack (D-64); far too heavy for a
  laptop session, and podman on macOS runs in a VM.
- **Shell out to `srt`.** Rejected above (node dependency, 0.0.x, second
  domain matcher).
- **Landlock + seccomp (Codex's former Linux path).** Landlock cannot
  isolate the network by namespace and Codex itself moved to bubblewrap.
- **Default on.** Rejected by the maintainer (D-1).
- **Per-call `/tmp` tmpfs.** Files would vanish between tool calls; the
  per-session directory keeps them for the session.
- **Launch flag always first on resume.** Rejected for strictest-wins (D-2):
  a launch `off` would silently loosen a session the user had turned on.

## Rollout-status (2026-10-31, first Mac run)

The maintainer ran `pytest r4/tests/test_sandbox_seatbelt.py` on a MacBook
Air (darwin, Python 3.11.13): 3 of 3 failed with `sandbox.refused` before
`sandbox-exec` ran. The test fixture put `HOME` inside the workspace, which
D-5 refuses as a writable root; the bwrap fixture had been fixed in review,
the Seatbelt one (skipped on Linux) had not. Fixed on
`wave/sandbox-macos-tests`: `HOME` beside the workspace, the "outside" write
target outside the per-user temp directory (the profile keeps that directory
writable, D-5, and pytest's `tmp_path` lives in it), and the self-test now
never picks its must-fail write target in a directory the backend itself
leaves writable (`extra_write_roots`). Rerun on the same Mac on the fix
branch, 2026-10-31: 3 passed in 0.40 s (writes and credential reads
confined, self-test passes, network denied with web access off). **The
macOS live leg is done**; a Python 3.10 run is the remaining open item for
D-65.

## Rollout-status (2026-10-30, accepted)

Accepted by the maintainer on 2026-10-30 ("yes I accept ADR-101. merge it
please") and merged with PR #645. The default stays off (D-1). Gap row D-65
stays **Partial** until the macOS live leg and a Python 3.10 run pass; the
open list in the block below is unchanged.

## Rollout-status (2026-10-30)

Built on `wave/local-tool-sandbox`; not merged, not released. Gap row D-65
is **Partial**.

| Landed | Commit |
|---|---|
| Core package `r4/src/r4/companion/sandbox/` (setting, policy, backends, proxy, netns forwarder, context, self-test, launcher, MCP policy) and its tests | `65a4188a4` |
| Spawn wiring (D-4), bridge verbs and snapshot, CLI flag, `r4 config sandbox`, the `r4 doctor` check (`local.checks.sandbox`), MCP daemon wrap and ratchet, hatch runtimes | `3f12f5781` |
| TUI: notice row, `sbx:` chip, `/sandbox` | same wave (TUI lane) |
| Docs: this revision, the D-13 amendments, D-65, user docs | same wave (docs lane) |
| Final-review fixes: hardened host-side git, per-run context pinning (ACP, background subagents, team plans), legacy `r4 agent` steps, project hooks, session directories kept to close/exit, uncached probe failures, `/sandbox` refused mid-turn, default rollback, `r4 config sandbox on` checked, CLI notice, doctor text line, plan-step cwd inside the workspace, legacy r4-mcp start, hermetic MCP tests | same wave (fix lane) |

Open:
- the macOS live leg, on a Mac (the Seatbelt tests skip on Linux);
- a run under Python 3.10 (the r4 client floor);
- agent playbook text on sandbox refusals (edit
  `r4-contracts/src/r4_contracts/agent_tool_playbook.py`, then regenerate
  with `r4 guide`);
- removing a session directory before exit once none of its sandboxed
  processes remains (today it stays until `close()` or exit, D-12);
- a thread started outside `pin_sandbox` that is not a background subagent
  or a team plan (none known) still reads the process-global context;
- smaller follow-ups: wrapped MCP stderr, detached-shell spill files in the
  session temp directory, a scoped token for a nested `r4`.

## Related

- [ADR-099](099-session-policy-and-quotas.md), [ADR-092](092-team-plans-declarative-orchestration.md), [ADR-046](046-remote-session-credential-egress.md), [ADR-061](061-mini-rivault-sandbox-profiles.md), [ADR-075](075-one-work-engine-venue-is-user-choice.md), [ADR-023](023-default-mcp-hardening-contract.md), [ADR-062](062-amendment-2026-10-15-institutional-and-local.md), [ADR-094](094-constitution-amendment-numbered-principles.md)
- [SANDBOX-PROFILES-MAP.md](../SANDBOX-PROFILES-MAP.md) (what this is not)
- User docs: [TUI reference §7](../r4/TUI_REFERENCE.md#7-sessions-local-and-remote), [security appendix](../user-manual/appendix-d-security.md#local-tool-sandbox-on-your-workstation)
- `.github/instructions/companion.instructions.md`, `agent-safety.instructions.md`
