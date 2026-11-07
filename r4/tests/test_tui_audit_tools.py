"""TUI audit (tools lane): the changes-overlay revert restores exactly or refuses.

Finding 68: the recorded before-state was capped at 64 KB, and revert wrote
that capped text over the file while reporting success; a file the session
created was reverted to an empty file instead of being removed.
"""

from __future__ import annotations

import pytest

from r4.companion import tools


def setup_function() -> None:
    tools.reset_session_changes()


def teardown_function() -> None:
    tools.reset_session_changes()


def test_revert_refuses_when_before_state_was_capped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = "x" * (tools._CHANGE_CONTENT_CAP + 1000) + "\nEND\n"
    tmp_path.joinpath("big.txt").write_text(original)
    tools._file_edit("big.txt", "END", "FIN")
    edited = tmp_path.joinpath("big.txt").read_text()

    (entry,) = tools.get_session_changes()
    assert entry["before_truncated"] is True
    with pytest.raises(ValueError, match="truncated"):
        tools.revert_session_change("big.txt")
    # The file is left exactly as the agent's edit made it, never half-restored.
    assert tmp_path.joinpath("big.txt").read_text() == edited


def test_revert_deletes_a_file_the_session_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools._file_write("new.txt", "content\n")

    (entry,) = tools.get_session_changes()
    assert entry["before_existed"] is False
    out = tools.revert_session_change("new.txt")
    assert out["reverted"] is True
    assert out["deleted"] is True
    assert not tmp_path.joinpath("new.txt").exists()


def test_revert_of_existing_empty_file_keeps_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("empty.txt").write_text("")
    tools._file_write("empty.txt", "filled\n")

    out = tools.revert_session_change("empty.txt")
    assert out["reverted"] is True
    assert out.get("deleted") is not True
    assert tmp_path.joinpath("empty.txt").read_text() == ""


def test_revert_of_small_existing_file_is_exact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("f.txt").write_text("v1\n")
    tools._file_edit("f.txt", "v1", "v2")
    tools._file_edit("f.txt", "v2", "v3")

    out = tools.revert_session_change("f.txt")
    assert out == {"path": "f.txt", "reverted": True, "bytes": 3}
    assert tmp_path.joinpath("f.txt").read_text() == "v1\n"


@pytest.mark.parametrize(
    "raw",
    [b"caf\xe9\n", b"a\r\nb\r\n", b"a\rb\n"],
    ids=["latin-1", "crlf", "lone-cr"],
)
def test_revert_refuses_when_write_read_a_lossy_before_state(tmp_path, monkeypatch, raw):
    # _file_write reads with errors="replace" and universal newlines, so the
    # recorded before-text does not hold these bytes; writing it back would
    # corrupt the file while reporting success (review of #68).
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("f.txt").write_bytes(raw)
    tools._file_write("f.txt", "new\n")

    (entry,) = tools.get_session_changes()
    assert entry["before_lossy"] is True
    with pytest.raises(ValueError, match="cannot revert it exactly"):
        tools.revert_session_change("f.txt")
    assert tmp_path.joinpath("f.txt").read_bytes() == b"new\n"


def test_revert_refuses_when_edit_read_a_crlf_before_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("f.txt").write_bytes(b"v1\r\nkeep\r\n")
    tools._file_edit("f.txt", "v1", "v2")
    edited = tmp_path.joinpath("f.txt").read_bytes()

    with pytest.raises(ValueError, match="cannot revert it exactly"):
        tools.revert_session_change("f.txt")
    assert tmp_path.joinpath("f.txt").read_bytes() == edited


def test_revert_restores_utf8_lf_bytes_exactly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw = "café — 日本\nend\n".encode()
    tmp_path.joinpath("f.txt").write_bytes(raw)
    tools._file_write("f.txt", "new\n")

    (entry,) = tools.get_session_changes()
    assert entry["before_lossy"] is False
    assert tools.revert_session_change("f.txt")["reverted"] is True
    assert tmp_path.joinpath("f.txt").read_bytes() == raw
