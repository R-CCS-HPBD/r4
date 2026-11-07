"""Automated coverage tests for the TUI ↔ Backend integration audit.

These tests enforce the invariants documented in
``docs/architecture/TUI_BACKEND_AUDIT.md``:

1. Every JS TUI RPC method is registered in the bridge ``_dispatch`` table.
2. Every slash command registered in ``SlashRegistry`` has a working handler.
3. The bridge returns bounded, JSON-serializable state for every method.
4. UMRS API route files parse without import errors (smoke test).
5. No orphan handlers exist in ``SlashRegistry`` (handler with no command).

Running these tests alongside the existing TUI suite catches regressions
when new RPC methods, slash commands, or UMRS endpoints are added without
corresponding TUI wiring.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.permissions import PermissionMode
from r4.companion.session_models import SessionMessage
from r4.companion.skills import SkillRegistry
from r4.companion.ui.bridge_server import CompanionBridgeServer
from r4.tui.commands import get_slash_registry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeStore:
    """Minimal session store for bridge tests."""

    def __init__(self, session: SimpleNamespace) -> None:
        self.session = session
        self.saved: list[str] = []

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
    """Build a fake companion session for bridge tests.

    The session mirrors the public surface of ``CompanionSession`` so the
    bridge server can call ``send``, ``request_turn_cancel``, ``usage_report``,
    and ``export_markdown`` without hitting the network.
    """
    client = SimpleNamespace(
        config=SimpleNamespace(
            profile="research",
            gateway_url="http://gateway.test",
            project_id="demo",
            model="config-model",
            endpoint_url=None,
            endpoint_provider=None,
            timeout=15.0,
        ),
        readiness=MagicMock(return_value={"ready": True}),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [{"instance_id": "ep-test", "status": "ready", "model": "test-model"}]
            }
        ),
    )
    messages: list[SessionMessage] = []

    def send(text: str) -> str:
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
        permissions=PermissionManagerStub(),
        context=SimpleNamespace(_memory={}),
        active_endpoints=[],
        metadata={},
        _harness=None,
        send=send,
        request_turn_cancel=MagicMock(),
        usage_report=MagicMock(return_value={"total_tokens": 0}),
        export_markdown=MagicMock(return_value="# Demo"),
    )


class PermissionManagerStub:
    """Stub permission manager matching the real public surface."""

    ASK_ALWAYS = PermissionMode.ASK_ALWAYS
    AUTO_SAFE = PermissionMode.AUTO_SAFE
    TRUST = PermissionMode.TRUST
    DENY = PermissionMode.DENY

    def __init__(self) -> None:
        self.mode = PermissionMode.ASK_ALWAYS

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode

    def set_approval_callback(self, callback) -> None:
        pass

    def approve_for_session(self, tool_name: str) -> None:
        pass


@pytest.fixture
def bridge() -> CompanionBridgeServer:
    session = _fake_session()
    server = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    server.initialize({})
    return server


# ---------------------------------------------------------------------------
# 1. Bridge dispatch table coverage
# ---------------------------------------------------------------------------

# Expected RPC methods based on the JS TUI (r4/companion-tui-js/src/index.mjs)
# and documented in docs/architecture/TUI_BACKEND_AUDIT.md.
EXPECTED_BRIDGE_METHODS = {
    "activate_skill",
    "add_anti_pattern",
    "add_lesson",
    "add_pattern",
    "add_task",
    "assets.patch",
    "assets.snapshot",
    "quota.units",
    "circles.list",
    "circles.members",
    # ADR-086 amendment 2026-10-19: console roster write verbs.
    "circles.create",
    "circles.member_add",
    "circles.member_remove",
    "usage.me",
    "files.upload",
    "files.download",
    "explain",
    "explain.match",
    "sweep.templates",
    "sweep.preview",
    "evidence.list",
    "citation.open",
    "knowledge.save",
    "grants.mine",
    "loops.list",
    "loops.status",
    "loops.stop",
    "loops.pause",
    "loops.resume",
    "traces.get",
    "traces.job",
    "traces.execution",
    "provenance.job",
    "provenance.lineage",
    "rag.query",
    "admin.rag_stats",
    "admin.corpus_attrition",
    "cancel_turn",
    "confirm_plan",
    "dispatch_slash_command",
    "export_session",
    "get_agent_status",
    "get_agent_tree",
    "get_artifacts",
    "get_backend_dashboard",
    "get_code_context",
    "get_event_log",
    "get_job",
    "get_job_log",
    "get_knowledge",
    "get_resources",
    "get_science_loop",
    # W1 (2026-09-08): session-diff capture + governance overlay reads.
    "get_governance_overview",
    "get_session_changes",
    "get_session_plan",
    # ADR-075 G2 (2026-09-30): session workflow arm toggle.
    "set_workflow_armed",
    # 2026-10-01: one role agent → its own chat session (agent tabs).
    # W2 (2026-09-08): persist server-side trust rows from the approval overlay.
    "trust_grant",
    "trust_list",
    # W3 (2026-09-08): per-file revert from the session's recorded before state.
    "revert_session_change",
    "get_state",
    "get_status",
    "get_task",
    "get_usage",
    "get_welcome",
    "initialize",
    "knowledge_graph_content",
    "list_active_agents",
    "list_checkpoints",
    "list_jobs",
    "list_mcp_servers",
    "list_mcp_tools",
    "list_models",
    "list_sessions",
    "list_skills",
    "list_slash_commands",
    "list_stoppable_endpoints",
    "list_tasks",
    "list_up_models",
    "load_latest_checkpoint",
    "load_session",
    "mcp_server_health",
    "project_cancel_job",
    "project_create",
    "project_register",  # /project register (2026-11-03)
    "project_current",
    "project_delete",
    "project_import",
    "project_init",
    "project_findings",
    "project_ingest",
    "project_list",
    "project_plan",
    "project_readiness",
    "project_run",
    "project_session_create",
    "project_status",
    "project_switch",
    "project_verify",
    "record_science_iteration",
    "replay_session",
    "save_checkpoint",
    "send_message",
    "set_interaction_mode",
    "set_knowledge_acquisition",
    "set_mcp_server_enabled",
    "set_model",
    "permission_rules.list",
    "permission_rules.remove",
    "shutdown",
    "stop_job",
    "summarize_session",
    "update_task",
    "workflow.list",
    "workflow.status",
    "workflow.run",
    "workflow.cancel",
    "workflow.results",
    "workflow.detail",
    "workflow.approve_gate",
    "workflow_describe",
    # 2026-10-01: definition+codes package for the work-pane card.
    "workflow.package",
    "workflow.export",
    # T5 (2026-09-19): teamup multi-agent session manager (verbs live on the
    # bridge; rows/secrets stay server-only; no surprise surface).
    "teamup.hardware",
    "teamup.create",
    "teamup.tailor",
    "teamup.crews",
    "teamup.engines",
    "teamup.list",
    "teamup.status",
    "teamup.run",
    "teamup.spawn_agent_session",
    "teamup.start",
    "teamup.pause",
    "teamup.resume",
    "teamup.cancel",
    "teamup.logs",
    "teamup.report",
    # ADR-092 slice 3: team plans (the chat tools' executor) and every
    # TeamService run for the Work pane.
    "team.plan_templates",
    "team.plan_run",
    "team.plan_result",
    "team.plan_cancel",
    "team.runs",
    # Research-loop / session management (ADR-025 projection) — chat-first
    # read-only cards served to the TUI (2026-08-31 wave).
    "research_loop.list",
    "research_loop.status",
    "research_session.view",
    # Knowledge management (M3): knowledge graph status projection.
    "research_knowledge.status",
    # C2: session pin/rename (store-level, dedicated pinned_at column).
    # G-P9-2 (WP-08): session fork.
    "session.fork",
    "session.pin",
    "session.turns",
    "session.queue_append",
    "session.queue_pop",
    "session.queue_clear",
    "session.queue_remote",
    # Wave 2026-09-21: frontend-venue runtime + sealed sources from the TUI.
    "session.venue_start",
    "session.venue_status",
    "session.venue_stop",
    "session.venue_sync",
    "session.venue_approvals",
    "session.venue_decide",
    "session.venue_questions",
    "session.venue_answer",
    # ADR-099 D-2: the session's web-access choice and the remote caps preview.
    "session.web_access_get",
    "session.web_access_set",
    # ADR-102 Amendment 1: /search-server and the offer after /web Public.
    "search_server.status",
    "search_server.up",
    "search_server.down",
    # ADR-108 W1b: /worktree (2026-11-03).
    "worktree.list",
    "worktree.enter",
    "worktree.exit",
    "worktree.mode",
    "session.sandbox_get",
    "session.sandbox_set",
    "session.venue_policy",
    "source.list_remote",
    "source.push",
    "source.unpush",
    "set_write_requires_plan",
    "session.unpin",
    "session.rename",
    # Wave D: session archive (store-level; hidden from the default list).
    "session.archive",
    # C4: on-demand compaction (force-trigger regardless of context limit).
    "session.compact",
    # TUI audit r2 #17: rewind/edit-and-resend cuts the companion history by turn id.
    "session.truncate",
    "session.side_question",
}


def _extract_bridge_dispatch_methods() -> set[str]:
    """Read the LIVE bridge RPC table (WP-08: module-level bindings).

    The table moved out of ``_dispatch`` in WP-08 (capability parity); the
    audit now inspects the same object the runtime consults.
    """
    from r4.companion.ui.bridge_server import _bridge_rpc_bindings

    return set(_bridge_rpc_bindings(CompanionBridgeServer.__new__(CompanionBridgeServer)).keys())


pytestmark = [
    pytest.mark.skipif(
        importlib.util.find_spec("umrs") is None,
        reason="umrs package not installed (submodule not checked out)",
    )
]


def test_bridge_dispatch_table_contains_all_expected_methods() -> None:
    """Every method called by the JS TUI must be in the bridge _dispatch table."""
    actual = _extract_bridge_dispatch_methods()
    missing = EXPECTED_BRIDGE_METHODS - actual
    assert not missing, f"Missing bridge methods: {missing}"


def test_bridge_dispatch_table_has_no_extra_methods() -> None:
    """The bridge _dispatch table should not contain unexpected methods."""
    actual = _extract_bridge_dispatch_methods()
    extra = actual - EXPECTED_BRIDGE_METHODS
    assert not extra, f"Unexpected bridge methods: {extra}"


# ---------------------------------------------------------------------------
# 2. SlashRegistry consistency
# ---------------------------------------------------------------------------


def test_slash_registry_has_no_orphan_handlers() -> None:
    """Every handler in the SlashRegistry must correspond to a registered command."""
    registry = get_slash_registry()
    commands = registry.command_list()
    command_names = {cmd.name for cmd in commands}
    # The registry's internal _commands dict should only contain names that
    # are either primary command names or aliases.
    for name in registry._commands:
        cmd = registry._commands[name]
        assert cmd.name in command_names, f"Orphan handler: /{name} → {cmd.name}"


class _FakeSlashRenderer:
    """Minimal renderer for slash-command dispatch tests."""

    def __init__(self) -> None:
        self.info_lines: list[str] = []
        self.system_lines: list[str] = []
        self.error_lines: list[str] = []

    def info(self, text: str) -> None:
        self.info_lines.append(str(text))

    def system(self, text: str) -> None:
        self.system_lines.append(str(text))

    def error(self, text: str, *, correlation_id: str | None = None) -> None:
        self.error_lines.append(str(text))

    def clear(self) -> None:
        pass


class _FakeSlashApp:
    """Minimal app for slash-command dispatch tests."""

    def __init__(self, registry: Any) -> None:
        self.renderer = _FakeSlashRenderer()
        self.commands = registry
        self.mode = MagicMock()
        self.session = MagicMock()
        self.client = MagicMock()
        self.skill_registry = MagicMock()
        self.session_store = MagicMock()
        self.approver = MagicMock()
        self.local_tools: list[dict] = []
        self.mcp_manager = MagicMock()
        self.context_manager = MagicMock()
        self._exit_requested = False

    def request_exit(self) -> None:
        self._exit_requested = True

    def set_mode(self, mode) -> None:
        pass

    def send_user_message(self, text: str) -> None:
        pass

    def show_banner(self) -> None:
        pass


def test_slash_registry_dispatches_all_registered_commands() -> None:
    """Every registered slash command should be dispatchable without raising."""
    registry = get_slash_registry()
    app = _FakeSlashApp(registry)
    commands = registry.command_list()
    failed: list[tuple[str, str]] = []
    for cmd in commands:
        try:
            registry.dispatch(app, f"/{cmd.name}")
        except Exception as exc:
            failed.append((cmd.name, str(exc)))
    assert not failed, f"Failed to dispatch commands: {failed}"


# ---------------------------------------------------------------------------
# 3. Bridge state serialization
# ---------------------------------------------------------------------------


def test_bridge_snapshot_is_json_serializable(bridge: CompanionBridgeServer) -> None:
    """The bridge snapshot must be JSON-serializable for stdio transport."""
    snapshot = bridge.snapshot()
    serialized = json.dumps(snapshot, default=str)
    parsed = json.loads(serialized)
    assert "session" in parsed
    assert "messages" in parsed
    assert "skills" in parsed


def test_bridge_snapshot_bounded_message_count(bridge: CompanionBridgeServer) -> None:
    """Snapshot messages must be bounded by MAX_RECENT_MESSAGES."""
    from r4.companion.ui.bridge_server import MAX_RECENT_MESSAGES

    snapshot = bridge.snapshot()
    assert len(snapshot["messages"]) <= MAX_RECENT_MESSAGES


class _HintSession:
    class client:  # noqa: D106 — attribute shell for _session_identity_hint
        class config:  # noqa: D106
            profile = "p"


def _patch_auth(monkeypatch, token_info, api_key, claims):
    from types import SimpleNamespace

    import r4.auth as r4auth

    manager = SimpleNamespace(
        profile="p",
        token_store=SimpleNamespace(load=lambda _p: token_info),
        api_key_manager=SimpleNamespace(get_api_key=lambda _p: api_key),
    )

    def _decode(raw):
        if isinstance(claims, Exception):
            raise claims
        return claims.get(raw)

    monkeypatch.setattr(r4auth, "AuthManager", lambda profile="default": manager)
    monkeypatch.setattr(r4auth, "decode_jwt_claims", _decode)


def test_session_identity_hint_jwt(monkeypatch) -> None:
    from types import SimpleNamespace

    from r4.companion.ui.bridge_server import _session_identity_hint

    _patch_auth(
        monkeypatch,
        token_info=SimpleNamespace(access_token="tok"),
        api_key=None,
        claims={"tok": {"sub": "mnagaso"}},
    )
    assert _session_identity_hint(_HintSession()) == ("mnagaso", "jwt")


def test_session_identity_hint_api_key_and_none(monkeypatch) -> None:
    from r4.companion.ui.bridge_server import _session_identity_hint

    _patch_auth(monkeypatch, token_info=None, api_key="opaque-key", claims={"opaque-key": {}})
    assert _session_identity_hint(_HintSession()) == ("", "api-key")
    _patch_auth(monkeypatch, token_info=None, api_key=None, claims={})
    assert _session_identity_hint(_HintSession()) == ("", "none")


def test_session_identity_hint_failures_report_none(monkeypatch) -> None:
    from types import SimpleNamespace

    from r4.companion.ui.bridge_server import _session_identity_hint

    # A decode failure is display sugar, never a snapshot break — state reads
    # as "none" (the chip's honest fallback), not a fabricated account.
    _patch_auth(
        monkeypatch,
        token_info=SimpleNamespace(access_token="tok"),
        api_key=None,
        claims=RuntimeError("store gone"),
    )
    assert _session_identity_hint(_HintSession()) == ("", "none")


# ---------------------------------------------------------------------------
# 4. UMRS API smoke tests
# ---------------------------------------------------------------------------


def test_umrs_api_modules_import_without_errors() -> None:
    """Every UMRS API route file should import without errors."""
    api_modules = [
        "umrs.api.audit_events",
        "umrs.api.audit_store",
        "umrs.api.context",
        "umrs.api.context_schema",
        "umrs.api.egress_audit",
        "umrs.api.endpoints",
        "umrs.api.files",
        "umrs.api.governance",
        "umrs.api.jobs",
        "umrs.api.knowledge",
        "umrs.api.llm",
        "umrs.api.llm_usage",
        "umrs.api.mcp",
        "umrs.api.model_visibility",
        "umrs.api.models",
        "umrs.api.ops",
        "umrs.api.pipelines",
        "umrs.api.provenance",
        "umrs.api.rag",
        "umrs.api.rag_service",
        "umrs.api.reflective_memory",
        "umrs.api.scopes",
        "umrs.api.secret_broker",
        "umrs.api.skills",
        "umrs.api.traces",
        "umrs.api.usage",
        "umrs.api.vision",
        "umrs.api.workflows",
        "umrs.api.zmq_sessions",
    ]
    failed: list[tuple[str, str]] = []
    for module_name in api_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failed.append((module_name, str(exc)))
    assert not failed, f"Failed to import UMRS API modules: {failed}"


# ---------------------------------------------------------------------------
# 5. Bridge error handling
# ---------------------------------------------------------------------------


def test_bridge_unknown_method_returns_error() -> None:
    """Calling an unknown method should return a structured error, not crash."""
    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock(db_path=":memory:"))
    response = server.handle_request({"id": 1, "method": "nonexistent_method", "params": {}})
    assert response["ok"] is False
    assert "error" in response
    assert "unknown method" in response["error"]["message"]


def test_bridge_handle_request_propagates_handler_errors() -> None:
    """Handler exceptions should be caught and returned as structured errors."""
    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock(db_path=":memory:"))
    # set_model with empty model should raise ValueError.
    response = server.handle_request({"id": 1, "method": "set_model", "params": {"model": ""}})
    assert response["ok"] is False
    assert "model is required" in response["error"]["message"]


# ---------------------------------------------------------------------------
# 6. Slash command coverage (every registered command has a handler)
# ---------------------------------------------------------------------------


def test_every_registered_slash_command_has_callable_handler() -> None:
    """Every command in SlashRegistry must have a callable handler."""
    registry = get_slash_registry()
    for name, cmd in registry._commands.items():
        assert callable(cmd.handler), f"Handler for /{name} is not callable"
        # Handler should accept (app, args) signature.
        sig = inspect.signature(cmd.handler)
        params = list(sig.parameters.values())
        assert len(params) >= 2, f"Handler for /{name} must accept (app, args)"


def test_slash_registry_help_text_present() -> None:
    """Every command should have non-empty help text."""
    registry = get_slash_registry()
    for name, cmd in registry._commands.items():
        assert cmd.help, f"Command /{name} has empty help text"
        assert isinstance(cmd.help, str), f"Command /{name} help is not a string"


def test_slash_registry_categories_are_documented() -> None:
    """Every command category should be a non-empty string."""
    registry = get_slash_registry()
    for name, cmd in registry._commands.items():
        assert cmd.category, f"Command /{name} has empty category"
        assert isinstance(cmd.category, str), f"Command /{name} category is not a string"


# ---------------------------------------------------------------------------
# 7. Bridge method parameter validation
# ---------------------------------------------------------------------------


def test_bridge_send_message_requires_text() -> None:
    """send_message should reject empty text."""
    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock(db_path=":memory:"))
    response = server.handle_request({"id": 1, "method": "send_message", "params": {"text": ""}})
    assert response["ok"] is False
    assert "message text is required" in response["error"]["message"]


def test_bridge_set_model_requires_model_name() -> None:
    """set_model should reject empty model name."""
    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock(db_path=":memory:"))
    response = server.handle_request({"id": 1, "method": "set_model", "params": {"model": ""}})
    assert response["ok"] is False
    assert "model is required" in response["error"]["message"]


def test_bridge_set_interaction_mode_validates_mode() -> None:
    """set_interaction_mode should reject unknown modes."""
    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock(db_path=":memory:"))
    response = server.handle_request(
        {"id": 1, "method": "set_interaction_mode", "params": {"mode": "invalid_mode"}}
    )
    assert response["ok"] is False
    assert "unknown mode" in response["error"]["message"]


# ---------------------------------------------------------------------------
# 8. v3 TUI source consistency: every bridge verb it sends is served
# ---------------------------------------------------------------------------

# H-4.1 (2026-10-18): this test scanned r4/companion-tui-js/src/index.mjs, which
# no longer exists, so it always skipped. It now scans the v3 TUI sources.
_V3_ROOT = Path(__file__).resolve().parents[2] / "packages" / "r4-tui-v3"
_TS_GENERIC = r"(?:<[^()]*?>)?"
_TS_METHOD_LITERAL = re.compile(r"""["']([A-Za-z_][\w.]*)["']""")
_TS_REQUEST_CALL = re.compile(r"\.request" + _TS_GENERIC + r"\(\s*([^,)]*)")
# H-4.4: typed reads go through ``rpc(rail, "verb", …)`` (src/bridge/rpc.ts);
# the verb is its second argument.
_TS_RPC_CALL = re.compile(r"\brpc\(\s*[^,]+,\s*([^,)]*)")


def _ts_call_args(text: str) -> list[str]:
    """First-argument text of every ``.request(…)`` and verb of every ``rpc(…)``."""
    return _TS_REQUEST_CALL.findall(text) + _TS_RPC_CALL.findall(text)


# Call sites whose first argument is not a string literal: (file, argument
# text) → where the verb names live. A new variable-method site fails the
# test until it is added here, so no verb escapes the check.
#   union    — a string-literal union type of that field/parameter name
#   table    — the string literals that start the elements of that const array
#   property — every ``<name>: "verb"`` property in the file (slash specs)
#   const    — the value literals of ``const <name> = … ? "a" : "b";``
#   wrapper  — the literal first arguments of calls to that local function
#   expand   — a template literal; the verbs its code paths can build
#   typed    — the keys of that interface (rpc()'s verbs: RpcResultMap)
_TS_VARIABLE_METHOD_SOURCES: dict[tuple[str, str], tuple[str, str, str | None]] = {
    ("src/adapters/companion-chat.ts", "turn.method"): ("union", "method", None),
    ("src/bridge/companion-runtime-bridge.ts", "method"): ("table", "RESOURCE_FEEDS", None),
    ("src/commands/slash.ts", "spec.rpc"): ("property", "rpc", None),
    # handleProjectLongOp's ``spec.rpc``; /session pin|unpin; /loops and /loop
    # status|stop|pause|resume (LOOP_CONTROL_VERBS ∪ the /loops sub check).
    ("src/commands/slash.ts", "`session.${verb}`"): ("expand", "session.pin session.unpin", None),
    ("src/commands/slash.ts", "`loops.${sub}`"): (
        "expand",
        "loops.status loops.stop loops.pause loops.resume",
        None,
    ),
    # /health authz hands bridge.request to probeAuthz, which sends each verb.
    ("src/commands/wave-intercepts.ts", "method"): (
        "table",
        "READ_ONLY_PROBE_RPCS",
        "src/lib/authz-audit.ts",
    ),
    ("src/bridge/rpc.ts", "method"): ("typed", "RpcResultMap", "src/bridge/rpc-types.gen.ts"),
    # The queue rail moved to the shared lib/queue-rail.ts in VP-2b (ADR-103);
    # chat-queue-rail.ts is now the console's wrapper and sends no verb itself.
    ("src/lib/queue-rail.ts", "localVerb"): ("const", "localVerb", None),
    # /settings rules (ADR-104 D-3): list / remove through the companion.
    ("src/lib/settings-rules.ts", "method"): ("union", "method", None),
    ("src/lib/traces-notice.ts", "verb"): ("union", "verb", None),
    ("src/stores/loops.ts", "method"): ("wrapper", "_request", None),
    ("src/stores/teams.ts", "method"): ("wrapper", "_request", None),
    ("src/stores/umrs-workflow.ts", "method"): ("wrapper", "bridgeRequest", None),
}


def _ts_union(name: str, text: str) -> list[str]:
    pattern = rf"\b{re.escape(name)}\??\s*:\s*((?:[\"'][\w.]+[\"']\s*\|?\s*)+)"
    return [v for m in re.finditer(pattern, text) for v in _TS_METHOD_LITERAL.findall(m.group(1))]


def _ts_table(name: str, text: str) -> list[str]:
    match = re.search(rf"\bconst\s+{re.escape(name)}\b.*?=\s*\[\s*\n(.*?)\n\];", text, re.S)
    return re.findall(r"""(?:^|[\[,])\s*["']([\w.]+)["']""", match.group(1), re.M) if match else []


def _ts_property(name: str, text: str) -> list[str]:
    return re.findall(rf"""\b{re.escape(name)}:\s*["']([\w.]+)["']""", text)


def _ts_const(name: str, text: str) -> list[str]:
    """Value literals of ``const name = "a"`` or a ternary chain (not the
    literals its conditions compare against)."""
    match = re.search(rf"\bconst\s+{re.escape(name)}\s*=\s*([^;]*);", text)
    value = "?" + match.group(1) if match else ""
    return re.findall(r"""[?:]\s*["']([\w.]+)["']""", value)


def _ts_template(arg: str, text: str) -> list[str]:
    """`` `teamup.${verb}` `` with ``verb: "pause" | …`` declared in the file."""
    match = re.fullmatch(r"`([\w.]*)\$\{(\w+)\}`", arg.strip())
    return [match.group(1) + v for v in _ts_union(match.group(2), text)] if match else []


_TS_TERNARY = re.compile(r"(?<!\?)\?(?![.?])")
_TS_STRINGS = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`")
_TS_DECLARATION = re.compile(r"\w+\??\s*:\s*[\w\[\]<>|.\s]+")
_TS_ASSERTION = re.compile(r"\s+(?:as|satisfies)\s+[\w\[\]<>|.\s]+$")


def _ts_arms(arg: str) -> list[str]:
    """Value arms of ``c ? "a" : d ? "b" : v`` (the conditions dropped); the
    whole argument when it is not a ternary."""
    parts = _TS_TERNARY.split(arg)
    if len(parts) == 1:
        return [arg.strip()]
    arms = [part.split(":")[:-1] for part in parts[1:-1]] + [parts[-1].split(":")]
    return [arm.strip() for group in arms for arm in group]


def _ts_arm_methods(arm: str, text: str) -> list[str]:
    """Verbs of one arm; ``"?"`` when a bare identifier (a variable verb) is
    part of it, so the caller reports the site as unresolved."""
    arm = _TS_ASSERTION.sub("", arm)  # "set_model" as never
    if arm.startswith("`"):
        return _ts_template(arm, text) or ["?"]
    literals = _TS_METHOD_LITERAL.findall(arm)
    variable = re.search(r"[A-Za-z_$]", _TS_STRINGS.sub("", arm))
    return literals + (["?"] if variable or not literals else [])


def _ts_arg_methods(arg: str, text: str) -> list[str]:
    return [verb for arm in _ts_arms(arg) for verb in _ts_arm_methods(arm, text)]


def _ts_wrapper(name: str, text: str) -> list[str]:
    """First arguments of ``name(…)`` calls (literals, ternary arms, or a
    template over a literal union); a variable operand yields ``"?"``. Only
    the declaration shape ``name(method: string`` is skipped."""
    out = []
    for arg in re.findall(rf"\b{re.escape(name)}\s*{_TS_GENERIC}\(\s*([^,)]*)", text):
        if not _TS_DECLARATION.fullmatch(arg.strip()):
            out.extend(_ts_arg_methods(arg, text))
    return out


def _ts_typed(name: str, text: str) -> list[str]:
    """Quoted member keys of ``interface <name> { … }``."""
    match = re.search(rf"\binterface\s+{re.escape(name)}\s*\{{(.*?)\n\}}", text, re.S)
    return re.findall(r"""^\s*["']([\w.]+)["']\s*:""", match.group(1), re.M) if match else []


_TS_RESOLVERS = {
    "union": _ts_union,
    "table": _ts_table,
    "property": _ts_property,
    "const": _ts_const,
    "wrapper": _ts_wrapper,
    "typed": _ts_typed,
    "expand": lambda name, _text: name.split(),
}


def _ts_call_methods(rel: str, arg: str, text: str, unresolved: list[str]) -> list[str]:
    source = _TS_VARIABLE_METHOD_SOURCES.get((rel, arg))
    if source is None:
        verbs = _ts_arg_methods(arg, text)  # "verb", or the arms of a ternary
        if "?" in verbs:
            unresolved.append(
                f"{rel}: .request({arg}, …) has no entry in _TS_VARIABLE_METHOD_SOURCES"
            )
        return [v for v in verbs if v != "?"]
    return _ts_mapped_methods(rel, source, text, unresolved)


def _ts_mapped_methods(
    rel: str, source: tuple[str, str, str | None], text: str, unresolved: list[str]
) -> list[str]:
    kind, name, other = source
    if other is not None:
        text = (_V3_ROOT / other).read_text(encoding="utf-8")
    names = _TS_RESOLVERS[kind](name, text)
    if not names or "?" in names:
        unresolved.append(f"{rel}: {kind} {name!r} resolved to {names}")
    return [n for n in names if n != "?"]


def _v3_bridge_methods() -> tuple[dict[str, set[str]], list[str], set[tuple[str, str]]]:
    """Verb → files that send it; unresolved sites; variable sites seen."""
    calls: dict[str, set[str]] = {}
    unresolved: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted((_V3_ROOT / "src").rglob("*.ts*")):
        rel = path.relative_to(_V3_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for raw in _ts_call_args(text):
            arg = raw.strip()
            if (rel, arg) in _TS_VARIABLE_METHOD_SOURCES:
                seen.add((rel, arg))
            for name in _ts_call_methods(rel, arg, text, unresolved):
                calls.setdefault(name, set()).add(rel)
    return calls, unresolved, seen


def test_js_tui_rpc_methods_match_bridge_dispatch_table() -> None:
    """Every bridge verb the v3 TUI sends is in the bridge dispatch table.

    Scans ``packages/r4-tui-v3/src/**/*.ts(x)`` for ``.request("verb", …)``
    literals and resolves the variable-method call sites through
    ``_TS_VARIABLE_METHOD_SOURCES``; fails on a verb ``_bridge_rpc_bindings``
    does not serve (the bridge answers it with ``unknown method``). The name
    is kept from the retired JS-TUI version (plan H-4.1 cites it).
    """
    assert (_V3_ROOT / "src").is_dir(), f"v3 TUI sources not found at {_V3_ROOT}"
    calls, unresolved, seen = _v3_bridge_methods()
    assert not unresolved, "unresolved bridge call sites:\n" + "\n".join(unresolved)
    stale = set(_TS_VARIABLE_METHOD_SOURCES) - seen
    assert not stale, f"_TS_VARIABLE_METHOD_SOURCES entries match no call site: {sorted(stale)}"
    core = {"initialize", "get_state", "send_message", "shutdown"}
    assert core <= set(calls), f"v3 TUI is missing core RPC calls: {core - set(calls)}"
    served = _extract_bridge_dispatch_methods()
    unknown = {verb: sorted(files) for verb, files in calls.items() if verb not in served}
    assert not unknown, f"v3 TUI sends verbs the bridge does not serve: {unknown}"


def test_v3_bridge_method_scan_flags_unserved_and_unmapped_sites(tmp_path, monkeypatch) -> None:
    """The scan reports an unserved literal verb and an unmapped variable site."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.ts").write_text(
        'await bridge.request<{ ok: boolean }>(\n  "no_such_verb", {});\n'
        "await bridge.request(dynamicVerb, {});\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_V3_ROOT", tmp_path)
    calls, unresolved, _seen = _v3_bridge_methods()
    assert calls == {"no_such_verb": {"src/a.ts"}}
    assert unresolved and "dynamicVerb" in unresolved[0]
    assert "no_such_verb" not in _extract_bridge_dispatch_methods()


def test_v3_bridge_method_scan_sees_ternary_arms_and_variable_operands(
    tmp_path, monkeypatch
) -> None:
    """A ternary with a variable arm is unresolved, and a wrapper call whose
    first argument is a ternary is checked, not skipped as a declaration."""
    stores = tmp_path / "src" / "stores"
    stores.mkdir(parents=True)
    (tmp_path / "src" / "b.ts").write_text(
        'await bridge.request(flag ? "known_a" : verbVar, {});\n', encoding="utf-8"
    )
    (stores / "teams.ts").write_text(
        "async function _request<T>(method: string, params: object): Promise<T> {\n"
        "  return bridge.request(method, params);\n}\n"
        'await _request(paused ? "teamup.resume" : "teamup.no_such", {});\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_V3_ROOT", tmp_path)
    calls, unresolved, _seen = _v3_bridge_methods()
    assert calls["known_a"] == {"src/b.ts"}
    assert any("verbVar" in line for line in unresolved), unresolved
    assert {"teamup.resume", "teamup.no_such"} <= set(calls), calls
    assert "teamup.no_such" not in _extract_bridge_dispatch_methods()


def test_v3_bridge_method_scan_reads_typed_rpc_calls(tmp_path, monkeypatch) -> None:
    """H-4.4: ``rpc(rail, "verb", …)`` sends a verb too; the scan sees it."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "c.ts").write_text(
        'const a = await rpc(getCompanionBridge(), "no_such_typed_verb", {}, 10);\n'
        'void rpc(bridge, "session.fork", { session_id: sid });\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_V3_ROOT", tmp_path)
    calls, unresolved, _seen = _v3_bridge_methods()
    assert calls == {"no_such_typed_verb": {"src/c.ts"}, "session.fork": {"src/c.ts"}}
    assert unresolved == []


# ---------------------------------------------------------------------------
# C2: session pin/rename bridge calls (store-direct semantics)
# ---------------------------------------------------------------------------


def test_bridge_session_pin_unpin_rename(tmp_path):
    """C2 verbs flow through the real SessionStore (pinned-first, flag truth)."""
    from r4.companion.session_store import CompanionSessionStore

    store = CompanionSessionStore(db_path=tmp_path / "c2.db")
    session = store.create_session(
        MagicMock(
            config=SimpleNamespace(
                profile="default",
                gateway_url="http://g",
                project_id="p",
                model="m",
            )
        ),
        name="C2 Target",
    )
    server = CompanionBridgeServer(
        client=MagicMock(
            config=SimpleNamespace(
                profile="default",
                gateway_url="http://g",
                project_id="p",
                model="m",
            )
        ),
        store=store,
    )

    pin = server.session_pin({"session_id": session.session_id})
    assert pin["pinned"] is True
    listed = server.list_sessions({"limit": 50})["sessions"]
    assert listed[0]["id"] == session.session_id
    assert listed[0]["pinned"] is True

    unpin = server.session_unpin({"session_id": session.session_id})
    assert unpin["pinned"] is False
    assert [s["pinned"] for s in server.list_sessions({"limit": 50})["sessions"]] == [False]

    renamed = server.session_rename({"session_id": session.session_id, "name": "Renamed"})
    assert renamed["name"] == "Renamed"
    assert server.list_sessions({"limit": 50})["sessions"][0]["name"] == "Renamed"


def test_bridge_session_archive_hides_from_default_list(tmp_path):
    """Wave D: archive hides the session from the default list (C2's pin keeps it)."""
    from r4.companion.session_store import CompanionSessionStore

    store = CompanionSessionStore(db_path=tmp_path / "c2.db")
    session = store.create_session(
        MagicMock(
            config=SimpleNamespace(
                profile="default",
                gateway_url="http://g",
                project_id="p",
                model="m",
            )
        ),
        name="D Target",
    )
    server = CompanionBridgeServer(
        client=MagicMock(
            config=SimpleNamespace(
                profile="default",
                gateway_url="http://g",
                project_id="p",
                model="m",
            )
        ),
        store=store,
    )

    archived = server.session_archive({"session_id": session.session_id})
    assert archived["archived"] is True
    assert server.list_sessions({"limit": 50})["sessions"] == []

    with pytest.raises(ValueError, match="session_id is required"):
        server.session_archive({})
    with pytest.raises(ValueError, match="session not found"):
        server.session_archive({"session_id": "nope"})


def test_bridge_session_pin_unknown_raises(tmp_path):
    from r4.companion.session_store import CompanionSessionStore

    store = CompanionSessionStore(db_path=tmp_path / "c2b.db")
    server = CompanionBridgeServer(client=MagicMock(), store=store)
    with pytest.raises(ValueError, match="session not found"):
        server.session_pin({"session_id": "nope"})
    with pytest.raises(ValueError, match="session_id is required"):
        server.session_pin({})
    with pytest.raises(ValueError, match="name is required"):
        server.session_rename({"session_id": "whatever", "name": ""})


def test_bridge_session_compact_syncs_store(tmp_path):
    """C4: /compact rewrites history, saves the store, returns from→to counts."""
    store = MagicMock()
    store.save = MagicMock()

    class _Ctx:
        @staticmethod
        def compact(payload, keep_recent=6):
            return [{"role": "system", "content": "[Conversation summary]\nshort"}] + [
                {"role": "user", "content": "keep"}
            ]

    session = SimpleNamespace(
        session_id="s-1",
        name="C4",
        messages=[SimpleNamespace(role="user", content=f"u{i}") for i in range(10)],
        context=_Ctx(),
        agent=None,
    )

    server = CompanionBridgeServer(client=MagicMock(), store=store)
    server._session = session

    result = server.session_compact({"keep_recent": 2})

    assert result["changed"] is True
    assert result["from"] == 10
    assert result["to"] == 2
    store.save.assert_called_once_with(session)


def test_bridge_session_compact_noop_saves_nothing(tmp_path):
    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock())
    session = SimpleNamespace(
        session_id="s-1",
        name="C4 tiny",
        messages=[SimpleNamespace(role="user", content="one")],
        context=SimpleNamespace(compact=lambda payload, keep_recent=6: payload),
        agent=None,
    )
    server._session = session

    result = server.session_compact({})

    assert result["changed"] is False
    assert result["message_count"] == 1
    server._store.save.assert_not_called()
