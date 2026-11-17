#!/usr/bin/env python3
"""Prove that umrs-protocol was installed from this checkout (U2.0, ADR-117 D-2).

CI runs this after every install of umrs-protocol. It exits 1 with one
``::error::`` line per problem unless all of these hold:

(a) the installed distribution came from ``<root>/umrs/protocol``: its PEP 610
    ``direct_url.json`` names that directory, and ``umrs_protocol.__file__``
    resolves under it (an editable install; ``--non-editable`` drops this
    second half for a job that builds the package from the checkout);
(b) the installed ``umrs_protocol/hpc_gate.py`` bytes equal the checkout's;
(c) ``importlib.metadata.version("umrs-protocol")`` equals the version in
    ``umrs/protocol/pyproject.toml``;
(d) with ``--flipped`` (U2.0b onward): ``r4_contracts.hpc_gate`` is
    ``umrs_protocol.hpc_gate``, and the gate's sha256 and GATE_VERSION are the
    release pinned in ``umrs/protocol/tests/golden/gate.json``.

It catches an index install (the name is unclaimed on PyPI, so a package
index could substitute it) and a stale shadow on the self-hosted runner, the
failure class of the MessageQueue proof in ci.yaml's Test UMRS job.

Stdlib only; Python 3.9 compatible (ops-watch may run the host's python3).

Usage: python scripts/ci/assert_umrs_protocol.py [--root DIR] [--non-editable] [--flipped]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

DIST = "umrs-protocol"
PACKAGE = "umrs_protocol"
GATE = "hpc_gate.py"
VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def default_root() -> Path:
    """The repository root this script lives in (scripts/ci/ -> root)."""
    return Path(__file__).resolve().parents[2]


def protocol_dir(root: Path) -> Path:
    return (root / "umrs" / "protocol").resolve()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent)
    except ValueError:
        return False
    return True


def direct_url_dir(dist: importlib.metadata.Distribution) -> Path | None:
    """The local directory a PEP 610 direct_url.json names, or None."""
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    url = urlparse(json.loads(raw).get("url", ""))
    if url.scheme != "file":
        return None
    return Path(unquote(url.path)).resolve()


def origin_problems(root: Path, module_file: Path, editable: bool) -> list[str]:
    """(a): where the installed distribution and module came from."""
    want = protocol_dir(root)
    problems = []
    source = direct_url_dir(importlib.metadata.distribution(DIST))
    if source != want:
        problems.append(f"{DIST} was not installed from {want} (direct_url: {source})")
    if editable and not _is_under(module_file, want):
        problems.append(f"{PACKAGE} imports from {module_file}, not under {want}")
    return problems


def gate_problems(root: Path, module_file: Path) -> list[str]:
    """(b): the installed gate bytes equal the checkout's."""
    installed = module_file.parent / GATE
    checkout = protocol_dir(root) / "src" / PACKAGE / GATE
    if not installed.is_file():
        return [f"installed {PACKAGE} has no {GATE} ({installed})"]
    if installed.read_bytes() != checkout.read_bytes():
        return [f"installed {installed} differs from the checkout's {checkout}"]
    return []


def checkout_version(root: Path) -> str:
    text = (protocol_dir(root) / "pyproject.toml").read_text(encoding="utf-8")
    match = VERSION_LINE.search(text)
    return match.group(1) if match else ""


def version_problems(root: Path) -> list[str]:
    """(c): the installed version is the checkout's."""
    installed = importlib.metadata.version(DIST)
    want = checkout_version(root)
    if installed != want:
        return [f"{DIST} {installed} is installed; the checkout is {want or '(unreadable)'}"]
    return []


def flipped_problems(root: Path) -> list[str]:
    """(d): the r4_contracts alias and the pinned gate release (U2.0b)."""
    pinned = json.loads((protocol_dir(root) / "tests" / "golden" / "gate.json").read_text())
    alias = importlib.import_module("r4_contracts.hpc_gate")
    gate = importlib.import_module(f"{PACKAGE}.hpc_gate")
    problems = []
    if alias is not gate:
        problems.append("r4_contracts.hpc_gate is not umrs_protocol.hpc_gate (no alias)")
    digest = hashlib.sha256(Path(gate.__file__).read_bytes()).hexdigest()
    if digest != pinned["sha256"]:
        problems.append(f"gate sha256 {digest} is not the pinned release {pinned['sha256']}")
    want_version = pinned["gate_version"]
    if want_version != gate.GATE_VERSION:
        problems.append(f"GATE_VERSION {gate.GATE_VERSION} is not {want_version}")
    return problems


def install_problems(root: Path, editable: bool) -> list[str]:
    """(a) to (c); an absent distribution is a single problem."""
    try:
        module = importlib.import_module(PACKAGE)
        importlib.metadata.distribution(DIST)
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        return [f"{DIST} is not installed: {exc}"]
    module_file = Path(module.__file__ or "").resolve()
    return [
        *origin_problems(root, module_file, editable),
        *gate_problems(root, module_file),
        *version_problems(root),
    ]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=default_root(), help="repository root")
    parser.add_argument(
        "--non-editable",
        action="store_true",
        help="the package was built from the checkout, not installed editable",
    )
    parser.add_argument(
        "--flipped", action="store_true", help="also prove the r4_contracts alias (U2.0b)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    problems = install_problems(args.root, editable=not args.non_editable)
    if args.flipped and not problems:
        problems = flipped_problems(args.root)
    for problem in problems:
        print(f"::error title=umrs-protocol proof::{problem}")
    if problems:
        return 1
    module = importlib.import_module(PACKAGE)
    print(
        f"umrs-protocol proof: {importlib.metadata.version(DIST)} from {module.__file__}"
        + (" (alias flipped)" if args.flipped else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
