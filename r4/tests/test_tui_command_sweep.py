from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.chat_core.session import ChatSession, Turn
from r4.chat_core.session_store import SessionStore
from r4.companion.skills import SkillRegistry
from r4.tui.commands import SUBCOMMANDS, get_slash_registry
from r4.tui.modes import Mode


class RecordingRenderer:
    def __init__(self) -> None:
        self.info_lines: list[str] = []
        self.system_lines: list[str] = []
        self.error_lines: list[str] = []
        self.clear_count = 0

    def info(self, text: str) -> None:
        self.info_lines.append(str(text))

    def system(self, text: str) -> None:
        self.system_lines.append(str(text))

    def error(self, text: str, *, correlation_id: str | None = None) -> None:
        suffix = f" cid={correlation_id}" if correlation_id else ""
        self.error_lines.append(f"{text}{suffix}")

    def clear(self) -> None:
        self.clear_count += 1


class FakeJobClient:
    def list(self, **_kwargs):
        return [SimpleNamespace(job_id="job-1", job_type="endpoint_provision", status="running")]

    def get(self, job_id: str):
        return SimpleNamespace(job_id=job_id, job_type="endpoint_provision", status="running")

    def cancel(self, job_id: str):
        return SimpleNamespace(job_id=job_id, job_type="endpoint_provision", status="cancelled")

    def retry(self, job_id: str):
        return SimpleNamespace(job_id=job_id, job_type="endpoint_provision", status="queued")


class FakeWorkflowClient:
    def list_workflows(self):
        return [SimpleNamespace(name="wf-demo", description="demo workflow", status="available")]

    def status(self, execution_id: str):
        return SimpleNamespace(execution_id=execution_id, status="running", progress=0.5)

    def cancel(self, _execution_id: str) -> bool:
        return True

    def trigger(self, **_kwargs):
        return SimpleNamespace(execution_id="exec-1", status="queued")

    def upload(self, **_kwargs):
        return SimpleNamespace(workflow_id="wf-uploaded", name="wf-uploaded", status="accepted")


class FakeMcpApi:
    def list_tools(self):
        return [{"name": "search", "description": "search docs"}]

    def list_servers(self):
        return [{"name": "server-a"}]


class FakeKnowledgeClient:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self._loops: list[dict] = [
            {
                "loop_id": "loop-1",
                "status": "running",
                "phase": "observe",
                "research_goal": "demo",
                "current_iteration": 2,
                "max_iterations": 10,
            }
        ]

    def list_loops(self, project_id: str):
        return list(self._loops)

    def get_loop(self, project_id: str, loop_id: str):
        for loop in self._loops:
            if loop["loop_id"] == loop_id:
                return {
                    **loop,
                    "iterations": [
                        {
                            "iteration_id": "it1",
                            "iteration_number": 1,
                            "phase": "observe",
                            "status": "completed",
                        },
                        {
                            "iteration_id": "it2",
                            "iteration_number": 2,
                            "phase": "observe",
                            "status": "running",
                        },
                    ],
                }
        raise LookupError(loop_id)

    def start_loop(self, project_id: str, research_goal: str, max_iterations: int = 10):
        self.started.append(
            {"project_id": project_id, "goal": research_goal, "max": max_iterations}
        )
        return {"loop_id": "loop-new", "status": "running", "research_goal": research_goal}

    def stop_loop(self, project_id: str, loop_id: str):
        self.stopped.append(loop_id)
        return {"stopped": loop_id}

    def query(self, query: str, *, project_id: str, limit: int = 20, **_kwargs):
        self.queries = [*getattr(self, "queries", []), (query, project_id, limit)]
        return {
            "status": "ok",
            "query": {"results": [{"title": "MOF-5 notes", "score": 0.82}]},
        }


class FakeMcpManager:
    def list_connections(self) -> list[object]:
        return []

    def connect(self, target: str) -> bool:
        return target == "server-a"

    def connect_all(self) -> dict[str, bool]:
        return {"server-a": True}

    def summary(self) -> str:
        return "1 connected"


class FakeClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(project_id="demo")
        self.mcp = FakeMcpApi()
        self.jobs = FakeJobClient()
        self.workflows = FakeWorkflowClient()
        self.knowledge = FakeKnowledgeClient()
        self.rag = SimpleNamespace(
            list_documents=lambda **_kwargs: [
                {
                    "document_id": "doc-1",
                    "filename": "paper.pdf",
                    "status": "indexed",
                    "chunks_created": 3,
                }
            ],
            query=self._rag_query,
            # K1.3: /rag upload calls the upload client (it only printed a hint).
            upload=lambda _path, **_kwargs: SimpleNamespace(
                document_id="job-1", status="queued-central"
            ),
        )

    # ── Loop execution surface used by UmrsRuntime (delegates to knowledge fake)
    def start_loop(
        self, project_id, research_goal, max_iterations=10, approved=False, session_id=None
    ):
        self.last_loop_start = {
            "project_id": project_id,
            "research_goal": research_goal,
            "max_iterations": max_iterations,
            "approved": approved,
            "session_id": session_id,
        }
        return self.knowledge.start_loop(project_id, research_goal, max_iterations)

    def list_loops(self, **_kwargs):
        return {"loops": self.knowledge.list_loops(self.config.project_id)}

    def get_loop_status(self, loop_id):
        return self.knowledge.get_loop(self.config.project_id, loop_id)

    def list_loop_iterations(self, loop_id):
        return self.knowledge.get_loop(self.config.project_id, loop_id)["iterations"]

    def stop_loop(self, loop_id):
        return self.knowledge.stop_loop(self.config.project_id, loop_id)

    def readiness(self):
        return {"ready": True, "checks": {"gateway": "ok"}}

    def provision_endpoint(self, model: str, wait: bool = True):
        return {
            "instance_id": "ep-new",
            "status": "pending" if not wait else "ready",
            "model": model,
        }

    def list_models(self):
        return ["served-model"]

    def list_endpoints(self, include_stopped: bool = False):
        return {
            "endpoints": [
                {
                    "instance_id": "ep-1",
                    "status": "ready" if not include_stopped else "stopped",
                    "model": "endpoint-model",
                    "models": ["endpoint-a", "endpoint-b"],
                    "backend": "compose",
                    "host": "localhost",
                    "port": 8001,
                }
            ]
        }

    def get_endpoint_status(self, instance_id: str):
        return {
            "instance_id": instance_id,
            "status": "ready",
            "model": "endpoint-model",
            "backend": "compose",
            "detail": "ready",
        }

    def stop_endpoint(self, instance_id: str, force: bool = False):
        return {
            "instance_id": instance_id,
            "status": "deprovisioned",
            "force": force,
        }

    def _rag_query(self, query: str, *, project_id=None, **_kwargs):
        # Mirrors RAGClient.query -> RAGResponse(answer, sources[RAGSource]).
        self.rag_queries = [*getattr(self, "rag_queries", []), (query, project_id)]
        return SimpleNamespace(
            answer=f"answer: {query}",
            sources=[SimpleNamespace(document_id="doc-1", source_name="paper.pdf", score=0.9)],
        )

    def list_jobs(self):
        return {"jobs": [{"id": "job-1", "type": "endpoint_provision", "status": "running"}]}

    def list_workflows(self):
        return {"workflows": [{"name": "wf-demo", "status": "available"}]}

    # ── Generic GW route stubs (table-driven to stay under complexity gates)
    _GET_ROUTE_TABLE = (
        (("audit", "governance"), lambda _path, _params: {"events": []}),
        (("traces",), lambda _path, _params: {"traces": []}),
        (
            ("checkpoints",),
            lambda path, _params: (
                {"checkpoint_id": "chk-1", "job_id": "job-1"}
                if "latest" in path
                else {"checkpoints": []}
            ),
        ),
        (("recovery",), lambda _path, _params: {"sessions": []}),
        (("step-replay",), lambda _path, _params: {"replay_id": "rep-1", "steps": []}),
        (("workflow-outcomes",), lambda _path, _params: {"outcomes": []}),
        (("workflow-fork",), lambda _path, _params: {"nodes": [], "edges": []}),
        (("adaptive-planning",), lambda _path, _params: {"suggestions": [], "heuristics": []}),
        (("vectors",), lambda _path, _params: {"versions": []}),
        (("knowledge",), lambda _path, _params: {"results": []}),
        (
            ("model-visibility",),
            lambda _path, _params: {"model_name": "served-model", "scope": "project"},
        ),
        (("reflective-memory",), lambda _path, _params: {"entries": []}),
        (("secret-broker",), lambda _path, _params: {"secrets": [], "events": []}),
        (("components",), lambda _path, _params: []),
    )

    def get(self, path: str, *, params=None):
        for markers, handler in self._GET_ROUTE_TABLE:
            if any(marker in path for marker in markers):
                return handler(path, params)
        return {}

    def post(self, path: str, *, json=None):
        if "vectors" in path:
            return {"status": "queued"}
        if "knowledge" in path:
            return {"results": []}
        for markers, handler in _POST_ROUTE_HANDLERS:
            if any(marker in path for marker in markers):
                return handler(path, json)
        return {}

    def patch(self, path: str, *, json=None):
        # Generic PATCH stub
        if "model-visibility" in path:
            return {"model_name": "served-model", "scope": json.get("scope") if json else "project"}
        return {}

    def delete(self, path: str):
        # Generic DELETE stub
        return {}


