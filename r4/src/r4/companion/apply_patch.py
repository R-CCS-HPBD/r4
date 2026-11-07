"""Unified-diff `apply_patch` support for the companion.

Self-contained, stdlib-only parser and applier for the unified diff
format (RFC of ``diff -u`` / ``git diff``):

- create (``--- /dev/null``), delete (``+++ /dev/null``), modify, rename
- hunk anchors honoured first at the expected line, then a small offset
  search window (repo truth: models drift a few lines, whole-file rewrite
  costs tokens — a strict-but-tolerant matcher keeps the tool usable)
- **all-or-nothing per file**: a file is only written if every hunk
  applies; conflicts never leave partial writes on disk
- ``dry_run`` reports the same stat with ``applied=False``
- the Codex envelope (``*** Begin Patch`` / ``*** Update File:`` /
  ``*** Add File:`` / ``*** Delete File:`` / ``*** Move to:``, ``@@ <context>``
  hunks without line numbers) and bare ``@@`` hunks in unified diffs: such
  "floating" hunks are located by their context, exact first, then ignoring
  trailing and then surrounding whitespace (TB 4.0 harness-efficiency plan
  2026-10-24, item H10: 10 of 17 failed calls were Codex envelopes or bare
  ``@@`` hunks rejected with "patch contained no file diffs"). After an
  ``@@ <context>`` line the first match below it is used; without one the
  old block must match exactly one place, else the hunk is a conflict. When
  the match ignored indentation, ``+`` lines move by the same indentation
  difference. A modify patch whose hunks change nothing is a conflict
  (review 2026-10-24: a wrongly placed floating hunk must never report
  ``applied`` silently).

No ``patch(1)`` subprocess — deterministic behaviour for graders and
repeatable events (`file_patched`) for the UI layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Anchor search window (lines) around each hunk's expected position.
SEARCH_WINDOW = 10
# Max failing hunks to enumerate in conflict errors.
MAX_CONFLICTS_SHOWN = 3


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    # (kind, text) with kind in {" ", "-", "+"} and text without trailing \n
    lines: list[tuple[str, str]] = field(default_factory=list)
    # No line numbers (Codex ``@@ <context>`` or bare ``@@``): located by content.
    floating: bool = False
    # Codex ``@@ <context>`` text: the hunk is searched for after this line.
    context: str = ""


@dataclass
class FilePatch:
    action: str  # "create" | "delete" | "modify" | "rename"
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class PatchApplyResult:
    applied: bool
    dry_run: bool
    files: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_display(self) -> str:
        if self.conflicts:
            head = f"Conflicts in {len(self.conflicts)} file(s); affected files NOT written."
            shown = self.conflicts[:MAX_CONFLICTS_SHOWN]
            lines = [f"- {c['path']}: {c['detail']}" for c in shown]
            if len(self.conflicts) > len(shown):
                lines.append(f"... and {len(self.conflicts) - len(shown)} more")
            return "\n".join([head, *lines])
        total_add = sum(f["added"] for f in self.files)
        total_del = sum(f["removed"] for f in self.files)
        mode = " [dry-run]" if self.dry_run else ""
        parts = [f"Patched {len(self.files)} file(s) (+{total_add}/-{total_del}){mode}"]
        for f in self.files:
            parts.append(f"- {f['action']} {f['path']} (+{f['added']}/-{f['removed']})")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _strip_prefix(path: str) -> str:
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _parse_hunk_header(line: str) -> tuple[int, int, int, int] | None:
    """Parse ``@@ -a[,b] +c[,d] @@ [heading]`` into (a, b, c, d) ints."""
    try:
        body = line.split("@@", 2)[1].strip()
        parts = body.split(" ")
        old_body, new_body = parts[0], parts[1]
        old_nums = old_body[1:].split(",")
        new_nums = new_body[1:].split(",")
        return (
            int(old_nums[0]),
            int(old_nums[1]) if len(old_nums) > 1 else 1,
            int(new_nums[0]),
            int(new_nums[1]) if len(new_nums) > 1 else 1,
        )
    except (IndexError, ValueError):
        return None


def _finish_hunk(fp: FilePatch, hunk: Hunk | None) -> None:
    if hunk is not None and hunk.lines:
        fp.hunks.append(hunk)


def _finish_file(files: list[FilePatch], fp: FilePatch | None, hunk: Hunk | None) -> None:
    if fp is None:
        return
    _finish_hunk(fp, hunk)
    if fp.new_path is None and fp.action != "delete":
        fp.new_path = fp.old_path
    files.append(fp)


def _start_file(raw: str) -> FilePatch:
    old_path = _strip_prefix(raw[4:].split("\t", 1)[0])
    if old_path == "/dev/null":
        return FilePatch("create", None, None)
    return FilePatch("modify", old_path, None)


def _finish_plus_plus(fp: FilePatch, raw: str) -> None:
    new_path = _strip_prefix(raw[4:].split("\t", 1)[0])
    if new_path == "/dev/null":
        fp.action = "delete"
        return
    fp.new_path = new_path
    if fp.action == "create":
        return
    fp.action = "rename" if fp.old_path != new_path else "modify"


def _floating_hunk(header: str) -> Hunk:
    """A hunk without line numbers; *header* is the text after ``@@``."""
    context = header.strip()
    if context.endswith("@@"):
        context = context[:-2].strip()
    return Hunk(0, 0, 0, 0, [], floating=True, context=context)


def _next_hunk(fp: FilePatch, prev: Hunk | None, raw: str) -> Hunk | None:
    _finish_hunk(fp, prev)
    parsed = _parse_hunk_header(raw)
    return Hunk(*parsed, []) if parsed else _floating_hunk(raw[2:])


def _scan_line(fp: FilePatch | None, hunk: Hunk | None, raw: str) -> None:
    if fp is None or hunk is None:
        return
    if raw.startswith((" ", "+", "-")):
        hunk.lines.append((raw[0], raw[1:]))


def parse_patch(text: str) -> list[FilePatch]:
    """Parse a unified diff, or a Codex ``*** Begin Patch`` envelope, into FilePatch objects."""
    if _is_codex_patch(text):
        return parse_codex_patch(text)
    files: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    for raw in text.splitlines():
        if raw.startswith("--- ") and not raw.startswith("----"):
            _finish_file(files, current, hunk)
            current, hunk = _start_file(raw), None
            continue
        if raw.startswith("+++ ") and current is not None:
            _finish_plus_plus(current, raw)
        elif raw.startswith("@@") and current is not None:
            hunk = _next_hunk(current, hunk, raw)
        else:
            _scan_line(current, hunk, raw)
    _finish_file(files, current, hunk)
    return files


# ---------------------------------------------------------------------------
# Codex envelope (``*** Begin Patch`` … ``*** End Patch``)
# ---------------------------------------------------------------------------

_CODEX_FILE_OPS = ("*** Update File: ", "*** Add File: ", "*** Delete File: ")
_CODEX_MARKERS = ("*** Begin Patch", "*** End Patch", "*** End of File")
_CODEX_MOVE = "*** Move to: "

ACCEPTED_FORMATS = (
    "accepted formats: a unified diff (`--- a/path`, `+++ b/path`, `@@ -l,n +l,n @@` "
    "hunks; `--- /dev/null` creates, `+++ /dev/null` deletes) or a Codex envelope "
    "(`*** Begin Patch`, then `*** Update File: path` with `@@` context hunks, "
    "`*** Add File: path` with `+` lines, or `*** Delete File: path`, then `*** End Patch`)"
)


def _is_codex_patch(text: str) -> bool:
    for raw in text.splitlines():
        if raw.startswith(("*** Begin Patch", *_CODEX_FILE_OPS)):
            return True
    return False


def _codex_file_op(raw: str) -> FilePatch | None:
    """The FilePatch a ``*** Update/Add/Delete File:`` line opens, else None."""
    for prefix, action in zip(_CODEX_FILE_OPS, ("modify", "create", "delete")):
        if raw.startswith(prefix):
            path = raw[len(prefix) :].strip()
            if action == "create":
                return FilePatch(action, None, path)
            return FilePatch(action, path, path if action == "modify" else None)
    return None


def _codex_move(fp: FilePatch, raw: str) -> None:
    fp.new_path = raw[len(_CODEX_MOVE) :].strip()
    fp.action = "rename" if fp.new_path != fp.old_path else "modify"


def _codex_line(fp: FilePatch, hunk: Hunk | None, raw: str) -> Hunk | None:
    """Add one body line to the current hunk; return the hunk that is now current."""
    if raw.startswith("@@"):
        _finish_codex_hunk(fp, hunk)
        return _floating_hunk(raw[2:])
    if fp.action == "create":
        hunk = hunk or Hunk(0, 0, 1, 0, [])
        if raw.startswith("+"):
            hunk.lines.append(("+", raw[1:]))
        return hunk
    hunk = hunk or _floating_hunk("")
    if raw.startswith((" ", "+", "-")):
        hunk.lines.append((raw[0], raw[1:]))
    elif not raw:  # a blank context line whose leading space was stripped
        hunk.lines.append((" ", ""))
    return hunk


def _finish_codex_hunk(fp: FilePatch, hunk: Hunk | None) -> None:
    """Drop trailing blank context (often a stray empty line) before keeping the hunk."""
    if hunk is not None and hunk.floating:
        while hunk.lines and hunk.lines[-1] == (" ", ""):
            hunk.lines.pop()
    _finish_hunk(fp, hunk)


def _finish_codex_file(files: list[FilePatch], fp: FilePatch | None, hunk: Hunk | None) -> None:
    if fp is not None:
        _finish_codex_hunk(fp, hunk)
    _finish_file(files, fp, None)


def parse_codex_patch(text: str) -> list[FilePatch]:
    """Parse a Codex ``*** Begin Patch`` envelope into FilePatch objects."""
    files: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    for raw in text.splitlines():
        opened = _codex_file_op(raw)
        if opened is not None:
            _finish_codex_file(files, current, hunk)
            current, hunk = opened, None
        elif raw.startswith(_CODEX_MOVE) and current is not None:
            _codex_move(current, raw)
        elif current is not None and not raw.startswith(_CODEX_MARKERS):
            hunk = _codex_line(current, hunk, raw)
    _finish_codex_file(files, current, hunk)
    return files


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> tuple[list[str], bool]:
    """Split ``path`` into lines without newlines; report trailing-newline state."""
    text = path.read_text(errors="replace")
    if not text:
        return [], True
    lines = text.splitlines()
    ends_newline = text.endswith("\n")
    # splitlines() on "a\n\n" gives ["a", ""] — matches diff semantics.
    return lines, ends_newline


def _matches_at(lines: list[str], old_block: list[str], start: int) -> bool:
    if start < 0 or start + len(old_block) > len(lines):
        return False
    return lines[start : start + len(old_block)] == old_block


def _same(a: str, b: str) -> bool:
    return a == b


def _same_rstrip(a: str, b: str) -> bool:
    return a.rstrip() == b.rstrip()


def _same_strip(a: str, b: str) -> bool:
    return a.strip() == b.strip()


_FLOATING_MATCHERS = (_same, _same_rstrip, _same_strip)


def _block_starts(lines: list[str], block: list[str], start_at: int) -> list[int]:
    """Every index >= *start_at* where *block* matches, by the strictest
    matcher that finds any."""
    last = len(lines) - len(block)
    for same in _FLOATING_MATCHERS:
        starts = [
            start
            for start in range(max(0, start_at), last + 1)
            if all(same(lines[start + k], text) for k, text in enumerate(block))
        ]
        if starts:
            return starts
    return []


def _seek(lines: list[str], block: list[str], start_at: int) -> int | None:
    """First index >= *start_at* where *block* matches, strict matcher first."""
    starts = _block_starts(lines, block, start_at)
    return starts[0] if starts else None


def _seek_unique(lines: list[str], block: list[str], start_at: int, hunk: "Hunk") -> int | None:
    """The one index >= *start_at* where *block* matches; a conflict when several do."""
    starts = _block_starts(lines, block, start_at)
    if len(starts) > 1:
        lines_at = ", ".join(str(start + 1) for start in starts[:5])
        raise ValueError(
            f"{_hunk_label(hunk, located=True)} matches {len(starts)} places "
            f"(lines {lines_at}); add an `@@ <line above it>` context or more context lines"
        )
    return starts[0] if starts else None


def _context_end(lines: list[str], context: str, from_pos: int) -> int | None:
    """Index after the hunk's ``@@ <context>`` line; None without one or when absent."""
    if not context:
        return None
    for start_at in (from_pos, 0):
        found = _seek(lines, [context], start_at)
        if found is not None:
            return found + 1
    return None


