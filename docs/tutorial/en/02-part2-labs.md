# Part 2 labs — a small workflow (~3 h)

Dated 2026-10-29 · R4 + UMRS tutorial · EN ([日本語](../ja/02-part2-labs.md)) ·
Part 2 section of `slides/tutorial-r4-umrs.en.pptx` (slides 44–51)

In Part 1 one solve took well under a second with your fast solver. A
**model search** needs one solve per candidate model — 163 solves here
(2 × 81 candidate models + the ak135 baseline).
You package that search as a UMRS workflow: two search steps **fan out**
(each scores one block of models), and a **rank** step **gathers** their
results and writes a residual report. It runs on your own local UMRS. The
finished bundle is `docs/tutorial/workflows/seismic-wf/`; you build your
own copy (`my-seismic-wf/` in the repository folder) step by step.

As in Part 1, **everything happens inside the console** (`r4`, started in
the repository folder; cheat sheet at the top of
[01-part1-labs.md](01-part1-labs.md)). Commands starting with `!` run in
the workspace from the console; use `/term` for long output.

Input data: the folder from Part 1,
`docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/` (ISC picks —
cite International Seismological Centre (2026), On-line Bulletin,
https://doi.org/10.31905/D808B830), or the **synthetic** fallback
`docs/tutorial/tasks/eikonal/data/synthetic-kii/`. Both hold `event.json`
and `p_picks.csv`.

## Concepts

- **Step** — a Python class: `setup(config)`, `run(ctx) -> StepResult`,
  `teardown()`; imported as `from umrs.pipeline_sdk import Step, StepContext, StepResult`.
- **`workflow.json`** — the bundle's manifest: `name`, `source`, `steps`;
  each step has `name`, `description`, `entrypoint` (`"module:Class"`),
  `config`, `depends_on` (the upload contract requires `description` and
  `depends_on` on every step).
- **Bucket** — object storage on your disk. A run reads inputs from the
  bucket named by its `bucket` parameter and writes outputs to
  `<bucket>-output`.
- **Keys flow downstream** — a step returns `StepResult(output_keys=[…])`;
  the next step (with `depends_on`) receives them as `ctx.input_keys` and
  reads them with `ctx.download_parent_bytes(key)`. There are no
  `inputs`/`outputs` fields in `workflow.json`.
- **Fan-out / gather** — steps with no dependency between them may run side
  by side; a step that `depends_on` several parents receives **all** their
  output keys in `ctx.input_keys`.
- **routing_hints** (`gpu`, `image`, `prefer`, …) say where a step should
  run on a cluster — Part 3. On the laptop's dev profile every step runs
  in-process inside the umrs container.

**Constraint for this lab:** on the stand-alone dev profile, steps run inside
the umrs container and nothing from `requirements` is installed — a step may
import only the standard library, NumPy (already in the container) and the
umrs SDK. Numba and matplotlib are **not** there. That is why the bundle
carries its own NumPy solver (`eikonal.py`, the diagonal-plane version) and
why `seismic_core.py` uses only `csv`, `json`, `math`, `datetime` and NumPy.

## Lab 2.1 — a one-step workflow (30 min, slide 47)

Copy the finished bundle, then cut it down to one step:

```text
!cp -r docs/tutorial/workflows/seismic-wf my-seismic-wf
```

> In my-seismic-wf/workflow.json keep only the step search_low and rename
> the workflow to my-seismic. Do not change steps.py.

The result should read:

```json
{
  "name": "my-seismic",
  "version": "1.0",
  "description": "Tutorial Part 2: fit a 1-D P-velocity model to first-P picks with 3-D eikonal solves (fan-out search, gather and rank).",
  "source": {"type": "local_path", "path": "."},
  "execution_mode": "in_process",
  "requirements": [],
  "resources": {"gpu_count": 0, "cpu_limit": "2", "memory_limit": "2Gi", "timeout_seconds": 1800},
  "steps": [
    {
      "name": "search_low",
      "description": "Score candidate models with a shallow Moho (24-28 km), plus the ak135 baseline.",
      "entrypoint": "steps:ModelSearchStep",
      "config": {
        "event_key": "event.json",
        "picks_key": "p_picks.csv",
        "grid_km": 4.0,
        "upper": [5.7, 5.9, 6.1],
        "lower": [6.5, 6.7, 6.9],
        "moho_km": [24.0, 26.0, 28.0],
        "mantle": [7.7, 7.9, 8.1],
        "include_ak135": true
      },
      "depends_on": [],
      "timeout_seconds": 900
    }
  ]
}
```

The four value lists make 3 × 3 × 3 × 3 = 81 three-layer models (upper
crust, lower crust from 18 km, Moho depth, mantle), plus the ak135 baseline.
The step class it names, in `my-seismic-wf/steps.py`:

```python
class ModelSearchStep(Step):
    """Fan-out: score one block of candidate models."""

    name = "search"

    def setup(self, config: dict) -> None:
        self.config = config or {}

    def run(self, ctx: StepContext) -> StepResult:
        time.sleep(float(self.config.get("debug_delay_seconds", 0)))
        event, picks = _inputs(ctx, self.config)
        h = float(self.config.get("grid_km", 4.0))
        scores = [core.score(event, picks, m, h) for m in core.candidates(self.config)]
        if self.config.get("include_ak135"):
            scores.append({**core.score(event, picks, core.AK135_CRUST, h), "baseline": True})
        key = f"{self.config['step_name']}.json"
        ctx.upload_bytes(key, json.dumps(scores).encode(), content_type="application/json")
        return StepResult(output_keys=[key], extra={"models": len(scores)})
```

`_inputs()` reads `event.json` and `p_picks.csv` from the input bucket.
`config["step_name"]` is filled in by the runner (UMRS and `run_local.py`
both do it), so the same class can serve several steps.
`debug_delay_seconds` is there only for the failure clinic.

Check before uploading — there is no server-side dry run:

```text
/code run my-seismic-wf/run_local.py --timeout 300 -- --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 --workflow my-seismic-wf/workflow.json
!r4 workflow lint my-seismic-wf
```

(`--data docs/tutorial/tasks/eikonal/data/synthetic-kii` for the
fallback.) `run_local.py` plays the runner on your laptop (folders as
buckets) and writes `local-run/local-output/search_low.json` **in the
repository folder** — 82 scores, each with the model's layers, `rms_s` and
`mean_s`. On a workstation this took about 30 s. `lint` checks the bundle's
shape only — Lab 2.5 shows why that is not enough. (`my-seismic-wf/` and
`local-run/` are yours; git does not track them.)

## Lab 2.2 — upload the data, run, read the results (20 min, slide 48)

The console has no bucket commands (and `/files upload` has no bucket
option), so the bucket steps use `!`:

```text
!r4 bucket create default seismic
!r4 bucket list
```

`bucket create` makes `<you>.default.seismic` and its `-output` twin; note
the full name shown and use it below in place of `<you>.default.seismic`.

```text
!r4 files upload docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/event.json --bucket <you>.default.seismic
!r4 files upload docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03/p_picks.csv --bucket <you>.default.seismic
```

Now upload and run the **finished** three-step bundle, so you see a whole
run and its report before building the rest yourself:

```text
/workflow upload docs/tutorial/workflows/seismic-wf
/workflow run seismic-model-search --param bucket=<you>.default.seismic
```

Watch it in the Work pane (`/work`, or bare `/workflow`); **Alt+W** opens
the running workflow's graph, and `t` on a row runs or traces it. The run
takes a minute or more (`--wait` is capped at 20 s, so do not rely on it).
Then:

```text
/workflow status <execution-id>
/workflow results <execution-id>
!r4 files download report.md --bucket <you>.default.seismic-output -o report.md
```

and ask the agent to read `report.md` with you. `/workflow list` shows your
runs, `/workflow show seismic-model-search` the uploaded bundle. Your own
one-step copy runs the same way (`/workflow upload my-seismic-wf`,
`/workflow run my-seismic --param bucket=…`) and leaves `search_low.json`
in the output bucket.

*Outside the console* (fallback): `r4 workflow run docs/tutorial/workflows/seismic-wf -p bucket=<b> --wait`,
`r4 workflow results <id>`, `r4 files download …`.

Then a 10-minute break (minutes 70–80). A run still red (failed)? Leave it
open — staff fix it after the break.

## Lab 2.3 — fan out and gather (40 min, slide 49)

Restore the fan-out in your copy. Ask the agent:

> In my-seismic-wf/workflow.json add back the steps search_high and rank
> exactly as in docs/tutorial/workflows/seismic-wf/workflow.json. Then
> explain what ctx.input_keys holds in RankStep.

The two entries:

```json
{
  "name": "search_high",
  "description": "Score candidate models with a deeper Moho (30-35 km).",
  "entrypoint": "steps:ModelSearchStep",
  "config": {
    "event_key": "event.json",
    "picks_key": "p_picks.csv",
    "grid_km": 4.0,
    "upper": [5.7, 5.9, 6.1],
    "lower": [6.5, 6.7, 6.9],
    "moho_km": [30.0, 32.0, 35.0],
    "mantle": [7.7, 7.9, 8.1]
  },
  "depends_on": [],
  "timeout_seconds": 900
},
{
  "name": "rank",
  "description": "Gather both searches, rank all models, write the residual report.",
  "entrypoint": "steps:RankStep",
  "config": {
    "event_key": "event.json",
    "picks_key": "p_picks.csv",
    "grid_km": 4.0
  },
  "depends_on": ["search_low", "search_high"],
  "timeout_seconds": 300
}
```

`search_high` uses the **same class** with other values. The gather step,
already in `steps.py`:

```python
class RankStep(Step):
    """Gather: rank every scored model and write the report."""

    name = "rank"

    def setup(self, config: dict) -> None:
        self.config = config or {}

    def run(self, ctx: StepContext) -> StepResult:
        scores = []
        for key in ctx.input_keys:
            scores.extend(json.loads(ctx.download_parent_bytes(key)))
        baseline = next(s for s in scores if s.get("baseline"))
        ranked = sorted((s for s in scores if not s.get("baseline")), key=lambda s: s["rms_s"])
        event, picks = _inputs(ctx, self.config)
        h = float(self.config.get("grid_km", 4.0))
        report = core.report_markdown(event, ranked, baseline, len(picks), h)
        ctx.upload_bytes("report.md", report.encode(), content_type="text/markdown")
        ctx.upload_bytes(
            "best_model.json",
            json.dumps(ranked[0], indent=2).encode(),
            content_type="application/json",
        )
        return StepResult(
            output_keys=["report.md", "best_model.json"],
            extra={"best_rms_s": ranked[0]["rms_s"], "ak135_rms_s": baseline["rms_s"]},
        )
```

`search_low` and `search_high` do not depend on each other, so they can
run side by side. `rank` waits for both, and its `ctx.input_keys` holds
**both** parents' keys: `["search_low.json", "search_high.json"]`.

Dry run, lint, upload, run, read the report:

```text
/code run my-seismic-wf/run_local.py --timeout 300 -- --data docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 --workflow my-seismic-wf/workflow.json
!r4 workflow lint my-seismic-wf
/workflow upload my-seismic-wf
/workflow run my-seismic --param bucket=<you>.default.seismic
/workflow results <execution-id>
!r4 files download report.md --bucket <you>.default.seismic-output -o report.md
```

Expected for the synthetic fallback (4 km grid, 163 solves, 54 s locally
with NumPy): ak135 RMS **1.48 s** (mean −1.30 s), best model RMS **0.21 s**
(mean −0.01 s). For the real picks, compare your report with the 2 km
answer key (ak135 1.32 s; best of 162 models 0.44 s; KRSO-1D v2 0.46 s) —
the 4 km grid is coarser, so expect somewhat different numbers.

Read the top of the ranking: models with the Moho at **26 km and 28 km tie**
— on a 4 km grid, Moho depths 2 km apart cannot be told apart, and the
report says so. Several models fit almost equally well: Moho depth trades
off against velocity. That is a science result, not a bug.

## Lab 2.4 — the agent as co-author (30 min, slide 50)

1. **Reuse existing code.** Ask:
   *"Turn docs/tutorial/workflows/legacy/plot_travel_times.py into a fourth
   step 'plot' in my-seismic-wf, after 'rank'. Steps cannot use
   matplotlib: standard library only (NumPy is allowed), write
   travel_times.svg. Dry-run it with run_local.py and run r4 workflow lint
   before you finish."* Or bring your own script.
2. **Pull in an outside skill.** Skills install from a GitHub path:
   `!r4 skill github install <owner>/<repo>/skills/<name>` (installing by a
   full URL is not supported yet); then `/skills` lists it. The facilitator
   names the shared catalogue on the day.
3. **Debug from the error.** *"The run failed. Read `/workflow results
   <id>`, find the cause, fix it, upload and re-run."*

Compare with your hand-made steps: did the agent keep to the standard
library, read the picks from the input bucket (not a hard-coded path like
the legacy script's `DATA`), add `depends_on: ["rank"]`, and pass the dry
run and `lint` first?

## Lab 2.5 — failure clinic (20 min, slide 51)

The facilitator hands each pair one broken bundle (generated by
`docs/tutorial/workflows/make_fault_set.py` into
`docs/tutorial/workflows/faults/`; each is a copy of `seismic-wf` with one
fault in `workflow.json`, named `seismic-<fault>`). Upload and run it, read
the error, fix `workflow.json` (yourself or with the agent), upload and run
again:

```text
/workflow upload docs/tutorial/workflows/faults/f1-bad-entrypoint
/workflow run seismic-f1-bad-entrypoint --param bucket=<you>.default.seismic
```

Where to look:

| What | In the console |
|---|---|
| status and the error per step | `/workflow results <id>`, `/workflow status <id>` |
| the attempt timeline of a job | `/job <id>` |
| activity and events | `/logs` (Activity pane), **F9** |
| in-process step output | `/term`, then `./r4-local logs umrs` |

Note: **all four broken bundles pass `r4 workflow lint`.**

## Homework (slide 53)

Pick another event from the ISC Bulletin (ask the agent to download it, or
`!cd docs/tutorial/tasks/eikonal && python fetch_isc_picks.py --event <id> --out data/<name>`),
run your workflow on it, and keep the citation. Or go further: hold the
model fixed and **relocate** the event — search over hypocentres and origin
times instead of velocity models (the answer key holds both fixed at the
ISC solution; a real study would not). Finish the `plot` step if you did
not.
