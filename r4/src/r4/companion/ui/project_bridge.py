"""Typed project_* bridge RPCs for Bun TUI (not Companion slash).

Lifecycle is intentionally Bun-only: do NOT extend shared SUBCOMMANDS["project"]
or _cmd_project. Wrappers catch SystemExit and return structured dicts.

``project_run`` starts the project's research loop on UMRS (the canonical
ADR-036 spine) through ``r4 project loop start``; the retired RC1 in-process
run is gone (WP-07, 2026-09-07). Every lifecycle op shells out through
``_start_project_cli_job``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from r4.config import get_config, reload_config
from r4.project import get_project_manager


def _r4_project_argv(cli_args: list[str]) -> list[str]:
    """Build argv to run ``r4 project …`` in a killable subprocess."""
    return [
        sys.executable,
        "-c",
        "import sys; from r4.cli import main; "
        "sys.argv = ['r4'] + sys.argv[1:]; "
        "raise SystemExit(main() or 0)",
        "project",
        *cli_args,
    ]


def _loop_start_args(project_id: str, params: dict[str, Any]) -> list[str]:
    """``r4 project loop start …`` argv for the bridge's /project run."""
    args = ["loop", "start", project_id]
    goal = str(params.get("research_goal") or params.get("goal") or "").strip()
    if goal:
        args.append(goal)
    max_iter = params.get("max_iterations")
    args += ["--max-iterations", str(int(max_iter) if max_iter is not None else 10)]
    if bool(params.get("approve")):
        args.append("--approve")
    workflow_name = str(params.get("workflow_name") or "").strip()
    if workflow_name:
        args += ["--workflow", workflow_name]
    args.append("--json")
    return args


