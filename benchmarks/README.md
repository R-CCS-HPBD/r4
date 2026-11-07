# LLM Code Generation Benchmarks

Benchmark suite for evaluating LLMs on code generation tasks, including general
algorithmic problems and HPC-specific parallel programming.

## Quick Start

```bash
# Activate venv and run all benchmarks
source .venv/bin/activate
cd benchmarks
python run_benchmarks.py

# Run with specific models only
python run_benchmarks.py --models DeepSeek-V4-Pro,GLM-5.1

# Run with specific categories only
python run_benchmarks.py --categories unit_test,algorithm

# Run single-shot (pass@1 only)
python run_benchmarks.py --single-shot

# Run with pass@k sampling (k=3)
python run_benchmarks.py --k 3 --temperature 0.7

# Run with 3x repeated passes for statistical rigor
python run_benchmarks.py --repeats 3 --single-shot

# Generate final report from saved results
python report.py --input results/
```

## Architecture

```
benchmarks/
  prompts/              # Prompt datasets
    unit_test.py        # Function synthesis (HumanEval-style)
    algorithm.py        # Algorithmic problems
    code_synthesis.py   # Real-world code synthesis
    optimization.py     # Performance optimization tasks
    parallel.py         # MPI/OpenMP/CUDA parallel tasks
    numerical.py        # Numerical stability problems
    profiling.py        # Profiling & analysis tasks
  sandbox.py            # Secure execution sandbox (Docker/container)
  evaluator.py          # Automated evaluation pipeline
  gateway_client.py     # R4 Gateway HTTP client
  run_benchmarks.py     # Main benchmark runner
  throughput_bench.py   # Streaming decode throughput (TTFT/TPOT/tok/s)
  report.py             # Results aggregation and reporting
  results/              # Output directory for raw results
    *.json              # Per-model-per-run result files
```

## Models

Currently benchmarking:
- **DeepSeek-V4-Pro** — 128k context, tool calling
- **GLM-5.1** — 32k context, fp8 quantization
- **Kimi K2.6** — 128k context, vision + tools
- **gemma-4-31B-it** — 128k context, vision + tools

Planned:
- **Claude Opus** (when available via public provider)

## Metrics Collected

| Metric | Description |
|--------|-------------|
| pass@k | Probability ≥1 correct in k samples |
| compilability | Fraction of outputs that compile/parse |
| execution_time_ms | Runtime of generated code |
| tokens_per_second | Model inference throughput |
| time_to_first_token_ms | Latency to first token |
| code_quality | Cyclomatic complexity, lint warnings |

## Prompt Categories

1. **Unit Test** — Function synthesis with provided test cases
2. **Algorithm** — Classic algorithmic problems (DP, graph, FFT)
3. **Code Synthesis** — Real-world tasks (file I/O, data processing)
4. **Optimization** — Speed improvement of given code
5. **Parallel** — MPI/OpenMP/CUDA/HIP kernel generation
6. **Numerical** — Numerical stability and precision
7. **Profiling** — Performance analysis and instrumentation
