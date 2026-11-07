"""Unit pins for companion apply_patch (parser + applier + tool wrapper).

Deterministic: tmp_path workspaces only, no LLM, no network.
"""

from __future__ import annotations

from pathlib import Path

from r4.companion.apply_patch import apply_patch, parse_patch
from r4.companion.tools import _apply_patch

MODIFY_PATCH = """\
--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 alpha
-beta
+bravo
 gamma
"""

NEW_PATCH = """\
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+one
+two
"""

DELETE_PATCH = """\
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
+"""

RENAME_PATCH = """\
diff --git a/before.py b/after.py
similarity index 80%
rename from before.py
rename to after.py
--- a/before.py
+++ b/after.py
@@ -1,2 +1,2 @@
 keep
-old_line
+new_line
"""

HEADING_PATCH = """\
--- a/h.py
+++ b/h.py
@@ -1,2 +1,3 @@ def heading()
 x
+y
 z
"""


def _seed(tmp_path: Path, name: str, content: str) -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_parse_modify_structure() -> None:
    fps = parse_patch(MODIFY_PATCH)
    assert len(fps) == 1
    fp = fps[0]
    assert fp.action == "modify"
    assert fp.old_path == "app.py" and fp.new_path == "app.py"
    assert fp.hunks[0].old_start == 1 and fp.hunks[0].old_count == 3


def test_apply_modify_in_place(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "alpha\nbeta\ngamma\n")
    result = apply_patch(MODIFY_PATCH, workdir=tmp_path)
    assert result.applied and not result.conflicts
    assert (tmp_path / "app.py").read_text() == "alpha\nbravo\ngamma\n"
    assert result.files[0]["added"] == 1 and result.files[0]["removed"] == 1


def test_apply_create_file(tmp_path: Path) -> None:
    result = apply_patch(NEW_PATCH, workdir=tmp_path)
    assert result.applied and not result.conflicts
    assert (tmp_path / "new.py").read_text() == "one\ntwo\n"
    assert result.files[0]["action"] == "create"


def test_create_conflict_when_target_exists(tmp_path: Path) -> None:
    _seed(tmp_path, "new.py", "already\n")
    result = apply_patch(NEW_PATCH, workdir=tmp_path)
    assert not result.applied
    assert "already exists" in result.conflicts[0]["detail"]
    assert (tmp_path / "new.py").read_text() == "already\n"


def test_apply_delete_file(tmp_path: Path) -> None:
    _seed(tmp_path, "old.py", "gone\n")
    result = apply_patch(DELETE_PATCH, workdir=tmp_path)
    assert result.applied and not result.conflicts
    assert not (tmp_path / "old.py").exists()


def test_apply_rename_file(tmp_path: Path) -> None:
    _seed(tmp_path, "before.py", "keep\nold_line\n")
    result = apply_patch(RENAME_PATCH, workdir=tmp_path)
    assert result.applied and not result.conflicts
    assert not (tmp_path / "before.py").exists()
    assert (tmp_path / "after.py").read_text() == "keep\nnew_line\n"


def test_conflict_leaves_no_partial_write(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "alpha\nomega\ngamma\n")  # hunk won't match
    result = apply_patch(MODIFY_PATCH, workdir=tmp_path)
    assert not result.applied
    assert "could not be located" in result.conflicts[0]["detail"]
    assert (tmp_path / "app.py").read_text() == "alpha\nomega\ngamma\n"


def test_dry_run_reports_but_never_writes(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "alpha\nbeta\ngamma\n")
    result = apply_patch(MODIFY_PATCH, workdir=tmp_path, dry_run=True)
    assert result.applied and result.dry_run
    assert (tmp_path / "app.py").read_text() == "alpha\nbeta\ngamma\n"


def test_second_apply_conflicts_idempotency(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "alpha\nbeta\ngamma\n")
    first = apply_patch(MODIFY_PATCH, workdir=tmp_path)
    assert first.applied
    second = apply_patch(MODIFY_PATCH, workdir=tmp_path)
    assert not second.applied
    assert (tmp_path / "app.py").read_text() == "alpha\nbravo\ngamma\n"


def test_hunk_header_with_section_heading(tmp_path: Path) -> None:
    _seed(tmp_path, "h.py", "x\nz\n")
    result = apply_patch(HEADING_PATCH, workdir=tmp_path)
    assert result.applied and not result.conflicts
    assert (tmp_path / "h.py").read_text() == "x\ny\nz\n"


def test_anchor_search_window_tolerance(tmp_path: Path) -> None:
    # Hunk anchor is 2 lines off but the content block is findable
    patch = """\
--- a/s.py
+++ b/s.py
@@ -3,3 +3,3 @@
 one
-two
+three
 four
"""
    _seed(tmp_path, "s.py", "one\ntwo\nfour\n")
    result = apply_patch(patch, workdir=tmp_path)
    assert result.applied and not result.conflicts
    assert (tmp_path / "s.py").read_text() == "one\nthree\nfour\n"


