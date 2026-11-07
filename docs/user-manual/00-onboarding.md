# 0. Account Creation & Onboarding

This guide walks through creating a user account and completing first-time login,
from the administrator's perspective and the new user's perspective.

Both roles are covered in sequence — an admin completes Part A, then sends the
credentials to the new user who follows Part B.

---

## Part A — Admin: Create the Account

These steps run on the UMRS server (or on any machine that can read the
server-side `user_files/` directory).

### A1. Locate the admin CLI

```bash
# SSH into the server, then activate the server environment
ssh <umrs-host>

# In the deployment directory:
cd <r4-checkout>/umrs/deploy/rccs-cloud   # adjust to your site
set -o allexport && source .env && set +o allexport
export PYTHONPATH="$(cd ../.. && pwd)/src${PYTHONPATH:+:$PYTHONPATH}"

# Verify the CLI is available
umrs-server admin --help
```

### A2. Register the user

```bash
umrs-server admin user add <username> \
  --fullname "Firstname Lastname" \
  --email <user@example.org> \
  --role user \
  --project <project-id>
```

For HPC deployments (Slurm), also set the Slurm accounting name so the user can
provision endpoints:

```bash
umrs-server admin user add <username> \
  --fullname "Firstname Lastname" \
  --email <user@example.org> \
  --role user \
  --project <project-id> \
  --slurm-account <slurm-account-name>
```

> **Tip:** Run with `--issue-token` to create the account and generate a login
> token in a single command (see A3 below for the one-liner).

### A3. Generate a login token

Run the minting command where the server runs — on the server host inside the
site's server container, e.g. `podman exec <server-container> umrs-server admin
token generate-jwt ...`. From the converge that ships the 2026-10-03 change
on, the token then embeds that site's public gateway URL
(`UMRS_PUBLIC_GATEWAY_URL`, which the converge sets in each site's server
container), so the user logs in with `r4 auth login --api-key TOKEN` alone.
Tokens minted before that converge carry no URL and still need
`--gateway-url`.
Pass `--gateway-url URL` to embed another URL, `--no-gateway-url` to embed
none. Below, `USERNAME`, `PROJECT_ID`, `ACCOUNT` stand for your values.

```bash
# Option 1: Re-generate separately (if user was already added without --issue-token)
umrs-server admin token generate-jwt \
  --user USERNAME \
  --project PROJECT_ID \
  --days 90
```

Or use the combined one-liner:

```bash
# Option 2: Create account + token in one step
umrs-server admin user add USERNAME \
  --fullname "Firstname Lastname" \
  --email user@example.org \
  --role user \
  --project PROJECT_ID \
  --slurm-account ACCOUNT \
  --issue-token \
  --token-days 90
```

The token is printed to stdout, followed by the login line to send with it.
Copy both — you will send them to the new user.

**Example output** (`user add ... --issue-token` in the server container):

```
...
  Onboarding token (valid 90d):
    eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

  Onboarding steps for the new user:
  1. (recommended) r4 via Gateway HTTP (the token carries the gateway URL https://data0.ai.r-ccs.riken.jp/rccs, so no other flag is needed):
       r4 auth login --api-key eyJhbGci...
  2. (alternative) umrs direct ZMQ — requires CURVE keys if CURVE auth is enabled:
       umrs login --host HOST --api-key eyJhbGci...
...
```

A token minted where `UMRS_PUBLIC_GATEWAY_URL` is unset carries no gateway
URL; its login line then reads
`r4 auth login --gateway-url https://HOST/SITE --api-key ...` — replace
`https://HOST/SITE` with the site's public URL before sending it (the minting
commands themselves refuse the literal example).

### A4. Verify the user record

```bash
umrs-server admin user list
umrs-server admin user show <username>
```

### A5. (Optional) Generate a client config template

```bash
umrs-server admin user generate-template <username> \
  --fullname "Firstname Lastname" \
  -o user_<username>.json
```

Send `user_<username>.json` to the new user alongside the token. They can import
it directly (see B3 below).

---

## Part B — User: First Login

These steps run on the **user's machine** (laptop, login node, container).

### B1. Install the client

```bash
curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash
export PATH="$HOME/.local/bin:$PATH"
```

The client is not on PyPI, so `pip install r4` would fetch an unrelated
package (corrected 2026-11-07); use the installer above, or a clone (see
[the quickstart](02-quickstart.md)). It works on macOS and Linux, and on
Windows inside WSL2. To upgrade, re-run the installer; to uninstall,
`uv tool uninstall r4`.

