# 29. Stand-alone Deployment — the platform on one host, registered as an r4 source

> **Updated:** 2026-10-16. The one-command path (`./r4-local up`), the
> `register` verb and the Docker overlay **ran live on Linux 2026-10-16**
> (a Linux workstation, Docker Engine: build, bootstrap, stack up, register, the smoke
> test, an idempotent re-run, `down` and a resume). The GPU overlays and
> the macOS and WSL2 one-command paths have
> **not yet been run live**. Verified by hand on macOS on 2026-10-12 (Apple
> M3, podman machine, native ollama with `llama3.2:3b`, dev profile), with
> the older registration (`standalone.sh token`, then `r4 source add`,
> `r4 source use` and `r4 auth login`): `r4 chat`, `r4 knowledge query`,
> `r4 endpoint list`, and `umrs info` in `direct` mode. The step in which
> `register` creates the stack's project was added later on 2026-10-16 and
> has not yet been run live.
> **Status:** a development and evaluation deployment. Do not put sensitive
> research data in it yet. Operator reference:
> [deploy/standalone/README.md](../../deploy/standalone/README.md).

The stand-alone deployment runs the whole platform in containers on one machine,
for one scientist:
- `umrs-server`;
- Postgres + pgvector;
- SeaweedFS;
- the NGINX + auth-sidecar edge;
- the RAG service;
- the model, on ollama.

It is the same UMRS with one configuration line changed (`scheduler.kind:
compose`). To the client it is one more **platform source**
([Chapter 11](11-sources.md)), named `standalone`, next to any cluster you
already use.

## One command