def test_multi_file_atomicity_per_file(tmp_path: Path) -> None:
    patch = """\
--- a/good.py
+++ b/good.py
@@ -1,1 +1,1 @@
-ok
+done
--- a/bad.py
+++ b/bad.py
@@ -1,1 +1,1 @@
-missing
+nope
"""
    good_path = tmp_path / "good.py"
    good_path.write_text("ok\n")
    (tmp_path / "bad.py").write_text("different\n")
    result = apply_patch(patch, workdir=tmp_path)
    assert not result.applied  # one file conflicted
    assert [c["path"] for c in result.conflicts] == ["bad.py"]
    assert good_path.read_text() == "done\n"  # good file still applied
    assert (tmp_path / "bad.py").read_text() == "different\n"


def test_path_escape_outside_workdir_rejected(tmp_path: Path) -> None:
    patch = """\
--- a/../escape.py
+++ b/../escape.py
@@ -1,1 +1,1 @@
-a
+b
"""
    result = apply_patch(patch, workdir=tmp_path)
    detail = " ".join(c["detail"] for c in result.conflicts)
    assert "escapes workdir" in detail


def test_wrapper_returns_display_text(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "alpha\nbeta\ngamma\n")
    out = _apply_patch(MODIFY_PATCH, workdir=str(tmp_path))
    assert out.startswith("Patched 1 file(s) (+1/-1)")


def test_malformed_patch_reported(tmp_path: Path) -> None:
    result = apply_patch("not a diff at all", workdir=tmp_path)
    assert not result.applied
    assert "no file diffs" in result.conflicts[0]["detail"]


# ---------------------------------------------------------------------------
# Codex envelope and bare @@ hunks (TB 4.0 harness-efficiency plan, item H10).
# Old code: every case below returned "patch contained no file diffs" (or, for
# a bare @@ hunk under ---/+++ headers, silently patched nothing).
# ---------------------------------------------------------------------------

CODEX_UPDATE = """\
*** Begin Patch
*** Update File: app.py
@@ def main():
     x = 1
-    y = 2
+    y = 3
     return x + y
*** End Patch
"""


def test_codex_update_patch_applies(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "import os\n\ndef main():\n    x = 1\n    y = 2\n    return x + y\n")
    result = apply_patch(CODEX_UPDATE, workdir=tmp_path)
    assert result.applied, result.conflicts
    assert (tmp_path / "app.py").read_text() == (
        "import os\n\ndef main():\n    x = 1\n    y = 3\n    return x + y\n"
    )
    assert result.files == [{"path": "app.py", "action": "modify", "added": 1, "removed": 1}]


def test_codex_context_header_picks_the_right_occurrence(tmp_path: Path) -> None:
    _seed(tmp_path, "m.py", "def a():\n    return 1\n\ndef b():\n    return 1\n")
    patch = """\
*** Begin Patch
*** Update File: m.py
@@ def b():
-    return 1
+    return 2
*** End Patch
"""
    assert apply_patch(patch, workdir=tmp_path).applied
    assert (tmp_path / "m.py").read_text() == "def a():\n    return 1\n\ndef b():\n    return 2\n"


def test_codex_add_delete_and_move(tmp_path: Path) -> None:
    _seed(tmp_path, "old.txt", "bye\n")
    _seed(tmp_path, "src.py", "keep\nold_line\n")
    patch = """\
*** Begin Patch
*** Add File: pkg/new.py
+print('hi')
+print('there')
*** Delete File: old.txt
*** Update File: src.py
*** Move to: dst.py
@@
 keep
-old_line
+new_line
*** End Patch
"""
    result = apply_patch(patch, workdir=tmp_path)
    assert result.applied, result.conflicts
    assert (tmp_path / "pkg/new.py").read_text() == "print('hi')\nprint('there')\n"
    assert not (tmp_path / "old.txt").exists()
    assert not (tmp_path / "src.py").exists()
    assert (tmp_path / "dst.py").read_text() == "keep\nnew_line\n"
    assert [f["action"] for f in result.files] == ["create", "delete", "rename"]


def test_codex_hunks_without_begin_line_and_multiple_hunks(tmp_path: Path) -> None:
    _seed(tmp_path, "c.py", "a\nb\nc\nd\ne\nf\n")
    patch = """\
*** Update File: c.py
@@
 a
-b
+B
@@
 e
-f
+F
"""
    assert apply_patch(patch, workdir=tmp_path).applied
    assert (tmp_path / "c.py").read_text() == "a\nB\nc\nd\ne\nF\n"


