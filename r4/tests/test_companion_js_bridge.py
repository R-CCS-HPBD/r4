from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from r4.companion.cancellation import TurnCancellation
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.session_models import SessionMessage, UsageSummary
from r4.companion.skills import SkillRegistry
from r4.companion.ui.bridge_server import (
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_RPC_NAMES,
    CompanionBridgeServer,
)


class FakeStore:
    def __init__(self, session):
        self.session = session
        self.saved = []

    def create_session(self, client, name=None, *, model=None):
        self.session.client = client
        if model:
            self.session.agent.model = model
        return self.session

    def load(self, session_id, client):
        if session_id != self.session.session_id:
            return None
        self.session.client = client
        return self.session

    def save(self, session):
        self.saved.append(session.session_id)

    def list_sessions(self, limit=50):
        return [
            {
                "id": self.session.session_id,
                "name": self.session.name,
                "created_at": self.session.created_at,
                "updated_at": self.session.updated_at,
                "model": self.session.agent.model,
            }
        ][:limit]


def _fake_session() -> SimpleNamespace:
    client = SimpleNamespace(
        config=SimpleNamespace(
            profile="research",
            gateway_url="http://gateway.test",
            project_id="demo",
            model="config-model",
            endpoint_url=None,
            endpoint_provider=None,
        ),
        readiness=MagicMock(return_value={"ready": True}),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {
                        "instance_id": "ep-test",
                        "status": "ready",
                        "model": "test-model",
                    }
                ]
            }
        ),
    )
    messages: list[SessionMessage] = []

    def send(text: str, **_kwargs: Any) -> str:
        messages.append(SessionMessage(role="user", content=text))
        messages.append(SessionMessage(role="assistant", content="answer"))
        return "answer"

    return SimpleNamespace(
        session_id="session-1",
        name="Demo",
        client=client,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        agent=SimpleNamespace(model="test-model"),
        messages=messages,
        skills=SkillRegistry(),
        permissions=PermissionManager(),
        context=SimpleNamespace(_memory={}),
        usage_summary=UsageSummary(),
        active_endpoints=[],
        metadata={},
        _harness=None,
        send=send,
        request_turn_cancel=MagicMock(),
        usage_report=MagicMock(return_value={"total_tokens": 0}),
        export_markdown=MagicMock(return_value="# Demo"),
    )


def test_bridge_initialize_returns_bounded_state() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(
        client=session.client, store=FakeStore(session), model="override-model"
    )

    state = bridge.initialize({})

    assert state["session"]["id"] == "session-1"
    assert state["session"]["model"] == "override-model"
    assert state["session"]["profile_default"] == "config-model"
    assert state["session"]["model_provisioned"] is False
    assert state["config"]["project_id"] == "demo"
    assert state["bridge_protocol"] == BRIDGE_PROTOCOL_VERSION
    assert any(skill["name"] == "research" for skill in state["skills"])


def test_bridge_initialize_auto_selects_ready_endpoint_when_unset() -> None:
    session = _fake_session()
    session.agent.model = None
    session.client.list_endpoints = MagicMock(
        return_value={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "live-model"},
            ]
        }
    )
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    state = bridge.initialize({})

    assert state["session"]["model"] == "live-model"
    assert state["session"]["model_provisioned"] is True


def test_bridge_send_message_blocks_without_model() -> None:
    session = _fake_session()
    session.agent.model = None
    session.client.list_endpoints = MagicMock(return_value={"endpoints": []})
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge.initialize({})

    response = bridge.handle_request(
        {"id": 1, "method": "send_message", "params": {"text": "hello"}}
    )

    assert response["ok"] is False
    assert "No model selected" in response["error"]["message"]


def test_bridge_send_message_saves_and_returns_snapshot() -> None:
    session = _fake_session()
    store = FakeStore(session)
    bridge = CompanionBridgeServer(client=session.client, store=store)

    result = bridge.send_message({"text": "hello"})

    assert result["response"] == "answer"
    assert [message["role"] for message in result["state"]["messages"]] == ["user", "assistant"]
    assert store.saved[-1] == "session-1"


def test_bridge_send_message_emits_canonical_budget_state() -> None:
    """T1 canonical budget envelope lands on the JSONL bridge after a turn."""
    session = _fake_session()
    lines: list[dict] = []
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._emit_line = lines.append  # capture JSONL emissions

    bridge.send_message({"text": "hello"})

    budgets = [line for line in lines if line.get("type") == "budget_state"]
    assert len(budgets) == 1
    payload = budgets[0]["payload"]
    assert payload["version"] == 1
    assert payload["session_id"] == "session-1"
    assert payload["tool_rounds_limit"] == 1000  # default when agent attr missing
    assert payload["llm_calls_used"] == 0
    assert payload["context_used_tokens"] == 0


