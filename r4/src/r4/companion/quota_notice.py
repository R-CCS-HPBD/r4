"""One readable line for a platform quota refusal (ADR-099 D-5/D-6, RS-1).

A remote session's provider calls go through UMRS's inference proxy, which
refuses a call over the user's daily token cap or request rate with a typed
429; a session start over the live-session cap is refused the same way. The
error text r4 sees carries the server's message ("daily token limit
reached for user alice at rccs: 2000310 of 2000000 tokens; resets
2026-10-24T00:00:00Z"), and the code when the body itself is shown.
``quota_notice`` turns that into the line the transcript shows; any other
error answers None.
"""

from __future__ import annotations

import re

from r4.errors import R4Error, RateLimitError

TOKENS_CODE = "quota.tokens_per_day_exceeded"
RPM_CODE = "quota.rpm_exceeded"
SESSIONS_CODE = "quota.concurrent_sessions_exceeded"

_SUBJECT = r"(?P<scope>user|project) (?P<subject>\S+?)"
_TOKENS_RE = re.compile(
    r"daily token limit reached for " + _SUBJECT + r" at [^:]+: "
    r"(?P<used>\d+) of (?P<limit>\d+) tokens; resets (?P<resets>\S+?Z)",
    re.IGNORECASE,
)
_RPM_RE = re.compile(
    r"request rate limit reached for " + _SUBJECT + r" at [^:]+: "
    r"(?P<used>\d+) of (?P<limit>\d+) requests per minute; retry in (?P<retry>\d+)s",
    re.IGNORECASE,
)
_SESSIONS_RE = re.compile(
    r"concurrent session limit reached for " + _SUBJECT + r" at [^:]+: "
    r"(?P<used>\d+) of (?P<limit>\d+) live sessions",
    re.IGNORECASE,
)
_ADMIN = "A platform admin can raise it."
#: The console's hint for a rate-limit refusal (the bridge shows it beside
#: the error); a quota notice converted from a RateLimitError keeps it.
RATE_LIMIT_HINT = "Rate limited — check /quota first (owner-scoped gauge), or switch models."
#: The longest wait for a request-rate refusal's Retry-After (Q5).
RPM_WAIT_CAP_SEC = 60.0


def _resets(text: str) -> str:
    """``2026-10-24T00:00:00Z`` → ``2026-10-24 00:00 UTC``."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", text)
    return f"{match.group(1)} {match.group(2)} UTC" if match else text


def _tokens(match: re.Match[str]) -> str:
    limit = int(match.group("limit"))
    who = f"{match.group('scope').lower()} {match.group('subject')}"
    return (
        f"Daily token limit reached ({limit:,} tokens/day for {who}); "
        f"resets {_resets(match.group('resets'))}. {_ADMIN}"
    )


def _rpm(match: re.Match[str]) -> str:
    limit = int(match.group("limit"))
    who = f"{match.group('scope').lower()} {match.group('subject')}"
    return (
        f"Request rate limit reached ({limit:,} requests/minute for {who}); "
        f"try again in {match.group('retry')}s. {_ADMIN}"
    )


def _sessions(match: re.Match[str]) -> str:
    limit = int(match.group("limit"))
    who = f"{match.group('scope').lower()} {match.group('subject')}"
    return (
        f"Concurrent session limit reached ({limit:,} live remote sessions for "
        f"{who}); stop one with `r4 session stop <id>`. {_ADMIN}"
    )


_PATTERNS = ((_TOKENS_RE, _tokens), (_RPM_RE, _rpm), (_SESSIONS_RE, _sessions))

_BARE = {
    TOKENS_CODE: f"Daily token limit reached. {_ADMIN}",
    RPM_CODE: f"Request rate limit reached; try again in a minute. {_ADMIN}",
    SESSIONS_CODE: (
        f"Concurrent session limit reached; stop one with `r4 session stop <id>`. {_ADMIN}"
    ),
}


def quota_notice(error_text: str) -> str | None:
    """One transcript line for a typed quota refusal, else None."""
    text = str(error_text or "")
    for pattern, render in _PATTERNS:
        match = pattern.search(text)
        if match:
            return render(match)
    for code, line in _BARE.items():
        if code in text:
            return line
    return None


class QuotaRefusedError(R4Error):
    """A platform quota refusal; its text is the ``quota_notice`` line."""


def raise_quota_notice(exc: BaseException) -> None:
    """Raise ``QuotaRefusedError`` (the notice) when ``exc`` is a quota refusal.

    A rate-limit error keeps the console's hint (the bridge picks the hint
    off the error it shows) and its Retry-After.
    """
    notice = quota_notice(f"{exc}")
    if notice is None:
        return
    details: dict[str, object] = {}
    if isinstance(exc, RateLimitError):
        details = {"hint": RATE_LIMIT_HINT, "retry_after": exc.retry_after}
    raise QuotaRefusedError(notice, details) from exc


def rpm_wait_seconds(exc: BaseException) -> float | None:
    """How long to wait before re-sending after a request-rate refusal
    (``quota.rpm_exceeded``): its full Retry-After, else the "retry in Ns"
    of its message, else a minute, at most :data:`RPM_WAIT_CAP_SEC`. None
    for any other error."""
    text = f"{exc}"
    match = _RPM_RE.search(text)
    if match is None and RPM_CODE not in text:
        return None
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        wait = float(retry_after)
    else:
        wait = float(match.group("retry")) if match else RPM_WAIT_CAP_SEC
    return min(max(1.0, wait), RPM_WAIT_CAP_SEC)


__all__ = [
    "QuotaRefusedError",
    "RATE_LIMIT_HINT",
    "RPM_CODE",
    "RPM_WAIT_CAP_SEC",
    "SESSIONS_CODE",
    "TOKENS_CODE",
    "quota_notice",
    "raise_quota_notice",
    "rpm_wait_seconds",
]
