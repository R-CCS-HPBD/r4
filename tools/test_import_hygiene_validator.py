"""Static test-import-hygiene validator (structure-hardening plan step 1.5).

Phase 1 (ADR-053) deleted the test-suite ``sys.path`` splices and
``importlib.util.spec_from_file_location`` harnesses that predated the
jobs↔api cycle fix. This validator keeps them deleted: any new path splice
or spec-load in a test tree fails the gate unless the file is named in
``tools/test_import_hygiene_allowlist.txt``.

Allowlist semantics (intentional, reviewed exceptions only):

* isolated foundation-module pins (e.g. ``provision_roster`` stdlib-only
  assertions) — a plain import would let a heavyweight dep leak pass
  silently;
* genuinely non-packaged script targets (hyphenated dirs, sibling-repo
  ``gateway/sidecar``, ``umrs/tools`` shadowing the repo-root ``tools``);
* cross-repo test-helper splices that have no package route today.

Matching is AST-based so prose mentions in comments/docstrings never fire.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCAN_ROOTS: tuple[str, ...] = (
    "tests",
    "umrs/tests",
    "r4/tests",
    "mcp-servers/tests",
)

ALLOWLIST_PATH = "tools/test_import_hygiene_allowlist.txt"

_PATH_SPLICE_CALLS = frozenset({"insert", "append"})
_SPEC_CALLS = frozenset({"module_from_spec", "spec_from_file_location"})


@dataclass(frozen=True)
class HygieneFinding:
    """One forbidden import-machinery call in a test file."""

    path: str
    line: int
    call: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.call} — {self.message}"


def _load_allowlist(repo_root: Path) -> frozenset[str]:
    allowlist_file = repo_root / ALLOWLIST_PATH
    if not allowlist_file.exists():
        return frozenset()
    entries: set[str] = set()
    for raw in allowlist_file.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return frozenset(entries)


def _call_name(node: ast.Call) -> str | None:
    """Return the dotted call name for attribute/simple calls, else None."""
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        parts.reverse()
        return ".".join(parts)
    return None


def _is_path_splice(call_name: str) -> bool:
    parts = call_name.split(".")
    return (
        len(parts) == 3
        and parts[0] == "sys"
        and parts[1] == "path"
        and parts[2] in _PATH_SPLICE_CALLS
    )


def _is_spec_load(call_name: str) -> bool:
    return call_name.endswith(tuple("." + c for c in _SPEC_CALLS)) or call_name in _SPEC_CALLS


def _scan_file(path: Path, rel: str) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, ValueError):
        # Un-parseable fixture trees (e.g. legacy vendor dirs) are outside
        # hygiene scope; pytest collection handles them separately.
        return findings
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None:
            continue
        if _is_path_splice(name):
            findings.append(
                HygieneFinding(
                    rel,
                    node.lineno,
                    name,
                    "sys.path splice in test tree (ADR-053); use plain imports "
                    "or earn an allowlist entry with a recorded reason",
                )
            )
        elif _is_spec_load(name):
            findings.append(
                HygieneFinding(
                    rel,
                    node.lineno,
                    name,
                    "spec-load harness in test tree (ADR-053); use plain imports "
                    "or earn an allowlist entry with a recorded reason",
                )
            )
    return findings


def scan_repo(repo_root: Path) -> list[HygieneFinding]:
    allowlist = _load_allowlist(repo_root)
    findings: list[HygieneFinding] = []
    for root in SCAN_ROOTS:
        scan_dir = repo_root / root
        if not scan_dir.is_dir():
            continue
        for path in sorted(scan_dir.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if rel in allowlist or "__pycache__" in path.parts:
                continue
            if any(part.startswith(".") for part in path.parts):
                continue  # editor locks / hidden dirs
            if not path.is_file():
                continue  # broken symlink
            findings.extend(_scan_file(path, rel))
    return findings


def main(argv: list[str] | None = None) -> int:
    args = set(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parent.parent
    findings = scan_repo(repo_root)
    if "--json" in args:
        print(
            json.dumps(
                [{"path": f.path, "line": f.line, "call": f.call} for f in findings],
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(finding.format())
        if not findings:
            print("Test-import hygiene: no forbidden path splices or spec-loads")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
