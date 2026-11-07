"""``r4 workflow export`` and ``WorkflowClient.export`` (2026-11-07).

Collaborators who see a workflow (own, system, or shared to their project)
download it from ``GET {workflows}/{name}/export`` as a zip carrying an
EXPORT.json checksum list. The client streams to a temp file, verifies every
listed sha256, and only then renames into place; ``--extract`` refuses any
member path that escapes the target directory. The HTTP layer is an
``httpx.MockTransport`` — nothing leaves the process.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from r4._cli.workflow import WorkflowCommand, _cmd_export
from r4.errors import R4Error
from r4.workflows import WorkflowClient, extract_export, verify_export

_SOURCES = {"workflow.json": b'{"name": "seg"}', "steps/segment.py": b"class Segment: pass\n"}


def _manifest(files: dict[str, bytes], excluded: list[str]) -> dict:
    rows = [
        {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for rel, data in files.items()
    ]
    return {
        "workflow": "seg",
        "exported_at": "2026-11-07T00:00:00Z",
        "umrs_version": "test",
        "files": rows,
        "excluded": excluded,
        "images": "not included",
    }


def _zip_bytes(files: dict[str, bytes], manifest: dict | None = None, extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, data in {**files, **(extra or {})}.items():
            zf.writestr(rel, data)
        zf.writestr("EXPORT.json", json.dumps(manifest or _manifest(files, [".env"])))
    return buf.getvalue()


class _Core:
    """Stands in for HTTPCore: real headers shape, mock transport."""

    def __init__(self, body: bytes = b"", status: int = 200):
        self.body, self.status, self.requests = body, status, []

    def build_headers(self) -> dict:
        return {"Content-Type": "application/json", "X-Project-ID": "proj-a"}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "workflow 'seg' not found"})
        return httpx.Response(200, content=self.body, headers={"content-type": "application/zip"})

    def _client(self, timeout=None) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))


def _client(core: _Core) -> WorkflowClient:
    parent = SimpleNamespace(
        config=SimpleNamespace(gateway_url="http://gw:8000"),
        platform_http=core,
        _api_prefix="/gw",
    )
    return WorkflowClient(parent)


def _leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".part"))


def test_default_dest_is_cwd_name_zip_and_sends_project_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    core = _Core(_zip_bytes(_SOURCES))

    path = _client(core).export("seg")

    assert path == tmp_path / "seg.zip"
    assert verify_export(path)["workflow"] == "seg"
    request = core.requests[0]
    assert request.url.path == "/gw/workflows/seg/export"
    assert request.headers["X-Project-ID"] == "proj-a"
    assert "application/zip" in request.headers["Accept"]
    assert "Content-Type" not in request.headers
    assert _leftovers(tmp_path) == []


def test_existing_directory_dest_receives_name_zip(tmp_path):
    out = tmp_path / "exports"
    out.mkdir()

    path = _client(_Core(_zip_bytes(_SOURCES))).export("seg", out)

    assert path == out / "seg.zip"
    assert path.is_file()


@pytest.mark.parametrize("suffix", ["/", "/nested/"])
def test_trailing_slash_dest_is_a_new_directory(tmp_path, monkeypatch, suffix):
    """``-o ./exports/`` with no exports dir used to write a FILE named
    ``exports``; a trailing separator now means a directory (2026-11-07)."""
    monkeypatch.chdir(tmp_path)

    path = _client(_Core(_zip_bytes(_SOURCES))).export("seg", "./exports" + suffix)

    expected = tmp_path / ("exports" + suffix) / "seg.zip"
    assert path.resolve() == expected
    assert expected.is_file() and zipfile.is_zipfile(expected)
    assert (tmp_path / "exports").is_dir()


def test_existing_file_is_refused_before_any_request(tmp_path):
    target = tmp_path / "seg.zip"
    target.write_bytes(b"keep me")
    core = _Core(_zip_bytes(_SOURCES))

    with pytest.raises(R4Error, match="already exists"):
        _client(core).export("seg", target)

    assert target.read_bytes() == b"keep me"
    assert core.requests == []


def test_overwrite_replaces_an_existing_file(tmp_path):
    target = tmp_path / "seg.zip"
    target.write_bytes(b"old")

    path = _client(_Core(_zip_bytes(_SOURCES))).export("seg", target, overwrite=True)

    assert zipfile.is_zipfile(path)
    assert _leftovers(tmp_path) == []


def test_checksum_mismatch_raises_and_leaves_no_file(tmp_path):
    manifest = _manifest(_SOURCES, [])
    manifest["files"][1]["sha256"] = "0" * 64
    core = _Core(_zip_bytes(_SOURCES, manifest))

    with pytest.raises(R4Error, match="sha256 mismatch"):
        _client(core).export("seg", tmp_path / "seg.zip")

    assert list(tmp_path.iterdir()) == []


def test_an_unlisted_member_fails_verification(tmp_path):
    core = _Core(_zip_bytes(_SOURCES, extra={"smuggled.py": b"x"}))

    with pytest.raises(R4Error, match="smuggled.py: in the archive but not listed"):
        _client(core).export("seg", tmp_path / "seg.zip")

    assert list(tmp_path.iterdir()) == []


def test_a_listed_but_missing_member_fails_verification(tmp_path):
    manifest = _manifest({**_SOURCES, "gone.py": b"y"}, [])
    core = _Core(_zip_bytes(_SOURCES, manifest))

    with pytest.raises(R4Error, match="gone.py: listed but missing"):
        _client(core).export("seg", tmp_path / "seg.zip")


def test_a_non_zip_body_raises_and_leaves_no_file(tmp_path):
    core = _Core(b"<html>sign in</html>")

    with pytest.raises(R4Error, match="not a readable workflow export archive"):
        _client(core).export("seg", tmp_path / "seg.zip")

    assert list(tmp_path.iterdir()) == []


def test_a_server_404_raises_and_leaves_no_file(tmp_path):
    with pytest.raises(R4Error, match="not found"):
        _client(_Core(status=404)).export("seg", tmp_path / "seg.zip")

    assert list(tmp_path.iterdir()) == []


def _zip_file(tmp_path: Path, members: dict[str, bytes]) -> Path:
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in members.items():
            zf.writestr(rel, data)
    return path


@pytest.mark.parametrize("bad", ["../evil.txt", "steps/../../evil.txt", "/abs/evil.txt"])
def test_extract_refuses_an_escaping_member_and_writes_nothing(tmp_path, bad):
    archive = _zip_file(tmp_path, {"ok.py": b"ok", bad: b"pwned"})
    out = tmp_path / "out"

    with pytest.raises(R4Error, match="escapes"):
        extract_export(archive, out)

    assert not (out / "ok.py").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_extract_unpacks_and_keeps_existing_files_without_overwrite(tmp_path):
    archive = _zip_file(tmp_path, {"steps/a.py": b"new"})
    out = tmp_path / "out"

    assert extract_export(archive, out) == out.resolve()
    assert (out / "steps/a.py").read_bytes() == b"new"
    (out / "steps/a.py").write_bytes(b"edited")
    with pytest.raises(R4Error, match="already exists"):
        extract_export(archive, out)
    assert (out / "steps/a.py").read_bytes() == b"edited"
    extract_export(archive, out, overwrite=True)
    assert (out / "steps/a.py").read_bytes() == b"new"


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse(argv: list[str]) -> Namespace:
    parser = argparse.ArgumentParser()
    WorkflowCommand().register(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def test_cli_parses_export_flags():
    args = _parse(["workflow", "export", "seg", "-o", "out/", "--force", "--extract", "src"])

    assert args.workflow_command == "export"
    assert (args.workflow_name, args.output, args.force, args.extract) == (
        "seg",
        "out/",
        True,
        "src",
    )


def _cli_wf(tmp_path: Path, body: bytes) -> MagicMock:
    archive = tmp_path / "seg.zip"
    archive.write_bytes(body)
    wf = MagicMock()
    wf.export.return_value = archive
    return wf


def test_cli_prints_path_and_counts(tmp_path, capsys):
    wf = _cli_wf(tmp_path, _zip_bytes(_SOURCES))
    args = _parse(["workflow", "export", "seg", "--force"])

    assert _cmd_export(wf, args) == 0

    wf.export.assert_called_once_with("seg", None, overwrite=True)
    out = capsys.readouterr().out
    assert f"exported seg -> {tmp_path / 'seg.zip'}" in out
    assert "files: 2  excluded: 1" in out


def test_cli_json_and_extract(tmp_path, capsys):
    wf = _cli_wf(tmp_path, _zip_bytes(_SOURCES))
    dest = tmp_path / "src"
    args = _parse(["workflow", "export", "seg", "--extract", str(dest), "--json"])

    assert _cmd_export(wf, args) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["files"] == 2 and summary["excluded"] == 1
    assert summary["extracted_to"] == str(dest.resolve())
    assert (dest / "steps/segment.py").read_bytes() == _SOURCES["steps/segment.py"]


def test_cli_extract_escape_fails_with_an_error(tmp_path, capsys):
    files = {"../evil.py": b"x"}
    wf = _cli_wf(tmp_path, _zip_bytes(files))
    args = _parse(["workflow", "export", "seg", "--extract", str(tmp_path / "src")])

    assert _cmd_export(wf, args) == 1

    assert "escapes" in capsys.readouterr().err
    assert not (tmp_path / "evil.py").exists()


def test_cli_reports_an_sdk_error(tmp_path, capsys):
    wf = MagicMock()
    wf.export.side_effect = R4Error("seg.zip already exists; pass overwrite=True (CLI: --force)")

    assert _cmd_export(wf, _parse(["workflow", "export", "seg"])) == 1

    assert "--force" in capsys.readouterr().err
