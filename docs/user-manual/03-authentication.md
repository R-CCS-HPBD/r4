# 3. Authentication

R4 supports multiple authentication modes depending on your deployment.

## Authentication Modes

| Mode | When | Login Required? | How Identity Works |
|------|------|-----------------|-------------------|
| **API Key / JWT** | Dev, HPC, automation | Yes | `r4 auth login --api-key TOKEN` |
| **OIDC** | Shared deployments | Yes | `r4 auth login --oidc` (browser-based) |
| **HPC Internal** | Login nodes, compute jobs | No | mTLS / reverse proxy / SSH boundary provides identity |

## API-Key Authentication

The most common path for development and HPC deployments.

### Login

Replace `TOKEN` with the token your administrator sent.

```bash
# Admin-issued JWT with embedded gateway_url — single flag is enough:
r4 auth login --api-key TOKEN

# A token without gateway_url (older tokens, --no-gateway-url) — name it:
r4 auth login \
  --gateway-url "https://HOST/SITE" \
  --api-key TOKEN
```

When the token is a JWT, `r4 auth login` automatically extracts and displays
your username, project, gateway URL, and expiry. Explicit flags override JWT claims.

Tokens minted by a server whose deployment sets `UMRS_PUBLIC_GATEWAY_URL`
carry the site's public gateway URL (e.g.
`https://data0.ai.r-ccs.riken.jp/rccs`): the minting commands embed
`UMRS_PUBLIC_GATEWAY_URL`, which your deployment sets in each site's server
container, unless `--gateway-url URL` overrides it or `--no-gateway-url`
leaves it out. Older tokens never carry it and need `--gateway-url`. The
minting command prints the matching login line — `r4 auth login --api-key
TOKEN` when the URL is embedded.

### Token Types

| Token Type | Creator | Lifetime | Use Case |
|------------|---------|----------|----------|
| **Admin token** | `umrs-server admin token generate` | Configurable | UMRS admin operations |
| **JWT** | `umrs-server admin token generate-jwt` | 1–365 days | User authentication via Gateway |
| **API key** | Environment variable | Permanent | CI/CD automation |

### Obtaining a Token

Ask your administrator:

```bash
# On the UMRS server host, inside the site's server container — e.g.
# podman exec <server-container> umrs-server admin ... — so the token embeds
# that site's gateway URL.
# Option 1: Create user with token in one step
umrs-server admin user add alice \
  --project team-alpha \
  --issue-token

# Option 2: Generate token separately
umrs-server admin token generate-jwt \
  --user alice \
  --project team-alpha \
  --days 30
```

The output shows `sub`, `project_id`, `roles`, `gateway` (when embedded) and `expires` — verify these match your intended identity.

## OIDC Authentication

For shared deployments with enterprise identity providers (Okta, Azure AD, etc.).

### Setup

```bash
export R4_OIDC_ISSUER="https://<your-okta-domain>/oauth2/default"
export R4_OIDC_CLIENT_ID="<public-client-id>"

r4 auth login \
  --gateway-url "https://HOST/SITE" \
  --project PROJECT_ID \
  --oidc
```

OIDC tokens never carry a gateway URL, so `--gateway-url` stays required here.
This opens a browser for interactive login. The token is cached locally.

Provider presets fill issuer/scope automatically:

```bash
r4 login --oidc --provider gakunin    # or: orcid, github
```

## Profile Management

R4 stores credentials in profiles. You can maintain multiple profiles for different deployments.

### View Profiles

```bash
r4 config show                    # Active profile
r4 config context list            # All profiles and contexts
r4 config context list --json     # Machine-readable output
```

### Switch Context

```bash
r4 config context --project new-project
r4 config context --umrs-username alice
r4 config context --clear-project
```

## Auth Commands

### Unified Login (`r4 login`)

The recommended way to authenticate for the first time. Combines profile setup,
token storage, and optional onboarding in a single command:

```bash
# Token with embedded gateway URL (minted where UMRS_PUBLIC_GATEWAY_URL is set)
r4 login --api-key TOKEN

# OIDC variant
r4 login --gateway-url "https://HOST/SITE" --oidc

# Extended setup — prompts for model selection, scaffolds R4.md, runs health checks
r4 login --api-key TOKEN --full
```