def _terminate(proc: subprocess.Popen[str]) -> None:
    """SIGTERM (or terminate on win32), then kill after a short grace period."""
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _catch_system_exit(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except SystemExit as exc:
        code = exc.code
        msg = str(code) if code not in (None, 0) else "command exited"
        raise RuntimeError(msg) from exc


@dataclass
class _ProjectJob:
    job_id: str
    operation: str
    process: subprocess.Popen[str] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    terminal_emitted: bool = False


class ProjectBridgeMixin:
    """Mixin expecting ``_emit_line`` and session rebuild hooks on the host."""

    _project_jobs: dict[str, _ProjectJob]

    def _ensure_project_jobs(self) -> None:
        if not hasattr(self, "_project_jobs"):
            self._project_jobs = {}

    def _emit_project_job_event(self, event: dict[str, Any]) -> None:
        self._emit_line({"project_job_event": event})  # type: ignore[attr-defined]

    def _finish_project_job(self, job: _ProjectJob, event: dict[str, Any]) -> None:
        """Emit one terminal event and remove the job from active tracking."""
        if job.terminal_emitted:
            return
        job.terminal_emitted = True
        self._emit_project_job_event(event)
        self._project_jobs.pop(job.job_id, None)

    def _rebuild_session_after_project_change(self) -> None:
        """Reload config and rebuild the companion session client."""
        reload_config()
        rebuild = getattr(self, "_rebuild_client_after_config_reload", None)
        if callable(rebuild):
            rebuild()
        elif hasattr(self, "session") and hasattr(self, "client"):
            # Best-effort: refresh session config pointer
            try:
                self.session.config = get_config()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    def _select_session_project(self, project_id: str) -> str | None:
        """Tab-scoped project selection (chat tabs, 2026-10-01).

        One bridge process serves exactly ONE chat tab / ACP session, so the
        active-project choice must stay local to this process. The previous
        global ``state.json`` write (``pm.set_current``) flipped every
        sibling tab's bridge — and every later CLI subprocess — onto the
        same project. ``R4_PROJECT_ID`` outranks the stored profile and the
        project defaults in ``Config.from_env`` and is honoured first by
        ``ProjectManager.get_current``, so setting it in-process is a
        complete selection: no global state file is touched, and the CLI
        lifecycle subprocesses this bridge spawns inherit the right project
        through the environment.

        Returns the previously selected project id (from this process's
        env), or ``None`` when nothing was selected here yet.
        """
        pm = get_project_manager()
        if not pm.get(project_id):
            raise RuntimeError(f"Project not found: {project_id}")
        previous = os.environ.get("R4_PROJECT_ID")
        os.environ["R4_PROJECT_ID"] = project_id
        self._rebuild_session_after_project_change()
        return previous

    # ── Short sync RPCs (C1) ──────────────────────────────────────────────

    def project_list(self, params: dict[str, Any]) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            pm = get_project_manager()
            projects = pm.list()
            current = pm.get_current()
            return {
                "projects": [
                    {
                        "project_id": p.project_id,
                        "name": p.name,
                        "active": p.project_id == current,
                        "gateway_url": p.gateway_url,
                        "model": p.model,
                    }
                    for p in projects
                ],
                "current": current,
            }

        return _catch_system_exit(_run)

    def project_current(self, params: dict[str, Any]) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            pm = get_project_manager()
            current = pm.get_current()
            if not current:
                return {"current": None, "project": None}
            cfg = pm.get(current)
            return {
                "current": current,
                "project": None
                if cfg is None
                else {
                    "project_id": cfg.project_id,
                    "name": cfg.name,
                    "gateway_url": cfg.gateway_url,
                    "model": cfg.model,
                },
            }

        return _catch_system_exit(_run)

    def project_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create and select the new project (create + switch semantics).

        The project record itself lands in the shared inventory
        (``projects.json``); the SELECTION stays tab-scoped (see
        ``_select_session_project``) so a create in one chat tab never
        hijacks the project of another tab.
        """
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        name = params.get("name")
        gateway = params.get("gateway")
        model = params.get("model")

        def _run() -> dict[str, Any]:
            from r4.project import ProjectConfig

            pm = get_project_manager()
            existing = pm.get(project_id)
            metadata = dict(existing.metadata) if existing else {}
            project = ProjectConfig(
                project_id=project_id,
                name=str(name) if name else (existing.name if existing else None),
                gateway_url=(
                    str(gateway) if gateway else (existing.gateway_url if existing else None)
                ),
                model=str(model) if model else (existing.model if existing else None),
                metadata=metadata,
            )
            pm.create(project)
            # Always select the new project (Bun semantics), tab-scoped.
            self._select_session_project(project_id)
            return {
                "ok": True,
                "project_id": project_id,
                "created": existing is None,
                "selected": True,
                "config_changed": True,
            }

        return _catch_system_exit(_run)

    def project_register(self, params: dict[str, Any]) -> dict[str, Any]:
        """Register the project on the platform (POST /gw/projects, ADR-051
        D-1) through this session's client, and record it locally.

        ``project_create`` writes the local registry only, so its project is
        unknown to the server (maintainer 2026-11-03: "i was not sure [how]
        to register new project on server"). Never moves the selection; a
        failure is ``ok: false`` with the words to show (never a raise).
        """
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        from r4.project_register import RegisterError, register_project, registered_text

        session = getattr(self, "session", None)
        client = getattr(session, "client", None) or getattr(self, "client", None)
        try:
            result = register_project(client, project_id, name=str(params.get("name") or ""))
        except RegisterError as exc:
            return {
                "ok": False,
                "project_id": project_id,
                "message": str(exc),
                "gateway_url": "",
                "local": "",
            }
        return {"ok": True, **result, "message": registered_text(result)}

    def project_switch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Switch THIS tab/session's active project (tab-scoped).

        Does not rewrite the global ``state.json`` current project: sibling
        chat tabs keep their own selection, and the terminal ``r4`` default
        is untouched (``r4 project switch`` still governs that).
        """
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")

        def _run() -> dict[str, Any]:
            previous = self._select_session_project(project_id)
            result: dict[str, Any] = {
                "ok": True,
                "project_id": project_id,
                "selected": True,
                "config_changed": True,
            }
            if previous and previous != project_id:
                result["note"] = (
                    f"selection is tab-scoped (this tab was {previous}; "
                    "other tabs and the CLI default are unchanged)"
                )
            return result

        return _catch_system_exit(_run)

    def project_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delete a project from the shared inventory (projects.json).

        Selection aftermath stays tab-scoped (2026-10-01): only THIS
        bridge process drops its env selection when the deleted project
        was the tab's active one — sibling tabs keep theirs until they
        act (their next action on the ghost id fails honestly with
        ``Project not found``). The global ``state.json`` current is
        cleared by ``ProjectManager.delete`` itself.
        """
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")

        def _run() -> dict[str, Any]:
            pm = get_project_manager()
            if not pm.get(project_id):
                raise RuntimeError(f"Project not found: {project_id}")
            pm.delete(project_id)
            was_selected_here = os.environ.get("R4_PROJECT_ID") == project_id
            if was_selected_here:
                os.environ.pop("R4_PROJECT_ID", None)
                self._rebuild_session_after_project_change()
            return {
                "ok": True,
                "project_id": project_id,
                "deleted": True,
                "was_selected": was_selected_here,
                "config_changed": was_selected_here,
            }

        return _catch_system_exit(_run)

    # ── Long async RPCs (C2) ──────────────────────────────────────────────

    def _start_project_cli_job(
        self,
        operation: str,
        cli_args: list[str],
    ) -> dict[str, Any]:
        self._ensure_project_jobs()
        job_id = str(uuid.uuid4())
        job = _ProjectJob(job_id=job_id, operation=operation)
        self._project_jobs[job_id] = job

        def _terminal(phase: str, **extra: Any) -> dict[str, Any]:
            return {
                "jobId": job_id,
                "operation": operation,
                "phase": phase,
                "message": f"{operation} {phase}",
                **extra,
            }

        def _cancelled() -> None:
            self._finish_project_job(job, _terminal("cancelled"))

        def worker() -> None:
            self._emit_project_job_event(_terminal("started"))
            try:
                if job.cancel.is_set():
                    _cancelled()
                    return
                proc = subprocess.Popen(
                    _r4_project_argv(cli_args),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env={**os.environ},
                    stdin=subprocess.DEVNULL,
                )
                job.process = proc
                if job.cancel.is_set():
                    proc.terminate()
                stdout, stderr = proc.communicate()
                self._finish_cli_run(job, _terminal, proc, stdout, stderr, _cancelled)
            except Exception as exc:  # noqa: BLE001
                self._finish_project_job(job, _terminal("error", error=str(exc)))

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"project-{operation}-{job_id[:8]}",
        ).start()
        return {"jobId": job_id, "operation": operation}

    def _finish_cli_run(self, job, _terminal, proc, stdout, stderr, _cancelled) -> None:
        """Map subprocess outcome to one terminal job event (single decision chain)."""
        if job.cancel.is_set():
            _cancelled()
            return
        if proc.returncode != 0:
            err = (stderr or stdout or f"exit {proc.returncode}").strip()
            self._finish_project_job(job, _terminal("error", error=err))
            return
        result: Any = None
        text = (stdout or "").strip()
        if text:
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                result = {"output": text}
        self._finish_project_job(job, _terminal("done", result=result))

    def project_import(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = str(params.get("project_id") or "").strip()
        paths = params.get("paths") or []
        if not project_id:
            raise ValueError("project_id is required")
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a non-empty list")
        return self._start_project_cli_job(
            "import",
            ["import", project_id, *[str(p) for p in paths], "--json"],
        )

    def project_init(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        return self._start_project_cli_job("init", ["init", project_id, "--json"])

    def project_verify(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = str(params.get("project_id") or "").strip()
        question = str(params.get("question") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        if not question:
            raise ValueError("question is required")
        return self._start_project_cli_job(
            "verify",
            ["verify", project_id, question, "--json"],
        )

    def project_readiness(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        # readiness may be stub — still launch; CLI returns honest error
        return self._start_project_cli_job("readiness", ["readiness", project_id, "--json"])

    def project_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = str(params.get("project_id") or "").strip()
        goal = str(params.get("research_goal") or params.get("goal") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        if not goal:
            raise ValueError("research_goal is required")
        return self._start_project_cli_job("plan", ["plan", project_id, goal, "--json"])

    def project_run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Start the project's research loop on UMRS (``r4 project loop start``).

        Folds the retired RC1 in-process run onto the canonical spine: the
        saved plan goal is used unless ``research_goal`` is given, the loop is
        durable and server-hosted, and the job finishes as soon as the loop
        has started — progress lives on the loop (Knowledge & Loops panel,
        research_loop_status). ``workflow_name`` picks the registered UMRS
        workflow the experiments run (UMRS uses the single registered one
        automatically). A legacy ``simulate`` flag is ignored and said so.
        """
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        started = self._start_project_cli_job("run", _loop_start_args(project_id, params))
        if params.get("simulate"):
            # The Bun TUI's slash parser still defaults simulate=true (its
            # dispatcher predates the retirement and is gate-frozen until
            # WP-08); simulated placeholder runs no longer exist, so the flag
            # is ignored — loudly, on the reply — and the loop starts on UMRS.
            started["note"] = (
                "simulate ignored: placeholder runs retired with the RC1 spine; "
                "the loop runs on UMRS (pass workflow_name to pick the workflow)"
            )
        return started

    def project_cancel_job(self, params: dict[str, Any]) -> dict[str, Any]:
        """Best-effort cancel: signal + terminate the lifecycle subprocess."""
        self._ensure_project_jobs()
        job_id = str(params.get("job_id") or params.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        job = self._project_jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "unknown job"}
        job.cancel.set()
        proc = job.process
        if proc is not None and proc.poll() is None:
            _terminate(proc)
        self._finish_project_job(
            job,
            {
                "jobId": job_id,
                "operation": job.operation,
                "phase": "cancelled",
                "message": f"{job.operation} cancelled",
            },
        )
        return {"ok": True, "jobId": job_id}

    def _cancel_all_project_jobs(self) -> None:
        """Terminate every active lifecycle subprocess during bridge shutdown."""
        self._ensure_project_jobs()
        for job_id in list(self._project_jobs):
            self.project_cancel_job({"jobId": job_id})

    def project_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Project status via the UMRS-side project summary (structured)."""
        import contextlib
        import io
        from argparse import Namespace

        def _run() -> dict[str, Any]:
            from r4._cli.project_knowledge import project_status as _pk_status

            buf = io.StringIO()
            ns = Namespace(
                project_id=params.get("project_id") or None,
                json=True,
                watch=False,
                interval=5.0,
            )
            with contextlib.redirect_stdout(buf):
                _pk_status(ns, get_project_manager())
            return {"status": json.loads(buf.getvalue() or "{}")}

        return _catch_system_exit(_run)

    def project_findings(self, params: dict[str, Any]) -> dict[str, Any]:
        """Verification findings for a project (severity/status breakdown)."""
        import contextlib
        import io
        from argparse import Namespace

        def _run() -> dict[str, Any]:
            from r4._cli.project_knowledge import project_findings as _pk_findings

            buf = io.StringIO()
            ns = Namespace(project_id=params.get("project_id") or None, json=True)
            with contextlib.redirect_stdout(buf):
                _pk_findings(ns, get_project_manager())
            return {"result": json.loads(buf.getvalue() or "{}")}

        return _catch_system_exit(_run)

    def _project_ingest_args(self, params: dict[str, Any]) -> tuple[str, str, list[str], list[str]]:
        """Normalize project_ingest params (no validation)."""
        graph = str(params.get("graph") or "").strip()
        project_id = (
            str(params.get("project_id") or "")
            or str(getattr(self.client.config, "project_id", "") or "")
        ).strip()
        files = list(map(str, params.get("files") or []))
        urls = list(map(str, params.get("urls") or []))
        return graph, project_id, files, urls

    def _project_ingest_validate(
        self, params: dict[str, Any]
    ) -> tuple[str, str, list[str], list[str]]:
        """Raise ValueError with usage when nothing actionable was passed."""
        graph, project_id, files, urls = self._project_ingest_args(params)
        if not (graph and project_id and (files or urls)):
            raise ValueError("usage: /project ingest <id> --graph <name> [--file F…] [--url U…]")
        return graph, project_id, files, urls

    def project_ingest(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ingest files/URLs into a named knowledge graph (egress-controlled)."""
        graph, project_id, files, urls = self._project_ingest_validate(params)

        def _run() -> dict[str, Any]:
            result = self.client.knowledge.graph_ingest(
                graph,
                project_id=project_id,
                files=files,
                urls=urls,
            )
            return {"ingest": result}

        return _catch_system_exit(_run)

    def project_session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a durable research session (ADR-025) for the project."""
        goal = str(params.get("goal") or params.get("research_goal") or "").strip()
        if not goal:
            raise ValueError("goal is required (session tracks one investigation)")
        project_id = (
            str(params.get("project_id") or "")
            or str(getattr(self.client.config, "project_id", "") or "")
        ).strip()
        if not project_id:
            raise ValueError("project_id is required (no active project)")

        def _run() -> dict[str, Any]:
            create_fn = getattr(self.client, "create_research_session", None)
            if create_fn is None:
                raise RuntimeError(
                    "research sessions need a UMRS-backed gateway (ADR-025); "
                    "this client has no sessions API"
                )
            title = str(params.get("title") or "")
            data = create_fn(project_id, goal, title=title)
            return {"session": (data or {}).get("session") or data}

        return _catch_system_exit(_run)
