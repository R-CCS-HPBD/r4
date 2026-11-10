# ADR-108: Work levels — session, worktree, loop, teamup — each with its own git worktree and its own UMRS scope

**Status:** Accepted (2026-11-03, maintainer on PR #707: "good. please proceed"). First Proposed (2026-11-03). The maintainer asked for this design and chose its three open options: "go with your recommendations": D-4 scope tags (not a graph per branch), D-5 one merge for files and knowledge, and D-7 start-time grants for unattended writes. Drafted by the agent.
**Date:** 2026-11-03
**Relates to:**
- [ADR-084](084-growth-governance.md): the proposed/established tiers. D-4 and D-5 build on them. ADR-084 is still Proposed; see D-0.
- [ADR-092](092-team-plans-declarative-orchestration.md): team plans; slice 5 workspace isolation (`isolation: worktree|copy`, `merge_back`).
- [ADR-093](093-retrieval-for-small-local-models.md): `graphs=` on every retrieval lane.
- [ADR-099](099-session-policy-and-quotas.md): the session's web-access choice.
- [ADR-067](067-loop-budgets-per-project-caps.md): loop budgets.
- [ADR-097](097-live-project-membership.md): the live project check.
- [ADR-106](106-service-model-tier.md): service-model roles.
- ADR-025: research sessions.
- Wave ledger (174)–(177).

## Context

Verified 2026-11-03 at parent `4e0b64a11`, umrs `b81af636`.

**Maintainer direction.**
- Isolation should follow Claude Code's worktree model and be available in a normal session, in teamup and in loops.
- The work is hierarchical: session → worktree → loop/teamup.
- Before planning, UMRS ingestion, knowledge/RAG build and workflows must be usable natively at every one of those levels.

**What exists.**
- **Team plans.** `isolation: worktree` does `git worktree add -B r4-plan/<run>/<id> <path> HEAD` from the repository the run's project root is in (`r4/src/r4/teams/plan/workspace.py:217-229`).
  - Writes are confined to the workspace (`r4/src/r4/companion/agent_workspace.py`).
  - A human-approved `merge_back` writes files back (`teams/plan/run.py:875`).
  - `r4 team-plan clean` removes plan worktrees.
  - The default is `none`, and no shipped template, `research-round` included, isolates.
- **No other surface has a worktree.** A session has no `/worktree`, teamup has no `--isolation`, and local loops have none.
- **Loops run teams; teams never start loops.** A plan loop runs one `research-round` team per iteration (ADR-092 slice 6, `runtime/plan_executor.py`).
- **UMRS has no idea of the user's branch.**
  - Ingest sends basenames only (`r4/src/r4/knowledge.py:106`, `:714`).
  - Workflow uploads keep only the server-side `source_path`.
  - Run manifests record the server's own git revision (`umrs/src/umrs/jobs/manifest.py:77-83`), not the user's.
  - Workflow outputs land in the job spec's `graph_id`, else the project's default graph (`umrs/src/umrs/jobs/knowledge_adapters.py:1660`).
- **Tiers exist only on nodes and edges.** `knowledge_nodes` and `knowledge_edges` carry `status` `established|proposed` (migration 0106); artifacts do not.
- **The agent marker is self-declared.** Agent mutations need an approved `proposal_id` (`umrs/src/umrs/api/_knowledge_mutation_guard.py:47-109`), but the `x-r4-actor` header that marks an agent is self-declared (ADR-084).
- **Most retrieval ignores graph scope.**
  - `search_docs` and the local loop's `rag.retrieve` pass no `graphs=`.
  - Only `knowledge_query` honours graph scoping.
  - Native teamup roles without a host manager get no `knowledge_query` and no workflow tools.
- **Unattended plan agents can't write to UMRS.** Plan agents under a loop or the CLI run with no lead manager. Every risky tool (`knowledge_ingest`, `workflow_run`) is refused fail-closed (`runtime/plan_executor.py:84-88`).

**What goes wrong without a decision.**
- A loop's experiment writes straight into the project's knowledge, indistinguishable from established findings.
- Discarding its branch leaves that knowledge behind, and merging it promotes nothing.
- Unattended levels can't ingest or run workflows at all, or they would need a person at every call.

## Decision

**D-0. Prerequisite: the tiers of ADR-084 D-1/D-2 are adopted as this ADR's base.**
- Proposed vs established is the knowledge half of a branch.
- This ADR does not reopen ADR-084's other decisions.
- Landing D-4 requires ADR-084's tier rows to be accepted, and extended to artifacts (D-4).

**D-1. Four levels, one tree.**
- The levels are **session → worktree → loop → teamup**.
  - A session may enter a worktree.
  - A loop or a teamup starts from a session or a worktree.
  - A loop's iterations may run teamups (plan rounds).
- A teamup started directly from a session or worktree is a sibling of loops, not their parent.
- A teamup does not start loops: the deny set already refuses `research_loop_start` to team agents.
- Every level has:
  - an **owner** (the level that created it);
  - a **lifetime** (a durable loop can outlive the session that started it);
  - a **workspace** (D-2);
  - a **scope** (D-3).
- The tree stops at the client/server boundary. A level the server runs (a UMRS loop, a venue run) works on the server's own copy and carries the scope of the level that started it.

**D-2. One worktree mechanism, modeled on Claude Code.**
- **One module** generalised from `teams/plan/workspace.py` serves every level.
- **Location and branch.** A worktree lives at `.r4/worktrees/<id>`, in flat directories: no worktree is nested inside another's directory. Its branch name carries the path: `r4/<session>[/loop-<id>[/iter-<n>]][/team-<run>/<role>]`.
- **Base.** A child is based on its parent's HEAD.
  - The parent's uncommitted work is first saved as a snapshot commit on a hidden ref (`refs/r4/snap/<id>`).
  - The parent's branch and index are not moved.
  - A child therefore sees what its parent sees. The top level's base is `worktree.base_ref`: the default branch or HEAD.
- **Safety.** Submodules are initialised in the new worktree. A directory that is not a git repository falls back to `copy`. The user's checkout is never stashed, reset or written.
- **Surfaces.**
  - Session: `/worktree [name]`, `/worktree exit keep|remove`, `/worktree list`, `r4 --worktree <name>`, a `wt:<name>` status chip, and a keep/remove question when the session ends.
  - Teamup: `--isolation worktree|copy|none`.
  - Loops: a loop whose iterations can write gets a worktree by default; a read-only loop gets none.
  - `research-round` sets `isolation: worktree` on its writing steps.
  - The same RPCs serve the VS Code extension (ADR-103).
- **Lifecycle.**
  - An unchanged worktree is removed when its owner ends.
  - A changed one is kept until its owner merges it (D-5) or discards it.
  - A parent with live children is never removed. Cleanup runs bottom-up.
  - `r4 worktree list|clean` covers every level; `r4 team-plan clean` stays as an alias.
  - *2026-11-09, ADR-104 amendment 3:* an agent-issued `worktree_exit(action="remove")` asks in autopilot rather than silently discarding files; R4-owned temporary cleanup remains outside this prompt.
- **Writes** stay inside the level's own worktree. This is the ADR-092 guard, extended to sessions and loops. Reads may reach the parent's tree.

**D-3. Every level has a UMRS scope, carried as a signed token.**
- **Fields.** A scope is `{project, path, parent, repo, branch, commit, grants, owner user, expiry}`. `path` is the level path, e.g. `s-ab12/loop-7/iter-3/team-r9/ED`.
- **Issue.** At level start, UMRS issues a **delegated scope token** on the parent's verified identity. It is signed like the handshake HMAC and bound to the user's live project membership (ADR-097).
- **Use.** Every UMRS call the level makes (ingest, knowledge read/write, files, workflow trigger/upload, findings) carries the token.
- **Verify.** The server checks the token: user, project, path, grants, and not revoked. This replaces the self-declared `x-r4-actor` for calls made under a scope.
- **Revoke.** Stopping or removing a level revokes its token and every token beneath it.
- **The root scope.** The session's root scope is the user's plain identity. Its writes are as today: interactive, established where ADR-084 allows.

**D-4. Knowledge is an overlay of scope tags, not a graph per branch.**
- **Writes.** A write made under a non-root scope lands **proposed**, tagged with the scope path. That covers:
  - an ingested artifact;
  - nodes and edges from understand;
  - a finding;
  - a workflow output.
- **Schema.** The tag is a `scope_path` column next to `status` on the tiered rows (`knowledge_nodes`, `knowledge_edges`, migration 0106). The same pair is added to knowledge artifacts.
- **Reads.** Every read under a scope sees **established rows plus rows proposed in its own scope and its ancestors' scopes**, never a sibling's or a child's. That covers RAG retrieve, `search_docs`, `knowledge_query`, graph reads and the loop executors.
- **Named graphs stay as they are.** They are for topics people curate, and `graphs=` keeps working on top of the overlay. No graph is created per branch, because agents may not create graphs.

**D-5. One merge moves files and knowledge up together.**
- **Merging a child into its parent** is one human-approved action. It merges the branch, as `merge_back` does for plans, and re-tags the child's proposed rows to the parent's scope.
- **At the top,** merging the session's worktree into the base branch also offers to **establish** the merged proposals. Establishing stays human-only (ADR-084): never an agent, never auto-approved.
- **Discarding** a worktree archives its proposed rows, which then stop showing in reads. No delete; the reason is recorded.
- **Files and knowledge cannot drift.** Promotion is per scope: there is no promoting a child's knowledge while dropping its branch, or the reverse.

**D-6. Provenance on every write.**
- Ingest records the path relative to the repository root, plus repo identity, branch, commit and a dirty flag.
- Workflow uploads and runs record the source commit of the uploaded tree, plus the triggering scope.
- Findings and outputs record their scope.
- The server's own `code_version_ref` stays as it is: it is the server's version, a separate fact.

**D-7. Unattended levels write only under a start-time grant.**
- **What a grant is.** When a loop or a teamup starts, the user grants a capability set:
  - ingest (yes/no);
  - web fetch, within the session's ADR-099 choice;
  - workflows (an allowlist);
  - data upload (a size cap);
  - each inside the project's ADR-067 budget.
- **Where it lives.** The grant is written into the scope token, so the server enforces it.
- **Outside the grant.** A call outside it is refused by name, never asked.
- **Interactive levels** (session, worktree) keep today's per-call approvals.
- **This replaces** the current fail-closed refusal for plan agents under a loop. Agents still never decide proposals or establish.

**D-8. The same UMRS tools at every level.**
- Each level is offered the same families: ingest, knowledge read/propose, files, workflows (list/describe/run/status/results), and web per D-7.
- They're filtered by grants and permissions, not by which surface the level runs on.
- Concretely:
  - native teamup roles get `knowledge_query` and the workflow read tools;
  - `search_docs` and the loop executors pass the scope (D-4);
  - the platform-down filter also covers `research_*`, `knowledge_build_from_context` and `project_ensure`.
- **A loop iteration may** call `web_search` and `web_fetch` (within its grant) and **request ingest** of a source it found, landing proposed in its scope. The server's pipeline then reads the file: OCR, vision, embedding.

**D-9. Models per level.**
- **Reasoning** uses the session's model wherever the client drives the level (ledger (177)). A level the server runs uses `svc-chat`, and the start reply says so.
- **Knowledge work** uses the server's service roles: extraction, OCR, vision captions, embedding and rerank, via an ADR-106 amendment. It never follows the session's model. One embedding model per index is a correctness requirement, and data stays on the platform.
- **Data class.** The data-class gate checks the **scope's** project, not the console's current project.

### Why

- **Git and UMRS each held half the picture.** Branches without knowledge scopes lose the knowledge a branch made; knowledge without branches can't be reviewed, merged or discarded with the work. One tree with one merge keeps them in step.
- **Tags, not graphs.** Scope tags reuse ADR-084's tiers and ADR-093's filters and need no graph creation. A graph per branch would multiply graphs without end and needs a human per branch.
- **Signed scopes.** Unattended levels act for a user for hours. A self-declared header can't be trusted for that; a revocable, grant-carrying token can.
- **Grants instead of prompts.** Prompts make unattended levels useless; fail-closed refusal makes them unable to learn. A grant fixed at start is the one point where a person decides.

## Consequences

- **Positive.**
  - Every level can learn: ingest, read, run workflows.
  - What a level learns is visible to it and its children, and promoted or discarded together with its code.
  - A whole loop's work is reviewable as one branch plus one set of proposals.
- **Costs.**
  - A UMRS migration: `scope_path` on tiered rows, tiers on artifacts.
  - A scope-token issuer and verifier.
  - The overlay filter on every read lane.
  - Snapshot refs in the user's repository; `r4 worktree clean` removes them.
  - ADR-084 has to be accepted first.
- **Not in scope.**
  - Server-side worktrees (the server already works on copies).
  - Non-git version-control hooks (a later amendment, like Claude Code's `WorktreeCreate`).
  - Branch-level access control beyond the scope's owner and project.

## Alternatives Considered

- **A named graph per branch.** Rejected: graphs multiply, agents can't create them, and reads would need the whole ancestor chain listed.
- **Knowledge independent of git.** Rejected: merging and discarding a branch would leave knowledge behind.
- **Every unattended write asks a person.** Rejected: it defeats loops.
- **Keep the fail-closed refusal.** Rejected: loops could never ingest or run workflows.
- **Teamup above loop** (the maintainer's first ordering). Kept as a sibling instead: loops already run teamups per iteration, and no team starts a loop.

## Rollout-status (2026-11-03)

Nothing landed yet. Planned waves; each carries its own ledger entry:
1. **W1 (client).** The worktree module and the session surface (D-2); `r4 worktree list|clean`; the data-class gate on the scope's project (D-9); the platform-down filter, and `graphs=` passed through by `search_docs` and the loop (D-8).
2. **W2 (umrs + client).** Scope tokens (D-3); provenance on ingest and workflow upload/run (D-6).
3. **W3 (umrs + client).** The overlay (D-4: migration, write tagging, read filter on every lane); merge = promote, and discard (D-5).
4. **W4 (client + umrs).** Teamup and loop isolation (D-2); grants (D-7); loop web and ingest-request tools; teamup role tool parity (D-8); the UMRS agentic loop reads the stored spine (it reads an empty in-memory graph today, found by reading the code, not yet run).
5. **W5 (umrs, ADR-106 amendment).** The knowledge pipeline on service roles (D-9): extraction on `svc-fast`, OCR in the job chain on `svc-ocr`, captions on `svc-vision`. `svc-embed` comes with a re-embed migration, because the vector size changes.

## Rollout-status (2026-11-03, acceptance)

Accepted by the maintainer on PR #707 ("good. please proceed"). W1 starts on `wave/adr108-w1a` (client fixes) and `wave/adr108-w1b` (worktree module and session surface). ADR-084's tiers are still Proposed; W3 waits on them.

## Rollout-status (2026-11-03, W1)

- **W1a** (ledger (179)): the data-class gate asks the loop's project (D-9); `search_docs` and the local loop's retrieve pass the user's graph constraints (D-8); `knowledge_build_from_context` is hidden while the platform is down. Test: `r4/tests/test_adr108_w1a.py` (11).
- **W1b** (ledger (180)): D-2 at the session level.
  - `r4/worktrees.py`, `r4 worktree list|create|remove|clean`, `r4 --worktree NAME`.
  - Bridge `worktree.list|enter|exit`, with `CompanionSession.rebind_workspace()`.
  - Console `/worktree`, the `wt:<id>` chip, and `resolveWorkspace()` following the tab.
  - Tests: `r4/tests/test_worktrees.py` (12), contract cases (3 verbs), TUI `tests/worktree.test.ts` (8).
- **Open from W1.** The quit-time keep/remove question (quitting keeps every worktree; recorded deviation from Claude Code). Teamup, loop and plan worktrees on the shared module (W4). Submodules are initialised best effort, a failure is a warning.

## Rollout-status (2026-11-04, W1c)

Maintainer 2026-11-04: "worktree should be automatically build by agents on cli, tui, vscode when it is enabled".

- **Landed (ledger (181)).**
  - `r4/companion/session_worktree.py`: one `WorktreeController` per session, driven by every surface. The bridge verbs delegate to it.
  - **Mode** `off|auto`: the session's `/worktree mode`, else `R4_WORKTREE`, else the profile's `worktree_mode` (`r4 config worktree`), else off.
  - **In `auto`,** the agent's first writer call (the ADR-092 writer set), once approved, moves the session into `auto-<session>`. The note leads that tool's result.
  - **The guard.** Every tool call in a session worktree runs under the `agent_workspace` guard: writes inside the worktree only, reads also from the origin checkout.
  - **Agent tools:** `worktree_enter` (SAFE) and `worktree_exit` (RISKY).
  - **`worktree_event` lines** go to the console and VS Code (a chip plus a notice). There is a new `worktree.mode` verb.
  - **End of session (Claude Code's rule):** an unchanged worktree is removed and a changed one kept. This closes W1's quit-time deviation without a dialog.
  - **VS Code:** `/worktree` and the agent's notices; the parity row is partial (no status-bar item).
- **Tests:**
  - `r4/tests/test_session_worktree.py` (12). It includes an end-to-end run of the real agent and the real `file_write` in auto mode: it lands in the worktree, an absolute write into the checkout is refused, and the checkout stays clean.
  - Contract cases for `worktree.mode`.
  - TUI `tests/worktree.test.ts` (13); VS Code `test/console-tools.test.mjs` (8).
- **Still open.** A PTY journey on a live console. The VS Code status-bar item. Teamup, loop and plan worktrees (W4).

## Rollout-status (2026-11-04, W2a)

- **D-6 for knowledge ingest** (ledger (182), umrs `fd267120`).
  - **Client.** `r4/git_provenance.py` reports, per ingested file, its repository-relative path, the origin host/path (credentials stripped), branch, commit and dirty flag. It goes on inline specs and as the upload's `provenance` form field.
  - **Server.** UMRS keeps a bounded, `client_reported` copy in the artifact's `metadata.provenance` (`umrs/knowledge/client_provenance.py`). A malformed block is dropped, never a failed ingest.
  - **Tests:** r4 `test_git_provenance.py` (10), umrs `test_client_provenance.py` (8).
- **Open for W2b.** Workflow upload/run provenance. The D-3 scope tokens. Showing provenance in `knowledge show` and the console.

## Rollout-status (2026-11-04, W2b)

- **D-3 scope tokens: server and client** (ledger (184), umrs `0a2d5496` + `f14aaf34`; surface family `scopes` is v1beta).
  - **Token.** `base64url(json).base64url(HMAC-SHA256)`. The key is derived by HKDF from `UMRS_HANDSHAKE_HMAC_SECRET` with its own salt and info, so a handshake signature is never a scope signature. Claims: user, project, path, grants, iat/exp (12 h default, 72 h cap), jti, parent, and repo/branch/commit. Code: `umrs/scopes/tokens.py`.
  - **Issue and revoke.** `POST /gw/scopes` issues a token on the caller's verified identity. With `parent_token`, the child must sit beneath its parent's path, hold a subset of its grants, and expire no later than it. `POST /gw/scopes/revoke` revokes a path and every path beneath it, for the caller's own user and project. Revocations are kept in `scope_token_revocations` (migration 0132). Code: `umrs/api/scope_tokens.py`.
  - **Grants.** `ingest`, `upload`, `web`, `workflow:<name>`, `workflow:*`.
  - **Use.** A request carrying `x-r4-scope` is checked: valid signature, not expired, not revoked, and the same user and project as the request's gateway-verified identity. A token never adds a right; it only narrows what that caller may do.
    - Ingest and upload need `ingest`. They run as `origin_actor="agent"`, so their rows land proposed, and the scope path is stamped on the artifacts (`metadata.scope_path`).
    - A workflow trigger or a run-definition trigger needs `workflow:<name>`.
    - A request without the header is unchanged.
  - **Errors.** `scopes.bad_request` 400, `scopes.invalid` 401, `scopes.revoked` 401, `scopes.forbidden` 403, `scopes.grant_missing` 403, `scopes.unavailable` 503 (the revocation store is unreachable, so the request fails closed).
  - **Client.** In `r4/scopes.py`, `acting_in_scope(token)` makes every platform request inside it carry `X-R4-Scope`. A direct provider request never carries it. `ScopeClient.issue/revoke` wrap the routes.
  - **Tests:** umrs `test_scope_tokens.py` (15), and the H-1 identity guard. r4 `test_scopes_client.py` (3).
- **Not yet.**
  - Nothing issues tokens yet. Loops and teamups start doing so in W4.
  - The D-4 read overlay (W3) still waits for ADR-084.
  - Workflow upload/run provenance, and showing provenance in `knowledge show`, are W2c.
- **Deployed** 2026-11-05 on all four fronts at `7fae80ba` (umrs `48a35e5f`; ledger (198)). `/gw/scopes` is live behind the edge auth.

## Related

- [ADR-084](084-growth-governance.md), [ADR-092](092-team-plans-declarative-orchestration.md), [ADR-093](093-retrieval-for-small-local-models.md), [ADR-099](099-session-policy-and-quotas.md), [ADR-067](067-loop-budgets-per-project-caps.md), [ADR-097](097-live-project-membership.md), [ADR-106](106-service-model-tier.md)
- Wave ledger (174)–(177)
