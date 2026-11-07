# 11. Multi-Source Routing

R4 can connect to multiple LLM backends simultaneously — your own platform, public cloud APIs, and other OpenAI-compatible services. The `source` system manages these connections.

## Concepts

A **source** is a named connection to an LLM backend. Each source has:

- A name (for quick reference)
- A URL (the API endpoint)
- A kind (`platform` or `public`)
- Optional authentication (API key, AWS credentials)

For platform backends, an R4 source and a UMRS connection profile identify the
same deployment but are consumed by different clients:

- R4 reads `~/.config/r4/sources.json` and sends HTTP requests to the source's
  Gateway URL.
- `umrs` reads `~/.config/umrs/connections/<name>.json` for ZMQ and HTTP
  diagnostics.
- `r4 source use <name>` changes both clients' default pointers. It does not
  lock the deployment or prevent another command from targeting another source.

The source name is a routing label, not a hostname. Multiple sources can use
different URL prefixes on the same host while targeting independent Gateway and
UMRS deployments, registries, schedulers, and credentials.

The names `site` and `local` are reserved for r4 itself (2026-10-15). No new
source row may take them, in any letter case: `r4 source add`,
`r4 source add-ollama`, the source row a login writes and the companion's
`source_add` tool all refuse them. A row of that name written earlier can
still be updated or removed.

### Each source gets only its own credential and identity (2026-10-15)

A request to a source carries only what belongs to that source. This applies
to every path: `--source`, `model@source`, `r4 endpoint`/`pool`/`health`
across sources, the companion's `source=` tools, model listing and scale
probes.

- **Credential.** The source's own key from `sources.json` is used first.
  Otherwise r4 uses `R4_API_KEY_<NAME>`, or the login kept under the source's
  own name (`r4 login --profile <name>`), but only when that login records
  that it was made for the source's deployment. A login that records another
  deployment, or no deployment at all, is not used. If no credential is
  found, no `Authorization` header is sent. Another deployment's login is
  never reused. This covers the active profile's login and `R4_API_KEY`
  (one legacy `R4_GATEWAY_URL` case is listed below).
- **Project and user.** A source on another deployment is sent its own
  `project`, if it has one. If it has none, no `X-Project-ID` header is sent.
  The `X-User-ID` header is the user named in its own token. The active
  deployment's project and user are never sent. Commands that act on a
  project (`r4 project storage`, knowledge, RAG, artifacts) use only that
  source's project under `--source`, never the project you switched to on
  the active deployment.
- **Data plane.** `--source` also moves the ZMQ endpoint to the source's
  `--zmq-host`/`--zmq-port`. The ZMQ session is registered at that source with
  its own token, and its CURVE keys are kept in a session file of their own.
  `r4 --source NAME` with no subcommand opens Console v3 on that source.
- **Where your login belongs.** r4 decides which deployment the profile's
  login (or `R4_API_KEY`) is for, in this order:
  1. the gateway `r4 login` / `r4 auth login` recorded. Every login records
     it from 2026-10-15: the `--gateway-url` you gave, the `gateway_url`
     claim of the token, or a URL typed at the prompt. A login that names
     none (a piped `--api-key-stdin` login, Enter at the prompt) keeps the
     deployment the login it replaces recorded, so a token refresh while
     another source is active stays home and the CLI prints the
     `--gateway-url` line that moves it. With nothing recorded before, it
     records the gateway r4 used at the time (`R4_GATEWAY_URL` or the active
     source). The built-in `http://localhost:8080` is never recorded unless
     one of those two names it: a profile or repo TOML often kept it as the
     old prompt default. With nothing to record, the home stays unknown.
  2. the `gateway_url` claim of the stored token;
  3. with `R4_GATEWAY_URL` set, the profile's `gateway_url` when it is a
     registered platform deployment and `R4_GATEWAY_URL` is another
     registered one (not when the profile names only
     `http://localhost:8080`);
  4. `R4_GATEWAY_URL`;
  5. the profile's `gateway_url`, when a registered platform source is that
     deployment;
  6. with no active source stored, the gateway the profile uses on its own.

  A repo `.r4/*.toml` `gateway_url` never decides it while a source is
  active.
