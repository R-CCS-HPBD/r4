# R4 CLI Deprecation Policy

## Lifecycle Phases

| Phase | Behavior | Help Visibility | Completion |
|-------|----------|-----------------|------------|
| **SOFT** | Warning on stderr | Visible | Included |
| **WARN** | Louder warning | Hidden | Included |
| **HIDDEN** | Minimal warning | Hidden | Excluded |
| **REMOVED** | Error with migration hint | N/A | N/A |

## Implementation

The deprecation framework lives in `r4/src/r4/_cli/deprecation.py`:

```python
from r4._cli.deprecation import DeprecationPhase, emit_deprecation_warning

emit_deprecation_warning(
    "r4 governance",           # old command
    "r4 admin governance",     # replacement
    phase=DeprecationPhase.SOFT,
    sunset_version="v1.0",
)
```

## Current Deprecations

There are no active soft-deprecation warnings. The following commands completed
the deprecation cycle and have been **removed** (phase REMOVED):

| Old Command | Replacement | Status |
|-------------|-------------|--------|
| `r4 ask agent` | `r4 agent` | REMOVED |
| `r4 ask search` | `r4 search` | REMOVED |
| `r4 ask teamup` | `r4 team` | REMOVED |
| `r4 ask companion` | `r4` (no args) | REMOVED |
| `r4 governance` | `r4 admin governance` | REMOVED |
| `r4 trace` | `r4 admin trace` | REMOVED |
| `r4 map` | `r4 admin map` | REMOVED |
| `r4 pool` | `r4 admin pool` | REMOVED |
| `r4 source` | `r4 admin source` | REMOVED |
| `r4 agent-exec` | `r4 run` | REMOVED |

## Adding New Deprecations

1. Add `emit_deprecation_warning()` call at the top of the old command's `handle()` method
2. Create the new command (or group entry) that shares the same handler
3. Add entry to the table above
4. Write a test in `r4/tests/test_cli_ux_redesign.py`
