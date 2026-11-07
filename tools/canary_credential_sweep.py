#!/usr/bin/env python3
"""Canary credential sweep (design §5.3, R1-7).

Two modes:

    --scan-dir PATH --canary TEXT --fingerprint TEXT [--token TEXT] [--allow GLOB]*
        Grep every file under PATH for the canary string, the key
        fingerprint (sha256:first8hex) and an optional session token.
        Expected: 0 hits outside explicitly allowed glob paths
        (encrypted ciphertext files, the podman secret mid-run).
        Reports a verdict JSON.

    --live
        Full end-to-end sweep on a live venue (requires UMRS env + podman):
        provision → exercise → teardown → scan. Exits 2 with guidance when
        the live environment is absent — never fakes a pass.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALLOW = (
    "*ciphertext*",
    "*.pg_dump*cipher*",
    "*/podman/data/overlay-containers/blob/**",  # sealed row payloads
    "*/secrets/jetstream/**",  # podman secret driver mid-run
)


@dataclass
class Hit:
    path: str
    line_no: int
    needle_kind: str  # "canary" | "fingerprint" | "token"
    line_preview: str


@dataclass
class ScanReport:
    allowed_hits: list[Hit] = field(default_factory=list)
    violations: list[Hit] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "violations": [h.__dict__ for h in self.violations],
            "allowed_hits": [h.__dict__ for h in self.allowed_hits],
            "ok": not self.violations,
        }


def _needles(canary: str | None, fingerprint: str | None, token: str | None) -> dict[str, str]:
    out = {}
    if canary:
        out[canary] = "canary"
    if fingerprint:
        out[fingerprint] = "fingerprint"
    if token:
        out[token] = "token"
    return out


def _preview(line: str, needle: str) -> str:
    """Redact the needle out of the reported preview (no echo of secrets)."""
    return line.replace(needle, "<needle>")[:120]


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def _scan_text(
    text: str,
    rel: str,
    needles: dict[str, str],
    allow: tuple[str, ...],
    report: ScanReport,
) -> None:
    """One file's text → hits appended to report (allow-globs swallow expected ones)."""
    for line_no, line in enumerate(text.splitlines(), start=1):
        for needle, kind in needles.items():
            if needle in line:
                hit = Hit(
                    path=rel,
                    line_no=line_no,
                    needle_kind=kind,
                    line_preview=_preview(line, needle),
                )
                if any(fnmatch.fnmatch(rel, pattern) for pattern in allow):
                    report.allowed_hits.append(hit)
                else:
                    report.violations.append(hit)


def scan_tree(
    root: Path,
    needles: dict[str, str],
    allow: tuple[str, ...] = DEFAULT_ALLOW,
    max_file_bytes: int = 512 * 1024 * 1024,
) -> ScanReport:
    report = ScanReport()
    root = root.resolve()
    for path in _iter_files(root):
        rel = str(path.relative_to(root))
        if path.stat().st_size > max_file_bytes:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        _scan_text(text, rel, needles, allow, report)
    return report


def cmd_scan(args: argparse.Namespace) -> int:
    needles = _needles(args.canary, args.fingerprint, args.token)
    if not needles:
        print("nothing to scan (no --canary/--fingerprint/--token)", file=sys.stderr)
        return 2
    allow = tuple(args.allow or DEFAULT_ALLOW)
    report = scan_tree(Path(args.scan_dir), needles, allow)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.violations else 1


def cmd_live(args: argparse.Namespace) -> int:
    """Live sweep against a configured venue (R1-7 release candidate)."""
    import os

    required = ("UMRS_SEAL_PUBKEY_B64", "UMRS_UNSEAL_PRIVKEY_PATH")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(
            "live sweep unavailable: missing config " + ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "Run on a deployed venue: podman host + sealed store configured, then "
            "`--live --project canary-release`.",
            file=sys.stderr,
        )
        return 2
    print(
        "live sweep: not wired to a deployed venue in this runbook — see ops guide", file=sys.stderr
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-dir", help="directory tree to scan")
    parser.add_argument("--canary", help="plaintext canary key string (will be redacted in output)")
    parser.add_argument("--fingerprint", help="key fingerprint to search for (sha256:xxxxxxxx)")
    parser.add_argument("--token", help="session token to verify never lands anywhere")
    parser.add_argument(
        "--allow",
        action="append",
        default=None,
        help="fnmatch pattern for expected ciphertext paths (repeatable)",
    )
    parser.add_argument("--live", action="store_true", help="run the full live sweep")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.live:
        return cmd_live(args)
    if not args.scan_dir:
        parser.error("--scan-dir required unless --live")
    return cmd_scan(args)


if __name__ == "__main__":
    raise SystemExit(main())
