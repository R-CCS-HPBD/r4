"""Frontend-venue session helpers (R1-5, design §2.5/D-1).

Rules for a venue worker process:
- The session token lives AT most in a mounted secret file
  (``R4_VENUE_SESSION_TOKEN_FILE``). It is read by the runtime to construct
  provider client objects and is never exported into environment variables
  of tool subprocesses.
- ``OPENAI_BASE_URL`` / ``ANTHROPIC_BASE_URL`` point at the egress sidecar;
  the proxy injects the real provider credential. The runtime's ``api_key``
  is the session token — identical shape, provider-specific.
- Local sessions are unaffected (env var absent).
"""

from __future__ import annotations

import os
from pathlib import Path

_TOKEN_FILE_ENV = "R4_VENUE_SESSION_TOKEN_FILE"
# Provider credentials a tool subprocess in a venue must never inherit: the
# runtime builds its provider clients from the token file, and the egress
# proxy injects the real key. R4_GATEWAY_TOKEN is NOT listed: hatch runtime
# packages need it (integrations/hatchling.py hands it on purpose).
_TOKEN_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "R4_ENDPOINT_API_KEY",
        "SESSION_TOKEN",
        "R4_SESSION_TOKEN",
    }
)


def _is_session_credential(key: str) -> bool:
    upper = key.upper()
    return key == _TOKEN_FILE_ENV or upper in _TOKEN_ENV_NAMES or "SESSION_TOKEN" in upper


def venue_token_file() -> str:
    return os.environ.get(_TOKEN_FILE_ENV, "").strip()


def in_venue() -> bool:
    """True inside a frontend-venue worker (token file present and readable)."""
    path = venue_token_file()
    return bool(path and Path(path).is_file())


def read_venue_token() -> str | None:
    """Read the mounted session token. None outside a venue; never logged."""
    path = venue_token_file()
    if not path:
        return None
    try:
        token = Path(path).read_text(encoding="utf-8", errors="strict").strip()
    except OSError:
        return None
    return token or None


def sanitize_tool_spawn_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Env for tool subprocesses in a venue worker.

    Drops the token-file reference, any session-token env binding and the
    provider API keys in ``_TOKEN_ENV_NAMES`` — tool subprocesses must never
    inherit session or provider credentials (design §2.5).
    Outside a venue the env is returned unchanged (local sessions keep their
    existing behavior — the user's own machine is the trust boundary there).
    """
    source = os.environ if env is None else env
    if not in_venue():
        return dict(source)
    cleaned = {key: value for key, value in source.items() if not _is_session_credential(key)}
    # Provider SDKs in the worker read base_url env + runtime token; the
    # subprocess tools still get the proxy env (no credentials inside).
    return cleaned
