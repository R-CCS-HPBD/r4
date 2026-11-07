# Part 1 labs — agentic work (labs ~65 min of the ~2 h Part 1)

Dated 2026-10-29 · R4 + UMRS tutorial · EN ([日本語](../ja/01-part1-labs.md)) ·
Part 1 section of `slides/tutorial-r4-umrs.en.pptx` (labs: slides 32–43) ·
background reading:
[04-background-model-agent-harness.md](04-background-model-agent-harness.md)

Every step here was checked against the code on 2026-10-29; the timings
and residuals were measured on a workstation (full answer key:
`docs/tutorial/data/README.md`). **Everything happens inside the R4
console.** Open it once, from the repository folder:

```bash
cd r4
source .venv/bin/activate
r4
```

## Console cheat sheet

| To… | In the console |
|---|---|
| check the connection | `/source` (pick `standalone`), `/health` |
| see settings, skills, MCP tools | **Ctrl+P** → "Open pane: Settings" (typing `/settings` does not open it; F7 is not bound) |
| choose the chat model | `/models` (picker); `/models default <name>` saves it |
| download another model | `/term`, then `deploy/standalone/standalone.sh model pull <name>` |
| see every tool call | **Ctrl+O** (verbose trace); **F9** Activity tray (Agent / Tools / Events) |
| answer a tool approval | **y** once · **a** for the rest of the session · **n** no (the agent is told you said no; the tool stays available); **a** is not offered for the always-ask tools; **Shift+Tab** cycles the mode (chat → plan → autopilot) |
| allow web access | `/web` (off / allowlist / public) |
| coding mode | `/code on`; run a script: `/code run <file> [--timeout N] -- <args>` (cwd = repository folder, default timeout 60 s) |
| a short shell command | `!<command>` — runs in the workspace; the output also goes to the model |
| long or interactive output | `/term` (a terminal pane); `/term share [N]` hands the last N lines to the agent |
| knowledge (RAG) | `/acquisition [off|extend <graph>]` (writes are open by default; this restricts them) · `/rag upload <file>` · `/rag docs` · `/rag <question>` · `/knowledge <query>` (bare `/knowledge`: status card) |
| tokens and context | `/cost` (turns, prompt, completion) · `/context` (totals) |
| skills, MCP | `/skills` (`/skills on <name>`) · `/mcp`, `/mcp tools` |
| saved permission rules | `/settings rules`; or edit `~/.config/r4/permissions.json` (Lab 1.3) |
| workflows (Part 2) | `/workflow …` · `/work` (Alt+W: graph) |
| keep or undo | `/export <file>.md` (bare `/export` previews) · `/retry` · `/rewind` (or Esc Esc on an empty prompt) · `/sessions export [id]` |
| leave / restart | `/quit`, then `r4` again in the same terminal |

A plain terminal is needed only for `./r4-local up/status/doctor` and to
relaunch the console with `R4_RAG_ROUTER=on r4` (Lab 1.4 C).

**The subject: seismic first arrivals.** A P wave is the first wave an
earthquake sends to a seismometer. You will download the observed P arrival
times of a real earthquake, make a 3-D travel-time solver fast with the
agent, and compare the solver's prediction with the observations.

**The event:** 2021-12-03, Kii Channel (off Wakayama), ISC event
**621611300** — mb 5.2, 16 km deep, about 70 km from Kobe, no casualties.
The picks come from the ISC Bulletin. Cite it wherever you use them:
**International Seismological Centre (2026), On-line Bulletin,
https://doi.org/10.31905/D808B830.** The repository holds **no copy** of
these data: you download them live in Lab 1.1. Without a network, use the
**synthetic** fallback `docs/tutorial/tasks/eikonal/data/synthetic-kii/`
(an invented event, 30 stations) and say that it is synthetic.

## Lab 1.1 — guided task (10 min, slides 32–33)

1. `/source` — pick `standalone`; `/health` — all green?
2. **Ctrl+P** → **Open pane: Settings**. Look at the connection
   (`localhost:8080`), the skills and the MCP tools.