def _find_floating(lines: list[str], hunk: Hunk, from_pos: int) -> int | None:
    """Locate a hunk without line numbers by its context (pure adds: append)."""
    old_block = [t for kind, t in hunk.lines if kind in (" ", "-")]
    anchored = _context_end(lines, hunk.context, from_pos)
    if not old_block:
        if anchored is not None:
            return anchored
        return from_pos if hunk.context else len(lines)
    if anchored is not None:
        found = _seek(lines, old_block, anchored)
        if found is not None:
            return found
    found = _seek_unique(lines, old_block, from_pos, hunk)
    return found if found is not None else _seek_unique(lines, old_block, 0, hunk)


def _find_anchor(lines: list[str], hunk: Hunk, from_pos: int) -> int | None:
    """Find where a hunk's old-side block starts in ``lines`` (0-based)."""
    if hunk.floating:
        return _find_floating(lines, hunk, from_pos)
    old_block = [t for kind, t in hunk.lines if kind in (" ", "-")]
    if not old_block:
        # pure-add hunk: anchor on position
        return max(0, min(hunk.old_start - 1 + from_pos, len(lines)))
    expected = hunk.old_start - 1 + from_pos
    for delta in range(0, SEARCH_WINDOW + 1):
        for start in (expected - delta, expected + delta):
            if _matches_at(lines, old_block, start):
                return start
    return None


