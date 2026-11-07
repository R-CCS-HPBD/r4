"""KnowledgeClient.readiness_approve rides a route UMRS serves (TUI audit
2026-10-17, round 2).

The client posted ``/gw/knowledge/readiness/approve``, which the gateway
rewrites to ``/umrs/knowledge/readiness/approve`` -- a path UMRS did not
serve; the only approval route (``/umrs/knowledge/approval``) held one
server-wide approval.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("umrs")

from r4.knowledge import KnowledgeClient  # noqa: E402


def _client() -> tuple[KnowledgeClient, MagicMock]:
    parent = MagicMock()
    parent._api_prefix = "/gw"
    parent.post.return_value = {"user_approved": True}
    return KnowledgeClient(parent), parent


def _umrs_post_paths() -> set[str]:
    from umrs.api import knowledge as kn

    return {r.path for r in kn.router.routes if "POST" in getattr(r, "methods", set())}


def test_approve_posts_the_project_and_reason():
    client, parent = _client()
    client.readiness_approve("proj-a", reason="reviewed")
    url = parent.post.call_args.args[0]
    assert url == "/gw/knowledge/readiness/approve"
    assert parent.post.call_args.kwargs["json"] == {"project_id": "proj-a", "reason": "reviewed"}


def test_approve_without_a_reason_sends_only_the_project():
    client, parent = _client()
    client.readiness_approve("proj-a")
    assert parent.post.call_args.kwargs["json"] == {"project_id": "proj-a"}


def test_umrs_serves_the_path_the_client_posts():
    client, parent = _client()
    client.readiness_approve("proj-a")
    url = parent.post.call_args.args[0]
    # gateway/nginx: location /gw/knowledge/ rewrites to /umrs/knowledge/$1.
    umrs_path = url.replace("/gw/knowledge/", "/umrs/knowledge/", 1)
    assert umrs_path in _umrs_post_paths()


def test_an_agent_client_sends_its_actor_header():
    # UMRS refuses an agent's approval (403 approval_forbidden_for_agent) only
    # when the actor header arrives; agent_mutation sets client.actor.
    client, parent = _client()
    client.actor = "agent"
    client.session_id = "s1"
    client.readiness_approve("proj-a")
    headers = parent.post.call_args.kwargs["headers"]
    assert headers == {"x-r4-actor": "agent", "x-r4-session-id": "s1"}


def test_a_human_client_sends_no_actor_header():
    client, parent = _client()
    client.readiness_approve("proj-a")
    assert parent.post.call_args.kwargs["headers"] is None
