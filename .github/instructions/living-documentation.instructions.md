---
description: 'Doc-freshness policy — update project docs and ADRs as work lands; keep date-stamped plan docs in the repo; defines how coding agents consume docs and where auto-debug truth lives.'
applyTo: '**'
---

# Living Documentation Policy

Purpose: agents must never plan against stale context. Docs rot == wrong
context for the next agent.

## 1. Update on progress (always)

- Any change that alters what a planning doc, overview, ADR status, or wave
  tracker asserts updates that doc **in the same landing** — status line,
  date, commit-hash evidence. Not "later": same wave.
- ADRs: a new decision is a new ADR (copy `docs/adr/_template.md`, next free
  number, add the index row). Status changes (Proposed→Accepted, Draft
  rounds) amend the ADR with a dated rollout/verification block — never
  silently.
- Chat is not storage: decisions and plans must end as committed files.
- **Pointer before prose.** A docs or `docs/verification/traceability.yaml`
  change that cites `umrs/` paths lands in the same commit as the umrs
  submodule pointer bump, or after it — never before. Reason: on 2026-09-13
  CI Boundary Check on `main` went red at `26b7505b` because traceability
  rows cited umrs paths that the parent's pinned submodule SHA did not yet
  contain (release plan R-01).

## 2. Plans and their lifecycle

- Plan/design docs live as files in `docs/development/` (dated name or dated
  header). Status vocabulary:
  - **LIVE** — accurate reference (guides, matrices)
  - **ACTIVE** — work still pending
  - **DONE / STALE / SUPERSEDED** — move to `docs/development/archive/` with
    a dated `> **ARCHIVED …**` banner + successor pointer
- Citation rule: never derive tasks from `archive/` or dated snapshots;
  banners name the successor.

## 3. How agents consume docs (navigation order)

The reading order, the status vocabularies and where each kind of truth lives
are in [docs/README.md](../../docs/README.md) — the one doc map. (2026-10-18:
this section kept its own five-step order, which started at the overview and
named an absent `docs/knowledge/`; it is now a pointer so the order has one
home.)

Verify-before-cite: before acting on a doc's numeric or structural claim,
spot-check it (grep code, `git log --format="%h %cs %s"`). Dated headers
are the freshness contract — distrust claims without dates.

## 4. Truth surfaces and learned failure modes

Where each kind of truth lives, and which source wins when two disagree:
[docs/README.md](../../docs/README.md) §2. (2026-10-18: this section kept its
own truth list, a third copy beside CONSTITUTION §9.4; it is now a pointer.)

**Promote learned failure modes**: when an agent discovers a new failure
mode, it lands in the place the *next* agent will look — the relevant
README, instruction file or ADR note — never only in chat or in an agent's
private memory. (2026-10-18: "or repo memory" removed; there is no repo
memory, and docs/README.md §4 says decisions end as committed files.)
