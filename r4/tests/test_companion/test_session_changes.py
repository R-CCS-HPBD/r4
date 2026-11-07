"""Session file-change tracking (OpenCode-parity session diffs) — W1.

Write/edit tools must record cumulative before→after per path so the
bridge can serve precise per-file diffs without git.
"""

from __future__ import annotations

from pathlib import Path

from r4.companion import tools


def setup_function() -> None:
    tools.reset_session_changes()


def teardown_function() -> None:
    tools.reset_session_changes()


def test_file_write_records_new_and_edited(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools._file_write("demo.txt", "line-a\n")
    tools._file_write("demo.txt", "line-a\nline-b\n")
    changes = tools.get_session_changes()
    assert len(changes) == 1
    entry = changes[0]
    assert entry["path"] == "demo.txt"
    assert entry["before"] == ""
    assert entry["after"] == "line-a\nline-b\n"
    assert entry["edits"] == 2


def test_file_edit_records_cumulative_before_after(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("doc.md").write_text("alpha\nbeta\n")
    tools._file_edit("doc.md", "alpha", "ALPHA")
    tools._file_edit("doc.md", "beta", "BETA")
    (entry,) = tools.get_session_changes()
    assert entry["before"] == "alpha\nbeta\n"
    assert entry["after"] == "ALPHA\nBETA\n"


def test_changes_sorted_by_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools._file_write("z.txt", "z")
    tools._file_write("a.txt", "a")
    paths = [c["path"] for c in tools.get_session_changes()]
    assert paths == sorted(paths)


def test_bridge_rpc_returns_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools._file_write("x.txt", "1\n")
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    payload = CompanionBridgeServer.get_session_changes(server, {})
    assert payload["changes"][0]["path"] == "x.txt"


def test_revert_restores_recorded_before_state(tmp_path, monkeypatch):
    """revert_session_change writes the recorded before back; history stays honest."""
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("f.txt").write_text("v1\n")  # pre-session state
    tools._file_edit("f.txt", "v1", "v2")
    out = tools.revert_session_change("f.txt")
    assert out["reverted"] is True
    assert tmp_path.joinpath("f.txt").read_text() == "v1\n"
    (entry,) = tools.get_session_changes()
    assert entry["after"] == "v1\n"
    assert entry.get("reverted") is True


def test_revert_deletes_file_created_by_session(tmp_path, monkeypatch):
    """Files created BY the session are removed (they did not exist at session start)."""
    monkeypatch.chdir(tmp_path)
    tools._file_write("new.txt", "content\n")
    out = tools.revert_session_change("new.txt")
    assert out["reverted"] is True
    assert out["deleted"] is True
    assert not tmp_path.joinpath("new.txt").exists()


def test_revert_unknown_path_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import pytest

    with pytest.raises(ValueError, match="no recorded change"):
        tools.revert_session_change("nope.txt")
