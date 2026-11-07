"""The session's sandbox setting: on or off, and where the choice came from (ADR-101 D-2, D-11).

One setting, ``sandbox: {mode, chosen_at, source}`` in session metadata beside
``web_access``. First match wins: the launch flag (``r4 --sandbox``, carried
in ``R4_LAUNCH_SANDBOX`` and popped by its consumer), the session's own earlier
choice, ``R4_SANDBOX``, the profile default ``sandbox_default`` in
``config.json``, then off (``default_off``). Before all of them: a frontend
venue worker resolves to off (``venue``, the remote podman sandbox does the
work) and a process already inside a sandbox (``R4_SANDBOXED`` set) to on
(``inherited``), so a nested ``r4`` never wraps again.

Repo layers (``.r4/config.toml``, ``.r4/local.toml``) never set it: a cloned
repository must not switch a user's sandbox off. That is why the profile
default is read from ``config.json`` by name here and is no ``_FieldSpec``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SANDBOX_MODES: tuple[str, ...] = ("off", "on")
SANDBOX_SOURCES: tuple[str, ...] = (
    "flag",
    "env",
    "session",
    "profile",
    "slash",
    "default_off",
    "venue",
    "inherited",
)
#: ``R4_SANDBOX=on|off``: a non-interactive choice (D-2 step 3).
SANDBOX_ENV = "R4_SANDBOX"
#: ``r4 --sandbox on|off`` hands its flag on in this variable; the consumer pops it.
SANDBOX_LAUNCH_ENV = "R4_LAUNCH_SANDBOX"
#: Set inside every sandboxed child: ``bwrap`` or ``seatbelt``.
SANDBOXED_ENV = "R4_SANDBOXED"

#: The profile-store keys (config.json, never a repo layer).
PROFILE_DEFAULT_KEY = "sandbox_default"
PROFILE_POLICY_KEY = "sandbox_policy"

UNAVAILABLE = "sandbox.unavailable"
SELF_TEST_FAILED = "sandbox.self_test_failed"
REFUSED = "sandbox.refused"

#: The one-line notice a local session shows while the sandbox is off (D-1).
SANDBOX_NOTICE_OFF = (
    "Sandbox off: shell, Python and MCP servers run with your full permissions. "
    "Turn it on with /sandbox on (this session) or r4 --sandbox on (at launch)."
)
#: The same notice where there is no ``/sandbox`` (``r4 -p``, the Rich REPL).
SANDBOX_NOTICE_OFF_CLI = (
    "Sandbox off: shell, Python and MCP servers run with your full permissions. "
    "Turn it on with r4 --sandbox on (this run), R4_SANDBOX=on, "
    "or r4 config sandbox on (the profile default)."
)
#: What turning it on confines (the TUI's expandable notice, ``/sandbox status``).
SANDBOX_DETAIL_LINES: tuple[str, ...] = (
    "Writes: only the workspace, a per-session temp directory and your extra_write_paths.",
    "Reads: everything except the r4 config directory and credential stores "
    "(~/.ssh, ~/.gnupg, ~/.aws, ~/.kube, ~/.docker, ~/.netrc, ...).",
    "Network: isolated; web traffic only through r4's proxy, which follows /web "
    "(off, allowlist or public) and refuses private and cloud-metadata addresses.",
    "Environment: API keys, passwords and session tokens are removed "
    "(R4_GATEWAY_URL and R4_GATEWAY_TOKEN stay).",
    "It applies from the agent's next command; what already runs keeps running unconfined.",
    "/sandbox off turns it off; /sandbox default on makes it the default.",
)

_FLAG_VALUES = {
    "on": "on",
    "true": "on",
    "1": "on",
    "yes": "on",
    "off": "off",
    "false": "off",
    "0": "off",
    "no": "off",
}
_LABEL_BY_SOURCE = {"inherited": "on (inherited)", "venue": "off (remote sandbox)"}


class SandboxError(RuntimeError):
    """A sandbox that cannot be used; ``code`` is the stable reason class."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


@dataclass(frozen=True)
class SandboxSetting:
    """One session's sandbox choice."""

    mode: str
    chosen_at: str = ""
    source: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode == "on"

    def label(self) -> str:
        """``on`` / ``off`` / ``on (inherited)`` / ``off (remote sandbox)``."""
        return _LABEL_BY_SOURCE.get(self.source, self.mode)

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> SandboxSetting | None:
        """The choice in ``metadata["sandbox"]``; None when absent or unreadable."""
        raw = metadata.get("sandbox") if isinstance(metadata, Mapping) else None
        if not isinstance(raw, Mapping) or raw.get("mode") not in SANDBOX_MODES:
            return None
        source = str(raw.get("source") or "session")
        return cls(str(raw["mode"]), str(raw.get("chosen_at") or ""), source)

    def to_metadata(self) -> dict[str, str]:
        return {"mode": self.mode, "chosen_at": self.chosen_at, "source": self.source}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_sandbox_flag(value: str) -> str:
    """``on``/``off`` (and true/false/1/0/yes/no) -> ``"on"`` | ``"off"``."""
    mode = _FLAG_VALUES.get(str(value).strip().lower())
    if mode is None:
        raise SandboxError(REFUSED, f"unknown sandbox mode {value!r}; use on or off")
    return mode