- **Active source.** If the active source is not the deployment your login
  belongs to, it does not inherit the profile's login, project or user. It
  gets its own key, its own ZMQ endpoint, the user named in its own token (or
  `UMRS_USERNAME`), and a project in this order: `R4_PROJECT_ID`, then the
  project `r4 project switch` selected when that project records this
  deployment (`r4 project create --gateway URL`), then its own `project`. If
  it has no credential of its own, r4 sends none. If r4 cannot tell where
  the login belongs and more than one platform deployment is registered,
  nothing of the login moves either. With a single platform deployment, the
  login is used as before. If `r4 project switch` selects a project that
  such a source is not sent, it says so and prints the `r4 source set`
  line that sends it.
- **The login's own deployment.** While the process targets another source,
  a request to the deployment your login belongs to (`--source a`,
  `model@a`, `r4 health --all`) still carries that login, its project and
  its user.
- **`R4_GATEWAY_URL`.** If the login belongs to another deployment (items
  1–3 above), its credential, project and user are not sent to
  `R4_GATEWAY_URL`. A registered platform source at that URL gets its own
  key. An `R4_API_KEY` exported with it is kept: the two are an explicit
  pair. An explicit `R4_SOURCE` wins over `R4_GATEWAY_URL`, so
  `export R4_SOURCE=standalone` works next to a site's `rccs.env`. Legacy
  exception: a login that records nothing (made before 2026-10-15 with an
  opaque or OIDC token) still goes to `R4_GATEWAY_URL`, as before, unless
  item 3 places it elsewhere. r4 cannot tell an unregistered
  `R4_GATEWAY_URL` (an SSH forward to the login's own deployment, say), an
  unregistered profile `gateway_url` or the old default
  `http://localhost:8080` from the login's deployment. Log in again with
  `--gateway-url` to record where the login belongs.
- **One host, one address.** Spellings of one address are one host:
  `localhost:8080` is `127.0.0.1:8080`, and so is `127.1:8080`. Other
  loopback addresses (`127.0.0.2`, `[::1]`, `foo.localhost`) are other
  listeners, for example an SSH forward to a remote deployment, so they are
  other deployments.
- **No credential.** When a source has no credential of its own, the CLI says
  how to give it one. It also says this when a source refuses its credential
  (401/403). `r4 source set` stores the key and keeps every other field of
  the row (ZMQ, tunnel, description, default model and project). Your active
  login is not changed:

  ```bash
  printf '%s' "$TOKEN" | r4 source set far --api-key-stdin
  # an http:// address on another host also needs --allow-insecure-http
  ```

Before 2026-10-15 a key-less source received the active deployment's token, and
`model@<platform source>` chat sent the active project and user headers. A
setup that relied on this now gets a 401 from that source. The message that
comes with the 401 gives the command that fixes it. If you switch between
several deployments with `r4 source use`, give each one its project with
`r4 source set NAME --project ID`. The profile's project is no longer sent to
the other deployments.

## Managing Sources

### List Sources

```bash
r4 source list
```

### Add a Source

```bash
# Platform source (your UMRS-backed Gateway). The token comes from a pipe:
# never an argument, never echoed (--api-key-env VAR also works; --api-key
# still works but warns that the key stays in shell history).
printf '%s' "$TOKEN" | r4 source add my-hpc \
  --url https://gateway.hpc.example.com \
  --kind platform \
  --api-key-stdin

# Public cloud source
r4 source add openai-prod \
  --url https://api.openai.com \
  --kind public \
  --api-key sk-xxxx

# AWS Bedrock source
r4 source add bedrock-us \
  --url https://bedrock-runtime.us-east-1.amazonaws.com \
  --kind public \
  --provider bedrock \
  --aws-region us-east-1 \
  --aws-profile my-profile

# UMRS source with ZMQ transport
r4 source add umrs-direct \
  --url https://umrs.internal:8000 \
  --kind platform \
  --zmq-host umrs.internal \
  --zmq-port 5555

# Local stand-alone UMRS stack (see Chapter 29 for bring-up and login)
r4 source add standalone --kind platform --url http://localhost:8080 --zmq-host 127.0.0.1 --zmq-port 5555

# A source with its own default model and project (2026-10-15)
printf '%s' "$TOKEN" | r4 source add standalone --replace --kind platform \
  --url http://localhost:8080 --zmq-host 127.0.0.1 --zmq-port 5555 \
  --default-model llama3.2:3b --project default --api-key-stdin

# Local Ollama (OpenAI-compatible; --use also activates it)
r4 source add-ollama --use
```

#### Default model, project and replacing a source (2026-10-15)

- `--default-model NAME` sets the model r4 uses for this source when a
  command names none. `--model`, `model@source` and `R4_MODEL` always win.
- `--project ID` sets the project sent to this source as `X-Project-ID`.
  `R4_PROJECT_ID` wins when the source is on the active deployment. A source on
  another deployment that has no project is sent no project at all.
- Adding a name that already exists updates that row in place, as it always
  has. Every field you do not give is cleared. To change one field and keep
  the others, use `r4 source set` (below).
- `--replace` also drops the credentials stored for the old row: its UMRS
  connection profile and token, the r4 login kept under its name, and the
  CURVE session files kept for it. None of them can then reach the new URL.
  An update that points the name at another deployment drops them too, with
  or without `--replace`. Your own profile login is never touched.
- `sources.json` is written in one atomic step, with mode 0600.
- `r4 source list --json` shows `default_model` and `project` for each row. It
  shows `has_api_key`, never the key.
- `r4 auth login`, `r4 login` and `r4 onboard` update the row whose URL they
  log in to. Since 2026-10-15 they keep every other field of it (the default
  model and project, OIDC, CURVE and tunnel settings).

#### Change one field of a source (`r4 source set`, 2026-10-15)

```bash
printf '%s' "$TOKEN" | r4 source set far --api-key-stdin   # its own credential
r4 source set far --project proj-b                          # the project it is sent
r4 source set far --default-model qwen3:8b
```

Every field you do not name is kept. A new key also drops the CURVE session
kept under the old one. The key flags are the same as for `source add`, and
the same plain-HTTP check applies: an `http://` URL on another host needs
`--allow-insecure-http`. The key is never printed.

### OpenAI-Compatible Providers (OpenAI API format)

Any backend that serves `GET /v1/models` and `POST /v1/chat/completions`
— OpenAI itself, a self-hosted vLLM/LiteLLM server, Groq, Together,
DeepSeek, etc. — registers as a `public` source with **no `--provider`
flag** (omit it, or pass `--provider openai-compatible`). R4 sends the API
key as a bearer token.

#### Register persistently

```bash
r4 source add openai \
  --url https://api.openai.com \
  --kind public \
  --api-key sk-xxxx

# Self-hosted OpenAI-compatible server (vLLM, LiteLLM, TGI-with-openai-shim, ...)
r4 source add my-vllm \
  --url http://my-host:8000 \
  --kind public

# A remote Ollama (or any local one) — shortcut, no key needed
r4 source add-ollama --use                        # localhost:11434
r4 source add-ollama rikyu --url http://rikyu:11434 --use
```

URL rules:

- Pass the **base URL** — `/v1` is appended by the provider layer.
- A registered URL ending in `/v1` is normalized (trailing `/v1` stripped),
  so `http://host:8000` and `http://host:8000/v1` are equivalent.
- Sources are stored in `~/.config/r4/sources.json`.

#### Quick one-shot: `r4 up --public`

For a single model without registering a named source, `r4 up --public`
configures a public endpoint on the active profile:

```bash
r4 up --public openai --api-key sk-xxxx --model gpt-4o
r4 up --public gemini --api-key AIza-xxxx --model gemini-2.0-flash
r4 up --public http://my-host:8000 --model my-served-model   # arbitrary URL
```

Built-in provider shortcuts (expand to their base URLs):

| Shortcut | Base URL |
|----------|----------|
| `openai` | `https://api.openai.com` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `groq` | `https://api.groq.com/openai` |
| `together` | `https://api.together.xyz` |
| `openrouter` | `https://openrouter.ai/api` |
| `deepseek` | `https://api.deepseek.com` |
| `fireworks` | `https://api.fireworks.ai/inference` |
| `mistral` | `https://api.mistral.ai` |

A site-run endpoint has no shortcut; pass its full URL.
Remove a quick endpoint with `r4 down --public`.

#### Verify

```bash
r4 source list                          # shows registered sources + auth mark
r4 model list --source openai           # live /v1/models probe
r4 chat --source openai --model gpt-4o-mini "hello"
```

Public sources are "provisioned by definition" — no `r4 endpoint up` is
needed. Route a model explicitly per call with `--source`, per reference
with `model@source` inside the TUI/agent surfaces, or make the source the
default with `r4 source use openai`.

#### Public sources and sensitive projects (2026-10-16)

A project whose data class is `sensitive` refuses a public source before
anything is sent. r4 decides from the source's address, not from its name
or kind:

- an address on your own machine (`localhost`, `127.0.0.1`) is allowed;
- the address of your institution's provider is allowed. The deployment
  keeps the list of these providers, and r4 reads it when you log in;
- every other address is public and is refused.

A `non_sensitive` project may use any source. See
[Chapter 5](05-projects-models.md#data-class-what-may-leave-your-machine)
for what the classes mean, what the refusal looks like, and
`r4 project data-class`.

### Switch Active Source

```bash
# Set active source
r4 source use my-hpc

# Clear active source (revert to profile default)
r4 source use
```

### Remove a Source

```bash
r4 source remove openai-prod
```

Removing a source deletes its stored credentials: the UMRS connection profile
and token, the r4 login kept under its name, and the CURVE session file kept
for it. These are local files only. The login of the profile r4 runs under
(`R4_PROFILE`, else `default`) is never deleted, even when a source has the
same name. A pushed public source is also unpushed from the server. A
platform source, or a source whose URL is this machine, can never have been
pushed, so removing one contacts no gateway at all (2026-10-15). Before
2026-10-15 removal also logged the source's name out, which revoked the
ACTIVE deployment's ZMQ session over the network.

### Push a Source (remote sessions)

`r4 source push <name>` seals a **public** provider's key on the active
platform so that remote sessions can use it. It refuses a platform source,
because a deployment's login is never sent to another deployment. It also
refuses a source on this machine (loopback), because a remote session cannot
reach it. A loopback URL is recognised in any spelling, for example
`localhost.`, `127.1`, `127.0.0.2`, `[::1]`, `[::ffff:127.0.0.1]`, `0.0.0.0`,
`2130706433`, or with the full stops `。．｡` that the HTTP client reads as
dots (`127。0。0。1`). A DNS name that resolves to this machine (such as
`localtest.me`) is not recognised: r4 makes no DNS lookup to decide. It also
refuses a public source that is really a deployment login: one whose URL is a
route of a registered platform source's deployment (for example
`https://b.example/gw/llm` next to platform source `b`), or whose key is a
token that names a gateway. Every refusal happens before any network call
(2026-10-15).

A remote session for a `sensitive` project does not start when it would
carry a pushed public source, unless that source is your institution's
provider. The error, `source_class_refused`, names the refused sources
(2026-10-16; see
[Chapter 5](05-projects-models.md#data-class-what-may-leave-your-machine)).

## Using Sources

### Per-Command Override

```bash
# Chat using a specific source
r4 chat "Hello" --source openai-prod

# Model list from a specific source
r4 model list --source my-hpc

# Provision without changing the active/default source
r4 endpoint up --source my-hpc --model Qwen/Qwen2.5-7B-Instruct

# Query one source; omit --source to aggregate all registered sources
r4 endpoint list --source my-hpc
r4 endpoint list
```

Explicit `--source` routing does not modify either active pointer. Independent
commands can therefore provision endpoints concurrently:

```bash
r4 endpoint up --source ai4s --model model-a &
r4 endpoint up --source rccs-cloud --model model-b &
r4 endpoint up --source bdrt --model model-c &
wait
```

For a repeatable multi-source launch, set `source` on each pool entry. R4
constructs a separate client for each entry and provisions entries in parallel
by default:

```yaml
endpoints:
  - name: model-on-ai4s
    source: ai4s
    model: model-a
    tier: system
  - name: model-on-rccs
    source: rccs-cloud
    model: model-b
    tier: system
```

```bash
r4 pool up pool.yaml
```

Each target must independently have valid credentials, model registration,
scheduler capacity, and a healthy Gateway. Sharing one frontend hostname does
not merge endpoint or model registries across sources.

### Cross-Source Model Routing

When you have multiple sources configured, R4 automatically routes model requests to the appropriate source based on model availability.

```bash
# Source "my-hpc" has Qwen models; source "openai-prod" has GPT models
r4 chat "Hello" --model gpt-4o          # Routes to openai-prod
r4 chat "Hello" --model qwen3:8b        # Routes to my-hpc
```

### Per-Endpoint Source Auth

Each endpoint can be scoped to a specific source with its own authentication:

```bash
r4 up --public openai --api-key sk-xxxx --model gpt-4o
r4 up --public gemini --api-key AIza-xxxx --model gemini-2.0-flash

# Both endpoints are usable simultaneously
r4 chat "Hello" --model gpt-4o
r4 chat "Hello" --model gemini-2.0-flash
```

## Service models are used automatically (2026-11-03)

You only choose your chat model. Everything else a prompt needs runs on the
platform's service models, picked for you, even when your chat model comes
from your own public endpoint:

- **decisions**: the gates that judge plans and choices;
- **sub-agents**: delegated sub-tasks, on the platform's fast model;
- **images** (VS Code): attached images are described by the platform's
  vision model when your chat model cannot read images;
- **retrieval and document reading**: embeddings, reranking and OCR run on
  the platform side.

R4 always tells you which model did the work: sub-agent lines read
"Delegating to Qwen3.8-27B-NVFP4 (svc-fast, rccs-cloud): …", decision
questions end with "Read by …", and VS Code says "image described by …".

R4 uses a service only while the platform is serving it. To pin a different
model for one of these jobs, set `R4_FAST_MODEL` or `R4_DECISION_MODEL` (for
example `R4_FAST_MODEL=my-small@my-openai`), or in VS Code the
`r4.visionProxyModel` setting.

## Several HPC sites in one session (2026-10-24)

Each registered **platform** source is an HPC site (its own gateway, login
and project). One session can use all of them; nothing is copied between
sites, and each site receives only its own credential.

```bash
r4 hpc sites            # every site: health, access mode, your account, scheduler, staged lane, GPU
r4 hpc sites --json     # the same as JSON (every run probes afresh)
r4 hpc jobs             # your jobs at every site, each once, as <id>@<site>
r4 hpc cancel 1234@bdrt # cancel (or retry) through the site that runs the job
```

The jobs table is shared by the RIKEN sites, so every gateway lists every
job; `r4 hpc jobs` shows each job once, labelled with the site that runs it
(`venue`). A job whose site cannot be named (a shared row, a server that
does not report venues yet, a site that is not one of yours) is shown by its
bare id with the reason in brackets. `r4 hpc cancel|retry <id>` without
`@site` asks your home site for the job (one lookup, any age) and sends the
action to the gateway of the site that runs it; when that site cannot be
told it asks you to name it as `<id>@<site>`. `r4 hpc jobs` exits 1 when no
site could be listed.

**In the agent.** With two or more sites, the agent receives the list with
its first message (`<hpc_sites>`; refreshed every 15 minutes, every 30 s
while a site fails) and the platform tools take `site=<name>` (workflow,
job, file, endpoint and health tools). With one site neither the list nor
the `site=` parameter is sent. Every result names its site, for example
`exec_7f@bdrt`; job rows are labelled with the site that runs the job, and
the agent's `job_cancel`/`job_retry` go to that site's gateway. Reading
(status, lists, results, downloads, health) works at any of your sites;
starting or changing work (run, replay, cancel, retry, provision, stop,
upload) at a site other than the session's own needs that site to be
selectable. The agent refuses rather than guesses:

| Refusal | Meaning |
|---|---|
| `unknown_site` | No site has that name, or the job runs at a site that is not one of yours; the error lists the valid ones |
| `site_access_undeclared` | The site has not yet declared how users may run there (its access mode); work is not started or changed there from another site until it does |
| `no_site_account` | Your account at that site is absent, disabled or could not be read |
| `site_unavailable` | The site did not answer (re-probed after 30 s, or at once with `hpc_sites(refresh=true)`) |
| `site_conflict` | `site=` and the `id@site` handle disagree, a job is named at a site other than the one that runs it, or two sources report the same server site name (name the source instead) |

A site whose access mode is undeclared is listed but marked **not
selectable**. You can still switch the whole session to it (`/source`, or
`r4 source use` before starting). The agent's `source_use` tool changes the
stored source for the *next* session only; in the running session it uses
`site=`.

Handing results from one site to the next works today only when the
consuming site has the staged step lane (rccs), the data sits in your
personal bucket, and the consuming workflow declares the parameter that
carries it.

## Provider Types

| Provider | Auth Type | Description |
|----------|-----------|-------------|
| `openai-compatible` | Bearer token | Any OpenAI-compatible API |
| `bedrock` | AWS SigV4 or Bearer | AWS Bedrock Converse API |
| `anthropic` | Bearer token | Anthropic API |

## Next Steps

- [Chapter 6 — Endpoints](06-endpoints.md)
- [Chapter 12 — Ask: Agent, Search & Teamup](12-ask.md)
