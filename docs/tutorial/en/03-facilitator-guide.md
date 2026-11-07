# Facilitator guide — Opening, Part 1, Part 2

Dated 2026-10-29 · R4 + UMRS tutorial · EN ([日本語](../ja/03-facilitator-guide.md))

Timings, preparation, expected answers and the failures you will see.
Speaker notes in the deck carry the per-slide talk track. The subject thread
is seismic first arrivals (replaced the typhoon case on 2026-10-27); full
answer key: `docs/tutorial/data/README.md`; task notes:
`docs/tutorial/tasks/eikonal/README.md`.

**The labs are driven from the console** (the R4 TUI started with `r4`):
native slash command where one exists, otherwise a chat request to the
agent, otherwise the in-console shell (`!<command>` for short output,
`/term` + `/term share` for long or interactive output). A plain terminal is
used only for `./r4-local up/status/doctor` and for relaunching with
`R4_RAG_ROUTER=on r4`. Attendees have a cheat sheet at the top of
`01-part1-labs.md`.

## Before the event

- [ ] Send `00-prework.md` (the attendee environment announcement) about
      two weeks ahead, with the bracketed fields filled.
- [ ] Rehearse the whole of Parts 1–2 **in the console** on a **fresh**
      machine of each supported OS class (plan §7: two rehearsals, one with
      a fresh user). Check that the console (bun or `r4 tui install`) starts
      on each.
- [ ] **Test the live ISC download from the venue network** (same Wi-Fi,
      same day of week if possible). In the console, after `/web`:
      `!cd docs/tutorial/tasks/eikonal && python fetch_isc_picks.py --event 621611300 --out data/kii-channel-2021-12-03`
      must print 25 stations within 2.5°. If the network blocks or throttles
      `www.isc.ac.uk`, plan for the synthetic fallback (`data/synthetic-kii`)
      and say so at the start of Lab 1.1.
- [ ] Record every demo (guided task, Lab 1.3, Lab 1.4 A–E, Lab 1.5, Lab 2.2
      run) as a fallback for slow venue networks or slow laptop models.
- [ ] Generate the failure-clinic bundles:
      `!python docs/tutorial/workflows/make_fault_set.py` → `docs/tutorial/workflows/faults/f1…f4`
      (workflow names `seismic-f1-bad-entrypoint` …).
- [ ] Choose the shared skill repository for Lab 2.4
      (`!r4 skill github install <owner>/<repo>/skills/<name>`) and test the
      install.
- [ ] Fill the event line on slide 1, the support channel on the closing
      slide (53), and insert the official FugakuNext slides (official
      RIKEN / R-CCS material only). **Inserting
      slides shifts every slide number** in the lecture cut (slide 55), in the
      Timings table below and in the speaker notes ("slides 12–31",
      "slide 42"): rebuild the deck and copy the new numbers into the table.
- [ ] Offline mirror (USB / local registry) of the container images, the
      model, `docs/tutorial/`, and Python wheels for **matplotlib, pandas and
      numba** (numba is the one most likely to fail on a venue network or an
      unusual platform). The mirror carries the **synthetic** fallback only:
      no ISC data — the repository keeps no copy (maintainer decision
      2026-10-27), so neither does the mirror.
- [ ] Rehearse Lab 1.4 E's agent call of an MCP tool on the current code
      (it returned an error until the fix of 2026-10-30).
- [ ] On the < 12 GB (`llama3.2:3b`) and ≥ 48 GB (`qwen3:30b`) tiers, check
      `/context` in rehearsal: R4 assumes a 131,072-token window for these
      names while Ollama serves 32,768 (`r4/src/r4/model_context.py:73,95`;
      `deploy/standalone/env/base.env:36`). Until this is fixed in code, keep
      sessions short (`/clear`).
- [ ] Lab 1.4 D: the repository folder already has a tracked `R4.md` (a
      developer briefing), `AGENTS.md` and `CLAUDE.md`, and `r4` loads all
      three (about 6.7k of the 8k-character budget). Attendees append their
      rule to `R4.md`; check in rehearsal that the agent does not overwrite
      it (alternative: a lab subfolder or a tutorial-specific `R4.md`).

## Timings

