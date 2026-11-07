"""Ratchet for mypy errors (hardening plan H-0.5, 2026-10-18).

Until 2026-10-18 CI ran ``mypy … || true``: the required Type Check job
could not fail. This tool keeps today's errors as a baseline and fails only
on errors that are not in it, so the job can be a real gate without a flood.

An error's key is ``file:code:message`` with the line number dropped (an
edit above an error does not make it new) and ``line N`` inside a message
normalised the same way. Keys form a multiset: a second identical error in
the same file is a new error.

CI shape (``.github/workflows/ci.yaml`` job ``typecheck``)::

    (cd r4 && python -m mypy src/r4 --ignore-missing-imports \\
        --no-error-summary) > out.txt; status=$?
    python tools/mypy_baseline.py check --baseline tools/mypy_baseline_r4.txt \\
        --prefix r4/ --status "$status" out.txt

To shrink the baseline after fixing errors, run mypy the same way and::

    python tools/mypy_baseline.py write --baseline tools/mypy_baseline_r4.txt \\
        --prefix r4/ out.txt

"The same way" means a fresh Python 3.11 venv whose every ``pip install``
carries ``-c tools/mypy_constraints.txt`` (the CI job's install step): the
message text depends on mypy, the stubs and the typed dependencies, so an
unconstrained venv produces a different error set.

``write`` only ever removes entries (ADR-052 ratchet). ``--allow-new`` is
for a deliberate bump of tools/mypy_constraints.txt (mypy, a stub or a
dependency): the bump, the rerun and ``write --allow-new`` go in one commit
whose message names the bump.

Exit codes: 0 ok, 1 new errors or write refused, 2 mypy did not finish
(crash, blocking error, usage) or its output could not be read.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from collections import Counter
from pathlib import Path

_ERROR_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+)(?::\d+)?: error: "
    r"(?P<msg>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$"
)
_LINE_REF_RE = re.compile(r"\bline \d+\b")
_HEADER = (
    "# mypy baseline (tools/mypy_baseline.py; hardening plan H-0.5).\n"
    "# One known error per line as file:code:message, line numbers dropped.\n"
    "# Shrink only: regenerate with `write` after fixing errors.\n"
)


def error_key(line: str, prefix: str = "") -> str | None:
    """The baseline key of one mypy output line, or None for non-errors."""
    match = _ERROR_RE.match(line.rstrip("\n"))
    if match is None:
        return None
    path = posixpath.normpath(prefix + match["file"]) if prefix else match["file"]
    message = _LINE_REF_RE.sub("line N", match["msg"].strip())
    return f"{path}:{match['code'] or 'misc'}:{message}"


def parse_output(text: str, prefix: str = "") -> Counter[str]:
    """Multiset of error keys in mypy's output."""
    keys = (error_key(line, prefix) for line in text.splitlines())
    return Counter(key for key in keys if key is not None)


def load_baseline(path: Path) -> Counter[str]:
    """Multiset of keys in a baseline file (missing file = empty baseline)."""
    if not path.exists():
        return Counter()
    lines = path.read_text(encoding="utf-8").splitlines()
    return Counter(ln for ln in lines if ln.strip() and not ln.startswith("#"))


def save_baseline(path: Path, keys: Counter[str]) -> None:
    """Write keys sorted, one line per occurrence, under the header."""
    body = "".join(f"{key}\n" for key in sorted(keys.elements()))
    path.write_text(_HEADER + body, encoding="utf-8")


def _report(title: str, keys: Counter[str], limit: int = 200) -> None:
    items = sorted(keys.elements())
    print(f"{title}: {len(items)}")
    for key in items[:limit]:
        print(f"  {key}")
    if len(items) > limit:
        print(f"  … {len(items) - limit} more")


def _read_current(args: argparse.Namespace) -> Counter[str] | None:
    """Parsed errors, or None when mypy did not finish or said nothing usable."""
    text = Path(args.output).read_text(encoding="utf-8", errors="replace")
    current = parse_output(text, args.prefix)
    if args.status is not None and args.status not in (0, 1):
        print(f"::error::mypy exited {args.status} (crash or blocking error):")
        print(text[-4000:])
        return None
    if args.status == 1 and not current:
        print("::error::mypy reported failure but no error line could be parsed:")
        print(text[-4000:])
        return None
    return current


def cmd_check(args: argparse.Namespace) -> int:
    """Fail on errors that the baseline does not hold."""
    current = _read_current(args)
    if current is None:
        return 2
    baseline = load_baseline(Path(args.baseline))
    new = current - baseline
    gone = baseline - current
    print(f"mypy: {sum(current.values())} errors, baseline {sum(baseline.values())}")
    if gone:
        _report(f"fixed since the baseline (shrink it with `write`) [{args.baseline}]", gone, 20)
    if new:
        _report(f"::error::new mypy errors not in {args.baseline}", new)
        return 1
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """Rewrite the baseline; refuse to add entries unless --allow-new."""
    current = _read_current(args)
    if current is None:
        return 2
    path = Path(args.baseline)
    added = current - load_baseline(path)
    if added and path.exists() and not args.allow_new:
        _report(
            "refusing to grow the baseline (fix these, or --allow-new for a version bump)", added
        )
        return 1
    save_baseline(path, current)
    print(f"wrote {sum(current.values())} entries to {path}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "write"):
        cmd = sub.add_parser(name)
        cmd.add_argument("output", help="file holding mypy's stdout/stderr")
        cmd.add_argument("--baseline", required=True)
        cmd.add_argument("--prefix", default="", help="prepended to mypy's paths (e.g. r4/)")
        cmd.add_argument("--status", type=int, default=None, help="mypy's exit status")
        if name == "write":
            cmd.add_argument("--allow-new", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parser().parse_args(argv)
    return cmd_check(args) if args.command == "check" else cmd_write(args)


if __name__ == "__main__":
    sys.exit(main())
