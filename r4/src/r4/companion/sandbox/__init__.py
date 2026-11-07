"""Local tool sandbox: opt-in OS containment for what a local session starts (ADR-101).

Every process the agent of a local session starts (shell, Python, the coding
and test tools, language servers, local stdio MCP servers, hatch runtimes) can
run inside bubblewrap (Linux) or Seatbelt (macOS): writes confined to the
workspace and a per-session temp directory, credential stores unreadable,
credentials removed from the environment, and the network isolated except
through R4's egress proxy, which follows the session's web access. Default
off (D-1); ``/sandbox on``, ``r4 --sandbox on``, ``R4_SANDBOX`` or the profile
default turn it on. Not a mini-RiVault and not an ADR-061 profile: a client
side wrapper around processes the harness already owns, with no job store,
queue or scheduler (D-10).

``launch`` (the MCP supervisor, run as ``__main__`` by the bootstrap in
:func:`mcp_launcher_prefix`) is deliberately not imported here, so runpy runs
it without a re-import warning.
"""

from __future__ import annotations

from .backends import (
    Backend,
    BwrapBackend,
    SeatbeltBackend,
    UnavailableBackend,
    backend_for_platform,
    seatbelt_profile,
)
from .context import (
    SandboxContext,
    SpawnSpec,
    bind_sandbox,
    current_sandbox,
    installed_sandbox,
    pin_sandbox,
    pinned_sandbox,
    set_current_sandbox,
    wrap_spawn,
)
from .mcp_policy import mcp_launcher_prefix, mcp_policy_digest, write_mcp_policy
from .policy import (
    SandboxPolicy,
    UserSandboxPolicy,
    deny_read_paths,
    safe_user_policy,
    scrub_env,
    user_sandbox_policy,
)
from .proxy import EgressProxy, ProxyEndpoint, decide, vet_addresses
from .selftest import run_self_test
from .setting import (
    REFUSED,
    SANDBOX_DETAIL_LINES,
    SANDBOX_ENV,
    SANDBOX_LAUNCH_ENV,
    SANDBOX_MODES,
    SANDBOX_NOTICE_OFF,
    SANDBOX_NOTICE_OFF_CLI,
    SANDBOX_SOURCES,
    SANDBOXED_ENV,
    SELF_TEST_FAILED,
    UNAVAILABLE,
    SandboxError,
    SandboxSetting,
    launch_setting,
    make_sandbox_setting,
    parse_sandbox_flag,
    profile_of,
    profile_sandbox_default,
    remember_sandbox_default,
    resolve_sandbox,
    restore_sandbox_default,
    safe_profile_default,
    sandbox_from_env,
    take_launch_sandbox,
)

__all__ = [
    "REFUSED",
    "SANDBOXED_ENV",
    "SANDBOX_DETAIL_LINES",
    "SANDBOX_ENV",
    "SANDBOX_LAUNCH_ENV",
    "SANDBOX_MODES",
    "SANDBOX_NOTICE_OFF",
    "SANDBOX_NOTICE_OFF_CLI",
    "SANDBOX_SOURCES",
    "SELF_TEST_FAILED",
    "UNAVAILABLE",
    "Backend",
    "BwrapBackend",
    "EgressProxy",
    "ProxyEndpoint",
    "SandboxContext",
    "SandboxError",
    "SandboxPolicy",
    "SandboxSetting",
    "SeatbeltBackend",
    "SpawnSpec",
    "UnavailableBackend",
    "UserSandboxPolicy",
    "backend_for_platform",
    "bind_sandbox",
    "current_sandbox",
    "decide",
    "deny_read_paths",
    "installed_sandbox",
    "launch_setting",
    "make_sandbox_setting",
    "mcp_launcher_prefix",
    "mcp_policy_digest",
    "parse_sandbox_flag",
    "pin_sandbox",
    "pinned_sandbox",
    "profile_of",
    "profile_sandbox_default",
    "remember_sandbox_default",
    "resolve_sandbox",
    "restore_sandbox_default",
    "run_self_test",
    "safe_profile_default",
    "safe_user_policy",
    "sandbox_from_env",
    "scrub_env",
    "seatbelt_profile",
    "set_current_sandbox",
    "take_launch_sandbox",
    "user_sandbox_policy",
    "vet_addresses",
    "wrap_spawn",
    "write_mcp_policy",
]
