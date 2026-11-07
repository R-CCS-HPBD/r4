"""ADR-050 D-3 pins: knowledge_build_from_context companion tool.

Contracts locked:

- The tool refuses WITHOUT project and refuses under acquisition mode
  'disabled' (the shared write gate); 'pinned' constricts the target;
  'open' passes through.
- Package shape is verbatim: JSONL session-history artifact carrying the
  excerpt exactly as passed, never a synthesized claim; the lane is
  'session-history' and built_by is 'agent' (review of K1 part B,
  2026-10-24: the model typed the excerpt, so it is an agent write).
- The durable upload route is the SAME central route every knowledge write
  takes (`/knowledge/upload` with project_id + optional graph_id) — not a
  side channel.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.knowledge_tools import build_knowledge_tools


def _client_with_mode(mode: str, *, graphs: list[str] | None = None):
    knowledge = MagicMock()
    knowledge.write_constraints = {"mode": mode, "graphs": graphs or []}
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="proj-h"),
        knowledge=knowledge,
        session_id="sess-pin-1",
    )
    knowledge._client = MagicMock()
    knowledge._client._api_prefix = ""
    knowledge._actor_headers = MagicMock(return_value={})
    knowledge._client.post_multipart.return_value = {"job_id": "job-ctl", "status": "queued"}
    return client


def _tool(client):
    tools = {t["name"]: t for t in build_knowledge_tools(client)}
    return tools["knowledge_build_from_context"]


class TestGates:
    def test_disabled_mode_refuses_before_any_upload(self) -> None:
        client = _client_with_mode("disabled")
        out = json.loads(_tool(client)["execute"](content="x", title="t"))
        assert out["code"] == "knowledge_acquisition_blocked"
        client.knowledge._client.post_multipart.assert_not_called()

    def test_open_mode_uploads_with_session_provenance(self) -> None:
        client = _client_with_mode("open")
        out = json.loads(_tool(client)["execute"](content="keep this verbatim", title="ucx note"))
        post = client.knowledge._client.post_multipart.call_args
        assert post.args[0] == "/knowledge/upload"
        kwargs = post.kwargs
        body, ctype = kwargs["content"], kwargs["content_type"]
        assert ctype == "application/x-ndjson"
        payload = json.loads(body.decode("utf-8").splitlines()[0])
        assert {k: payload[k] for k in ("lane", "session_id", "built_by")} == {
            "lane": "session-history",
            "session_id": "sess-pin-1",
            "built_by": "agent",
        }
        assert payload["lines"][0]["text"] == "keep this verbatim"
        assert payload["lines"][0]["verbatim"] is True
        assert kwargs["params"]["project_id"] == "proj-h"
        assert out["job_id"] == "job-ctl"

    def test_pinned_respects_graph_target(self) -> None:
        client = _client_with_mode("pinned", graphs=["lab-graph"])
        out_ok = json.loads(_tool(client)["execute"](content="c", title="t", graph_id="lab-graph"))
        assert out_ok["job_id"] == "job-ctl"
        out_denied = json.loads(
            _tool(client)["execute"](content="c", title="t", graph_id="other-graph")
        )
        assert out_denied["code"] == "knowledge_acquisition_blocked"

    def test_empty_excerpt_refused(self) -> None:
        client = _client_with_mode("open")
        out = json.loads(_tool(client)["execute"](content="   ", title="t"))
        assert out["code"] == "empty_context"

    def test_projectless_refused(self) -> None:
        client = _client_with_mode("open")
        client.config.project_id = None
        out = json.loads(_tool(client)["execute"](content="c", title="t"))
        assert out["code"] == "project_missing"

    def test_oversize_refused(self) -> None:
        client = _client_with_mode("open")
        out = json.loads(_tool(client)["execute"](content="x" * (5 * 1024 * 1024), title="t"))
        assert out["code"] == "context_too_large"

    def test_upload_failure_named_not_smudged(self) -> None:
        client = _client_with_mode("open")
        client.knowledge._client.post_multipart.side_effect = RuntimeError("500 exploded")
        out = json.loads(_tool(client)["execute"](content="c", title="t"))
        assert out["code"] == "context_build_failed"
        assert "500 exploded" in out["message"]


def test_build_from_context_is_risky_like_knowledge_ingest():
    """ADR-050 follow-up (2026-09-13): the session-history build is a durable
    write of chat content, so it sits in the same permission class as
    ``knowledge_ingest`` — never auto-approved under AUTO_SAFE."""
    from r4.companion.permissions import RISKY_TOOLS, SAFE_TOOLS, PermissionManager

    assert "knowledge_build_from_context" in RISKY_TOOLS
    assert "knowledge_build_from_context" not in SAFE_TOOLS
    assert PermissionManager().classify("knowledge_build_from_context") == "risky"
    assert PermissionManager().classify("knowledge_ingest") == "risky"


class TestVerbatimSliceFiltering:
    def test_jsonl_tool_roles_excluded_by_default(self) -> None:
        client = _client_with_mode("open")
        content = (
            '{"role": "user", "text": "keep me"}\n'
            '{"role": "tool", "text": "fetched noise"}\n'
            '{"role": "assistant", "text": "synth detail"}\n'
            '{"role": "tool_result", "text": "web fetch dump"}\n'
        )
        json.loads(_tool(client)["execute"](content=content, title="t"))
        body = client.knowledge._client.post_multipart.call_args.kwargs["content"]
        payload = json.loads(body.decode("utf-8").splitlines()[0])
        texts = [line["text"] for line in payload["lines"]]
        assert "keep me" in texts and "synth detail" in texts
        assert "fetched noise" not in texts
        assert "web fetch dump" not in texts
        roles = [line["role"] for line in payload["lines"]]
        assert roles == ["user", "assistant"]

    def test_include_tool_results_flag_keeps_all_lines(self) -> None:
        client = _client_with_mode("open")
        content = '{"role": "user", "text": "keep"}\n{"role": "tool", "text": "also keep"}\n'
        json.loads(_tool(client)["execute"](content=content, title="t", include_tool_results=True))
        body = client.knowledge._client.post_multipart.call_args.kwargs["content"]
        payload = json.loads(body.decode("utf-8").splitlines()[0])
        assert [line["text"] for line in payload["lines"]] == ["keep", "also keep"]

    def test_all_tool_roles_filtered_errors_named(self) -> None:
        client = _client_with_mode("open")
        content = '{"role": "tool", "text": "only tool output"}\n'
        out = json.loads(_tool(client)["execute"](content=content, title="t"))
        assert out["code"] == "nothing_after_tool_exclusion"
        assert "include_tool_results" in out["message"]

    def test_plain_text_passthrough_unfiltered(self) -> None:
        client = _client_with_mode("open")
        json.loads(_tool(client)["execute"](content="raw words, not jsonl", title="t"))
        body = client.knowledge._client.post_multipart.call_args.kwargs["content"]
        payload = json.loads(body.decode("utf-8").splitlines()[0])
        assert payload["lines"][0]["text"] == "raw words, not jsonl"


def test_the_upload_is_marked_as_the_agents_write():
    """Review of K1 part B (2026-10-24, M3): the tool uploaded model-typed
    "verbatim" text with no actor header, so it landed established as the
    user's; now the server's agent rule applies (approved proposal, rows
    proposed), and the proposal id travels."""
    from r4.knowledge import KnowledgeClient

    sent: dict = {}

    class _Http:
        _api_prefix = "/gw"

        def post_multipart(self, url, **kwargs):
            sent.update(url=url, headers=dict(kwargs.get("headers") or {}))
            return {"job_id": "job-a", "status": "queued"}

    knowledge = KnowledgeClient(_Http())  # type: ignore[arg-type]
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="proj-h"), knowledge=knowledge, session_id="s-1"
    )
    out = json.loads(_tool(client)["execute"](content="text", title="t", proposal_id="prop-1"))
    assert out["job_id"] == "job-a"
    assert sent["headers"]["x-r4-actor"] == "agent"
    assert sent["headers"]["x-proposal-id"] == "prop-1"
    assert knowledge.actor is None  # the mark ends with the call