3. `/models` — pick the chat model. The quickstart already downloaded one
   for your memory size. To get another: `/term`, then
   `deploy/standalone/standalone.sh model pull qwen2.5:7b` (it shows
   progress; do not use `!` for this — no timeout, and the output floods
   the model's context), then `/models default qwen2.5:7b`.
4. **Ctrl+O** — verbose transcript: every tool call is shown. **F9** opens
   the Activity tray.
5. `/web` — turn web access on (allowlist or public): the agent must reach
   `www.isc.ac.uk`.
6. Ask:

   > Download the P-wave picks for ISC event 621611300 (Kii Channel,
   > 2021-12-03) from the ISC Bulletin, keeping the first P per station
   > within 2.5°. Save the raw data with Python or curl, not with
   > web_fetch. Save event.json and p_picks.csv in
   > docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03. Then tell me
   > how many stations there are and which one is farthest. Cite the ISC
   > Bulletin.

   The agent asks before it runs a tool: **y** once, **a** for the rest of
   the session, **n** no — the agent is told
   you said no and can try another way (**Shift+Tab** cycles the mode:
   chat → plan → autopilot).

Expected: **25 stations; the farthest is JHT, about 268 km away.** Check it
yourself (the last rows are the farthest; `distance_deg` × 111.2 km):

```text
!tail -3 docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/p_picks.csv
```

The agent fetches with `python_exec` (urllib) or `shell_exec` (curl) from
the ISC FDSN event service
(`https://www.isc.ac.uk/fdsnws/event/1/query?eventid=621611300&includearrivals=true`),
or finds the reference downloader. If it gets stuck, run the reference
downloader yourself:

```text
!cd docs/tutorial/tasks/eikonal && python fetch_isc_picks.py --event 621611300 --out data/kii-channel-2021-12-03
```

No network? Use `docs/tutorial/tasks/eikonal/data/synthetic-kii/`
(synthetic: 30 stations, the farthest at 275 km).

Read the trace (**Ctrl+O**): which URL did it fetch, which tool did it run,
does the answer follow from what it saw? Each tool call is one round, and
each round is one model call. R4 marks what the agent downloads with python
or curl, and the files it saves, as web content: a later call that copies
from it asks you, for that call only (R4's taint fence, handbook 04, §11).
Whether the data are right is still yours to check.

## Lab 1.2 — your own task (10 min, slide 34)

> Using docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03, plot the
> P travel time (pick time minus origin time) against epicentral distance
> in km, with one marker per phase (Pg, Pb, Pn). Save it as
> travel_times.png and estimate the distance where Pn starts to arrive
> first.

(Pg: the direct wave in the upper crust · Pb: along the lower crust · Pn:
refracted just below the Moho.)

Expected: Pg and Pb up to 128 km, the first Pn at 132 km (station JKS) — a
crossover distance (where Pn starts to arrive first) of **about 130 km**. (Synthetic fallback: about 105 km, between
102 and 110 km.)

Then iterate: "Label the farthest station." · "Fit a straight line to the Pn
picks and print its slope as an apparent velocity." (The inverse of the
Pn slope is the uppermost-mantle velocity, about 8 km/s.)

The built-in `analysis` skill is loaded by words like *plot* and *pandas*;
its `python_exec` tool runs Python **on your machine, with your own permissions**:
R4's OS sandbox is off unless you turn it on (`/sandbox on`; ADR-101).
`ModuleNotFoundError: matplotlib` means pre-work step 5 was skipped.

*Stretch:* the knowledge pack's primer (`05-domain-primer-seismic-travel-times.md`)
gives the crossover distance for one layer over a half-space,
x = 2h √((v2 + v1) / (v2 − v1)). Ask the agent to compute it with
KRSO-1D v2's values (h = 28 km, v2 = 7.9 km/s, and v1 = 5.9 or 6.7 km/s)
and compare with your plot. Why do they differ? (The formula puts the
source at the surface — this one is 16 km deep — and has one crustal layer,
not two.)

## Lab 1.3 — agentic coding: make the 3-D solver fast (15 min, slides 35–36)

`docs/tutorial/tasks/eikonal/eikonal_slow.py` solves the eikonal equation
|∇T| = 1/v (first-arrival travel time T, velocity v) on a 3-D grid with the
fast sweeping method. It is correct but written in plain Python loops. The
tests and the benchmark are the **acceptance check**: you do not have to
read the agent's code to know whether it is right.

Switch on coding mode with `/code on`, then ask:

> Work in docs/tutorial/tasks/eikonal. eikonal_slow.py is correct but too
> slow for a regional 3-D grid. Write my_fast.py with the same `solve()`
> signature. It must pass `EIKONAL_IMPL=my_fast pytest test_eikonal.py`,
> and `python bench.py --impl my_fast --sizes 21 41 61` must show max |ΔT|
> below 1e-6. Explain what you changed and why it is faster.

Then run the acceptance check **yourself**:

```text
!cd docs/tutorial/tasks/eikonal && EIKONAL_IMPL=my_fast python -m pytest -q test_eikonal.py
!cd docs/tutorial/tasks/eikonal && python bench.py --impl my_fast --sizes 21 41
```

Tired of approving `pytest` every time? One line in your **user** rule
file `~/.config/r4/permissions.json` pre-approves it (optional):

```json
{"allow": ["shell_exec(prefix:pytest)"]}
```

A checked-in `.r4/permissions.json` in a repository can only ask or deny,
never allow (handbook 04, §11).

(`/code test` runs the whole project's tests, not one file — do not use it
here.) Measured on a workstation (one solve of the real event's grid, 2 km
spacing, 31 × 174 × 234 = 1.26 M nodes):

| Solver | One solve | Speed-up |
|---|---|---|
| `eikonal_slow.py` (pure Python) | 14.6 s | — |
| NumPy, diagonal planes | 1.40 s | ≈ 10× |
| Numba (same loops, compiled) | 0.24 s | ≈ 60× |

On a 1 km grid (9.9 M nodes): pure Python 117 s, Numba 1.5 s. Try the 1 km
grid in the terminal pane — it runs long — and hand the output to the
agent:

```text
/term
cd docs/tutorial/tasks/eikonal && python japan_event.py --impl my_fast --h 1.0
/term share
```

Your laptop will be slower than the workstation, but the ratios are
similar.

What to look for in the agent's explanation: Numba keeps the algorithm and
the update order, so the answer is identical. NumPy needs a different
order — all nodes on a plane i + j + k = const are independent within a
sweep — which is also how the method runs on a GPU. If the agent changes
the update order in another way, the max |ΔT| line is what catches it.

## Lab 1.4 — your own inference environment (15 min, D–E as a stretch; slides 37–40)

The knowledge pack (`docs/tutorial/data/knowledge-pack/en/`, Japanese in
`…/ja/`) describes an **invented observatory**, the Kinki Regional Seismic
Observatory (KRSO): its reference model KRSO-1D v2, its pick-quality rules
PQ-1…PQ-7, its review notes and its report format RR-2 — plus one textbook
primer (file 05). No model can know files 01–04, so the effect of RAG is
easy to see.

The question used throughout:

> Which reference model, Moho depth and mantle velocity do we use for a Kii
> Channel event, and when is a pick rejected?

Correct answer (only in the pack): **KRSO-1D v2** (rule RM-1), **Moho at
28 km**, **mantle 7.9 km/s**; a pick is rejected when its **|residual|
exceeds 2.5 s** (rule PQ-3). Sources: `01-reference-model-krso-1d-v2.md`
and `02-pick-quality-protocol.md`.

### A — ask first, with no knowledge

Do this **before** step B, so nothing is in the index yet. Ask the question
in chat, then type `/cost` (turns, prompt and completion tokens) and
`/context`. Write the result in the table under C.

Optional, the model alone (no retrieval, no tools) — do it after the chat
arm, because `!` output also reaches the model:

```text
!r4 chat "Which reference model, Moho depth and mantle velocity do we use for a Kii Channel event, and when is a pick rejected?"
```

### B — build the RAG

Knowledge writes are open by default; each `knowledge_ingest` call still asks
for approval. Ask the agent:

> Ingest the folder docs/tutorial/data/knowledge-pack/en into this
> project's knowledge.

(Its `knowledge_ingest` tool accepts a folder. By hand instead:
`/rag upload <file>` once per file — it does not take a folder.) Then:

```text
/rag docs               # the five documents, once indexed
/knowledge              # status card
```

Each document is split into passages, embedded with the stack's
`e5-base-v2`, stored in the local pgvector, and the local chat model
extracts a small knowledge graph. On a CPU laptop this takes minutes —
start it first and watch the Activity tray (**F9**).

### C — effect check: off vs on

```text
/rag Which reference model, Moho depth and mantle velocity do we use for a Kii Channel event, and when is a pick rejected?
/knowledge Moho depth reference model Kii Channel
```

`/rag` gives the RAG answer with its sources; `/knowledge` shows the
retrieved snippets. Then restart the console with retrieval injected into
most turns: `/quit`, and in the same terminal

```bash
R4_RAG_ROUTER=on r4
```

Ask the question in chat again, then `/cost` and `/context`.

| Arm | How | Correct? | Turns | Tokens | Cites a source? |
|---|---|---|---|---|---|
| Off — agent | chat before B, `/cost` | | | | |
| Off — model alone (optional) | `!r4 chat "…"` after the row above | | — | — | |
| On — RAG answer | `/rag <question>` | | — | — | |
| On — agent | `/quit`, `R4_RAG_ROUTER=on r4`, chat, `/cost` | | | | |

Notes: `R4_RAG_ROUTER` (`off` by default; there is no switch inside the
console) controls whether retrieved passages are put into the turn before
the model answers. With it off, the agent can still search on its own (its
`search_docs` tool) — that is why the "off" arm is taken **before** the
documents are ingested. Open the cited source and find the sentence: a RAG
answer is only as good as the passage it cites. The question has two
halves (model, rejection rule) that live in two files; check that the
answer got both.

### D — an instruction rule and a skill

Instructions: walking up from the folder you start it in, `r4` loads each
of `R4.md`, `AGENTS.md`, `agent.md`, `AGENT.md`, `CLAUDE.md` and
`OPENCODE.md` it finds (the nearest copy of each name), at most 4,000
characters per file and 8,000 in total, and reads them again at every
turn. **The repository folder already has its own `R4.md`, `AGENTS.md` and
`CLAUDE.md`** (notes for the platform's developers, about 6,700 characters
together), so they are already in every prompt. Add your rule to that
`R4.md`; do not let the agent overwrite it. Ask the agent:

> Add this one rule at the end of R4.md in the repository folder (create
> the file if it is missing): "Always name the reference model and its
> version, and the pick source with its citation."

Skill: copy the prepared skill into the project's skill folder.

```text
!mkdir -p .r4/skills && cp -r docs/tutorial/data/skills/residual-report .r4/skills/
```

Restart the console (`/quit`, then `r4`): skills are scanned only when a
session starts (the `R4.md` rule already applies from the next turn). `/skills`
lists the skill (`/skills on residual-report` if it is off; no console
command shows the loaded instructions). Ask: *"Write the residual report for
the Kii Channel event in
docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03."*

Check the report: does it name KRSO-1D v2, cite the ISC Bulletin, and apply
the PQ rules? A good check: station **JKS** is labelled Pn at 132 km, but
rule PQ-5 accepts Pn only beyond 140 km, so the report should flag JKS for
review. There is no custom slash-command feature; a skill is how you
package a repeatable task.

The skill is switched on by keywords in your message (whole words, listed
as `x-r4-keywords` in its `SKILL.md`: `residual`, `travel time`, `pick`,
`Pn`, `Moho` …). It also has Japanese keywords — 残差, 走時, モホ — so a
Japanese request such as 「自分の読み取りの残差レポートを書いて」 activates it
too.

### E — register one MCP tool

The stand-alone recipe (verified 2026-10-09, `deploy/standalone/README.md`
§MCP). The Hatch runtime runs on your machine; UMRS (in a container) must be
able to reach it. Open `/term` and run:

```bash
export HATCH_REGISTRY_URL="$PWD/mcp-servers/hatch-registry"
export R4_HATCH_BIND_HOST=0.0.0.0
export R4_HATCH_ADVERTISE_HOST=host.containers.internal
r4 mcp hatch install platform-tools
r4 mcp hatch connect platform-tools
r4 mcp list -t
```

Back in the console, `/mcp` (overlay) or `/mcp tools` lists the tools.
Listening beyond loopback is safe here: the runtime requires a random
per-runtime token on every call, which it registers with UMRS. (Hatch is
R4's mechanism that packages and starts MCP servers.) The agent
sees the tools as `mcp.<server>.<tool>`. A local `mcp.json` file is **not**
visible to the agent — registration through UMRS is the path
(`/mcp register` is not implemented yet).

Now let the agent call one. Restart the console (`/quit`, then `r4`): a
session loads its MCP tools when it starts. Ask: *"Which models are
available on the platform? Use the platform-tools MCP server."* The trace
(**Ctrl+O**) should show an `mcp.<server>.list_models` call. Its result
counts as MCP output for the taint fence: a later call that copies from it
asks you. *(Agent calls of MCP tools returned an error until a fix on
2026-10-30.)*

## Lab 1.5 — compare with the real earthquake (7 min, slide 41)

Your fast solver now predicts the arrival time at every station. Run it
with two velocity models — the global **ak135** (which the ISC also uses)
and the observatory's **KRSO-1D v2** from the knowledge pack:

```text
/code run docs/tutorial/tasks/eikonal/japan_event.py -- --impl my_fast --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 --model ak135
/code run docs/tutorial/tasks/eikonal/japan_event.py -- --impl my_fast --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 --model krso
```

`/code run` starts in the repository folder, so paths are repo-relative;
the script's own folder is on the import path, so `--impl my_fast` finds
your `my_fast.py`. The default limit is 60 s: with a slow solver add
`--timeout 300` before `--`. Without `--impl` the slow reference is used:
same answer, slower.

Each run writes `residuals.csv` and `comparison.png` into the data folder
(the second run overwrites the first — ask the agent to rename them in
between if you want both). Residual = observed − predicted; negative means
the wave came early.

Expected (2 km grid, 25 stations):

| Model | Mean residual | RMS |
|---|---|---|
| ak135 | −1.12 s | 1.32 s |
| KRSO-1D v2 | +0.04 s | 0.46 s |

With ak135 the script also prints how your residuals compare with the
ISC's own ak135 residuals (column `isc_residual_s`): they track station by
station, with a mean offset of −0.36 s that halves to −0.18 s on a 1 km grid
(`--h 1.0`, the Lab 1.3 terminal run). That is a first-order scheme
converging with grid spacing — your solver validated against an
independent one, on real data.

Synthetic fallback: `--data docs/tutorial/tasks/eikonal/data/synthetic-kii`
gives ak135 −1.21 s / RMS 1.38 s; with `--model krso --h 1.0` (the model
that made the data) −0.13 s / 0.17 s — not zero even with the true model.
There is no ISC column, so the comparison line is not printed.

Then ask the agent: *"Explain residuals.csv. Which phase is early against
ak135, by how much, and what does that suggest about the crust?"* (Pn beyond
~130 km arrives about 1 s early: a thinner crust than ak135's 35 km.) The
invented KRSO-1D v2 happens to fit this real event almost as well as the
best of 162 searched models (RMS 0.44 s) — but one event with 25 picks
cannot pin down the Moho: depth trades off against velocity (Part 2).

### Keep the session (slide 42)

```text
/export kii-session.md         # the whole session as Markdown (bare /export previews)
/retry                         # re-send the last prompt
/rewind                        # step back (or Esc Esc on an empty prompt)
/sessions export               # writes r4-session-<id8>.md
```

*Outside the console* (fallback): `r4 -p "…"` runs one prompt headless,
prints the final answer and saves the session.

Answers can vary between runs, so keep the acceptance check with the
session: same data + same prompt + same model lets you rerun it, and the
export records what the agent did, step by step.

**You keep:** `kii-session.md`, `my_fast.py`, `travel_times.png`,
`comparison.png` and `residuals.csv`, and your knowledge pack (the RAG
index, `R4.md`, the `residual-report` skill, the MCP tool). The downloaded
ISC data stay on your laptop (git ignores them); keep the citation with
anything you share.

## Good practice — four parts of a science prompt (slide 42)

Data · goal · constraints · **acceptance check**. Example: *"Using the ISC
picks in docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 (cite the
ISC Bulletin), compute observed − predicted P residuals against ak135 with
my_fast on a 2 km grid; do not edit the picks. Acceptance: our residuals
track the isc_residual_s column station by station, mean difference within
0.5 s."*
