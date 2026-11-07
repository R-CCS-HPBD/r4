# Auth Capability Matrix — CLI vs TUI vs VSCode

This matrix pins down which credential store each surface writes to and reads
from, so that a login made on one surface is visible to every other surface
without manual migration.

## Canonical credential stores

| Store | Path | Purpose |
|---|---|---|
| `sources.json` | `~/.config/r4/sources.json` | Primary credential store. Holds named sources (URL + api_key + kind); the *active* pointer selects the default source. |
| `config.json` | `~/.config/r4/config.json` | Per-profile api_key. Kept for backward compatibility; `sources.json` takes precedence when both exist. |
| SecretStorage | VS Code SecretStorage | Snapshot of the token. Used as fallback when `sources.json` has no active source with `api_key`. |
| UMRS connections | `~/.config/umrs/connections/<name>.json` | Python-side ZMQ session/transport metadata for the matching r4 profile. |
| Env vars | `R4_API_KEY`, `R4_GATEWAY_URL`, `R4_OIDC_*` | Override everything else; never persisted. |

## Login flows

| Surface | Command / Entry point | Writes to |
|---|---|---|
| `r4` CLI | `r4 login` / `r4 auth login` | `sources.json` ← canonical → `config.json` ← profile → SecretStorage ← JWT cache → UMRS connections |
| `r4` TUI (companion) | TUI login handler | delegates to `r4 auth login` via subprocess; same store as CLI |
| VS Code extension | `r4.login` command | (1) credential-picker import → re-exports an existing CLI source, never mints new credentials; (2) manual login → delegates to `r4 auth login`; (3) fallback: direct SecretStorage write when the r4 CLI is not installed |
| VS Code WebView | n/a | No independent auth surface — all tokens flow through the extension host |

## What went wrong (fresh install scenario)

Before `vscode-extension/src/auth.ts` delegated manual login to `r4 auth login`
(commit `6f7d6cda`), the extension's manual flow wrote *only* to SecretStorage.
Fresh machines had this divergence:

```
r4 login    → sources.json holds token  → extension picks it up (✓)
vscode login → token in SecretStorage only → r4 config.json / sources.json
               untouched → CLI cannot see the token (✗)
```

After the fix:

```
vscode login → r4 auth login (delegated) → canonical store updated
             → extension reads back via the same chain
             → fresh installs are self-sufficient (✓)
```

## Reading order (extension)

1. `sources.json` live `api_key` on the active source
2. VS Code SecretStorage (manual login snapshot)
3. `config.json` per-profile `api_key`
4. Environment variable `R4_API_KEY`

## Reading order (CLI)

1. Environment variables
2. `sources.json` active source
3. `config.json` profile

The two surfaces see the same content because they read the same stores in
the same priority order.

## Fresh-machine verification

```bash
# From a fresh HOME — no r4 config at all:
export R4_TEST_HOME=$(mktemp -d)
export HOME=$R4_TEST_HOME

# 1. CLI login hydrates the canonical store
r4 auth login --gateway-url https://data0.ai.r-ccs.riken.jp/rccs --api-key <JWT>

# 2. VS Code extension picks it up without any manual login
#    (the credential-picker offers it; picking it does not write SecretStorage)

# 3. Manual login in a fresh VS Code instance no longer dead-ends —
#    it delegates to `r4 auth login` and ends with the same canonical
#    state as step 1.
```