From a clone of this repository, with its `umrs` submodule:

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
./r4-local up
```

`./r4-local up` runs `deploy/standalone/standalone.sh quickstart`. In order:

1. **Preflight, with fixes.** It checks:
   - the host tools `python3`, `curl`, `nc` and `openssl`, and prints the
     exact install line for a missing one;
   - memory (16 GB, compared in MiB with a 10 % tolerance; under Docker
     Desktop, the memory of its VM);
   - disk;
   - ports 8080 and 5555–5563;
   - the per-OS items in the table below.

   A fix that changes your machine runs only with `--yes` or your yes;
   otherwise preflight stops and prints the exact command.
2. **A model sized to your memory**:

   | Memory | Model |
   |---|---|
   | < 12 GB | `llama3.2:3b` |
   | 12–24 GB | `qwen2.5:7b` |
   | ≥ 24 GB unified (Apple silicon, model on your ollama), or ≥ 12 GB of GPU memory | `qwen2.5:14b` |
   | ≥ 48 GB | `qwen3:30b` |

   For a model inside the stack, the memory is the container engine's:
   Docker Desktop's VM, or the macOS podman machine. The memory steps allow
   5 % (a "48 GB" Linux host reads about 47 GiB); GPU memory allows 2 %, so
   an 11 GB card counts as less than 12 GB. `--model NAME` overrides the
   table, and so does a model you set yourself. The choice goes to
   `deploy/standalone/env/local.env`. That file is gitignored; the tracked
   configuration is never edited.
3. **ollama**: your own when the stack can use it, otherwise one inside the
   stack. The model is pulled.
4. **The stack**: `bootstrap`, then `up`. A new stack gets the **dev**
   profile, and the run says so in one line. A stack that
   already exists here (its secrets or its volumes) with no recorded
   profile stays on **hardened**. The profile is recorded in `env/.state`,
   so later commands need no `STANDALONE_PROFILE=dev` prefix. On a rerun
   whose stack is up, pending schema migrations are applied.
5. **The r4 client** from this checkout. It goes into `.venv`, through
   `uv sync --all-packages --extra client --inexact`. Without uv, the
   one-line uv install is printed.
6. **Registration.** A 30-day token for the stack's user is
   minted inside the stack. It is piped on stdin into
   `r4 source add standalone --kind platform … --api-key-stdin
   --default-model <the model> --project default --replace`. The token is
   never a command-line argument, a temporary file or printed; r4 keeps it
   in your r4 and umrs config (0600), as it does any source's key.
   Registration **never runs `r4 source use` or `r4 auth login`**: your
   active source, for example rccs, stays exactly as it was. It also
   creates the stack's project, `default`, on the stack, and prints its
   data class (see [Data class on the stack](#data-class-on-the-stack)).
7. **A smoke test**, which prints a PASS/FAIL table:
   - one chat through the gateway, with the source's own model and
     project, as `R4_SOURCE=standalone r4 chat` uses them;
   - a short bundled sample document, `deploy/standalone/sample/r4-local-sample.md`,
     ingested. The test reads its knowledge row and vectors in Postgres;
   - one `r4 knowledge query`, which must cite the sample.

   It never reports PASS without that evidence.

Run it again whenever you like. On a working stack it only checks, mints a
fresh token, and repeats the smoke test without uploading the sample twice
(unless an earlier upload never got its vectors; then it is uploaded again).
It never deletes data.

### Prerequisites per OS

| OS | Container engine | Memory and disk | ollama | Also | Run live |
|---|---|---|---|---|---|
| **Linux** | Docker Engine with Compose v2 (`docker compose version`; Engine 20.10 or later for `host-gateway`), or podman ≥ 4.7 with `podman compose` backed by Docker Compose v2. The Python `podman-compose` is refused. | 16 GB RAM; 15 GB free disk to run, 30 GB to build the images. | Not needed: the stack runs its own. Your own ollama is used only when it listens beyond `127.0.0.1` and its `ollama` CLI is installed (see below). | `python3 curl nc openssl` (Fedora: `sudo dnf install -y python3 curl nmap-ncat openssl`; Debian/Ubuntu: `sudo apt-get install -y python3 curl netcat-openbsd openssl`). [uv](https://docs.astral.sh/uv/) for the client. Rootless podman: cgroup v2 delegation. | **ran live 2026-10-16** (Docker Engine on a Linux workstation) |
| **macOS** | podman ≥ 4.7 with a podman machine and Docker Compose v2 as its compose provider (`brew install podman docker-compose`), or Docker Desktop (Compose v2 included). | 16 GB RAM. The podman machine needs 8 GB with the model on your ollama, 16 GB with it inside; the Mac keeps at least 4 GiB. Quickstart creates, starts or resizes the machine with your yes, using `podman machine init/set --memory … --cpus … --disk-size …`; a first run with the machine stopped or missing gets that offer too. Docker Desktop: Settings > Resources > Memory; it must be running. With both installed (Docker Desktop means its app bundle — a bare docker CLI such as brew's docker or colima is not Desktop) and Desktop not running, a first run uses podman only if its machine already exists (and says so); with no machine it stops and asks you to start Docker Desktop or set `STANDALONE_ENGINE=podman`. 15 GB free disk to run, 30 GB to build the images (the machine's disk is a sparse file on the Mac's). | The Ollama app (<https://ollama.com/download>), recommended for Metal. It must serve a 32768-token window: quickstart offers `launchctl setenv OLLAMA_CONTEXT_LENGTH 32768` and an Ollama restart. | `xcode-select --install` for the host tools; uv. | the manual path, by hand (podman machine, native ollama); the one-command path: not yet run live |
| **WSL2** (Windows) | Docker Desktop with WSL integration (Compose v2 included), or podman ≥ 4.7 inside the distribution with Docker Compose v2 as its provider (as on Linux). | `%UserProfile%\.wslconfig` with `[wsl2]` and `memory=16GB`, then `wsl --shutdown`. The check reads the WSL VM's limit (Docker Desktop: its VM's) and prints this fix. 15 GB free disk to run, 30 GB to build the images. Keep the checkout on the Linux file system, not `/mnt/c`. | Inside the stack by default. | `/etc/wsl.conf` with `[boot]` and `systemd=true`, then `wsl --shutdown`: required for podman, only a warning under Docker Desktop. The host tools and uv as on Linux. | not yet run live |

On a Linux host where ollama is already installed as a service, it listens
on `127.0.0.1` only, which containers cannot reach. The stack then runs its
own ollama: no change to your host, and no route off it. An ollama that
listens on all addresses with no `ollama` CLI on the host is usually a
container (`docker run -p 11434:11434 ollama/ollama`); the stack runs its own
then, too. To use yours (for example for its GPU), quickstart prints the fix:
- `sudo systemctl edit ollama` with
  `Environment="OLLAMA_HOST=0.0.0.0:11434"` and
  `Environment="OLLAMA_CONTEXT_LENGTH=32768"`;
- `STANDALONE_MODEL_HOST=native` in `env/local.env`.

`0.0.0.0` serves the unauthenticated ollama API to your network unless a
firewall blocks port 11434.

A native ollama whose window is below 32768 tokens, or that does not report
its window, is never registered silently. It is fixed with your yes
(macOS), or the run stops with the exact fix and the way out:
`STANDALONE_MODEL_HOST=container` in `env/local.env` runs the stack's own.

On Linux, with a working NVIDIA GPU and the container toolkit, the container
ollama gets the GPU through a compose overlay: the `nvidia` device driver
under Docker (the toolkit's runtime or its hook), or CDI under podman, and
under Docker only when its daemon has CDI enabled. When `nvidia-smi` fails,
for example after a driver update without a reboot ("Driver/library version
mismatch"), the host counts as CPU-only and no overlay is added. This has
not yet been run live.

### Use it

The client is in the checkout's `.venv`:

```bash
source .venv/bin/activate              # or call .venv/bin/r4 directly
R4_SOURCE=standalone r4 chat "hello"   # one command against the stack
export R4_SOURCE=standalone            # this shell: r4 chat, r4 console, r4 knowledge …
r4 console
```

`R4_MODEL` or `R4_PROJECT_ID` exported in the same shell override the
source's model and project; the quickstart names them when they are set.

`r4 source use standalone` makes it your default; only you run that, the
quickstart never does.

### Your choices

| To | Do |
|---|---|
| pick the model yourself | `./r4-local up --model qwen2.5:7b` (kept on later runs) |
| use the hardened profile | `STANDALONE_PROFILE=hardened ./r4-local up` (recorded; later commands follow it) |
| choose the engine on a host with both | `STANDALONE_ENGINE=podman ./r4-local up`. Otherwise: the engine recorded in `env/.state`, else docker when its daemon answers, else podman. A `docker` that is podman underneath counts as podman. On macOS with Docker Desktop installed (its app bundle — a bare docker CLI such as brew's docker or colima is not Desktop) but not running, podman counts only if its machine exists. An exported or recorded engine that does not answer stops the run by name |
| move where the Desktop check looks | `STANDALONE_DESKTOP_APP=/path/to/Docker.app ./r4-local up` — where the script looks for Docker Desktop's app bundle (default `/Applications/Docker.app`); a bare docker CLI is never mistaken for Desktop |
| answer yes to every host fix | `./r4-local up --yes` |
| register again (new 30-day token) | `./r4-local register` |
| stop, look, follow | `./r4-local down` (data kept), `./r4-local status`, `./r4-local doctor`, `./r4-local logs [service]` |

## The manual path

Everything above, one command at a time, from `deploy/standalone/`.

### Required conditions

The prerequisites table above applies. In addition:

| | Requirement |
|---|---|
| Model | Must support tool calling (verified: `llama3.2:3b`). It must be staged: `ollama pull` for a model on the host, or `./standalone.sh model pull` for one in a container. `up` refuses a model that is not staged. |
| Client | `r4` from this checkout (`.venv/bin/r4`). An older `r4` on `PATH` may lack the team-engine packages. |

`./standalone.sh preflight` checks the host and refuses early, with the reason.

### Ports (on loopback)

| Port | What | Used by |
|---|---|---|
| `127.0.0.1:8080` | NGINX edge (`/gw/*`, JWT checked by the auth sidecar) | every `r4` command |
| `5555` | ZeroMQ control plane | `umrs` CLI, ZMQ-eligible `r4` operations |
| `5556–5563` | ZeroMQ data plane | file and embedding transfers |
| `11434` (host) | native ollama, when `STANDALONE_MODEL_HOST=native` | the stack's model endpoint |

The UMRS sidecar (`8082`) is deliberately **not** published by the hardened
profile. Only the dev profile publishes it and the stores on loopback, for
debugging; each of those ports can be moved in `env/local.env` (operator
README, "Ports").

### Profiles

| | `hardened` (default) | `dev` |
|---|---|---|
| Chosen by | the default, and an existing stack with no recorded profile | `STANDALONE_PROFILE=dev`, `./standalone.sh up --dev`, or the quickstart on a new stack; recorded in `env/.state` |
| Authentication | strict (JWT) | strict (JWT) |
| ZeroMQ authentication | `session` (CURVE keys minted at login) | `none`, loopback only |
| Step executor | sibling podman containers | in-process |
| Code | the image's | your checkout, mounted (edit, then `./standalone.sh restart umrs`) |

Both profiles require a JWT for every `/gw/*` call. Dev relaxes the ZeroMQ
credential, never the authentication gate.

### Bring the stack up

```bash
# once
echo 'STANDALONE_MODEL_HOST=native' > env/local.env   # macOS: run the model on the host (Metal)
ollama pull llama3.2:3b                               # the model the stack adopts
./standalone.sh preflight
./standalone.sh bootstrap        # secrets, IAM, migrations, bucket; the text embedder is in the image

# every session
./standalone.sh up --dev         # or plain `up`: the recorded profile, else hardened
./standalone.sh doctor           # health probes
```

`bootstrap` writes generated secrets to `env/.env.secrets`, which is gitignored.
Among them is `UMRS_SECRET_KEY`, which signs knowledge bundles. Never commit that
file or copy it to another machine.

### Register the stack as an r4 source

```bash
./standalone.sh register [user] [project]     # defaults: scientist, default
```

`register` mints a 30-day token inside the `umrs` container and pipes it
into `r4 source add standalone … --api-key-stdin`, with the stack's model as
the source's default model and its project. It never runs `r4 source use`
or `r4 auth login`, and stops with an error if your active source changed.
It then creates the project on the stack (`POST /gw/projects` with a
one-day token on stdin; a project that exists is left as it is) and prints
the project's data class.
`source add` also writes a matching `umrs` connection profile,
`~/.config/umrs/connections/standalone.json`, in `direct` mode with no SSH
tunnel. It does not make that profile the `umrs` CLI's current one: name
it with `umrs --profile standalone …`.

The older manual login is still available: `./standalone.sh token` prints the
`r4 source add`, `r4 source use` and `r4 auth login … --api-key <JWT>`
commands with the token in them. Those commands **switch your active source**
and put the token on a command line. Use them only in a config of their own
(`export XDG_CONFIG_HOME=$HOME/.config-standalone` in that shell).

- **The model matters.** r4's built-in default is a cluster model that does
  not exist here. `register` sets the stack's model on the source.
- `http://` is allowed for `localhost` only. Any other host needs `https://` for
  a credentialed source, or `--allow-insecure-http`.

### Verify

```bash
r4 source list                                         # "standalone" with a ✓ in AUTH
R4_SOURCE=standalone r4 endpoint list                  # standalone-chat  ready  (the adopted model)
R4_SOURCE=standalone r4 chat "Reply with OK"
R4_SOURCE=standalone r4 knowledge graphs -p default
umrs --profile standalone info                         # profile standalone, mode direct, zmq 127.0.0.1:5555
```

## Using it

Everything in Part II works against the stand-alone source, within the limits
below:
- chat and the TUI;
- projects;
- knowledge upload, search and query;
- research loops;
- team plans;
- MCP through Hatch, with the extra `R4_HATCH_BIND_HOST` / `R4_HATCH_ADVERTISE_HOST`
  settings in the operator README.

The local project `default` is the one `register` and `token` use.

### Data class on the stack

*Updated 2026-10-16.*

A project's data class decides which model providers may receive its data
([Chapter 5](05-projects-models.md#data-class-what-may-leave-your-machine)):

- `sensitive`: the stack's own models, models on your machine, and the
  providers on the institutional provider list only;
- `non_sensitive`: public and commercial providers too.

On a stand-alone stack the packaged list applies (RIKEN's Rikyu) and
cannot be changed yet. The stack does not read a list of your own, so a
provider your institution runs elsewhere counts as public here.

On the stack, new projects start as `non_sensitive`: it runs on your
machine, for you alone. A shared deployment starts them as `sensitive`.
`register` creates the stack's project through the stack's gateway, as any
client would, so the project exists with that class from the first run. A
project that already exists keeps the class it has; registering again never
changes it. `register` then prints the project's class, and for a
`non_sensitive` project the command that narrows it:

```bash
R4_SOURCE=standalone r4 project data-class get default
R4_SOURCE=standalone r4 project data-class set default sensitive   # narrow: no confirmation
```

Widening back to `non_sensitive` needs the typed confirmation
(`--confirm 'default non_sensitive'`). Only the project's owner (the user
it was first registered for) or an administrator can change its class.
`register` names the owner when it is another user.

## Limits (2026-10-15)

- **Model backends:** only `local` (ollama, adopted as an endpoint registry row)
  is wired. The `hosted` and `remote` backends are not.
- **Modalities:** the embedder is text-only. Images, video and audio fail their
  embed step by name.
- **`r4 knowledge health`** reports chunks that came from artifact ingestion as
  `ORPHANED`. Its checker keys chunks by document id, so this is a false alarm.
  `./standalone.sh orphan-repair` (a dry run unless `--apply`) is the real
  orphan count.
- **Speed:** on a laptop a 3B model takes seconds per chat turn, but minutes per
  agentic turn with many tool calls. A model in the container runs on the CPU
  unless the GPU overlay applies. Quickstart sets `OLLAMA_NUM_PARALLEL=2` for
  the container ollama when the memory allows it (operator README, "Parallel
  slots").
- **Images are built on your machine**, which needs about 30 GB free the
  first time. Prebuilt images are not published yet.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No active endpoint for '<model>'` | the source has no default model, or a manual login without `--model` | `./r4-local register` (sets the stack's model), or `--model <STANDALONE_CHAT_MODEL>` |
| `503` from `/gw/*` right after `restart umrs` | NGINX still holds the old upstream | `./standalone.sh restart umrs` already refreshes the gateway; after a manual container restart, reload NGINX in the gateway container |
| `401` on every call | token expired (30 days) or minted against another stack | `./r4-local register` |
| `register` fails with an argument error | the `r4` in `.venv` predates `source add --default-model/--project/--replace` | update the checkout, then `uv sync --all-packages --extra client --inexact` |
| `r4 project data-class …` answers `project default not found` | the stack's project was never created: a stack set up with `token`, or registered before 2026-10-16 | `./r4-local register` creates it |
| `creating project 'default' on the stack failed` | the gateway or umrs did not answer `/gw/projects` | `./r4-local doctor`, then `./r4-local logs gateway-nginx umrs` |
| `dev port 127.0.0.1:5432 … is held by …` | another program uses a debugging port | `./r4-local up` moves it; by hand, the printed `STANDALONE_…_PORT=` line in `env/local.env` |
| `your ollama listens on loopback only` | `STANDALONE_MODEL_HOST=native` on Linux with the default ollama unit | the printed `OLLAMA_HOST=0.0.0.0:11434` fix, or `STANDALONE_MODEL_HOST=container` |
| `serves <model> with a 4096-token window` | a native ollama without `OLLAMA_CONTEXT_LENGTH` | the printed fix (`launchctl setenv …` on macOS, `systemctl edit ollama` on Linux) |
| `umrs info` shows another profile, or `ssh_tunnel` | `register` leaves the `umrs` CLI's current profile as it was | `umrs --profile standalone info` (or `r4 source use standalone` to make the stack your default) |
| `docker is recorded for this stack (env/.state), but its daemon does not answer` | Docker Desktop or dockerd not started | start it, then `./r4-local up` |
| `Docker Desktop is installed but not running, and podman has no machine yet` | macOS, first run, Docker Desktop (its app bundle) and podman both installed | start Docker Desktop, then `./r4-local up`; or `STANDALONE_ENGINE=podman ./r4-local up` to make the podman machine |
| `'podman compose' finds no compose provider` | podman without Docker Compose v2 | the printed install line (`brew install docker-compose` on macOS) |
| `up` refuses: model not staged | the configured `STANDALONE_CHAT_MODEL` is not pulled | `ollama pull <model>` (native) or `./standalone.sh model pull <model>` |

## Stop, reset

```bash
./r4-local down                                  # stop; data kept
deploy/standalone/standalone.sh reset --yes      # DESTROY all volumes (graphs, vectors, buckets, embedder choice)
r4 source remove standalone                      # forget the source on the client
```
