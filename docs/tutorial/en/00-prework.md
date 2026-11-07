# Pre-work — set up your laptop before the day

Dated 2026-10-29 · R4 + UMRS tutorial · EN ([日本語](../ja/00-prework.md))

Please do this **before** the event: it takes 30–60 minutes, most of it
downloads. On the day there is only a 5-minute check. If something fails,
send us the output of the checks in steps 4–5 — do not wait for the day.

## 1. Will my machine work?

| | Supported (tested) | Best effort (not yet tested) | Not supported |
|---|---|---|---|
| OS | Linux x86_64, macOS | Windows 11 with WSL2 | Windows without WSL2 |
| Memory | 16 GB RAM or more | — | under 16 GB |
| Disk | 15 GB free (30 GB if images are built locally) | — | less |
| Containers | Docker with Compose v2, or Podman ≥ 4.7 (`podman compose`) | — | the Python `podman-compose` |
| GPU | not needed | an NVIDIA GPU speeds up the model | — |

Also needed: `git`, `python3`, `curl`, `nc`, `openssl`, and free ports
8080 and 5555–5563. The stack picks a free model for your memory:
`llama3.2:3b` under 12 GB, `qwen2.5:7b` for 12–24 GB, `qwen2.5:14b` with a
≥ 12 GB GPU or ≥ 24 GB Apple unified memory, `qwen3:30b` from 48 GB.
On a laptop CPU the model is slow but works; plan for answers that take
tens of seconds.

## 2. Get the tutorial repository

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
git submodule update --init umrs     # if you cloned without --recurse-submodules
```

## 3. Start the local stack (one command)

```bash
./r4-local up
```

This checks your machine (memory, disk, ports, tools), chooses and
downloads the model, starts the local UMRS (database, object store, model
server) in containers, installs the `r4` client into `.venv/` (it needs
[`uv`](https://docs.astral.sh/uv/) and prints how to install it if
missing), registers the stack as the `r4` source `standalone`, and ends
with a smoke test: one chat, one document upload, one query.
`./r4-local up --yes` answers the questions with the defaults.

The tutorial is driven from the console (TUI), which needs
[`bun`](https://bun.sh) or the release binary (`r4 tui install`). Without
either, `r4` falls back to a simpler line interface; please install one of
them before the day.

## 4. Check the stack, then open the console

In a terminal:

```bash
./r4-local status
./r4-local doctor
source .venv/bin/activate
r4
```

From here on the whole tutorial happens **inside the console** (the R4
TUI that `r4` opens). Lines starting with `/` are console commands; lines
starting with `!` run a shell command in your workspace from the console
(its output is also shown to the model). In the console:

```text
/source                 # pick "standalone"
/health                 # gateway, endpoints, platform check: all green?
/models                 # the chat model the quickstart downloaded
```

Then type a chat message: *Say hello in one sentence.*

## 5. Three extra Python packages for the labs

The agent runs Python on your machine, so the libraries it uses must be in
that environment. Still in the console:

```text
!uv pip install --python .venv/bin/python matplotlib pandas numba
!python -c "import numpy, matplotlib, pandas, numba, pytest; print('labs ok')"
```

`matplotlib` and `pandas` are for the plots. `numba` is for one way of
making the travel-time solver fast in Lab 1.3; the NumPy way works without
it, so if `numba` will not install on your machine, carry on and tell us.

All green in `/health`, a one-sentence answer and `labs ok`? You are ready.
Leave the console with `/quit`. Stop the stack until the day with
`./r4-local down` (your data stays); `./r4-local up` starts it again.

*Outside the console* (fallback): `r4 source use standalone`, `r4 doctor`
and `r4 chat "…"` do the same checks from a plain terminal.

## 6. Where your data goes

Everything above runs on your laptop: files and results in a local object
store (SeaweedFS), document vectors in a local Postgres + pgvector, the
model in a local Ollama. Nothing is sent to an external AI service. The
tutorial covers when and how you might choose otherwise.

One lab goes out to the internet on purpose: the agent downloads the
observed arrival times of a real earthquake from the public ISC Bulletin
(International Seismological Centre (2026), On-line Bulletin,
https://doi.org/10.31905/D808B830). Only the request for that event leaves
your laptop. Without a network the lab uses a synthetic event instead.

## Help

[support channel / e-mail — to be announced]. Please include the output
of `./r4-local doctor` and of `/health` in the console (or `r4 doctor`).
