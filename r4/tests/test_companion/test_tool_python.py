"""Pins for the tools' Python interpreter (TB 4.0 harness-efficiency plan, item H1).

``python_exec`` and the coding helpers ran ``sys.executable``: inside R4's own
venv (pipx, ``uv tool``, the bench's ``/opt/r4bench/venv``) the user's
packages were "missing". ``tool_python()`` picks the user's Python instead;
``R4_TOOL_PYTHON=sys`` restores the old interpreter.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from r4.companion import coding
from r4.companion.tools import _python_exec, set_shell_chunk_sink

try:
    from r4.companion import tool_python
except ImportError:  # only when this file runs against pre-H1 code (old-code proof)
    tool_python = None

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX PATH layout")


def _fake_python(directory: Path, name: str = "python3", *, recent: bool = True) -> Path:
    """A stand-in interpreter; it answers the version probe (3.8 or newer unless not *recent*)."""
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text(f"#!/bin/sh\necho {recent}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.fixture(autouse=True)
def _no_user_environment(monkeypatch, tmp_path):
    """No conda env, no activated venv, and a workspace without a .venv."""
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    (tmp_path / "workspace").mkdir()
    monkeypatch.chdir(tmp_path / "workspace")
    if tool_python is not None and hasattr(tool_python, "_usable"):
        tool_python._usable.cache_clear()


@pytest.fixture
def fake_venv(tmp_path, monkeypatch):
    """R4 runs from a venv the user did not activate; a system python3 is on PATH."""
    venv = tmp_path / "r4venv"
    venv_python = _fake_python(venv / "bin", "python")
    _fake_python(venv / "bin", "python3")
    system_python = _fake_python(tmp_path / "sysbin")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys, "executable", str(venv_python))
    monkeypatch.setenv("PATH", os.pathsep.join([str(venv / "bin"), str(tmp_path / "sysbin")]))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("R4_TOOL_PYTHON", raising=False)
    return {"venv": venv, "venv_python": venv_python, "system_python": system_python}


@posix_only
def test_venv_install_uses_the_path_python_outside_the_venv(fake_venv):
    assert tool_python.tool_python() == str(fake_venv["system_python"])


@posix_only
def test_sys_switch_restores_r4s_interpreter(fake_venv, monkeypatch):
    monkeypatch.setenv("R4_TOOL_PYTHON", "sys")
    assert tool_python.tool_python() == str(fake_venv["venv_python"])


@posix_only
def test_explicit_interpreter_path_wins(fake_venv, monkeypatch):
    monkeypatch.setenv("R4_TOOL_PYTHON", "/opt/conda/bin/python")
    assert tool_python.tool_python() == "/opt/conda/bin/python"


@posix_only
def test_an_activated_r4_venv_is_the_users_choice(fake_venv, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", str(fake_venv["venv"]))
    assert tool_python.tool_python() == str(fake_venv["venv_python"])


@posix_only
def test_another_activated_venv_on_path_is_used(fake_venv, monkeypatch, tmp_path):
    project_python = _fake_python(tmp_path / "project-venv" / "bin")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "project-venv"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(project_python.parent), os.environ["PATH"]]))
    assert tool_python.tool_python() == str(project_python)


@posix_only
def test_activated_venv_wins_without_being_on_path(fake_venv, monkeypatch, tmp_path):
    """Review 2026-10-24: the user's environment comes before PATH."""
    project_python = _fake_python(tmp_path / "project-venv" / "bin", "python")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "project-venv"))
    assert tool_python.tool_python() == str(project_python)


@posix_only
def test_conda_env_wins_over_path(fake_venv, monkeypatch, tmp_path):
    conda_python = _fake_python(tmp_path / "conda" / "bin", "python")
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path / "conda"))
    assert tool_python.tool_python() == str(conda_python)


@posix_only
def test_workspace_venv_wins_over_path(fake_venv):
    """Review 2026-10-24: a project's unactivated .venv got the system python."""
    workspace_python = _fake_python(Path.cwd() / ".venv" / "bin", "python")
    assert tool_python.tool_python() == str(workspace_python)