def make_sandbox_setting(mode: str, *, source: str, now: str = "") -> SandboxSetting:
    """A validated setting stamped now."""
    if source not in SANDBOX_SOURCES:
        raise SandboxError(REFUSED, f"unknown sandbox source {source!r}")
    return SandboxSetting(parse_sandbox_flag(mode), now or _now(), source)


def sandbox_from_env(env: Mapping[str, str]) -> SandboxSetting | None:
    """``R4_SANDBOX``; None when unset. An unknown value raises ``sandbox.refused``."""
    raw = str(env.get(SANDBOX_ENV) or "").strip()
    return make_sandbox_setting(raw, source="env") if raw else None


def _flag_choice(raw: str) -> SandboxSetting | None:
    try:
        return make_sandbox_setting(raw, source="flag")
    except SandboxError as exc:
        logger.warning("ignoring the launch sandbox %r: %s", raw, exc)
        return None


def take_launch_sandbox(env: MutableMapping[str, str] | None = None) -> SandboxSetting | None:
    """Pop ``R4_LAUNCH_SANDBOX`` (so nothing the agent starts inherits it) and parse it."""
    target = os.environ if env is None else env
    raw = str(target.pop(SANDBOX_LAUNCH_ENV, "") or "").strip()
    return _flag_choice(raw) if raw else None


def launch_setting(
    spec: str | None, env: MutableMapping[str, str] | None = None
) -> SandboxSetting | None:
    """The launch preset: an explicit ``--sandbox`` value, else ``R4_LAUNCH_SANDBOX``.

    The variable is popped either way; an invalid value is logged and None.
    """
    from_env = take_launch_sandbox(env)
    return _flag_choice(str(spec)) if spec else from_env


def profile_of(holder: Any) -> str | None:
    """``holder.profile`` (a config) when it is a non-empty string, else None."""
    profile = getattr(holder, "profile", None)
    return profile if isinstance(profile, str) and profile else None


def _env_choice(env: Mapping[str, str]) -> SandboxSetting | None:
    try:
        return sandbox_from_env(env)
    except SandboxError:
        return None


def _profile_choice(profile_default: str) -> SandboxSetting | None:
    mode = _FLAG_VALUES.get(str(profile_default or "").strip().lower())
    return SandboxSetting(mode, _now(), "profile") if mode else None


def _forced(env: Mapping[str, str]) -> SandboxSetting | None:
    """D-11: a venue worker is off (``venue``), a sandboxed process on (``inherited``)."""
    from ...venue import in_venue

    if in_venue():
        return SandboxSetting("off", _now(), "venue")
    if str(env.get(SANDBOXED_ENV) or "").strip():
        return SandboxSetting("on", _now(), "inherited")
    return None


def resolve_sandbox(
    *,
    metadata: Mapping[str, Any],
    env: Mapping[str, str],
    launch: SandboxSetting | None = None,
    profile_default: str = "",
) -> SandboxSetting:
    """ADR-101 D-2 (after D-11): first match wins."""
    candidates = (
        _forced(env),
        launch,
        SandboxSetting.from_metadata(metadata),
        _env_choice(env),
        _profile_choice(profile_default),
    )
    for choice in candidates:
        if choice is not None:
            return choice
    return SandboxSetting("off", _now(), "default_off")


def profile_name(profile: str | None = None) -> str:
    """The profile the store is read for: the argument, ``R4_PROFILE``, else ``default``."""
    from ...config import DEFAULT_PROFILE

    return profile or os.environ.get("R4_PROFILE") or DEFAULT_PROFILE


def _profile_store() -> Any:
    from ...config import ProfileConfigStore

    return ProfileConfigStore()


def profile_sandbox_default(profile: str | None = None) -> str:
    """``"on"`` | ``"off"`` | ``""`` from ``config.json`` key ``sandbox_default``."""
    stored = _profile_store().load(profile_name(profile))
    raw = str(stored.get(PROFILE_DEFAULT_KEY) or "").strip().lower()
    return _FLAG_VALUES.get(raw, "")


def safe_profile_default(profile: str | None = None) -> str:
    """:func:`profile_sandbox_default`, "" when the profile store is unreadable."""
    try:
        return profile_sandbox_default(profile)
    except Exception:  # noqa: BLE001 — an unreadable profile store is no default
        logger.debug("sandbox profile default unreadable", exc_info=True)
        return ""


def remember_sandbox_default(mode: str, profile: str | None = None) -> None:
    """Write the profile default (``/sandbox default on|off``, ``r4 config sandbox``)."""
    _profile_store().save(profile_name(profile), **{PROFILE_DEFAULT_KEY: parse_sandbox_flag(mode)})


def restore_sandbox_default(previous: str, profile: str | None = None) -> None:
    """Put back a default read before a write: ``""`` removes the key."""
    _profile_store().save(profile_name(profile), **{PROFILE_DEFAULT_KEY: previous or None})
