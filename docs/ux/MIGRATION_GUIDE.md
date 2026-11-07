# R4 CLI Migration Guide

This guide documents the command renames introduced in the R4 UX redesign.
All old command paths have been **removed** — they now return an error.
Use the new paths shown below.

## Quick Reference

```bash
# REMOVED                          → USE INSTEAD
r4 ask agent "question"            → r4 agent "question"
r4 ask agent run "objective"       → r4 agent run "objective"
r4 ask search "query"              → r4 search "query"
r4 ask teamup create name ...      → r4 team create name ...
r4 ask companion                   → r4  (just run r4 with no args)

r4 governance quotas               → r4 admin governance quotas
r4 trace <request-id>              → r4 admin trace <request-id>
r4 map --dot                       → r4 admin map --dot
r4 pool up roster.yaml             → r4 admin pool up roster.yaml
r4 source list                     → r4 admin source list

r4 agent-exec list                 → r4 run list
r4 agent-exec show <id>            → r4 run show <id>
```

## What Changed

### Conversational surfaces promoted to top-level

`r4 agent`, `r4 search`, and `r4 team` are now first-class top-level
commands. The `r4 ask` umbrella has been removed.

### Admin commands grouped

Operator-oriented commands (`governance`, `trace`, `map`, `pool`,
`source`) are grouped under `r4 admin`. The old individual top-level
commands have been removed.

### agent-exec → run

`r4 agent-exec` has been renamed to `r4 run`.

### Typo recovery

Misspelled commands suggest the closest match:

```
$ r4 healt
Unknown command 'healt'. Did you mean 'health'?
```