def _apply_one_hunk(lines: list[str], hunk: Hunk, from_pos: int) -> tuple[list[str], int]:
    """Apply one hunk at its located position; return (lines, position_after)."""
    old_count = sum(1 for kind, _ in hunk.lines if kind in (" ", "-"))
    anchor = _find_anchor(lines, hunk, from_pos)
    if anchor is None:
        raise ValueError(f"{_hunk_label(hunk)} could not be located")
    # A pure add (empty old block) inserts at the anchor, which for a numbered
    # hunk is its clamped expected position.
    new_block = _new_block_at(lines, hunk, anchor)
    new_lines = lines[:anchor] + new_block + lines[anchor + old_count :]
    return new_lines, anchor + len(new_block)


def _indent_of(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def _indent_shift(lines: list[str], hunk: Hunk, anchor: int) -> tuple[str, str]:
    """(the hunk's indentation, the file's) at the first non-blank old line.

    Equal unless a floating hunk matched ignoring surrounding whitespace."""
    pos = anchor
    for kind, text in hunk.lines:
        if kind == "+":
            continue
        if text.strip():
            return _indent_of(text), _indent_of(lines[pos])
        pos += 1
    return "", ""


def _reindented(text: str, shift: tuple[str, str]) -> str:
    """*text* with the hunk's indentation prefix replaced by the file's."""
    hunk_indent, file_indent = shift
    if hunk_indent == file_indent or not text.startswith(hunk_indent):
        return text
    return file_indent + text[len(hunk_indent) :]


def _new_block_at(lines: list[str], hunk: Hunk, anchor: int) -> list[str]:
    """The hunk's new side; context lines keep the file's own text (a floating
    hunk may have matched them ignoring whitespace) and ``+`` lines move by the
    same indentation difference."""
    shift = _indent_shift(lines, hunk, anchor)
    block: list[str] = []
    pos = anchor
    for kind, text in hunk.lines:
        if kind == "+":
            block.append(_reindented(text, shift))
            continue
        if kind == " ":
            block.append(lines[pos])
        pos += 1
    return block


def _hunk_label(hunk: Hunk, *, located: bool = False) -> str:
    if not hunk.floating:
        return f"hunk @@ -{hunk.old_start},{hunk.old_count}"
    first = next((t for kind, t in hunk.lines if kind in (" ", "-")), "")
    where = hunk.context or first
    if located:
        return f"hunk @@ {where[:60]!r}"
    return f"hunk @@ {where[:60]!r} (its context and '-' lines are not in the file)"


def _create_content(fp: FilePatch, workdir: Path) -> tuple[list[str], bool]:
    if fp.new_path and (workdir / fp.new_path).exists():
        raise ValueError(f"create target already exists: {fp.new_path}")
    new_lines = [t for kind, t in fp.hunks[0].lines if kind in (" ", "+")] if fp.hunks else []
    return new_lines, True  # created files end with newline by convention


def _target_content(fp: FilePatch, workdir: Path) -> tuple[list[str], bool]:
    """Compute post-image lines for a FilePatch. Raises ValueError on conflicts."""
    if fp.action == "create":
        return _create_content(fp, workdir)
    src = workdir / (fp.old_path or "")
    if not src.is_file():
        raise FileNotFoundError(fp.old_path or "")
    original, ends_newline = _read_lines(src)
    lines, from_pos = original, 0
    for hunk in fp.hunks:
        lines, from_pos = _apply_one_hunk(lines, hunk, from_pos)
    if fp.action == "modify" and fp.hunks and lines == original:
        raise ValueError(f"the patch changes nothing in {fp.old_path}")
    return lines, ends_newline


def _resolve(workdir: Path, rel: str) -> Path:
    root = workdir.resolve()
    target = (workdir / rel).resolve()
    # Containment by path components: a string prefix let ``<root>10/x``
    # pass for ``<root>1`` (sibling plan workspaces, ADR-092 slice 5).
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes workdir: {rel}")
    return target


def _apply_file(fp: FilePatch, workdir: Path, dry_run: bool) -> tuple[int, int]:
    """Apply one FilePatch; returns (added, removed) on success. Raises on conflict."""
    added = sum(1 for h in fp.hunks for k, _ in h.lines if k == "+")
    removed = sum(1 for h in fp.hunks for k, _ in h.lines if k == "-")
    # Guardrail before ANY filesystem access: reject paths escaping the root.
    for rel in (fp.old_path, fp.new_path):
        if rel:
            _resolve(workdir, rel)
    if dry_run:
        # still validate hunk location to keep dry-run honest
        _target_content(fp, workdir)
        return added, removed
    if fp.action == "delete":
        _target_content(fp, workdir)  # validate old content matches
        _resolve(workdir, fp.old_path or "").unlink()
        return added, removed
    lines, ends_newline = _target_content(fp, workdir)
    text = "\n".join(lines) + ("\n" if lines and ends_newline else "")
    out = _resolve(workdir, fp.new_path or fp.old_path or "")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    if fp.action == "rename":
        _resolve(workdir, fp.old_path or "").unlink()
    return added, removed


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def apply_patch(
    patch_text: str,
    workdir: str | Path | None = None,
    dry_run: bool = False,
) -> PatchApplyResult:
    """Apply a unified diff under ``workdir`` with per-file atomicity."""
    root = Path(workdir or ".").resolve()
    result = PatchApplyResult(applied=True, dry_run=dry_run)
    try:
        file_patches = parse_patch(patch_text)
    except Exception as exc:  # malformed diff input is a caller error
        return PatchApplyResult(
            applied=False,
            dry_run=dry_run,
            conflicts=[{"path": "-", "detail": f"malformed patch: {exc}"}],
        )
    if not file_patches:
        return PatchApplyResult(
            applied=False,
            dry_run=dry_run,
            conflicts=[
                {"path": "-", "detail": f"patch contained no file diffs; {ACCEPTED_FORMATS}"}
            ],
        )
    for fp in file_patches:
        path_label = fp.new_path or fp.old_path or "-"
        try:
            added, removed = _apply_file(fp, root, dry_run)
        except FileNotFoundError as exc:
            result.applied = False
            result.conflicts.append({"path": path_label, "detail": f"not found: {exc}"})
            continue
        except (ValueError, OSError) as exc:
            result.applied = False
            result.conflicts.append({"path": path_label, "detail": str(exc)})
            continue
        result.files.append(
            {"path": path_label, "action": fp.action, "added": added, "removed": removed}
        )
    return result
