"""D-40 transport, client side: import a bundle by its bytes; download an export."""

from __future__ import annotations

import io
import tarfile
from argparse import Namespace

import pytest

from r4.knowledge import KnowledgeClient


class _Inner:
    _api_prefix = "/gw"
    _config = object()

    def __init__(self):
        self.uploads: list = []

    def post_multipart(self, path, **kwargs):
        self.uploads.append({"path": path, **kwargs})
        return {"status": "ok", "project_id": "caller-proj", "import": {"node_count": 3}}


def _bundle_dir(tmp_path):
    root = tmp_path / "proj-a"
    (root / "tables").mkdir(parents=True)
    (root / "manifest.json").write_text("{}")
    (root / "tables" / "nodes.jsonl").write_text("{}\n")
    return root


def test_directory_is_packed_under_bundle(tmp_path):
    inner = _Inner()
    KnowledgeClient(inner).import_bundle(str(_bundle_dir(tmp_path)))
    upload = inner.uploads[0]
    assert (upload["path"], upload["content_type"], upload["params"]) == (
        "/gw/knowledge/import",
        "application/gzip",
        None,
    )
    names = tarfile.open(fileobj=io.BytesIO(upload["content"]), mode="r:gz").getnames()
    assert {"bundle/manifest.json", "bundle/tables/nodes.jsonl"} <= set(names)


def test_archive_passes_through_with_opt_out_and_proposal(tmp_path):
    archive = tmp_path / "shared.kb.tar.gz"
    archive.write_bytes(b"GZ-BYTES")
    inner = _Inner()
    knowledge = KnowledgeClient(inner)
    knowledge.actor = "agent"
    knowledge.import_bundle(str(archive), allow_unsigned=True, proposal_id="gp-1")
    upload = inner.uploads[0]
    assert upload["content"] == b"GZ-BYTES"
    assert upload["params"] == {"allow_unsigned": "true"}
    assert upload["headers"] == {"x-r4-actor": "agent", "x-proposal-id": "gp-1"}


def test_directory_without_manifest_refused(tmp_path):
    with pytest.raises(ValueError, match="no manifest.json"):
        KnowledgeClient(_Inner()).import_bundle(str(tmp_path))


def test_cli_routes_modern_bundles_to_the_platform(tmp_path, monkeypatch, capsys):
    from r4._cli import knowledge as kcli

    calls = {}

    class _K:
        def import_bundle(self, bundle, allow_unsigned=False):
            calls["bundle"], calls["allow_unsigned"] = bundle, allow_unsigned
            result = {"node_count": 3, "verified": True, "tables_verified": True}
            return {"project_id": "caller-proj", "import": result}

    monkeypatch.setattr("r4.client.Client", lambda *a, **k: type("C", (), {"knowledge": _K()})())
    bundle = _bundle_dir(tmp_path)
    handler = kcli.KnowledgeCommand()
    args = Namespace(bundle=str(bundle), project_id=None, allow_unsigned=True, as_json=False)
    assert handler._handle_import(args) == 0
    assert calls == {"bundle": str(bundle), "allow_unsigned": True}
    assert "Imported (verified) into project caller-proj" in capsys.readouterr().out


def test_export_download_resolves_a_job_id_to_the_project_key(tmp_path, monkeypatch, capsys):
    from r4._cli import knowledge as kcli

    got = {}

    class _K:
        def download_export(self, key, dest):
            got["key"], got["dest"] = key, dest
            return dest

    monkeypatch.setattr("r4.client.Client", lambda *a, **k: type("C", (), {"knowledge": _K()})())
    monkeypatch.setattr(kcli, "_current_project_id", lambda: "proj-a")
    handler = kcli.KnowledgeCommand()
    dest = str(tmp_path / "out.kb.tar.gz")
    assert handler._handle_export_download(Namespace(export="job-9", output=dest)) == 0
    assert got == {"key": "proj-a/knowledge-exports/job-9.kb.tar.gz", "dest": dest}


class _ExportInner(_Inner):
    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    def post(self, path, json=None, **kwargs):
        raise self._exc

    def get(self, *a, **k):
        return {}


def test_export_server_error_is_not_masked_as_a_partial_export():
    """Observed live: a 500 from the export route came back as status=ready
    partial=true with an 800 KB client-side dump. Only a 404 (older server)
    falls back."""
    from r4.errors import APIError

    knowledge = KnowledgeClient(_ExportInner(APIError("boom", status_code=500)))
    with pytest.raises(APIError):
        knowledge.export("proj-a")


def test_export_404_still_falls_back(monkeypatch):
    from r4.errors import APIError

    knowledge = KnowledgeClient(_ExportInner(APIError("nope", status_code=404)))
    monkeypatch.setattr(knowledge, "_export_fallback", lambda pid, exc: {"partial": True})
    assert knowledge.export("proj-a") == {"partial": True}
