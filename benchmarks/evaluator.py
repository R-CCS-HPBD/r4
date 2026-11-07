"""
Automated evaluation pipeline.

For each benchmark result:
1. Extract code from model output
2. Compile (C/C++) or validate syntax (Python)
3. Run tests in sandbox
4. Measure metrics (pass/fail, timing, code quality)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

try:
    from .gateway_client import ChatResponse, CompletionTiming
    from .sandbox import (
        compile_c_code,
        extract_function_code,
        run_c_binary,
        run_python_sandbox,
    )
except ImportError:
    from gateway_client import ChatResponse, CompletionTiming
    from sandbox import (
        compile_c_code,
        extract_function_code,
        run_c_binary,
        run_python_sandbox,
    )


@dataclass
class EvalResult:
    """Result of evaluating one prompt-model interaction."""

    prompt_id: str
    model: str
    category: str

    # Pass/fail
    compiled: bool | None = None  # None for Python (not applicable)
    passed: bool = False
    total_tests: int = 0
    passed_tests: int = 0

    # Timing
    sandbox_elapsed_ms: float = 0.0
    latency_ms: float = 0.0  # API latency
    tokens_per_second: float = 0.0

    # Output
    output_tokens: int = 0
    prompt_tokens: int = 0

    # Errors
    timeout: bool = False
    error: str | None = None
    compile_error: str | None = None

    # Raw
    raw_output: str = ""
    raw_stdout: str = ""

    # Code quality (basic)
    lines_of_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "model": self.model,
            "category": self.category,
            "compiled": self.compiled,
            "passed": self.passed,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "sandbox_elapsed_ms": round(self.sandbox_elapsed_ms, 2),
            "latency_ms": round(self.latency_ms, 2),
            "tokens_per_second": round(self.tokens_per_second, 2),
            "output_tokens": self.output_tokens,
            "prompt_tokens": self.prompt_tokens,
            "timeout": self.timeout,
            "error": self.error,
            "compile_error": self.compile_error,
            "lines_of_code": self.lines_of_code,
            "raw_output": self.raw_output[:2000],
            "raw_stdout": self.raw_stdout[:2000],
        }


@dataclass
class AggregateResult:
    """Aggregated results for a model across all prompts."""

    model: str
    total_prompts: int
    passed_prompts: int
    pass_rate: float
    avg_latency_ms: float
    avg_tokens_per_second: float
    avg_sandbox_ms: float
    total_output_tokens: int
    total_prompt_tokens: int
    compile_failures: int
    timeouts: int
    per_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_prompt: list[dict[str, Any]] = field(default_factory=list)


def evaluate_nlp_prompt(
    prompt_def: dict,
    response: ChatResponse,
    timing: CompletionTiming,
    model: str,
) -> EvalResult:
    """Evaluate a natural-language prompt (JA/etc.) against expected quality markers.

    Pass criteria: the generated output includes at least 2/3 of the
    keywords listed in the prompt's `expected_ja` field (rounded up when
    fractional). This catches the obvious failures — missing topic, wrong
    language, incomplete recall — while staying robust to phrasing variation.

    `expected_ja` entries may be a single keyword (``str``) or a synonym
    group (``list[str]``); a group counts as one hit when *any* member
    appears in the output.
    """
    content = response.content if response.content else ""
    raw_keywords = list(prompt_def.get("expected_ja", []))
    groups: list[list[str]] = [([kw] if isinstance(kw, str) else list(kw)) for kw in raw_keywords]

    result = EvalResult(
        prompt_id=prompt_def["id"],
        model=model,
        category=prompt_def.get("category", "unknown"),
        latency_ms=timing.total_time_ms,
        tokens_per_second=timing.tokens_per_second,
        output_tokens=timing.output_tokens,
        prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
        raw_output=content[:5000],
    )

    if not groups:
        result.passed = True
        return result

    hits, missing = _keyword_group_hits(groups, content)
    threshold = max(1, (len(groups) * 2 + 2) // 3)  # ceil( (2/3)*n )
    result.passed = hits >= threshold
    if not result.passed:
        result.error = f"quality below threshold ({hits}/{len(groups)} hits, missing: {missing})"
    return result


def _keyword_group_hits(groups: list[list[str]], content: str) -> tuple[int, list[str]]:
    """Count groups with at least one variant present; return (hits, missing)."""
    hits = 0
    missing: list[str] = []
    for group in groups:
        if any(variant in content for variant in group):
            hits += 1
        else:
            missing.append(group[0])
    return hits, missing


def evaluate_python_prompt(
    prompt_def: dict,
    response: ChatResponse,
    timing: CompletionTiming,
    model: str,
) -> EvalResult:
    """Evaluate a Python code generation prompt result.

    Args:
        prompt_def: Prompt definition dict with id, category, test_code, etc.
        response: Chat completion response from model.
        timing: CompletionTiming with latency/token metrics.
        model: Model name.

    Returns:
        EvalResult with pass/fail and metrics.
    """
    result = EvalResult(
        prompt_id=prompt_def["id"],
        model=model,
        category=prompt_def.get("category", "unknown"),
        latency_ms=timing.total_time_ms,
        tokens_per_second=timing.tokens_per_second,
        output_tokens=timing.output_tokens,
        prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
    )

    output = response.content
    result.raw_output = output[:5000]

    # Extract function code
    pattern = prompt_def.get("extraction_pattern")
    code = extract_function_code(output, pattern)
    if not code:
        result.error = "Could not extract function definition from output"
        return result

    result.lines_of_code = len(code.splitlines())

    # Run in sandbox with test code
    test_code = prompt_def.get("test_code", "")
    timeout = prompt_def.get("timeout_sec", 10)

    sandbox_result = run_python_sandbox(code, test_code, timeout)
    result.sandbox_elapsed_ms = sandbox_result.elapsed_ms
    result.raw_stdout = sandbox_result.stdout[:2000]

    if sandbox_result.timed_out:
        result.timeout = True
        result.error = "Execution timed out"
        return result

    if not sandbox_result.success:
        result.error = sandbox_result.error or "Tests failed"
        # Parse test counts from error if possible
        result.passed = False
        return result

    result.passed = True
    result.total_tests = _count_tests(test_code)
    result.passed_tests = result.total_tests
    return result


def evaluate_c_prompt(
    prompt_def: dict,
    response: ChatResponse,
    timing: CompletionTiming,
    model: str,
    work_dir: str | None = None,
) -> EvalResult:
    """Evaluate a C/C++ code generation prompt result.

    Args:
        prompt_def: Prompt definition dict.
        response: Chat completion response.
        timing: CompletionTiming.
        model: Model name.
        work_dir: Working directory for compilation.

    Returns:
        EvalResult.
    """
    result = EvalResult(
        prompt_id=prompt_def["id"],
        model=model,
        category=prompt_def.get("category", "unknown"),
        latency_ms=timing.total_time_ms,
        tokens_per_second=timing.tokens_per_second,
        output_tokens=timing.output_tokens,
        prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
    )

    output = response.content
    result.raw_output = output[:5000]

    # Extract code from markdown code blocks
    code = _extract_code_block(output, prompt_def.get("language", "c"))
    if not code:
        result.error = "Could not extract code from output"
        return result

    result.lines_of_code = len(code.splitlines())

    # Check GPU requirement
    if prompt_def.get("skip_if_no_gpu"):
        result.error = "GPU required — skipping (no GPU available in sandbox)"
        return result

    return _c_compile_run_verify(prompt_def, result, code, work_dir)


def _c_compile_run_verify(
    prompt_def: dict,
    result: EvalResult,
    code: str,
    work_dir: str | None,
) -> EvalResult:
    """Compile the extracted code, run it, and verify expected output."""
    compile_result = compile_c_code(
        code,
        compiler=prompt_def.get("compiler", "gcc"),
        compiler_flags=prompt_def.get("compiler_flags", "-O2"),
        source_ext=".cu" if "cuda" in prompt_def.get("subcategory", "") else ".c",
        work_dir=work_dir,
    )

    if not compile_result.success:
        result.compiled = False
        result.compile_error = compile_result.error
        result.error = compile_result.error
        return result

    result.compiled = True

    run_result = run_c_binary(
        compile_result.binary_path,
        timeout_sec=prompt_def.get("timeout_sec", 15),
        env=prompt_def.get("run_env"),
        run_cmd_template=prompt_def.get("run_cmd"),
    )

    result.sandbox_elapsed_ms = run_result.elapsed_ms
    result.raw_stdout = run_result.stdout[:2000]

    if run_result.timed_out:
        result.timeout = True
        result.error = "Execution timed out"
        return result

    if not run_result.success:
        result.error = f"Exit code {run_result.exit_code}: {run_result.stderr[:500]}"
        return result

    _c_check_expected_output(prompt_def, result, run_result.stdout)
    return result


def _c_check_expected_output(prompt_def: dict, result: EvalResult, stdout: str) -> None:
    """Set ``result.passed``/``error`` from expected-output rules."""
    expected = prompt_def.get("expected_output")
    expected_pattern = prompt_def.get("expected_output_pattern")
    matched = (expected and expected.strip() in stdout) or (
        expected_pattern and re.search(expected_pattern, stdout)
    )
    if matched or (expected is None and expected_pattern is None):
        result.passed = True
    else:
        result.error = f"Output mismatch. Got: {stdout[:200]}"


def _extract_code_block(output: str, language: str = "c") -> str | None:
    """Extract code from markdown code block."""
    # Try language-specific block first
    for lang in [language, ""]:
        pattern = rf"```{lang}\s*\n(.*?)```"
        match = re.search(pattern, output, re.DOTALL)
        if match:
            return match.group(1)

    # Fallback: any code block
    match = re.search(r"```\w*\s*\n(.*?)```", output, re.DOTALL)
    if match:
        return match.group(1)

    # No code block — assume entire output is code
    if "#include" in output or "int main" in output:
        return output

    return None


def _count_tests(test_code: str) -> int:
    """Count test functions in pytest-style test code."""
    return len(re.findall(r"^def test_\w+", test_code, re.MULTILINE))


def _empty_aggregate(model: str) -> AggregateResult:
    """Zeroed aggregate for an empty result set."""
    return AggregateResult(
        model=model,
        total_prompts=0,
        passed_prompts=0,
        pass_rate=0.0,
        avg_latency_ms=0.0,
        avg_tokens_per_second=0.0,
        avg_sandbox_ms=0.0,
        total_output_tokens=0,
        total_prompt_tokens=0,
        compile_failures=0,
        timeouts=0,
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate_results(
    eval_results: list[EvalResult],
    model: str,
) -> AggregateResult:
    """Aggregate evaluation results for a single model.

    Args:
        eval_results: List of EvalResult objects.
        model: Model name.

    Returns:
        AggregateResult with summary statistics.
    """
    total = len(eval_results)
    if total == 0:
        return _empty_aggregate(model)

    return AggregateResult(
        model=model,
        total_prompts=total,
        **_aggregate_stats(eval_results, total),
        per_category=_per_category_breakdown(eval_results),
        per_prompt=[r.to_dict() for r in eval_results],
    )


def _aggregate_stats(eval_results: list[EvalResult], total: int) -> dict[str, Any]:
    """Compute the numeric summary fields for :func:`aggregate_results`."""
    passed = compile_failures = timeouts = 0
    out_tok = prompt_tok = 0
    latencies: list[float] = []
    tps_vals: list[float] = []
    sandbox_vals: list[float] = []
    for r in eval_results:
        passed += 1 if r.passed else 0
        compile_failures += 1 if r.compile_error else 0
        timeouts += 1 if r.timeout else 0
        out_tok += r.output_tokens
        prompt_tok += r.prompt_tokens
        if r.latency_ms > 0:
            latencies.append(r.latency_ms)
        if r.tokens_per_second > 0:
            tps_vals.append(r.tokens_per_second)
        if r.sandbox_elapsed_ms > 0:
            sandbox_vals.append(r.sandbox_elapsed_ms)
    return {
        "passed_prompts": passed,
        "pass_rate": passed / total * 100,
        "avg_latency_ms": _mean(latencies),
        "avg_tokens_per_second": _mean(tps_vals),
        "avg_sandbox_ms": _mean(sandbox_vals),
        "total_output_tokens": out_tok,
        "total_prompt_tokens": prompt_tok,
        "compile_failures": compile_failures,
        "timeouts": timeouts,
    }


def _per_category_breakdown(eval_results: list[EvalResult]) -> dict[str, dict[str, Any]]:
    """Group pass/fail counts per prompt category."""
    per_category: dict[str, dict[str, Any]] = {}
    for r in eval_results:
        bucket = per_category.setdefault(r.category, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        bucket["passed" if r.passed else "failed"] += 1
    return per_category