def test_bridge_budget_state_skips_without_agent() -> None:
    """Sessions without an agent (degraded init) emit nothing — never crash."""
    session = _fake_session()
    session.agent = None
    lines: list[dict] = []
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    bridge._emit_line = lines.append

    bridge._emit_budget_state()

    assert [line for line in lines if line.get("type") == "budget_state"] == []


def _run_tool_approval_with_captured_lines(bridge, session) -> list[dict]:
    lines: list[dict] = []
    bridge._session = session
    bridge._emit_line = lines.append
    bridge._await_approval = lambda request_id, reply_q: "once"  # type: ignore[method-assign]
    bridge._on_tool_approval("workflow_run", "run deploy", {"wf": "demo"})
    return lines


def test_bridge_tool_approval_dual_emits_canonical_and_legacy() -> None:
    """Approval requests carry the T1 envelope alongside the legacy shape
    (one deprecation window; JSONL clients tolerate unknown keys)."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    lines = _run_tool_approval_with_captured_lines(bridge, session)

    canonical = [line for line in lines if line.get("type") == "approval_request"]
    legacy = [line for line in lines if "approval_request" in line]
    assert len(canonical) == 1
    assert len(legacy) == 1
    assert legacy[0]["approval_request"]["tool"] == "workflow_run"


def test_bridge_tool_approval_canonical_payload_fields() -> None:
    """The canonical approval envelope carries the canonical tool/risk/session."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    lines = _run_tool_approval_with_captured_lines(bridge, session)

    payload = [line for line in lines if line.get("type") == "approval_request"][0]["payload"]
    assert payload["tool"] == "workflow_run"
    assert payload["risk_tier"] == "risky"
    assert payload["session_id"] == "session-1"


