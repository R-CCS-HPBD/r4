"""Every PlatformError code raised in the tree is registered (2026-09-25).

`PlatformError.<family>("<name>", ...)` looks the HTTP status up in the
family's `*_ERRORS` table; an unregistered name raises KeyError inside the
error path, so the caller gets a bare 500 instead of the typed error. Found
live: `catalog.unprocessable` (umrs/api/catalog.py) and, by this sweep,
`sourcegrants.conflict` (umrs/api/source_grants.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import r4_contracts.errors as platform_errors

REPO_ROOT = Path(__file__).resolve().parents[1]
ERRORS_SRC = REPO_ROOT / "r4-contracts/src/r4_contracts/errors.py"
SCANNED = ("umrs/src", "gateway", "r4/src", "mcp-servers/src")

_CLASSMETHOD = re.compile(r"def (\w+)\(cls, name: str.*?code=f\"([\w]+)\.\{name\}\"", re.S)
_CALL = re.compile(r"PlatformError\.(\w+)\(\s*[\"']([a-z0-9_]+)[\"']")


def _registered_codes() -> set[str]:
    codes: set[str] = set()
    for attr, value in vars(platform_errors).items():
        if attr.endswith("_ERRORS") and isinstance(value, dict):
            codes.update(value)
    return codes


def _family_prefixes() -> dict[str, str]:
    """classmethod name -> code prefix, read from the factory definitions."""
    return dict(_CLASSMETHOD.findall(ERRORS_SRC.read_text()))


def test_every_raised_platform_error_code_is_registered() -> None:
    registered = _registered_codes()
    prefixes = _family_prefixes()
    assert prefixes, "no PlatformError factories parsed from errors.py"
    missing = []
    for root in SCANNED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            text = path.read_text(errors="ignore")
            for match in _CALL.finditer(text):
                family, name = match.groups()
                if family not in prefixes:
                    continue  # not a code factory (e.g. from_http_parts)
                code = f"{prefixes[family]}.{name}"
                if code not in registered:
                    line = text[: match.start()].count("\n") + 1
                    missing.append(f"{code}  ({path.relative_to(REPO_ROOT)}:{line})")
    assert not missing, "unregistered PlatformError codes:\n" + "\n".join(missing)
