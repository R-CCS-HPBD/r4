"""Tests for companion built-in tools (Phase 2i additions)."""

from __future__ import annotations

import json
from pathlib import Path

from r4.companion import tools as t
from r4.companion.permissions import SAFE_TOOLS


def test_grep_glob_in_safe_tools():
    assert "grep_search" in SAFE_TOOLS
    assert "glob_search" in SAFE_TOOLS


def test_grep_search_finds_matches(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\nhello again\n")
    (tmp_path / "b.py").write_text("def hello():\n    pass\n")
    out = t._grep_search(r"hello", path=str(tmp_path))
    assert "hello world" in out
    assert "hello again" in out
    assert "def hello" in out


def test_grep_search_no_matches(tmp_path: Path):
    (tmp_path / "a.txt").write_text("nothing here\n")
    out = t._grep_search("xyzzy", path=str(tmp_path))
    assert "no matches" in out.lower()


def test_grep_search_invalid_path():
    out = t._grep_search("x", path="/no/such/path/here")
    assert out.startswith("Error")


def test_glob_search_basic(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("y")
    (tmp_path / "c.txt").write_text("z")
    out = t._glob_search("*.py", path=str(tmp_path))
    assert "a.py" in out
    assert "b.py" in out
    assert "c.txt" not in out


def test_glob_search_recursive(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").write_text("x")
    out = t._glob_search("**/*.py", path=str(tmp_path))
    assert "deep.py" in out


def test_file_edit_unique_default(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("alpha beta gamma")
    out = t._file_edit(str(p), "beta", "BETA")
    assert "replaced 1" in out
    assert p.read_text() == "alpha BETA gamma"


def test_file_edit_multi_match_without_expected_fails(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo foo foo")
    out = t._file_edit(str(p), "foo", "BAR")
    assert "matches 3 locations" in out
    assert p.read_text() == "foo foo foo"  # unchanged


def test_file_edit_expected_count_match(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo foo foo")
    out = t._file_edit(str(p), "foo", "BAR", expected_count=3)
    assert "replaced 3" in out
    assert p.read_text() == "BAR BAR BAR"


def test_file_edit_expected_count_mismatch(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo foo foo")
    out = t._file_edit(str(p), "foo", "BAR", expected_count=2)
    assert "expected_count=2" in out
    assert p.read_text() == "foo foo foo"


def test_file_edit_diff_in_output(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("line1\nold\nline3\n")
    out = t._file_edit(str(p), "old", "new")
    assert "-old" in out or "old" in out  # diff content present
    assert "+new" in out


def test_file_write_diff_in_output(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello\n")
    out = t._file_write(str(p), "world\n")
    assert "Written" in out
    assert "+world" in out


def test_get_builtin_tools_registers_new_tools():
    tools = t.get_builtin_tools(client=None)
    names = {x["name"] for x in tools}
    expected = {
        "grep_search",
        "glob_search",
        "detect_code_context",
        "detect_language_servers",
        "start_language_servers",
        "compile_code",
        "run_code",
        "test_code",
    }
    assert expected <= names


def test_agent_builtin_tools_accept_keyword_args(monkeypatch):
    """Agent builtins must accept **kwargs like _invoke_tool_executor passes them.

    Without a session's getter the web tools follow R4_WEB_ACCESS (else they
    refuse, ADR-099 D-9): public here, so the handlers themselves run (no
    search backend configured: no network)."""
    monkeypatch.setenv("R4_WEB_ACCESS", "public")
    monkeypatch.delenv("R4_WEB_SEARCH_URL", raising=False)
    tools = t.get_builtin_tools(client=None)
    by_name = {tool["name"]: tool for tool in tools}

    web_search = by_name["web_search"]
    out = web_search["execute"](query="test query")
    assert out.startswith("Error: no web search backend configured")

    web_fetch = by_name["web_fetch"]
    out = web_fetch["execute"](url="not-a-url")
    assert "http://" in out.lower() or "https://" in out.lower()


def test_detect_code_context_reports_languages_and_make(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n\t@echo built\ntest:\n\t@echo tested\n")
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")

    payload = json.loads(t._detect_code_context(str(tmp_path)))

    assert payload["root"] == str(tmp_path)
    assert payload["languages"]["c"] == 1
    assert "make" in payload["build_systems"]
    assert "test" in payload["make_targets"]
    assert payload["language_servers"]["detected_languages"] == ["c"]


def test_detect_language_servers_reports_supported_languages(tmp_path: Path):
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")

    payload = json.loads(t._detect_language_servers(str(tmp_path)))

    assert payload["detected_languages"] == ["cpp"]
    assert payload["supported_languages"] == ["cpp"]
    assert payload["unsupported_languages"] == []


def test_compile_code_python_syntax_check(tmp_path: Path):
    source = tmp_path / "hello.py"
    source.write_text("print('hello')\n")

    out = t._compile_code(path=str(source), language="python")

    assert "Code compile" in out
    assert "py_compile" in out
    assert "exit code" not in out


def test_coding_skill_includes_grep_glob():
    from r4.companion.skills import SkillRegistry

    code = SkillRegistry().get("code")
    assert code is not None
    expected_tools = {
        "grep_search",
        "glob_search",
        "detect_code_context",
        "detect_language_servers",
        "start_language_servers",
        "compile_code",
    }
    assert expected_tools <= set(code.tool_names)
    assert any("read" in kw or "test" in kw for kw in code.keywords + ["test"])


def test_project_scaffold_python(tmp_path: Path):
    out = t._project_scaffold("myproj", path=str(tmp_path), template="python")
    assert "Created python project 'myproj'" in out
    assert (tmp_path / "myproj" / "README.md").exists()
    assert (tmp_path / "myproj" / "pyproject.toml").exists()
    assert (tmp_path / "myproj" / "src" / "myproj" / "__init__.py").exists()
    assert (tmp_path / "myproj" / "tests" / "test_placeholder.py").exists()


def test_project_scaffold_research(tmp_path: Path):
    out = t._project_scaffold("myresearch", path=str(tmp_path), template="research")
    assert "Created research project 'myresearch'" in out
    proj = tmp_path / "myresearch"
    assert (proj / "notebooks").is_dir()
    assert (proj / "scripts").is_dir()
    assert (proj / "results").is_dir()
    assert (proj / "drafts").is_dir()


def test_project_scaffold_hpc(tmp_path: Path):
    out = t._project_scaffold("myhpc", path=str(tmp_path), template="hpc")
    assert "Created hpc project 'myhpc'" in out
    proj = tmp_path / "myhpc"
    assert (proj / "jobs" / "template.sh").exists()
    assert (proj / "containers" / "Dockerfile").exists()
    assert (proj / "Makefile").exists()


def test_project_scaffold_existing_dir_fails(tmp_path: Path):
    d = tmp_path / "exists"
    d.mkdir()
    out = t._project_scaffold("exists", path=str(tmp_path), template="python")
    assert "already exists" in out


def test_project_scaffold_unknown_template(tmp_path: Path):
    out = t._project_scaffold("x", path=str(tmp_path), template="unknown")
    assert "unknown template" in out


def test_file_read_with_offset_and_limit(tmp_path: Path):
    sample = tmp_path / "sample.txt"
    sample.write_text("line1\nline2\nline3\nline4\n")
    out = t._file_read(str(sample), offset=1, limit=2)
    assert "2|line2" in out
    assert "3|line3" in out
    assert "1|" not in out
    assert "more lines" in out
