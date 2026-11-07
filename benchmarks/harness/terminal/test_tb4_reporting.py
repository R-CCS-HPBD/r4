"""TB 4.0 reporting (plan tb4-harness-efficiency-2026-10-24 §3.3, R1-R3): no docker, no model.

Synthetic harbor run trees stand in for real runs: two lanes (R4 with native tool
calls and a transcript, terminus-2 with a trajectory), a live job with a running
trial, a cancelled trial, an Epoch-flagged task and proxy rows with and without
the cut-stream estimate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.harness.terminal import tb4_combined_report as report
from benchmarks.harness.terminal import tb4_public_compare as public
from benchmarks.harness.terminal import tb4_trial_diagnostics as diag


def _trial(job: Path, task: str, reward: float | None, **extra) -> Path:
    trial_dir = job / f"{task}__{job.parent.name[:3]}{len(task)}"
    (trial_dir / "agent").mkdir(parents=True)
    result = {
        "task_name": f"terminal-bench/{task}",
        "trial_name": trial_dir.name,
        "started_at": "2026-10-24T01:00:00Z",
        "finished_at": extra.pop("finished_at", "2026-10-24T02:00:00Z"),
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": extra.pop("exception_info", None),
    }
    (trial_dir / "result.json").write_text(json.dumps(result))
    return trial_dir


def _job(root: Path, finished: bool) -> Path:
    job = root / "2026-10-24__00-58-24"
    job.mkdir(parents=True)
    (job / "job.log").write_text("")
    stamp = "2026-10-24T09:00:00" if finished else None
    (job / "result.json").write_text(json.dumps({"n_total_trials": 63, "finished_at": stamp}))
    return job


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _r4_run(tmp_path: Path) -> Path:
    run = tmp_path / "tb40-full"
    job = _job(run / "r4", finished=False)
    for task, reward in (("aa", 1.0), ("bb", 0.0), ("cc", 1.0), ("ks-solver-cpp", 1.0)):
        _trial(job, task, reward)
    dd = _trial(job, "dd", 0.0)
    _jsonl(
        dd / "agent" / "transcript.jsonl",
        [
            {"ts": "2026-10-24T01:00:00Z", "kind": "model_round", "data": {}},
            {
                "ts": "2026-10-24T01:00:01Z",
                "kind": "tool_call",
                "data": {
                    "name": "shell_exec",
                    "arguments": json.dumps({"command": "cd /app && make; ./t || echo no"}),
                    "tool_call_id": "c1",
                },
            },
            {
                "ts": "2026-10-24T01:02:01Z",
                "kind": "tool_result",
                "data": {
                    "name": "shell_exec",
                    "result": "timed out after 120 s",
                    "tool_call_id": "c1",
                },
            },
            {"ts": "2026-10-24T01:02:05Z", "kind": "model_round", "data": {}},
        ],
    )
    _trial(job, "ff", None, exception_info={"exception_type": "CancelledError"})
    (job / "ee__run1").mkdir()  # live trial: no result yet
    _jsonl(
        run / "model-proxy.jsonl",
        [
            {"status": 200, "dt_s": 11.0, "ttft_s": 1.0, "prompt_tokens": 1000,
             "completion_tokens": 400, "cached_tokens": 900, "finish": "tool_calls",
             "tool_calls": 1},
            {"status": 200, "dt_s": 5.0, "prompt_tokens": 1000, "completion_tokens": 100,
             "cached_tokens": 900, "finish": "stop", "tool_calls": 0},
            {"status": 499, "dt_s": 600.0, "cut": True, "relayed_chars": {"reasoning": 40000},
             "usage_estimated": {"completion_tokens": 10000}},
            {"status": 499, "dt_s": 600.0},  # an older log: no estimate
        ],
    )  # fmt: skip
    return run


def _terminus_run(tmp_path: Path) -> Path:
    run = tmp_path / "tb40-terminus-full"
    job = _job(run / "terminus", finished=True)
    for task, reward in (("aa", 1.0), ("bb", 1.0), ("cc", 0.0), ("ks-solver-cpp", 0.0)):
        _trial(job, task, reward)
    dd = _trial(job, "dd", 0.0)
    steps = [
        {"source": "user"},
        {
            "source": "agent",
            "tool_calls": [
                {
                    "function_name": "bash_command",
                    "arguments": {"keystrokes": "ls\n", "duration": 1},
                },
                {
                    "function_name": "bash_command",
                    "arguments": {"keystrokes": "make && make test\n", "duration": 59},
                },
            ],
        },
        {"source": "agent", "tool_calls": []},
    ]
    (dd / "agent" / "trajectory.json").write_text(json.dumps({"steps": steps}))
    (job / "gg__run1").mkdir()  # the job ended without this trial's result
    _jsonl(
        run / "model-proxy.jsonl",
        [{"status": 200, "dt_s": 10.0, "ttft_s": None, "prompt_tokens": 500,
          "completion_tokens": 200, "cached_tokens": 0, "finish": "stop", "tool_calls": 0}],
    )  # fmt: skip
    return run


@pytest.fixture
def lanes(tmp_path: Path) -> tuple[report.Lane, report.Lane]:
    return (
        report.load_lane("r4", [_r4_run(tmp_path)]),
        report.load_lane("terminus-2", [_terminus_run(tmp_path)]),
    )


# ── R1: public rows ────────────────────────────────────────────────────────────


class TestPublicRows:
    def test_opencode_row_dated_notes_and_derived_label(self) -> None:
        text = "\n".join(public.public_section())
        assert "| AA Coding Agents — OpenCode | 40.00 |" in text
        assert "re-verified 2026-10-24" in text
        assert "as recorded 2026-10-03; not shown on the source page 2026-10-24" in text
        assert "derived: 8.7B tok" in text and "26.4M" in text
        assert "claude-code-vs-opencode" in text  # the OpenCode row's source

    def test_public_rows_keep_their_shape(self) -> None:
        """tb4_run_diff unpacks (name, score, spread)."""
        assert all(len(row) == 3 for row in public.PUBLIC)
        assert ("AA Coding Agents — OpenCode", 40.0, "–") in public.PUBLIC

    def test_recorded_values_are_marked(self) -> None:
        by_name = {name: spread for name, _, spread in public.PUBLIC}
        assert any("†" in name for name in by_name)  # board 5 × 66 / AA 41.92
        assert "†" in by_name["Vals.ai — mini-swe-agent, max effort, 3 runs"]


# ── R3: defect tags ────────────────────────────────────────────────────────────


class TestDefectTags:
    def test_epoch_lists(self) -> None:
        kinds = list(diag.EPOCH_FLAGGED.values())
        assert (kinds.count("fp"), kinds.count("fn")) == (19, 11)
        assert "epoch.ai/benchmarks/terminal-bench-4/review" in diag.EPOCH_SOURCE
        assert "preferencemodel.com" in diag.PM_SOURCE

    @pytest.mark.parametrize(
        ("task", "tags"),
        [
            ("terminal-bench/ks-solver-cpp", ["epoch-fp"]),
            ("cargo-flight-dispatch", ["epoch-fn"]),
            ("biped-contact-dynamics", ["pm-trap"]),
            ("wal-recovery-ordering", ["epoch-fp", "pm-trap"]),
            ("music-harmony", ["epoch-fn", "pm-note"]),
            ("photonic-waveguide-routing", []),
        ],
    )
    def test_task_tags(self, task: str, tags: list[str]) -> None:
        assert diag.task_tags(task) == tags

    def test_diagnose_record_carries_tags(self, tmp_path: Path) -> None:
        job = _job(tmp_path / "r4", finished=True)
        trial_dir = _trial(job, "cumulative-layout-shift", 0.0)
        trial = json.loads((trial_dir / "result.json").read_text())
        rec = diag.diagnose("terminal-bench/cumulative-layout-shift", trial, trial_dir, "Media", [])
        assert rec["tags"] == ["epoch-fn", "pm-trap"]
        assert "tags" in diag.markdown([rec]).splitlines()[0]


# ── R2: combined report ────────────────────────────────────────────────────────


_FAILED = {"verifier_result": {"rewards": {"reward": 0.0}}}


class TestStatistics:
    @pytest.mark.parametrize(
        ("a", "b", "p"),
        [(12, 3, 0.0352), (11, 4, 0.1185), (5, 0, 0.0625), (0, 0, 1.0), (2, 1, 1.0)],
    )
    def test_mcnemar_exact(self, a: int, b: int, p: float) -> None:
        assert report.mcnemar_exact(a, b) == pytest.approx(p, abs=5e-4)
        assert report.mcnemar_exact(b, a) == report.mcnemar_exact(a, b)

    @pytest.mark.parametrize(
        ("command", "n"),
        [
            ("cd /app && make; ./t || echo no", 4),
            ("ls\n", 1),
            ("cat > f.py <<'EOF'\na = 1; b = 2\nif a and b:\n    pass\nEOF\npython f.py", 2),
            ("", 0),
        ],
    )
    def test_shell_segments(self, command: str, n: int) -> None:
        assert report.shell_segments(command) == n

    @pytest.mark.parametrize(
        ("trial", "status", "category"),
        [
            ({"verifier_result": {"rewards": {"reward": 1.0}}}, None, "solved"),
            ({"exception_info": {"exception_type": "AgentTimeoutError"}}, None, "timeout"),
            (
                {"exception_info": {"exception_type": "NonZeroAgentExitCodeError",
                                    "exception_message": "Command failed (exit -9): x"}},
                None,
                "infra",
            ),
            ({"exception_info": {"exception_type": "NonZeroAgentExitCodeError"}}, None, "harness"),
            ({"task_name": "terminal-bench/aa", **_FAILED}, "error", "harness"),
            ({"task_name": "terminal-bench/cargo-flight-dispatch", **_FAILED}, "success", "flagged-task"),
            # review 2026-10-24: an Epoch false positive accepts wrong answers, so failing it is real
            ({"task_name": "terminal-bench/ks-solver-cpp", **_FAILED}, "success", "model"),
            ({"exception_info": {"exception_type": "VerifierTimeoutError"}}, None, "verifier-timeout"),
            ({"task_name": "terminal-bench/aa", **_FAILED}, "success", "model"),
            ({"task_name": "terminal-bench/aa"}, "success", "infra"),  # no verifier result
        ],
    )  # fmt: skip
    def test_failure_category(self, trial: dict, status: str | None, category: str) -> None:
        assert report.failure_category(trial, status) == category


class TestCombinedReport:
    def test_lane_progress_on_partial_runs(self, lanes) -> None:
        r4, term = lanes
        assert set(r4.trials) == {"aa", "bb", "cc", "dd", "ks-solver-cpp"}
        assert (r4.running, r4.cancelled, r4.abandoned) == ({"ee"}, {"ff"}, set())
        assert (term.running, term.abandoned) == (set(), {"gg"})
        assert report.started(r4) == 7

    def test_scores_with_and_without_flagged_tasks(self, lanes) -> None:
        rows = {label: (k, n) for label, k, n in report.score_rows(lanes[0])}
        assert rows["finished trials"] == (3, 5)
        assert rows["/63 runnable"] == (3, 63)
        assert rows["/66 (3 GPU tasks = 0)"] == (3, 66)
        assert rows["finished, unflagged"] == (2, 4)  # ks-solver-cpp is an Epoch fp
        assert rows["/35 runnable unflagged"] == (2, 35)
        assert rows["/36 unflagged"] == (2, 36)

    def test_paired_counts(self, lanes) -> None:
        r4, term = lanes
        assert report.paired_counts(r4, term) == (5, 1, 2, 1)
        assert report.paired_counts(r4, term, unflagged=True) == (4, 1, 1, 1)

    def test_proxy_tokens_cuts_and_speed(self, lanes) -> None:
        p = report.proxy_stats(lanes[0].rows)
        assert (p["prompt"], p["completion"], p["cut_estimate"]) == (2000, 500, 10000)
        assert (p["cuts"], p["cuts_unestimated"], p["cut_min"]) == (2, 1, 20.0)
        assert p["decode_tps"] == pytest.approx(40.0)  # 400 / (11 - 1)
        assert p["cache_hit"] == pytest.approx(0.9)
        assert p["native_tools"] and p["no_tool_replies"] == 1
        assert not report.proxy_stats(lanes[1].rows)["native_tools"]

    def test_log_stats_same_units_for_both_lanes(self, lanes) -> None:
        r4, term = (report.log_stats(lane) for lane in lanes)
        assert (r4["requests"], r4["shell_calls"], r4["segments"]) == (2, 1, 4)
        assert (r4["blocking_s"], r4["shell_timeouts"]) == (120, 1)
        assert (term["requests"], term["shell_calls"], term["segments"]) == (2, 2, 3)
        assert (term["blocking_s"], term["no_action"]) == (60, 1)

    def test_render(self, lanes) -> None:
        text = report.render(list(lanes))
        assert "## Paired: r4 vs terminus-2" in text
        assert "| all tasks | 5 | 1 | 2 | 1 | 1.000 | no detectable difference |" in text
        assert "| ks-solver-cpp | 1 | solved | 0 | model | epoch-fp |" in text
        assert "| AA Coding Agents — OpenCode | 40.00 |" in text
        assert "No `run-meta.json`" in text

    @staticmethod
    def _cells(lane: report.Lane) -> dict[str, str]:
        return dict(zip(report.EFFICIENCY_HEADER, report.efficiency_row(lane), strict=True))

    def test_efficiency_row_r4(self, lanes) -> None:
        cells = self._cells(lanes[0])
        assert cells["Tasks started / attempts"] == "7 / 7"
        assert cells["Shell segments / request"] == "2.00"
        assert cells["Cut requests"] == "2 (1 unestimated)"
        assert cells["No-action replies"] == "1"
        assert cells["Agent / shell timeouts"] == "0 / 1"

    def test_efficiency_row_terminus(self, lanes) -> None:
        cells = self._cells(lanes[1])
        assert cells["Shell segments / request"] == "1.50"
        assert cells["No-action replies"] == "1"  # from the trajectory, not the proxy
        assert cells["Blocking or wait min"] == "1"

    def test_cli_writes_out(self, tmp_path: Path, capsys) -> None:
        r4, term = _r4_run(tmp_path), _terminus_run(tmp_path)
        out = tmp_path / "report.md"
        assert report.main(["--lane", f"r4={r4}", "--lane", f"t={term}", "--out", str(out)]) == 0
        assert out.read_text() == capsys.readouterr().out

    def test_empty_live_run(self, tmp_path: Path) -> None:
        """A run with no finished trial yet still renders (M0 at its start)."""
        run = tmp_path / "live"
        (_job(run / "r4", finished=False) / "aa__x").mkdir()
        lane = report.load_lane("live", [run])
        assert (lane.trials, lane.running) == ({}, {"aa"})
        assert "| live | – (0) | 0.0% (0/63; 0.0–5.7)" in report.render([lane])


# ── review 2026-10-24: infra pairs, attempts, unmeasured rows, clipped commands ──


def _infra_kill() -> dict:
    return {
        "exception_info": {
            "exception_type": "NonZeroAgentExitCodeError",
            "exception_message": "Command failed (exit -9): docker compose exec",
        }
    }


def _rerun_lane(tmp_path: Path) -> report.Lane:
    """R4: t1 host-killed, then solved in the re-run; t2 solved."""
    run = tmp_path / "r4run"
    job = _job(run / "r4", finished=True)
    _trial(job, "t1", None, **_infra_kill())
    _trial(job, "t2", 1.0)
    rerun = _job(run / "r4-rerun", finished=True)
    _trial(rerun, "t1", 1.0, finished_at="2026-10-24T05:00:00Z")
    return report.load_lane("r4", [run])


def _killed_lane(tmp_path: Path) -> report.Lane:
    """terminus-2: t1 host-killed (no re-run pass), t2 solved."""
    run = tmp_path / "termrun"
    job = _job(run / "terminus", finished=True)
    _trial(job, "t1", None, **_infra_kill())
    _trial(job, "t2", 1.0)
    return report.load_lane("terminus-2", [run])


class TestInfraPairs:
    def test_one_lanes_infra_kill_is_not_the_other_lanes_win(self, tmp_path: Path) -> None:
        """Old code: (2, 1, 1, 0), t1 counted as an R4-only win."""
        r4, term = _rerun_lane(tmp_path), _killed_lane(tmp_path)
        assert report.solved(r4.trials["t1"])  # the re-run replaced the kill
        assert report.infra_in_either(r4, term) == {"t1"}
        assert report.paired_counts(r4, term) == (1, 1, 0, 0)
        text = "\n".join(report.paired_section(r4, term))
        assert "Left out of the test: 1 task(s)" in text

    def test_host_out_of_space_is_infra_and_its_rerun_counts(self, tmp_path: Path) -> None:
        """Old code: the ENOSPC trial counted as a real failed attempt and beat its re-run."""
        enospc = {
            "OSError": "[Errno 28] No space left on device: '/tmp/tmp68jirv8d'",
            "RuntimeError": "failed to send keys: OCI runtime exec failed: open "
            "/tmp/runc-process1846876793: no space left on device",
        }
        for exc, message in enospc.items():
            run = tmp_path / exc
            info = {"exception_info": {"exception_type": exc, "exception_message": message}}
            _trial(_job(run / "terminus", finished=True), "t1", None, **info)
            rerun = _job(run / "terminus-rerun", finished=True)
            _trial(rerun, "t1", 1.0, finished_at="2026-10-24T09:00:00Z")
            lane = report.load_lane("terminus-2", [run])
            assert report.solved(lane.trials["t1"]), exc

    def test_an_infra_failure_awaiting_its_rerun_is_running_not_finished(
        self, tmp_path: Path
    ) -> None:
        """Old code: the out-of-space trial counted as finished (accuracy 0/1, 0 min)."""
        run = tmp_path / "term"
        info = {"exception_info": {"exception_type": "OSError",
                                   "exception_message": "[Errno 28] No space left on device"}}  # fmt: skip
        job = _job(run / "terminus", finished=True)
        _trial(job, "t1", None, **dict(info))
        _trial(job, "t2", None, **dict(info))
        rerun = _job(run / "terminus-rerun", finished=False)
        (rerun / "t1__abc").mkdir()  # started, no result yet
        lane = report.load_lane("terminus-2", [run])
        assert "t1" in lane.running and "t1" not in lane.trials
        assert report.valid_tasks(lane) == set()  # t2: infra, no re-run
        assert report.score_rows(lane)[0] == ("finished trials", 0, 0)
        row = next(r for r in report.progress_section([lane]) if r.startswith("| terminus-2"))
        assert row.startswith("| terminus-2 | 0 | 1 | 1 |")

    def test_a_full_disk_inside_the_task_is_not_infra(self) -> None:
        trial = {"exception_info": {"exception_type": "AgentTimeoutError",
                                    "exception_message": "No space left on device"}}  # fmt: skip
        assert not report.is_infra_trial(trial)

    def test_a_later_real_attempt_does_not_replace_an_earlier_one(self, tmp_path: Path) -> None:
        """No best-of-n: an extra dir in a lane keeps the first real attempt."""
        run = tmp_path / "twice"
        _trial(_job(run / "a", finished=True), "t1", 0.0)
        _trial(_job(run / "b", finished=True), "t1", 1.0, finished_at="2026-10-24T09:00:00Z")
        lane = report.load_lane("r4", [run])
        assert not report.solved(lane.trials["t1"])

    def test_per_attempt_efficiency_counts_reruns(self, tmp_path: Path) -> None:
        r4 = _rerun_lane(tmp_path)
        assert (report.started(r4), report.attempts(r4)) == (2, 3)
        cells = dict(zip(report.EFFICIENCY_HEADER, report.efficiency_row(r4), strict=True))
        assert cells["Tasks started / attempts"] == "2 / 3"


class TestTokenAndCommandCounts:
    def test_finished_stream_without_usage_adds_its_estimate(self) -> None:
        """Old code: a 200 row with only usage_estimated added 0 tokens."""
        rows = [
            {"status": 200, "dt_s": 5.0, "prompt_tokens": 100, "completion_tokens": None,
             "usage_estimated": {"completion_tokens": 700}},
            {"status": 200, "dt_s": 5.0, "prompt_tokens": 100, "completion_tokens": 50},
        ]  # fmt: skip
        stats = report.proxy_stats(rows)
        assert (stats["completion"], stats["unmeasured"]) == (750, 1)

    def test_clipped_arguments_still_count_their_segments(self) -> None:
        """The transcript clips arguments at 8,000 chars; the JSON no longer parses."""
        full = json.dumps({"command": "cd /app && make && ./run " + "x" * 9000})
        assert report.shell_segments(report._command(full[:8000])) == 3


class TestPublicProvenance:
    def test_board_row_names_the_mirror_and_dates(self) -> None:
        text = "\n".join(public.public_section())
        assert "Board (Snorkel mirror) — Claude Code" in text
        assert "Official board" not in text
        assert "±3.23 (95% CI†)" in text
        assert "page date 2026-08-14" in text and "updated 2026-10-22" in text
        assert "| AA Coding Agents — OpenCode | 40.00 | – | n/s | n/s |" in text
        assert "https://www.tbench.ai/" in text
        assert "330 trials (5 × 66), which are themselves †" in text


class TestUnattendedSettings:
    def test_headline_arm_lists_its_unattended_behaviour(self) -> None:
        meta = {"lane": "r4", "agent_knobs": {"R4_AUTONOMOUS": "1"}}
        settings = report.unattended_settings(meta)
        assert "completion review, one per task (H4): on" in settings
        assert "shell detach on timeout (H18): on" in settings
        knobs = {"R4_AUTONOMOUS": "1", "R4_COMPANION_COMPLETION_REVIEW": "0"}
        off = report.unattended_settings({"lane": "r4", "agent_knobs": knobs})
        assert "completion review, one per task (H4): off" in off
        assert report.unattended_settings({"lane": "terminus-2", "agent_knobs": {}}) == []

    def test_rendered_run_settings_carry_the_caveat(self, tmp_path: Path) -> None:
        run = _r4_run(tmp_path)
        meta = {"lane": "r4", "agent_knobs": {"R4_AUTONOMOUS": "1"}}
        (run / "run-meta.json").write_text(json.dumps(meta))
        text = report.render([report.load_lane("r4", [run])])
        assert "unattended-run behaviour (R4_AUTONOMOUS=1): completion review" in text
        assert "Assume the result will be checked by programmatic tests" in text
