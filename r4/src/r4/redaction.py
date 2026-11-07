"""One credential redactor for every r4 surface (c2-transport, 2026-10-21).

:func:`redact_tokens` is the companion's last-pass redactor
(``r4.companion.tool_summaries``): PEM keys, JWTs, ``Bearer``/``Basic``
headers, provider tokens (OpenAI, Hugging Face, GitHub, AWS, Slack), URL
passwords and credential-named values. :func:`redact_argv` is the same pass
for text that echoes a command line (argparse errors), plus the rules only
argv needs: ``pk-``/``rk-`` keys, and any other long opaque run is taken for an
API key, except pure hex (UUIDs, digests), which r4 prints as ids.

The companion package is imported on first use: ``r4.cli`` imports this
module at startup, and ``r4.companion`` costs ~0.1 s to import.
"""

from __future__ import annotations

import re

#: The marker :func:`redact_argv` leaves (ASCII: argv text reaches plain terminals and CI logs).
ARGV_REDACTED = "<redacted>"

# `pk-…`/`rk-…` keys (cli.py's list had them; redact_tokens knows only `sk-`).
_ARGV_KEY_PREFIXES = re.compile(r"\b(?:pk|rk)-[A-Za-z0-9_-]{8,}")
# 32+ characters with no path, URL or dotted-name separator around them.
_OPAQUE_RUN = re.compile(r"(?<![\w/:.-])[A-Za-z0-9_+=-]{32,}(?![\w/:.-])")
_HEX_ID = re.compile(r"[0-9a-fA-F-]+")
# argparse's own "argument -k/--api-key: " prefix names the option, not a
# value: kept out of the credential-named-value rule.
_ARGPARSE_PREFIX = re.compile(r"argument [^:\s]+: ")


def redact_tokens(text: str) -> str:
    """``text`` with every credential shape replaced; idempotent."""
    from .companion.tool_summaries import redact_tokens as _redact

    return _redact(text)


def _redact_opaque(match: re.Match[str]) -> str:
    token = match.group(0)
    return token if _HEX_ID.fullmatch(token) else ARGV_REDACTED


def redact_argv(text: str) -> str:
    """:func:`redact_tokens` for command-line echoes, plus the opaque-run rule."""
    from .companion.tool_summaries import _REDACTED

    prefix = _ARGPARSE_PREFIX.match(text)
    head = prefix.group(0) if prefix else ""
    body = redact_tokens(text[len(head) :]).replace(_REDACTED, ARGV_REDACTED)
    body = _ARGV_KEY_PREFIXES.sub(ARGV_REDACTED, body)
    return head + _OPAQUE_RUN.sub(_redact_opaque, body)
