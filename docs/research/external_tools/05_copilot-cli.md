# 05 — GitHub Copilot CLI

> Source: [tmp/copilot-cli/README.md](../../../tmp/copilot-cli/README.md),
> [tmp/copilot-cli/changelog.md](../../../tmp/copilot-cli/changelog.md).
> The repo is a **distribution-only** mirror (README, LICENSE, CHANGELOG,
> install script). No source is shipped, so this report is feature-only.

## What it is

A **closed-source terminal coding agent** from GitHub that reuses the same
"Copilot coding agent" harness powering the GitHub web product. Distributed
as a binary via:

- `curl https://gh.io/copilot-install | bash`
- Homebrew (`copilot-cli` cask)
- WinGet (`GitHub.Copilot`)
- npm (`@github/copilot`)

Requires an active Copilot subscription. Default model is **Claude Sonnet
4.5**, switchable via `/model` to GPT-5 / Sonnet 4.

## Philosophy (inferred from README)

- **Terminal-native** — "no context switching required."
- **GitHub-integrated by default** — repos, issues, PRs accessible by
  natural language using existing GitHub auth.
- **MCP-powered extensibility** — ships with GitHub's MCP server out of
  the box; supports custom user MCP servers.
- **Full control / preview every action** — every agent action is shown
  before execution, nothing auto-runs.
- **Premium-request metered** — every prompt consumes one premium request
  from the user's monthly quota.

## Feature surface (from README + changelog)

- **Agentic loop**: build, edit, debug, refactor, plan and execute.
- **Slash commands** (named in README): `/login`, `/model`, `/lsp`,
  `/feedback`, `/experimental`, `/banner`.
- **Auth options**:
  - `/login` interactive (GitHub OAuth device flow).
  - **PAT** via `GH_TOKEN` / `GITHUB_TOKEN` env, with the
    "Copilot Requests" fine-grained scope.
- **MCP**:
  - Built-in GitHub MCP server.
  - User-supplied MCP servers via config.
- **LSP integration** (notable):
  - User-level: `~/.copilot/lsp-config.json`
  - Repo-level: `.github/lsp.json`
  - JSON schema:
    ```json
    {
      "lspServers": {
        "typescript": {
          "command": "typescript-language-server",
          "args": ["--stdio"],
          "fileExtensions": { ".ts": "typescript", ".tsx": "typescript" }
        }
      }
    }
    ```
  - `/lsp` command surfaces status; the CLI does **not** bundle servers,
    user installs each LSP separately.
- **Experimental mode** (`--experimental` or `/experimental`) — flag is
  persisted in config after first activation.
- **Autopilot mode** (experimental): `Shift+Tab` cycles modes; autopilot
  encourages the agent to keep working until a task completes (i.e. multi-
  turn agent without per-turn user gate).
- **Banner** (`--banner` to replay the launch animation; cosmetic but
  documented).

## Standout ideas worth borrowing

1. **LSP support with user-level + repo-level config files** and a `/lsp`
   command. The agent gains real go-to-definition / diagnostics / hover
   data. We have nothing equivalent in r4, and on HPC nodes LSP servers
   are usually already installed for compiled-language users — easy win.
2. **GitHub MCP as a default extension** — analogue for us would be a
   first-class **UMRS MCP** and **r4 MCP** that ship out of the box, so
   `r4 ask <verb>` calls them without extra config.
3. **Repo-level config under `.github/lsp.json`** convention. We can
   adopt `.r4/` (already implied) for repo-level overrides of profile,
   permitted MCPs, and pinned model.
4. **Mode cycling via `Shift+Tab`** — discrete operating modes
   (interactive / autopilot / plan / review). Currently r4 has only
   single-shot vs. interactive TUI; explicit modes simplify UX.
5. **Premium-request metering surfaced in product** — even if we don't
   bill, surfacing per-turn cost (tokens × $/1k from LiteLLM) builds
   trust on shared HPC quotas.
6. **PAT auth as first-class fallback** — env-var `GH_TOKEN` /
   `GITHUB_TOKEN` precedence is documented. We should document equivalent
   `R4_API_KEY` precedence and standardise the CI / headless story.
7. **Distribution channels matter**: brew / winget / npm / curl-bash all
   listed. We currently expect users to `pip install -e ./r4`. A
   single-shot install script (`curl https://r4.example/install | bash`)
   is a low-effort UX upgrade.
