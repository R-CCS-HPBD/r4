# ADR-104: One agent mode, one permission decision, approvals answered by the companion

**Status:** Accepted (2026-10-31, maintainer: "yes i approve ADR-104. merge 660"; amendment 1 2026-11-03, maintainer "good. please proceed for implementation"; amendment 2 2026-11-05, maintainer "we need true autopilot mode (except file delete if no full backup exists)"; amendment 3 2026-11-09, maintainer chose "only file deletion asks" for autopilot and recognised "user files and worktrees" as its scope; first Proposed 2026-10-31; number 104 because PR #659 holds 103; maintainer request: "r4's implementation for tool call allowance and mode (chat/plan/auto) and approv etc are now redundant/confusing/mis-implemented with bugs. please verify whole structure and detail implementations and have a clean and good stracture as claude and opencode2 etc."; open questions decided by the agent under the 2026-09-24 delegation, reasons below; `wave/permission-modes`)
**Date:** 2026-10-31
**Relates to:** [ADR-035](035-enforcement-point-matrix.md) (server trust allowlist in TRUST); [ADR-039](039-untrusted-content-provenance-taint.md) (taint fence); [ADR-076](076-decision-model-role.md) (decision gate); [ADR-092](092-team-plans-declarative-orchestration.md) (MD-5 never-auto, MD-6 card grants, D-6 forks); [ADR-099](099-session-policy-and-quotas.md) D-8 (one settings rule local and remote); G14 (persistent rules, wave (102), `docs/development/r4-harness-gap-vs-claude-2026-10-28.md` §3.4)

## Context

Verified on `main` `43dbd2f48` (2026-10-31) by reading the code end to end
(Python companion, bridge, ACP, Console v3):

**Too many knobs for one idea.** Whether a tool call runs was decided by
three session values and four flags that each meant part of the same thing:
`metadata.interaction_mode` (chat/plan/autopilot), `PermissionManager.mode`
(ask_always/auto_safe/deny/trust), a sticky `metadata.permission_override`,
ACP's `metadata.auto_approve_tools`, `ApprovalPolicy` (ask/auto/deny, a label
shim), `--allow-all-tools`/`--yolo`, and `/trust` `/allow-all` `/approve`.
Four functions mapped mode to permission, each differently:
`agent_settings._permission_mode` (chat → `auto_safe`),
`command_bridge._default_permission_for_interaction_mode` (chat →
`ask_always`, overwritten a line later), `acp_session.set_mode` (chat →
`ask_always`, autopilot recorded only in `auto_approve_tools`), and
`session.confirm_plan` (writes the override). The console showed two badges
(`mode:` and `perm:`) and had two cycles (Shift+Tab, Alt+M) that drifted
apart (`mode:autopilot · perm:auto-safe`).

**Defects that follow from it** (each reproduced by a test in this wave):

1. Accepting a plan with "auto-approve" wrote `permission_override=trust`;
   every later mode switch honoured it, so the session stayed in TRUST after
   going back to chat, across resumes.
