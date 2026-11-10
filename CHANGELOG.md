# Changelog

All notable changes to the R4 + UMRS platform. Format:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions cover the
parent repo, its pinned `umrs` submodule (`UMRS-org/umrs@ai4s-platform`) and
the TUI binaries. Gap rows `D-nn` refer to
[docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md);
the per-wave detail is kept in the maintainers' development records.

## [Unreleased]

- **HPC gate release integrity (ADR-112 wave E, repo-side):** configured gate
  releases require a separately pinned maintainer FIDO identity and reject bad
  signatures, downgrade, stale clients, expiry and local/live gate hash
  mismatches. Live JSON uses the same actual hello check and `live_fork()` rollback verdict as text, combined witness/live is refused, and active checks bind the recorded enrollment domain. A read-only CI double build checks R4-wheel and gate SHA-256.
  No production release key, signed manifest, operator consent or first
  non-maintainer acceptance is claimed; unconfigured clients retain a labelled
  legacy compatibility path.
- **Literal local autopilot; deletion asks** (ADR-104 amendment 3;
  2026-11-09). Autopilot auto-answers every local non-deletion permission
  prompt, including trust-policy misses, credential/force-push fences, user
  `ask` rules, tainted memory/messages, team-plan gates and `ask_user`, with an
  audit note and no fabricated human approval. Recognised user-file deletion,
  patch rename/delete sources, worktree removal and deleting merge-backs always
  ask. Subagents now inherit an isolated view of the parent's permission
  policy. Explicit denies and backend/sandbox authorization are unchanged.
- **Durable subagent child recording** (S1/S2, D-70; 2026-11-10): sync and
  async children now get stable backend and child-session identities before
  work starts. The existing session store and event log retain parent lineage,
  task/prompt/model/tool declarations, full replies/follow-ups, redacted and
  capped tool history, and completed/failed/cancelled state. Nested children
  use the immediate parent and the same custom database; child writes never
  invoke the parent's snapshot rail. Ordinary Work rows now correlate by the
  backend `agent_id` and finish from `agent_result`, including defensive
  result-before-spawn handling and distinct execution turns for follow-ups.
  Console inspection builds on this in S4 below; remote venue parity remains S5
  (plan).
- **Read-only durable subagent inspection** (S3, D-70; 2026-11-10): the typed
  `inspect_subagent` bridge RPC returns the child's full stored conversation,
  immediate-parent/backend lineage, task/prompt/model/tool declarations,
  lifecycle, and cursor-paginated actual event history without loading or
  switching a live session. Event payloads stay redacted and explicitly bounded;
  `list_sessions` gains opt-in local child inventory and lineage filters while
  default lists and writable loading still exclude children. Remote child
  inventory/transport remains S5 work.
- **Console subagent inspector** (S4, D-70; 2026-11-10): Work-row agent details
  and the active parent's durable child rows in Sessions open the actual child
  read-only, including after bridge restart. The inspector shows task/prompt,
  lineage, model, lifecycle, follow-ups and chronological expandable event
  history. It polls only open non-terminal local children, paginates by durable
  event id, and never calls `load_session` or creates a second writer. Remote
  children report local-only unavailability until S5 rather than fabricated data.
- **Server-bound remote subagent inspection** (S5, D-70; 2026-11-10,
  repo-side): venue workers mirror actual child snapshots/events through an
  explicitly child-aware lease rail, never the parent's generic mirror. UMRS
  validates root lease/running venue, immediate-parent and nested bindings,
  revision/source-id replay and payload caps while storing history in the
  existing snapshot journal. Failed event appends replay in order before clean
  completion; persistent transport outages retain truthful local execution with
  visible degraded-sync evidence. Generic snapshots, session-bus writes and new
  venue workers cannot mutate or adopt a bound child. Authenticated owner
  inventory and inspection use the S3 lineage/message/replay/redaction contract
  without local or parent fallback. Local non-mirror behavior remains unchanged.
  Maintainer live remote acceptance is still open; no deployment or
  `remote verified` claim is made.

## [v0.2.0-rc4] — 2026-11-07

