"""A plan agent's workspace, honoured by the file, shell and python tools
(ADR-092 slice 5).

Inside ``agent_workspace(ws)``: relative paths and the working directory
resolve under the workspace; every path is resolved to its real path first,
so ``..`` and symlinks cannot leave it; writes land only in the workspace,
reads in the workspace or the project root; anything else is refused by
name. Outside a workspace (the lead's own session) every tool is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from r4.companion import tools
from r4.companion.agent_workspace import (
    AgentWorkspace,
    WorkspaceEscape,
    agent_workspace,
    current_workspace,
    resolve,
)
from r4.companion.apply_patch import apply_patch


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    """project/ (the root), ws/ (an isolated copy), outside/ (neither)."""
    project = tmp_path / "project"
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    for d in (project, ws, outside):
        d.mkdir()
    (project / "orig.txt").write_text("project file\n")
    (ws / "orig.txt").write_text("workspace file\n")
    (outside / "secret.txt").write_text("SECRET outside\n")
    (ws / "sub").mkdir()
    return {"project": project, "ws": ws, "outside": outside}


@pytest.fixture
def isolated(tree: dict[str, Path]):
    workspace = AgentWorkspace.of(tree["ws"], tree["project"], label="impl#0")
    with agent_workspace(workspace):
        yield workspace


def _refused(text: str) -> bool:
    return text.startswith("Error: refused:") and "ADR-092 slice 5" in text


# ── the context variable ────────────────────────────────────────────────────


def test_no_workspace_is_set_by_default_and_the_context_restores(tree) -> None:
    assert current_workspace() is None
    ws = AgentWorkspace.of(tree["ws"], tree["project"])
    with agent_workspace(ws):
        assert current_workspace() == ws
        assert ws.isolated
    assert current_workspace() is None


def test_a_non_isolated_agent_works_in_the_project_root(tree) -> None:
    ws = AgentWorkspace.of(tree["project"], tree["project"])
    assert not ws.isolated
    with agent_workspace(ws):
        assert resolve("new.txt", write=True) == Path(os.path.realpath(tree["project"])) / "new.txt"
        with pytest.raises(WorkspaceEscape):
            resolve(str(tree["outside"] / "x"), write=True)


# ── relative, absolute, .., symlink: file_read ──────────────────────────────


def test_file_read_relative_resolves_in_the_workspace(isolated) -> None:
    assert tools._file_read("orig.txt") == "workspace file\n"


def test_file_read_may_read_the_project_root_by_absolute_path(tree, isolated) -> None:
    assert tools._file_read(str(tree["project"] / "orig.txt")) == "project file\n"


@pytest.mark.parametrize("how", ["absolute", "dotdot", "home"])
def test_file_read_outside_is_refused_by_name(tree, isolated, how: str) -> None:
    path = {
        "absolute": str(tree["outside"] / "secret.txt"),
        "dotdot": "../outside/secret.txt",
        "home": "~/.ssh/id_rsa",
    }[how]
    result = tools._file_read(path)
    assert _refused(result) and "SECRET" not in result


def test_file_read_through_a_symlink_that_leads_out_is_refused(tree, isolated) -> None:
    (tree["ws"] / "link.txt").symlink_to(tree["outside"] / "secret.txt")
    (tree["ws"] / "linkdir").symlink_to(tree["outside"])
    assert _refused(tools._file_read("link.txt"))
    assert _refused(tools._file_read("linkdir/secret.txt"))


def test_a_sibling_whose_name_extends_the_workspace_is_outside(tree, isolated) -> None:
    """Containment is by path component: ``<ws>-evil`` is not inside ``<ws>``."""
    sibling = tree["ws"].parent / (tree["ws"].name + "-evil")
    sibling.mkdir()
    assert _refused(tools._file_write(f"../{sibling.name}/x.txt", "pwned"))
    assert not (sibling / "x.txt").exists()


# ── file_write and file_edit ────────────────────────────────────────────────


def test_file_write_relative_lands_in_the_workspace_not_the_project(tree, isolated) -> None:
    result = tools._file_write("sub/new.py", "x = 1\n")
    assert result.startswith("Written")
    assert (tree["ws"] / "sub" / "new.py").read_text() == "x = 1\n"
    assert not (tree["project"] / "sub").exists()


@pytest.mark.parametrize(
    "path",
    ["{project}/orig.txt", "{outside}/x.txt", "../project/orig.txt", "sub/../../outside/x.txt"],
)
def test_file_write_outside_the_workspace_is_refused_project_root_included(
    tree, isolated, path: str
) -> None:
    target = path.format(project=tree["project"], outside=tree["outside"])
    assert _refused(tools._file_write(target, "pwned\n"))
    assert (tree["project"] / "orig.txt").read_text() == "project file\n"
    assert not (tree["outside"] / "x.txt").exists()


def test_file_write_through_a_symlink_is_refused(tree, isolated) -> None:
    (tree["ws"] / "evil.txt").symlink_to(tree["outside"] / "secret.txt")
    (tree["ws"] / "evildir").symlink_to(tree["outside"])
    (tree["ws"] / "proj").symlink_to(tree["project"])
    assert _refused(tools._file_write("evil.txt", "pwned"))
    assert _refused(tools._file_write("evildir/new.txt", "pwned"))
    assert _refused(tools._file_write("proj/orig.txt", "pwned"))
    assert (tree["outside"] / "secret.txt").read_text() == "SECRET outside\n"
    assert not (tree["outside"] / "new.txt").exists()
    assert (tree["project"] / "orig.txt").read_text() == "project file\n"


def test_file_edit_is_contained_like_file_write(tree, isolated) -> None:
    assert tools._file_edit("orig.txt", "workspace", "edited").startswith("Edited")
    assert (tree["ws"] / "orig.txt").read_text() == "edited file\n"
    assert _refused(tools._file_edit(str(tree["project"] / "orig.txt"), "project", "x"))
    (tree["ws"] / "evil.txt").symlink_to(tree["outside"] / "secret.txt")
    assert _refused(tools._file_edit("evil.txt", "SECRET", "x"))
    assert (tree["outside"] / "secret.txt").read_text() == "SECRET outside\n"


def test_an_isolated_write_is_not_recorded_as_a_session_change(tree, isolated) -> None:
    tools.reset_session_changes()
    try:
        tools._file_write("rec.txt", "a\n")
        assert tools.get_session_changes() == []
    finally:
        tools.reset_session_changes()


def test_a_project_root_agent_write_is_still_recorded(tree) -> None:
    tools.reset_session_changes()
    try:
        with agent_workspace(AgentWorkspace.of(tree["project"])):
            tools._file_write("rec.txt", "a\n")
        assert [c["after"] for c in tools.get_session_changes()] == ["a\n"]
    finally:
        tools.reset_session_changes()


# ── apply_patch ─────────────────────────────────────────────────────────────

_PATCH = """--- a/orig.txt
+++ b/orig.txt
@@ -1 +1 @@
-workspace file
+patched file
"""


def test_apply_patch_applies_under_the_workspace(tree, isolated) -> None:
    out = tools._apply_patch(_PATCH)
    assert "orig.txt" in out
    assert (tree["ws"] / "orig.txt").read_text() == "patched file\n"
    assert (tree["project"] / "orig.txt").read_text() == "project file\n"


def test_apply_patch_workdir_outside_is_refused(tree, isolated) -> None:
    assert _refused(tools._apply_patch(_PATCH, workdir=str(tree["project"])))
    assert _refused(tools._apply_patch(_PATCH, workdir=".."))
    assert (tree["project"] / "orig.txt").read_text() == "project file\n"


def test_apply_patch_paths_cannot_climb_out_of_the_workspace(tree, isolated) -> None:
    patch = "--- /dev/null\n+++ b/../outside/new.txt\n@@ -0,0 +1 @@\n+pwned\n"
    out = tools._apply_patch(patch)
    assert "escapes workdir" in out
    assert not (tree["outside"] / "new.txt").exists()


def test_apply_patch_containment_is_by_component_not_string_prefix(tmp_path: Path) -> None:
    """``<root>10/x`` is not inside ``<root>1`` (sibling workspaces)."""
    (tmp_path / "ws1").mkdir()
    (tmp_path / "ws10").mkdir()
    patch = "--- /dev/null\n+++ b/../ws10/x.txt\n@@ -0,0 +1 @@\n+pwned\n"
    result = apply_patch(patch, workdir=tmp_path / "ws1")
    assert not result.applied
    assert not (tmp_path / "ws10" / "x.txt").exists()


# ── shell_exec and python_exec: the working directory ───────────────────────


def test_shell_exec_starts_in_the_workspace(tree, isolated) -> None:
    out = tools._shell_exec("pwd")
    assert out.strip() == os.path.realpath(tree["ws"])


def test_shell_exec_cwd_resolves_inside_and_is_refused_outside(tree, isolated) -> None:
    assert tools._shell_exec("pwd", cwd="sub").strip() == os.path.realpath(tree["ws"] / "sub")
    for cwd in ("..", str(tree["project"]), str(tree["outside"]), "/"):
        assert _refused(tools._shell_exec("touch made-here", cwd=cwd))
    assert not (tree["project"] / "made-here").exists()
    assert not (tree["outside"] / "made-here").exists()


def test_streamed_shell_exec_starts_in_the_workspace_too(tree, isolated) -> None:
    previous = tools.get_shell_chunk_sink()
    tools.set_shell_chunk_sink(lambda _sid, _chunk: None)
    try:
        out = tools._shell_exec("pwd")
        assert os.path.realpath(tree["ws"]) in out
        assert _refused(tools._shell_exec("pwd", cwd=str(tree["outside"])))
    finally:
        tools.set_shell_chunk_sink(previous)


def test_python_exec_starts_in_the_workspace(tree, isolated) -> None:
    out = tools._python_exec("import os; print(os.getcwd())")
    assert out.strip() == os.path.realpath(tree["ws"])


# ── the read tools ──────────────────────────────────────────────────────────


def test_list_dir_is_contained(tree, isolated) -> None:
    assert "orig.txt" in tools._list_dir(".")
    assert "orig.txt" in tools._list_dir(str(tree["project"]))  # a read of the root
    assert _refused(tools._list_dir(str(tree["outside"])))
    assert _refused(tools._list_dir("../outside"))


def test_glob_search_is_contained(tree, isolated) -> None:
    (tree["ws"] / "linkdir").symlink_to(tree["outside"])
    found = tools._glob_search("**/*.txt")
    assert "orig.txt" in found and "secret.txt" not in found
    # an explicit symlinked component is followed by glob: the result is dropped
    assert "secret.txt" not in tools._glob_search("linkdir/*.txt")
    assert _refused(tools._glob_search("../outside/*"))
    assert _refused(tools._glob_search("*", path=str(tree["outside"])))


@pytest.mark.parametrize("ripgrep", [True, False])
def test_grep_search_is_contained(tree, isolated, monkeypatch, ripgrep: bool) -> None:
    (tree["ws"] / "linkdir").symlink_to(tree["outside"])
    (tree["ws"] / "linkfile.txt").symlink_to(tree["outside"] / "secret.txt")
    if not ripgrep:
        monkeypatch.setattr(tools, "_grep_with_rg", lambda *a, **k: None)
    assert "SECRET" not in tools._grep_search("SECRET")
    assert _refused(tools._grep_search("SECRET", path=str(tree["outside"])))


# ── outside a plan agent nothing changes ────────────────────────────────────


def test_without_a_workspace_the_tools_are_unchanged(tree, monkeypatch) -> None:
    monkeypatch.chdir(tree["project"])
    assert tools._file_read(str(tree["outside"] / "secret.txt")) == "SECRET outside\n"
    target = tree["outside"] / "lead.txt"
    assert tools._file_write(str(target), "lead\n").startswith("Written")
    assert tools._shell_exec("pwd").strip() == os.path.realpath(tree["project"])
    tools.reset_session_changes()