2. `/approve ask` (or the plan's "manual") pinned `ask_always`, so a later
   switch to autopilot silently kept asking.
3. Autopilot set through ACP or `/mode` was not persisted where resume reads
   it: a resumed autopilot session came back asking, while the badge still
   said autopilot.
4. The server trust allowlist (ADR-035) was wired only when the session was
   constructed in TRUST or a plan was accepted; `/trust`, `/mode autopilot`,
   ACP autopilot and the settings document all ran process-global
   ("legacy") TRUST.
5. **The console answered approvals itself.** After one "yes" for a tool it
   answered every later ask of that tool "yes", whatever the arguments (a
   test pinned `ls` approved → `rm -rf x` auto-approved), while the companion
   believed it had asked per call. A project rule file kept by the console
   did the same. An auto-answer for a background tab went to the active
   tab's bridge, whose request ids collide (both count from 1).
6. A G14 `allow` rule was evaluated before the protected-path fence, so
   `allow: ["file_write"]` wrote `.git/hooks/*` without asking; wildcard
   rules (`mcp.*`, `*`) never matched; `apply_patch` (path inside
   `patch_text`) passed every path fence and was not decision-gated.
7. An agent fork's card grants (MD-6) skipped G14 deny rules; a template
   crew's `teamup_start` returned "allowed" before the taint fence and the
   rules.
8. One "no" hid the tool for the rest of the session: rejecting one
   fenced `git push --force` removed `shell_exec`.
9. "always" on a never-auto tool recorded a session grant and a server trust
   row; only a console hint prevented it.
10. A second approval replaced the first card (the first waiter blocked until
    the turn was cancelled); Esc on any modal (help, palette) rejected the
    pending approval.
11. The per-project rules the console kept were never seen by the companion,
    so "always for this project" bound only that console, never the CLI or
    ACP. *(Checked and not a defect: rules load from the process cwd, and the
    bridge chdirs into `--fs-root` before a session exists, so cwd is the
    workspace.)*

**Dead code.** `r4/agent/confirmation.py` (`ToolConfirmationRouter`, risk
tiers, `yolo_mode`) had no caller; `r4/chat_core/approval.py`
(`ToolApprover`, `R4_TOOL_APPROVAL`) was used only for its enum as a label;
most of the console's `lib/plan-mode.ts` was used only by its own test.

**Reference designs.** Claude Code: one permission mode the user cycles
(default / acceptEdits / plan / bypass), rules allow/ask/deny from several
sources evaluated deny-first, safety checks that no mode bypasses, one
decision function, the prompt offers "yes / yes and don't ask again for this
/ no, tell Claude what to do". opencode: an agent (build / plan) plus a
permission map (allow/ask/deny per tool and pattern); the client renders the
ask and returns once / always / reject.

## Decision

### D-1 One mode

The session has one mode, `chat | plan | autopilot`, stored in
`metadata.interaction_mode` (unchanged key, so saved sessions resume). The
permission behaviour is **derived** from it and never stored:

| Mode | `PermissionManager` mode | What runs without a card |
|---|---|---|
| `chat` (default) | `auto_safe` | read-only tools; everything else asks |
| `plan` | `auto_safe` + turn refusal | read-only tools only; other tools are refused (not asked); the first reply is a plan |
| `autopilot` | `trust` | every tool, except what D-3 always asks; with a server checker only server-allowlisted tools (ADR-035) |

One module owns the rule: `r4/companion/agent_mode.py`
(`normalize_mode`, `permission_for`, `session_mode`, `apply_mode`). Every
surface (bridge RPC and slash, ACP, plan confirmation, session restore, the
settings document) calls it. `apply_mode` also wires the server trust
checker whenever the result is `trust` (defect 4).

`permission_override` and `auto_approve_tools` are retired: read once when a
session is restored (`trust` or `auto_approve_tools: true` → `autopilot`
unless the session is in plan mode; anything else is dropped) and removed.
`ask_always` and `deny` stay in `PermissionMode` for non-session callers
(the plan/gate CLI lanes ask a terminal per call), not as a session mode.

*Why one axis (agent decision):* the only states anyone used were the three
modes; `ask_always` (ask even for reads) was raised to `auto_safe` by the
bridge anyway, and `deny` is better said by a deny rule. Claude Code's
`acceptEdits` has no R4 analogue worth a fourth mode today; a G14 allow rule
gives the same per tool.

**Plan acceptance changes the mode** (Claude's ExitPlanMode): the plan card's
`a` switches to `autopilot`, `m` to `chat`, then the plan runs. The badge
shows it; leaving is an ordinary mode switch. A stopped execution returns to
`plan` with the plan still awaiting confirmation (as before).

**Surfaces.** Shift+Tab and `/mode` change the mode. `/trust` (alias
`/yolo`) is `/mode autopilot`. `/approve`, `/allow-all`, the
`set_permission_mode` RPC and Alt+M are retired; `--allow-all-tools` /
`--yolo` launch in autopilot. The console shows one badge, `mode:<m>`, with
`autopilot(server)` / `autopilot(legacy!)` for the trust source.

**Remote settings document** (ADR-099 D-8, schema 1): unchanged shape.
`interaction_mode` is authoritative; `permission_mode` is written derived
from it (so the umrs validator and older workers keep working); a document
carrying only `permission_mode: trust` (an older client) applies as
`autopilot`.

### D-2 One decision

`PermissionManager.decide(tool, args)` returns a `Decision(behavior,
source, reason, fenced, escalate)` from one ordered ladder; `check()` is
`decide()` then resolve (deny → no; allow → the ADR-076 gate when
`escalate`; ask → the approval callback, fenced or not). The ladder, first
match wins:

| # | Rung | Result |
|---|---|---|
| 1 | session mode `deny` | deny |
| 2 | G14 `deny` rule | deny |
| 3 | never-auto tool (MD-5, ADR-036) | ask (per call) |
| 4 | taint fence (ADR-039) | ask, fenced |
| 5 | credential / identity / force-push fence | ask, fenced |
| 6 | G14 `ask` rule | ask, fenced |
| 7 | protected path (also inside `apply_patch` text) | ask, fenced |
| 8 | template crew `teamup_start` (its plan card asks) | allow |
| 9 | G14 `allow` rule | allow (gated) |
| 10 | `trust`: server allowlist hit or no checker | allow (gated) / miss → ask |
| 11 | `auto_safe` and a read-only tool | allow |
| 12 | session "always" for the tool | allow (gated) |
| 13 | otherwise | ask |

Rungs 1–7 are the safety floor: no rule, mode or grant passes them (fixes
defects 6, 7). An agent fork's MD-6 grant turns only rung 13 into allow
(defect 7). Rules match with `fnmatch` on the tool name (defect 6).
`apply_patch` joins `RISKY_TOOLS`.

A denied call hides its tool for the session only when the deny is
tool-wide (rung 1, or a rule with no argument pattern); a person's "no" or a
fenced/argument-specific deny never hides the tool (defect 8).

### D-3 The companion answers; the console renders

All remembering lives in the companion. The console never answers an ask by
itself (defect 5).

**Ask** (bridge → console, legacy line kept, one field added):

```json
{"approval_request": {"id": 7, "tool": "shell_exec", "description": "...",
  "agent": "", "risk_tier": "risky", "fenced": false, "fence_reason": "",
  "session_approval_allowed": true,
  "options": ["once", "always", "always_project", "reject"],
  "diff": "...", "path": "..."}}
```

`options` is the server's word on what this ask may be answered with:
`["once", "reject"]` for a never-auto or fenced ask, else all four. The
project is the companion's (the session's project, else the profile's, else
`default`, the console's old name for "no project"); the answer carries no
project id. A console that predates `options` derives it from
`session_approval_allowed`.

**Answer** (console → bridge): `{"approval_response": {"id": 7, "decision":
"once" | "always" | "always_project" | "reject"}}`. `yes` → `once` and
`no` → `reject` are accepted (old consoles); an unknown word or a word not
in the ask's `options` is downgraded (`always*` → `once` when not offered;
anything else → `reject`). Remote asks keep `session.venue_decide`
(`once | always | reject`).

**Remembering:**

| Answer | Effect | Store |
|---|---|---|
| `once` | this call | — |
| `always` | the tool for this session (rung 12) + server trust row `session:<id>` | memory; settings document `session_approved_tools` |
| `always_project` | a user-level allow rule for this project (rung 9) + server trust row `project:<id>` | `~/.config/r4/projects/<id>/permissions.json` (`{"allow": [...]}`) |
| `reject` | this call; the agent is told it was the user | — |

The rule files are one G14 loader with three sources, all deny-first:
`~/.config/r4/permissions.json` (user, allow/ask/deny),
`~/.config/r4/projects/<id>/permissions.json` (user per project,
allow/ask/deny; the console's old list format `[{"tool": …}]` is read as
`allow` entries) and `<workspace>/.r4/permissions.json` (checked in,
ask/deny only), reloaded when a session is configured or restored, so a
rule written by one surface binds the others (defect 11). The rule files
themselves are protected paths. `/settings rules` lists and removes through the bridge
(`permission_rules.list` / `permission_rules.remove`), never by editing the
file in TypeScript.

**Console.** Pending asks queue per tab; the card shows the head, the next
appears when it is answered (defect 10). Only Esc on the card itself rejects
it. The card's keys follow `options`: `y`/Enter once, `a` always (session),
`p` always (project), `n` reject with feedback, `r`/Esc reject.

### Out of scope

The legacy `r4 agent` CLI (`agent/core.py` `AgentMode`, `egress_policy`,
`EgressGuard`): a separate engine with its own defects, recorded in the
ledger entry for a later wave. Plan-mode refusal stays a turn-level refusal
(wave 80, prefix caching), not a ladder rung. The Jupyter magic keeps
setting `PermissionMode` directly (no session modes there).

## Consequences

- One place to read what a mode means (`agent_mode.py`) and one table for
  what a call does (`PermissionManager.decide`). The console carries no
  copy of either.
- Behaviour changes users see: accepting a plan visibly switches the mode;
  `/approve` and `/allow-all` are gone (`/mode`); Alt+M is free; after one
  "yes" the next call of the same tool asks again (that was the bug);
  "always this project" now also binds sessions opened from the CLI and ACP.
- Old sessions resume in the mode they effectively had (`permission_override
  = trust` → autopilot).

## Amendment 1 (2026-11-03): autopilot runs; no project-wide answer

**Maintainer, 2026-11-03:** "now autopilot(server) asks tool call approvals
permission, which is not what users expects for autopilot mode. can you
summarize the problem of this design? also when approval, it may be
unnecessary to allow for project wide." — then "good. please proceed for
implementation". Details decided by the agent under the 2026-09-24
delegation; reasons below.

**What was wrong** (verified on `main` `7d52afa76` by running the ladder):

1. Signed in, autopilot consulted the server trust allowlist (ADR-035) for
   every tool, and only a trust row allowed one. Nothing seeds those rows;
   they appeared only when a person answered `a` or `p`. So
   `autopilot(server)` asked for every tool of a new user.
2. D-2 placed the TRUST rung (10) before the read-only rung (11) and the
   session rung (12), so a server "no" asked even for `file_read` — autopilot
   was stricter than chat — and an `a` did not stop the next ask unless the
   self-granted trust row was written (sign-in) and read back (a 10 s
   negative cache was never cleared).
3. The `p` answer wrote a project trust row the check never read: the client
   asked with `session:<id>` scope and the server's fallback chain is
   session → system only.
4. The allowlist was not a policy: users granted rows to themselves, so it
   could refuse nothing they wanted; it duplicated the session memory.
5. `p` ("always for this project") wrote a durable, argument-blind allow rule
   on one keypress — `p` on `shell_exec` allowed every command in every later
   session of the project, in chat too.

**Decision.**

- **A1-1 Autopilot runs what the floor lets through.** The ladder's
  allowances are now: plan card (8), allow rule (9), read-only tool (10, in
  chat, plan **and** autopilot), session "always" (11), autopilot (12),
  otherwise ask (13). Rungs 1–7 are unchanged.
- **A1-2 The server allowlist is an opt-in organisation policy.** The
  checker is wired only when `R4_AUTOPILOT_SERVER_POLICY=1` (off by default)
  and consults the session's project scope (`project:<id>`, falling back to
  system on the server). When on, it narrows only rung 12: read-only tools
  and a session "always" still run. Badge: `mode:autopilot` by default,
  `mode:autopilot(policy)` when the policy is on; `trust_source` is `local` /
  `server` (`legacy` retired). ADR-105 D-4 is unchanged: without a checker,
  autopilot asks before `job_commit`.
