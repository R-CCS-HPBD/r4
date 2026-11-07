# ADR-NNN: Short title

**Status:** Proposed
**Date:** YYYY-MM-DD
**Relates to:** ADR-xxx (why it matters), invariant / gap / work-package ids

## Context

Two to five paragraphs. What is true today (verified: file:line, commit, or
command output — not memory), what forces the decision, and what a
contributor might otherwise do by accident.

## Decision

The rule or design in normative language ("X owns Y", "clients never Z").
Number the sub-decisions (D-1, D-2, …) when there is more than one so
rollout blocks can reference them.

### Why

The reasons a future reader needs to not reverse this.

## Consequences

- Positive
- Cost / negative
- NOT in scope

## Alternatives Considered

- **Option** — why rejected.

## Rollout-status (YYYY-MM-DD)

Landed vs open, per sub-decision, with evidence (test file + count,
migration id, commit hash, live-leg id). Append a new dated block per wave;
never rewrite an older one. Status in the header and in
[`README.md`](README.md) changes only through a dated block here.

## Related

- [ADR-xxx](xxx-title.md)
- Instructions / runbooks this decision binds