@posix_only
def test_a_too_old_python_is_skipped(fake_venv, monkeypatch, tmp_path):
    """Review 2026-10-24: a RHEL8 /usr/bin/python3 is 3.6; R4's interpreter is kept."""
    old = _fake_python(tmp_path / "rhel8", recent=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(fake_venv["venv"] / "bin"), str(old.parent)]))
    assert tool_python.tool_python() == str(fake_venv["venv_python"])


@posix_only
def test_outside_a_venv_sys_executable_is_kept(fake_venv, monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    assert tool_python.tool_python() == str(fake_venv["venv_python"])


@posix_only
def test_no_python_outside_the_venv_falls_back(fake_venv, monkeypatch):
    monkeypatch.setenv("PATH", str(fake_venv["venv"] / "bin"))
    assert tool_python.tool_python() == str(fake_venv["venv_python"])


@posix_only
def test_macos_command_line_tools_stub_is_skipped(fake_venv, monkeypatch, tmp_path):
    stub = _fake_python(tmp_path / "usrbin")
    real = _fake_python(tmp_path / "later", "python")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(tool_python, "_MACOS_STUB", str(stub))
    monkeypatch.setattr(tool_python, "_macos_clt_missing", lambda: True)
    monkeypatch.setenv("PATH", os.pathsep.join([str(stub.parent), str(real.parent)]))
    assert tool_python.tool_python() == str(real)


@posix_only
def test_macos_python3_is_used_when_the_tools_are_installed(fake_venv, monkeypatch, tmp_path):
    stub = _fake_python(tmp_path / "usrbin")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(tool_python, "_MACOS_STUB", str(stub))
    monkeypatch.setattr(tool_python, "_macos_clt_missing", lambda: False)
    monkeypatch.setenv("PATH", str(stub.parent))
    assert tool_python.tool_python() == str(stub)


# ---------------------------------------------------------------------------
# The tools use it
# ---------------------------------------------------------------------------


def test_coding_commands_use_the_tool_python(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_TOOL_PYTHON", "/custom/python3")
    source = tmp_path / "job.py"
    source.write_text("print('x')\n")
    (tmp_path / "pyproject.toml").write_text("")
    kwargs = {"root": tmp_path, "language": "python", "timeout": 5}
    run = coding._run_steps(source=source, executable=None, args="", **kwargs)
    compiled = coding._compile_steps(source=source, target=None, output_path=None, **kwargs)
    tests = coding._test_steps(target=None, **kwargs)
    suggested = coding._suggested_commands(tmp_path, {"python": 1}, [], [])
    assert [run[0].argv[0], compiled[0].argv[0], tests[0].argv[0]] == ["/custom/python3"] * 3
    assert suggested == ["/custom/python3 -m pytest"]


def _base_interpreter_dir(tmp_path: Path) -> Path:
    """A PATH dir whose python3 is the base interpreter (no pyvenv.cfg beside it)."""
    bindir = tmp_path / "base-bin"
    bindir.mkdir()
    (bindir / "python3").symlink_to(os.path.realpath(sys.executable))
    return bindir


@posix_only
@pytest.mark.skipif(sys.prefix == sys.base_prefix, reason="the test run is not inside a venv")
def test_python_exec_runs_outside_r4s_venv(tmp_path, monkeypatch):
    """Old code: python_exec printed the venv prefix (R4's interpreter)."""
    set_shell_chunk_sink(None)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("R4_TOOL_PYTHON", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(_base_interpreter_dir(tmp_path)), "/bin"]))
    out = _python_exec("import sys; print(sys.prefix)", timeout=60)
    assert out.strip() == os.path.realpath(sys.base_prefix) or out.strip() == sys.base_prefix
    assert out.strip() != sys.prefix


@posix_only
@pytest.mark.skipif(sys.prefix == sys.base_prefix, reason="the test run is not inside a venv")
def test_python_exec_off_switch_keeps_r4s_venv(tmp_path, monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("R4_TOOL_PYTHON", "sys")
    monkeypatch.setenv("PATH", os.pathsep.join([str(_base_interpreter_dir(tmp_path)), "/bin"]))
    out = _python_exec("import sys; print(sys.prefix)", timeout=60)
    assert out.strip() == sys.prefix