def _post_reflective(path, _json):
    if "reset" in path:
        return {"status": "ok"}
    return {"results": [], "entry_id": "e-1"}


def _post_secret_broker(path, _json):
    if "cleanup" in path:
        return {"cleaned": 0}
    if "revoke" in path:
        return {"status": "revoked"}
    return {"secret_id": "s-1"}


def _post_lease_action(path, _json):
    return {"lease_id": "lease-1"}


_POST_ROUTE_HANDLERS = (
    (("renew", "lease"), _post_lease_action),
    (("reflective-memory",), _post_reflective),
    (("secret-broker",), _post_secret_broker),
)


class FakeSessionStore:
    def __init__(self) -> None:
        self.loaded = ChatSession(model="saved-model")
        _add_turn(self.loaded, "saved question", "saved answer")

    def list_sessions(self, **_kwargs):
        return [{"id": "saved-1", "name": "Saved", "surface": "tui", "model": "saved-model"}]

    def load(self, session_id: str):
        return self.loaded if session_id == "saved-1" else None


class FakeApp:
    def __init__(self, tmp_path: Path) -> None:
        self.renderer = RecordingRenderer()
        self.commands = get_slash_registry()
        self.session = ChatSession(model="served-model", system_prompt="system")
        _add_turn(self.session, "first question", "first answer")
        _add_turn(self.session, "second question", "second answer")
        self.client = FakeClient()
        self.mcp_manager = FakeMcpManager()
        self.skill_registry = SkillRegistry()
        self.mode = Mode.CHAT
        self.local_tools = [{"name": "local-tool", "description": "local helper"}]
        self.context_manager = None
        self.session_store = FakeSessionStore()
        self.exit_requested = False
        self.banner_count = 0
        self.dashboard_count = 0
        self.sent_messages: list[str] = []
        self.tmp_path = tmp_path

    def request_exit(self) -> None:
        self.exit_requested = True

    def show_banner(self) -> None:
        self.banner_count += 1

    def render_dashboard(self) -> None:
        self.dashboard_count += 1
        self.renderer.info("dashboard rendered")

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode

    def send_user_message(self, text: str) -> None:
        self.sent_messages.append(text)


