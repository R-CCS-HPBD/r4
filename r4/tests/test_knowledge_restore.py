"""K4 closure tests — KnowledgeClient.restore + CLI restore handler."""

from __future__ import annotations

import argparse

from r4._cli.knowledge import KnowledgeCommand


class _StubHttpClient:
    """Minimal Client stub honoring KnowledgeClient.post(url, json=...)."""

    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.posted: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict, **_kwargs):
        self.posted.append((url, json))
        return {
            "status": "ok",
            "restore": {
                "project_id": "proj-b",
                "verified": True,
                "artifact_count": 3,
                "document_count": 2,
                "node_count": 1,
                "edge_count": 0,
                "embedding_count": 4,
                "catalog_count": 1,
                "a2l_link_count": 0,
                "a2l_ref_count": 0,
                "skipped_count": 1,
            },
        }


class TestKnowledgeRestoreClient:
    def test_restore_posts_payload(self):
        from r4.knowledge import KnowledgeClient

        client = KnowledgeClient(_StubHttpClient())
        result = client.restore("/srv/bundles/proj-a.kb", project_id="proj-b", verify=True)

        url, payload = client._client.posted[0]
        assert url.endswith("/knowledge/restore")
        assert payload == {
            "bundle_dir": "/srv/bundles/proj-a.kb",
            "project_id": "proj-b",
            "verify": True,
            "allow_unsigned": False,
        }
        assert result["restore"]["project_id"] == "proj-b"

    def test_restore_defaults_verify_and_only(self):
        from r4.knowledge import KnowledgeClient

        client = KnowledgeClient(_StubHttpClient())
        client.restore("/srv/bundles/x.kb")
        _url, payload = client._client.posted[0]
        assert payload == {
            "bundle_dir": "/srv/bundles/x.kb",
            "verify": True,
            "allow_unsigned": False,
        }

    def test_restore_allow_unsigned_forwarded(self):
        """G-P2-3: explicit opt-out flows into the server payload."""
        from r4.knowledge import KnowledgeClient

        client = KnowledgeClient(_StubHttpClient())
        client.restore("/srv/bundles/x.kb", allow_unsigned=True)
        _url, payload = client._client.posted[0]
        assert payload["allow_unsigned"] is True


class TestRestoreCli:
    def test_handler_payload_and_outcome(self, monkeypatch, capsys):
        from r4 import client as client_module

        class _FakeClient:
            def __init__(self):
                self.knowledge = _FakeRestore()

        monkeypatch.setattr(client_module, "Client", _FakeClient)
        args = argparse.Namespace(
            knowledge_command="restore",
            bundle_dir="/srv/bundles/x.kb",
            project_id="proj-b",
            no_verify=False,
            as_json=False,
        )
        code = KnowledgeCommand()._handle_restore(args)
        out = capsys.readouterr().out
        assert code == 0
        assert "verified" in out
        assert "Artifacts" in out


class _FakeRestore:
    def restore(self, bundle_dir, *, project_id=None, verify=True, allow_unsigned=False):
        assert bundle_dir == "/srv/bundles/x.kb"
        assert project_id == "proj-b"
        assert verify is True
        assert allow_unsigned is False
        return {
            "status": "ok",
            "restore": {
                "project_id": "proj-b",
                "verified": True,
                "artifact_count": 1,
                "degraded": False,
            },
        }
