# Agent onboarding — how an AI agent works here

**Status:** LIVE — written 2026-10-18 (plan H-11.5), for any vendor's agent
(Claude, Codex, Gemini, Copilot, …). The short version is
[AGENTS.md](../../AGENTS.md); this file is the method behind it. Reading
order: [docs/README.md](../README.md).

## 1. Before you touch anything

1. Read [AGENTS.md](../../AGENTS.md), then follow [docs/README.md](../README.md) §1
   as far as your task needs. Always read the plan in scope and its progress table.
2. Check STATE.md: automation mode is MANUAL; escalations
   name what is blocked on the maintainer.
3. Make your own worktree; never share a checkout with another session
   (plan H-3.2): `git fetch origin && git worktree add ../ai4s-<slug>
   origin/main -b wave/<date>-<slug>`, plus `git -C ../ai4s-<slug> submodule
   update --init umrs` when the wave touches umrs. Another session's
   uncommitted work in a shared tree is not yours to stash, reset or
   restore. Land the wave as one PR from that branch: the proposed
   convention (plan H-3.1, pending maintainer decision M-H8) is PR per wave,
   merged on green CI with a merge commit. Push to `main` only when the
   maintainer's wave grants it. After the merge, `git worktree remove` it.
4. Never guess: grep. Every path, symbol, count or status you write is read
   from the tree at write time. Line numbers in plans drift; re-locate by
   symbol name.

## 2. The wave contract

The contract block is in [AGENTS.md](../../AGENTS.md) "Wave contract" (binding
text: CONSTITUTION §9.2). What each step means in practice:

- **Verify**: reproduce the defect or confirm the gap in code before planning.
- **Decide or escalate**: decisions inside the granted authority are yours,
  recorded with reasons in the plan or ADR. Anything marked **[M]**
  (credentials, host edits, a deploy the maintainer did not ask for, ADR
  acceptance, merge policy M-H8) is the maintainer's: name the exact command
  and stop.
- **Implement**: every behavioural change ships with a test that fails on the
  old code. Run it against the old code once to prove it.
- **Gate**: AGENTS.md rules 1–2; commands in [developer.md](developer.md) §3.
- **Document**: in the same commit, the docs your change makes wrong (dated
  note), the gap-analysis row, the plan's progress table, one ledger entry.
- **Report**: the Report line of the contract, also written into the ledger
  entry so the next agent finds it.

## 3. The audit method (finding and fixing defect classes)

This method found 110+ TUI defects and the UMRS authorization inventory in
October 2026 (tui-function-audit-2026-10-17.md,
platform-hardening plan §1.4).

1. **Read-only finders**, one per surface: report candidate defects with
   file:line evidence; change nothing.
2. **Three independent verifiers per finding**: callee trace (does the code
   do what the finder says?), caller reachability (can a user reach it?),
   refutation (try to prove the finding wrong). Keep only findings that
   survive.
3. **File-owned fix lanes**: each lane owns a disjoint set of files; a lane
   never edits another lane's files. Each fix comes with a fail-before test.
4. **Adversarial review** of the combined diff.
5. **Full suites + gate** (commands in [developer.md](developer.md) §3)
   before anything is committed.

Scheduled sweeps only read and report (open issues); they never fix code.

## 4. Hand-off discipline

The next agent may be a different model with no chat history. Leave the
state where it will look:

- one dated entry in wave-ledger.md
  (append-only; never edit earlier entries);
- the plan's progress table (status, commit, date), same commit;
- escalations by name in STATE.md and the plan's table;
- a newly learned failure mode in the README, instruction file or ADR the
  next agent reads, never only in chat or private memory.

## 5. Gotchas that still bite (verified 2026-10-18)

- **umrs gates** run on the files changed between the two submodule pointers
  (`--umrs-changed`), not on the parent diff; new untracked umrs files are
  invisible until `git -C umrs add -N`.