def test_bridge_tool_approval_canonical_matches_legacy_fields() -> None:
    """Canonical request ids + arguments mirror the legacy approval line."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    lines = _run_tool_approval_with_captured_lines(bridge, session)

    payload = [line for line in lines if line.get("type") == "approval_request"][0]["payload"]
    legacy = [line for line in lines if "approval_request" in line][0]["approval_request"]
    assert payload["request_id"] == legacy["id"]
    assert payload["arguments"] == {"wf": "demo"}


def test_bridge_cancel_turn_requests_active_cancel() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    token = TurnCancellation()
    bridge._active_turn_cancel = token

    bridge.cancel_turn({})

    assert token.is_set()


def test_bridge_skill_and_permission_methods_update_session() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    skill_state = bridge.activate_skill({"name": "hpc"})
    # ADR-104 D-1: the permission mode follows the one mode (set_permission_mode retired).
    permission_state = bridge.set_interaction_mode({"mode": "autopilot"})["state"]

    assert any(skill["name"] == "hpc" and skill["active"] for skill in skill_state["skills"])
    assert permission_state["session"]["permission_mode"] == PermissionMode.TRUST.value
    assert "set_permission_mode" not in BRIDGE_RPC_NAMES


def test_bridge_dispatch_slash_command_collects_output() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/skills list"})

    assert result["handled"] is True
    assert any("research" in line["text"] for line in result["output"])


def test_bridge_dispatch_usage_command_collects_usage_output() -> None:
    session = _fake_session()
    session.usage_report = MagicMock(
        return_value={
            "summary": {
                "llm_calls": 2,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "budget": {"node_hours_estimated": 0.25},
        }
    )
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/usage"})

    assert result["handled"] is True
    text = "\n".join(line["text"] for line in result["output"])
    assert "LLM calls: 2" in text
    assert "Node-hours: 0.25" in text


def test_bridge_dispatch_export_previews_markdown() -> None:
    session = _fake_session()
    session.export_markdown = MagicMock(return_value="# Demo\n\n## User\nHello\n")
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/export"})

    assert result["handled"] is True
    text = "\n".join(line["text"] for line in result["output"])
    assert "session export" in text.lower()
    assert "# Demo" in text


def test_bridge_dispatch_export_writes_markdown_file(tmp_path) -> None:
    session = _fake_session()
    session.export_markdown = MagicMock(return_value="# Demo\n")
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    out = tmp_path / "session.md"

    result = bridge.dispatch_slash_command({"command": f"/export {out}"})

    assert result["handled"] is True
    assert out.read_text(encoding="utf-8") == "# Demo\n"
    assert any("saved to" in line["text"] for line in result["output"])


def test_bridge_dispatch_progress_without_harness_is_graceful() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/progress"})

    assert result["handled"] is True
    assert any("unavailable" in line["text"] for line in result["output"])


def test_bridge_dispatch_probe_and_progress_with_harness() -> None:
    session = _fake_session()
    progress = SimpleNamespace(orientation=MagicMock(return_value="[Session Continuity]\n○ step-1"))
    harness = SimpleNamespace(progress=progress, refresh_probe=MagicMock())
    session.harness = harness
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    progress_result = bridge.dispatch_slash_command({"command": "/progress"})
    probe_result = bridge.dispatch_slash_command({"command": "/probe"})

    assert progress_result["handled"] is True
    assert any("Session Continuity" in line["text"] for line in progress_result["output"])
    assert probe_result["handled"] is True
    assert any("probe refreshed" in line["text"] for line in probe_result["output"])
    harness.refresh_probe.assert_called_once()


def test_bridge_dispatch_lessons_without_harness_is_graceful() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/lessons"})

    assert result["handled"] is True
    assert not any("unknown command" in line["text"].lower() for line in result["output"])
    assert any("unavailable" in line["text"] for line in result["output"])


def test_bridge_dispatch_lessons_with_harness() -> None:
    session = _fake_session()
    lesson = SimpleNamespace(
        rule="Use r4 model stage for downloads", created_at="2026-06-01T12:00:00"
    )
    steering = SimpleNamespace(
        list_lessons=MagicMock(return_value=[lesson]),
        clear=MagicMock(),
    )
    harness = SimpleNamespace(steering=steering)
    session.harness = harness
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/lessons"})

    assert result["handled"] is True
    assert any("Steering lessons" in line["text"] for line in result["output"])
    assert any("model stage" in line["text"] for line in result["output"])
    steering.list_lessons.assert_called_once()


def test_bridge_dispatch_template_without_harness_is_graceful() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/template list"})

    assert result["handled"] is True
    assert not any("unknown command" in line["text"].lower() for line in result["output"])
    assert any("unavailable" in line["text"] for line in result["output"])


def test_bridge_dispatch_template_with_harness() -> None:
    session = _fake_session()
    harness = MagicMock()
    template = SimpleNamespace(name="data_analysis", description="Exploratory data analysis")
    harness.list_templates.return_value = [template]
    harness.active_template.return_value = None
    session.harness = harness
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/template list"})

    assert result["handled"] is True
    assert any("Harness templates" in line["text"] for line in result["output"])
    assert any("data_analysis" in line["text"] for line in result["output"])


def test_bridge_dispatch_template_apply() -> None:
    session = _fake_session()
    harness = MagicMock()
    harness.apply_template.return_value = {
        "template": "experiment_design",
        "description": "Design and run computational experiments on HPC",
        "activated_skills": ["hpc", "code"],
        "missing_skills": [],
        "milestones_added": ["Define experiment parameters", "Run experiments"],
    }
    session.harness = harness
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/template experiment_design"})

    assert result["handled"] is True
    assert any("template → experiment_design" in line["text"] for line in result["output"])
    harness.apply_template.assert_called_once_with("experiment_design")


def test_bridge_backend_dashboard_collects_backend_state() -> None:
    session = _fake_session()
    session.client.list_endpoints = MagicMock(
        return_value={
            "endpoints": [
                {
                    "instance_id": "ep-1",
                    "status": "ready",
                    "model": "qwen",
                    "backend": "slurm",
                    "host": "node-a",
                    "port": 8001,
                }
            ]
        }
    )
    session.client.jobs = SimpleNamespace(
        list=MagicMock(return_value=[SimpleNamespace(job_id="job-1", status="running")])
    )
    session.client.workflows = SimpleNamespace(
        list_workflows=MagicMock(return_value=[SimpleNamespace(name="wf-demo", status="available")])
    )
    session.client.rag = SimpleNamespace(
        list_documents=MagicMock(return_value=[{"document_id": "doc-1", "filename": "paper.pdf"}])
    )
    session.client.mcp = SimpleNamespace(list_tools=MagicMock(return_value=[{"name": "search"}]))
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/dashboard"})

    dashboard = result["dashboard"]
    assert result["handled"] is True
    assert dashboard["readiness"]["ready"] is True
    assert dashboard["endpoints"][0]["id"] == "ep-1"
    assert dashboard["jobs"][0]["id"] == "job-1"
    assert dashboard["workflows"][0]["name"] == "wf-demo"
    assert dashboard["rag_documents"][0]["name"] == "paper.pdf"
    assert dashboard["mcp_tools"] == ["search"]


def test_bridge_handle_request_wraps_errors() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    response = bridge.handle_request({"id": 7, "method": "missing", "params": {}})

    assert response["id"] == 7
    assert response["ok"] is False
    assert "unknown method" in response["error"]["message"]


def test_bridge_list_models_returns_options_and_current() -> None:
    session = _fake_session()
    session.client.list_models = MagicMock(return_value=["served-a", "served-b"])
    session.client.list_endpoints = MagicMock(
        return_value={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "endpoint-model"},
            ]
        }
    )
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.list_models({})

    names = {option["name"] for option in result["models"]}
    assert "endpoint-model" in names
    assert result["current"] == "test-model"


def test_bridge_list_models_excludes_failed_endpoints() -> None:
    session = _fake_session()
    session.client.list_endpoints = MagicMock(
        return_value={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "ready-model"},
                {"instance_id": "ep-2", "status": "failed", "model": "failed-model"},
            ]
        }
    )
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.list_models({})

    names = {option["name"] for option in result["models"]}
    assert "ready-model" in names
    assert "failed-model" not in names


def test_bridge_list_models_includes_public_sources() -> None:
    """Public BYOK sources must appear in /models as model@source."""
    from unittest.mock import patch

    from r4.model_routing import SourceProbe
    from r4.tui import model_options

    session = _fake_session()
    session.client.list_endpoints = MagicMock(return_value={"endpoints": []})
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    # The collector reads the detailed probe pass (failures stay named,
    # 2026-10-20 seam); kind comes from the probe row as the store reports it.
    fake_probed = [
        SourceProbe(source_name="byok", kind="public", models=("qwen3.6-35b", "kimi-k3")),
        SourceProbe(source_name="rccs-cloud", kind="platform", models=("platform-model",)),
    ]

    with (
        patch("r4.source.get_source_store") as mock_store,
        patch("r4.model_routing.probe_sources_detailed", return_value=fake_probed),
    ):
        mock_store.return_value.get_active.return_value = "rccs-cloud"
        model_options.reset_extra_source_model_cache()
        result = bridge.list_models({})

    names = {option["name"] for option in result["models"]}
    assert "qwen3.6-35b@byok" in names
    assert "kimi-k3@byok" in names
    # Active platform source is covered by endpoint options, not extra-source probe.
    assert "platform-model@rccs-cloud" not in names


def test_model_is_provisioned_for_public_source_without_legacy_endpoint() -> None:
    """model@<public-source> is provisioned even with no legacy endpoint configured.

    Regression for the picker/provision mismatch: selecting a RiKyu model from
    /models must not raise "not provisioned" on the next turn when the profile
    has no endpoint_url / endpoint_provider.
    """
    from unittest.mock import patch

    from r4.companion.model_resolution import model_is_provisioned
    from r4.source import Source

    session = _fake_session()
    # No legacy public endpoint on the profile.
    session.client.config.endpoint_url = None
    session.client.config.endpoint_provider = None
    session.client.list_endpoints = MagicMock(return_value={"endpoints": []})

    rikyu = Source(name="rikyu", kind="public", url="https://api.rikyu.test", api_key="k")
    with patch("r4.source.get_source_store") as mock_store:
        mock_store.return_value.list.return_value = [rikyu]
        mock_store.return_value.get.return_value = rikyu
        assert model_is_provisioned(session.client, "qwen3.6-35b@rikyu") is True


def test_bridge_list_up_models_returns_registry_options() -> None:
    from unittest.mock import patch

    session = _fake_session()
    session.client.list_endpoints = MagicMock(return_value={"endpoints": []})
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    with patch(
        "r4.tui.commands._registry_model_options",
        return_value=[{"name": "registry-model", "source": "registry", "status": "available"}],
    ):
        result = bridge.list_up_models({})

    assert result["models"][0]["name"] == "registry-model"
    assert result["models"][0]["label"] == "registry-model (available)"
    assert result["session_model"] == "test-model"


def test_bridge_list_stoppable_endpoints_filters_terminal_statuses() -> None:
    session = _fake_session()
    session.client.list_endpoints = MagicMock(
        return_value={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "m1"},
                {"instance_id": "ep-2", "status": "failed", "model": "m2"},
            ]
        }
    )
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.list_stoppable_endpoints({})

    assert len(result["endpoints"]) == 1
    assert result["endpoints"][0]["id"] == "ep-1"


def test_bridge_get_welcome_returns_brand_banner() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session

    result = bridge.get_welcome({})

    assert "R4 Companion" in result["banner"]
    assert "readiness" in result


def test_bridge_set_interaction_mode_cycles_and_persists() -> None:
    session = _fake_session()
    store = FakeStore(session)
    bridge = CompanionBridgeServer(client=session.client, store=store)

    result = bridge.set_interaction_mode({})

    assert result["mode"] == "plan"
    assert session.metadata["interaction_mode"] == "plan"
    assert store.saved


def test_bridge_dispatch_approve_is_retired() -> None:
    """ADR-104 D-1: /approve and /allow-all are gone; /mode is the switch."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/approve deny"})

    assert result["state"]["session"]["permission_mode"] != PermissionMode.DENY.value


