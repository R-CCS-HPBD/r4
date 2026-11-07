# R4 CLI UX Architecture

This directory contains documentation for the R4 CLI UX redesign.

## Documents

| Document | Description |
|----------|-------------|
| [COMMAND_TAXONOMY.md](COMMAND_TAXONOMY.md) | Command namespace organization |
| [DEPRECATION_POLICY.md](DEPRECATION_POLICY.md) | How commands are deprecated |
| [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) | User-facing migration instructions |

## Design Principles

1. **Backward compatible** — old commands work with deprecation warnings
2. **Discoverable** — grouped help, typo recovery, related command hints
3. **Consistent** — CLI and TUI surfaces share command names
4. **Suppressible** — all warnings can be silenced via env vars
