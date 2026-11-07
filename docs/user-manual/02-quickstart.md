# 2. Quick Start

This guide gets you from zero to chatting with a model in under 5 minutes. It assumes an administrator has already deployed Gateway, UMRS, and the backing infrastructure.

> **New user? Need an account first?** See [Chapter 0 — Account Creation & Onboarding](00-onboarding.md) for the full admin + user walkthrough.

> **Need to deploy infrastructure first?** See [Chapter 16 — Deployment Overview](16-deployment.md).

## Step 1: Install the Client

The R4 client consists of three lightweight packages:

| Package | Purpose | CLI |
|---------|---------|-----|
| `r4-contracts` | Shared schemas (stdlib, zero deps) | — |
| `r4` | Primary user CLI/SDK | `r4` |
| `umrs` | UMRS diagnostics & control plane | `umrs` |

### Recommended: One-Shot Installer

```bash
curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash
export PATH="$HOME/.local/bin:$PATH"
```

The installer clones the repository into `~/.r4/src` and installs the
packages into the virtual environment `~/.r4/venv`. The packages are not on
PyPI (`r4` depends on workspace packages of this repository), so `pip install
r4` does not work; use the installer or a local clone.

### From a Local Clone

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
make install-client              # r4 + r4-contracts + umrs client
# or
make install                     # full monorepo install (developers)
```

### Optional Extras

Install extras from the clone, into the active virtual environment:

```bash
pip install -e "./r4[chat]"              # Chat command extras
pip install -e "./r4[jupyter]"           # Jupyter notebook integration
pip install -e "./r4[umrs]"              # ZeroMQ transport for efficient file transfers
pip install -e "./r4[mcp-use]"           # MCP protocol support (Python 3.11+)
pip install -e "./umrs/src[client-full]" # ZMQ/CURVE + S3 transport for umrs
```

### No Server Yet? Run the Stand-Alone Stack

To try R4 on one machine without a deployed site, run the stand-alone stack
(the UMRS server and its gateway in containers, from the prebuilt image
`ghcr.io/r-ccs-hpbd/r4-standalone-umrs`, with a local model server) from the clone with `./r4-local up`.
See [the stand-alone stack](../../deploy/standalone/README.md).

## Step 2: Authenticate

### Unified Login (Recommended)

Replace `TOKEN` with the token your administrator sent. A token minted on the
front carries the site's gateway URL and your project (2026-10-03), so the
token alone is enough:

```bash
r4 login --api-key TOKEN

# Extended setup — prompts for model, scaffolds R4.md, runs health checks
r4 login --api-key TOKEN --full
```

If the token carries no gateway URL (an older token, or one minted with
`--no-gateway-url`), name it explicitly — the site's public URL, `https://HOST/SITE`:

```bash
r4 login --gateway-url "https://HOST/SITE" --project PROJECT_ID --api-key TOKEN
```

### API-Key / JWT (Alternative)

```bash
r4 auth login --api-key TOKEN
```

### OIDC (Shared deployments)

OIDC tokens never carry a gateway URL, so `--gateway-url` stays required:

```bash
export R4_OIDC_ISSUER="https://<your-okta-domain>/oauth2/default"
export R4_OIDC_CLIENT_ID="<public-client-id>"

r4 auth login \
  --gateway-url "https://HOST/SITE" \
  --project PROJECT_ID \
  --oidc
```

### Get a Token

Ask your administrator to create your account and issue a token. Run inside
the site's UMRS server container, the token embeds that site's public gateway URL from `UMRS_PUBLIC_GATEWAY_URL`;
elsewhere pass `--gateway-url "https://HOST/SITE"`:

```bash
# Admin runs on the UMRS server (USERNAME, PROJECT_ID: your values):
umrs-server admin user add USERNAME --project PROJECT_ID --issue-token

# Or generate a token separately:
umrs-server admin token generate-jwt \
  --user USERNAME \
  --project PROJECT_ID \
  --days 30
```

## Step 3: Verify

```bash
r4 config show        # Show saved profile
r4 auth status        # Verify credentials
r4 health             # Platform health check
r4 model list         # First authenticated call
```

If `r4 model list` returns `Authentication failed`, your token is invalid. Regenerate it and re-run `r4 auth login`.

## Step 4: Use the Platform

### Bring Up an Endpoint

```bash
# List available endpoints
r4 endpoint list

# Provision a new endpoint
r4 endpoint up --model Qwen/Qwen2.5-7B-Instruct --wait

# Or use a public cloud endpoint (no UMRS required)
r4 up --public openai --api-key sk-xxxx --model gpt-4o
```

### Chat

```bash
# One-shot
r4 chat "What is quantum computing?"

# Streaming
r4 chat "Count from 1 to 10." --stream

# Interactive session
r4 chat -i
```

### Interactive Companion TUI

```bash
r4
r4 companion
```

Requires Node.js ≥ 18 and the JS bundle in `r4/companion-tui-js/dist/`.

### RAG

```bash
r4 rag upload paper.pdf --project mylab
r4 rag docs list --project mylab
r4 rag query "What are the key findings?" --project mylab
```

### Python SDK

```python
from r4 import Client

client = Client()
response = client.chat("What is quantum computing?")
print(response.content)
```

## Step 5: Run the Demo Suite

```bash
cd demos
bash ./run_all.sh --auto
```

## Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| `r4` not found | `export PATH="$HOME/.local/bin:$PATH"` |
| Authentication failed | Regenerate token: `umrs-server admin token generate-jwt` |
| Connection refused | Check `r4 config show` for correct Gateway URL |
| No models available | Verify endpoint is running: `r4 endpoint list` |
| Timeout errors | Check `r4 health` for service status |

## Next Steps

- [Chapter 3 — Authentication](03-authentication.md) for detailed auth options
- [Chapter 4 — Chat & Companion](04-chat.md) for interactive features
- [Chapter 6 — Endpoints](06-endpoints.md) for endpoint management