def test_bridge_trust_is_autopilot_and_leaving_it_drops_trust() -> None:
    """ADR-104 defects 1-2: /trust (or an explicit permission pick) used to
    pin a sticky override that outlived every later mode switch."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    trust = bridge.dispatch_slash_command({"command": "/trust"})
    switched = bridge.set_interaction_mode({"mode": "chat"})

    assert trust["state"]["session"]["permission_mode"] == PermissionMode.TRUST.value
    assert trust["state"]["session"]["interaction_mode"] == "autopilot"
    assert switched["state"]["session"]["permission_mode"] == PermissionMode.AUTO_SAFE.value
    assert "permission_override" not in session.metadata


def test_bridge_yolo_alias_maps_to_trust() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.dispatch_slash_command({"command": "/yolo"})

    assert result["handled"] is True
    assert result["state"]["session"]["permission_mode"] == PermissionMode.TRUST.value


def test_bridge_set_model_updates_session_and_snapshot() -> None:
    session = _fake_session()
    store = FakeStore(session)
    bridge = CompanionBridgeServer(client=session.client, store=store)

    snapshot = bridge.set_model({"model": "new-model"})

    assert session.agent.model == "new-model"
    assert snapshot["session"]["model"] == "new-model"
    assert store.saved[-1] == "session-1"


def test_bridge_get_code_context_detects_local_sources(tmp_path) -> None:
    (tmp_path / "analysis.R").write_text("print('hello')\n")
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    result = bridge.get_code_context({"path": str(tmp_path)})

    assert result["code_context"]["primary_language"] == "r"
    assert result["code_context"]["languages"]["r"] == 1


def test_bridge_parse_ask_user_answer_matches_id() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    line = json.dumps({"ask_user_response": {"id": 3, "answer": "use /mnt/c/fixture"}})
    assert bridge._parse_ask_user_answer(line, 3) == "use /mnt/c/fixture"
    assert bridge._parse_ask_user_answer(line, 4) is None
    assert (
        bridge._parse_ask_user_answer(
            json.dumps({"approval_response": {"id": 3, "decision": "once"}}),
            3,
        )
        is None
    )
    assert bridge._parse_ask_user_answer("", 1) == "[ask_user cancelled: bridge closed]"
    assert (
        bridge._parse_ask_user_answer(
            json.dumps({"ask_user_response": {"id": 1, "answer": None}}),
            1,
        )
        == ""
    )


def test_bridge_parse_approval_ignores_ask_user_traffic() -> None:
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))

    assert (
        bridge._parse_approval_decision(
            json.dumps({"ask_user_response": {"id": 1, "answer": "x"}}),
            1,
        )
        is None
    )
    assert (
        bridge._parse_approval_decision(
            json.dumps({"approval_response": {"id": 2, "decision": "once"}}),
            2,
        )
        == "once"
    )


def test_bridge_configure_session_wires_ask_user_callback(monkeypatch) -> None:
    from r4.agent import tools as agent_tools

    monkeypatch.setattr(agent_tools, "_ask_user_callback", None)
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._configure_session(session)

    callback = agent_tools._ask_user_callback
    assert callback is not None
    assert callback.__self__ is bridge
    assert callback.__func__ is CompanionBridgeServer._on_ask_user
    monkeypatch.setattr(agent_tools, "_ask_user_callback", None)


def _forward_tool_success(bridge, lines, *, name="lab_file_read", args=None):
    """Drive _forward_event with tool_call + tool_result, capturing lines."""
    from r4.companion.agent import CompanionEvent, CompanionEventType

    bridge._emit_line = lines.append
    bridge._forward_event(
        CompanionEvent(
            CompanionEventType.TOOL_CALL,
            "Calling tool",
            data={"name": name, "tool_call_id": "tc-7", "arguments": args or {}},
        )
    )
    bridge._forward_event(
        CompanionEvent(
            CompanionEventType.TOOL_RESULT,
            "120 ok chars",
            data={"name": name, "tool_call_id": "tc-7", "status": "ok", "duration_ms": 21},
        )
    )


def test_bridge_tool_events_emit_canonical_and_legacy_lines() -> None:
    """Canonical tool lines coexist with legacy event lines (window)."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    lines: list[dict] = []

    _forward_tool_success(bridge, lines, args={"s3_key": "data/x.csv"})

    types = [line.get("type") for line in lines]
    assert types.count("tool_call_start") == 1
    assert types.count("tool_call_end") == 1
    assert sum(1 for line in lines if "event" in line) == 2