- **A1-3 Approvals are once / always (this session) / reject.** `p` and
  `always_project` are removed from the card and the wire; an older
  console's `always_project` or `p` counts as `always`. An `a` lives in the
  companion's session memory only — no self-granted trust row is written.
  The per-project user rule file still loads and `/settings rules` still
  lists and removes its entries; a standing rule is written on purpose.

*Why not keep `p` with a narrower scope (agent decision):* Claude Code's
equivalent is a command-prefix rule, opencode's "always" lasts the session.
A prefix-scoped answer is a possible later addition; a tool-wide durable
grant on one keypress is not.

**Consequences.** Autopilot behaves as users expect (asks only at the
safety floor, never-auto tools, compute commits without a policy). Removed:
`approval_answers.ALWAYS_PROJECT`, `permission_rules.add_project_allow`,
`CompanionBridgeServer._record_durable_trust`, the console's trust-lane
probe. Trust rows written before this amendment expire (24 h TTL) or die with
their session.

## Amendment 2 (2026-11-05): true autopilot; a delete is backed up first

**Maintainer, 2026-11-05:** "the autopilot mode is still not a real
autopilot. we need true autopilot mode (except file delete if no full backup
exists)". Answers to the three questions it raised (same day): keep the
knowledge decisions asking; of the fences keep only credential access; a
full backup is one R4 makes itself (or a git-committed, unchanged file).

