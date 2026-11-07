"""Sanitized ``args_summary`` builders for tool cards (wave T3-ui S2).

Emission-side allowlist for canonical ``tool_call_start`` payloads. A card
never shows raw arguments on any rail: every tool resolves through the
category map (name → category → field subset → truncation → redaction).
Unknown tools fall to the default = tool name only.

Redaction is the last pass before any value crosses the wire (sk- keys,
Bearer tokens, JWTish triplets). Held line from the T3-ui plan: rails must
omit emission for tools without a safe summary (never degrade to raw args —
default "name only" entry covers everything by definition, so emission is
always possible but never unfiltered).
"""

from __future__ import annotations

import re
from typing import Any, Optional

MAX_PATH_CHARS = 96
MAX_QUERY_CHARS = 96
MAX_SHELL_CHARS = 120
MAX_RESULT_PREVIEW_CHARS = 200

# Bounded tool-result preview on the wire (round-3 task 2): 16 KiB covers an
# entire failing traceback / a typical file-read page end-to-end, caps the
# webview store at 50 cards × 16 KiB ≈ 800 KiB, and keeps a single JSONL
# bridge frame workable. Anything bigger is fetched on demand by call id.
MAX_ENVELOPE_RESULT_CHARS = 16 * 1024

# Marker suffix *inside* the preview so the card can show a real truncation
# hint without a separate parse.
TRUNCATED_MARKER = "…[truncated — expand to load full result]"

_READ_TOOLS = {
    "lab_file_read",
    "file_read",
    "read_text_file",
    "rag_list_docs",
    "list_dir",
}
_SEARCH_KEYS = ("query", "q")
_SEARCH_SUFFIXES = ("_search",)
_WRITE_TOOLS = {
    "file_write",
    "file_edit",
    "file_patch",
    "write_text_file",
    "create_file",
    "apply_patch",
    "edit_files",
}
_EXEC_TOOLS = {
    "shell_exec",
    "python_exec",
    "nodejs_exec",
    "hpc_submit",
    "run_code",
    "compile_code",
    "test_code",
}
_PATH_KEYS = ("path", "file_path", "s3_key", "file", "bundle_dir", "uri", "dir_path")
_ARG_KEYS = ("command", "code", "cmd", "script", "source")

_REDACTED = "⟨redacted⟩"
# The end of a credential-named key: `api_key`, `access_token`,
# `AWS_SECRET_ACCESS_KEY`, `secret_key` (s3cfg), `password` — not
# `max_tokens` or `s3_key`. No leading `[\w-]*`: that went quadratic on
# long hyphenated runs (`a-a-a-…`, 16 KiB → 7 s).
_SECRET_NAME = (
    r"(?:api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key"
    r"|secret|token|passwd|password)"
)
# (pattern, replacement). Order matters: whole-shape secrets first, then the
# key-named values, so a JSON/YAML/env/ini assignment keeps its key name.
_TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"
        ),
        _REDACTED,
    ),
    (re.compile(r"sk-[A-Za-z0-9_-]{4,}"), _REDACTED),
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), _REDACTED),
    # `Authorization: Basic …` / `Token …` (the Bearer form is above).
    (
        re.compile(
            r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?:(?:basic|digest|token)\s+)?[^\s\"',;]+"
        ),
        rf"\1{_REDACTED}",
    ),
    # A JWT, also one wrapped onto following lines of token characters.
    (
        re.compile(
            r"(?m)eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]*)?"
            r"(?:\r?\n[A-Za-z0-9_.-]{8,}(?=\r?$))*"
        ),
        _REDACTED,
    ),
    # Provider tokens recognisable on their own: Hugging Face, GitHub, AWS, and
    # Slack (`xoxb-`/`xoxp-`/… with a dash; `xoxe-1-` refresh and `xapp-1-`
    # app-level tokens slipped through before 2026-10-21).
    (
        re.compile(
            r"\b(?:hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
            r"|AKIA[0-9A-Z]{16}|xox[abeprs]-[A-Za-z0-9-]{10,}|xapp-[0-9]+-[A-Za-z0-9-]{10,})"
        ),
        _REDACTED,
    ),
    # A Slack incoming-webhook URL is itself the credential: keep the host.
    (
        re.compile(r"(?i)(\bhooks\.slack\.com/(?:services|workflows|triggers)/)[A-Za-z0-9/_-]+"),
        rf"\1{_REDACTED}",
    ),
    # `scheme://user:password@host` keeps the user and host.
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]{0,15}://[^\s/:@\"']+:)[^\s/@\"']+@"), rf"\1{_REDACTED}@"),
    # `api_key=…`, `"api_key": "…"`, `secret_key = …`, `HF_TOKEN=…`.
    (
        re.compile(rf"(?i)({_SECRET_NAME}[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}}]+)"),
        rf"\1{_REDACTED}",
    ),
)


def redact_tokens(text: str) -> str:
    """Last-pass token redaction; idempotent."""
    out = text
    for pattern, replacement in _TOKEN_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_of(arguments: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _category(tool_name: str) -> str:
    if tool_name in _READ_TOOLS:
        return "read"
    if tool_name in _WRITE_TOOLS:
        return "write"
    if tool_name in _EXEC_TOOLS:
        return "exec"
    if tool_name.endswith(_SEARCH_SUFFIXES):
        return "search"
    return "default"


def args_summary_for(tool_name: str, arguments: Optional[dict[str, Any]]) -> str:
    """Sanitized one-line summary of a tool call. Always safe to emit."""
    category = _category(tool_name)
    arguments = arguments if isinstance(arguments, dict) else {}
    if category in ("read", "write"):
        return redact_tokens(_truncate(_first_of(arguments, _PATH_KEYS), MAX_PATH_CHARS))
    if category == "search":
        return redact_tokens(_truncate(_first_of(arguments, _SEARCH_KEYS), MAX_QUERY_CHARS))
    if category == "exec":
        raw = _first_of(arguments, _ARG_KEYS)
        first_line = raw.splitlines()[0] if raw else ""
        return redact_tokens(_truncate(first_line, MAX_SHELL_CHARS))
    return ""  # default: tool name only


def result_preview_for(tool_name: str, result: Any) -> str:
    """Sanitized capped result preview for tool_call_end."""
    raw = result if isinstance(result, str) else str(result or "")
    return redact_tokens(_truncate(raw, MAX_RESULT_PREVIEW_CHARS))


def result_preview_full(tool_name: str, result: Any) -> str:
    """Sanitized, BOUNDED result text for tool_call_end (preview surface).

    Round-2: no cap at all meant a 5 MB job log flowed uncapped into the
    webview transcript store. Round-3 task 2 bounds the envelope at
    ``MAX_ENVELOPE_RESULT_CHARS`` (16 KiB): enough for any realistic error
    or a typical file read; anything larger stays in the session's result
    journal and is fetched on demand via `sessions/tool_result`.
    Sanitization (``redact_tokens``) still runs on the full payload.
    """
    raw = result if isinstance(result, str) else str(result or "")
    safe = redact_tokens(raw)
    if len(safe) <= MAX_ENVELOPE_RESULT_CHARS:
        return safe
    return safe[:MAX_ENVELOPE_RESULT_CHARS] + TRUNCATED_MARKER


def result_envelope_cap() -> int:
    return MAX_ENVELOPE_RESULT_CHARS
