"""Markdown link checker: relative links and #anchors in tracked .md files.

Hardening plan H-11.8 (2026-10-18). Standard library only, no network.

For every tracked Markdown file (``git ls-files '*.md'``) it reads inline links
``[text](target)``, images, reference definitions ``[id]: target`` and HTML
``href``/``src`` attributes outside code fences and inline code, and checks:

- a relative path (or a ``/``-rooted one, which GitHub resolves from the repo
  root) names a tracked file or a directory holding tracked files;
- a ``#fragment`` on a Markdown target (or a bare ``#fragment``) matches a
  heading slug of that file under GitHub's rules (lower case, punctuation
  dropped, each space a hyphen, ``-1``/``-2`` suffixes for repeats) or an
  explicit HTML ``id``/``name`` anchor.

Skipped: ``http(s)``, ``mailto`` and other URL schemes, targets inside a git
submodule (CI checks the parent tree without it, so local and CI agree), and
line anchors (``#L10``). Existence is judged against tracked files, never the
working tree, so an untracked local file cannot hide a broken link.

Usage::

    python tools/md_link_check.py                       # all tracked .md; exit 1 on any broken link
    python tools/md_link_check.py --baseline tools/md_link_check_baseline.txt
                                                        # exit 1 only on links not in the baseline
    python tools/md_link_check.py --write-baseline tools/md_link_check_baseline.txt
    python tools/md_link_check.py docs/README.md AGENTS.md   # just these files

A baseline line is ``<file>\\t<target>`` (no line number, so edits elsewhere
in a file do not shift it). The baseline only shrinks: fix a link, then drop
its line; entries that no longer match are reported as stale.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_INLINE_LINK_RE = re.compile(
    r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*"
    r"(<[^>\n]+>|[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
_REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)")
_HTML_ATTR_RE = re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HTML_ANCHOR_RE = re.compile(r"""<[a-z][^>]*\b(?:id|name)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_LINE_ANCHOR_RE = re.compile(r"^L\d+(?:C\d+)?(?:-L\d+(?:C\d+)?)?$")
_SLUG_DROP_RE = re.compile(r"[^\w\- ]")
_MD_LINK_TEXT_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class BrokenLink:
    file: str
    line: int
    target: str
    reason: str

    @property
    def key(self) -> str:
        return f"{self.file}\t{self.target}"

    def render(self) -> str:
        return f"{self.file}:{self.line}: {self.target} ({self.reason})"


# --- reading -----------------------------------------------------------------


