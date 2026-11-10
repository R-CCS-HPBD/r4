"""Shared harness for the bridge RPC contract tests (plan H-4.5).

``make_harness`` builds a ``CompanionBridgeServer`` on a real session store
and session with only the network faked (``FakeClient``: GET/POST answers
scripted by URL suffix). Family test files
(``test_bridge_rpc_contract*.py``) extend ``harness.client`` per case and
run each verb through ``run_verb`` exactly as the JSONL dispatcher calls it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import WorkflowCatalogItem, WorkflowResult, WorkflowStatus

from r4.companion import session_store as store_module
from r4.companion.session_models import SessionMessage
from r4.companion.session_store import CompanionSessionStore
from r4.companion.ui import rpc_schema
from r4.companion.ui.bridge_server import CompanionBridgeServer, _bridge_rpc_bindings


class McpHooks:
    """The MCP sandbox hooks (ADR-101 D-9), recorded: no daemon is probed or restarted.

    Installed by :func:`make_harness` for every bridge test (review Q-1): a
    ``session.sandbox_set`` once reached the real ``127.0.0.1:50800`` and
    could stop a developer's own MCP daemon. Without ``raising=False``: a
    renamed hook fails here instead of going quiet.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.profiles: list[Any] = []
        self.restarted: list[str] = []
        self.error: Exception | None = None
        self.in_sync = True

    def hook(self, name: str) -> Any:
        def call(*args: Any) -> list[str]:
            self.calls.append(name)
            self.profiles.extend(args)
            if self.error is not None:
                raise self.error
            return list(self.restarted)

        return call

    def status(self) -> dict[str, Any]:
        return {"in_sync": self.in_sync}


def install_mcp_hooks(monkeypatch: pytest.MonkeyPatch) -> McpHooks:
    import r4.companion.mcp_exposure as mcp_exposure

    hooks = McpHooks()
    for name in ("require_mcp_sandbox", "release_mcp_sandbox_default"):
        monkeypatch.setattr(mcp_exposure, name, hooks.hook(name))
    monkeypatch.setattr(mcp_exposure, "mcp_sandbox_status", hooks.status)
    return hooks


LEASE = {"expires_at": "2999-01-01T00:00:00+00:00", "is_valid": True}
VENUE_ROW = {
    "state": "running",
    "reason": "",
    "source_names": ["openai-main"],
    "default_source": "openai-main",
    "web_policy": "public",
    "runtime": {"phase": "idle", "turn_seq": 0, "partial": ""},
    "lease": LEASE,
}
TRUST_ROW = {
    "user_id": "u1",
    "scope": "project:demo",
    "tool_name": "bash",
    "expires_at": "2999-01-01T00:00:00+00:00",
    "created_at": "2026-10-18T00:00:00+00:00",
    "created_by": "u1",
}


class _Config:
    profile = "default"
    gateway_url = "http://gw.example"
    project_id = "demo"
    model = "config-model"
    endpoint_url = None
    endpoint_provider = None
    endpoint_api_key = None
    auto_retry = False