| Block | Full course (Part 1: minutes from its start) | Hyogo 1.5 h lecture (2026-12-03), minutes |
|---|---|---|
| Opening (slides 1–7) | 30 min | 0–10 (slides 1–6) |
| Part 1: agentic work; model, agent, harness (slides 8–9) | 0–8 | — (folded into the next block) |
| Part 1: three ways to run an agent + boundary check (slide 10) | 8–15 | — |
| Part 1: the whole picture — the harness map (slide 11) | 15–20 | 10–45 (slides 9, 11–31) |
| Part 1: under the hood (model, agent loop, harness, skills, MCP, knowledge; slides 12–31) | 20–55 | (in 10–45) |
| Lab 1.1 guided task (slides 32–33) | 55–65 | 45–60 live demo, slides 32–34 (download, trace, travel-time curve) |
| Lab 1.2 own task (slide 34) | 65–75 | (in 45–60) |
| Lab 1.3 agentic coding (slides 35–36) | 75–90 | — (show the measured numbers, slides 35–36) |
| Lab 1.4 inference environment, A–C (D–E stretch; slides 37–40) | 90–105 | 60–80 live demo, slides 37–39 + Lab 1.5 slide 41 |
| Lab 1.5 compare (slide 41) | 105–112 | (in 60–80) |
| Good practice 112–116 + quiz 116–120 (slides 42–43) | 112–120 | 80–85 (slides 42–43) |
| Q&A / bridge to Part 2 (slide 44) | — | 85–90 (slide 44) |
| Part 2 (slides 44–51) | 3 h | — |

The Hyogo minutes and slide numbers are also on the deck's last slide
(55, the 1.5 h lecture cut); change both together. Skipped in the lecture
cut: slides 7–8, 10, 40 and 45–54.

Part 1 in the full course is now **about 2 h** (it was 1.5 h before the
"under the hood" block). Slack goes, in order, to install stragglers, Lab
1.4 D–E, re-running the RAG on/off check, then the optional slide (54:
when data leaves — external models and remote tool servers). Companion
reading for the block: `04-background-model-agent-harness.md`; each of its
mechanisms is tagged as a general agent-harness pattern or R4-specific, as
on the slides.

Part 2 has a 10-minute break at minutes 70–80 but no break slide: announce
it from slide 48 (Lab 2.2), whose last line says so.

In the Hyogo demo, run the download first (it can be slow); while the
knowledge pack indexes (minutes on CPU), show Lab 1.5 with a solver that is
already fast:
`/code run docs/tutorial/tasks/eikonal/japan_event.py -- --impl answer_key.eikonal_numba --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03`.

## Expected answers

- **Lab 1.1:** 25 stations within 2.5°; farthest **JHT, ~268 km**. Picks:
  Pg and Pb up to 128 km, Pn from 132 km (JKS). Synthetic: 30 stations,
  farthest 275 km.
- **Lab 1.2:** crossover **~130 km** (last Pb 128 km, first Pn 132 km).
  Synthetic: ~102–110 km. Stretch, our arithmetic: the one-layer formula
  with h = 28 km, v2 = 7.9 km/s gives ≈ 195 km for v1 = 6.7 km/s and
  ≈ 147 km for v1 = 5.9 km/s — longer than observed, because the formula
  puts the source at the surface (this one is 16 km deep, closer to the
  Moho) and has one crustal layer. The discussion matters more than the
  number.
- **Lab 1.3:** the acceptance commands pass; max |ΔT| = 0 for both answer
  keys (`answer_key/eikonal_numba.py`, `answer_key/eikonal_numpy_diag.py`).
  Workstation timings on the 1.26 M-node grid: pure Python 14.6 s, NumPy
  1.40 s (≈ 10×), Numba 0.24 s (≈ 60×); 1 km grid 117 s vs 1.5 s (≈ 75×);
  `bench.py --sizes 21 41 61`: Numba 56–62×.
- **Lab 1.4 question:** **KRSO-1D v2** (RM-1), **Moho 28 km**, **mantle
  7.9 km/s**, reject when **|residual| > 2.5 s** (PQ-3); files 01 and 02.
  Without the pack a model can only guess a public model and a textbook
  Moho. **Lab 1.4 D:** the report should name KRSO-1D v2, cite the ISC, and
  flag **JKS** (Pn at 132 km, PQ-5 requires > 140 km) for review. With
  KRSO-1D v2 no pick exceeds the PQ-3 limit (largest |residual| ≈ 1.2 s, JKN2,
  in the 2026-10-27 run); 25 accepted stations > 8 (PQ-7).
- **Lab 1.5 (2 km grid):** ak135 mean **−1.12 s**, RMS **1.32 s**;
  KRSO-1D v2 **+0.04 s / 0.46 s**. Ours − ISC's ak135 residual: −0.36 s at
  2 km, −0.18 s at 1 km (ak135 at 1 km: −0.94 s / 1.14 s). Synthetic:
  ak135 −1.21 s / 1.38 s (2 km); KRSO-1D v2 at 1 km −0.13 s / 0.17 s.
