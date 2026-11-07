"""An explicit null for an optional tool parameter means "absent".

Live on the stand-alone stack (2026-10-09): llama3.2:3b called file_read with
limit/max_lines/offset all null; the nulls replaced the tool's defaults and
every read failed on int(None), so neither plan reader could see the file.
"""

from __future__ import annotations

from r4.companion.agent import ResearchCompanion, _without_null_optionals


def test_null_optionals_are_dropped_and_required_nulls_kept():
    tool = {"parameters": {"required": ["path"]}}
    args = {"path": None, "limit": None, "offset": 0, "max_lines": None}
    assert _without_null_optionals(tool, args) == {"path": None, "offset": 0}


def test_file_read_with_null_optionals_reads_the_file(tmp_path):
    from r4.companion.tools import get_builtin_tools

    target = tmp_path / "t.py"
    target.write_text("line one\nline two\n")
    tool = next(t for t in get_builtin_tools() if t["name"] == "file_read")
    companion = ResearchCompanion.__new__(ResearchCompanion)
    companion._emit = lambda *a, **k: None
    out = companion._invoke_tool_executor(
        "file_read", tool, {"path": str(target), "limit": None, "max_lines": None, "offset": None}
    )
    assert "line one" in out and "Error" not in out
