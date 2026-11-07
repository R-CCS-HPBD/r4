# Stand-alone R4 + UMRS — the whole platform on one host

**Status (2026-10-15).** The stack boots, and `r4 chat`, knowledge upload and
`r4 knowledge query` work end to end. That was measured on macOS (Apple M3,
podman machine, native ollama, dev profile):
`r4` → nginx → auth sidecar (JWT) → umrs `/umrs/llm` → endpoint registry →
ollama.
The knowledge graph, artifacts, teamup and the research loop were verified
by hand on 2026-10-09, and T2 passes on the dev profile (Known-missing,
below). Registration was verified then with the older `token` +
`r4 source use` + `r4 auth login` flow. **Ran live on Linux 2026-10-16** (a Linux workstation, Docker Engine): the one-command
path (`./r4-local up` — image build, bootstrap, stack up, the smoke test's
chat/ingest/retrieval), the `register` verb, the Docker overlay, an
idempotent re-run, `down` and a resume on the same volumes.

**Not yet run live:**
- `register` creating the stack's project on the server (added 2026-10-16,
  after the live run; tested against stubs and a local HTTP probe only);
- the GPU overlays (the live host was CPU-only);
- the macOS and WSL2 one-command paths;
- T2 on the hardened profile.

Fine for development and evaluation; do not put sensitive research data in
it yet.

Design: [ADR-090](../../docs/adr/090-standalone-single-host-deployment.md).
The plan and parity matrix are kept in the maintainers' development records.

## What this is

Everything that normally lives on an institution's front-end — `umrs-server`,
Postgres + pgvector, SeaweedFS, the NGINX + auth-sidecar edge, the MCP surfaces
and the model — runs in containers on one machine, for one scientist.

It is **not** a different product. The mode switch is one line of configuration:

```yaml
# site.yaml
scheduler:
  kind: compose          # ADR-004 — already one of the three legal authorities
```

`get_orchestrator()` hands back `DockerComposeBackend` instead of
`SlurmPodmanBackend` (`umrs/src/umrs/orchestrator/factory.py:45-49`). Everything
above that line — the durable job store, the attempt state machine, the sidecar
routers, the `X-Verified-*` HMAC handshake, the ZAP session binding, the access
guard, the knowledge spine — is the same process reading a different YAML file.

To the client the stack is one more **platform source**, `standalone`.
`./standalone.sh register`, which the one-command path runs, adds it with:
- its own 30-day token;
- its ZeroMQ address;
- the chat model the stack serves;
- the stack's project, which `register` also creates on the server
  (2026-10-16, below).

The model must be named: r4's built-in default is a cluster model that does
not exist here. Loopback is deliberately exempt from the `https://` upgrade
(`r4/src/r4/config.py`).

## One command

*(Added 2026-10-15; onboarding decisions D-5, D-6, D-7.)*
From the repository root:

```bash
./r4-local up            # = deploy/standalone/standalone.sh quickstart
```

It runs these steps, in this order (`lib/quickstart.sh`):

1. **Preflight, with fixes** (`lib/preflight.sh`). It checks:
   - the host tools `python3`, `curl`, `nc` and `openssl`, and prints the
     exact `apt`/`dnf`/`pacman` line for a missing one;
   - memory in MiB with a 10 % tolerance, so a 16 GB host that reports
     15.3 GiB passes. Under WSL2 the limit is the WSL VM's, and the
     `.wslconfig` fix is printed;
   - under WSL2, systemd: required for rootless podman, only a warning under
     Docker Desktop;
   - on macOS with podman, a podman machine that exists, is *running*
     (`podman info`) and is big enough: 8 GB with the model on the host, 16 GB
     with it inside. It is created, started or resized only with `--yes` or
     your yes; otherwise the exact command is printed. On macOS podman counts
     as the engine as soon as it is installed, so a first run reaches this
     offer even with the machine stopped or missing — unless Docker Desktop
     is installed too (its app bundle — a bare docker CLI such as brew's
     docker or colima is not Desktop) but not running: then podman counts
     only if its machine already exists (with a warning naming Docker
     Desktop), and with no machine the run stops, asking you to start
     Docker Desktop or to set
     `STANDALONE_ENGINE=podman`. Then `podman compose` must work: it needs a
     compose provider (`brew install docker-compose`);
   - under Docker Desktop (macOS, Linux, WSL2), the memory of Desktop's VM,
     which must be running;
   - disk space;
   - ports 8080 and 5555–5563, which must be free or held by this stack.
     A dev debugging port held by another program is moved in
     `env/local.env`, to a port nothing listens on and the stack does not
     publish.