def _git_lines(root: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def submodule_paths(root: Path) -> list[str]:
    """Paths of git submodules (gitlinks), from .gitmodules."""
    gitmodules = root / ".gitmodules"
    if not gitmodules.is_file():
        return []
    text = gitmodules.read_text(encoding="utf-8")
    return re.findall(r"^\s*path\s*=\s*(\S+)\s*$", text, re.MULTILINE)


def unfenced_lines(text: str) -> list[tuple[int, str]]:
    """(line number, text) of every line outside fenced code blocks."""
    result: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        match = _FENCE_RE.match(line)
        if match and (fence is None or match.group(1) == fence):
            fence = None if fence else match.group(1)
            continue
        if fence is None:
            result.append((number, line))
    return result


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code with inline code removed (links there are examples)."""
    return [(number, _INLINE_CODE_RE.sub("", line)) for number, line in unfenced_lines(text)]


def link_targets(line: str) -> list[str]:
    """Every link target on one prose line (angle brackets removed)."""
    found = [m.group(1) for m in _INLINE_LINK_RE.finditer(line)]
    found += [m.group(1) for m in _REF_DEF_RE.finditer(line)]
    found += [m.group(1) for m in _HTML_ATTR_RE.finditer(line)]
    return [t[1:-1] if t.startswith("<") and t.endswith(">") else t for t in found]


# --- anchors -------------------------------------------------------------------


def github_slug(heading: str) -> str:
    """GitHub's heading id (github-slugger): rendered text, lower case, no punctuation."""
    text = _MD_LINK_TEXT_RE.sub(r"\1", heading)
    text = _HTML_TAG_RE.sub("", text).replace("*", "").replace("`", "")
    return _SLUG_DROP_RE.sub("", text.lower()).replace(" ", "-")


def anchors_of(text: str) -> set[str]:
    """Every fragment a Markdown file answers to (headings plus HTML ids)."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for _, line in unfenced_lines(text):
        anchors.update(a.lower() for a in _HTML_ANCHOR_RE.findall(line))
        match = _HEADING_RE.match(line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


# --- checking ------------------------------------------------------------------


class Repo:
    """The files links may name, their directories and lazily read anchors.

    ``files`` are the tracked files plus untracked, not-ignored ones, minus
    any deleted from the working tree: the tree the next commit would hold if
    everything were added. In CI that is exactly the tracked tree; locally an
    uncommitted rename does not read as a broken link. ``sources`` (the files
    checked) are tracked Markdown files only, so untracked notes never fail it.
    """

    def __init__(
        self, root: Path, files: list[str], submodules: list[str], sources: list[str] | None = None
    ) -> None:
        self.root = root
        self.files = set(files)
        self.dirs = {str(p) for f in files for p in PurePosixPath(f).parents}
        self.submodules = tuple(s.rstrip("/") for s in submodules)
        self.sources = sorted(sources if sources is not None else self.files)
        self._anchors: dict[str, set[str]] = {}

    @classmethod
    def from_git(cls, root: Path) -> Repo:
        tracked = _git_lines(root, "ls-files")
        untracked = _git_lines(root, "ls-files", "--others", "--exclude-standard")
        present = [f for f in tracked + untracked if (root / f).exists()]
        sources = [f for f in tracked if f.endswith(".md") and (root / f).is_file()]
        return cls(root, present, submodule_paths(root), sources)

    def in_submodule(self, path: str) -> bool:
        return any(path == s or path.startswith(s + "/") for s in self.submodules)

    def anchors(self, path: str) -> set[str]:
        if path not in self._anchors:
            text = (self.root / path).read_text(encoding="utf-8", errors="replace")
            self._anchors[path] = anchors_of(text)
        return self._anchors[path]


# Files under this prefix are copied to the repository root of the public
# export (scripts/public_export/export.py apply_overlay), so their links are
# written for the root and resolved from there (2026-11-07).
EXPORT_OVERLAY = "scripts/public_export/public-overlay/"


def link_base(source: str) -> PurePosixPath:
    """Directory a relative link in ``source`` resolves from."""
    if source.startswith(EXPORT_OVERLAY):
        source = source[len(EXPORT_OVERLAY) :]
    return PurePosixPath(source).parent


def resolve(source: str, path_part: str) -> str | None:
    """Repo-relative POSIX path of a link from ``source``; None when it leaves the repo."""
    path_part = unquote(path_part)
    base = PurePosixPath() if path_part.startswith("/") else link_base(source)
    parts: list[str] = []
    for piece in (base / path_part.lstrip("/")).parts:
        if piece == "..":
            if not parts:
                return None
            parts.pop()
        elif piece != ".":
            parts.append(piece)
    return "/".join(parts) or "."


def _skippable(target: str) -> bool:
    return not target or bool(_SCHEME_RE.match(target)) or target.startswith("//")


def check_target(repo: Repo, source: str, target: str) -> str | None:
    """The reason ``target`` (linked from ``source``) is broken, or None."""
    if _skippable(target):
        return None
    path_part, _, fragment = target.partition("#")
    path_part = path_part.split("?", 1)[0]
    resolved = resolve(source, path_part) if path_part else source
    if resolved is None:
        return "points outside the repository"
    if repo.in_submodule(resolved):
        return None
    if resolved not in repo.files and resolved not in repo.dirs:
        return "no such tracked file or directory"
    return _check_fragment(repo, resolved, unquote(fragment))


def _check_fragment(repo: Repo, resolved: str, fragment: str) -> str | None:
    if not fragment or not resolved.endswith(".md") or resolved not in repo.files:
        return None
    if _LINE_ANCHOR_RE.match(fragment):
        return None
    if fragment.lower() in repo.anchors(resolved):
        return None
    return f"no heading or anchor #{fragment} in {resolved}"


def check_file(repo: Repo, source: str) -> list[BrokenLink]:
    text = (repo.root / source).read_text(encoding="utf-8", errors="replace")
    broken: list[BrokenLink] = []
    for number, line in prose_lines(text):
        for target in link_targets(line):
            reason = check_target(repo, source, target)
            if reason:
                broken.append(BrokenLink(source, number, target, reason))
    return broken


def check_files(repo: Repo, sources: list[str]) -> list[BrokenLink]:
    return [link for source in sources for link in check_file(repo, source)]


# --- baseline -------------------------------------------------------------------


def read_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {ln for ln in lines if ln.strip() and not ln.startswith("#")}


def write_baseline(path: Path, broken: list[BrokenLink]) -> None:
    header = (
        "# Known broken Markdown links (tools/md_link_check.py --write-baseline).\n"
        "# <file>\\t<target>; this list only shrinks: fix a link, then delete its line.\n"
    )
    keys = sorted({link.key for link in broken})
    path.write_text(header + "".join(f"{k}\n" for k in keys), encoding="utf-8")


def _report(broken: list[BrokenLink], baseline: set[str] | None) -> int:
    new = [b for b in broken if baseline is None or b.key not in baseline]
    for link in new:
        print(link.render())
    if baseline is not None:
        stale = sorted(baseline - {b.key for b in broken})
        for key in stale:
            print(f"stale baseline entry (fixed? delete the line): {key}")
        print(
            f"md_link_check: {len(broken)} broken, {len(broken) - len(new)} baselined, {len(new)} new"
        )
    else:
        print(f"md_link_check: {len(broken)} broken link(s)")
    return 1 if new else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("paths", nargs="*", help="Markdown files to check (default: all tracked)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path, help="fail only on links not listed here")
    parser.add_argument("--write-baseline", type=Path, help="record today's broken links")
    args = parser.parse_args(argv)

    repo = Repo.from_git(args.root)
    sources = args.paths or [f for f in repo.sources if f.endswith(".md")]
    broken = check_files(repo, sources)
    if args.write_baseline:
        write_baseline(args.write_baseline, broken)
        print(
            f"md_link_check: wrote {len({b.key for b in broken})} entries to {args.write_baseline}"
        )
        return 0
    baseline = read_baseline(args.baseline) if args.baseline else None
    return _report(broken, baseline)


if __name__ == "__main__":
    raise SystemExit(main())