**Decision.** In autopilot (TRUST) the ladder no longer asks at:

| Rung | Before | Now in autopilot |
|---|---|---|
| never-auto: `team_plan_run` (ADR-092 MD-5), plan gates | ask | run (noted) |
| never-auto: `knowledge_graph_decide`, `research_loop_decide` | ask | **ask** (CONSTITUTION §2, unchanged) |
| taint fence (ADR-039) | ask | run (noted) |
| credential / identity probe, force-push, unscannable wrapper | ask | **ask** |
| plain `git push` (`risky_no_trust`) | ask | run |
| `ask` rule the user wrote | ask | **ask** |
| protected path | ask | run (noted) |
| `job_commit` without a server checker (ADR-105 D-4) | ask | run as an **auto** commit: UMRS refuses it over the project's auto-commit threshold or with none set |
| ADR-076 decision-gate doubt | ask | noted, runs |
| deny rule, organisation policy (amendment 1) | deny / ask | unchanged |

**A delete nobody approved is recoverable** (`companion/delete_guard.py`).
Every allowed call that no person was asked about (autopilot, an allow rule,
a session "always") is scanned for deletes: `apply_patch` `Delete File`;
`rm`/`rmdir`/`unlink`/`shred`/`git rm` (also inside `bash -c`);
`os.remove`/`os.unlink`/`os.rmdir`/`shutil.rmtree`/`Path(...).unlink()`.
Each target that git does not hold committed and unchanged is copied first
to `~/.local/share/r4/trash/<time>-<id>/<workspace>/<path>`; then the call
runs. The call asks (per call, `y`/`n`) when R4 cannot make the backup: a
glob, a variable or a substitution in the targets, `git clean`,
`find -delete`, `xargs rm`, a computed path in Python, a target outside the
workspace or the workspace itself, more than 512 MiB, a failed copy. A call
a person approved is not copied (the person decided). The guard reads the
call's text; a program that deletes in some other way is not seen — the
local sandbox (ADR-101) is the containment.