2. **Model by memory** (`lib/model.sh`, D-7). `--model NAME` overrides the
   table, and so does a value you set:

   | Memory | Model |
   |---|---|
   | < 12 GB | `llama3.2:3b` |
   | 12–24 GB | `qwen2.5:7b` |
   | ≥ 24 GB unified (Apple silicon, model on the host), or ≥ 12 GB VRAM | `qwen2.5:14b` |
   | ≥ 48 GB | `qwen3:30b` |

   Memory is the host's for your own ollama. For the model in a container
   it is the engine's: Docker Desktop's VM, or the macOS podman machine.
   The steps allow 5 % (a "48 GB" host reads ~47 GiB), GPU memory 2 % (an
   11 GB card is not a 12 GB one). The choice, the container's memory limit
   and its parallel slots go to `env/local.env`, never to the tracked
   `env/base.env`. A model or memory limit you set, in `env/local.env` or
   in `env/base.env`, is kept.
3. **ollama.** On macOS the stack uses your own ollama when one is
   installed, and checks that it serves the 32k window. With your yes it fixes
   the window (`launchctl setenv`); otherwise it stops with the exact fix.
   On Linux an ollama that listens on `127.0.0.1` only (the systemd default)
   cannot be reached from containers. The stack then runs its own ollama and
   prints the `OLLAMA_HOST=0.0.0.0` fix for using yours. Yours is used only
   when it listens beyond loopback *and* the `ollama` CLI is installed: one
   that listens widely with no CLI is usually itself a container, so the
   stack runs its own. A window that is short, or that your ollama does not
   report, is never registered. The model is pulled.
4. **`bootstrap`**, then **`up`** with the **dev** profile for a new stack
   (D-6: dev until hardened passes T2 and the WS-08 socket lands). The run
   says this in one line. A stack that already exists (its secrets or its
   volumes) with no recorded profile stays on hardened. `up` records the
   profile and engine in `env/.state` (gitignored). Bootstrap reads the
   secrets in a subshell: no later process (uv, make, r4) inherits them.
5. **The r4 client** from this checkout. If `.venv` is missing it is created
   with `uv sync --all-packages --extra client --inexact`. If uv is missing,
   its one-line install is printed.
6. **`register`** (D-5): a 30-day token for the stack's user, minted inside
   umrs, is piped into `r4 source add standalone … --api-key-stdin
   --default-model <model> --project default --replace`. The token is never
   an argument, a temporary file or output; r4 stores it in your r4 and
   umrs config (0600), as it does any source's key. **It never runs
   `r4 source use` or `r4 auth login`**: your active source (for example
   rccs) stays as it was. Use the stack with
   `R4_SOURCE=standalone .venv/bin/r4 chat "hello"`, or
   `source .venv/bin/activate && export R4_SOURCE=standalone` and then
   `r4 console`. `R4_MODEL` or `R4_PROJECT_ID` exported in your shell still
   override the source's model and project; the run names them.
   *(2026-10-16)* `register` then creates the stack's project as a server
   row: `POST /gw/projects` through the gateway, with a one-day token for
   the same user and project on curl's stdin (`-H @-`, `--noproxy '*'`).
   The server's create is idempotent (an existing row is returned as it is,
   never rewritten). Before this, no stand-alone path created the row, so
   `UMRS_PROJECT_DEFAULT_DATA_CLASS=non_sensitive` (compose.yaml, ADR-062
   amendment D-4) applied to nothing and `r4 project data-class … default`
   answered 404. `register` prints the class the server returned, the
   narrowing command for a `non_sensitive` project, and the owner when it
   is not the registering user; a failed create stops `register` by name.
