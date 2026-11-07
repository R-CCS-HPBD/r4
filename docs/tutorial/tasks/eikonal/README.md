# Task: seismic first-arrival times in 3-D — make the solver fast, then meet a real earthquake

Dated 2026-10-26, revised 2026-10-27 (adopted as the tutorial's storyline; no local ISC copy) · candidate storyline for the R4 + UMRS tutorial (the
maintainer's proposal: an eikonal travel-time solver instead of a
heat-equation example, 3-D from the start, and a step with a real Japanese
event whose observed P arrivals the agent downloads). Status: **prototype,
every number below measured locally**; not yet wired into the deck or labs.

## The science in one paragraph

The first-arrival travel time T of a seismic wave obeys the eikonal equation
|∇T| = 1 / v. The fast sweeping method (Zhao, 2005) solves it on a grid with
a first-order upwind (Godunov) update and Gauss–Seidel sweeps in the eight
diagonal orderings of a 3-D grid. Compare predicted with observed arrivals
and you are doing what earthquake location and travel-time tomography do.

## Files

| File | What |
|---|---|
| `eikonal_slow.py` | Correct but slow 3-D solver — plain Python loops over nested lists. `solve(velocity, h, (iz, iy, ix)) -> T` |
| `models.py` | Constant, linear-gradient (both with exact travel times), 1-D layered (default: the ak135 crust), and a 3-D "basin" model |
| `test_eikonal.py` | Acceptance tests: source = 0, analytic accuracy, symmetry, agreement with the reference to 1e-6 |
| `bench.py` | Speed-up and largest difference from the reference on the basin model |
| `fetch_isc_picks.py` | Downloads observed P picks for one event from the ISC Bulletin (standard library only) |
| `make_synthetic_picks.py`, `data/synthetic-kii/` | **Synthetic** offline fallback (invented event and stations, KRSO-1D v2 + noise); real ISC data is downloaded live and never committed (`data/*` is gitignored) |
| `japan_event.py` | Simulates the event on a 3-D grid and compares with the observed picks (CSV + figure) |
| `fit_1d_model.py` | Grid search over three-layer 1-D models — one independent solve per model |
| `plot_demo.py` | Slice of the 3-D basin model with wavefronts |
| `answer_key/` | Facilitator only: Numba version, NumPy diagonal-plane version |

## The event

**2021-12-03 00:28:28.6 UTC, Kii Channel (off Wakayama), ISC event 621611300**
— ISC hypocentre 33.815° N, 135.142° E, 16 km depth, mb 5.2; about 70 km from
Kobe; no casualties. The ISC Bulletin carries the JMA network's P picks; 25
stations lie within 2.5° (20–270 km), with the classic Pg → Pn crossover at
about 130 km. Picks come with the ISC's own residuals against ak135.

Data: **International Seismological Centre (2026), On-line Bulletin,
https://doi.org/10.31905/D808B830** — cite it wherever these data appear
(slides, handbook, attendee reports). Decision 2026-10-27 (maintainer): **no
local copy of ISC data in the repository** — the agent downloads it live in
the lab; the offline fallback is synthetic.

## Lab flow and prompts

**Step 1 — make it fast (agentic coding).**

> eikonal_slow.py is correct but too slow for a regional 3-D grid. Write
> my_fast.py with the same `solve()` signature. It must pass
> `EIKONAL_IMPL=my_fast pytest test_eikonal.py`, and `python bench.py --impl
> my_fast --sizes 21 41 61` must show max |ΔT| below 1e-6. Explain what you
> changed and why it is faster.

**Step 2 — a real earthquake (data + simulation).**

> Download the P-wave arrival times for ISC event 621611300 (Kii Channel,
> 2021-12-03) from the ISC FDSN event service with arrivals included. Keep
> the first P per station within 2.5°, rebuild station positions from
> distance and azimuth, and save event.json and p_picks.csv. Then run
> japan_event.py with my solver and explain the figure. Cite the ISC Bulletin.

If the venue network fails: `python japan_event.py --data
data/synthetic-kii` — a synthetic event computed from the observatory's
model KRSO-1D v2 (say it is synthetic). `fetch_isc_picks.py` is the
reference downloader.

