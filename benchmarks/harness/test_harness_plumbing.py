"""Deterministic plumbing tests for the harness benchmark runner.

Echo adapter only — no LLM calls. Validates suite loading, fail-closed
registries, workdir isolation, and the grader path end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.harness.adapters import load_adapters
from benchmarks.harness.adapters.copilot import CopilotAdapter
from benchmarks.harness.cases import HarnessCase, load_suite
from benchmarks.harness.graders import load_graders
from benchmarks.harness.runner import run_case


def _write_suite(tmp_path: Path, cases: list[dict]) -> Path:
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"name": "plumbing", "cases": cases}))
    return suite


def test_load_suite_and_graders(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path,
        [
            {
                "id": "c1",
                "prompt": "make out.txt",
                "graders": ["status_success", "artifact_present"],
                "expected_outputs": ["out.txt"],
            }
        ],
    )
    cases = load_suite(suite)
    assert cases[0].case_id == "c1"
    assert cases[0].expected_outputs == ("out.txt",)
    load_graders(list(cases[0].grader_names))


def test_load_suite_rejects_unknown_case_keys(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path, [{"id": "c1", "prompt": "x", "typo_key": 1}])
    with pytest.raises(KeyError, match="unknown keys"):
        load_suite(suite)


def test_unknown_grader_fails_closed() -> None:
    with pytest.raises(KeyError, match="unknown grader"):
        load_graders(["nonexistent"])


def test_unknown_adapter_fails_closed() -> None:
    with pytest.raises(KeyError, match="unknown harness"):
        load_adapters(["nonexistent"])


def test_copilot_adapter_availability_is_explicit() -> None:
    # Either the CLI is installed (None = usable) or a clear reason is given.
    reason = CopilotAdapter().available()
    assert reason is None or "copilot CLI not on PATH" in reason


def test_echo_adapter_passes_artifact_grading(tmp_path: Path) -> None:
    case = HarnessCase(
        case_id="c1",
        prompt="make out.txt",
        grader_names=("status_success", "metrics_sanity", "artifact_present"),
        expected_outputs=("out.txt",),
    )
    (adapter,) = load_adapters(["echo"])
    outcome = run_case(adapter, case, tmp_path / "work", run_index=0)
    assert outcome.passed, [g.note for g in outcome.grades]
    assert (tmp_path / "work" / "echo" / "c1-run0" / "out.txt").is_file()
    assert (tmp_path / "work" / "echo" / "c1-run0" / "bench.json").is_file()


def test_setup_files_and_test_pass_grader(tmp_path: Path) -> None:
    case = HarnessCase(
        case_id="c2",
        prompt="run the test",
        setup_files={"check.py": "print('ok')\n"},
        grader_names=("status_success", "test_pass"),
        test_command="python check.py",
    )
    (adapter,) = load_adapters(["echo"])
    outcome = run_case(adapter, case, tmp_path / "work", run_index=0)
    assert outcome.passed, [g.note for g in outcome.grades]


def test_content_match_grader_failure_is_recorded(tmp_path: Path) -> None:
    case = HarnessCase(
        case_id="c3",
        prompt="make out.txt with content",
        grader_names=("status_success", "content_match"),
        expected_outputs=("out.txt",),
        expect={"content_match": {"out.txt": ["this pattern does not exist"]}},
    )
    (adapter,) = load_adapters(["echo"])
    outcome = run_case(adapter, case, tmp_path / "work", run_index=0)
    assert not outcome.passed
    content_grade = next(g for g in outcome.grades if g.name == "content_match")
    assert "missing" in content_grade.note


def test_r4_adapter_ablation_env_maps_layers() -> None:
    from benchmarks.harness.adapters.r4_companion import R4CompanionAdapter

    adapter = R4CompanionAdapter()
    assert adapter.supports_ablation("full")
    assert adapter.supports_ablation("no_steering")
    assert not R4CompanionAdapter().ablation_env()  # default full cell: no flags

    adapter.ablation = "no_steering"
    env = adapter.ablation_env()
    assert env["R4_HARNESS_ABLATE_STEERING"] == "1"
    assert env["R4_HARNESS_ABLATION_LABEL"] == "no_steering"

    adapter.ablation = "no_guides"
    assert adapter.ablation_env()["R4_HARNESS_ABLATE_GUIDES"] == "1"
    adapter.ablation = "no_memory"
    assert adapter.ablation_env()["R4_HARNESS_ABLATE_MEMORY"] == "1"


def test_non_r4_adapters_support_full_cell_only(tmp_path: Path) -> None:
    (adapter,) = load_adapters(["echo"])
    assert adapter.supports_ablation("full")
    assert not adapter.supports_ablation("no_steering")
    assert adapter.ablation_env() == {}


def test_run_suite_skips_unsupported_cells(tmp_path: Path) -> None:
    from benchmarks.harness.runner import run_suite

    case = HarnessCase(
        case_id="c1",
        prompt="make out.txt",
        grader_names=("status_success", "artifact_present"),
        expected_outputs=("out.txt",),
    )
    (adapter,) = load_adapters(["echo"])
    outcomes = run_suite([adapter], [case], tmp_path / "work", ablations=("full", "no_steering"))
    # echo cannot ablate -> only the full cell ran
    assert len(outcomes) == 1
    assert outcomes[0].ablation == "full"


def test_ablation_cell_tags_outcome_and_filename() -> None:
    from benchmarks.harness.runner import CaseOutcome, _outcome_dict, render_summary

    outcome = CaseOutcome(
        case_id="c1",
        harness="r4_companion",
        run_index=0,
        status="success",
        ablation="no_steering",
        metrics={"ablation": "no_steering", "llm_calls": 3},
    )
    assert _outcome_dict(outcome)["ablation"] == "no_steering"
    table = render_summary([outcome])
    assert "| ablation |" in table
    assert "no_steering" in table


def _write_run(root: Path, stamp: str, cells: list[tuple[bool, str]]) -> None:
    out = root / stamp / "suiteX"
    out.mkdir(parents=True)
    for idx, (passed, ablation) in enumerate(cells):
        payload = {
            "case": "c1",
            "harness": "r4_companion",
            "run_index": idx,
            "ablation": ablation,
            "status": "success",
            "passed": passed,
            "metrics": {},
            "grades": [],
            "error": None,
        }
        (out / f"cell-{idx}.json").write_text(json.dumps(payload))


def test_trend_detects_regression(tmp_path: Path) -> None:
    from benchmarks.harness.trend import compare

    _write_run(tmp_path, "20260101T000000Z", [(True, "full"), (True, "full")])
    _write_run(tmp_path, "20260102T000000Z", [(False, "full"), (False, "full")])
    _, regressions = compare(tmp_path, "suiteX", threshold=0.05, history=8)
    assert regressions, "0% after 100% must alert"
    assert "r4_companion/full" in regressions[0]


def test_trend_clean_within_threshold(tmp_path: Path) -> None:
    from benchmarks.harness.trend import compare

    _write_run(tmp_path, "20260101T000000Z", [(True, "full"), (True, "no_guides")])
    _write_run(tmp_path, "20260102T000000Z", [(True, "full"), (True, "no_guides")])
    trend, regressions = compare(tmp_path, "suiteX", threshold=0.05, history=8)
    assert not regressions
    assert any("no_guides" in line for line in trend)


def test_trend_single_run_never_alerts(tmp_path: Path) -> None:
    from benchmarks.harness.trend import compare

    _write_run(tmp_path, "20260101T000000Z", [(False, "full")])
    _, regressions = compare(tmp_path, "suiteX", threshold=0.05, history=8)
    assert not regressions


# --- adapters (B5, 2026-10-05) ---------------------------------------------------------


def test_claude_code_adapter_renders_its_json_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_case reached _render_result without ``self.`` (NameError on every case)."""
    import subprocess

    from benchmarks.harness.adapters import claude_code

    stdout = json.dumps(
        {
            "result": "done",
            "num_turns": 4,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "total_cost_usd": 0.01,
            "is_error": False,
        }
    )
    monkeypatch.setattr(
        claude_code.subprocess,
        "run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=""),
    )
    case = HarnessCase(case_id="c1", prompt="hi")
    result = claude_code.ClaudeCodeAdapter().run_case(case, tmp_path, run_index=0)
    assert result.status == "success"
    assert result.response_text == "done"
    assert result.metrics["llm_calls"] == 4
    assert result.metrics["total_tokens"] == 120