7. **Smoke test**: one chat through the gateway with the source's own model
   and project, as `R4_SOURCE=standalone r4 chat` runs it; the bundled
   `sample/r4-local-sample.md` ingested, with a knowledge row and its
   vectors read from Postgres; one `r4 knowledge query` that must cite it.
   It prints a PASS/FAIL table and never reports PASS without that evidence.

Run it again at any time. On a working stack (umrs answers in its
container) it only checks, applies pending schema migrations, re-mints the
token, and re-runs the smoke test. The sample is not uploaded twice, unless
an earlier upload never got its vectors: then it is uploaded again. It
never deletes data. `./r4-local down | status | doctor | logs | register`
forward to the verbs below.


## Requirements

| | |
|---|---|
| OS | Linux (Docker Engine or rootless podman), macOS (podman machine or Docker Desktop), Windows **via WSL2 only** |
| Engine | Docker Compose v2, or `podman compose` (podman ≥ 4.7) with Docker Compose v2 as its provider. The Python `podman-compose` is refused by name. With both installed: `STANDALONE_ENGINE`, else the engine recorded in `env/.state`, else docker when its daemon answers, else podman. A `docker` that is podman underneath (the podman-docker shim) counts as podman. On macOS with Docker Desktop installed (its app bundle — a bare docker CLI such as brew's docker or colima is not Desktop) but not running, podman counts only if its machine exists; otherwise the run stops and names both choices. (`STANDALONE_DESKTOP_APP` moves where the bundle is looked for.) An exported or recorded engine must answer, or the run stops naming it. Under Docker, `compose.docker.yaml` maps `host.containers.internal` to the host (podman does that itself). |
| RAM | Linux and WSL2: 16 GB (compared in MiB, 10 % tolerance; WSL2: the VM's `.wslconfig` limit). macOS: the podman VM needs 16 GB if it also runs the model, **~8 GB with `STANDALONE_MODEL_HOST=native`** — on a 16 GB Mac use native, or the VM starves macOS and ollama. `preflight` checks the right number and prints the resize line with it. |
| Disk | **15 GB to run**: the UMRS image is pulled, published for linux/amd64 and linux/arm64 (about 1 GB compressed; since 2026-11-06). **30 GB to build** it yourself with `STANDALONE_BUILD=1` (~16 GB transient, never returned by a macOS podman VM). Measured, not guessed. |
| GPU | optional. CPU-only works. On Linux the container ollama gets the GPU when `nvidia-smi` works: `compose.gpu-nvidia.yaml` under Docker with the NVIDIA runtime registered or `nvidia-container-runtime-hook` on PATH; `compose.gpu-cdi.yaml` with a CDI spec under podman, or under Docker whose daemon has CDI enabled (`docker info --format '{{json .CDISpecDirs}}'` is not `[]`). A broken NVML is a CPU host. `STANDALONE_GPU=auto\|off\|nvidia\|cdi`, exported or in `env/local.env`, overrides. Not yet run live. |
| Network | **Internet required for now** (image pulls, `model pull`). The runtime model network has no egress. Fully offline/air-gapped is a later milestone — ADR-090 D-9. |

Native Windows is unsupported: `_BINARY_SUFFIXES` (`r4/src/r4/_cli/tui.py:43-48`)
has no win32 key, so the console binary cannot be fetched.

## By hand — developing on a Mac

The one-command path does all of this. By hand:

```bash
# once
echo 'STANDALONE_MODEL_HOST=native' > env/local.env   # model on native ollama (Metal), see below
ollama pull llama3.2:3b
./standalone.sh bootstrap        # secrets, IAM, migrations, bucket (the text embedder is in the image)

# every day
./standalone.sh up --dev         # waits for health, adopts the model; records dev in env/.state
./standalone.sh register         # the r4 source `standalone`; your active source is unchanged
```

After that `R4_SOURCE=standalone r4 chat "hello"` talks to your local stack.
`./standalone.sh token` still prints the manual login commands (`source use` +
`auth login`, which switch your active source).

### The UMRS image

`./r4-local up` and `./standalone.sh up` pull the published UMRS server image
`ghcr.io/r-ccs-hpbd/r4-standalone-umrs` (CPU, linux/amd64 for Windows/WSL2,
Intel Macs and Linux; linux/arm64 for Apple-silicon Macs), pinned by digest in
`compose.yaml` and `standalone.sh` (2026-11-06). The large server image is
not built on your machine; only the two small gateway images (`gateway-sidecar`,
`gateway-nginx`) are.

- **While the image is private**, log in once with a GitHub token that has
  `read:packages`: `podman login ghcr.io -u <github-user>` (or
  `docker login ghcr.io -u <github-user>`). Without it the pull stops with
  that same hint.
- **Another image:** set `UMRS_IMAGE` in `env/local.env`.
- **Build from your checkout** (UMRS developers): `STANDALONE_BUILD=1`
  (exported or in `env/local.env`) selects `compose.build.yaml`, which builds
  `ai4s-standalone-umrs:latest` the way every install did before. It needs
  30 GB free.

The edit-run loop:

| You changed | Do |
|---|---|
| `umrs/src/**` or `r4-contracts/**` | `./standalone.sh restart umrs` — the dev profile mounts your checkout over the image's code, so no rebuild |
| `r4/**` | nothing — `r4` runs natively from your checkout |
| dependencies (`pyproject.toml`) | `STANDALONE_BUILD=1 ./standalone.sh up` builds the image from your checkout (compose.build.yaml; ≈16 GB of transient disk on macOS, never returned by the VM), or use the next published image (`UMRS_IMAGE`) |
| anything, and you want a clean slate | `./standalone.sh reset --yes` (destroys all volumes, including the embedder choice) |

## Commands

```bash
./standalone.sh quickstart [--yes] [--model NAME]  # everything, in order (= ./r4-local up)
./standalone.sh preflight              # check the host; refuses early and loudly
./standalone.sh bootstrap              # first run: secrets, IAM, s3cfg, migrations, bucket
./standalone.sh up [--dev|--hardened]  # hardened unless recorded or asked; records the profile
./standalone.sh register [user] [project]  # the r4 source `standalone`, 30-day token on stdin
./standalone.sh token [user]           # manual client login commands with a 30-day JWT
./standalone.sh doctor                 # health probes
./standalone.sh restart [svc]          # restart one service (default umrs); refreshes the gateway
./standalone.sh logs [svc]             # follow logs
./standalone.sh model pull|list|register
./standalone.sh embedder pull R D      # stage ANOTHER text embedder into /models/D (e5-base-v2 is bundled)
./standalone.sh reranker pull [R] [D]  # stage the cross-encoder (default MiniLM -> /models/ms-marco-MiniLM-L6-v2)
./standalone.sh migrate [--status]     # apply (or list) schema migrations against the running postgres
./standalone.sh orphan-repair [--apply] [--project P]...  # knowledge orphan repair; dry run unless --apply
./standalone.sh down                   # stop, keep data
./standalone.sh reset --yes            # DESTROY all volumes
```

`env/local.env` (gitignored) holds this machine's own settings — it is layered
after the profile, and anything exported in your shell still wins. Never edit the
tracked `env/*.env` files for a host-specific choice. Quickstart writes its own
choices there (model host, model, memory, slots, moved dev ports) and records
each in `env/.state`, so a later run may update a value it chose itself, never
one you set (a model or memory limit an older install edited into
`env/base.env` counts as yours). `STANDALONE_GPU` and
`STANDALONE_HOST_GATEWAY` are read from there too. `env/.state` (gitignored,
0600) also holds the profile and engine `up` last used; later verbs read it,
and an exported `STANDALONE_PROFILE` or `STANDALONE_ENGINE` still wins.

## Profiles

Two profiles, differing by **configuration only** — there is no `if dev:` branch
in product code (ADR-090 D-2).

| | hardened (default) | dev (opt-in) |
|---|---|---|
| `UMRS_AUTH_MODE` | `strict` | `strict` — dev relaxes the *credential*, never the gate |
| `UMRS_ZMQ_AUTH_TYPE` | `session` (CURVE + ZAP) | `none`, loopback-only |
| `UMRS_STEP_EXECUTOR` | `podman` (sibling containers) | `inproc` |
| Uploaded step code | refused by name | runs in the UMRS process (`UMRS_STEP_UPLOADED_CODE=legacy_in_process`, ADR-074's transition switch) |
| Mocks | absent | absent: `slurm-mock` and `compute-mock` are the `ci` compose profile (`STANDALONE_PROFILES=core,ci`) |
| Chosen by | the default, and an existing stack with no recorded profile | `STANDALONE_PROFILE=dev`, `up --dev`, or quickstart on a new stack; `up` records it in `env/.state` and later verbs follow it |
| Banner | none | red `DEV PROFILE` banner on each verb; quickstart says it in one line instead |

Dev is for development and demos. It is not a "lighter install" of the same
thing, and `standalone.sh` says so every time it starts.

## Model backends

One seam, three adoptions (ADR-090 D-3). Each is an `EndpointRegistry` row
created by `POST /umrs/endpoints` then `PUT .../status ready` — stand-alone
**adopts** an endpoint, it never provisions one, so no GPU floor is relaxed.

- **`local`** (default) — **ollama** on the `standalone-models` network, which is
  `internal: true`. Offline is enforced at the network tier, not by environment
  variables. Small models are the default: `llama3.2:3b` (~2.0 GB) on a laptop,
  `qwen2.5:7b` or `:14b` where there is VRAM to spare. The model **must support
  tool calling** or the companion cannot call tools — verified working on
  `llama3.2:3b` 2026-10-08 (`finish_reason: tool_calls`).

  Because the runtime network has no egress, weights are staged explicitly:

  ```bash
  ./standalone.sh model pull llama3.2:3b   # needs network; writes to the volume
  ./standalone.sh model list               # what is staged
  ```

  Then set `STANDALONE_CHAT_MODEL` in `env/local.env` (quickstart writes the
  model it chose there). `up` refuses by name if the configured model is not
  staged, rather than failing at first chat.

  `OLLAMA_CONTEXT_LENGTH` (default 32768) must stay large: ollama otherwise
  serves 4096 tokens, and the companion's tool set alone is ~10.5k, so tools get
  silently truncated. **On macOS the model runs on CPU inside the podman VM** —
  fine for staging and smoke tests, far too slow for an interactive agent. Use a
  GPU Linux host for real work.
  **On a Mac, run the model natively instead** (`STANDALONE_MODEL_HOST=native` in
  `env/local.env`). The containers then reach your own ollama at
  `host.containers.internal:11434`, which uses Metal — far faster than CPU inside
  the podman VM — and the VM needs only ~8 GB because it no longer hosts the
  model. The trade-off: offline is then only as strong as your host's own
  setup, not enforced by the container network.
  `up` refuses to register a native model whose served window is below
  `OLLAMA_CONTEXT_LENGTH`, or that does not report its window, and prints
  the fix (`STANDALONE_ALLOW_SHORT_CONTEXT=1` registers it anyway).
  **On Linux**, a native ollama must listen beyond `127.0.0.1` to be reached
  from containers (`OLLAMA_HOST=0.0.0.0:11434` in its systemd unit). That
  serves the unauthenticated ollama API to your network unless a firewall
  blocks 11434. So by default quickstart runs the container ollama there.

  **Parallel slots (ADR-093 MD-11, decided 2026-10-11).** On a capable host —
  ≥ 16 GB RAM, or a GPU — the recommended setting is `OLLAMA_NUM_PARALLEL=2`:
  side calls (the router, extraction, the sufficiency check) then get their own
  slot instead of evicting the agent's. The default stays `1`, because two
  slots at a 32k window need ~9.6 GiB of KV cache for `llama3.2:3b`, over the
  default `STANDALONE_CHAT_MEM=8g`.
  - Container ollama: `OLLAMA_NUM_PARALLEL=2` and `STANDALONE_CHAT_MEM=12g` in
    `env/local.env`, then `./standalone.sh up`.
  - Native ollama (`host.containers.internal:11434`): compose does not manage it,
    so set it on the host — macOS app: `launchctl setenv OLLAMA_NUM_PARALLEL 2`,
    then quit and reopen Ollama; Linux service: `systemctl edit ollama`
    (`Environment=OLLAMA_NUM_PARALLEL=2`); by hand: `OLLAMA_NUM_PARALLEL=2 ollama serve`.

  `./standalone.sh doctor` prints the slot count it can read (the container's
  env, or the launchd/systemd env of a native ollama) as a hint; it never fails
  on it.
- **`hosted`** — an external API through the existing `infer_proxy` with a squid
  egress sidecar. Requires the project's `data_class` to be `non_sensitive`
  unless the API is an institutional provider (ADR-062 amendment 2026-10-15).
  On a stand-alone stack the packaged list applies (RIKEN's Rikyu) and
  cannot be changed yet. Compose passes no `UMRS_INSTITUTIONAL_PROVIDER_HOSTS`
  to umrs, and `register` never runs `r4 login`, which is how r4 learns a
  site's list (2026-10-16). `non_sensitive` is the
  stand-alone default for new projects, and `register` creates the stack's
  project with it (2026-10-16); check/change with
  `r4 project data-class get|set`. Research data leaves the machine; the TUI
  marks the session.
- **`remote`** — models stay on the institution's GPU cluster, everything else
  local, over the existing `ssh_tunnel.sh`.

The **embedder is a separate knob** and defaults to local-CPU under all three, so
switching chat backend never invalidates a catalog (ADR-090 D-4).

## Ports

The hardened profile publishes only the gateway (`127.0.0.1:8080`) and the ZMQ
planes (`5555`, `5556-5563`). The umrs sidecar (`8082`) is **not** published: its
routers trust the `X-Verified-*` headers the edge sets, without an HMAC check, so
a published port would let any local process act as any project. The dev overlay
(`compose.dev.yaml`) publishes the sidecar and the stores on loopback for
debugging, each on a port you can move: `STANDALONE_UMRS_HTTP_PORT` (8082),
`STANDALONE_POSTGRES_PORT` (5432), `STANDALONE_S3_PORT` (9000),
`STANDALONE_S3_IAM_PORT` (8111) and `STANDALONE_SIDECAR_PORT` (8081) in
`env/local.env`. Quickstart moves one that another program holds. Everything
else is reached through `compose exec -T`.

## MCP

Hatch/Hatchling, exactly as the remote deployment uses it (ADR-090 D-5). The Hatch
runtime runs on your machine and UMRS runs in a container, so tell the runtime where
to listen and what address UMRS should dial:

```bash
export HATCH_REGISTRY_URL="$PWD/../../mcp-servers/hatch-registry"   # the in-repo registry
export R4_HATCH_BIND_HOST=0.0.0.0                       # a loopback-only port is unreachable from podman on macOS
export R4_HATCH_ADVERTISE_HOST=host.containers.internal
r4 mcp hatch install platform-tools
r4 mcp hatch connect platform-tools
```

Listening beyond loopback is safe: the runtime then gets a random per-runtime token,
requires it on every request (a LAN peer gets 401), and registers it with UMRS, which
presents it on each tool call. Listings show the token as `***`. Verified 2026-10-09:
a call through UMRS reaches the tool, and the tool's own gateway calls go to this
stack. The full write-up is kept in the maintainers' development records.

## Knowledge and RAG

*(Updated 2026-10-09.)*

- **`r4 knowledge upload <file>`** builds a graph and vectors: the local chat model
  extracts entities and relations through the endpoint registry, and the e5-base-v2
  bundled in the image writes the vectors. Follow it with `r4 job list`.
  `graph-ingest --file` does the same into a named graph.
- **By modality:** text, code and structured files embed; PDF and PPTX embed through
  their extracted text. An image, video, audio or binary file **fails** its understand
  and embed steps by name ("no embedding model for image content"): this site has a
  text embedder only.
- **`r4 knowledge search`** ranks by meaning (the RAG service encodes the query with
  the same model) and names the lane it ran.
- **`r4 knowledge query "<question>"`** answers from your uploaded documents and cites
  them by name. The legacy per-experience RAG store is not consulted
  (`UMRS_RAG_QUERY_FACADE=spine`, the default).
- **Orphan repair (ADR-093 MD-17, 2026-10-11).** Embed writes from before wave F's
  one-transaction writer could leave chunk or file nodes whose artifact row is
  gone. `./standalone.sh orphan-repair` counts them per project and writes
  nothing; read the JSON line, then `./standalone.sh orphan-repair --apply`
  deletes them (one transaction per project; a second run finds nothing).
  Projects are named with `--project P` (repeatable); without one it checks
  `default`, the project `token` uses. Run `./standalone.sh migrate` first
  (0122 indexes the column the repair reads).

## The retrieval eval loop (ADR-093)

*(Added 2026-10-10.)* ADR-093 changes retrieval one measured factor at a time,
on the SciFact harness in `benchmarks/retrieval/` (its README has the data and
the arms). Every factor is a knob, and the stack never needs an image rebuild
for one. One turn of the loop:

1. **Code.** Edit `umrs/src/**`. The dev profile mounts it into `umrs` and
   `rag`, so `./standalone.sh restart umrs rag` runs the new code.
2. **Schema.** A new migration (the lexical lane's 0120, the graph lane's
   label index 0121) applies against the running database: `./standalone.sh migrate --status` lists what is pending,
   `./standalone.sh migrate` applies it. Dev applies the checkout's
   migrations; hardened applies the ones in its image.
3. **Models.** The cross-encoder is staged once, with network:
   `./standalone.sh reranker pull` (MiniLM, ~91 MB, into the `umrs_models`
   volume). Then `UMRS_RAG_RERANKER_MODEL=/models/ms-marco-MiniLM-L6-v2` in
   `env/local.env`. A hub id is refused by name: nothing downloads at runtime.
   The image never bakes MiniLM in (ADR-093 MD-3, decided 2026-10-11): this
   pull is the one path, and the reranker stays off by default
   (`UMRS_RAG_RERANK_DEFAULT=off`; S4 failed narrowly).
4. **Knobs.** Every ADR-093 knob is mapped in `compose.yaml` with its
   default (listed in `env/base.env`). Set one in `env/local.env`, then
   `STANDALONE_PROFILE=dev ./standalone.sh up`. `restart` does not re-read env:
   an env file only interpolates when a container is created.
5. **Check.** `./standalone.sh doctor` says whether the reranker is off,
   staged, or configured but not what the rag container runs (a forgotten
   `up`). The rag service's `/rag/stats` names the reranker model and revision,
   or says `unavailable (not staged)`, and every `GET /gw/rag/retrieve?trace=1`
   reports each lane, `unavailable` with its reason included.
6. **Measure.** Run the arm, paired against its predecessor and the baseline,
   with `benchmarks/retrieval/run.py`. Keep the commits, the model revisions
   and the `UMRS_RAG_*` values with the result. A default changes only when
   its gate in ADR-093 passes on the tune split.

## Known-missing

- WS-06: the `hosted` and `remote` backends. `local` (ollama) is adopted as an
  `EndpointRegistry` row by `up`; the other two are not wired.
- WS-08 step lane: `UMRS_STEP_WORKDIR` under an identical-path mount — the fix was
  measured to work inside `podman machine` on 2026-10-08, but is not implemented.
- WS-09 parity suite. **Verified by hand on 2026-10-09:** the knowledge graph,
  artifacts, teamup and research loop each run end to end
  (validation report §9–§12, maintainers' development records). **T0** (deployment truth over these files) is
  `tests/test_standalone_deployment_truth.py`. **T2** (the four planes, live) is
  `R4_RUN_E2E=1 STANDALONE_PROFILE=dev pytest tests/test_standalone_live_e2e.py`:
  8 passed in about 2 minutes on the dev profile. Still missing: a T2 run on
  hardened, a CI lane for it (WS-10), and a `doctor` that runs it.
- Hardened embeds in the UMRS process, not in the ADR-061 sandbox, and its
  workflow steps cannot launch: both need the podman socket in the umrs container
  (WS-08).
- Search scores are uncalibrated (ADR-083 D-5), and the knowledge lane has no image
  embedder: see the validation report §7–§8.
