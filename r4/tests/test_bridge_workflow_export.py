"""Pins for the console's ``workflow.export`` bridge verb (2026-11-07).

The bridge picks the folder (explicit, else cwd, else ~/Downloads, each only
when writable), never overwrites (numeric suffix), passes ``overwrite=False``
to ``WorkflowClient.export`` and reads the counts back from EXPORT.json.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from r4.companion.ui import workflow_export as wx
from r4.companion.ui.bridge_server import BRIDGE_RPC_NAMES, CompanionBridgeServer


def _zip(dest: Path, manifest: Any) -> Path:
    with zipfile.ZipFile(dest, "w") as archive:
        archive.writestr("EXPORT.json", json.dumps(manifest))
    return dest


class _FakeWorkflows:
    """``WorkflowClient.export`` as the contract states it; records each call."""

    def __init__(self, manifest: Any = None) -> None:
        self.calls: list[tuple[str, Path, bool]] = []
        self.manifest = manifest if manifest is not None else {"files": [{}, {}], "excluded": ["x"]}

    def export(self, name: str, dest: Path, *, overwrite: bool = False) -> Path:
        self.calls.append((name, Path(dest), overwrite))
        return _zip(Path(dest), self.manifest)


def test_export_names_a_missing_workflow():
    with pytest.raises(ValueError, match="workflow_name is required"):
        wx.export_workflow(_FakeWorkflows(), {"workflow_name": "  "})


def test_export_writes_into_dest_dir_and_reports_counts(tmp_path):
    fake = _FakeWorkflows()
    out = wx.export_workflow(fake, {"workflow_name": "demo", "dest_dir": str(tmp_path)})
    assert fake.calls == [("demo", tmp_path / "demo.zip", False)]
    assert out == {
        "ok": True,
        "workflow_name": "demo",
        "path": str(tmp_path / "demo.zip"),
        "files": 2,
        "excluded": 1,
    }


def test_export_never_overwrites_an_earlier_export(tmp_path):
    (tmp_path / "demo.zip").write_bytes(b"old")
    (tmp_path / "demo-1.zip").write_bytes(b"older")
    fake = _FakeWorkflows()
    out = wx.export_workflow(fake, {"workflow_name": "demo", "dest_dir": str(tmp_path)})
    assert out["path"] == str(tmp_path / "demo-2.zip")
    assert (tmp_path / "demo.zip").read_bytes() == b"old"
    assert (tmp_path / "demo-1.zip").read_bytes() == b"older"


def test_free_export_path_sanitises_the_name(tmp_path):
    assert wx.free_export_path(tmp_path, "../a b/c") == tmp_path / "a_b_c.zip"
    assert wx.export_file_name("...") == "workflow"


def test_free_export_path_refuses_after_the_last_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "_MAX_SUFFIX", 1)
    (tmp_path / "w.zip").write_bytes(b"")
    (tmp_path / "w-1.zip").write_bytes(b"")
    with pytest.raises(ValueError, match="already holds 1 exports"):
        wx.free_export_path(tmp_path, "w")


def test_dest_dir_prefers_a_writable_cwd(tmp_path):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    assert wx.export_dest_dir(cwd=tmp_path, home=home) == tmp_path


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through chmod")
def test_dest_dir_falls_back_to_downloads_when_cwd_is_read_only(tmp_path):
    cwd = tmp_path / "ro"
    cwd.mkdir()
    downloads = tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)
    cwd.chmod(0o500)
    try:
        assert wx.export_dest_dir(cwd=cwd, home=tmp_path / "home") == downloads
        with pytest.raises(ValueError, match="neither the working directory"):
            wx.export_dest_dir(cwd=cwd, home=tmp_path / "nohome")
    finally:
        cwd.chmod(0o700)


def test_explicit_dest_dir_must_be_a_writable_folder(tmp_path):
    with pytest.raises(ValueError, match="is not a writable folder"):
        wx.export_dest_dir(str(tmp_path / "missing"))


def test_manifest_counts_tolerate_a_bad_archive(tmp_path):
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    assert wx.manifest_counts(tmp_path / "bad.zip") == (None, None)
    assert wx.manifest_counts(_zip(tmp_path / "list.zip", [1])) == (None, None)


def test_manifest_counts_skip_non_list_fields(tmp_path):
    path = _zip(tmp_path / "odd.zip", {"files": "x", "excluded": []})
    assert wx.manifest_counts(path) == (None, 0)


def test_bridge_method_routes_to_the_session_client(tmp_path):
    fake = _FakeWorkflows()
    server = SimpleNamespace(session=SimpleNamespace(client=SimpleNamespace(workflows=fake)))
    out = CompanionBridgeServer.workflow_export(  # type: ignore[arg-type]
        server, {"workflow_name": "wf", "dest_dir": str(tmp_path)}
    )
    assert out["path"] == str(tmp_path / "wf.zip")
    assert fake.calls == [("wf", tmp_path / "wf.zip", False)]


def test_workflow_export_is_bound():
    assert "workflow.export" in BRIDGE_RPC_NAMES


def test_workflow_export_is_classed_as_a_read():
    """A MUTATE verb runs on the stdin thread under the exclusive lock: a
    600 s download froze the console. The export changes no bridge, session
    or remote state and writes a fresh file, so it is a read (2026-11-07)."""
    from r4.companion.ui.rpc_concurrency import MUTATING_RPCS, READ_RPCS, RpcClass, rpc_class

    assert rpc_class("workflow.export") is RpcClass.READ
    assert "workflow.export" in READ_RPCS
    assert "workflow.export" not in MUTATING_RPCS


def test_concurrent_exports_never_pick_the_same_file(tmp_path):
    """``workflow.export`` is a READ (2026-11-07), so two console exports of
    one workflow can overlap: the second must not choose the name the first
    is still downloading into (it would replace it on landing)."""
    import threading

    started, release = threading.Event(), threading.Event()

    class _Slow(_FakeWorkflows):
        def export(self, name: str, dest: Path, *, overwrite: bool = False) -> Path:
            if not self.calls:
                self.calls.append((name, Path(dest), overwrite))
                started.set()
                release.wait(5)
                return _zip(Path(dest), {"files": [{}], "excluded": []})
            return super().export(name, dest, overwrite=overwrite)

    fake = _Slow()
    params = {"workflow_name": "demo", "dest_dir": str(tmp_path)}
    first: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: first.update(wx.export_workflow(fake, params)))
    worker.start()
    assert started.wait(5)
    second = wx.export_workflow(fake, params)
    release.set()
    worker.join(5)
    assert {first["path"], second["path"]} == {
        str(tmp_path / "demo.zip"),
        str(tmp_path / "demo-1.zip"),
    }
    assert wx.manifest_counts(Path(first["path"])) == (1, 0)
    assert wx.manifest_counts(Path(second["path"])) == (2, 1)
