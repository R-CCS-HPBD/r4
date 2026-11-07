"""Harbor installed-agent that runs the R4 Companion inside Terminal-Bench tasks.

Mirrors the SWE-bench lane's in-container pattern for the Harbor runner:

- host-side: build pure-python wheels of the four r4 packages into a content-
  addressed cache (``$XDG_CACHE_HOME/r4bench/terminal-wheels``);
- container-side: upload wheels + standalone driver, ``python3 -m venv``,
  ``pip install`` the wheels (dependencies resolve from PyPI — task containers
  sit on the default docker bridge with internet);
- config: one ``~/.config/r4/sources.json`` whose only model source is the host
  model proxy (``model_proxy.py``) under the public source's name, holding the
  proxy's per-run token. The source's API key, platform profiles and their
  JWTs never enter task containers (default since 2026-10-05);
- run: one CompanionSession (TRUST) per task instruction, JSON verdict on the
  last stdout line, mapped onto Harbor's AgentContext token fields. The full
  payload is written to a directory the agent user owns (``$HOME/.r4bench``,
  else a fresh ``/tmp`` dir) and downloaded into the trial's agent logs as
  ``driver-result.json``, with the session transcript (``transcript.jsonl``)
  when the payload names one.

Usage (``run_tb4.sh`` starts the proxy and exports the token):

    R4BENCH_MODEL_TOKEN=<proxy token> harbor run \
        -d terminal-bench/terminal-bench@4.0.0 -e docker \
        -a benchmarks.harness.terminal.r4_agent:R4CompanionAgent \
        -m glm-5.3@rikyu -l 5

``--ak workdir=...`` changes the agent workspace (default: the task image's own
working directory, as other harnesses start; TB 4.0 has /workspace and /task
images besides /app — 2026-10-05),
``--ak source=...`` names the public source (proxy entry name; the source in
~/.config/r4/sources.json on the legacy route),
``--ak exec_timeout_sec=...`` caps one task's agent execution,
``--ak decision=nimble`` turns the decision model on through the host proxy
(``nimble_proxy.py``; export ``R4BENCH_NIMBLE_TOKEN``), default ``off``,
``--ak model_proxy_port=8333`` is the host model proxy's port (default 8333;
export ``R4BENCH_MODEL_TOKEN``, or the agent refuses to start),
``--ak copy_source_key=true`` restores the legacy route: the filtered source
entry, API key included, is copied into the container (default false),
``--ak profile=terminal`` runs the R4_AGENT_PROFILE=terminal ablation (default:
product defaults, the headline), ``--ak temperature=`` / ``--ak top_p=`` set
R4_COMPANION_TEMPERATURE / R4_COMPANION_TOP_P (default: unset, R4's own).
Other R4 knobs go in with harbor's ``--ae NAME=VALUE``.

NOTE: importing this module requires the harbor python package (the harbor CLI
venv). The pure helpers live in ``support.py`` without that requirement.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from benchmarks.harness.terminal.support import (
    AGENT_OUTPUT_DIR_SCRIPT,
    CONTAINER_IP_SCRIPT,
    DEFAULT_MODEL_PROXY_PORT,
    DEFAULT_R4_TIMEOUT_SEC,
    DRIVER_PATH,
    HOST_SOURCES_PATH,
    MODEL_TOKEN_ENV,
    R4_SYSTEM_DEPENDENCIES,
    REMOTE_DRIVER_NAME,
    agent_config_owner_command,
    agent_env,
    ensure_wheelhouse,
    model_route,
    model_sources_json,
    parse_flag,
    with_decision_source,
)


class R4CompanionAgent(BaseInstalledAgent):
    """Runs the R4 Companion agent loop against each Terminal-Bench task."""

    def __init__(
        self,
        logs_dir: Path,
        model: str | None = None,
        source: str = "rikyu",
        workdir: str = "",
        bench_dir: str = "/opt/r4bench",
        exec_timeout_sec: int = 3600,
        install_timeout_sec: int = 900,
        r4_timeout_sec: int = DEFAULT_R4_TIMEOUT_SEC,
        decision: str = "off",
        nimble_port: int = 8331,
        model_proxy_port: int = DEFAULT_MODEL_PROXY_PORT,
        copy_source_key: bool | str = False,
        profile: str = "",
        temperature: float | str | None = None,
        top_p: float | str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._model_override = model
        self._source = source
        self._workdir = workdir
        self._bench_dir = bench_dir
        self._exec_timeout_sec = int(exec_timeout_sec)
        self._install_timeout_sec = int(install_timeout_sec)
        # Per-request HTTP read timeout for model calls (r4's R4_TIMEOUT;
        # default 300s is under one glm-5.3 turn on large TB contexts).
        self._r4_timeout_sec = int(r4_timeout_sec)
        # Decision model (ADR-076): "off" or "nimble" (the host proxy,
        # nimble_proxy.py, reached as an extra public source). The driver's env
        # (B5): R4_TIMEOUT, R4_DECISION_MODEL (set even when off: r4 turns the
        # decision model on by default) and the profile/sampling knobs only when
        # asked. Built (and validated) here, so a bad value fails before any
        # container work.
        self._decision = str(decision).strip().lower()
        self._agent_env = agent_env(self._r4_timeout_sec, decision, profile, temperature, top_p)
        self._nimble_port = int(nimble_port)
        # Model route (B2, 2026-10-05): the host model proxy by default; the
        # legacy key copy only on an explicit copy_source_key=true. Checked
        # here so a missing token fails before any container work.
        self._model_proxy_port = int(model_proxy_port)
        self._copy_source_key = parse_flag(copy_source_key)
        model_route(self._model_proxy_port, self._copy_source_key, self._model_token())
        super().__init__(logs_dir, *args, **kwargs)

    @staticmethod
    def name() -> str:
        return "r4-companion"

    def _model(self) -> str | None:
        return self._model_override or self.model_name

    @staticmethod
    def _model_token() -> str:
        return os.environ.get(MODEL_TOKEN_ENV, "")

    async def _resolve_workdir(self, environment: BaseEnvironment) -> str:
        """The explicit --ak workdir, else the image's working directory."""
        if not self._workdir:
            result = await self.exec_as_agent(environment, command="pwd")
            lines = (result.stdout or "").strip().splitlines()
            self._workdir = lines[-1].strip() if lines else "/app"
            self.logger.info("r4bench workdir: %s", self._workdir)
        return self._workdir

    async def _record_container_ip(self, environment: BaseEnvironment) -> None:
        """The address the model proxy sees for this trial (container-ip.txt), so
        its usage rows can be attributed exactly (2026-10-05: addresses are reused
        slot by slot and a killed driver reports no call count)."""
        try:
            result = await self.exec_as_agent(environment, command=CONTAINER_IP_SCRIPT)
            address = (result.stdout or "").strip().splitlines()[-1].strip()
            (self.logs_dir / "container-ip.txt").write_text(address + "\n")
        except Exception as exc:  # noqa: BLE001 — diagnostics never break a trial
            self.logger.debug("r4bench: container address unknown: %s", exc)

    async def _agent_home(self, environment: BaseEnvironment) -> str:
        result = await self.exec_as_agent(environment, command='printf %s "$HOME"')
        home = (result.stdout or "").strip()
        return home or "/root"

    async def _agent_output_dir(self, environment: BaseEnvironment) -> str:
        """A directory the agent user can write (the bench dir is root-owned)."""
        result = await self.exec_as_agent(environment, command=AGENT_OUTPUT_DIR_SCRIPT)
        lines = (result.stdout or "").strip().splitlines()
        if not lines:
            raise RuntimeError("r4bench: no agent-writable output directory")
        return lines[-1].strip()

    async def install(self, environment: BaseEnvironment) -> None:
        bench = self._bench_dir
        await self.ensure_system_dependencies(environment, R4_SYSTEM_DEPENDENCIES)
        wheelhouse = ensure_wheelhouse()
        await environment.upload_dir(wheelhouse, f"{bench}/wheels")
        await environment.upload_file(DRIVER_PATH, f"{bench}/{REMOTE_DRIVER_NAME}")
        # venv + install: wheels arrive as files, their deps (httpx) resolve
        # from PyPI on the task container's bridge network. pyyaml is pinned
        # in explicitly: without PyYAML the companion skill registry falls
        # back to a flat front-matter parser that silently drops nested
        # metadata (x-r4-keywords/x-r4-dependencies), which disarms skill
        # auto-activation and leaves the model with platform-ops tools only
        # (measured 2026-09-29: 17 offered tools instead of 42).
        await self.exec_as_root(
            environment,
            command=(
                "set -e; "
                f"python3 -m venv {shlex.quote(bench)}/venv && "
                f"{shlex.quote(bench)}/venv/bin/pip install --quiet --upgrade pip && "
                f"{shlex.quote(bench)}/venv/bin/pip install --quiet {shlex.quote(bench)}/wheels/*.whl 'pyyaml>=6.0' && "
                f"{shlex.quote(bench)}/venv/bin/python -c 'import r4; print(r4.__name__)' && "
                f"{shlex.quote(bench)}/venv/bin/python -c 'import yaml; print(\"yaml:\", yaml.__version__)' && "
                f'{shlex.quote(bench)}/venv/bin/python -c \'from r4.companion.skills import SkillRegistry; r=SkillRegistry(); n=r._load_builtin_skills(); s=r.get("code"); print("r4bench sentinel: skills=%d code_keywords=%d" % (n, len(s.keywords) if s else -1))\''
            ),
            timeout_sec=self._install_timeout_sec,
        )
        await self._resolve_workdir(environment)
        home = await self._agent_home(environment)
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(home)}/.config/r4",
        )
        await self._upload_config_text(
            environment,
            content=self._sources_json(),
            remote_path=f"{home}/.config/r4/sources.json",
            filename="sources.json",
        )
        await self.exec_as_root(environment, command=agent_config_owner_command(home))

    def _sources_json(self) -> str:
        sources = model_sources_json(
            self._source,
            proxy_port=self._model_proxy_port,
            copy_source_key=self._copy_source_key,
            token=self._model_token(),
            host_sources=HOST_SOURCES_PATH.read_text,
        )
        if self._decision == "nimble":
            token = os.environ.get("R4BENCH_NIMBLE_TOKEN", "")
            sources = with_decision_source(sources, self._nimble_port, token)
        return sources

    def _driver_command(self, out_dir: str) -> str:
        bench = shlex.quote(self._bench_dir)
        command = (
            f"cd {shlex.quote(self._workdir)} && "
            f"{bench}/venv/bin/python {bench}/{REMOTE_DRIVER_NAME} "
            f"--prompt-file {bench}/prompt.txt "
            f"--workdir {shlex.quote(self._workdir)} "
            f"--result-file {shlex.quote(out_dir)}/result.json"
        )
        model = self._model()
        if model:
            command += f" --model {shlex.quote(model)}"
        return command

    @staticmethod
    def _driver_payload(result: Any) -> dict[str, Any]:
        """The JSON verdict on the driver's last stdout line."""
        lines = (result.stdout or "").strip().splitlines()
        if not lines:
            raise RuntimeError(
                f"r4 companion driver produced no output "
                f"(rc={result.return_code}): {(result.stderr or '')[-400:]}"
            )
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"r4 companion driver output is not JSON: {lines[-1][-400:]}"
            ) from exc

    async def _download(self, environment: BaseEnvironment, source: str, name: str) -> None:
        """Copy one in-container file into the trial's agent logs; never fails a trial."""
        try:
            await environment.download_file(source, self.logs_dir / name)
        except Exception as exc:  # noqa: BLE001 — never break a trial on logs
            self.logger.debug("r4bench: download of %s failed: %s", name, exc)

    async def _download_artifacts(
        self, environment: BaseEnvironment, out_dir: str, payload: dict[str, Any]
    ) -> None:
        # The full driver payload (response text included); stdout alone is
        # not synced by harbor.
        await self._download(environment, f"{out_dir}/result.json", "driver-result.json")
        # The session transcript (H12/W6): the payload names it; the driver's
        # default sits beside the result.
        transcript = payload.get("transcript_path")
        if not (isinstance(transcript, str) and transcript):
            transcript = f"{out_dir}/transcript.jsonl"
        await self._download(environment, transcript, "transcript.jsonl")

    def _record_context(
        self, context: AgentContext, payload: dict[str, Any], model: str | None
    ) -> None:
        metrics = payload.get("metrics") or {}
        context.n_input_tokens = metrics.get("prompt_tokens")
        context.n_output_tokens = metrics.get("completion_tokens")
        context.metadata = {
            "r4_driver": {
                "status": payload.get("status"),
                "model": model,
                "source": self._source,
                "model_route": "copy" if self._copy_source_key else "proxy",
                "llm_calls": metrics.get("llm_calls"),
                "compaction_calls": metrics.get("compaction_calls"),
                "wallclock_sec": metrics.get("wallclock_sec"),
                "skills_active": metrics.get("skills_active"),
                "n_skill_tools_offered": metrics.get("n_skill_tools_offered"),
                "yaml_version": metrics.get("yaml_version"),
                "decision": self._decision,
                "decisions": metrics.get("decisions"),
                "agent_env": dict(self._agent_env),
                "transcript_path": payload.get("transcript_path"),
            }
        }

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        await self._resolve_workdir(environment)
        await self._record_container_ip(environment)
        with tempfile.TemporaryDirectory(prefix="r4bench-prompt-") as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.txt"
            prompt_path.write_text(instruction)
            await environment.upload_file(prompt_path, f"{self._bench_dir}/prompt.txt")
        out_dir = await self._agent_output_dir(environment)
        try:
            result = await self.exec_as_agent(
                environment,
                command=self._driver_command(out_dir),
                env=dict(self._agent_env),
                cwd=self._workdir,
                timeout_sec=self._exec_timeout_sec,
            )
        except BaseException:
            # A killed, failed or cancelled driver raises here; its transcript is
            # still the only per-step record (TB 4.0 re-run 2026-10-05: two
            # SIGKILLed trials left nothing to diagnose). Shielded so a cancelled
            # trial still gets the copy; a second cancel just skips it.
            with contextlib.suppress(BaseException):  # the original error is what matters
                await asyncio.shield(
                    self._download(environment, f"{out_dir}/transcript.jsonl", "transcript.jsonl")
                )
            raise
        payload = self._driver_payload(result)
        await self._download_artifacts(environment, out_dir, payload)
        self._record_context(context, payload, self._model())
        if payload.get("status") == "error":
            raise RuntimeError(f"r4 companion driver error: {payload.get('error')}")