- **The pre-commit hook and worktrees** (updated 2026-10-18, plan
  H-3.3/H-3.4). Hooks live in the shared `.git/hooks/` and run in every
  worktree. The tracked hook `scripts/git-hooks/pre-commit` blocks on the
  gate and runs it with every `GIT_*` variable removed; the r4 test helpers
  that create temp repositories (`r4/tests/git_hermetic.py`) strip them too.
  Before that, the old untracked `.git/hooks/pre-commit` let the hook's
  `GIT_*` variables reach `test_file_filtering.py` `_git_init`, which
  rewrote the shared `.git/config` (`core.bare = true`, user `test`).
  Check what is installed:
  `readlink "$(git rev-parse --git-common-dir)/hooks/pre-commit"` should
  print `../../scripts/git-hooks/pre-commit` (the maintainer installs it with
  `bash scripts/install-hooks.sh --force`; agents never write `.git/hooks`).
  If it prints nothing and that hook file exists, the old hook is still in
  place: from a linked worktree, run the gate by hand first
  (`bash scripts/ci-lint-local.sh --files <changed>` ([developer.md §3](developer.md#3-first-green-gate)), plus `bun run check`
  in `packages/r4-tui-v3` for v3 files), then commit with hooks off for that
  one commit: `git -c core.hooksPath="$(mktemp -d)" commit`. Either way,
  stage with `git add <paths>` and run a plain `git commit`, never
  `git commit -- <paths>`.
- **umrs work happens only in the `umrs/` submodule of this checkout.**
  Never run git in a separate umrs clone (the maintainer's machine has one
  beside the repository).
- **Running TUIs use the working tree**: a bridge spawned from a checkout
  imports uncommitted code. `r4 doctor` row `install_source` warns when the
  running `r4` package is a checkout with uncommitted changes (2026-10-18);
  daily use runs a pinned install ([developer.md](developer.md) §1).
- **Two catalogs**: the data catalog (`/umrs/catalog`, `r4 data assets`) is
  not the Scientific Catalog (`/umrs/scientific-catalogs`, `r4 catalog`;
  gap D-39, Partial).
- **One loop, one team seam**: extend the server-hosted loop engine and the
  Team Engine SPI ([ADR-072](../adr/072-one-team-seam-research-roles.md));
  never add another loop or team mechanism (CONSTITUTION, "One loop, one team engine").
- **Transport is dual by design** (ZeroMQ data plane + HTTPS control plane,
  [ADR-059](../adr/059-transport-and-edge-dual-transport-umrs-edge.md)). The
  r4 client reaches ZeroMQ (control 50110, data 50111–50118) by direct TCP
  and never opens an SSH tunnel itself. Server-side tunnels are deployment
  config: `UMRS_TUNNEL_MODE` on firewalled login nodes and the bdrt reverse
  tunnel to data0 ([ADR-018](../adr/018-reverse-ssh-tunnel-hpc-login-nodes.md)); gap row
  D-50 holds the facts.
- **Rootless podman on the front-end:** the remote-session egress nftables
  guard is privileged and opt-in (`umrs_front_frontend_venue_nft_enabled`,
  rootful only), so it is off on a rootless host; say so in any live receipt.
- **Tests never edit `sys.path`** or load modules by path; the import graph is
  the bug ([ADR-053](../adr/053-umrs-import-discipline.md)).
- **Generated files**: `.claude/r4-guide.md` and
  `.github/instructions/r4-agent-tools.instructions.md` come from `r4 guide`
  (the same playbook the R4 agent reads through its `platform_guide` tool);
  the source is `r4-contracts/src/r4_contracts/agent_tool_playbook.py`.
- **Codacy**: CONTRIBUTING step 4 and R4.md ask for Codacy analysis on
  every edited file. Its instruction file
  `.github/instructions/codacy.instructions.md` is gitignored, so a fresh
  clone lacks it; whether Codacy stays a project rule is maintainer decision
  M-H6. Until then: run it where your checkout has it, and where it does not,
  say in your report that it could not run.

## 6. Audit sweep (plan H-12; written 2026-10-18)

An audit sweep looks for whole classes of defects and reports them. It never
fixes anything itself (CONSTITUTION §9.1: scheduled and sweep work only
reads and reports). Fixes happen later, in an ordinary wave that the
maintainer grants.

**The weekly sweep.**
`.github/workflows/audit-sweep.yaml`
runs Mondays 04:41 UTC, or by hand with `workflow_dispatch` (a dry run by
default).

- It runs the static guards listed under surfaces 1–5 below, on the default
  branch.
- `tools/audit_sweep.py` turns their output
  into finding classes and keeps **one open issue per class**, labelled
  `audit`. Each issue carries a `<!-- audit-sweep:key=… -->` marker, and
  the sweep touches only issues that have that marker.
- When a class disappears, its issue body is marked **clear**. The sweep
  never closes an issue: a person closes it after checking the fix.
- If a guard that should have run did not report, that is a row in the
  `sweep-error` issue. The sweep never goes quiet. Without the umrs
  checkout, the enforcement validator runs without `--live`: its rows are
  still filed, but none of its issues is marked clear that week.
- The raw guard output is kept as the run's `audit-sweep` artifact for 30
  days.

To see the same report locally:

```bash
OUT=$(mktemp -d)   # fill it as the workflow's "Run the static guards" step does
python -m tools.audit_sweep --inputs "$OUT" --dry-run
```

**Surfaces.** Grep the code before relying on this list.

| # | Surface | Guard or source | In the weekly run |
|---|---|---|---|
| 1 | UMRS route authorization | `umrs/tests/test_h1_scope_guard.py`; plan Appendix A | yes |
| 2 | r4 client paths vs nginx and mounted routes | `tools/client_route_liveness.py --json` (H-10.2) | yes |
| 3 | New unauthenticated routes | `tools/route_inventory.py check` | yes |
| 4 | Mutating routes without an enforcement row | `tools/enforcement_matrix_validator.py --live --json` (ADR-035) | yes |
| 5 | Markdown links | `tools/md_link_check.py` (H-11.8), all tracked `.md`, no baseline | when the file exists |
| 6 | TUI functions (v3 slash catalog, keys, bridge RPCs) | tui-function-audit-2026-10-17.md; H-5.3 reachability test once it lands | manual |
| 7 | Object-id routes (Appendix A.2: load the row, compare its project) | not provable by the scope guard | manual |
| 8 | Deploy and CI workflows (permissions, masked exits, unguarded writes) | `tests/test_*workflow_contract.py` | manual |
| 9 | Docs against code (claims, counts, retired names) | AGENTS.md "Verify before cite"; `tools/doc_sanity_gate.py` | manual |

**The method, step by step.** This is §3 applied to a sweep. Sub-agents or
separate sessions run the finders and verifiers, read-only.

1. **Finder**, one per surface. Prompt:
   > Read-only. Surface: `<surface>`. List candidate defects of the class
   > `<class>` (for example: a route honours a requested project without a
   > check). For each one give `file:line`, the code path, the user-visible
   > effect, and the evidence you read. Do not edit, run git, or touch a
   > host. If you are not sure, report the candidate and say why.
2. **Three verifiers per candidate**, independent of each other. None of
   them sees the other verifiers' answers.
   - *Callee*:
     > Trace the code from `file:line` down. Does it do what the finding
     > says? Quote the lines.
   - *Caller*:
     > Trace up from `file:line` to an entry point (route, slash command,
     > CLI, job). Can a real user reach it, with which identity and inputs?
     > Name the path, or say "unreachable" with your evidence.
   - *Refute*:
     > Try to prove the finding wrong: a guard upstream, a test that pins
     > the behaviour, a config that turns it off. Report "refuted" with
     > evidence, or "stands".

   Keep a finding only when the callee and caller verifiers confirm it and
   the refute verifier fails to refute it. Record the others as refuted,
   with the reason, so the next sweep does not raise them again.
3. **Fail-before tests.** Before any fix, the fix wave turns each kept
   finding into a test that fails on the current code, and records the
   failing run.
4. **No auto-fix.** A sweep never edits code, commits, pushes or deploys.
   Fixes go to file-owned lanes in a wave the maintainer grants, as in §3.

**Report format**, one record per kept finding:

```
id:        <surface>-<nn>                  e.g. authz-07
class:     <finding class / audit issue key>
where:     <file:line> (+ entry point from the caller verifier)
effect:    <what a user sees or can do>
evidence:  callee <quote>; caller <path>; refute <why it stands>
severity:  P0 (security, data loss) | P1 (broken feature) | P2 (debt)
test:      <test that fails on the current code, or "to write">
owner:     <lane / file set that would fix it>
```

**From results to the ledger.**

1. Add one dated entry to
   wave-ledger.md per sweep. It names:
   - the surfaces swept, and the count kept and refuted per surface;
   - the `audit` issues opened or updated (`#nn`);
   - the gap rows touched (D-61 security watch, D-44 quality measurement,
     plus the row of each surface).
2. Put the kept findings in the plan or audit document that owns the
   surface, for example the hardening plan's appendices or the TUI audit,
   rather than only in the ledger.
3. Escalate by name in STATE.md and the plan's table. Anything that needs
   a credential, a host or a maintainer decision is **[M]**.
4. The fix wave's ledger entry cites the finding ids it closed. The `audit`
   issue is closed by hand once a sweep shows the class clear.
