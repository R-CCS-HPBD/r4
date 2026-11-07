# Harness Benchmarks

Harness-level comparison: **hold the model constant, vary the harness**.
Answers "is the R4 harness getting better, and does it keep pace with
commercial/OSS agent harnesses?" — not "which LLM is best" (that is the
parent `benchmarks/` package).

Plan: `docs/development/harness-benchmarking-plan.md`
Capability matrix (Phase 0 freeze): `capability_matrix.json`

## Quick start

```bash
source .venv/bin/activate

# Deterministic plumbing check (no LLM, no tokens)
python -m pytest benchmarks/harness/test_harness_plumbing.py -v

# Real comparison: R4 companion vs Claude Code headless, model pinned
python -m benchmarks.harness.runner \
    --suite benchmarks/harness/suites/sample_coding.json \
    --harnesses r4_companion,claude_code \
    --runs 3 --keep-workdirs

# Ablation grid (harness-layer contributions; each label = layer OFF)
python -m benchmarks.harness.runner \
    --suite benchmarks/harness/suites/sample_coding.json \
    --harnesses r4_companion \
    --ablations full,no_steering,no_guides,no_memory \
    --runs 3 --model <pinned-model>

# Trend compare (weekly lane alert step): exit 1 on >5% pass-rate drop
python -m benchmarks.harness.trend \
    --root benchmarks/results/harness --suite sample_coding \
    --threshold 0.05 --history 8
```

Ablation cells map to companion harness layers
(`r4/companion/ablation.py`): `no_steering` drops SteeringLoop lessons
(injection + learning), `no_guides` drops FeedforwardGuides domain guides
and workflow templates, `no_memory` drops the remembered-context
injection. Env probe and progress orientation stay on in every cell
(session continuity, not steering). Non-r4 harnesses run `full` only and
are skipped per other cells with a note. Weekly lane:
`.github/workflows/harness-bench-weekly.yaml` (needs repo secret
`R4_BENCH_SOURCES_JSON`).

## Harnesses

| Name | Driver | Status |
|---|---|---|
| `r4_companion` | `CompanionSession` subprocess driver (TRUST mode, sandboxed workdir) | usable |
| `claude_code` | `claude -p --output-format json --dangerously-skip-permissions --allowedTools Write,Read,Edit,Bash` | usable when CLI installed; needs Anthropic API key (pay-as-you-go) or gateway: BYOM via `R4_BENCH_CLAUDE_BASE_URL` — **point at `benchmarks/harness/same_model/anthropic_bridge.py`** (full Anthropic↔OpenAI message/tool translation, Anthropic SSE streaming, forces the target model on every body incl. session-title sub-calls, 5xx retry before the first byte; boot it with `same_model/start_proxies.sh`, or `.venv/bin/python benchmarks/harness/same_model/anthropic_bridge.py --port 8330` for debugging; it resolves upstream URL + key from the r4 public-source registry through `anthropic_shim._resolve_config`: `R4_BENCH_SHIM_SOURCE`, default `rikyu`, entry `kind: public` + `provider: openai`/unset; overrides `R4_BENCH_SHIM_UPSTREAM`, `R4_BENCH_SHIM_API_KEY`, `R4_BENCH_SHIM_TARGET_MODEL`). **`benchmarks/harness/anthropic_shim.py` no longer serves (2026-10-05):** it answered `stream: true` with plain JSON and forwarded `tool_use`/`tool_result` blocks raw, so Claude Code tool loops died on turn 2 — `claude_code` results produced through it are likely invalid. **Do not set `R4_BENCH_CLAUDE_AUTH_TOKEN` for BYOM lanes** — `ANTHROPIC_AUTH_TOKEN` makes the CLI refuse start (connectors-disabled rc=1). |
| `opencode` | `opencode run <prompt> --format json --auto` | usable when CLI installed; free/open-source; native BYOM (`-m provider/model`, point provider at our LiteLLM) |
| `aider` | `aider --yes-always --no-git --message ...` | usable when CLI installed; BYOM |
| `copilot` | `copilot -p <prompt> --allow-all-tools` | usable when CLI installed + Copilot subscription; **no BYOM** — vendor-best lane only, annotate actual model |
| `echo` | oracle placeholder writer | plumbing/CI only, NEVER for comparisons |

## Suites