**Visible, not asked.** Each thing autopilot let through that used to ask,
and each backup, is a `[autopilot] …` line in the transcript
(`permissions.note_permission`, drained by the agent after every check).

**Consequences.** Autopilot asks for four things only: credential access
(and force-push), a knowledge decision, the user's own `ask` rules, and a
delete it cannot back up — plus an organisation policy when one is on.
Amends ADR-092 MD-5 (plan runs and gates in autopilot), ADR-105 D-4
(`job_commit` in autopilot without a checker), ADR-076 D-3 (the gate's
question becomes a note in autopilot) and ADR-039 (the taint fence asks
outside autopilot only); CONSTITUTION §2 unchanged.

## Amendment 3 (2026-11-09): literal local autopilot; filesystem/worktree deletion asks

**Maintainer decision.** In answer to the renewed request for truly automatic
work, the maintainer selected **“only file deletion asks”**, explicitly
including the other local prompts that amendment 2 retained, and selected
**“user files and worktrees”** rather than every destructive backend operation.
Backend authorization and sandbox constraints were explicitly kept.

**Decision.** `PermissionManager.resolve` converts every non-deletion local
`ask` in TRUST into an audited allow after the complete ladder has classified
it. This includes local prompts for:

- `knowledge_graph_decide` and `research_loop_decide`;
- credential/identity probes and force-push;
- user `ask` rules, organisation-allowlist misses/errors, saved-memory and
  inter-session taint, protected paths and decision-model doubts;