def test_bridge_tool_canonical_payloads_carry_fields() -> None:
    """Canonical payloads hold the tool name, status, and duration."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    lines: list[dict] = []

    _forward_tool_success(bridge, lines, args={"s3_key": "data/x.csv"})

    start = next(line for line in lines if line.get("type") == "tool_call_start")
    end = next(line for line in lines if line.get("type") == "tool_call_end")
    assert start["payload"]["name"] == "lab_file_read"
    assert end["payload"]["status"] == "done"
    assert end["payload"]["duration_ms"] == 21


def test_bridge_tool_start_summary_is_sanitized() -> None:
    """args_summary allowlist applies; raw arguments never cross."""
    session = _fake_session()
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    lines: list[dict] = []

    _forward_tool_success(bridge, lines, args={"s3_key": "data/x.csv", "api_key": "sk-topsecret1"})

    payload = [line for line in lines if line.get("type") == "tool_call_start"][0]["payload"]
    assert payload["args_summary"] == "data/x.csv"
    assert "sk-topsecret1" not in str(payload)
    assert payload["tool_call_id"] == "tc-7"


def _attach_usage_state(session: SimpleNamespace, *, prompt_tokens: int) -> None:
    """Give the fake session one recorded LLM round of usage."""
    turn = SimpleNamespace(total_tokens=prompt_tokens + 40, cost_usd=0.0, model="test-model")
    session.client.session_cost = SimpleNamespace(
        turns=[turn],
        total_tokens=turn.total_tokens,
        total_cost_usd=0.0,
    )
    session.usage_records = [SimpleNamespace(prompt_tokens=prompt_tokens)]


def test_bridge_tool_start_emits_live_cost_update() -> None:
    """Ctx chip updates DURING the turn: each tool-start carries the round's
    freshly recorded context numbers (usage lands before tools execute)."""
    from r4.companion.agent import CompanionEvent, CompanionEventType

    session = _fake_session()
    _attach_usage_state(session, prompt_tokens=12_345)
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    lines: list[dict] = []
    bridge._emit_line = lines.append

    bridge._forward_event(
        CompanionEvent(
            CompanionEventType.TOOL_CALL,
            "Calling tool",
            data={"name": "shell_exec", "tool_call_id": "tc-9", "arguments": {}},
        )
    )

    cost_lines = [line for line in lines if line.get("type") == "cost"]
    assert len(cost_lines) == 1
    payload = cost_lines[0]["payload"]
    assert payload["context_used_tokens"] == 12_345
    assert payload["context_limit_tokens"] > 0


def test_bridge_tool_start_cost_reflects_latest_round() -> None:
    """A second round's larger prompt supersedes the first — the chip tracks
    context growth round over round, not just at turn end."""
    from r4.companion.agent import CompanionEvent, CompanionEventType

    session = _fake_session()
    _attach_usage_state(session, prompt_tokens=10_000)
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    lines: list[dict] = []
    bridge._emit_line = lines.append

    def _call(call_id: str) -> None:
        bridge._forward_event(
            CompanionEvent(
                CompanionEventType.TOOL_CALL,
                "Calling tool",
                data={"name": "file_read", "tool_call_id": call_id, "arguments": {}},
            )
        )

    _call("tc-1")
    # Round 2's LLM call records a bigger prompt before its tools dispatch.
    turn2 = SimpleNamespace(total_tokens=22_040, cost_usd=0.0, model="test-model")
    session.client.session_cost.turns.append(turn2)
    session.usage_records.append(SimpleNamespace(prompt_tokens=22_000))
    _call("tc-2")

    cost_payloads = [line["payload"] for line in lines if line.get("type") == "cost"]
    assert [p["context_used_tokens"] for p in cost_payloads] == [10_000, 22_000]


# ── ACP parity surfaces: MCP / knowledge content / workflow describe ────────────


def _parity_bridge() -> CompanionBridgeServer:
    session = _fake_session()
    session.client.mcp = SimpleNamespace(
        list_servers=lambda: [
            {
                "name": "filesystem",
                "server_id": "srv-1",
                "status": "healthy",
                "url": "http://fs:8080",
            }
        ],
        list_tools=lambda server=None: [
            SimpleNamespace(
                name="read_file",
                description="Read a file",
                parameters={"type": "object"},
                server="filesystem",
            )
        ],
        check_server_health=lambda server_id: {
            "server_id": server_id,
            "status": "healthy",
        },
    )
    session.client.knowledge = SimpleNamespace(
        graph_content=lambda graph_id, **kwargs: {
            "graph_id": graph_id,
            "name": "experiment-history",
            "nodes": [{"id": "n1", "type": "finding", "name": "f1"}],
            "edges": [],
            "truncated": False,
        }
    )
    session.client.workflows = SimpleNamespace(
        describe=lambda name: {
            "workflow_name": name,
            "description": "demo",
            "parameter_schema": {"type": "object", "properties": {}},
            "steps": [{"name": "ingest"}, {"name": "analyze"}],
        }
    )
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    return bridge


def test_bridge_list_mcp_servers_and_tools() -> None:
    bridge = _parity_bridge()

    servers = bridge._dispatch("list_mcp_servers", {})
    assert servers["servers"][0]["name"] == "filesystem"
    assert servers["servers"][0]["status"] == "healthy"

    tools = bridge._dispatch("list_mcp_tools", {"server": "filesystem"})
    tool = tools["tools"][0]
    assert tool["name"] == "read_file"
    assert tool["server"] == "filesystem"
    assert tool["input_schema"] == {"type": "object"}


def _write_user_mcp_config(tmp_path, monkeypatch, body: str) -> None:
    (tmp_path / "r4").mkdir()
    (tmp_path / "r4" / "mcp-servers.json").write_text(body)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


_MERGE_CONFIG = (
    '{"mcpServers": {"context7": {"type": "http", "url": "https://mcp.context7.com/mcp"},'
    ' "fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]}}}'
)


def _merged_servers_by_name(tmp_path, monkeypatch) -> dict:
    _write_user_mcp_config(tmp_path, monkeypatch, _MERGE_CONFIG)
    bridge = _parity_bridge()
    servers = bridge._dispatch("list_mcp_servers", {})["servers"]
    return {s["name"]: s for s in servers}


def test_bridge_list_mcp_servers_merges_user_config(tmp_path, monkeypatch) -> None:
    by_name = _merged_servers_by_name(tmp_path, monkeypatch)
    assert by_name["filesystem"]["source"] == "registry"
    assert by_name["filesystem"]["agent_visible"] is True
    for name in ("context7", "fetch"):
        assert by_name[name]["source"] == "user-config:user"
        assert by_name[name]["agent_visible"] is False


def test_bridge_user_mcp_rows_carry_actionable_hint(tmp_path, monkeypatch) -> None:
    by_name = _merged_servers_by_name(tmp_path, monkeypatch)
    assert by_name["fetch"]["status"].startswith("declared stdio")
    assert "not agent-visible" in by_name["context7"]["status"]
    assert "r4 mcp register context7" in by_name["context7"]["hint"]
    assert "--port 50800" in by_name["fetch"]["hint"]


def test_bridge_list_mcp_servers_survives_registry_outage(tmp_path, monkeypatch) -> None:
    _write_user_mcp_config(
        tmp_path, monkeypatch, '{"mcpServers": {"fetch": {"type": "stdio", "command": "uvx"}}}'
    )
    bridge = _parity_bridge()
    bridge.session.client.mcp.list_servers = MagicMock(side_effect=RuntimeError("500"))

    servers = bridge._dispatch("list_mcp_servers", {})["servers"]
    assert [s["name"] for s in servers] == ["fetch"]  # user rows still visible


def _toggle_mcp(bridge, name, enabled):
    return bridge._dispatch("set_mcp_server_enabled", {"name": name, "enabled": enabled})


def test_bridge_set_mcp_server_enabled_off_then_on(tmp_path, monkeypatch) -> None:
    from r4.companion import mcp_exposure

    _write_user_mcp_config(
        tmp_path, monkeypatch, '{"mcpServers": {"fetch": {"type": "stdio", "command": "uvx"}}}'
    )
    # Toggling on exposes the server: unstubbed, that spawned a real
    # remote_proxy which outlived the test run (orphaned 7h, 2026-10-09).
    monkeypatch.setattr(mcp_exposure, "expose_local_mcp", lambda *_a, **_k: {})
    bridge = _parity_bridge()
    res = _toggle_mcp(bridge, "fetch", False)
    assert res["applied"] is True
    fetch = [s for s in res["servers"] if s["name"] == "fetch"][0]
    assert fetch["enabled"] is False
    assert fetch["status"] == "disabled (off)"
    on_again = _toggle_mcp(bridge, "fetch", True)
    assert [s for s in on_again["servers"] if s["name"] == "fetch"][0]["enabled"] is True


def test_bridge_user_row_marked_visible_when_registered(tmp_path, monkeypatch) -> None:
    _write_user_mcp_config(
        tmp_path, monkeypatch, '{"mcpServers": {"filesystem": {"type": "stdio", "command": "x"}}}'
    )
    bridge = _parity_bridge()
    servers = bridge._dispatch("list_mcp_servers", {})["servers"]
    user_row = [s for s in servers if str(s.get("source", "")).startswith("user-config")][0]
    assert user_row["agent_visible"] is True
    assert user_row["status"].startswith("registered ✓")


def test_bridge_toggle_on_triggers_exposure(tmp_path, monkeypatch) -> None:
    from r4.companion import mcp_exposure

    _write_user_mcp_config(
        tmp_path, monkeypatch, '{"mcpServers": {"fetch": {"type": "stdio", "command": "uvx"}}}'
    )
    bridge = _parity_bridge()
    calls = []
    monkeypatch.setattr(
        mcp_exposure,
        "expose_local_mcp",
        lambda client, name, description="": calls.append(name) or {},
    )
    res = bridge._dispatch("set_mcp_server_enabled", {"name": "fetch", "enabled": True})
    assert calls == ["fetch"]
    assert "exposure" in res


def test_bridge_set_mcp_server_enabled_rejects_unknown_name(tmp_path, monkeypatch) -> None:
    _write_user_mcp_config(
        tmp_path, monkeypatch, '{"mcpServers": {"a": {"type": "http", "url": "https://a"}}}'
    )
    bridge = _parity_bridge()
    missing = _toggle_mcp(bridge, "ghost", True)
    assert missing["applied"] is False


def test_bridge_mcp_server_health_validation() -> None:
    bridge = _parity_bridge()

    health = bridge._dispatch("mcp_server_health", {"server_id": "srv-1"})
    assert health["status"] == "healthy"

    try:
        bridge._dispatch("mcp_server_health", {})
    except ValueError as exc:
        assert "server_id" in str(exc)
    else:  # pragma: no cover - contract guard
        raise AssertionError("missing server_id must raise")


def test_bridge_knowledge_graph_content() -> None:
    bridge = _parity_bridge()

    content = bridge._dispatch("knowledge_graph_content", {"graph_id": "g-abc"})
    assert content["graph_id"] == "g-abc"
    assert content["nodes"][0]["type"] == "finding"
    assert content["truncated"] is False

    try:
        bridge._dispatch("knowledge_graph_content", {})
    except ValueError as exc:
        assert "graph_id" in str(exc)
    else:
        raise AssertionError("missing graph_id must raise")


def test_bridge_workflow_describe() -> None:
    bridge = _parity_bridge()

    describe = bridge._dispatch("workflow_describe", {"workflow_name": "demo-wf"})
    assert describe["workflow_name"] == "demo-wf"
    assert [step["name"] for step in describe["steps"]] == ["ingest", "analyze"]

    try:
        bridge._dispatch("workflow_describe", {})
    except ValueError as exc:
        assert "workflow_name" in str(exc)
    else:
        raise AssertionError("missing workflow_name must raise")


def test_rebuild_propagates_fresh_client_into_session_and_agent() -> None:
    """Project-switch lineage bug: after `_rebuild_client_after_config_reload`,
    the snapshot must describe the NEW client (badge/chat follow the switch)."""
    session = _fake_session()
    session.client = SimpleNamespace(config=SimpleNamespace(project_id="opposum", gateway_url="g"))
    session.agent = SimpleNamespace(model="m", client=session.client)
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge._session = session
    bridge._client = None  # force the lazy rebuild to mint a new Client()

    bridge._rebuild_client_after_config_reload()

    fresh = bridge.client
    assert fresh is bridge.client  # property minted once, kept
    assert bridge._session.client is fresh
    assert bridge._session.agent.client is fresh
    assert bridge._session.client.config.project_id != "opposum"
