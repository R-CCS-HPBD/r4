"""Tests for the companion Jupyter magic commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._config = MagicMock()
    client._config.model = "test-model"
    # Make _request return a ChatResponse-like object
    resp = MagicMock()
    resp.content = "Test response from companion"
    resp.raw_response = {"choices": [{"message": {"content": "Test response from companion"}}]}
    client._request.return_value = resp
    client.list_endpoints.return_value = {
        "endpoints": [
            {"instance_id": "ep-test", "model": "test-model", "status": "ready"},
        ],
        "total": 1,
    }
    return client


@pytest.fixture
def magics(mock_client, tmp_path, monkeypatch):
    """Create CompanionMagics with mocked client and temp DB."""
    # Point session DB to tmp
    import r4.companion.session as sess_mod

    monkeypatch.setattr(sess_mod, "DB_PATH", tmp_path / "sessions.db")

    # Point memory to tmp
    import r4.companion.context as ctx_mod

    monkeypatch.setattr(ctx_mod, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(ctx_mod, "MEMORY_FILE", tmp_path / "memory" / "memory.json")

    from r4.jupyter.companion_magic import CompanionMagics

    # IPython Magics requires a Configurable-compatible shell
    shell = MagicMock()
    shell.configurables = []

    # Bypass traitlets validation by creating the object without __init__
    m = CompanionMagics.__new__(CompanionMagics)
    m.shell = shell
    m._session = None
    m._store = None
    m._client = mock_client
    m._model = None
    return m


class TestCompanionLineMagic:
    def test_empty_query(self, magics):
        """Empty query shows usage hint (no error)."""
        magics.companion("")  # should not raise

    def test_sends_query(self, magics):
        """Line magic sends query and gets response."""
        with patch("r4.jupyter.companion_magic.display") as mock_display:
            magics.companion("What GPU should I use?")
            mock_display.assert_called()
            # Session should be created
            assert magics._session is not None

    def test_output_variable(self, magics):
        """Response stored in user namespace when -o flag used."""
        magics.companion("-o result What GPU?")
        assert magics.shell.user_ns.__setitem__.call_args[0][0] == "result"

    def test_model_override(self, magics):
        """--model flag sets the model."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion("-m custom-model hello")
            assert magics._model == "custom-model"


class TestCompanionCellMagic:
    def test_empty_cell(self, magics):
        """Empty cell shows usage hint."""
        magics.companion_cell("", "")  # should not raise

    def test_sends_cell(self, magics):
        """Cell magic sends multi-line prompt."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_cell("", "Help me analyze\nmy seismic data")
            assert magics._session is not None
            assert len(magics._session.messages) == 2  # user + assistant

    def test_skill_flag(self, magics):
        """--skill flag activates a skill for the cell."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_cell("-s hpc", "Submit a Slurm job")
            assert "hpc" in magics._session.skills._active


class TestCompanionSessions:
    def test_list_empty(self, magics):
        """List sessions when empty."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_sessions("list")

    def test_list_after_send(self, magics):
        """After a send, at least one session exists."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion("hello")
            magics.companion_sessions("list")
            # Should have displayed a table
            assert magics._store.list_sessions()

    def test_resume_nonexistent(self, magics):
        """Resume with bad ID doesn't crash."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_sessions("resume nonexistent")

    def test_export_nonexistent(self, magics):
        """Export with bad ID doesn't crash."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_sessions("export nonexistent")


class TestCompanionRemember:
    def test_remember(self, magics):
        """Remember stores a memory fact."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_remember("project seismic simulation")
            value = magics._session.context.get_memory("project")
            assert value == "seismic simulation"


class TestCompanionSkill:
    def test_list_skills(self, magics):
        """List skills without error."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_skill("")

    def test_activate_skill(self, magics):
        """Activate a skill by name."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion_skill("hpc")
            assert "hpc" in magics._session.skills._active


class TestCompanionConfig:
    def test_info(self, magics):
        """Show config info."""
        with patch("r4.jupyter.companion_magic.display"):
            magics.companion("init")  # ensure session exists
            magics.companion_config("--info")

    def test_trust(self, magics):
        """--trust sets TRUST mode."""
        from r4.companion.permissions import PermissionMode

        with patch("r4.jupyter.companion_magic.display"):
            magics.companion("init")
            magics.companion_config("--trust")
            assert magics._session.permissions.mode == PermissionMode.TRUST


class TestLoadExtension:
    def test_load_unload(self):
        """Extension load/unload doesn't crash."""
        from r4.jupyter.companion_magic import (
            load_ipython_extension,
            unload_ipython_extension,
        )

        shell = MagicMock()
        load_ipython_extension(shell)
        shell.register_magics.assert_called_once()
        unload_ipython_extension(shell)  # no-op
