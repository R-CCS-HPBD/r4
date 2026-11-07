# Same-model harness benchmark

Holds the model constant (glm-5.3 on rikyu) and swaps the agent harness: R4
companion, Claude Code, OpenCode. Every lane gets byte-identical prompts, the
same bwrap sandbox, the same sampling, and proxy-side accounting. Variants of
R4 (patched copies of `r4/src/r4`) isolate individual harness fixes.

Reports built with this code:
- r4-harness-gap-2026-10-04.md — the gap diagnosis and fixes F1–F6 (internal, not published).
- r4-harness-benchmark-detailed-2026-10-04.md — metrics, harness architectures, replicates, leave-one-out attribution (internal, not published).
- `docs/` here — architecture notes on the R4 harness and on Claude Code / OpenCode, with code citations.

## Code here, data outside git

The question set, corpus and every answer, grade and log come from project
research data. They never enter the repository, and the question set and
corpus are not distributed with it: to run the bench you need your own bench
root with the layout below.
`paths.py` resolves one bench root from `$R4_SAME_MODEL_BENCH`, which is
required (there is no default). The scripts stop with a message when it is
unset, and refuse to run if it points inside the checkout:

| Under the bench root | Contents |
|---|---|
| `gold/gold.json`, `corpus/`, `results/source_kinds.json` | inputs: the verified question set and the corpus |
| `proto/`, `current/` | arm C prototype KG CLI (`kgq`) and arm B platform KG tools (`kgq-current`) |
| `r4glm/` | grades (`grades_all.json`), reports (`report_all.json`, `full_metrics.md`, `gap_struct.json`, …) |
| `r4glm/harness/` | `results_<lane>/`, `logs/`, `run/` (proxy sockets), `work/`, `smoke/` outputs, `r4_variants/` |

`smoke/` in this directory is a synthetic fixture (one invented project) for
plumbing checks; smoke-run outputs still go to the bench root.

The model key never leaves the proxies: `openai_passthrough.py` and
`anthropic_bridge.py` resolve the rikyu source in-process through the repo
shim, listen on UNIX sockets (0600, in a 0700 dir), and the sandboxed
harnesses see only dummy keys. Answering and grading both use glm-5.3 on rikyu.

## Layout

| File | Role |
|---|---|
| `paths.py` | code/data locations (above) |
| `sandbox.py` | bwrap sandbox: own netns, clearenv, allow-listed home, relays to the proxy sockets, per-case egress guard |
| `start_proxies.sh`, `stop_proxies.sh` | bridge (Claude Code), passthrough (OpenCode, R4), kgq Postgres/S3 relays |
| `anthropic_bridge.py`, `openai_passthrough.py`, `proxy_common.py`, `egress_guard.py` | the proxies and the per-case egress logger |
| `run_glm.py` | shared prompt builder; also the "as deployed" R4 lane (host network, real config) |
| `run_r4.py` | hardened R4 lane (`--variant/--r4-src` for patched copies) |
| `run_harness.py` | Claude Code and OpenCode lanes, sandbox probes, kgq checks |
| `apply_patches.py`, `run_ablation_group.sh` | build R4 fix variants (F1–F6) and run ablation groups. The patches match the pre-repair tree (`fcfd7177`): since the 2026-10-05 harness repair (waves W1–W4, ADR-087) `r4/src` already contains F1–F4 and F6 in their final form, so the script's assertions fail on a current tree by design — use it only to rebuild the historical variants from `fcfd7177` |
| `grade_all.py`, `grade_fill.py`, `grade_r4.py`, `glmjudge.py`, `judge_reliability.py` | blind glm-5.3 rubric grading, retries, judge agreement |
| `gap_analysis.py`, `analyze_all.py`, `analyze_r4.py`, `full_metrics.py`, `classify_losses.py` | proxy-log structure, cell summaries, every report table |
| `toolbench.py`, `toolbench_post.py` | tool-level retrieval check of the KG arms |
| `opencode.glm.json` | OpenCode provider config (local passthrough, dummy key) |

## Run

```bash
export R4_SAME_MODEL_BENCH=/path/to/bench-root   # required; outside the checkout
B=benchmarks/harness/same_model; PY=.venv/bin/python

bash $B/start_proxies.sh                                    # needs a registered rikyu source in r4
$PY $B/run_harness.py --smoke --probe --harness claude,opencode --arms A   # sandbox self-check
$PY $B/run_r4.py --qids all --arms A,B,C,P --par 4 --timeout 1200
$PY $B/run_harness.py --harness claude,opencode --qids all --arms A,C --par 2

# R4 fix variants: copy r4/src/r4 to $R4_SAME_MODEL_BENCH/r4glm/harness/r4_variants/<name>/src/r4, then
$PY $B/apply_patches.py $R4_SAME_MODEL_BENCH/r4glm/harness/r4_variants/<name>/src/r4 F1,F2,F3,F4,F6
$PY $B/run_r4.py --variant <name> --r4-src $R4_SAME_MODEL_BENCH/r4glm/harness/r4_variants/<name>/src --qids all --arms A

$PY $B/grade_all.py && $PY $B/grade_fill.py                 # glm-5.3 grading (never the Anthropic API)
$PY $B/gap_analysis.py > $R4_SAME_MODEL_BENCH/r4glm/gap_analysis.out
$PY $B/analyze_all.py && $PY $B/full_metrics.py             # → r4glm/report_all.json, full_metrics.md
bash $B/stop_proxies.sh
```

Requirements: `bwrap`, `socat`, the Claude Code and OpenCode binaries, and for
arms B/C/P the local `kgprobe-pg` (127.0.0.1:55432) and `kgprobe-s3`
containers. Run the analysis scripts and they print aggregates and question
IDs only; keep it that way — no answer text in logs or reports.