- local team-plan runs and gates, including callback-free autopilot.

Explicit denials still deny. The `[autopilot] …` transcript note records the
original decision source. Automatic resolution creates no human-approval
record, MD-6 card grant, human compute-commit claim or human gate provenance.
Automatic plan-gate answers are not journalled for replay: a resumed chat
session must ask again; autopilot simply decides again without a card.

**Backend ownership is unchanged.** These local answers do not manufacture a
human actor or approval. UMRS's agent-knowledge decision guard and proposal
state, research-loop policy/durable inbox, compute auto-commit threshold,
HPC signature/person-presence checks, workflow gates, pre-tool blocks, tool
availability, agent-only deny sets and OS/workspace sandboxes remain in force
and may still refuse execution. `R4_AUTOPILOT_SERVER_POLICY` may still produce
a policy decision and the `autopilot(policy)` badge, but its local prompt is
automatically answered rather than blocking autopilot.

**`ask_user` follows the same mode.** Local bridge, ACP and remote-worker
autopilot return the existing bounded auto-answer instruction: choose the
most reasonable option, state the assumption and continue. Chat still follows
`ask_user_mode`; remote deletion approvals continue through the durable
venue-approval lane.

**Deletion is different.** In TRUST every recognised user-file deletion and
agent-issued worktree removal asks, even when the target is git-recoverable
or R4 could copy it. No backup is created before that yes. Other automatic
allow paths retain amendment 2's backup-and-run behaviour. Recognition covers
the existing shell/Python/patch patterns, patch rename sources,
`worktree_exit(action="remove")`, and merge-back batches through their exact
structured `Change(kind="delete")` paths—not removed diff lines. Non-deleting
merge-backs run automatically. Internal temporary-workspace cleanup and
backend resource/memory deletion are outside this amendment's selected scope.
The textual guard remains best-effort, not a claim to recognise arbitrary code
or MCP side effects; OS sandbox containment is unchanged.

**Subagents inherit the lead's effective permission view.** Ordinary spawned
children receive an isolated permission fork, fixing the previous unwired
fail-closed child without widening its tool allowlist or the `TEAM_AGENT_DENY`
set. Child-session transcript persistence is planned separately in
the subagent visibility plan.

**Supersedes and preserves.** This supersedes amendment 2's backup-and-run
rule in TRUST and its remaining local prompts, and qualifies ADR-092 MD-5's
local interactive gates, ADR-099's `ask_user` default, ADR-108's agent-issued
worktree removal, and ADR-111/114's every-mode local taint prompts. It does
not amend CONSTITUTION §2 or any server-owned knowledge, loop, HPC or workflow
authorization rule.