class Scripted:
    """GET/POST answers scripted by URL suffix (the last matching suffix wins)."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}

    def _answer(self, path: str) -> Any:
        hits = [answer for suffix, answer in self.answers.items() if path.endswith(suffix)]
        return hits[-1] if hits else {"status": "ok", "applied": True}

    def get(self, path: str, **_kw: Any) -> Any:
        return self._answer(path.split("?", 1)[0])

    def post(self, path: str, json: Any = None, **_kw: Any) -> Any:
        _ = json
        return self._answer(path)

    def delete(self, path: str, **_kw: Any) -> Any:
        return self._answer(path.split("?", 1)[0])


# UMRS ExecutionRecord.to_dict (GET /workflows/executions/{id} → "execution").
_EXECUTION_RECORD = {
    "execution_id": "ex-1",
    "workflow_name": "wf",
    "user_id": "u1",
    "status": "running",
    "submitted_at": 1.0,
    "started_at": 2.0,
    "finished_at": None,
    "error": None,
    "progress": 0.5,
    "params": {},
    "steps": [
        {
            "name": "prep",
            "status": "succeeded",
            "started_at": 2.0,
            "finished_at": 3.0,
            "error": None,
            "progress": 1.0,
            "extra": {},
        }
    ],
    "artifacts": [],
    "durable_job_id": None,
    "cancel_requested": False,
    "identity_provenance": None,
    "gates": {},
}


def _workflows() -> SimpleNamespace:
    status = WorkflowStatus(execution_id="ex-1", workflow_name="wf", status="running", progress=0.5)
    result = WorkflowResult(execution_id="ex-1", workflow_name="wf", status="completed")
    # UMRS _describe_workflow_entry: catalog step rows are {name, entrypoint};
    # /package replaces them with the workflow.json steps verbatim.
    describe = {
        "status": "ok",
        "workflow_name": "wf",
        "parameter_schema": {"type": "object", "properties": {"n": {"type": "integer"}}},
        "steps": [{"name": "prep", "entrypoint": "steps.prep:Prep"}],
        "requirements": [],
        "triggers": [],
    }
    package = dict(
        describe,
        steps=[
            {"name": "prep", "entrypoint": "steps.prep:Prep", "config": {"k": 1}},
            {
                "name": "train",
                "entrypoint": "steps.t:T",
                "depends_on": ["prep"],
                "requires": ["gpu"],
            },
        ],
        resources={},
        execution_mode=None,
        output=None,
        source_path="/srv/wf",
        files=[{"path": "a.py", "bytes": 3, "lines": 1, "content": "x=1", "truncated": False}],
        files_truncated=False,
    )
    return SimpleNamespace(
        list_workflows=lambda: [WorkflowCatalogItem(workflow_name="wf", name="wf", tags=["t"])],
        status=lambda _id: status,
        cancel=lambda _id: status,
        approve_gate=lambda *_a, **_k: status,
        trigger=lambda *_a, **_k: result,
        get_results=lambda _id: result,
        get_execution_detail=lambda _id: dict(_EXECUTION_RECORD),
        describe=lambda _name, **_k: describe,
        definition_package=lambda _name, **_k: package,
    )


class FakeClient(Scripted):
    """The r4 client surface the covered handlers touch; network faked."""

    def __init__(self) -> None:
        super().__init__()
        self.config = _Config()
        self._config = _Config()
        self._api_prefix = "/umrs"
        self.http = Scripted()
        self.workflows = _workflows()
        self.readiness = MagicMock(return_value={})
        self.list_endpoints = MagicMock(
            return_value={
                "endpoints": [{"instance_id": "ep-1", "status": "ready", "model": "gpt-x"}]
            }
        )

    def list_assets(self, **_kw: Any) -> dict[str, Any]:
        return {"rows": [{"kind": "dataset", "id": "d1"}], "snapshot_hash": "h1"}

    def assets_patch(self, **_kw: Any) -> dict[str, Any]:
        return {
            "upserts": [{"kind": "dataset", "id": "d2"}],
            "tombstones": ["dataset:d1"],
            "snapshot_hash": "h2",
        }

    def trust_list(self) -> dict[str, Any]:
        return {"rows": [dict(TRUST_ROW)], "count": 1}

    def trust_grant(self, tool_name: str, scope: str = "global") -> dict[str, Any]:
        return dict(TRUST_ROW, tool_name=tool_name, scope=scope)

    def list_session_turns(self, *, session_id: str, turn_id: str | None) -> dict[str, Any]:
        if turn_id:
            return {"session_id": session_id, "entry": {"turn_id": turn_id}}
        return {"entries": [{"turn_id": "t1"}], "count": 1}


@dataclass
class Harness:
    server: CompanionBridgeServer
    client: FakeClient
    store: CompanionSessionStore
    session: Any

    @property
    def sid(self) -> str:
        return str(self.session.session_id)


def make_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """A bridge server on a real store and session; only the network is faked."""
    install_mcp_hooks(monkeypatch)  # hermetic: never the host's MCP daemon (review Q-1)
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "on")
    store_module.set_snapshot_enabled(None)
    client = FakeClient()
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    server = CompanionBridgeServer(client=client, store=store, model="gpt-x")
    session = server._open_session(
        session_id=None, explicit_model="gpt-x", skill=None, system_prompt=None, venue="local"
    )
    server._session = session
    server._configure_session(session)
    server._emit_line = lambda _line: None  # type: ignore[method-assign]
    return Harness(server, client, store, session)


def with_history(h: Harness, turns: int = 8) -> None:
    for index in range(turns):
        h.session.messages.append(
            SessionMessage(role="user", content=f"q{index}", metadata={"turn_id": f"t{index + 1}"})
        )
        h.session.messages.append(
            SessionMessage(
                role="assistant", content=f"a{index}", metadata={"turn_id": f"t{index + 1}"}
            )
        )
    h.store.save(h.session)


# Each case: (verb, label, setup(harness) -> params). Several cases may share a verb.
Setup = Callable[[Harness], dict[str, Any]]
Case = tuple[str, str, Setup]


def plain(params: dict[str, Any]) -> Setup:
    """A setup that only supplies params."""
    return lambda _h: params


def run_verb(h: Harness, method: str, setup: Setup) -> Any:
    """Run one verb's real handler, as the JSONL dispatcher calls it."""
    params = setup(h)
    return _bridge_rpc_bindings(h.server)[method](params)


def schema_errors(h: Harness, method: str, setup: Setup) -> list[str]:
    """Errors of the handler's real output against the verb's schema."""
    return rpc_schema.validate_result(method, run_verb(h, method, setup))


def case_ids(cases: list[Case]) -> list[str]:
    return [f"{method}[{label}]" for method, label, _ in cases]


def uncovered(family: dict[str, type], cases: list[Case]) -> set[str]:
    """Verbs of a family registry that no case exercises."""
    return set(family) - {method for method, _, _ in cases}