Suite JSON is harness-agnostic. Per case: `id`, `prompt`, optional
`setup.files`, `graders`, `expected_outputs`, `test_command`,
`expect.content_match`, `repeat`, `max_sec`. Unknown keys fail closed.

Graders evaluate the workspace left behind, never harness logs:
`status_success`, `metrics_sanity`, `artifact_present`, `test_pass`,
`content_match`.

## Metrics

From authoritative surfaces only (ADR-020 section 13): adapter-reported
`llm_calls`, `total_tokens`, `wallclock_sec`, `compaction_calls`,
`tool_calls`, `cost_usd` where available. Black-box harnesses (aider)
report wall-clock only.

## SWE-bench lane

`benchmarks/harness/swe/` — real SWE-bench Verified instances under
claw-swe-bench fairness rules: ONE prompt template for all harnesses,
future-commit stripping (anti gold-patch leakage), web tools denied
(containers stay on the default bridge — the model API endpoint needs
egress; provider APIs reachable, web banned per claw rules),
runner-side `git diff` patch collection, FAIL_TO_PASS pytest verdict
(resolution is ONLY reported by the runner, never by the agent).

Host requirements: linux x86_64 with docker/podman + SWE-bench instance
images (`swebench/sweb.eval.x86_64.<iid with __->_1776_>:latest`), plus
per-harness linux runtimes: `R4BENCH_OPENCODE_LINUX_BIN` (opencode linux x64
binary) and `R4BENCH_R4_LINUX_VENV` (r4 linux x86_64 venv whose base
interpreter lives in a sibling `python312/` python-build-standalone dir —
see bootstrap note below). Benchmark-quality comparisons REQUIRE native
x86_64: qemu/Rosetta emulation runs the CPython-based r4 driver acceptably
but is an order of magnitude too slow for node-based harnesses (opencode).

```bash
python -m benchmarks.harness.swe.runner \
    --harnesses r4_companion,opencode --count 3 \
    --model-map "r4_companion=kimi-k3@rikyu,opencode=rikyu/kimi-k3"

# deterministic lane checks (no docker, no LLM)
python -m pytest benchmarks/harness/swe/test_swe_plumbing.py -v
```

Runtime bootstrap (build once per host):

```bash
# 1. standalone python + venv (portable across containers)
curl -fsSL https://github.com/astral-sh/python-build-standalone/releases/latest/download/cpython-3.12.14+20260814-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz | tar -xz -C ~/.r4bench
mv ~/.r4bench/python ~/.r4bench/python312
docker run --rm --platform linux/amd64 -v ~/.r4bench:/out -v $PWD:/r4platform:ro \
  --entrypoint /out/python312/bin/python3.12 python:3.12-slim -m venv /out/venv312
docker run --rm --platform linux/amd64 -v ~/.r4bench:/out -v $PWD:/r4platform:ro \
  --entrypoint /out/venv312/bin/python python:3.12-slim -m pip install \
  /r4platform/r4-contracts /r4platform/science /r4platform/packages/r4-teamengine-spi /r4platform/r4

# 2. opencode linux binary
curl -fsSL https://github.com/sst/opencode/releases/latest/download/opencode-linux-x64.tar.gz | tar -xz -C /tmp
mkdir -p ~/.r4bench/bin && cp /tmp/opencode ~/.r4bench/bin/opencode
```

## Same-model research-question lane

`benchmarks/harness/same_model/` — R4 vs Claude Code vs OpenCode on one model
(glm-5.3 on rikyu), byte-identical prompts, one bwrap sandbox, proxy-side
accounting, blind glm-5.3 grading, and patched R4 variants for per-fix
ablations. Code only: the research question set, corpus, answers and grades
stay outside git under `$R4_SAME_MODEL_BENCH` (see that README).

## Terminal-Bench lane

`benchmarks/harness/terminal/` — the R4 companion under Harbor on
Terminal-Bench 2.1 / 4.0. `model_proxy.py` keeps the model key on the host
(task containers get a per-run token) and streams long reasoning turns past
the rikyu gateway's 600 s idle cut; `tb4_public_compare.py` sets a run against
the public TB 4.0 results. Results land in `benchmarks/results/terminal-bench/`.

## Security

The r4_companion driver runs in `PermissionMode.TRUST` and Claude Code runs
with `--dangerously-skip-permissions`. Every case gets an isolated,
freshly-created workdir. Only run version-pinned, reviewed suites — never
point the runner at prompts from untrusted sources or live checkouts.