- **Part 2 report (synthetic, 4 km grid, NumPy, 54 s locally):** ak135
  RMS 1.48 s (mean −1.30 s), best 0.21 s (mean −0.01 s); Moho 26 and 28 km
  tie. Real picks at 2 km for reference: best of 162 models 5.7 / 6.5 /
  Moho 26 / 7.9 km/s, RMS 0.44 s; synthetic best 5.9 / 6.5 / Moho 26 / 7.9,
  0.14 s.
- **Quiz (slide 43):** Q1 no — that knowledge is in your documents, which
  the harness (RAG) puts in front of the model; Q2 only as right as the
  tests — also check the benchmark's max |ΔT| and one real case; Q3 the
  model is too slow along these paths, or the location / origin time is
  off.

Teaching points to land: ak135 is about 1 s too slow along these paths
(Pn); Moho depth trades off against lower-crust velocity (several models
within 0.02 s); best values at the edge of a search range mean "extend the
range", not "found it"; even with the true model the residual is not zero
(first-order scheme + picking noise); origin time and hypocentre were held
fixed at the ISC solution.

## Failure clinic key

Attendees run each bundle with `/workflow upload docs/tutorial/workflows/faults/<fault>`
and `/workflow run seismic-<fault> --param bucket=<b>`, then read
`/workflow results <id>` and `/job <id>`.

| Bundle | Symptom | Fix |
|---|---|---|
| f1-bad-entrypoint | `search_low` fails before running: `module 'steps' has no attribute 'ModelSearchSteps'` | `steps:ModelSearchStep` |
| f2-missing-key | `search_high` cannot read `picks.csv` (no such key) | `picks_key: p_picks.csv` |
| f3-timeout | `search_low` exceeds its 5 s budget (`debug_delay_seconds: 30`) | remove the delay or raise `timeout_seconds` |
| f4-lost-parents | `rank` starts at once, finds no scores (`StopIteration` at `next(...)`) | `depends_on: ["search_low", "search_high"]` |

All four pass `r4 workflow lint` — make that point. f1, f2 and f4 were
reproduced with `run_local.py` on 2026-10-27 (locally f2 fails earlier,
when `run_local.py` stages the missing file); f3's timeout is enforced by
the server only, so **check f3 on the stack in rehearsal**.

## Console mapping and its gaps (checked against the code, 2026-10-27; approvals and instructions rows 2026-10-29)

