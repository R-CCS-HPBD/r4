# Terminal-Bench Lane

Runs the **R4 Companion** harness against [Terminal-Bench](https://www.tbench.ai/)
tasks under the [Harbor](https://www.harborframework.com/) runner. Complements
the sample-coding suite (host-side) and the SWE-bench lane (in-container):
holds the model constant, varies nothing harness-side, and measures the
companion on fixed third-party terminal tasks with independent verifiers.

## Setup (once per host)

```bash
uv tool install 'harbor[modal]'    # harbor CLI (its own venv)
docker --version                    # task environments run on local docker
```

The agent module lives in this repo; `harbor run` needs the checkout on
`PYTHONPATH` so the custom-agent import path resolves.

## Run

Model calls go through the host model proxy by default (2026-10-05): the
agent refuses to start without `R4BENCH_MODEL_TOKEN`. `run_tb4.sh` starts the
proxy and exports the token; by hand:

```bash
cd <repo root>
TOKEN=$(openssl rand -hex 24)
PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/model_proxy.py \
    --source rikyu --model glm-5.3 --port 8333 --token "$TOKEN" &
PYTHONPATH=$PWD R4BENCH_MODEL_TOKEN="$TOKEN" harbor run \
    -d terminal-bench/terminal-bench@4.0.0 \
    -e docker \
    -a benchmarks.harness.terminal.r4_agent:R4CompanionAgent \
    -m glm-5.3@rikyu \
    -l 5 -n 2 -y

# smoke: built-in sanity task (same proxy and token)
PYTHONPATH=$PWD R4BENCH_MODEL_TOKEN="$TOKEN" harbor run -t hello-world/hello-world \
    -a benchmarks.harness.terminal.r4_agent:R4CompanionAgent \
    -m glm-5.3@rikyu -y

# legacy route, no proxy: the source's API key is copied into the container
PYTHONPATH=$PWD harbor run -t hello-world/hello-world \
    -a benchmarks.harness.terminal.r4_agent:R4CompanionAgent \
    -m glm-5.3@rikyu --ak copy_source_key=true -y

# plumbing tests (no docker, no LLM):
python -m pytest benchmarks/harness/terminal/test_terminal_plumbing.py -v
```

Results land in `jobs/<timestamp>/{result.json,<trial>/agent/...}`. The
trial's agent logs hold `driver-result.json`, the full companion payload
(response text + token metrics + active-skill and decision-gate diagnostics),
and `transcript.jsonl` when the driver payload names a session transcript
(`transcript_path`; best effort). `result.json` maps tokens onto Harbor's
AgentContext fields.

## How it works

1. `support.ensure_wheelhouse()` builds pure-python wheels of `r4-contracts`,
   `science`, `packages/r4-teamengine-spi`, `r4` into a content-addressed
   cache (`$XDG_CACHE_HOME/r4bench/terminal-wheels/<hash>`) — rebuilt only
   when package sources change.
2. `R4CompanionAgent.install()` (per trial): uploads wheels +
   `driver.py`, creates `/opt/r4bench/venv`, pip-installs wheels + `pyyaml`
   (deps resolve from PyPI over the task container's bridge network), writes
   `~/.config/r4/sources.json` whose only model source is the host model
   proxy (`model_proxy.py`, port 8333) under the public source's name, with
   the proxy's per-run token (`support.proxied_source`). The source's API key
   stays in `model_proxy.py` on the host; platform profiles/JWTs never enter
   task containers. Only `--ak copy_source_key=true` copies the filtered
   `kind=public` source entry, API key included, which the agent's shell and
   the task code can read (`support.filter_sources`, fails closed). Default
   since 2026-10-05 (B2); proxy mode without `R4BENCH_MODEL_TOKEN` fails at
   agent start.
3. `run()` executes the standalone driver (TRUST mode, approval callback
   pre-approving every tool call) in the agent workdir; the driver's last
   stdout line is the JSON verdict. The driver writes the full payload into a
   directory the agent user owns (`$HOME/.r4bench`, else a fresh `/tmp` dir):
   `/opt/r4bench` is root-owned, and a task that runs its agent as a non-root
   user failed there with PermissionError (`risk-scorer-replay`, B1). Agent
   errors raise; task failures (verifier 0.0) do not.
4. The driver states that nobody is there to answer through R4's own
   settings, with `setdefault` so a harbor `--ae` value wins:
   `R4_AUTONOMOUS=1` (stuck-loop guards nudge instead of ending the turn) and,
   since 2026-10-24, `R4_ASK_USER_MODE=auto` (`ask_user` gets R4's auto-answer
   instead of a question nobody reads). Its env is built in one place,
   `support.agent_env()`: `R4_TIMEOUT`, `R4_DECISION_MODEL`, and the profile
   and sampling knobs only when asked (table below).

## Flags

| `--ak` kwarg | Default | Meaning |
|---|---|---|
| `source` | `rikyu` | Public source from host `~/.config/r4/sources.json` |
| `model` | (unset) | Overrides `-m`; r4 form `model@source` |
| `workdir` | image WORKDIR | Agent workspace inside the container (was fixed `/app` until 2026-10-05) |
| `exec_timeout_sec` | `3600` | Per-task agent execution cap (TB 4.0 board runs: `28800`) |
| `install_timeout_sec` | `900` | Per-trial install cap |
| `r4_timeout_sec` | `1200` | Per-request model HTTP read timeout (`R4_TIMEOUT` in-container; r4 default 300 is under one glm-5.3 turn on large contexts) |
| `decision` | `off` | `nimble` = decision model through `nimble_proxy.py` (`R4BENCH_NIMBLE_TOKEN`) |
| `model_proxy_port` | `8333` | Port of `model_proxy.py` on the bench host; needs `R4BENCH_MODEL_TOKEN` (the proxy's `--token`) |
| `copy_source_key` | `false` | `true` = legacy route: copy the source entry, API key included, into the container instead of using the proxy |
| `profile` | (unset) | `terminal` = `R4_AGENT_PROFILE=terminal`, the lean-agent ablation (plan H2); unset or `default` = R4 product defaults, the headline setting |
| `temperature` | (unset) | `R4_COMPANION_TEMPERATURE` (0–2); unset = R4's default (0.4) |
| `top_p` | (unset) | `R4_COMPANION_TOP_P` (0–1); unset = not sent |

Any other R4 switch goes in with harbor's own `--ae NAME=VALUE` (applied to
every agent command; it overrides the agent's own env), e.g. an M2 arm:
`run_tb4.sh tb40-m2a -i terminal-bench/<task> --ae R4_COMPANION_THINK_BUDGET_SEC=300`.
The R4 switches are listed in `docs/user-manual/appendix-b-env-vars.md`.

## Terminal-Bench 4.0 against the public board (2026-10-05)

```bash
benchmarks/harness/terminal/run_tb4.sh tb40-<date>              # all CPU tasks, 4 at a time
benchmarks/harness/terminal/run_tb4.sh tb40-<date> -i terminal-bench/<task>   # one task
# decision-model lane (ADR-076, Nimble through nimble_proxy.py and the host's rccs-cloud
# profile); a second lane beside a running one needs its own model proxy port
TB4_DECISION=nimble TB4_PROXY_PORT=8334 benchmarks/harness/terminal/run_tb4.sh tb40-<date>-nimble
PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/tb4_public_compare.py \
    --run benchmarks/results/terminal-bench/tb40-<date>/r4 --tasks <tb4 dataset dir> \
    --not-run fp8-rmsnorm-gemm,jax-speedrun-gpu,math-eval-grader \
    --proxy-log benchmarks/results/terminal-bench/tb40-<date>/model-proxy.jsonl
# per-trial diagnostics (verifier tests, driver metrics, proxy rows, transcript counts)
PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/tb4_trial_diagnostics.py \
    --run benchmarks/results/terminal-bench/tb40-<date>/r4 --tasks <tb4 dataset dir> \
    --proxy-log benchmarks/results/terminal-bench/tb40-<date>/model-proxy.jsonl > <date>_trials.jsonl
# task-by-task diff of two runs (like-for-like accuracy, sign test, harness symptoms)
PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/tb4_run_diff.py \
    --base benchmarks/results/terminal-bench/tb40-20261004/baseline_trials.jsonl \
    --new <date>_trials.jsonl --base-label "before" --new-label "after"
# one report for R4, terminus-2 and the public rows (since 2026-10-24, R2): progress,
# Wilson CIs on /63 and /66 with and without Epoch-flagged tasks, paired McNemar,
# tokens (B1 cut estimates included), shell segments, cut minutes, decode speed;
# read-only, writes only --out; works on a run that is still going. Per task a
# real attempt beats an infra failure (exit -9 re-runs in r4-rerun / terminus-rerun
# replace the kill) and the first real attempt counts; a task that is infra in
# either lane is left out of the paired test and counted (review 2026-10-24)
PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/tb4_combined_report.py \
    --lane r4=benchmarks/results/terminal-bench/tb40-full-<date> \
    --lane terminus-2=benchmarks/results/terminal-bench/tb40-terminus-full-<date> \
    --out benchmarks/results/terminal-bench/tb40-report-<date>.md
```

Each run directory holds `run-meta.json` (since 2026-10-24, B3), written by
`run_tb4.sh` and `run_tb4_terminus.sh` before harbor starts
(`python -m benchmarks.harness.terminal.support run-meta --out F --lane r4|terminus-2 -- <harbor args>`):
the commit and dirty flag, `R4BENCH_PACKAGE_ROOT`, the parsed harbor settings
(tasks included/excluded, concurrency, `--ak` values), `agent_knobs` (the R4
switches the agent env carries: driver defaults, then the agent's env, then
`--ae`; allowlist `R4_COMPANION_*`, `R4_SHELL_*`, `R4_AUTONOMOUS`,
`R4_AGENT_PROFILE`, `R4_ASK_USER_MODE`, `R4_TOOL_PYTHON`, `R4_TIMEOUT`,
`R4_DECISION_MODEL`), `temperature` / `top_p` (`null` = the agent's default),
the exec timeout and the install footprint (what each lane leaves in the task
container). Names or values that look like credentials are dropped, and only
their names are listed under `dropped_as_secret`; the proxy tokens travel in
the environment and are never recorded.

Each trial's agent logs hold:
- `driver-result.json`;
- since the 2026-10-05 harness repair (W6), the R4 session transcript
  `transcript.jsonl` (every tool call and result, continue nudges, per-round
  usage), copied even when the driver dies;
- `container-ip.txt`, the address the model proxy logs as the peer, so the
  diagnostics attribute proxy rows exactly.

The model proxy passes SSE through to streaming clients (R4 streams every
agent round since ADR-087) and closes the upstream request when the client
goes away; each usage row says `"stream": true` for those. Since 2026-10-24
(B1) a streaming row also has `relayed_chars` (`reasoning`, `content`,
`tool_args`: characters relayed to R4, never the text), and every row has
`decode_tps` (completion tokens per second after the first token; `null` when
unknown). A stream the client cut (R4's thinking budget; status 499) is marked
`"cut": true`. It, and any row without upstream usage, carries
`usage_estimated.completion_tokens` = ceil(relayed chars / 4), so per-trial
token totals include cut rounds. Older logs lack these fields. `run_tb4.sh` also
starts `exec_watch.sh` (`TB4_EXEC_WATCH=0` to skip): when a trial's driver
`docker compose exec` client disappears, `<run>/exec-watch.log` gets a host
process snapshot.

A trial killed from the host (`NonZeroAgentExitCodeError`, exit −9) is flagged
`infra_kill` by the diagnostics and is not valid for scoring. `run_tb4.sh` re-runs
those tasks once into `<run>/r4-rerun` after the main pass, and since 2026-10-24
`run_tb4_terminus.sh` does the same into `<run>/terminus-rerun` (`TB4_RERUN_INFRA=0`
skips it; `tb4_trial_diagnostics.py --run <run>/r4 --infra-kills` lists them).
Merge the passes (latest finished trial per task wins):

```bash
PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/tb4_trial_diagnostics.py \
    --run benchmarks/results/terminal-bench/tb40-<date>/r4 \
    --run benchmarks/results/terminal-bench/tb40-<date>/r4-rerun --tasks <tb4 dataset dir> \
    --proxy-log benchmarks/results/terminal-bench/tb40-<date>/model-proxy.jsonl
``` The raw run trees are
git-ignored (`benchmarks/results/terminal-bench/.gitignore`); only the
`*_summary.md` / `*_trials.jsonl` beside them are meant for git.

Both lanes from one command, R4 built from a clean export of the pushed HEAD,
with preflight checks (rikyu up, Nimble READY, lane ports free):

```bash
benchmarks/harness/terminal/run_tb4_lanes.sh <tag> problem   # the 10 problem tasks, both lanes
benchmarks/harness/terminal/run_tb4_lanes.sh <tag> all       # every CPU task, both lanes
.venv/bin/python benchmarks/harness/terminal/tb4_lane_summary.py benchmarks/results/terminal-bench/tb40-<tag>-<date>*
```

Both lanes are benched: decision off (the public rows' setting) and decision on.
In the decision lane, every auto-approved risky tool call is first scored by
Nimble (`decision` lines in the transcript; `tb4_watch.py` shows `gate`, `esc`,
`gate_err`, `gate_max_ms`). The bench approves every escalation (nobody is there
to answer), so the gate costs time and is logged, but does not block. The launcher
exits if either proxy does not answer with the run's own token: a proxy left over
from another run would otherwise serve the trials (2026-10-06).

What `run_tb4.sh` fixes so the comparison is fair and safe:
- **Key:** model calls go through `model_proxy.py`; containers get a per-run
  token, never the source's API key.
- **Long turns:** glm-5.3 can reason for 30–40 min in one TB 4.0 turn. rikyu's
  gateway answers 502 after 600 s without a byte, so the proxy streams upstream
  and hands R4 one assembled reply; R4's read timeout is 2 h.
- **Output cap:** without a `max_tokens`, rikyu stops a reply at 32,768 tokens,
  which a first turn can spend entirely on reasoning. The proxy sets 131,072
  when R4 sends none (public glm-5.3 runs allow about 128k). Since ADR-087
  (2026-10-05) R4 sends its own limit (131,072 for reasoning families), so the
  proxy default only matters for runs of older R4 trees.
- **Timeouts:** each task's own 8 h agent timeout (the board's), exec cap 28800.
- **Working directory:** the agent starts in the image's WORKDIR (TB 4.0 has
  `/workspace` and `/task` images besides `/app`).
- **GPU tasks:** the three H100-only tasks are excluded (harbor 0.23's Docker
  environment cannot allocate GPUs; their verifiers are calibrated on H100).
- Task names in `-i`/`-x` need the `terminal-bench/` prefix.

Never add `--upload` or `--launch`: they send trajectories to Harbor Hub.

## Gotchas

- **PyYAML is load-bearing** (discovered 2026-09-29): without it, the
  companion skill parser silently drops nested front-matter
  (`x-r4-keywords`/`x-r4-dependencies`), so skill auto-activation never fires
  and the model sees only the umrs/platform-ops whitelist (17 tools instead of
  42+ on file tasks). The lane pins `pyyaml>=6.0` into the bench venv; the
  product-side silent-degradation behavior is unchanged.
- **hello-world with glm-5.3 can legitimately fail**: its instruction wording
  matches no coding-skill keywords, and the score depends on the model
  pulling `activate_skill('code')` from the discovery catalog itself. A 0.0
  there is a harness×model result, not a lane defect — check
  `driver-result.json` (`skills_active`, `n_skill_tools_offered`) before
  blaming plumbing.
- Terminal-Bench 4.0 contains GPU tasks; local docker only supplies GPUs when
  the environment supports them (`--override-gpus`, host NVIDIA runtime). CPU
  tasks are the default lane here.
- Agent runs in TRUST mode with auto-approved tool calls — only against
  pinned, versioned task definitions inside the disposable Harbor container.
- Failure triage (first 5-task sweep, 2026-09-29):
  `Request timed out: The read operation timed out` → raise `--ak r4_timeout_sec`;
  `Command timed out after N seconds` → raise `--ak exec_timeout_sec` and/or
  Harbor `--agent-timeout-multiplier`; reward `0.0` with no exception → read
  the trial's `driver-result.json` for skills/tools/narrative before blaming
  the lane.