def _add_turn(
    session: ChatSession,
    user_text: str,
    assistant_text: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    session.add_user(user_text)
    session.add_assistant(
        assistant_text,
        usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    )
    session.turns.append(
        Turn(
            user=user_text,
            assistant=assistant_text,
            model=session.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    )


@pytest.fixture(autouse=True)
def patch_external_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4 import models as models_module
    from r4._cli import down as down_module
    from r4._cli import up as up_module

    class FakeUpCommand:
        def handle(self, _namespace) -> None:
            return None

    class FakeDownCommand:
        def handle(self, _namespace) -> None:
            return None

    class FakeModelClient:
        def list_models(self, **_kwargs):
            return [
                SimpleNamespace(name="registry-model", status=SimpleNamespace(value="available"))
            ]

    monkeypatch.setattr(up_module, "UpCommand", FakeUpCommand)
    monkeypatch.setattr(down_module, "DownCommand", FakeDownCommand)
    monkeypatch.setattr(models_module, "ModelClient", FakeModelClient)


def _main_command_inputs(tmp_path: Path) -> dict[str, str]:
    return {
        "help": "/help",
        "exit": "/exit",
        "clear": "/clear",
        "new": "/new",
        "overview": "/overview",
        "mode": "/mode",
        "plan": "/plan",
        "models": "/models",
        "endpoint": "/endpoint",
        "up": "/up endpoint-model",
        "down": "/down ep-1",
        "health": "/health",
        "dashboard": "/dashboard",
        "tools": "/tools",
        "mcp": "/mcp",
        "trust": "/trust",
        "rag": "/rag",
        "job": "/job",
        "workflow": "/workflow",
        "research": "/research",
        "context": "/context",
        "usage": "/usage",
        "compact": "/compact",
        "history": "/history",
        "cost": "/cost",
        "export": "/export",
        "save": f"/save {tmp_path / 'session.md'}",
        "share": "/share",
        "sessions": "/sessions",
        "system": "/system",
        "config": "/config",
        "retry": "/retry",
        "progress": "/progress",
        "probe": "/probe",
        "lessons": "/lessons",
        "template": "/template list",
        "skills": "/skills",
        "companion": "/companion",
        "decision-model": "/decision-model",
        "code": f"/code status {tmp_path}",
        "project": "/project",
        "agent": "/agent",
        "artifact": "/artifact help",
        "run": "/run help",
        "search": "/search",
        "team": "/team",
        "audit": "/audit list",
        "trace": "/trace list --job job-1",
        "checkpoint": "/checkpoint list job-1",
        "recovery": "/recovery list",
        "replay": "/replay show rep-1",
        "outcome": "/outcome list",
        "lineage": "/lineage job-1",
        "adaptive": "/adaptive suggest wf-demo",
        "vectors": "/vectors versions",
        "governance": "/governance quotas demo",
        "knowledge": "/knowledge quantum",
        "visibility": "/visibility show served-model",
        "memory": "/memory list session",
        "secret": "/secret list demo",
        "pipeline": "/pipeline components chunkers",
    }


def test_all_registered_tui_commands_smoke(tmp_path: Path) -> None:
    registry = get_slash_registry()
    command_inputs = _main_command_inputs(tmp_path)
    assert set(command_inputs) == {command.name for command in registry.command_list()}

    for command_name, raw_command in command_inputs.items():
        app = FakeApp(tmp_path)
        assert registry.dispatch(app, raw_command), command_name
        assert app.renderer.error_lines == [], (command_name, app.renderer.error_lines)


@pytest.mark.parametrize(
    "raw_command",
    [
        "/endpoint list",
        "/endpoint list --all",
        "/endpoint status ep-1",
        "/endpoint up endpoint-model",
        "/endpoint down ep-1",
        "/endpoint help",
        "/mcp list",
        "/mcp tools",
        "/mcp connect",
        "/mcp connect server-a",
        "/mcp status",
        "/mcp register",
        "/rag query what is indexed",
        "/rag upload paper.pdf",
        "/rag docs",
        "/rag list",
        "/job list",
        "/job describe job-1",
        "/job cancel job-1",
        "/job retry job-1",
        "/workflow list",
        "/workflow trigger wf-demo",
        "/workflow upload workflow.zip",
        "/workflow status exec-1",
        "/workflow cancel exec-1",
        "/mode chat",
        "/mode plan",
        "/mode autopilot",
        "/models default",
        "/yolo",
        "/skills list",
        "/skills on 1",
        "/skills off 1",
        "/code lsp",
    ],
)
def test_tui_subcommands_smoke(tmp_path: Path, raw_command: str) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, raw_command)
    assert app.renderer.error_lines == []


def test_subcommand_metadata_covers_implemented_families() -> None:
    expected = {
        "endpoint": {"list", "up", "down", "status"},
        "job": {"list", "describe", "cancel", "retry"},
        "workflow": {"list", "trigger", "upload", "status", "cancel"},
        "research": {"list", "status", "start", "stop"},
        "code": {"status", "lsp", "plan", "compile", "run", "test", "fix"},
    }
    for family, names in expected.items():
        actual = {subcommand.name for subcommand in SUBCOMMANDS[family]}
        assert actual >= names, family


def test_trust_is_mode_autopilot_and_approve_is_retired(tmp_path: Path) -> None:
    """ADR-104 D-1: one mode; /trust and /yolo switch it, /approve is gone."""
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/trust")
    assert app.mode is Mode.AUTOPILOT
    app.mode = Mode.CHAT
    assert app.commands.dispatch(app, "/yolo")
    assert app.mode is Mode.AUTOPILOT
    names = {cmd.name for cmd in app.commands.command_list()}
    assert "approve" not in names and "allow-all" not in names


def test_model_alias_dispatches_like_models(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/model 1")

    assert app.session.model == "endpoint-a"


def test_down_lists_choices_without_killing_bridge(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)
    app.client.list_endpoints = lambda include_stopped=False: {
        "endpoints": [
            {
                "instance_id": "ep-1",
                "status": "ready",
                "model": "model-a",
                "backend": "compose",
            },
            {
                "instance_id": "ep-2",
                "status": "ready",
                "model": "model-b",
                "backend": "compose",
            },
        ]
    }

    assert app.commands.dispatch(app, "/down")
    assert any("usage: /endpoint down" in line for line in app.renderer.info_lines)
    assert app.renderer.error_lines == []


def test_down_stops_endpoint_by_id(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/down ep-1")
    assert any("endpoint ep-1" in line for line in app.renderer.system_lines)
    assert app.renderer.error_lines == []


def test_sessions_loads_active_session_and_prints_history(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    app.commands.dispatch(app, "/sessions saved-1")

    assert app.session.model == "saved-model"
    assert [message.content for message in app.session.messages] == [
        "saved question",
        "saved answer",
    ]
    assert any("saved question" in line for line in app.renderer.info_lines)
    assert any("saved answer" in line for line in app.renderer.info_lines)


def test_workflow_trigger_forwards_full_input_definition(tmp_path: Path) -> None:
    """/workflow trigger carries the Studio-equivalent parameters object."""
    app = FakeApp(tmp_path)
    app.client.workflows.trigger = MagicMock(
        return_value=SimpleNamespace(execution_id="exec-w1", status="running", progress=None)
    )

    assert app.commands.dispatch(
        app,
        '/workflow trigger wf-demo --params-json {"topic": "climate", "limit": 99} '
        "--param limit=5 --param tag=review --input-file s3://k/a.tif "
        "--input-file s3://k/b.tif --project proj-9 --wait",
    )

    app.client.workflows.trigger.assert_called_once()
    _, kwargs = app.client.workflows.trigger.call_args
    assert kwargs.get("workflow_id") == "wf-demo"
    assert kwargs.get("parameters") == {"topic": "climate", "limit": "5", "tag": "review"}
    assert kwargs.get("input_files") == ["s3://k/a.tif", "s3://k/b.tif"]
    assert kwargs.get("project_id") == "proj-9"
    assert kwargs.get("wait") is True


def test_workflow_trigger_accepts_params_file(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)
    app.client.workflows.trigger = MagicMock(
        return_value=SimpleNamespace(execution_id="exec-w2", status="queued", progress=None)
    )
    (tmp_path / "wf-params.json").write_text('{"a": 1}', encoding="utf-8")
    params_path = tmp_path / "wf-params.json"

    assert app.commands.dispatch(app, f"/workflow trigger wf-demo --params-file {params_path}")

    _, kwargs = app.client.workflows.trigger.call_args
    assert kwargs.get("parameters") == {"a": 1}


def test_workflow_trigger_rejects_invalid_params_json(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)
    app.client.workflows.trigger = MagicMock()

    assert app.commands.dispatch(app, "/workflow trigger wf-demo --params-json not-json")

    app.client.workflows.trigger.assert_not_called()
    assert any("invalid JSON" in line for line in app.renderer.error_lines)


def test_workflow_trigger_plain_name_keeps_empty_parameters(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)
    app.client.workflows.trigger = MagicMock(
        return_value=SimpleNamespace(execution_id="exec-w3", status="queued", progress=None)
    )

    assert app.commands.dispatch(app, "/workflow trigger wf-demo")

    _, kwargs = app.client.workflows.trigger.call_args
    assert kwargs.get("parameters") == {}


def test_research_lists_loops_with_iteration_counts(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/research list")

    assert any("loop-1" in line for line in app.renderer.info_lines)
    assert any("iter 2/10" in line for line in app.renderer.info_lines)
    assert app.renderer.error_lines == []


def test_research_status_shows_recent_iterations(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/research status loop-1")

    assert any("phase:" in line for line in app.renderer.info_lines)
    assert any("2. observe" in line for line in app.renderer.info_lines)
    assert app.renderer.error_lines == []


def test_research_start_passes_goal_max_and_project(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(
        app,
        "/research start Optimize MOF-5 capture --project proj-1 --max-iterations 25",
    )

    assert app.client.knowledge.started == [
        {"project_id": "proj-1", "goal": "Optimize MOF-5 capture", "max": 25}
    ]
    assert any("research loop started: loop-new" in line for line in app.renderer.system_lines)
    assert app.renderer.error_lines == []


@pytest.mark.parametrize("raw_command", ["/knowledge MOF-5 capture", "/kb MOF-5 capture"])
def test_knowledge_search_uses_query_facade(tmp_path: Path, raw_command: str) -> None:
    # It POSTed the GET-only /gw/knowledge/search (405); the fake's POST
    # stub answered any "knowledge" path, so the sweep never saw it.
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, raw_command)

    assert app.client.knowledge.queries == [("MOF-5 capture", "demo", 20)]
    assert any("MOF-5 notes" in line for line in app.renderer.info_lines)
    assert app.renderer.error_lines == []


def test_rag_query_uses_rag_facade_and_renders_citations(tmp_path: Path) -> None:
    # It called a Client.rag_query that does not exist (audit #11).
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/rag query what is indexed")

    assert app.client.rag_queries == [("what is indexed", "demo")]
    assert "answer: what is indexed" in app.renderer.system_lines
    assert "[1] paper.pdf (score: 0.90)" in app.renderer.info_lines
    assert app.renderer.error_lines == []


def test_research_start_requires_goal_text(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/research start")

    assert app.client.knowledge.started == []
    assert any("goal text is required" in line for line in app.renderer.error_lines)


def test_research_stop_invokes_stop_loop(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/research stop loop-1")

    assert app.client.knowledge.stopped == ["loop-1"]
    assert any("research loop stopped: loop-1" in line for line in app.renderer.system_lines)


def test_research_start_uses_local_runtime_via_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "local-loops.db"))
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/research start bootstrap my hypothesis")

    assert any("local-" in line for line in app.renderer.system_lines)
    assert any("runtime: local" in line for line in app.renderer.system_lines)
    assert app.renderer.error_lines == []


def test_research_session_aggregates_runtime_and_loops(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(app, "/research session")

    assert any("research session demo" in line for line in app.renderer.system_lines)
    assert any("loop-1" in line for line in app.renderer.info_lines)
    assert app.renderer.error_lines == []


def test_research_start_wires_session_flag(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    assert app.commands.dispatch(
        app, "/research start digital filter for MERMAIDs --session sess-abc"
    )

    assert app.renderer.error_lines == []
    assert app.client.last_loop_start["session_id"] == "sess-abc"
    assert app.client.last_loop_start["research_goal"] == "digital filter for MERMAIDs"


def test_resume_alias_dispatches_like_sessions(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)

    app.commands.dispatch(app, "/resume saved-1")

    assert app.session.model == "saved-model"
    assert [message.content for message in app.session.messages] == [
        "saved question",
        "saved answer",
    ]


def test_history_falls_back_to_raw_messages_without_completed_turns(tmp_path: Path) -> None:
    app = FakeApp(tmp_path)
    app.session = ChatSession(model="served-model", system_prompt="system")
    app.session.add_user("message without endpoint")

    app.commands.dispatch(app, "/history")

    assert any("raw message log" in line for line in app.renderer.info_lines)
    assert any("message without endpoint" in line for line in app.renderer.info_lines)


def test_session_store_load_restores_model_context(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = ChatSession(model="model-a", system_prompt="system prompt")
    _add_turn(session, "question", "answer", prompt_tokens=7, completion_tokens=11)
    session_id = store.save(session, name="Saved", surface="tui")

    loaded = store.load(session_id)

    assert loaded is not None
    assert [message.role for message in loaded.messages] == ["system", "user", "assistant"]
    assert [message.content for message in loaded.messages] == [
        "system prompt",
        "question",
        "answer",
    ]
    assert loaded.total_prompt_tokens == 7
    assert loaded.total_completion_tokens == 11
