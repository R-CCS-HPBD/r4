"""Terminal lane plumbing checks: no docker, no LLM, no tokens.

Guards the fail-closed secret filter and the standalone driver contract.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.harness.terminal import support

LANE_DIR = Path(__file__).resolve().parent


def _r4_agent():
    pytest.importorskip("harbor", reason="harbor lives in the uv tool venv")
    from benchmarks.harness.terminal import r4_agent

    return r4_agent


_SOURCES = json.dumps(
    {
        "sources": [
            {
                "name": "bdrt",
                "kind": "platform",
                "url": "https://data0.ai.r-ccs.riken.jp/bdrt",
                "api_key": "platform-jwt",
                "zmq_host": "data0.ai.r-ccs.riken.jp",
            },
            {
                "name": "rikyu",
                "kind": "public",
                "url": "https://api.rikyu.r-ccs.riken.jp/v1",
                "api_key": "sk-test",
            },
        ],
        "active": "bdrt",
    }
)


class TestFilterSources:
    def test_picks_named_public_source(self) -> None:
        out = json.loads(support.filter_sources(_SOURCES, "rikyu"))
        assert out["active"] == "rikyu"
        assert len(out["sources"]) == 1
        entry = out["sources"][0]
        assert entry["name"] == "rikyu"
        assert entry["url"] == "https://api.rikyu.r-ccs.riken.jp/v1"
        assert entry["api_key"] == "sk-test"

    def test_refuses_platform_source(self) -> None:
        with pytest.raises(ValueError, match="kind='platform'"):
            support.filter_sources(_SOURCES, "bdrt")

    def test_refuses_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="not in host sources.json"):
            support.filter_sources(_SOURCES, "nope")

    def test_refuses_source_without_key(self) -> None:
        data = json.loads(_SOURCES)
        data["sources"][1]["api_key"] = ""
        with pytest.raises(ValueError, match="url/api_key"):
            support.filter_sources(json.dumps(data), "rikyu")

    def test_refuses_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            support.filter_sources("{nope", "rikyu")

    def test_never_leaks_zmq_fields_for_public_source(self) -> None:
        out = json.loads(support.filter_sources(_SOURCES, "rikyu"))
        assert "zmq_host" not in out["sources"][0]


class TestDriverStandalone:
    def test_driver_imports_nothing_from_the_repo(self) -> None:
        """The driver runs inside a task container without the checkout; any
        benchmarks.* import would be a latent container-only crash."""
        tree = ast.parse((LANE_DIR / "driver.py").read_text())
        module_imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_imports.add(node.module)
        assert not [name for name in module_imports if name.startswith("benchmarks")]

    def test_driver_error_payload_shape(self) -> None:
        """main() must always print one JSON verdict line and exit 0, even on
        a missing prompt file (mirrors the SWE lane's failure isolation)."""
        import subprocess
        import sys

        proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(LANE_DIR / "driver.py"),
                "--prompt-file",
                "/definitely/missing.txt",
                "--workdir",
                str(LANE_DIR),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["status"] == "error"
        assert payload["error"]

    def test_driver_verdict_survives_an_unwritable_result_file(self, tmp_path: Path) -> None:
        """B1 (2026-10-05): a result file the agent user cannot write is recorded
        in the payload; the verdict line on stdout still arrives."""
        import subprocess
        import sys

        proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(LANE_DIR / "driver.py"),
                "--prompt-file",
                "/definitely/missing.txt",
                "--workdir",
                str(LANE_DIR),
                "--result-file",
                str(tmp_path / "no-such-dir" / "result.json"),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["status"] == "error"
        assert payload["result_file_error"].startswith("FileNotFoundError")

    def test_driver_puts_the_transcript_beside_the_result(self, tmp_path, monkeypatch) -> None:
        """W6: with --result-file and no R4_TRANSCRIPT_PATH, the session transcript
        is written next to the result; an explicit path is left alone."""
        from benchmarks.harness.terminal import driver

        seen: list[str] = []

        def fake_drive(*_args):
            import os

            seen.append(os.environ.get("R4_TRANSCRIPT_PATH", ""))
            return {"status": "success", "response_text": "", "metrics": {}, "error": None}

        monkeypatch.setattr(driver, "drive", fake_drive)
        monkeypatch.delenv("R4_TRANSCRIPT_PATH", raising=False)
        argv = ["--prompt-file", "p", "--workdir", str(tmp_path)]
        driver.main([*argv, "--result-file", str(tmp_path / "result.json")])
        assert seen[-1] == str(tmp_path / "transcript.jsonl")
        monkeypatch.setenv("R4_TRANSCRIPT_PATH", "/elsewhere/t.jsonl")
        driver.main([*argv, "--result-file", str(tmp_path / "result.json")])
        assert seen[-1] == "/elsewhere/t.jsonl"

    def test_decision_summary_counts_gate_outcomes(self) -> None:
        """Skipped and cached gate entries (r4 decisions/harness.py, H13) are not
        decision-model calls; skips coalesce into one entry with a count."""
        from types import SimpleNamespace

        from benchmarks.harness.terminal.driver import _decision_summary

        log = [
            {"purpose": "tool_gate", "outcome": "skipped", "count": 3},
            {
                "purpose": "tool_gate",
                "outcome": "asked",
                "escalated": True,
                "latency_ms": 40,
                "gate_ms": 50,
            },
            {"purpose": "tool_gate", "outcome": "cached", "escalated": True, "gate_ms": 0},
            {
                "purpose": "tool_gate",
                "outcome": "error",
                "error": "TimeoutError: x",
                "gate_ms": 5000,
            },
            {"purpose": "tool_gate", "escalated": False, "latency_ms": 30},  # pre-outcome entry
            {"purpose": "lesson_check", "kept": True, "latency_ms": 20},
        ]
        summary = _decision_summary(SimpleNamespace(metadata={"decision_log": log}), {})
        assert summary["calls"] == 4  # asked + error + pre-outcome gate + lesson check
        assert summary["tool_gate_calls"] == 3
        assert summary["tool_gate_skipped"] == 3
        assert summary["tool_gate_cached"] == 1
        assert summary["tool_gate_escalations"] == 2
        assert summary["gate_ms_median"] == 5000
        assert summary["latency_ms_median"] == 30
        assert summary["first_error"] == "TimeoutError: x"


class TestAgentOutputDir:
    """B1: the result goes where the agent user can write (bench dir is root-owned)."""

    @staticmethod
    def _run(home: str | None) -> str:
        import os
        import subprocess

        env = {k: v for k, v in os.environ.items() if k != "HOME"}
        if home is not None:
            env["HOME"] = home
        proc = subprocess.run(  # noqa: S603
            ["sh", "-c", support.AGENT_OUTPUT_DIR_SCRIPT],  # noqa: S607
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return proc.stdout

    def test_home_dir_first(self, tmp_path: Path) -> None:
        out = self._run(str(tmp_path / "home"))
        assert out == str(tmp_path / "home" / ".r4bench")
        assert Path(out).is_dir()

    @pytest.mark.skipif(not Path("/proc/self").is_dir(), reason="needs procfs")
    @pytest.mark.parametrize("home", ["/proc/self/no-home", None])
    def test_fresh_tmp_dir_when_home_is_unusable(self, home: str | None) -> None:
        out = Path(self._run(home))
        try:
            assert out.parent == Path("/tmp")
            assert out.name.startswith("r4bench.")
            assert out.is_dir()
        finally:
            out.rmdir()


class TestModelRoute:
    """B2 (2026-10-05): the model proxy is the default; the key copy is explicit."""

    def test_default_port_is_the_proxy_port_everywhere(self) -> None:
        assert support.DEFAULT_MODEL_PROXY_PORT == 8333
        assert "PORT=${TB4_PROXY_PORT:-8333}" in (LANE_DIR / "run_tb4.sh").read_text()
        assert "| `model_proxy_port` | `8333` |" in (LANE_DIR / "README.md").read_text()
        assert "default=DEFAULT_MODEL_PROXY_PORT" in (LANE_DIR / "model_proxy.py").read_text()

    def test_proxy_route_needs_the_token(self) -> None:
        with pytest.raises(ValueError, match="R4BENCH_MODEL_TOKEN"):
            support.model_route(8333, False, "")
        assert support.model_route(8333, False, "run-token") == "proxy"

    def test_no_proxy_port_without_the_copy_flag_refuses(self) -> None:
        with pytest.raises(ValueError, match="copy_source_key=true"):
            support.model_route(0, False, "run-token")

    def test_key_copy_only_on_the_explicit_flag(self) -> None:
        assert support.model_route(0, True, "") == "copy"
        assert support.model_route(8333, True, "") == "copy"

    def test_proxy_route_never_reads_the_host_key(self) -> None:
        def host_sources() -> str:
            raise AssertionError("the host sources.json must not be read in proxy mode")

        out = json.loads(
            support.model_sources_json(
                "rikyu",
                proxy_port=8333,
                copy_source_key=False,
                token="run-token",
                host_sources=host_sources,
            )
        )
        (entry,) = out["sources"]
        assert entry["api_key"] == "run-token"
        assert entry["url"].endswith(":8333/v1")

    def test_copy_route_filters_the_host_sources(self) -> None:
        out = json.loads(
            support.model_sources_json(
                "rikyu",
                proxy_port=8333,
                copy_source_key=True,
                token="",
                host_sources=lambda: _SOURCES,
            )
        )
        assert [e["api_key"] for e in out["sources"]] == ["sk-test"]

    @pytest.mark.parametrize(
        "value, expected",
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("", False),
            ("off", False),
        ],
    )
    def test_parse_flag(self, value: object, expected: bool) -> None:
        assert support.parse_flag(value) is expected

    def test_parse_flag_refuses_other_words(self) -> None:
        with pytest.raises(ValueError, match="true or false"):
            support.parse_flag("maybe")


class TestAgentEnv:
    """B5 (2026-10-24): the driver's env carries each knob only when asked."""

    def test_default_env_sets_no_knob(self) -> None:
        assert support.agent_env() == {"R4_TIMEOUT": "1200", "R4_DECISION_MODEL": "off"}
        assert support.knob_env("default", "", None) == {}

    def test_each_knob_when_asked(self) -> None:
        env = support.agent_env(7200, "nimble", "terminal", "1", 0.95)
        assert env == {
            "R4_TIMEOUT": "7200",
            "R4_DECISION_MODEL": "Bespoke-Nimble-9B@nimble",
            "R4_AGENT_PROFILE": "terminal",
            "R4_COMPANION_TEMPERATURE": "1.0",
            "R4_COMPANION_TOP_P": "0.95",
        }
        assert support.knob_env(temperature=0.4) == {"R4_COMPANION_TEMPERATURE": "0.4"}

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"profile": "lean"}, "profile"),
            ({"temperature": "hot"}, "temperature must be a number"),
            ({"temperature": 2.5}, "temperature must be between"),
            ({"top_p": 1.5}, "top_p must be between"),
            ({"decision": "maybe"}, "decision"),
        ],
    )
    def test_bad_values_are_refused(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            support.agent_env(**kwargs)

    def test_driver_defaults_are_the_support_copy(self) -> None:
        from benchmarks.harness.terminal import driver

        assert driver.UNATTENDED_ENV == support.DRIVER_ENV_DEFAULTS

    def test_driver_runs_the_session_with_ask_user_mode_auto(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B5: the driver states unattended intent through r4's existing setting
        (R4_ASK_USER_MODE=auto, plan H12); an explicit value (harbor --ae) wins."""
        from benchmarks.harness.terminal import driver

        seen = _fake_r4(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))  # never the user's ~/.config/r4
        monkeypatch.chdir(tmp_path)
        for name in ("R4_AUTONOMOUS", "R4_ASK_USER_MODE"):
            monkeypatch.setenv(name, "")  # registers the restore
            monkeypatch.delenv(name)
        (tmp_path / "prompt.txt").write_text("do the task")
        payload = driver.drive(tmp_path / "prompt.txt", tmp_path, None)
        assert payload["status"] == "success"
        assert seen[-1] == {"R4_AUTONOMOUS": "1", "R4_ASK_USER_MODE": "auto"}
        monkeypatch.setenv("R4_ASK_USER_MODE", "ask")
        driver.drive(tmp_path / "prompt.txt", tmp_path, None)
        assert seen[-1]["R4_ASK_USER_MODE"] == "ask"


def _fake_r4(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Just enough of the r4 package for driver.drive(); returns the env each
    session.send() saw."""
    import os
    import sys
    from types import ModuleType, SimpleNamespace

    seen: list[dict] = []
    usage = dict.fromkeys(
        (
            "llm_calls",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "compaction_calls",
            "subagent_calls",
        ),
        0,
    )

    class Session:
        def __init__(self, **_kwargs: object) -> None:
            self.permissions = SimpleNamespace(
                set_approval_callback=lambda _cb: None, set_trust_checker=lambda _c: None
            )
            self.tools: list = []
            self.metadata: dict = {}
            self.usage_summary = SimpleNamespace(to_dict=lambda: usage)

        def send(self, _prompt: str) -> str:
            seen.append({k: os.environ[k] for k in ("R4_AUTONOMOUS", "R4_ASK_USER_MODE")})
            return "done"

    modules = {
        "r4": ModuleType("r4"),
        "r4.client": ModuleType("r4.client"),
        "r4.companion": ModuleType("r4.companion"),
        "r4.companion.permissions": ModuleType("r4.companion.permissions"),
        "r4.companion.session": ModuleType("r4.companion.session"),
    }
    modules["r4.client"].Client = lambda: SimpleNamespace(config=SimpleNamespace())  # type: ignore[attr-defined]
    modules["r4.companion.permissions"].PermissionMode = SimpleNamespace(TRUST="trust")  # type: ignore[attr-defined]
    modules["r4.companion.session"].CompanionSession = Session  # type: ignore[attr-defined]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return seen


class TestRunMeta:
    """B3 (2026-10-24): run-meta.json says what a run ran with, never a credential."""

    _R4_ARGS = [
        *("-d", "terminal-bench/terminal-bench@4.0.0", "-e", "docker"),
        *("-a", "benchmarks.harness.terminal.r4_agent:R4CompanionAgent", "-m", "glm-5.3@rikyu"),
        *("--ak", "model_proxy_port=8333", "--ak", "decision=off", "--ak", "nimble_port=8331"),
        *("--ak", "exec_timeout_sec=28800", "--ak", "r4_timeout_sec=7200", "-n", "4"),
        *("-x", "terminal-bench/fp8-rmsnorm-gemm", "-x", "terminal-bench/jax-speedrun-gpu"),
        *("-i", "terminal-bench/biped-contact-dynamics"),
    ]

    def test_parse_harbor_args(self) -> None:
        parsed = support.parse_harbor_args(
            [*self._R4_ARGS, "--ae=R4_COMPANION_THINK_BUDGET_SEC=300", "-y", "-q"]
        )
        assert parsed["concurrency"] == "4" and parsed["model"] == "glm-5.3@rikyu"
        assert parsed["exclude"] == [
            "terminal-bench/fp8-rmsnorm-gemm",
            "terminal-bench/jax-speedrun-gpu",
        ]
        assert parsed["include"] == ["terminal-bench/biped-contact-dynamics"]
        assert parsed["ak"]["exec_timeout_sec"] == "28800"
        assert parsed["ae"] == {"R4_COMPANION_THINK_BUDGET_SEC": "300"}

    def _r4_meta(self, tmp_path: Path) -> dict:
        args = [*self._R4_ARGS, "--ak", "profile=terminal", "--ak", "temperature=1.0"]
        args += ["--ae", "R4_COMPANION_COMPLETION_REVIEW=0", "--ae", "R4_ASK_USER_MODE=ask"]
        meta = support.write_run_meta(
            tmp_path / "run-meta.json",
            "r4",
            args,
            extra={"proxy_port": "8333", "default_max_tokens": "131072"},
            environ={"R4BENCH_PACKAGE_ROOT": "/cache/src/abc"},
        )
        assert json.loads((tmp_path / "run-meta.json").read_text()) == meta
        return meta

    def test_r4_lane_records_the_agent_knobs(self, tmp_path: Path) -> None:
        meta = self._r4_meta(tmp_path)
        assert meta["agent_knobs"] == {
            "R4_AUTONOMOUS": "1",
            "R4_ASK_USER_MODE": "ask",  # --ae wins over the driver default
            "R4_TIMEOUT": "7200",
            "R4_DECISION_MODEL": "off",
            "R4_AGENT_PROFILE": "terminal",
            "R4_COMPANION_TEMPERATURE": "1.0",
            "R4_COMPANION_COMPLETION_REVIEW": "0",
        }
        assert (meta["temperature"], meta["top_p"]) == ("1.0", None)

    def test_r4_lane_records_the_run(self, tmp_path: Path) -> None:
        meta = self._r4_meta(tmp_path)
        picked = {key: meta[key] for key in ("lane", "package_root", "exec_timeout_sec")}
        assert picked == {
            "lane": "r4",
            "package_root": "/cache/src/abc",
            "exec_timeout_sec": "28800",
        }
        assert len(meta["commit"]) == 40
        assert meta["dirty"] in (True, False)
        assert (meta["harbor"]["concurrency"], len(meta["harbor"]["exclude"])) == ("4", 2)
        assert "/opt/r4bench" in " ".join(meta["install_footprint"])
        assert meta["proxy_port"] == "8333"

    def test_r4_lane_records_its_output_limit(self, tmp_path: Path) -> None:
        """Review 2026-10-24: "TOKEN" in the name had it dropped as a secret."""
        meta = self._r4_meta(tmp_path)
        assert meta["default_max_tokens"] == "131072"
        assert "set:default_max_tokens" not in meta["dropped_as_secret"]

    def test_terminus_lane_record(self) -> None:
        args = ["-a", "terminus-2", "-m", "openai/glm-5.3", "--ak", "temperature=0.4"]
        meta = support.run_meta("terminus-2", [*args, "--ak", "api_base=http://127.0.0.1:8335/v1"])
        assert meta["temperature"] == "0.4"
        assert meta["agent_knobs"] == {}
        assert meta["install_footprint"] == list(support.TERMINUS_INSTALL_FOOTPRINT)
        with pytest.raises(ValueError, match="lane"):
            support.run_meta("other", [])

    def test_never_writes_a_credential(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hex_token = "0123456789abcdef" * 3  # openssl rand -hex 24
        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", hex_token)
        monkeypatch.setenv("R4_GATEWAY_TOKEN", "gw-secret-value")
        sk_key = "sk-" + "abcdef" * 3  # built at runtime: a fake key, never a literal one
        args = [*self._R4_ARGS, "--ak", "copy_source_key=true", "--ak", f"api_key={sk_key}"]
        args += ["--ae", "R4_GATEWAY_TOKEN=gw-secret-value", "--ae", "R4_COMPANION_API_KEY=k1"]
        args += ["--ae", f"R4_COMPANION_NOTE={hex_token}", "--ae", "OPENAI_API_KEY=k2"]
        args += ["--ak", "llm_call_kwargs=" + json.dumps({"api_key": sk_key})]
        support.write_run_meta(
            tmp_path / "run-meta.json", "r4", args, extra={"token": hex_token, "jwt": "eyJa.eyJb.c"}
        )
        text = (tmp_path / "run-meta.json").read_text()
        for secret in (hex_token, "gw-secret-value", sk_key, "=k1", "eyJa"):
            assert secret not in text
        meta = json.loads(text)
        assert meta["harbor"]["ak"]["copy_source_key"] == "true"  # a flag, not a key
        assert "ak:api_key" in meta["dropped_as_secret"]
        assert "ae:R4_GATEWAY_TOKEN" in meta["dropped_as_secret"]

    def test_cli_writes_the_file(self, tmp_path: Path) -> None:
        out = tmp_path / "run-meta.json"
        assert (
            support.main(
                ["run-meta", "--out", str(out), "--lane", "r4", "--set", "a=b", "--", "-n", "2"]
            )
            == 0
        )
        meta = json.loads(out.read_text())
        assert meta["a"] == "b" and meta["harbor"]["concurrency"] == "2"

    @pytest.mark.parametrize("script", ["run_tb4.sh", "run_tb4_terminus.sh"])
    def test_both_launchers_write_run_meta(self, script: str) -> None:
        text = (LANE_DIR / script).read_text()
        assert "support run-meta" in text and "run-meta.json" in text

    @pytest.mark.parametrize(
        ("script", "rerun_dir"),
        [("run_tb4.sh", "r4-rerun"), ("run_tb4_terminus.sh", "terminus-rerun")],
    )
    def test_both_launchers_rerun_host_killed_trials(self, script: str, rerun_dir: str) -> None:
        """Review 2026-10-24: only the R4 lane re-ran exit -9 kills, so the paired test
        counted terminus-2's kills as R4 wins."""
        text = (LANE_DIR / script).read_text()
        assert "--infra-kills" in text and f'"$OUT/{rerun_dir}"' in text


class TestAgentSurface:
    def test_name(self) -> None:
        assert _r4_agent().R4CompanionAgent.name() == "r4-companion"

    def test_repo_root_contains_packages(self) -> None:
        for relpath in support.PACKAGE_DIRS:
            assert (support.REPO_ROOT / relpath / "pyproject.toml").is_file(), relpath

    def test_driver_file_next_to_agent(self) -> None:
        assert support.DRIVER_PATH.is_file()

    def test_agent_defaults_to_the_model_proxy(self) -> None:
        import inspect

        params = inspect.signature(_r4_agent().R4CompanionAgent.__init__).parameters
        assert params["model_proxy_port"].default == 8333
        assert params["copy_source_key"].default is False

    def test_agent_refuses_proxy_mode_without_a_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_cls = _r4_agent().R4CompanionAgent
        monkeypatch.delenv("R4BENCH_MODEL_TOKEN", raising=False)
        with pytest.raises(ValueError, match="R4BENCH_MODEL_TOKEN"):
            agent_cls(logs_dir=tmp_path)

    def test_run_downloads_result_and_transcript_from_agent_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B1 + transcript: the result comes from the agent-writable dir, the
        transcript from the payload's path; a failed transcript copy is not fatal."""
        import asyncio
        from types import SimpleNamespace

        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", "run-token")
        agent = _r4_agent().R4CompanionAgent(logs_dir=tmp_path, workdir="/app")
        payload = {
            "status": "success",
            "metrics": {"prompt_tokens": 5},
            "transcript_path": "/home/u/.r4/t.jsonl",
        }
        env = _FakeEnvironment(payload, fail_on="/home/u/.r4/t.jsonl")
        context = SimpleNamespace(n_input_tokens=None, n_output_tokens=None, metadata=None)
        asyncio.run(agent.run("do the task", env, context))
        assert "--result-file /home/u/.r4bench/result.json" in env.commands[-1]
        assert env.downloads == [
            ("/home/u/.r4bench/result.json", tmp_path / "driver-result.json"),
            ("/home/u/.r4/t.jsonl", tmp_path / "transcript.jsonl"),
        ]
        assert context.n_input_tokens == 5
        assert context.metadata["r4_driver"]["model_route"] == "proxy"
        assert (tmp_path / "container-ip.txt").read_text().strip()  # proxy-row attribution

    def test_run_hands_the_driver_only_the_asked_knobs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B5: --ak profile/temperature/top_p reach the driver's env; a default
        agent passes none of them (product defaults are the headline)."""
        import asyncio
        from types import SimpleNamespace

        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", "run-token")
        agent_cls = _r4_agent().R4CompanionAgent
        payload = {"status": "success", "metrics": {}}

        def driver_env(**kwargs: object) -> dict:
            agent = agent_cls(logs_dir=tmp_path, workdir="/app", **kwargs)
            env = _FakeEnvironment(payload)
            context = SimpleNamespace(n_input_tokens=None, n_output_tokens=None, metadata=None)
            asyncio.run(agent.run("do the task", env, context))
            return env.driver_env or {}

        assert driver_env() == {"R4_TIMEOUT": "1200", "R4_DECISION_MODEL": "off"}
        asked = driver_env(profile="terminal", temperature=1.0, top_p="0.95")
        assert asked["R4_AGENT_PROFILE"] == "terminal"
        assert asked["R4_COMPANION_TEMPERATURE"] == "1.0"
        assert asked["R4_COMPANION_TOP_P"] == "0.95"
        with pytest.raises(ValueError, match="profile"):
            agent_cls(logs_dir=tmp_path, profile="lean")

    def test_killed_driver_still_yields_its_transcript(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TB 4.0 re-run 2026-10-05: a SIGKILLed driver (exit -9) makes harbor
        raise inside exec; the transcript beside the result is still copied."""
        import asyncio
        from types import SimpleNamespace

        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", "run-token")
        agent = _r4_agent().R4CompanionAgent(logs_dir=tmp_path, workdir="/app")
        env = _FakeEnvironment({}, driver_rc=-9)
        context = SimpleNamespace(n_input_tokens=None, n_output_tokens=None, metadata=None)
        with pytest.raises(Exception):  # noqa: B017 — harbor's NonZeroAgentExitCodeError
            asyncio.run(agent.run("do the task", env, context))
        assert ("/home/u/.r4bench/transcript.jsonl", tmp_path / "transcript.jsonl") in env.downloads

    def test_cancelled_driver_still_yields_its_transcript(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """harbor cancels a trial with CancelledError (a BaseException): the
        transcript copy still runs, shielded, and the cancel propagates."""
        import asyncio
        from types import SimpleNamespace

        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", "run-token")
        agent = _r4_agent().R4CompanionAgent(logs_dir=tmp_path, workdir="/app")
        env = _FakeEnvironment({}, cancel_driver=True)
        context = SimpleNamespace(n_input_tokens=None, n_output_tokens=None, metadata=None)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(agent.run("do the task", env, context))
        assert ("/home/u/.r4bench/transcript.jsonl", tmp_path / "transcript.jsonl") in env.downloads


class _FakeEnvironment:
    """Just enough of a harbor environment for R4CompanionAgent.run()."""

    default_user = None

    def __init__(
        self, payload: dict, *, fail_on: str = "", driver_rc: int = 0, cancel_driver: bool = False
    ) -> None:
        self.payload, self.fail_on, self.driver_rc = payload, fail_on, driver_rc
        self.cancel_driver = cancel_driver
        self.commands: list[str] = []
        self.downloads: list[tuple[str, Path]] = []
        self.driver_env: dict | None = None

    async def exec(self, command: str, **kwargs: object) -> object:
        from types import SimpleNamespace

        self.commands.append(command)
        is_driver = support.REMOTE_DRIVER_NAME in command
        if is_driver:
            self.driver_env = dict(kwargs.get("env") or {})  # type: ignore[call-overload]
        if is_driver and self.cancel_driver:
            import asyncio

            raise asyncio.CancelledError
        stdout = json.dumps(self.payload) if is_driver else "/home/u/.r4bench"
        return SimpleNamespace(
            return_code=self.driver_rc if is_driver else 0, stdout=stdout, stderr=""
        )

    async def upload_file(self, source: object, target: str) -> None:
        self.commands.append(f"upload {source} -> {target}")

    async def download_file(self, source: str, target: Path) -> None:
        self.downloads.append((source, target))
        if source == self.fail_on:
            raise FileNotFoundError(source)


_STREAM_LINES = [
    'data: {"id":"c1","created":1,"model":"glm-5.3","choices":[{"delta":{"reasoning_content":"think "}}]}',
    'data: {"choices":[{"delta":{"reasoning_content":"more"}}]}',
    'data: {"choices":[{"delta":{"content":"Hi"}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t0","function":{"name":"shell_exec","arguments":"{\\"comm"}}]}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"and\\": \\"ls\\"}"}}]}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"t1","function":{"name":"file_read","arguments":"{}"}}]}}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7}}',
    "data: [DONE]",
]


class TestAgentConfigOwner:
    """M0 2026-10-24: in images with a non-root USER and no declared default_user,
    sources.json stayed root-owned and R4 died on Permission denied in a minute."""

    def test_the_command_gives_the_file_to_the_owner_of_its_directory(self, tmp_path: Path) -> None:
        conf = tmp_path / "home dir" / ".config" / "r4"
        conf.mkdir(parents=True)
        target = conf / "sources.json"
        target.write_text("{}")
        target.chmod(0o644)
        cmd = support.agent_config_owner_command(str(tmp_path / "home dir"))
        subprocess.run(["bash", "-c", cmd], check=True)
        assert oct(target.stat().st_mode & 0o777) == "0o600"
        assert target.stat().st_uid == conf.stat().st_uid

    def test_install_chowns_sources_json_after_uploading_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        r4_agent = _r4_agent()
        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", "run-token")
        monkeypatch.setattr(r4_agent, "ensure_wheelhouse", lambda: tmp_path)
        agent = r4_agent.R4CompanionAgent(logs_dir=tmp_path, workdir="/app")

        async def no_deps(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(agent, "ensure_system_dependencies", no_deps)
        monkeypatch.setattr(agent, "_sources_json", lambda: "{}")

        class Env(_FakeEnvironment):
            async def upload_dir(self, source: object, target: str) -> None:
                self.commands.append(f"upload_dir {source} -> {target}")

        env = Env({})
        asyncio.run(agent.install(env))
        uploaded = next(
            i for i, c in enumerate(env.commands) if c.endswith("/.config/r4/sources.json")
        )
        owner = [c for c in env.commands[uploaded:] if "chown" in c and "sources.json" in c]
        assert owner, env.commands


class TestRemoteDriverName:
    """M0 2026-10-25 wdm-design: the model ran `pkill -f driver.py` for the task's own
    driver.py and killed R4's (exit 143)."""

    def test_the_container_driver_is_not_named_like_a_task_driver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("R4BENCH_MODEL_TOKEN", "run-token")
        agent = _r4_agent().R4CompanionAgent(logs_dir=tmp_path, workdir="/app")
        command = agent._driver_command("/out")
        assert "driver" not in command.split("/venv/bin/python ", 1)[1].split()[0].rsplit("/", 1)[1]
        assert support.REMOTE_DRIVER_NAME in command


class TestModelProxy:
    """Host model proxy: the container gets a token, never the source key."""

    def test_proxied_source_carries_token_not_key(self) -> None:
        out = json.loads(support.proxied_source("rikyu", 8332, "run-token"))
        assert out["active"] == "rikyu"
        (entry,) = out["sources"]
        assert entry == {
            "name": "rikyu",
            "kind": "public",
            "url": f"http://{support.HOST_GATEWAY_PLACEHOLDER}:8332/v1",
            "api_key": "run-token",
        }

    def test_proxied_source_needs_token(self) -> None:
        with pytest.raises(ValueError, match="R4BENCH_MODEL_TOKEN"):
            support.proxied_source("rikyu", 8332, "")

    def test_chat_payload_rules(self) -> None:
        from benchmarks.harness.terminal.model_proxy import check_chat_payload

        assert check_chat_payload({"model": "glm-5.3", "messages": []}, "glm-5.3") is None
        assert check_chat_payload({"model": "glm-5.3@rikyu"}, "glm-5.3") is None
        assert "only glm-5.3" in check_chat_payload({"model": "other"}, "glm-5.3")
        assert check_chat_payload({"model": "glm-5.3", "stream": True}, "glm-5.3") is None
        assert check_chat_payload([], "glm-5.3") == "body must be a JSON object"

    def test_usage_record_counts_only(self) -> None:
        from benchmarks.harness.terminal.model_proxy import usage_record

        body = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {"content": "secret text", "tool_calls": [{}, {}]},
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 8},
            },
        }
        rec = usage_record("172.18.0.2", 200, 1.234, body)
        assert rec["prompt_tokens"] == 10 and rec["cached_tokens"] == 8 and rec["tool_calls"] == 2
        assert "secret text" not in json.dumps(rec)
        assert usage_record("172.18.0.2", 502, 0.1, None)["prompt_tokens"] is None

    @staticmethod
    def _assembled() -> dict:
        from benchmarks.harness.terminal.model_proxy import StreamState

        state = StreamState()
        for line in _STREAM_LINES:
            state.feed_line(line)
        return state.assemble()

    def test_stream_state_assembles_reasoning_and_text(self) -> None:
        out = self._assembled()
        msg = out["choices"][0]["message"]
        assert (out["id"], out["model"]) == ("c1", "glm-5.3")
        assert (msg["reasoning_content"], msg["content"]) == ("think more", "Hi")
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 7}

    def test_stream_state_assembles_split_tool_calls(self) -> None:
        calls = self._assembled()["choices"][0]["message"]["tool_calls"]
        assert [c["id"] for c in calls] == ["t0", "t1"]
        assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls"}

    def test_stream_state_records_error_event(self) -> None:
        from benchmarks.harness.terminal.model_proxy import StreamState

        state = StreamState()
        state.feed_line('data: {"error": {"message": "overloaded"}}')
        assert state.error == {"message": "overloaded"}

    @staticmethod
    def _upstream(handler):
        import httpx

        from benchmarks.harness.terminal.model_proxy import Upstream

        up = object.__new__(Upstream)  # skip the host sources file
        up.url, up._headers = "http://upstream.invalid/v1/chat/completions", {}
        client = httpx.Client(transport=httpx.MockTransport(handler))
        up._client = lambda: client
        return up

    def test_relay_passes_sse_through_and_assembles_usage(self) -> None:
        import httpx

        sent: list[dict] = []

        def handler(request):
            sent.append(json.loads(request.content))
            body = "\n".join([*_STREAM_LINES, ""]).encode()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

        up = self._upstream(handler)
        heads, lines = [], []
        status, body = up.relay(
            {"model": "glm-5.3", "stream": True}, lambda *head: heads.append(head), lines.append
        )
        assert sent[0]["stream"] is True
        assert sent[0]["stream_options"]["include_usage"] is True
        assert heads == [(200, "text/event-stream")]
        assert list(filter(None, lines)) == _STREAM_LINES
        assert status == 200
        assert body["usage"]["completion_tokens"] == 7
        assert body["choices"][0]["message"]["content"] == "Hi"

    def test_relay_stops_reading_upstream_when_the_client_goes_away(self) -> None:
        import httpx

        produced: list[int] = []

        class Endless(httpx.SyncByteStream):
            def __iter__(self):
                for i in range(1000):
                    produced.append(i)
                    yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n'

        def handler(request):
            return httpx.Response(200, stream=Endless())

        def write(line: str) -> None:
            if len(produced) > 3:
                raise BrokenPipeError

        with pytest.raises(OSError):
            self._upstream(handler).relay(
                {"model": "glm-5.3", "stream": True}, lambda *_: None, write
            )
        assert len(produced) < 10

    def test_relay_end_to_end_through_the_handler(self, tmp_path: Path) -> None:
        """A streaming client gets SSE lines over HTTP; the log gets one usage row."""
        import threading
        from http.server import ThreadingHTTPServer

        import httpx

        from benchmarks.harness.terminal.model_proxy import UsageLog, make_handler

        class FakeUpstream:
            def relay(self, _payload, start, write, _state=None):
                start(200, "text/event-stream")
                for line in _STREAM_LINES:
                    write(line)
                    write("")
                return 200, {"usage": {"completion_tokens": 7}, "choices": [{}]}

        log = UsageLog(tmp_path / "log.jsonl")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(FakeUpstream(), "glm-5.3", "tok", log)
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
            with httpx.stream(
                "POST",
                url,
                json={"model": "glm-5.3", "stream": True, "messages": []},
                headers={"Authorization": "Bearer tok"},
            ) as resp:
                got = [line for line in resp.iter_lines() if line]
                assert resp.headers["content-type"] == "text/event-stream"
        finally:
            server.shutdown()
        assert got == _STREAM_LINES
        row = json.loads((tmp_path / "log.jsonl").read_text())
        assert row["status"] == 200 and row["completion_tokens"] == 7

    def test_relayed_chars_count_each_part(self) -> None:
        from benchmarks.harness.terminal.model_proxy import StreamState

        state = StreamState()
        for line in _STREAM_LINES:
            state.feed_line(line)
        # "think more"; "Hi"; '{"command": "ls"}' + "{}"
        assert state.relayed_chars() == {"reasoning": 10, "content": 2, "tool_args": 19}

    def test_cut_row_estimates_the_relayed_tokens(self) -> None:
        """B1: a client cut (499) has no upstream usage; the row says so and
        estimates completion tokens from the relayed chars (ceil(chars / 4))."""
        from benchmarks.harness.terminal.model_proxy import StreamState, stream_fields

        state = StreamState()
        state.ttft = 2.0
        state.feed_line('data: {"choices":[{"delta":{"reasoning_content":"' + "r" * 401 + '"}}]}')
        record = {"completion_tokens": None, "dt_s": 12.0}
        fields = stream_fields(state, 499, record)
        assert fields["cut"] is True
        assert fields["relayed_chars"] == {"reasoning": 401, "content": 0, "tool_args": 0}
        assert fields["usage_estimated"] == {"completion_tokens": 101}
        assert fields["decode_tps"] == 10.1  # 101 tokens over 12 s - 2 s
        assert fields["ttft_s"] == 2.0

    def test_finished_row_keeps_real_usage(self) -> None:
        from benchmarks.harness.terminal.model_proxy import StreamState, stream_fields

        fields = stream_fields(StreamState(), 200, {"completion_tokens": 7, "dt_s": 1.0})
        assert "cut" not in fields and "usage_estimated" not in fields
        assert stream_fields(None, 200, {}) == {}

    def test_decode_tps(self) -> None:
        from benchmarks.harness.terminal.model_proxy import decode_tps, usage_record

        assert decode_tps(100, 12.0, 2.0) == 10.0
        assert decode_tps(None, 12.0, 2.0) is None
        assert decode_tps(100, 12.0, None) is None
        assert decode_tps(100, 2.0, 2.0) is None
        body = {"usage": {"completion_tokens": 50}, "choices": [{}], "_ttft_s": 1.0}
        assert usage_record("172.18.0.2", 200, 6.0, body)["decode_tps"] == 10.0

    @staticmethod
    def _hang_up_mid_stream(address: tuple, log_path: Path) -> None:
        """Start one streaming request, read its first bytes, hang up, then wait
        for the proxy's usage row."""
        import socket
        import time

        body = json.dumps({"model": "glm-5.3", "stream": True, "messages": []}).encode()
        head = (
            "POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        )
        with socket.create_connection(address) as client:
            client.sendall(head.encode() + body)
            assert client.recv(4096)  # the stream has started
        deadline = time.monotonic() + 10
        while not (log_path.exists() and log_path.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_client_cut_mid_stream_logs_cut_and_estimate(self, tmp_path: Path) -> None:
        """B1 end to end: a client that hangs up mid-stream leaves a 499 row with
        cut: true, the relayed chars and a token estimate (old code: no usage)."""
        import threading
        from http.server import ThreadingHTTPServer

        import httpx

        from benchmarks.harness.terminal.model_proxy import UsageLog, make_handler

        chunk = b'data: {"choices":[{"delta":{"reasoning_content":"' + b"x" * 400 + b'"}}]}\n'

        class Endless(httpx.SyncByteStream):
            def __iter__(self):
                yield from (chunk for _ in range(200_000))

        upstream = self._upstream(lambda _request: httpx.Response(200, stream=Endless()))
        log_path = tmp_path / "log.jsonl"
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(upstream, "glm-5.3", "tok", UsageLog(log_path))
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self._hang_up_mid_stream(server.server_address, log_path)
        finally:
            server.shutdown()
        row = json.loads(log_path.read_text())
        assert row["status"] == 499 and row["cut"] is True
        assert row["relayed_chars"]["reasoning"] >= 400
        assert row["usage_estimated"]["completion_tokens"] >= 100
        assert row["completion_tokens"] is None

    def test_default_max_tokens_only_when_client_sent_none(self) -> None:
        from benchmarks.harness.terminal.model_proxy import apply_default_max_tokens

        body = {"model": "glm-5.3"}
        assert apply_default_max_tokens(body, 131072) and body["max_tokens"] == 131072
        own = {"model": "glm-5.3", "max_tokens": 1000}
        assert not apply_default_max_tokens(own, 131072) and own["max_tokens"] == 1000
        assert not apply_default_max_tokens({"max_completion_tokens": 5}, 131072)
        assert not apply_default_max_tokens({}, 0)


class TestTrialAttribution:
    """tb4_trial_diagnostics.trial_rows: exact by container address, never guessed."""

    @staticmethod
    def _rows():
        from datetime import datetime, timezone

        def row(peer, minute):
            return {"peer": peer, "_t": datetime(2026, 10, 5, 9, minute, tzinfo=timezone.utc)}

        return [row("172.29.0.2", 10), row("172.29.0.2", 20), row("172.30.0.2", 15)]

    _TRIAL = {
        "agent_execution": {
            "started_at": "2026-10-05T09:00:00Z",
            "finished_at": "2026-10-05T09:59:00Z",
        }
    }

    def test_recorded_address_wins(self) -> None:
        from benchmarks.harness.terminal.tb4_trial_diagnostics import trial_rows

        own = trial_rows(self._TRIAL, self._rows(), llm_calls=1, peer="172.29.0.2")
        assert [r["peer"] for r in own] == ["172.29.0.2", "172.29.0.2"]

    def test_call_count_then_unattributed(self) -> None:
        from benchmarks.harness.terminal.tb4_trial_diagnostics import _proxy_stats, trial_rows

        assert len(trial_rows(self._TRIAL, self._rows(), llm_calls=1)) == 1
        assert trial_rows(self._TRIAL, self._rows()) is None
        assert _proxy_stats(None) == {"proxy_rows": "unattributed"}


class TestInfraKillRerun:
    """Host-killed trials (exit -9) are listed for run_tb4.sh's re-run pass, and a
    re-run's later trial replaces the killed one when runs are merged."""

    @staticmethod
    def _trial(root: Path, job: str, task: str, finished: str, message: str = "") -> None:
        d = root / job / f"{task}__x"
        d.mkdir(parents=True)
        result = {
            "task_name": f"terminal-bench/{task}",
            "trial_name": f"{task}__{job}",
            "finished_at": finished,
            "exception_info": {"exception_message": message} if message else None,
        }
        (d / "result.json").write_text(json.dumps(result))

    def test_listing_and_merge(self, tmp_path: Path) -> None:
        from benchmarks.harness.terminal.tb4_trial_diagnostics import _load_runs, infra_killed

        first, rerun = tmp_path / "r4", tmp_path / "r4-rerun"
        self._trial(first, "j1", "mp", "2026-10-05T11:27:00Z", "Command failed (exit -9): cd /app")
        self._trial(first, "j1", "react", "2026-10-05T12:00:00Z")
        self._trial(first, "j1", "slow", "2026-10-05T12:00:00Z", "Command failed (exit 1): x")
        assert infra_killed([first]) == ["mp"]
        self._trial(rerun, "j2", "mp", "2026-10-06T03:00:00Z")
        _, trials = _load_runs([first, rerun])
        assert trials["terminal-bench/mp"]["trial_name"] == "mp__j2"
        assert infra_killed([first, rerun]) == []


class TestRunDiff:
    """tb4_run_diff.py: validity, transitions and the sign test."""

    _OK = {"reward": 1.0, "tests_passed": 3, "tests_total": 3}
    _BAD = {"reward": 0.0, "tests_passed": 1, "tests_total": 3}
    _BETTER = {"reward": 0.0, "tests_passed": 2, "tests_total": 3}
    _CANCELLED = {"reward": 0.0, "exception": "CancelledError"}

    @pytest.mark.parametrize(
        ("rec", "is_valid", "is_solved"),
        [
            (_OK, True, True),
            (_BAD, True, False),
            (_CANCELLED, False, False),
            (None, False, False),
            ({"reward": None}, False, False),
        ],
    )
    def test_valid_and_solved(self, rec, is_valid, is_solved) -> None:
        from benchmarks.harness.terminal.tb4_run_diff import solved, valid

        assert valid(rec) is is_valid
        assert solved(rec) is is_solved

    @pytest.mark.parametrize(
        ("base", "new", "change"),
        [
            (_BAD, _OK, "**gained**"),
            (_OK, _BAD, "**lost**"),
            (_BAD, _BETTER, "tests +1"),
            (_BAD, _BAD, ""),
            (_CANCELLED, _OK, "new only"),
            (_OK, None, "base only"),
        ],
    )
    def test_change(self, base, new, change) -> None:
        from benchmarks.harness.terminal.tb4_run_diff import _change

        assert _change(base, new) == change

    def test_sign_test(self) -> None:
        from benchmarks.harness.terminal.tb4_run_diff import sign_test

        assert sign_test(0, 0) == "no discordant pairs"
        assert sign_test(5, 0) == "p = 0.062"
        assert sign_test(3, 3) == "p = 1.000"