| Task | In the console | Gap — what to do |
|---|---|---|
| Connection check | `/source` (pick `standalone`), `/health` | no `/doctor` in the console: `./r4-local doctor` outside |
| Settings, skills, MCP at a glance | Ctrl+P → "Open pane: Settings" | typing `/settings` does not open the pane; F7 is not bound |
| Chat model | `/models`; `/models default <name>` saves | no download wizard: `/term` → `deploy/standalone/standalone.sh model pull NAME`; never `!` (no timeout, floods the context) |
| Tool trace | Ctrl+O; F9 Activity tray | — |
| Approvals | y once / a this session / n no; Shift+Tab cycles the mode (chat → plan → autopilot); `/settings rules` | `r4 -p … --yolo` (headless) does not auto-approve tools; a durable allow goes in `~/.config/r4/permissions.json` (a repository's `.r4/permissions.json` can only ask or deny) |
| ISC download | `/web` (allowlist or public), then chat | `web_fetch` strips to text: tell the agent to save raw data with Python or curl. `/web` limits only `web_fetch` / `web_search`; the Python/curl download is not filtered by it (`r4/src/r4/companion/web_access.py:12`) |
| Coding task | `/code on`, then chat; acceptance with `!cd … && python -m pytest -q test_eikonal.py` | `/code test` runs the whole project root, not one file — do not use it |
| Run a script | `/code run <file> [--timeout N] -- <args>` (cwd = repository folder) | default timeout 60 s: add `--timeout 300` for slow solvers and `run_local.py`; `run_local.py` writes `./local-run` in the repository folder |
| Long output | `/term`, `/term share [N]` | `!` sends all output to the model — keep it for short output |
| Knowledge ingest | Ask the agent to ingest the folder (`knowledge_ingest`; writes are open by default, each call asks for approval) | `/rag upload` takes one file, not a folder |
| RAG on/off | off = chat before ingest; `/rag <question>`; `/knowledge <query>` | no in-console router toggle: `/quit`, then `R4_RAG_ROUTER=on r4`; with the router off the agent may still call `search_docs` — take the "off" arm before ingesting |
| Tokens | `/cost`, `/context` | `/usage` shows server-side usage, not the session |
| Instructions, skills | ask the agent to add the rule to the existing `R4.md`; `!mkdir -p .r4/skills && cp -r …`; restart (skills are scanned at session start; `R4.md` is re-read every turn); `/skills` | no console command shows loaded instructions; the repository's own `R4.md`, `AGENTS.md`, `CLAUDE.md` are always loaded; skills by URL not supported (`!r4 skill github install owner/repo/skills/name`); no custom slash commands — package the task as a skill |
| MCP | `/term` for the Hatch recipe; `/mcp`, `/mcp tools` | `/mcp register` is a stub; a local `mcp.json` is invisible to the agent; `localhost` URLs resolve inside the umrs container |
| Session | `/export file.md`, `/retry`, `/rewind` (Esc Esc), `/sessions export [id]` | no path argument for `/sessions export` (writes `r4-session-<id8>.md`) |
| Buckets, files | `!r4 bucket create default seismic`, `!r4 bucket list`, `!r4 files upload … --bucket …`, `!r4 files download …` | no bucket commands in the console; `/files upload` has no bucket option |
| Workflow lint | `!r4 workflow lint <dir>` | no console form |
| Workflow run | `/workflow upload <dir>`, `/workflow run <name> --param bucket=<b>`, `/work` (Alt+W graph), `/workflow status / results / list / show` | `--wait` is capped at 20 s — watch the Work pane instead |
| Workflow logs | `/workflow results <id>`, `/job <id>`, `/logs`, `/term` → `./r4-local logs umrs` | no `workflow logs` command |

## Other sharp edges

| Where | What happens | What to do |
|---|---|---|
| Knowledge upload | graph extraction by the local model: minutes on CPU | start Lab 1.4 B early; watch F9 and `/rag docs` |
| Japanese knowledge pack | the stack's embedder `e5-base-v2` is English-only: on the JA pack it ranks the PQ-3 rule section 15th of 17 (EN pack: 4th); `multilingual-e5-base` ranks it 3rd (`background/embedding_probe.json`) | check the cited passages; if the JA answer misses PQ-3, also ingest the EN pack or ask in English |
| Plotting, Numba | `python_exec` runs on the host with your rights (the OS sandbox is off by default; `/sandbox on` confines it) | pre-work installs matplotlib + pandas + numba into `.venv`; without numba the NumPy route still works |
| ISC data | real picks are downloaded live; the repository keeps no copy (`data/*` is gitignored; decision 2026-10-27) — only slide 6's two figures show them, as images with the citation (2026-11-05) | cite *International Seismological Centre (2026), On-line Bulletin, https://doi.org/10.31905/D808B830* on every slide, report and figure; network down → `data/synthetic-kii`, always called synthetic |
| Solver accuracy | first-order fast sweeping: ~0.25 s bias at 2 km grid spacing, about half at 1 km; non-zero residual even with the true model | expected; use it to teach grid convergence |
| Fenced approval cards | after the agent downloads data with python or curl, or calls an MCP tool, a call that copies ≥ 24 characters of that output asks with a `fenced: <reason> — this call only` row, and offers no `a` / `p` (handbook 04, §11; fixed 2026-10-30) | expected: read what the call does, then `y` (this call) or `n` |
| Workflows | dev profile only: steps run in-process, nothing from `requirements` installed (stdlib + NumPy available, no Numba, no matplotlib); hardened profile cannot launch steps | keep the bundle's NumPy solver; keep the quickstart's dev profile |
| Console | needs `bun` (or the release binary via `r4 tui install`), else the line interface without panes | make sure it is installed in pre-work |

## Not yet verified live

- **Parts 1–2 on a live stand-alone stack, from the console.** The console
  mapping was checked against the code and the science was measured locally
  (`run_local.py`, `japan_event.py`, `bench.py`), but the labs have not been
  run end-to-end in the console on a running stack: in particular the
  agent doing the ISC download in Lab 1.1, the folder ingest and the skill
  report in Lab 1.4, `/code run` with `--timeout`, and the Part 2 run
  (`!r4 bucket create` → `!r4 files upload` → `/workflow upload` →
  `/workflow run … --param bucket=…` → results). Rehearse them first;
  confirm that the in-process runner can read the attendee's
  `<you>.default.seismic` bucket.
- **Windows 11 + WSL2** — not tested.
- **f3-timeout** — server-side only, see the failure clinic.