def test_codex_hunk_tolerates_trailing_whitespace_and_blank_lines(tmp_path: Path) -> None:
    _seed(tmp_path, "w.py", "def f():   \n\n    return 1\n")
    patch = (
        "*** Begin Patch\n*** Update File: w.py\n@@\n"
        " def f():\n"  # the file has trailing spaces here
        "\n"  # a blank context line without its leading space
        "-    return 1\n+    return 2\n"
        "\n"  # a stray trailing blank line
        "*** End Patch\n"
    )
    assert apply_patch(patch, workdir=tmp_path).applied
    assert (tmp_path / "w.py").read_text() == "def f():   \n\n    return 2\n"


def test_codex_hunk_that_does_not_match_is_a_conflict(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "something else\n")
    result = apply_patch(CODEX_UPDATE, workdir=tmp_path)
    assert not result.applied
    assert "could not be located" in result.conflicts[0]["detail"]
    assert (tmp_path / "app.py").read_text() == "something else\n"


def test_bare_hunk_header_in_a_unified_diff_is_located_by_content(tmp_path: Path) -> None:
    _seed(tmp_path, "u.py", "one\ntwo\nthree\n")
    patch = """\
--- a/u.py
+++ b/u.py
@@
 one
-two
+TWO
"""
    result = apply_patch(patch, workdir=tmp_path)
    assert result.applied, result.conflicts
    assert result.files[0]["added"] == 1
    assert (tmp_path / "u.py").read_text() == "one\nTWO\nthree\n"


def test_codex_patch_through_the_tool_wrapper(tmp_path: Path) -> None:
    _seed(tmp_path, "app.py", "def main():\n    x = 1\n    y = 2\n    return x + y\n")
    out = _apply_patch(CODEX_UPDATE, workdir=str(tmp_path))
    assert out.startswith("Patched 1 file(s) (+1/-1)")


def test_no_file_diffs_error_names_the_accepted_formats(tmp_path: Path) -> None:
    result = apply_patch("@@ -1 +1 @@\n-a\n+b\n", workdir=tmp_path)
    detail = result.conflicts[0]["detail"]
    assert detail.startswith("patch contained no file diffs")
    assert "*** Begin Patch" in detail
    assert "--- a/path" in detail


# ---------------------------------------------------------------------------
# Floating hunks must not land in the wrong place (review 2026-10-24). Before
# the fix a bare hunk took the first match, a whitespace-insensitive match kept
# the model's indentation on '+' lines, and both reported applied=True.
# ---------------------------------------------------------------------------

_TWIN = "def a():\n    x = 1\n    return x\n\ndef b():\n    x = 1\n    return x\n"


def test_bare_hunk_matching_two_places_is_a_conflict(tmp_path: Path) -> None:
    _seed(tmp_path, "twin.py", _TWIN)
    patch = "--- a/twin.py\n+++ b/twin.py\n@@\n     x = 1\n-    return x\n+    return x + 1\n"
    result = apply_patch(patch, workdir=tmp_path)
    assert not result.applied
    assert "matches 2 places (lines 2, 6)" in result.conflicts[0]["detail"]
    assert (tmp_path / "twin.py").read_text() == _TWIN


def test_context_header_still_picks_the_first_match_below_it(tmp_path: Path) -> None:
    _seed(tmp_path, "twin.py", _TWIN)
    patch = (
        "*** Begin Patch\n*** Update File: twin.py\n@@ def b():\n"
        "-    x = 1\n+    x = 2\n*** End Patch\n"
    )
    assert apply_patch(patch, workdir=tmp_path).applied
    expected = "def a():\n    x = 1\n    return x\n\ndef b():\n    x = 2\n    return x\n"
    assert (tmp_path / "twin.py").read_text() == expected


def test_whitespace_insensitive_match_reindents_the_added_lines(tmp_path: Path) -> None:
    """A 2-space hunk matched the 4-space file; '+' lines kept 2 spaces (IndentationError)."""
    _seed(tmp_path, "twin.py", _TWIN)
    patch = (
        "*** Begin Patch\n*** Update File: twin.py\n@@ def b():\n"
        "-  x = 1\n+  x = 2\n+  y = 3\n*** End Patch\n"
    )
    result = apply_patch(patch, workdir=tmp_path)
    assert result.applied, result.conflicts
    assert (
        (tmp_path / "twin.py")
        .read_text()
        .endswith("def b():\n    x = 2\n    y = 3\n    return x\n")
    )


def test_a_patch_that_changes_nothing_is_a_conflict(tmp_path: Path) -> None:
    _seed(tmp_path, "same.py", "a = 1\n")
    patch = "*** Begin Patch\n*** Update File: same.py\n@@\n-a = 1\n+a = 1\n*** End Patch\n"
    result = apply_patch(patch, workdir=tmp_path)
    assert not result.applied
    assert "changes nothing in same.py" in result.conflicts[0]["detail"]
