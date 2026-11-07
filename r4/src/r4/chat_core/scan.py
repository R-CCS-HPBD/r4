"""Pre-send content scanner — detects secrets in user messages.

Lightweight patterns covering common accidental disclosures. The full
LogScrubber runs server-side; this is the client-side safety net.
"""

from __future__ import annotations

import re

_SCAN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI API key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("GitHub token", re.compile(r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}")),
    ("AWS access key", re.compile(r"AKIA[A-Z0-9]{16}")),
    ("Bearer token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-.]{20,}", re.IGNORECASE)),
    (
        "generic API key",
        re.compile(
            r"\b(api[_-]?key|apikey|api_token)\s*[=:]\s*[\"']?([a-zA-Z0-9_\-]{20,})[\"']?",
            re.IGNORECASE,
        ),
    ),
    ("email address", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")),
]


def scan_content(text: str) -> list[str]:
    """Return labels for any sensitive patterns found in text."""
    return [label for label, pattern in _SCAN_PATTERNS if pattern.search(text)]


__all__ = ["scan_content"]
