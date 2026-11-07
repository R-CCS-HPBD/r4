# R4 Quick Start

This is the current end-user onboarding path for a shared deployment.

It assumes an administrator already runs Gateway, UMRS, storage, and the backing cluster or workstation services. If you need to deploy infrastructure, use the [admin guide](admin-guide/README.md) instead.

## 1. Install The Client

From the repository root:

```bash
./scripts/setup_r4_client.sh
export PATH="$HOME/.local/bin:$PATH"
```

That installs the local `r4` package into a user-local virtual environment and writes a wrapper command to `~/.local/bin/r4`.

The client install includes the small `r4-contracts` dependency used for local validation of recipes, schedules, skills, and other public data shapes. Users do not install or run UMRS, Gateway, or RiVault when connecting to a shared deployment.

## 2. Authenticate & Configure A Profile

`r4 auth login` saves the Gateway URL, authenticates, and materialises a local
profile in a single command.

**API-key / JWT deployments** — **this is the typical path for dev/HPC deployments**:

```bash
r4 auth login --gateway-url https://<gateway-host> --project <project-id> --api-key <YOUR_TOKEN>
```

Preferred shared-deployment flow with OIDC:

```bash
export R4_OIDC_ISSUER="https://<your-okta-domain>/oauth2/default"
export R4_OIDC_CLIENT_ID="<public-client-id>"

r4 auth login --gateway-url https://<gateway-host> --project <project-id> --oidc
```

When the token is a JWT, `r4 auth login` automatically extracts and displays
your username, project, and expiry (for UX only — the server re-validates on
every request).

> **Tip:** `r4 auth login` replaces the old two-step `r4 config init` + `r4 auth login`
> flow. The old `r4 init` and `r4 config init` commands still work but print a deprecation
> notice.

### 2a. Obtain A Token (API-Key Deployments)

Ask an admin to create your account and issue a token:

```bash
# On the UMRS server — create user and issue an onboarding token in one step
umrs-server admin user add <your-username> \
  --project <project-id> \
  --issue-token
```

The admin gives you the printed token. You run:

```bash
r4 auth login --gateway-url https://<gateway-host> --api-key eyJ...
```

Alternatively, if the admin did not use `--issue-token`, they can generate a
token separately:

```bash
umrs-server admin token generate-jwt \
  --user <your-username> \
  --project <project-id> \
  --days 30
```

> **Defaults:** omitting `--user` produces `sub=admin`, omitting `--project` produces
> `project_id=default`. Always pass explicit values to avoid accidentally using wrong scope.
> The output shows `sub`, `project_id`, `roles`, and `expires` — verify these match your
> intended identity before using the token.

To see the profiles and local project/user contexts already saved on this machine:

```bash
r4 config context list
r4 config context list --json
```

## 3. Verify The Environment

```bash
r4 config show        # show saved profile
r4 auth status        # verify stored credentials are valid
r4 health             # platform readiness (config, auth, gateway, umrs, umrs_profile)
r4 model list --limit 10   # first real authenticated call
```

`r4 health` checks include `umrs_profile` — verifying that the umrs
connection profile is in sync with r4. If you also use `umrs`, a single
`r4 login` is sufficient; umrs automatically discovers r4's profile and
token as a fallback.

> If `r4 model list` returns `Authentication failed`, your token is missing or wrong.
> Regenerate it with `umrs-server admin token generate-jwt --user <you> --project <project-id>`
> and re-run `r4 auth login --api-key <new-token>`.
>
> `r4 model list` uses ZMQ by default and falls back to HTTP when ZMQ is
> unreachable. Output uses ANSI color styling in terminals.

## 4. Bring Up Or Reuse An Endpoint

The rest of the platform demos assume an active endpoint exists.

### Option A: Platform Endpoint (UMRS-provisioned)

```bash
r4 endpoint list
r4 endpoint up --model <model-name> --wait
r4 endpoint status <endpoint-id>
```

If your deployment already keeps endpoints warm, reuse the existing one instead of provisioning a new one.

### Option B: Public Cloud Endpoint (No UMRS Required)

Use any cloud LLM directly without the platform gateway:

```bash
# OpenAI
r4 up --public openai --api-key sk-xxxx --model gpt-4o

# Google Gemini
r4 up --public gemini --api-key AIza-xxxx --model gemini-2.0-flash

# AWS Bedrock
r4 up --public bedrock --model anthropic.claude-sonnet-4-6

# Remove when done
r4 down --public
```

See the [Public Endpoints guide](public-endpoints.md) for all supported
providers, security notes, and detailed examples.

## 5. Run Common Commands

```bash
# One-shot chat
r4 chat "Summarize the purpose of this platform."

# Streaming chat
r4 chat "Count from 1 to 5." --stream

# Project management
r4 project current
r4 project list

# RAG
r4 rag upload paper.pdf --project <project-id>
r4 rag docs list --project <project-id>
r4 rag query "What are the key findings?" --project <project-id>

# Endpoint and job inspection
r4 endpoint list
r4 job list

# Agent and search
r4 agent sessions
r4 search "find endpoint provisioning code" --scope code_only --depth shallow
```

## 6. Run The Canonical Demo

The supported demo flow is the real `r4` workflow suite:

```bash
cd demos
bash ./run_all.sh --auto
```

This runs all demos (00 through 07) in order. Use `--destroy` to tear down the
endpoint after the suite completes.

## Python SDK

```python
from r4 import Client

client = Client()
response = client.chat("What is quantum computing?")
print(response.content)
```

## Companion UI

The research companion launches the JavaScript/Ink TUI:

```bash
r4
r4 companion
r4 companion --resume SESSION_ID
```

**Requirements:** Node.js ≥ 18 on `PATH`, and the JS bundle in `r4/companion-tui-js/dist/`
(build with `cd r4/companion-tui-js && npm run build` if missing).

## Troubleshooting

### `r4` not found

```bash
export PATH="$HOME/.local/bin:$PATH"
which r4
```

### `Authentication failed` despite `r4 health` passing

`r4 health` is intentionally quick, so it can pass while a stored token is missing,
expired, or signed for the wrong Gateway. Use the account-specific check before
debugging endpoints or models.

Fix:
1. Generate a fresh token on the UMRS server:
   ```bash
   umrs-server admin token generate-jwt --user <your-username> --project <project-id>
   ```
   Check the printed `sub` and `project_id` match your identity and active project.
2. Log in on the client:
   ```bash
   r4 auth login --api-key <token>
   ```
3. Verify:
   ```bash
   r4 health account
   r4 auth status
   r4 model list --limit 5
   ```

### `r4 login` or `r4 auth login` not found

The login commands are `r4 login` (primary) or `r4 auth login` (alias).

### OIDC variables missing

```bash
echo "$R4_OIDC_ISSUER"
echo "$R4_OIDC_CLIENT_ID"
```

### Gateway health is not ready

`r4 health` may still report partial readiness even when the profile is valid. Use
`r4 health full` for the broader diagnostic view, then continue with `r4 endpoint list`,
`r4 endpoint up`, and the workflow demos so you can see which live surfaces are healthy.

## Next Steps

- Use [index.md](index.md) for the current R4 doc index
- Use [../LIBRARY_GROUP_STRUCTURE.md](../LIBRARY_GROUP_STRUCTURE.md) to understand how `r4`, `r4-contracts`, Gateway, UMRS, and RiVault fit together
- Use [../../demos/README.md](../../demos/README.md) for the canonical demo order
- Use [admin-guide/README.md](admin-guide/README.md) if you are operating the shared deployment
