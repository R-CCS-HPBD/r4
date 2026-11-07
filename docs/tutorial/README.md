# R4 + UMRS tutorial — materials

Dated 2026-10-27 (deck rows updated 2026-10-29). Plan: the maintainers'
internal tutorial plan, ../development/tutorial-plan-2026-10-08.md
(2026-10-27 revision; not part of the public release). Status: **draft** — Opening, Part 1 (~2 h) and Part 2
(~3 h) complete; Part 3 is an outline until the platform feature study.
Subject thread: **seismic first arrivals** (a 3-D eikonal solver, a real
Japanese earthquake, a 1-D model search); it replaced the typhoon thread on
2026-10-27.

| Path | What |
|---|---|
| `slides/tutorial-r4-umrs.en.pptx`, `slides/tutorial-r4-umrs.ja.pptx` | The deck, 55 slides each (2026-11-05; 51 from 2026-11-01, 48 from 2026-10-29): Opening, Part 1 with the three deployment patterns (slide 10), the harness map (slide 11) and the "under the hood" block (slides 12–31, with a "Next" map at each block boundary), Part 2, Part 3 outline, one optional slide (when data leaves: external models and remote tool servers) and the 1.5 h lecture cut. Timing and talk track in the speaker notes |
| `slides/build_tutorial_deck.py` + `deck_kit.py`, `slides_*.py`, `code_comments_ja.py`, `kii_story_data.py` | The deck's source (python-pptx); `kii_story_data.py` loads the Kii Channel picks (the real ISC event for slide 6, the synthetic fallback) and the arrival bins shared with the figure script `render_kii_map.py`. Rebuild: `.venv/bin/python docs/tutorial/slides/build_tutorial_deck.py` |
| `slides/render_kii_map.py`, `slides/assets/kii-station-map.{en,ja}.png` | The station map on ETOPO1 relief (cartopy, kept out of the deck build). Station map: `uv run --script docs/tutorial/slides/render_kii_map.py --lang both`; the PNGs are committed in `slides/assets/` |
| `background/` | The measured numbers behind the "under the hood" slides: `next_token_probe.py` (tokens, next-token probabilities on a local Qwen2.5 model) and `embedding_probe.py` (retrieval scores with the stack's embedder), with their JSON output |
| `tasks/eikonal/` | The seismic task: slow 3-D solver, tests, benchmark, answer keys (Numba, NumPy planes), ISC downloader, real-event comparison, model search, synthetic fallback — see its README |
| `data/` | Knowledge pack for the RAG lab (an invented observatory, EN/JA), the `residual-report` skill, and the **answer key** (`data/README.md`) |
| `workflows/seismic-wf/` | Part 2 fan-out workflow (`search_low`, `search_high` → `rank`) with `run_local.py` (local dry run) |
| `workflows/make_fault_set.py`, `workflows/legacy/plot_travel_times.py` | Failure-clinic bundles (generated, not committed) and the "existing code" of Lab 2.4 |
| `en/`, `ja/` | Handbook: `00-prework` (attendee announcement), `01-part1-labs`, `02-part2-labs`, `03-facilitator-guide`, `04-background-model-agent-harness` |

## Data policy

Real earthquake data come from the **ISC Bulletin** and are downloaded live by
the agent in the lab; they are never committed (`tasks/eikonal/data/*` is
gitignored, decision 2026-10-27). Cite **International Seismological Centre
(2026), On-line Bulletin, https://doi.org/10.31905/D808B830** wherever they
appear. The offline fallback (`tasks/eikonal/data/synthetic-kii`) is
synthetic and must be called so. One exception, as figures only
(2026-11-05): the story slide's map and travel-time chart
(`slides/assets/kii-station-map.*.png`, `kii-traveltime.*.png`) show the real
stations and picks, rendered by `render_kii_map.py` from a fresh download;
no table of ISC values is committed, and both figures carry the citation.

## How the material was checked (2026-10-26/27)

- Every command on the slides and in the handbook was checked against the
  code; where the plan assumed a feature that does not exist, the material
  uses the nearest real path and the facilitator guide lists it.
- The solver keys pass the tests and agree with the reference exactly; the
  real-event comparison was run on a live ISC download (25 stations) and
  matches the ISC's own ak135 residuals within 0.2–0.4 s; the workflow runs
  locally (54 s) and passes `r4 workflow lint`.
- Both decks pass the OOXML validator and were checked visually.
- **Not yet done:** the labs on a live stand-alone stack, a Windows/WSL2
  test, the native-speaker review of the Japanese, and the local
  model-serving quality test (plan §1a) — which should include
  the agentic-coding prompt.