- Rebuild of v0.2.0-rc3, whose export was refused by the secret-scanning gate: a test fixture shaped like a Stripe key is now built at runtime.

## [v0.2.0-rc3] — 2026-11-07

- Rebuild of v0.2.0-rc2, whose public release could not be published: the macOS arm64 console job used the retired `macos-14` runner. Release workflows now use `macos-15`.

## [v0.2.0-rc2] — 2026-11-07

Since `v0.2.0-rc1` (2026-09-15). The platform was re-based on the
2026-09-22 design ([R4-UMRS-DESIGN.md](docs/R4-UMRS-DESIGN.md)). Gap
totals went from 13 implemented / 33 partial / 10 absent / 2 contradicts
at the design reset to **35 / 19 / 0 / 2**.

### Security
- **True autopilot; deletes are backed up first** (ADR-104 amendment 2;
  2026-11-05, not released yet). Autopilot now asks for four things only:
  reading credentials (keys, tokens, keychain) or force-pushing, a knowledge
  decision, your own `ask` rules, and a delete it cannot back up. Untrusted-
  content arguments, protected paths, plain `git push`, plan gates, team-plan
  budget cards, compute commits (UMRS's auto-commit threshold still applies)
  and decision-model doubts run and show as `[autopilot] …` lines. Any delete
  nobody approved (autopilot, an allow rule, "always this session") first
  copies the files to `~/.local/share/r4/trash/` unless git holds them
  committed and unchanged; globs, variables, `git clean`, `find -delete` and
  targets outside the workspace ask.
- **Autopilot runs; no "always for this project"** (ADR-104 amendment 1;
  2026-11-03, not released yet). Signed in, autopilot asked for every tool,
  `file_read` included: it consulted a server allowlist nothing seeds, and
  that check came before the read-only and "this session" allowances. Now
  autopilot runs every tool the safety floor lets through (never-auto tools,
  fences, ask rules and protected paths still ask). An organisation can
  narrow it to its server allowlist with `R4_AUTOPILOT_SERVER_POLICY=1`
  (badge `autopilot(policy)`). The approval card offers `y` once, `a` this
  session, `n`/`r` reject: `p` is gone (one keypress wrote a durable rule that
  ignored the arguments); standing rules go in `~/.config/r4/permissions.json`.
  An `a` no longer writes a server trust row.
- **One mode, one permission decision; the console never answers an
  approval itself** (ADR-104, accepted 2026-10-31; not released yet). The session
  has one mode, `chat | plan | autopilot` (Shift+Tab, `/mode`; `/trust` =
  autopilot); the permission mode follows it. Fixed: a "yes" on one call
  auto-approved every later call of that tool in the console (`ls` then
  `rm -rf`); accepting a plan with auto-approve left the session in TRUST
  after switching back to chat, across resumes; `/approve ask` pinned
  asking even in autopilot; ACP autopilot resumed as asking; `/trust`,
  `/mode autopilot` and ACP ran TRUST without the server allowlist; an
  `allow` rule wrote `.git/hooks` without asking; `apply_patch` passed every
  path fence; wildcard rules never matched; a plan agent's grant skipped
  deny rules; one "no" hid a tool for the session; a second approval
  replaced the first card; Esc on the help overlay rejected a pending
  approval. Behaviour changes: `/approve`, `/allow-all`, Alt+M and the
  `set_permission_mode` RPC are gone; accepting a plan visibly switches to
  autopilot (`a`) or chat (`m`); "always for this project" is written by
  the companion (`~/.config/r4/projects/<id>/permissions.json`) and binds
  the CLI and ACP too. Removed dead code: `r4/agent/confirmation.py`,
  `r4/chat_core/approval.py` (`R4_TOOL_APPROVAL`).
- **Local tool sandbox, opt-in** (ADR-101, D-65; 2026-10-30, not released
  yet). A local session's shell, Python, code and test tools, language
  servers, local stdio MCP servers and hatch runtimes can run under
  bubblewrap (Linux) or Seatbelt (macOS): writes only to the workspace and a
  per-session temp directory, credential stores and the r4 config directory
  unreadable, secrets scrubbed from the environment, network only through
  r4's loopback proxy following the session's web access (local MCP servers
  follow their own `sandbox.web` entry, default none; hatch runtimes keep
  the host network), with private and cloud-metadata addresses refused.
  r4's own file tools are not inside it. Off by default and announced at every
  local session open; `/sandbox on`, `r4 --sandbox on`, `R4_SANDBOX`, `r4
  config sandbox on`. Fails closed after a self-test. Behaviour change with
  the sandbox off too: `compile_code`, `run_code`, `test_code` and
  `test_fix_loop` now get the same tool environment as `shell_exec`
  (`R4_COMPANION_SESSION=1`; credentials are removed only with the sandbox
  on or in a venue worker) and their own process group. macOS not yet run live.
- **Loop routes are owner-scoped** (V-13). List, status, iterations,
  snapshot, state, append, stop, pause and resume used to accept any
  authenticated caller. Another user's loop is now a 404.
- **Bundle signatures cover table contents** (ADR-032 addendum 8). A
  signed bundle with edited rows used to verify.
- **Disabling an account ends its ZMQ session keys** at the next sync
  (ADR-071, D-03). They used to remain valid until their TTL.
- **Chat route gating:** every chat path, including streaming, resolves
  through the data-class gate (D-37). A sensitive project never dials a
  public provider.
- **Knowledge ingest:** the remote route refuses server-side paths (typed
  refusal); bundle restore is admin-only.
- **Raw bundle export** needs the owner or an admin, a catalog licence, and
  an audited acknowledgement for sensitive projects.

### Added
- **Research loop**
  - Parallel hypothesis tracks: `r4 project loop start --tracks N` (D-34, ADR-066 amendment 2).
  - Ask policy with maturity stages: `r4 project ask_policy --stage` (D-33, amendment 3).
  - Loop replay names the companion session that started the loop; loops can be listed by session (D-52, ADR-073).
- **Skills:** skill-edit plane with merge-on-green: `r4 skill pending / approve / reject`, approval requires a passing test result (D-27).
- **Research team:** planner, experimenter, analyst and reviewer through the Team Engine SPI, via `r4 team launch --team research` and `--crew research` (D-29, ADR-072).
- **Knowledge bundles** (D-40, D-41)
  - Transport: signed `.kb.tar.gz` server export, `r4 knowledge export-download`, `r4 knowledge import`.
  - Canonical document identity (`cid-1`), with a backfill runner.
  - Opt-in raw source materials: `--with-raw` on export, `--no-raw` on import.
- **Scientific Catalog object:** `r4 catalog`, pinning, budgets (D-39, D-43, D-36).
- **Embedding compute:** runs in an opt-in network-isolated sandbox (D-12).
- **Serving:** per-partition GPU-memory ceiling (`gpu_mem_util_max`), applied to every Slurm launch.
- **Operations:** identity backfill runbook; submodule lineage guard (`scripts/check-umrs-lineage.sh`, a pre-push hook and a CI job).

### Fixed
- **CI:** red on every push on 2026-09-24. The canonical umrs remote lagged the pin; also an r4-web typecheck error on shared TUI source.
- **Loki:** crash loop of about 430,000 restarts from a root-owned volume.
- **Serving:** the GPU-memory cap was bypassed by `r4 endpoint up`, so GB10 launches failed at startup.
- **Models:** `r4 model stage` to a disallowed path answered a bare 500; it is now a typed 400 that names the cluster-side register flow.
- **Export:** `r4 project export --strict` never exited non-zero; `r4 knowledge info` / `validate` rejected the server's `.kb.tar.gz`.
- **Teams:** snapshot reload dropped per-role models.

### Changed
- The TUI binary release workflow creates the GitHub release when it is missing.
- Deploy docs: redeploy happens only through the converge workflow (not `start.sh` on the host).

## [v0.2.0-rc1] — 2026-09-15

Production release candidate. Structure hardening (ADR-052/053/054),
release blockers R-01…R-14 closed. See
the production release plan (maintainers' development records).
