# Enabling Okta OIDC Authentication

This guide covers **two independent** Okta integration paths.  You do NOT
need to deploy both — pick the one that matches your use case:

| Path | What it does | When you need it | Okta app type |
|---|---|---|---|
| **r4 CLI login** ([Step 5](#step-5-r4-cli--oidc-login-server-side-callback)) | Users run `r4 auth login --oidc`; CLI opens browser, obtains token via PKCE | Always (primary auth path) | Native Application (public client) |
| **oauth2-proxy** ([Steps 1–4](#step-1-configure-oauth2-proxy)) | Protects browser-accessible web UIs (dashboards, n8n) behind session cookies | Only if you serve a browser web UI | Web Application (confidential client) |

> **TL;DR — CLI-only deployment:**  If you only need CLI/API access
> (no browser web UI), skip Steps 1–4 entirely.  Create **one** Okta
> Native Application, configure the gateway env vars in
> [Step 2](#step-2-configure-gateway-environment), and follow
> [Step 5](#step-5-r4-cli--oidc-login-server-side-callback).
> oauth2-proxy is not required.

## Architecture

The gateway supports two distinct authentication paths.  They share the
same JWKS validation layer but differ in how identity reaches the gateway:

### Browser traffic (oauth2-proxy session)

```
Browser  ──cookie──▶  oauth2-proxy  ──▶  NGINX  ──▶  Auth Sidecar  ──▶  Gateway
                         │                              │
                         │  1. Browser has session       │  3. Sidecar validates
                         │     cookie from oauth2-proxy  │     RS256 JWT via JWKS
                         │  2. oauth2-proxy injects      │  4. Returns X-User-ID,
                         │     Authorization: Bearer     │     X-Project-ID headers
                         │     <ID token> upstream       │
                         ▼                               ▼
                       Okta                         Okta JWKS
                      (IdP)                         /v1/keys
```

The browser never sends a Bearer token directly.  oauth2-proxy manages the
session cookie, validates it against Okta, and **injects the OIDC ID token
as an `Authorization: Bearer` header** into the upstream request.  NGINX's
`auth_request` sends this token to the sidecar for RS256/JWKS validation.

### CLI / API traffic (direct Bearer token)

```
r4 CLI / API client  ──Bearer JWT──▶  NGINX  ──▶  Auth Sidecar  ──▶  Gateway
                                                       │
                                                       │  Sidecar validates
                                                       │  RS256 JWT via JWKS
                                                       ▼
                                                  Okta JWKS
                                                  /v1/keys
```

The CLI obtains an access token via OIDC Authorization Code + PKCE
(see [Step 5](#step-5-r4-cli--oidc-login-server-side-callback)) and sends
it directly as `Authorization: Bearer <token>`.  No oauth2-proxy session
is involved.

## Prerequisites

> **Note:** The two Okta applications below are independent.  If you are
> deploying **CLI-only** (no browser web UI), you only need item 2.

Two separate Okta applications are required — one confidential web client
for oauth2-proxy, and one public client for the r4 CLI:

1. **Okta Application — oauth2-proxy (browser login)**
   - Type: Web Application (confidential client)
   - Sign-in redirect URI: `https://<YOUR_HOST>/oauth2/callback`
   - Sign-out redirect URI: `https://<YOUR_HOST>/`
   - Grant types: Authorization Code
   - Note the **Client ID**, **Client Secret**, and **Okta domain**

2. **Okta Application — r4 CLI (programmatic login)**
   - Type: Public client (client authentication = `None`)
   - Sign-in redirect URI: `https://<YOUR_HOST>/<cluster>/auth/cli-callback`
   - Sign-out redirect URI: `https://<YOUR_HOST>/`
   - Grant types: Authorization Code
   - PKCE: Required (S256)
   - Note the **Client ID** (no client secret)

3. **oauth2-proxy** binary — [Install guide](https://oauth2-proxy.github.io/oauth2-proxy/installation)

## Step 1: Configure oauth2-proxy

> **Skip Steps 1–4** if you only need CLI/API authentication.
> oauth2-proxy is only required for browser-based web UI login.

Copy the example config and fill in your values:

```bash
cp deploy/oauth2-proxy/oauth2-proxy.cfg.example /etc/oauth2-proxy/oauth2-proxy.cfg

# Generate a cookie secret
python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Edit `/etc/oauth2-proxy/oauth2-proxy.cfg` and replace all `YOUR_*` placeholders.

## Step 2: Configure Gateway Environment

> **Required for both paths** (CLI and oauth2-proxy).  These env vars
> configure JWT validation in the auth sidecar and gateway regardless of
> how the token was obtained.

Set these environment variables for the **auth sidecar** and **gateway**:

```bash
# --- Auth mode: RS256 via Okta JWKS ---
JWT_ALGORITHM=RS256
JWT_JWKS_URL=https://YOUR_OKTA_DOMAIN/oauth2/default/v1/keys
JWT_ISSUER=https://YOUR_OKTA_DOMAIN/oauth2/default

# --- Default project for Okta users ---
# Okta tokens don't carry a project_id claim.
# Set a default so all authenticated users land in one project.
# WARNING: This collapses all users into one authorization namespace.
# Acceptable for initial deployment, but long-term you should implement
# OIDC-identity → local account → project/account mapping to prevent
# audit ambiguity, quota mixing, and accidental cross-user access.
JWT_DEFAULT_PROJECT=default

# --- Audience check (recommended) ---
# Validates the 'aud' claim in incoming JWTs.  Without this, tokens
# issued for a different Okta client_id may accidentally be accepted.
JWT_AUDIENCE=api://your-okta-app-client-id
```

These apply to the auth sidecar (`gateway/sidecar/main.py`), the only
Python component left at the edge (ADR-014 removed the Python Gateway).

## Step 3: NGINX Configuration

When oauth2-proxy is in front of NGINX, it validates the browser's session
cookie against Okta and injects the OIDC ID token as an
`Authorization: Bearer <token>` header into the upstream request.  The
browser itself never sends a Bearer token — oauth2-proxy manages the
cookie-to-token translation.

The existing NGINX `auth_request` directive in `gateway/nginx/nginx.conf`
sends this injected token to the sidecar at `:8081/validate`, which
validates it as an RS256 JWT via Okta's JWKS endpoint.
**No NGINX config changes are required.**

If you want NGINX to sit *in front of* oauth2-proxy (recommended for TLS
termination), add a location block:

```nginx
# Forward unauthenticated traffic through oauth2-proxy
location /oauth2/ {
    proxy_pass http://127.0.0.1:4180;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location = /oauth2/auth {
    proxy_pass http://127.0.0.1:4180;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
}
```

## Step 4: Start Services

```bash
# Start oauth2-proxy
oauth2-proxy --config=/etc/oauth2-proxy/oauth2-proxy.cfg &

# Start the gateway stack (sidecar + gateway + nginx)
# with the env vars from Step 2 exported
```

## Step 5: R4 CLI — OIDC Login (Server-side Callback)

> **This is the primary authentication path for CLI/API users.**
> It uses OAuth 2.0 Authorization Code + PKCE — NOT the Device
> Authorization Grant.  No oauth2-proxy is involved in this flow.
>
> **Okta application type:** Native Application (public client).
> Do NOT use "Web Application" — that is a confidential client type
> that requires a client_secret, which the CLI does not use.

The r4 CLI authenticates via OIDC Authorization Code + PKCE.  The redirect
URI is the gateway's HTTPS endpoint, satisfying HTTPS + FQDN requirements.

**Okta Application Setup for CLI:**
- Type: Public client with client authentication set to `None`
- If Okta requires a concrete application type, use a public-client type
  (for example `Native Application` or `Single-Page Application`), not a
  confidential `Web Application`
- Sign-in redirect URI: `https://<YOUR_HOST>/<cluster>/auth/cli-callback`
  (e.g. `https://data0.ai.r-ccs.riken.jp/rccs/auth/cli-callback`)
- Grant types: Authorization Code
- PKCE: Required (S256)
- No client secret is used by the r4 CLI token exchange

**CLI Usage:**
```bash
export R4_OIDC_ISSUER="https://YOUR_OKTA_DOMAIN/oauth2/default"
export R4_OIDC_CLIENT_ID="YOUR_CLIENT_ID"
export R4_GATEWAY_URL="https://YOUR_HOST/rccs"

r4 auth login --gateway-url https://YOUR_HOST/rccs --project default --oidc
```

**Flow:**
1. CLI opens browser → Okta login page
2. User authenticates at Okta
3. Okta redirects to `https://<YOUR_HOST>/<cluster>/auth/cli-callback?code=…&state=…`
4. Gateway trampoline page JS-redirects to CLI's localhost with the auth code
5. CLI exchanges code for tokens at Okta (using PKCE verifier)
6. Token stored locally

> **Note:** The redirect URI registered in Okta is always HTTPS + FQDN.
> The localhost relay is a browser-local navigation, not an Okta redirect URI.
> If you also deploy browser-based gateway login through oauth2-proxy,
> keep that as a separate `Web Application` integration using
> `https://<YOUR_HOST>/oauth2/callback`.

## Verification

```bash
# 1. Browser: visit http://YOUR_HOST — should redirect to Okta login

# 2. After login, check the sidecar validates the token:
curl -v http://YOUR_HOST/gw/llm/v1/models
# Should return 200 with model list (not 401)

# 3. Check audit log
curl http://YOUR_HOST:8081/health
```

## Environment Variable Reference

| Variable | Component | Purpose |
|---|---|---|
| `JWT_ALGORITHM` | Sidecar + Gateway | Set to `RS256` for Okta |
| `JWT_JWKS_URL` | Sidecar + Gateway | Okta JWKS endpoint |
| `JWT_ISSUER` | Sidecar + Gateway | Expected `iss` claim value |
| `JWT_AUDIENCE` | Sidecar + Gateway | Expected `aud` claim (**recommended** — prevents cross-client token acceptance) |
| `JWT_DEFAULT_PROJECT` | Sidecar + Gateway | Fallback project_id for IdP tokens (see warning in Step 2) |
| `R4_OIDC_ISSUER` | r4 CLI | OIDC issuer URL for CLI login |
| `R4_OIDC_CLIENT_ID` | r4 CLI | Public client ID for CLI login |
| `R4_GATEWAY_URL` | r4 CLI | Gateway HTTPS URL (e.g. `https://data0.ai.r-ccs.riken.jp/rccs`) |
| `R4_OIDC_CLIENT_ID` | R4 CLI | Client ID for OIDC |
| `R4_GATEWAY_URL` | R4 CLI | Gateway HTTPS URL (used as redirect base) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| 401 "Invalid token" | JWKS URL wrong or unreachable | Verify `JWT_JWKS_URL` is reachable from sidecar |
| 401 "Token missing project_id" | `JWT_DEFAULT_PROJECT` not set | Set `JWT_DEFAULT_PROJECT=default` |
| 401 "No JWKS key found for kid=…" | Key rotation in progress | Sidecar auto-retries once; restart if persistent |
| oauth2-proxy redirect loop | Callback URL mismatch | Ensure `redirect_url` matches Okta app config |
