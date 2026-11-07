"""ingest_status route fix — client now serves from /knowledge/status.

The historic /knowledge/ingest/status path has no downstream route (404
since the k2 lane); the client's method must hit the working status route.
"""

from __future__ import annotations

from typing import Any

from r4.knowledge import KnowledgeClient


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    @property
    def _api_prefix(self) -> str:
        return "/gw"

    def get(self, path, params=None):
        self.calls.append((path, params))
        return {"ok": True}


def test_ingest_status_uses_knowledge_status_route():
    kc = KnowledgeClient.__new__(KnowledgeClient)
    fake = _FakeClient()
    kc._client = fake
    kc.actor = None
    kc.session_id = None
    kc.graph_constraints = {}

    out = kc.ingest_status("proj-a")

    assert out == {"ok": True}
    (path, params), *_ = fake.calls
    assert path.endswith("/knowledge/status")
    assert "/ingest/" not in path
    assert params == {"project_id": "proj-a"}