For a richer experience install extras from a clone, into the active
virtual environment:

```bash
pip install -e "./r4[chat]"         # Chat command extras
pip install -e "./r4[companion]"    # JS companion TUI extras
pip install -e "./r4[umrs]"         # ZeroMQ transport (required for file/data ops)
```

Check the installation:

```bash
r4 --version
```

### B2. Authenticate

Use the token provided by the admin (replace `TOKEN` with it):

```bash
r4 login --api-key TOKEN
```

`r4 login` saves a named **profile** in `~/.config/r4/` and validates the token
against the gateway. When the token is a JWT, it auto-extracts your username,
project, gateway URL, and expiry — so no other flags are needed.

**SSH tunnel example** (gateway reachable only from its host):

```bash
# First open the SSH tunnel (once per session)
ssh -fN \
  -L <gateway-port>:127.0.0.1:<gateway-port> \
  <gateway-host>

# Then log in — override gateway_url to use tunnel endpoint
r4 login --gateway-url http://127.0.0.1:<gateway-port> --api-key TOKEN
```

**OIDC / Okta example** (shared deployments):

```bash
r4 login \
  --gateway-url http://<gateway-host>:<port> \
  --project <project-id> \
  --oidc \
  --oidc-issuer https://<okta-domain>/oauth2/v1 \
  --oidc-client-id <client-id>
```

### B3. (Optional) Import a config template from the admin

If the admin provided a `user_<username>.json` template:

```bash
umrs config init \
  --template user_<username>.json \
  --username <username>
```

This pre-populates server endpoints and S3 bucket paths so you do not need to
configure them manually.

### B4. Verify the connection

```bash
r4 config show          # Active profile (gateway URL, project, username)
r4 health               # Platform readiness check
r4 model list           # First authenticated API call
```

Expected output for `r4 health` (shape may vary slightly by version):

```
✓ Gateway is ready
  gateway     ok
  auth_sidecar ok
  umrs        ok
Checked context: profile=default project=<project>
```

The `gateway`, `auth_sidecar`, and `umrs` lines verify the edge chain and
that platform-side UMRS is reachable through your credentials.

If `r4 model list` returns `Authentication failed`, the token may be expired or
the gateway URL is wrong. Ask the admin to regenerate the token.

---

## Part C — Quick Smoke Test

After successful login, run one command end-to-end to confirm the platform is
working for your account:

```bash
r4 chat "Hello from <username>. What is 2 + 2?"
```

If the model responds, your account is fully onboarded.

---

## Reference: Common Admin Scenarios

### Reset a user's token

```bash
umrs-server admin token generate-jwt \
  --user USERNAME \
  --project PROJECT_ID \
  --days 90
```

### Add a user to an additional project

```bash
umrs-server admin user add-to-project <username> <project-id>
```

### Set or change the Slurm account

```bash
umrs-server admin user set-slurm-account <username> <slurm-account-name>
```

### Disable / re-enable a user

```bash
umrs-server admin user disable <username>
umrs-server admin user enable <username>
```

### OIDC login (Okta) — bind an existing user to an IdP subject

```bash
# Find the OIDC subject from the identity provider (e.g. Okta user ID "00u1a2b3")
umrs-server admin user set-oidc-subject <username> <oidc-subject>
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Authentication failed` on `r4 model list` | Expired or wrong token | Regenerate with `umrs-server admin token generate-jwt` |
| `Connection refused` on `r4 health` | SSH tunnel not open | Open tunnel: `ssh -fN -L <gateway-port>:127.0.0.1:<gateway-port> <gateway-host>` |
| `Cannot provision endpoint` | Missing Slurm account | Admin runs `umrs-server admin user set-slurm-account` |
| `OIDC login fails` | Subject not bound | Admin runs `umrs-server admin user set-oidc-subject` |
| `r4 login` succeeds but no models | Models not staged | Run `r4 model list --scope system`; ask admin to stage models |

---

## Next Steps

- [Chapter 2 — Quick Start](02-quickstart.md) — First chat, RAG, and endpoint workflow
- [Chapter 3 — Authentication](03-authentication.md) — Profile management, multiple deployments
- [Chapter 6 — Endpoints](06-endpoints.md) — Provision and manage model endpoints
- [Chapter 20 — User & Access Management](20-user-management.md) — Full admin reference