**Step 3 — interpret (science, with the agent as an assistant).**

> The observed arrivals are about 1 s early against ak135. Search three-layer
> 1-D models for a better fit and tell me what the data can and cannot
> constrain.

## Measured answer key (2026-10-26, workstation CPU)

Real-event grid, 2 km spacing: 31 × 174 × 234 = 1.26 M nodes.

| Solver | One solve | vs reference |
|---|---|---|
| `eikonal_slow.py` (pure Python) | 14.6 s | — |
| NumPy diagonal planes | 1.40 s | ≈ 10× |
| Numba (same loops) | 0.24 s | ≈ 60× |

On the 1 km grid (9.9 M nodes): pure Python **117 s**, Numba **1.5 s** (≈ 75×),
identical residuals. On the basin benchmark
(`bench.py --sizes 21 41 61`) Numba is 56–62× faster; every key agrees with
the reference to max |ΔT| = 0 and passes all tests.

Comparison with the observations (ak135 crust, ISC hypocentre and origin time):

| | 2 km grid | 1 km grid |
|---|---|---|
| Residual (obs − sim), mean / RMS | −1.12 s / 1.32 s | −0.94 s / 1.14 s |
| Our residual − ISC's ak135 residual, mean / spread | −0.36 s / 0.23 s | −0.18 s / 0.14 s |

1-D model search (`fit_1d_model.py`, 162 models with the mid-crust boundary
fixed at 18 km, 2 km grid, Numba ≈ 30 s; the reference solver would need
≈ 40 min — this is the Part 2 fan-out): best RMS **0.44 s** (from 1.32 s),
mean ≈ 0, for 5.7 / 6.5 km/s crust, Moho 26 km, mantle 7.9 km/s; many models
lie within 0.02 s. The tutorial observatory's model KRSO-1D v2 scores
0.46 s. Full answer key, real and synthetic: `../../data/README.md`.

Teaching points:

- **Validation before interpretation.** Our 3-D eikonal residuals track the
  ISC's ak135 residuals station by station; the remaining offset halves when
  the grid spacing halves (first-order scheme, point-source error) — grid
  convergence seen with real data.
- **Speed-ups that keep the answer.** Numba keeps the algorithm and the
  update order: bit-identical answers. NumPy needs a different traversal —
  all nodes on a plane i + j + k = const are independent within a sweep —
  which is also how the method runs on a GPU (Detrixhe, Gibou & Min, 2013).
- **The data really are early** against the global ak135 model: the Pn
  branch beyond ~130 km arrives about 1 s early, consistent with a thinner
  crust than ak135's 35 km under this region.
- **Non-uniqueness.** Moho depth trades off against mantle velocity, and the
  best values sit at the edge of the search ranges: extend the grid, and
  never read one event's 25 picks as "the Moho is at 26 km". Origin time and
  hypocentre stay fixed at the ISC (ak135) solution — a real study would
  relocate.
- Flat Earth and stations at sea level are deliberate simplifications;
  station elevations and Earth curvature are a few tenths of a second at
  most at these distances.

## How it carries the storyline

| Part | Task |
|---|---|
| 1 | Step 1 (make it fast) and step 2 (real event) with the agent; RAG variant: the agent implements fast sweeping from a methods note in the knowledge pack |
| 2 | Workflow: the 1-D model search as a fan-out (one step per block of models), then a gather step that ranks models and plots the best fit; optional: relocate the event by grid search over hypocentres |
| 3 | GPU and scale: 3-D models with a slab or a basin, many events, a small travel-time tomography |

## Run it

```bash
cd docs/tutorial/tasks/eikonal
python -m pytest test_eikonal.py
python fetch_isc_picks.py --event 621611300 --out data/kii-channel-2021-12-03   # live; offline: data/synthetic-kii
python japan_event.py --impl answer_key.eikonal_numpy_diag
uv pip install numba && python japan_event.py --impl answer_key.eikonal_numba --h 1.0
python fit_1d_model.py --impl answer_key.eikonal_numba
```