Additional flags:
- `--profile NAME` — Save to a named profile (default: `default`)
- `--model NAME` — Set a default model
- `--umrs-username NAME` — Set the UMRS username for job/data operations

### Auth Subcommands (`r4 auth`)

```bash
r4 auth login [--gateway-url URL] [--api-key TOKEN | --oidc] [--project ID]
r4 auth logout [--profile NAME] [--revoke-server-session]
r4 auth status [--profile NAME]     # Verify current credentials
r4 auth refresh [--profile NAME]    # Refresh expired token
```

### What `r4 auth logout` removes (2026-10-22)

`r4 auth logout` (and its alias `r4 logout`) works on this machine only. It
sends no credential over the network, whichever way the profile is chosen
(`--profile`, `R4_PROFILE`, the active source, `default`). It deletes every
local copy of the login: the profile's API key, the stored token, the copy on
the platform source row, the umrs connection token, the legacy
`s3cfg_<user>` file, and the CURVE session files of this login. A CURVE
session's client secret key exists only in its file, so deleting the file
ends the session for this machine.

The server keeps its side of the session until the session's TTL runs out.
For each deleted session that records its deployment, logout prints the
command that revokes it now. The command has a `<TOKEN>` placeholder and never
contains your token; a password in the gateway URL (`https://user:pw@host`)
is shown as `***@` (2026-10-22):

```text
curl -fsS -X DELETE -H "Authorization: Bearer <TOKEN>" https://HOST/SITE/umrs/zmq/session/<id>
```

`--revoke-server-session` makes logout revoke the sessions itself. Logout
reads the stored credential, deletes every local copy, then sends that
credential only when all of the following are true:

- the session file records its deployment URL (a plain `http(s)` URL) and
  its subject;
- the subject is this login;
- the credential provably belongs to that deployment, meaning its JWT
  `gateway_url` claim names it, or the credential carries no claim and the
  login that stored this very credential named that gateway (`r4 auth login
  --gateway-url URL`, a URL typed at its prompt, or `r4 onboard`).

The recorded gateway on its own proves nothing (2026-10-22): logout keeps it,
and `r4 source add`, `r4 init` or a token refresh store a different
credential under it. A login that names no gateway, a login the gateway
answered HTTP 401, a credential stored any other way, and a refreshed token
are refused; use the printed command.

If any of these is in doubt, logout refuses, says why, and still deletes the
local files. `R4_GATEWAY_URL`, `UMRS_BASE_URL` and `R4_API_KEY` are never used
to revoke.

## Profile Sync Between r4 and umrs

When you run `r4 login`, a matching umrs connection profile is
automatically written to `~/.config/umrs/connections/<profile>.json`. This keeps
both tools pointed at the same gateway and ZMQ endpoints.

**Fallback chain** — if no umrs profile exists for the active name,
umrs reads r4's config as a fallback:

1. `~/.config/umrs/connections/<name>.json` (primary)
2. `~/.config/r4/sources.json` → matching source entry (fallback)
3. `~/.config/r4/config.json` → matching profile entry (fallback)

Set `R4_PROFILE` to tell umrs which r4 profile to use:

```bash
R4_PROFILE=blue umrs ping       # uses r4's "blue" profile
R4_PROFILE=blue umrs model list  # same fallback
```

**Token sharing** — umrs also falls back to r4's token store
(`~/.config/r4/tokens.json`) when no `.token` file exists for the active profile.
This means a single `r4 login` is sufficient for both tools.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `R4_GATEWAY_URL` | Gateway URL (overrides profile) |
| `R4_API_KEY` | API key (overrides profile) |
| `R4_PROJECT` | Default project ID |
| `R4_OIDC_ISSUER` | OIDC issuer URL |
| `R4_OIDC_CLIENT_ID` | OIDC client ID |
| `R4_PROFILE` | Active profile name (also used by umrs as fallback) |

## Security Notes

- Tokens are stored in `~/.config/r4/` with restrictive file permissions.
- The server re-validates every token on every request — client-side extraction is for UX only.
- Use `r4 auth logout` to clear stored credentials when done.
- For automation, prefer environment variables over stored profiles.

## Next Steps

- [Chapter 4 — Chat & Companion](04-chat.md)
- [Chapter 6 — Endpoints](06-endpoints.md)
