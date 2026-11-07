"""Detect stale package-name references in build configuration files.

Catches leftover ``aisci`` references in Makefile, pyproject.toml, and
CI workflow files.  These references break ``make build``, ``make type-check``,
and other developer commands.

Usage:
    python tools/stale_name_validator.py          # exits 0 on success
    python -m pytest tests/test_stale_name_validator.py   # CI gate

Enforcement class: PERMANENT
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STALE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\baisci\b", re.IGNORECASE), "stale 'aisci' package name reference"),
)

SCAN_FILES: tuple[str, ...] = (
    "Makefile",
    "pyproject.toml",
    ".github/workflows/*.yaml",
    ".github/workflows/*.yml",
)

ALLOWLIST_PATHS: tuple[str, ...] = (
    # Architecture docs may reference historical names for context
    "docs/ARCHITECTURE_ARCHAEOLOGY.md",
    "docs/ARCHITECTURE_CONVERGENCE.md",
    "docs/ARCHITECTURE_EXECUTION_PLAN.md",
)


@dataclass(frozen=True)
class StaleNameFinding:
    path: str
    line: int
    content: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.message} — {self.content.strip()!r}"


def validate_no_stale_names(repo_root: Path) -> list[StaleNameFinding]:
    """Scan build files for stale package name references."""
    findings: list[StaleNameFinding] = []
    allowlist = set(ALLOWLIST_PATHS)

    for glob_pattern in SCAN_FILES:
        for path in sorted(repo_root.glob(glob_pattern)):
            rel = path.relative_to(repo_root).as_posix()
            if rel in allowlist:
                continue
            if not path.is_file():
                continue
            try:
                lines = path.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line_text in enumerate(lines, 1):
                # Skip comments that reference the validator itself
                if "stale_name_validator" in line_text:
                    continue
                for pattern, message in STALE_PATTERNS:
                    if pattern.search(line_text):
                        findings.append(
                            StaleNameFinding(
                                path=rel,
                                line=lineno,
                                content=line_text,
                                message=message,
                            )
                        )
    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    findings = validate_no_stale_names(repo_root)
    if not findings:
        print("Stale name validator passed: no stale package name references found")
        return 0
    for f in findings:
        print(f.format())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