def _oc_event(event_type: str, **part: object) -> str:
    return json.dumps({"type": event_type, "sessionID": "s1", "part": {"type": event_type, **part}})


def test_opencode_v2_events_count_every_step_and_sum_token_parts() -> None:
    """OpenCode v2.0.18: no ``tokens.total`` and no step_finish for the last step."""
    from benchmarks.harness.adapters.opencode import parse_opencode_events

    tokens = {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 900, "write": 2}}
    stdout = "\n".join(
        [
            "INFO starting",  # not an event
            _oc_event("step_start", messageID="m1"),
            _oc_event("tool_use", tool="read"),
            _oc_event("step_finish", messageID="m1", tokens=tokens),
            _oc_event("step_start", messageID="m2"),
            _oc_event("step_finish", messageID="m2", tokens=tokens),
            _oc_event("step_start", messageID="m3"),
            _oc_event("text", text="the answer"),
        ]
    )
    parsed = parse_opencode_events(stdout)
    assert parsed["response_text"] == "the answer"
    assert parsed["metrics"] == {
        "llm_calls": 3,
        "total_tokens": 2 * (100 + 10 + 5 + 900 + 2),
        "cache_read_tokens": 1800,
        "unfinished_steps": 1,
    }


def test_opencode_v1_events_keep_the_reported_total() -> None:
    from benchmarks.harness.adapters.opencode import parse_opencode_events

    tokens = {"total": 50, "input": 30, "output": 20, "cache": {"read": 10}}
    stdout = "\n".join(
        [_oc_event("step_finish", tokens=tokens), _oc_event("step_finish", tokens=tokens)]
    )
    metrics = parse_opencode_events(stdout)["metrics"]
    assert metrics["llm_calls"] == 2
    assert metrics["total_tokens"] == 100
    assert metrics["cache_read_tokens"] == 20
    assert metrics["unfinished_steps"] == 0
