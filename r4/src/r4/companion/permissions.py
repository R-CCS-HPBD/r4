"""Permission manager — gate risky tool execution.

Permission modes:
  ASK_ALWAYS   — prompt the user every time (default)
  AUTO_SAFE    — auto-approve safe tools, prompt for risky
  DENY         — block all tool calls
  TRUST        — auto-approve everything within the session
"""

from __future__ import annotations

import contextvars
import enum
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from r4_contracts.team_engine.plan import GATE_TOOL, TEAM_AGENT_DENY

from . import agent_workspace
from .permission_rules import RuleSet, dangerous_path_reason
from .taint import TaintRegistry
from .team_plan_tools import RUN_ARGUMENTS, describe_run_call

logger = logging.getLogger(__name__)

_DEFAULT_REMEMBERED_APPROVAL_TTL_SEC = 1800.0


def _approval_args_key(tool_name: str, arguments: dict) -> str:
    """Content-hash key for one approved (tool, args) pair.

    Pattern: goose ToolPermissionStore (Apache-2.0) — blake3(toolname+args).
    """
    canonical = json.dumps(
        {"tool": tool_name, "args": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _commit_plan(arguments: Any) -> dict[str, Any]:
    plan = arguments.get("plan") if isinstance(arguments, dict) else None
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except ValueError:
            return {}
    return plan if isinstance(plan, dict) else {}


def commit_plan_digest(arguments: Any) -> str:
    """The digest of the plan a job_commit call carries ('' when none)."""
    digest = _commit_plan(arguments).get("digest")
    return digest if isinstance(digest, str) else ""


def _describe_job_commit(arguments: dict) -> str:
    """Only what UMRS signed: unsigned fields of the plan object are the
    model's and never shown as fact (ADR-105 D-4 review)."""
    plan = _commit_plan(arguments)
    raw_signed = plan.get("signed")
    signed: dict[str, Any] = raw_signed if isinstance(raw_signed, dict) else {}
    lines = [f"  {key}: {_card_value(str(key), signed[key])}" for key in sorted(signed)]
    digest = commit_plan_digest(arguments)
    return "\n".join(
        [
            f"Run UMRS plan {digest[:12] or '?'} (signed by UMRS; nothing else is shown):",
            *lines,
        ]
    )


def _card_key(tool_name: str, arguments: dict) -> str:
    """The key of one approved card (ADR-092 MD-6): the card's arguments only,
    nulls dropped as the companion drops them before the executor sees them."""
    names = CARD_GRANT_TOOLS.get(tool_name, frozenset())
    given = arguments if isinstance(arguments, dict) else {}
    card = {key: value for key, value in given.items() if key in names and value is not None}
    return _approval_args_key(tool_name, card)


def _remembered_approval_ttl() -> float:
    """The ``approval_ttl_s`` agent setting (ADR-099 D-8); env-overridable.

    2026-10-30: the per-call approval cache it once timed
    (``remember_approval``) had no caller and was removed; the value is kept
    because the remote-session settings schema (umrs ``agent_settings``)
    still carries it.
    """
    raw = os.environ.get("R4_PERMISSION_APPROVAL_TTL")
    try:
        value = float(raw) if raw is not None else _DEFAULT_REMEMBERED_APPROVAL_TTL_SEC
    except ValueError:
        return _DEFAULT_REMEMBERED_APPROVAL_TTL_SEC
    return value if value > 0 else _DEFAULT_REMEMBERED_APPROVAL_TTL_SEC


# Tool classification — safe tools can never mutate state or execute code
SAFE_TOOLS: frozenset[str] = frozenset(
    {
        # final_output is the companion's own result channel — zero side
        # effects; blocking on it after every turn reads as a dead stop
        # (reported 2026-09-02 after approving file_write).
        "final_output",
        # ADR-108 W1c: only adds isolation (a branch and a directory under
        # .r4/worktrees); the user's checkout is never written.
        "worktree_enter",
        "file_read",
        "list_dir",
        # ADR-111: reads the user's own saved memory files; changes nothing.
        "memory_read",
        "search_docs",
        # K1.4: reads one passage under the retrieve verb's access rule.
        "open_passage",
        "lab_catalog_search",
        "lab_file_read",
        "list_models",
        "hpc_job_status",
        "grep_search",
        "glob_search",
        "workflow_list",
        "workflow_describe",
        "workflow_status",
        "workflow_results",
        "workflow_wait",
        # Multi-HPC (2026-10-24): the site inventory reads and changes
        # nothing; the model is told to consult it before naming a site.
        "hpc_sites",
        "research_loop_list",
        "research_loop_status",
        "research_loop_approvals",
        "research_session_view",
        "knowledge_status",
        "knowledge_show",
        "knowledge_graph_list",
        "knowledge_query",
        "knowledge_graph_propose",
        "project_list",
        "detect_code_context",
        "detect_language_servers",
        "source_list",
        "demo_plan",
        "project_status",
        # ADR-076: a decision is evidence, never an effect — one scoring
        # call to the decision model; asking approval for it on every
        # use (2026-10-02 audit) made the opted-in tool unusable.
        "decision_evaluate",
        # ADR-092 D-9: listing templates and reading a plan's result spend
        # nothing and change nothing.
        "team_plan_templates",
        "team_plan_result",
        # ADR-105 S1 (2026-11-02): the agent-tool manifest classes these UMRS
        # reads `safe`; outside this set chat mode asked before every job
        # status check, while workflow_status / hpc_job_status ran.
        "job_list",
        "job_status",
        "rag_query",
        "rag_list_documents",
        # ADR-105 S2: waits on the caller's own job events; reads only.
        "job_wait_any",
        # ADR-105 S3: a plan is a UMRS computation that stores nothing.
        "workflow_plan",
    }
)

# ADR-105 D-4: the one tool that turns a UMRS plan into compute.
COMMIT_TOOL = "job_commit"

RISKY_TOOLS: frozenset[str] = frozenset(
    {
        COMMIT_TOOL,
        # ADR-108 W1c: may remove a worktree (a changed one only with discard).
        "worktree_exit",
        "shell_exec",
        "python_exec",
        "file_write",
        "file_edit",
        "hpc_submit",
        "workflow_run",
        "workflow_trigger",
        # B-family (2026-09-27): team start/continue = many LLM calls + budget;
        # ask in chat/ask_always modes, autopilot fires (user-confirmed rule).
        "teamup_start",
        "teamup_run",
        # ADR-092 D-9: a plan run fans out many agents under a token budget
        # (and is never auto-approved, MD-5: NEVER_AUTO_APPROVE below);
        # cancelling one ends work the user approved.
        "team_plan_run",
        "team_plan_cancel",
        # ADR-092 slice 2: a plan gate (ADR-066 class) asks the human whether
        # the run goes on; it is the engine's, never an agent's tool.
        GATE_TOOL,
        "workflow_cancel",
        "workflow_upload",
        "research_loop_start",
        "research_loop_stop",
        "research_loop_decide",
        "research_session_create",
        "knowledge_ingest",
        "knowledge_build_from_context",  # ADR-050: durable write of chat content
        "knowledge_graph_decide",
        "project_ensure",
        "compile_code",
        "run_code",
        "test_code",
        "start_language_servers",
        "source_add",
        "source_use",
        "source_remove",
        "pool_up",
        "pool_down",
        "demo_prepare",
        "endpoint_upgrade",
        "workflow_approve_gate",
        "mcp_register_server",
        "mcp_unregister_server",
        "endpoint_provision",
        "endpoint_stop",
        "job_cancel",
        # ADR-105 S1 (2026-11-02): manifest class `elevated` — a retry runs
        # compute again; an upload writes the project's knowledge store.
        "job_retry",
        "rag_upload",
        # ADR-104: writes every file its patch names (protected paths are
        # read from the patch text, ``permission_rules.dangerous_path_reason``).
        "apply_patch",
    }
)

# Tools that must NEVER be auto-approved — an explicit per-call approval is
# mandatory even in TRUST/agent/autopilot mode and even when the tool was
# session-approved. Recording the user's decision on a knowledge mutation is
# the human authorization boundary: the agent must never stamp it for itself,
# whatever the mode (closes the TRUST/autopilot bypass).
NEVER_AUTO_APPROVE: frozenset[str] = frozenset(
    {
        "knowledge_graph_decide",
        # Deciding a server-hosted loop's tool-grain gate IS the human's
        # permission decision (ADR-036 (a)); an agent auto-approving it would
        # hollow out ask-mode loops.
        "research_loop_decide",
        # ADR-092 MD-5 (maintainer, 2026-10-11): a plan run fans out many
        # agents under a token budget, so its card is always read by a human,
        # under autopilot too; no mode or session approval fires it.
        "team_plan_run",
        # ADR-092 slice 2: a plan ``gate`` is a human decision by definition
        # (ADR-066 classes); nothing but a human answer passes it.
        GATE_TOOL,
    }
)

# Why a never-auto tool asks: the fence reason the approval UI shows.
_NEVER_AUTO_REASONS: dict[str, str] = {
    "team_plan_run": (
        "a plan run fans out agents under a token budget — asked in chat and plan; "
        "autopilot runs it (ADR-092 MD-5, ADR-104 amendment 2)"
    ),
    GATE_TOOL: (
        "a plan gate is a human decision (ADR-066 class) — asked in chat and plan, "
        "autopilot answers it; a no stops the run (ADR-092 slice 2)"
    ),
}

# Which agent's call is asking, while its approval callback runs (ADR-092
# slice 2: ``ApprovalRequest.agent``). Set by an agent fork around the
# parent's callback, read by the approval surfaces (bridge, ACP); empty for
# the lead's own calls. A context variable, not a callback argument, so the
# three-argument callback every surface implements is unchanged.
_APPROVING_AGENT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "r4_approving_agent", default=""
)


def approving_agent() -> str:
    """The label of the agent whose call is being approved now, or ``""``
    when the lead (or no agent fork) asked. The plan run is in the card's
    ``[agent <label> · plan <run>]`` prefix."""
    return _APPROVING_AGENT.get()


# Why the ask being answered now came from a content fence, or "" (fix
# 2026-10-30, approval fence). Set by ``_require_fenced_approval`` around
# the approval callback for the asks that look at THIS call's arguments:
# the ADR-039 taint fence, the credential/identity shell and path fences,
# a G14 ``ask`` rule and a protected path. The surfaces read it (the TUI
# bridge puts ``fenced`` on the approval line) so no client answers such
# an ask from an earlier yes or a project rule; ``approve_for_session``
# refuses to turn an "always" on it into a session grant. A context
# variable for the same reason as ``_APPROVING_AGENT``.
_APPROVAL_FENCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "r4_approval_fence", default=""
)


# What autopilot let through that a person would otherwise have been asked
# (ADR-104 amendment 2): a tainted call, a protected path, a plan gate, a
# decision-gate doubt, a backed-up delete. Set while a call is checked and
# drained by the agent (``take_permission_notes``), which puts each one in
# the transcript. A context variable for the same reason as the two above.
_PERMISSION_NOTES: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "r4_permission_notes", default=()
)


def note_permission(text: str) -> None:
    """Record one note for the call being checked now."""
    logger.info("autopilot: %s", text)
    _PERMISSION_NOTES.set((*_PERMISSION_NOTES.get(), text))


def take_permission_notes() -> tuple[str, ...]:
    """The notes of the last check in this context, cleared."""
    notes = _PERMISSION_NOTES.get()
    _PERMISSION_NOTES.set(())
    return notes


def approval_fence() -> str:
    """The fence reason of the ask being answered now, or ``""`` when the
    ask is an ordinary one (mode, trust allowlist, decision gate, never-auto
    tool). A fenced ask is a per-call human decision on every surface."""
    return _APPROVAL_FENCE.get()


_NEVER_AUTO_DEFAULT_REASON = "human-authorization boundary — never auto-approved (ADR-036)"

# ADR-092 MD-6 (maintainer, 2026-10-11): a human approval of one of these
# tools' cards grants the work it starts the card's listed SAFE tools, for
# that run only. Value: the argument names that make up the card (``wait_sec``
# changes how long the call waits, not what runs, and the executor fills its
# default, so it is not part of the key).
CARD_GRANT_TOOLS: dict[str, frozenset[str]] = {
    "team_plan_run": RUN_ARGUMENTS - {"wait_sec"},
}
_CARD_APPROVAL_TTL_SEC = 600.0

# ADR-092 slice 4 (maintainer, 2026-10-11): a tool whose call, for some
# arguments, is approved by the plan card its executor raises, never by a card
# of its own. ``teamup_start`` with a template crew runs that crew's plan
# through the session's plan executor, which asks the never-auto
# ``team_plan_run`` card (MD-5, with its MD-6 grant list) and starts nothing
# without a human yes; a second ``teamup_start`` card in front of it asked the
# user twice. Which calls those are is ``r4.teams.crews.plan_crew_of``, the
# reading the executor routes by. ``hpc`` and unknown crews keep the tool's own
# rule. A plan agent never reaches this: TEAM_AGENT_DENY refuses the tool in an
# agent's fork before any rung of the ladder.
PLAN_CARD_DELEGATES: frozenset[str] = frozenset({"teamup_start"})


def plan_card_decides(tool_name: str, arguments: Any) -> bool:
    """True when this call's only approval is the plan card its executor asks."""
    if tool_name not in PLAN_CARD_DELEGATES or not isinstance(arguments, dict):
        return False
    from ..teams.crews import plan_crew_of  # lazy: r4.teams imports this module

    return plan_crew_of(arguments.get("crew")) is not None


# ADR-092 D-6: TEAM_AGENT_DENY (imported above, and public here under the same
# name) is the set of tools a plan agent or a team role is never offered and
# never allowed, whatever its allowlist says. It is defined in
# r4_contracts.team_engine.plan, beside the plan validator that refuses a
# profile listing one of them, so the fork's refusal and the plan grammar read
# one set and an approval card never lists a tool the agent cannot have.

# The companion's terminal channel is not a capability: an agent's allowlist
# never narrows it away, or a schema step (ADR-092 D-5) could not end its turn.
_FORK_CHANNEL_TOOLS: frozenset[str] = frozenset({"final_output"})

# Credential / identity probe command shapes seen in shell_exec arguments.
# These ALWAYS require an explicit per-call approval in every mode (TRUST and
# session blanket approvals cannot short-circuit them). Motivation (2026-09-05
# agent-debug round-5 review): when a git push hit a locked SSH key, the agent
# probed for secrets (security find-generic-password -g, dump-keychain-style
# keychain reads) and alternate identities (GIT_SSH_COMMAND overrides,
# ssh-add) before asking. The ladder must force ask-first.
#
# Split rationale from _RE riskiness flags: these regexes only classify the
# COMMAND string passed to shell_exec; they do not inspect output.
_SHELL_NEVER_AUTO_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        # Keychain secret reads / dumps — and ANY other security subcommand:
        # after wrapper normalization the binary alone puts it in this class.
        r"\bsecurity\b",
        # ssh-agent key registration (identity manipulation)
        r"\bssh-add(\s|$)",
        # Alternate SSH identity via env override or -c flag
        r"\bGIT_SSH_COMMAND\s*=",
        r"\bcore\.sshCommand\b",
        # Direct key-handle SSH invocations
        r"\bssh\s+[^|&;]*?(-i|--identity-file)\s+\S+",
        # Force-push destroys remote history — always ask, never auto
        r"\bgit\s+push\b[^|&;]*\s(-f|--force|--force-with-lease)\b",
        r"\bgit\s+push\b[^|&;]*\s\+\S+",
        # Credential-store reads: private key material, keychains, generic
        # PEM/key files, netrc, cloud credentials, UMRS connection profiles
        r"\.ssh/id_|\big reads?\b",
        r"\.(pem|key|keychain|keychain-db)\b",
        r"\.netrc\b",
        r"\.aws/(credentials|config)\b",
        r"\.config/umrs/connections/",
        r"~/\.ssh/",
        r"/keychain",
        # Bare env/printenv dumps (env with no args prints all secrets;
        # `env FOO=bar cmd` intentionally does not match — trailing `(;|&&|$)`).
        r"(^|&&|;;?)\s*(printenv|env)\s*(;|&&|$)",
        # Glob-shaped dotfiles: any filename token starting with a literal '.'
        # immediately followed by glob metachars (`cat .env*`, `cat ~/.ss*/id_*`,
        # `head .en?` — a regex-only fence otherwise never sees the expansion).
        r"(^|[/\s])\.\w*[*?]+",
        # Wildcards inside credential-directory paths (`cre?entials` under
        # .aws, so the credential-path axis itself can't be globbed past).
        # Leading dot is REQUIRED: bare `config/` (`cat config/*.yaml`,
        # `ls config/*`) matches some real directory in almost every repo
        # and would falsely escalate to never_auto (post-review rule 9).
        r"\.(aws|ssh|config|netrc|s3cfg|gitconfig)/[^\s]*[*?]",
        r"\bkeyring\b",
        r"\bdotenv_values?\b",
        # Humans establish (CONSTITUTION §2; review of K1 part A, 2026-10-24):
        # the review CLI's decisions are the person's; the subprocess is also
        # marked (R4_COMPANION_SESSION) so the CLI and UMRS refuse them.
        r"\br4\s+knowledge\s+review\s+(-\S+\s+\S+\s+)*(approve|reject|establish)\b",
    )
)

# Risky-untrusted shapes: inherit existing RISKY handling (prompt in AUTO_SAFE
# and DENY), but additionally must not be short-circuited by TRUST mode or a
# session blanket approval. git push (non-force) sits here.
_SHELL_RISKY_NO_TRUST_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in (r"\bgit\s+push\b",)
)

# Credential path axis — shared across shell_exec (path inside a command
# string) and file tools (path-ish argument values). Writing .gitconfig or
# .git/config is included: a write that plants core.sshCommand re-opens the
# alternate-identity bypass (`core.sshCommand` is itself never_auto).
_CREDENTIAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\.ssh/id_",
        r"\.ssh/config$",
        r"~?/?\.ssh(/|$|\s)",
        r"\.netrc\b",
        r"\.aws/(credentials|config)\b",
        r"\.config/umrs/connections/",
        r"\.gitconfig$",
        r"\.git/config$",
        r"/[Kk]eychain[s]?(\.db|-db)?(/|$|\s)?",
        r"\.(pem|key|keychain|keychain-db)\b",
        # .env family (site secrets per the deployment README): .env, .env.*
        r"(^|[/\s])\.env(\.[\w.-]*)?(\s|$)",
        # MinIO S3 credentials config: ~/.s3cfg (the rotation runbook) and
        # the per-user ~/.config/umrs/s3cfg_<user> r4 login writes.
        r"(^|[/\s])\.?s3cfg\w*\b",
        # Token files (umrs connection profiles, PATs saved to disk)
        r"\.(token|tokens)\b",
        # r4's own stores (cycle-2 security lane, 2026-10-21): source keys,
        # OIDC tokens, profile API keys, the CURVE client secret.
        r"\.config/r4/(sources|tokens|config)\.json\b",
        r"curve-session[\w.-]*\.json\b",
        # Other common credential stores on a workstation.
        r"\.git-credentials\b",
        r"\.docker/config\.json\b",
        r"\.kube/config\b",
        r"\.pgpass\b",
        r"huggingface/token\b",
        r"\.gnupg(/|$|\s)",
    )
)

_FILE_TOOL_PATH_TOOLS: frozenset[str] = frozenset(
    {"file_read", "file_write", "file_edit", "list_dir", "grep_search", "glob_search"}
)
_FILE_PATH_ARG_KEYS: tuple[str, ...] = ("path", "file", "file_path", "dir", "directory")

# A recursive content search rooted at an ancestor of one of these (``~``,
# ``~/.config``) reads the stores inside it (the Python fallback walks hidden
# directories), so it is fenced like a read of the store itself.
_RECURSIVE_READ_TOOLS: frozenset[str] = frozenset({"grep_search"})
_CREDENTIAL_DIRS: tuple[str, ...] = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/r4",
    ".config/umrs/connections",
)


def _credential_path_fence(tool_name: str, arguments: dict) -> str | None:
    """Credential-path axis beyond shell_exec: file tools touching any
    credential-store path are never_auto in every mode.

    The path is checked as named AND as the tool will open it (``~``
    expanded, ``..`` and symlinks followed): a symlink a cloned repo ships
    (``notes.txt -> ~/.ssh/id_ed25519``) is the key it points at.
    """
    if tool_name not in _FILE_TOOL_PATH_TOOLS:
        return None
    recursive = tool_name in _RECURSIVE_READ_TOOLS
    for value in _path_arg_values(tool_name, arguments):
        if _names_credential_path(value) or (recursive and _holds_credential_dir(value)):
            return "never_auto"
    return None


def _path_arg_values(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    """The path-ish argument values of a file tool call (a glob's pattern too)."""
    keys = _FILE_PATH_ARG_KEYS + (("pattern",) if tool_name == "glob_search" else ())
    values = (arguments.get(key) for key in keys)
    return [value for value in values if isinstance(value, str) and value]


def _real_path(value: str) -> str | None:
    """The path the file tool opens (agent workspace honoured, symlinks
    followed), or None when it cannot be resolved (the tool refuses it too)."""
    try:
        return os.path.realpath(agent_workspace.resolve(value, write=False))
    except (OSError, ValueError, RuntimeError):
        return None


def _names_credential_path(value: str) -> bool:
    candidates = {value, _real_path(value) or value}
    return any(p.search(text) for text in candidates for p in _CREDENTIAL_PATH_PATTERNS)


def _holds_credential_dir(value: str) -> bool:
    """True when ``value`` resolves to a directory holding a credential store."""
    real = _real_path(value)
    if real is None:
        return False
    home = os.path.expanduser("~")
    stores = (os.path.realpath(os.path.join(home, name)) for name in _CREDENTIAL_DIRS)
    return any(store == real or store.startswith(real.rstrip(os.sep) + os.sep) for store in stores)


# Interpreter wrappers (`bash -c`, `sh -c`, `python -c`, …) — round-6-review
# policy (supersedes the round-6 blanket escalation): normalize and scan the
# inner payload. A wrapper containing a fenced token or credential path is
# never_auto; a wrapper whose payload cannot be scanned (eval/exec sources,
# base64|sh pipes, curl|sh pipes, command-substitution produced programs,
# heredoc into an interpreter) is never_auto; anything else is ordinary
# RISKY shell_exec. Prompt-fatigue resistance is the point.
_INTERPRETER_WRAPPER_RE = re.compile(
    r"\b(bash|sh|zsh|fish|dash|ksh|python|python3|py|perl|ruby|node|"
    r"osascript)(\.\d+)?\s+-(c|e)\b",
    re.IGNORECASE,
)

# Payloads that cannot be analyzed to prove the wrapper benign.
# (eval/exec forms, pipe-to-interpreter, cmstdoutd-substitution programs,
# heredocs into an interpreter or sourced script).
_WRAPPER_UNSCANNABLE_RE = re.compile(
    r"\b(eval|exec)\b|base64\b[^|]*\|\s*(bash|sh|zsh)|curl\b[^|]*\|\s*(bash|sh|zsh)"
    r"|\b(bash|sh|zsh)\s+(\$\(|<(?!<)|<<<?)|(\$\([^)]*\)\s*(-c|-e)\b)",
    re.IGNORECASE,
)

_QUOTE_ESCAPE_RE = re.compile(r"(?<=\w)[\"'](?=\w)")  # sec"urity" → security


def _normalize_wrapper(command: str) -> str:
    """Flatten wrappers for scanning.

    1. Strip obfuscation quotes inside tokens (`sec"urity"` → `security`).
    2. Expand shell-interpreter wrappers to include their payload for the
       pattern scan (`bash -c 'CMD'` contributes the literal CMD).
    """
    command = _QUOTE_ESCAPE_RE.sub("", command)
    return command


_COMMAND_FENCE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    tuple((pattern, "never_auto") for pattern in _SHELL_NEVER_AUTO_PATTERNS)
    + tuple((pattern, "never_auto") for pattern in _CREDENTIAL_PATH_PATTERNS)
    + tuple((pattern, "risky_no_trust") for pattern in _SHELL_RISKY_NO_TRUST_PATTERNS)
)


def _shell_command_fence(tool_name: str, arguments: dict) -> str | None:
    """Classify a shell_exec command string as fenced.

    Returns "never_auto" | "risky_no_trust" | None. Only shell_exec-style
    tools with a `command` argument participate.

    Axes (round-6 task 3.3 + 3.4, wrapper policy revised 2026-09-05 review):
    1. Wrapper payloads that can't be analyzed (eval/exec, base64|sh,
       curl|sh, command-substitution programs, heredoc into an interpreter)
       → never_auto.
    2. Trivial obfuscation (`sec"urity"`) is normalized before scanning;
       wrapper payloads stay in the scanned text, so a fenced token inside
       `bash -c`/`python -c` still hits rule 3.
    3. Binary/pattern rules (`security`, `ssh-add`, `GIT_SSH_COMMAND=`,
       `core.sshCommand`, `ssh -i`, `git push --force`, `printenv`/bare `env`).
    4. Path axis: any command referencing credential paths (`~/.ssh`,
       `.netrc`, `.aws/credentials`, `.config/umrs/connections/`, keychains)
       is credential-category regardless of the binary.
    """
    if tool_name != "shell_exec":
        return None
    command = arguments.get("command") or arguments.get("cmd") or ""
    if not isinstance(command, str) or not command:
        return None
    if _WRAPPER_UNSCANNABLE_RE.search(command):
        return "never_auto"
    command = _normalize_wrapper(command)
    for pattern, label in _COMMAND_FENCE_RULES:
        if pattern.search(command):
            return label
    return None


# ADR-076 decision gate, read-only skip (W7/H13, 2026-10-05). The gate asks
# the decision model before an auto-approved RISKY call; for a shell command
# the parallel-batch classifier already reads as read-only (agent.py
# ``_is_read_only_shell_command``: ls/cat/head/find/wc-led, no redirection or
# mutation markers) the question is skipped. The skip is stricter than that
# classifier: no command chaining, substitution or background (`;`, `&`, `&&`,
# `||`, backticks, `$(`, `<(`, newlines), no find actions that write or run
# (`-delete`, `-exec`, `-ok`, `-fprint`, …), every pipeline stage must itself
# classify read-only, and a fenced command (credential / force-push) never
# skips. A skipped question can only remove a decision-model escalation; the
# static policy above is unchanged.
_GATE_HIDDEN_EFFECT_RE = re.compile(
    r"[;&`\n\r]|\$\(|[<>]\(|\s-(delete|exec|execdir|ok|okdir|fprint|fprint0|fprintf|fls)\b"
)


def decision_gate_skip(tool_name: str, arguments: dict) -> str | None:
    """Why the ADR-076 decision gate need not ask about this call, or None (ask).

    Only ``shell_exec`` commands qualify (there is no read-only classifier
    for ``python_exec`` code, so Python is always asked). Returns
    ``"read_only_shell"`` for a command every pipeline stage of which the
    existing read-only classifier accepts, with no hidden effect (see
    ``_GATE_HIDDEN_EFFECT_RE``) and no credential / force-push fence.
    """
    if tool_name != "shell_exec":
        return None
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    if _GATE_HIDDEN_EFFECT_RE.search(command) or _shell_command_fence(tool_name, arguments):
        return None
    try:
        from .agent import _is_read_only_shell_command
    except ImportError:  # pragma: no cover - the classifier moved: never skip on a guess
        logger.warning("read-only shell classifier unavailable; the decision gate asks")
        return None
    if all(_is_read_only_shell_command(stage) for stage in command.split("|")):
        return "read_only_shell"
    return None


class PermissionMode(enum.Enum):
    ASK_ALWAYS = "ask_always"
    AUTO_SAFE = "auto_safe"
    DENY = "deny"
    TRUST = "trust"


@dataclass(frozen=True)
class Decision:
    """What the ladder says about one call (ADR-104 D-2).

    ``behavior`` is ``allow``, ``ask`` or ``deny``; ``source`` names the rung
    that decided; ``fenced`` marks an ask about THIS call's arguments (a
    per-call human decision on every surface); ``escalate`` sends an allow
    through the ADR-076 decision gate; ``tool_wide`` marks a deny that holds
    whatever the arguments (only those hide the tool for the session).
    """

    behavior: str
    source: str
    reason: str = ""
    fenced: bool = False
    escalate: bool = False
    tool_wide: bool = False


_DEFAULT_ASK = Decision("ask", "default")

# Tools that change saved memory (ADR-111); ``_rung_memory_taint`` asks for
# them once the session has seen untrusted content.
MEMORY_WRITE_TOOLS = frozenset({"memory_write", "memory_forget"})
# Other calls that reach the memory files: the CLI, its folder variable, the
# Jupyter magic, the default folder in any spelling of the home directory.
_MEMORY_MARKERS = ("r4 memory", "R4_MEMORY_DIR", "companion_remember", ".config/r4/memory")


def _untrusted_seen(registry: Any) -> bool:
    """Untrusted content entered the session (sticky mark or live excerpts)."""
    if getattr(registry, "ever_tainted", False) is True:
        return True
    try:
        return len(registry) > 0
    except TypeError:  # a registry without a size (test doubles): its mark only
        return False


def touches_memory(tool_name: str, arguments: Any) -> bool:
    """Whether a call writes saved memory: a memory tool, or a non-read-only
    call whose arguments name the memory folder or the memory CLI."""
    if tool_name in MEMORY_WRITE_TOOLS:
        return True
    if tool_name in SAFE_TOOLS:
        return False
    from .memory_store import memory_root

    text = json.dumps(arguments, default=str, ensure_ascii=False)
    markers = (*_MEMORY_MARKERS, str(memory_root()))
    return any(marker in text for marker in markers)


# Never-auto tools autopilot runs (ADR-104 amendment 2, maintainer
# 2026-11-05): a plan gate and a plan run's budget card. The knowledge
# decisions stay a person's (CONSTITUTION §2), in every mode.
_AUTOPILOT_DECIDES = frozenset({"team_plan_run", GATE_TOOL})

# The modes in which a read-only tool runs without asking (ADR-104 amendment 1).
_AUTO_SAFE_MODES = frozenset({PermissionMode.AUTO_SAFE, PermissionMode.TRUST})


class PermissionManager:
    """Decides whether a tool call runs: :meth:`decide` walks one ordered
    ladder (ADR-104 D-2), :meth:`check` acts on the answer.

    Rungs 1-7 are the safety floor (deny, never-auto, fences, rules,
    protected paths): no mode, rule or grant passes them. Then the
    allowances: a template crew's plan card, a G14 allow rule, a read-only
    tool (AUTO_SAFE and TRUST), a session "always", then TRUST itself, which
    an opt-in organisation policy (the server allowlist, ADR-035) may narrow
    (ADR-104 amendment 1, 2026-11-03: the read-only and session rungs came
    after TRUST, so a server "no" asked even for ``file_read``). Anything
    left asks.
    """

    _LADDER: tuple[str, ...] = (
        "_rung_deny_mode",
        "_rung_deny_rule",
        "_rung_never_auto",
        "_rung_taint",
        "_rung_memory_taint",
        "_rung_fence",
        "_rung_ask_rule",
        "_rung_protected_path",
        "_rung_plan_card",
        "_rung_allow_rule",
        "_rung_safe",
        "_rung_session",
        "_rung_trust",
    )

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.ASK_ALWAYS,
        approval_callback: Callable[[str, str, dict], bool] | None = None,
        trust_checker: Callable[[str], bool] | None = None,
    ):
        self.mode = mode
        self._approval_callback = approval_callback
        # Server-side allowlist consult (ADR-035): when present, TRUST mode
        # auto-approves ONLY rows the server has on file for this user/scope.
        # Absent → legacy behavior (process-global TRUST).
        self._trust_checker = trust_checker
        self._trust_check_cache: dict[str, tuple[bool, float]] = {}
        # ADR-105 D-4: plan digest -> time of a HUMAN yes to its job_commit
        # card (recorded only where the approval callback said yes, like the
        # card grants below); the commit tool pops it by the digest it sends
        # and tells UMRS ``auto`` when there is none.
        self._commit_approvals: dict[str, float] = {}
        # Per-session blanket approvals (tool_name → True)
        self._session_approvals: dict[str, bool] = {}
        self._approval_recorder: Callable[[str, str], None] | None = None
        # ADR-039/WP-19 default: fence is always wired (empty until a real
        # registry is attached); the fence must never self-disable.
        self._taint_registry = TaintRegistry()
        # ADR-076 decision gate: consulted only where a RISKY call would be
        # auto-approved; a reason turns that into an explicit approval. It
        # can add a question, never remove one.
        self._escalation_check: Callable[[str, dict], str | None] | None = None
        # ADR-092 D-9: a tool whose card depends on its executor's own state
        # renders it through the executor (``set_tool_describer``).
        self._tool_describers: dict[str, Callable[[dict], str]] = {}
        # ADR-092 MD-6: card key → when a human approved that card (one-shot).
        self._card_approvals: dict[str, float] = {}
        # G14: persistent permission rules, set by the session after load.
        self.ruleset: RuleSet | None = None

    @property
    def trust_source(self) -> str:
        """Badge honesty: what decides in TRUST mode.

        Derived — never stale across set_mode / set_trust_checker.
          "server"  the organisation policy is on (ADR-104 amendment 1): the
                    server allowlist narrows autopilot; check cache TTL bounds
                    revocation latency (60s positive, 10s negative).
          "local"   the default: autopilot runs every tool the safety floor
                    lets through; nothing server-side narrows it.
          ""        mode is not TRUST — badge moot.
        """
        if self.mode != PermissionMode.TRUST:
            return ""
        return "server" if self._trust_checker is not None else "local"

    def set_trust_checker(self, checker: Callable[[str], bool] | None) -> None:
        """Attach the server trust allowlist checker (clears its cache)."""
        self._trust_checker = checker
        self._trust_check_cache.clear()

    def clear_trust_check_cache(self) -> None:
        """Drop cached allowlist answers (called after server-side mutation)."""
        self._trust_check_cache.clear()

    def _server_trusted(self, tool_name: str) -> bool:
        """Consult the server allowlist with a short positive-result cache.

        Unreachable/rejected → False (fail closed); the caller then falls
        through to explicit approval instead of auto-trusting.
        """
        if self._trust_checker is None:
            return False
        cached = self._trust_check_cache.get(tool_name)
        now = time.time()
        if cached is not None and cached[1] > now:
            return cached[0]
        try:
            allowed = bool(self._trust_checker(tool_name))
        except Exception as exc:
            logger.debug("trust checker lookup failed for %s: %s", tool_name, exc)
            allowed = False
        # cache positives for 60s, negatives for 10s
        self._trust_check_cache[tool_name] = (allowed, now + (60 if allowed else 10))
        return allowed

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode

    def set_escalation_check(self, check: Callable[[str, dict], str | None] | None) -> None:
        """Attach the decision gate (ADR-076): (tool, args) → a reason to ask, or None."""
        self._escalation_check = check

    def set_tool_describer(self, tool_name: str, describer: Callable[[dict], str] | None) -> None:
        """Render ``tool_name``'s approval card with ``describer`` (ADR-092 D-9).

        ``team_plan_run``'s card must say what the call will run, and that
        depends on the executor: its run root, the session's current model,
        its validation. The module's static describer saw none of these and
        disagreed with the executor (a card saying a call fails before any
        spend while the plan ran). The plan tools register their own, which
        goes through the same ``prepare`` as the call. ``None`` unregisters.
        A fork keeps its own registrations, never its parent's.
        """
        if describer is None:
            self._tool_describers.pop(tool_name, None)
        else:
            self._tool_describers[tool_name] = describer

    def _card(self, tool_name: str, arguments: dict) -> str:
        """The approval description: a registered describer, else the static one.

        A registered describer that raises falls back to the static card,
        so a broken describer never fails or skips the approval (ADR-092 D-9).
        """
        describer = self._tool_describers.get(tool_name)
        if describer is not None:
            try:
                return str(describer(arguments))
            except Exception as exc:  # noqa: BLE001 - a card never fails the approval
                logger.debug("describer for %s failed: %s", tool_name, exc)
        return _describe(tool_name, arguments)

    def set_taint_registry(self, registry: Any) -> None:
        """Attach a TaintRegistry (ADR-039). None resets to an empty one."""
        self._taint_registry = registry if registry is not None else TaintRegistry()

    @property
    def taint_registry(self) -> Any:
        """The registry this manager's taint fence consults (ADR-039), read-only.

        ADR-092 D-6: the plan broker exports an agent's excerpts from its
        fork's registry and imports them downstream; it never swaps the slot.
        """
        return self._taint_registry

    def fork_for_agent(
        self,
        label: str,
        *,
        allow: Iterable[str] | None = None,
        run_id: str = "",
        grants: Iterable[str] = (),
    ) -> "PermissionManager":
        """One agent's own manager over this one (ADR-092 D-6).

        Parallel agents sharing one manager raced on its taint slot (every
        companion re-attaches its registry at construction and at each gate)
        and asked anonymous approvals. A fork owns its taint slot and its
        label; it shares this manager's mode, approvals, trust checker,
        decision gate and durable recorder by reference; its allowlist only
        narrows (``None`` adds no narrowing beyond :data:`TEAM_AGENT_DENY`).

        ``grants`` (ADR-092 MD-6) are the SAFE tools an approved plan card
        listed: the fork runs them without the per-call ask of the lead's
        mode, for its own lifetime only (one turn of that run); every other
        rung (the safety floor, the server allowlist, DENY) still applies.
        Anything outside :data:`SAFE_TOOLS` is dropped, so a risky tool still
        asks per call; nothing is written to the shared approvals, so nothing
        outlives the run.
        """
        return _AgentPermissionFork(self, label=label, allow=allow, run_id=run_id, grants=grants)

    def take_card_approval(self, tool_name: str, arguments: dict) -> bool:
        """True once per human approval of this exact call's card (ADR-092 MD-6).

        Recorded by :meth:`_require_explicit_approval` when the approval
        callback said yes to a :data:`CARD_GRANT_TOOLS` call, and consumed
        here by the executor of that call: a call that was not approved by a
        human (no callback, a CLI run) or a second run of the same arguments
        gets no grant. Expires after ten minutes unused.
        """
        stamp = self._card_approvals.pop(_card_key(tool_name, arguments), None)
        return stamp is not None and time.time() - stamp <= _CARD_APPROVAL_TTL_SEC

    def take_commit_approval(self, digest: str) -> bool:
        """True once when a human approved the job_commit card of the plan
        with this digest (ADR-105 D-4); expires like a card grant."""
        stamp = self._commit_approvals.pop(digest, None) if digest else None
        return stamp is not None and time.time() - stamp <= _CARD_APPROVAL_TTL_SEC

    def _note_commit_approval(self, tool_name: str, arguments: dict) -> None:
        if tool_name == COMMIT_TOOL:
            digest = commit_plan_digest(arguments)
            if digest:
                self._commit_approvals[digest] = time.time()

    def _note_card_approval(self, tool_name: str, arguments: dict) -> None:
        """Record a human yes (called only from the callback invocation).

        Only for a tool that is also never auto-approved: no TRUST, AUTO_SAFE
        or session approval can resolve its card, so the record
        exists only after the approval callback was asked and said yes.
        """
        if tool_name in CARD_GRANT_TOOLS and tool_name in NEVER_AUTO_APPROVE:
            self._card_approvals[_card_key(tool_name, arguments)] = time.time()

    def set_approval_callback(self, cb: Callable[[str, str, dict], bool]) -> None:
        """Set the user-facing approval callback.

        The callback receives (tool_name, description, arguments) and must
        return True to approve or False to deny.
        """
        self._approval_callback = cb

    # ── The decision (ADR-104 D-2) ─────────────────────────────────────────

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> Decision:
        """The first rung of the ladder that has an answer for this call."""
        for rung in self._LADDER:
            answer: Callable[[str, dict[str, Any]], Decision | None] = getattr(self, rung)
            decision = answer(tool_name, arguments)
            if decision is not None:
                return decision
        return _DEFAULT_ASK

    def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return True if the tool call is allowed (asking when it must)."""
        return self.resolve(self.decide(tool_name, arguments), tool_name, arguments)

    def resolve(self, decision: Decision, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Act on a decision: refuse, run (through the decision gate when
        ``escalate``) or ask the approval callback."""
        if decision.behavior == "deny":
            logger.info("%s denied (%s) %s", tool_name, decision.source, decision.reason)
            return False
        if decision.behavior == "allow":
            return self._run_allowed(decision, tool_name, arguments)
        if decision.fenced:
            return self._require_fenced_approval(tool_name, arguments, decision.reason)
        return self._require_explicit_approval(tool_name, arguments, reason=decision.reason or None)

    def denies_tool(self, tool_name: str) -> bool:
        """True when every call of this tool is denied, whatever its
        arguments: the session is in DENY, a rule denies the whole tool, or
        nobody can be asked (no approval callback) about a tool that is not
        auto-approved. Only such a deny hides the tool (ADR-104 D-2)."""
        if self.mode == PermissionMode.DENY:
            return True
        if self.ruleset and self.ruleset.denies_tool(tool_name):
            return True
        return self._approval_callback is None and not self._auto_runs(tool_name)

    def _auto_runs(self, tool_name: str) -> bool:
        """True when the mode alone runs this tool without asking."""
        if self.mode == PermissionMode.TRUST:
            return tool_name not in NEVER_AUTO_APPROVE - _AUTOPILOT_DECIDES
        return self.mode in _AUTO_SAFE_MODES and tool_name in SAFE_TOOLS

    def _run_allowed(self, decision: Decision, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Run an allowed call nobody was asked about: through the decision
        gate when ``escalate``, and only once its deletes are recoverable
        (ADR-104 amendment 2)."""
        if decision.escalate and not self._gated_allow(tool_name, arguments):
            return False
        return self._deletes_recoverable(tool_name, arguments)

    def _deletes_recoverable(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Back up what the call deletes, or ask (per call) when R4 cannot."""
        from .delete_guard import protect, scan

        found = scan(tool_name, arguments)
        if not found.deletes:
            return True
        protection = protect(found)
        if protection.ok:
            if protection.note:
                note_permission(protection.note)
            return True
        return self._require_fenced_approval(tool_name, arguments, protection.reason)

    def _gated_allow(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """An auto-approval of a risky call, unless the decision gate asks
        first; in autopilot its doubt is a note, never a question."""
        if self._escalation_check is None or tool_name not in RISKY_TOOLS:
            return True
        try:
            reason = self._escalation_check(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 - a broken gate never blocks or approves
            logger.debug("decision gate failed for %s: %s", tool_name, exc)
            reason = None
        if not reason:
            return True
        if self.mode == PermissionMode.TRUST:
            note_permission(f"{tool_name}: the decision model doubted this call ({reason}); ran")
            return True
        return self._require_explicit_approval(tool_name, arguments, reason=reason)

    def _rule(self, behavior: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        ruleset = self.ruleset
        return ruleset.first(behavior, tool_name, arguments) if ruleset else None

    def _rung_deny_mode(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        if self.mode != PermissionMode.DENY:
            return None
        return Decision("deny", "deny_mode", "the session denies every tool", tool_wide=True)

    def _rung_deny_rule(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        rule = self._rule("deny", tool_name, arguments)
        if rule is None:
            return None
        reason = f"{rule.source} permission rule denies ({rule.raw or rule.tool_name})"
        return Decision("deny", "rule", reason, tool_wide=not rule.prefix)

    def _rung_never_auto(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # The human-authorization boundary: an explicit per-call approval in
        # every mode (TRUST and session approvals cannot short-circuit it).
        if tool_name not in NEVER_AUTO_APPROVE:
            return None
        if self.mode == PermissionMode.TRUST and tool_name in _AUTOPILOT_DECIDES:
            note_permission(f"{tool_name}: autopilot answers it (ADR-104 amendment 2)")
            return None
        reason = _NEVER_AUTO_REASONS.get(tool_name, _NEVER_AUTO_DEFAULT_REASON)
        return Decision("ask", "never_auto", reason)

    def _rung_taint(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # ADR-039: arguments derived from untrusted content (RAG / document /
        # lab / MCP / web tool results) are asked per call in every mode.
        tainted = self._taint_registry.is_tainted(arguments)
        if tainted is None:
            return None
        if self.mode == PermissionMode.TRUST:
            note_permission(f"{tool_name}: its arguments copy {tainted} content; ran in autopilot")
            return None
        logger.info(
            "Tainted arguments for %s (%s) — explicit approval required", tool_name, tainted
        )
        reason = (
            f"arguments contain {tainted} content from an untrusted "
            "tool result (ADR-039 taint fence)"
        )
        return Decision("ask", "taint", reason, fenced=True)

    def _rung_memory_taint(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # ADR-111 D-4: saved memory outlives the session. Once untrusted
        # content (RAG, document, lab, MCP or web results) entered it, a
        # memory write is a person's decision in every mode, autopilot too:
        # an injected instruction must not save itself for later sessions.
        # The mark is sticky (``ever_tainted``: no TTL, kept across a resume)
        # and covers the other ways to a memory file: a file tool on the
        # memory folder, a shell ``r4 memory`` (review 2026-11-07).
        if not touches_memory(tool_name, arguments) or not _untrusted_seen(self._taint_registry):
            return None
        reason = "saving memory after untrusted content entered this session (ADR-111 D-4)"
        return Decision("ask", "memory_taint", reason, fenced=True)

    def _rung_fence(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # Credential / identity probes and force-pushes in shell commands,
        # file tools touching credential stores (round-6 3.2-3.4).
        fence = _shell_command_fence(tool_name, arguments)
        if fence is None:
            fence = _credential_path_fence(tool_name, arguments)
        if fence is None:
            return None
        if fence == "risky_no_trust" and self.mode == PermissionMode.TRUST:
            return None  # a plain git push: autopilot runs it (amendment 2)
        reason = "credential/identity or force-push fence — always asks (round-6 3.2-3.4)"
        return Decision("ask", "fence", reason, fenced=True)

    def _rung_ask_rule(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        rule = self._rule("ask", tool_name, arguments)
        if rule is None:
            return None
        reason = f"{rule.source} permission rule asks ({rule.raw or rule.tool_name})"
        return Decision("ask", "rule", reason, fenced=True)

    def _rung_protected_path(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        reason = dangerous_path_reason(tool_name, arguments)
        if reason is None:
            return None
        if self.mode == PermissionMode.TRUST:
            note_permission(f"{reason}; ran in autopilot")
            return None
        return Decision("ask", "protected_path", reason, fenced=True)

    def _rung_plan_card(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # ADR-092 slice 4: a template crew's ``teamup_start`` is approved by
        # the plan card its executor asks, never by a card of its own.
        if not plan_card_decides(tool_name, arguments):
            return None
        return Decision("allow", "plan_card", "approval delegated to the plan's own card")

    def _rung_allow_rule(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        rule = self._rule("allow", tool_name, arguments)
        if rule is None:
            return None
        return Decision("allow", "rule", f"{rule.source} permission rule allows", escalate=True)

    def _rung_trust(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # Autopilot runs what the floor let through; the opt-in organisation
        # policy (ADR-035, ADR-104 amendment 1) may narrow it to its allowlist.
        if self.mode != PermissionMode.TRUST:
            return None
        # ADR-104 amendment 2 (2026-11-05) lifts ADR-105 D-4's autopilot ask for
        # job_commit: it runs as an auto commit, which UMRS still refuses over
        # the project's auto-commit threshold (or with none set).
        if self._trust_checker is None or self._server_trusted(tool_name):
            return Decision("allow", "trust", escalate=True)
        reason = "not on your organisation's autopilot allowlist (ADR-035)"
        return Decision("ask", "trust_allowlist", reason)

    def _rung_safe(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        # A read-only tool runs in chat, plan and autopilot alike.
        if tool_name in SAFE_TOOLS and self.mode in _AUTO_SAFE_MODES:
            return Decision("allow", "safe")
        return None

    def _rung_session(self, tool_name: str, arguments: dict[str, Any]) -> Decision | None:
        if not self._session_approvals.get(tool_name):
            return None
        return Decision("allow", "session", escalate=True)

    # ── Asking ─────────────────────────────────────────────────────────────

    @property
    def session_approved_names(self) -> frozenset[str]:
        """Blanket-approved tool names for the session (forum for UI)."""
        return frozenset(self._session_approvals)

    def _require_explicit_approval(
        self,
        tool_name: str,
        arguments: dict,
        *,
        reason: str | None = None,
    ) -> bool:
        """Prompt via the approval callback; deny closed without one.

        ``reason`` names the rung that asked (never-auto / taint / credential
        fence / trust-allowlist miss) so the approval UI can tell the user WHY
        this call could not be auto-approved — without it, autopilot looks
        broken even when the fences behave exactly as designed.
        """
        if self._approval_callback:
            description = self._card(tool_name, arguments)
            if reason:
                description = f"[fence: {reason}]\n{description}"
            approved = self._approval_callback(tool_name, description, arguments)
            if approved:
                self._note_card_approval(tool_name, arguments)
                self._note_commit_approval(tool_name, arguments)
            return approved
        # No callback — deny by default (safe)
        logger.warning("No approval callback set, denying tool: %s", tool_name)
        return False

    def _require_fenced_approval(self, tool_name: str, arguments: dict, reason: str) -> bool:
        """An explicit approval a content fence asked for (fix 2026-10-30).

        The ask carries :func:`approval_fence` while the callback runs, so
        the surface shows it as a per-call decision: no earlier yes, project
        rule or "always" answers it, and an "always" given on it grants
        nothing for the session (:meth:`approve_for_session`).
        """
        token = _APPROVAL_FENCE.set(reason)
        try:
            return self._require_explicit_approval(tool_name, arguments, reason=reason)
        finally:
            _APPROVAL_FENCE.reset(token)

    def approve_for_session(self, tool_name: str) -> None:
        """Grant blanket approval for a tool for the rest of the session.

        Not while a fenced ask is being answered (:func:`approval_fence`):
        an "always" on a tainted, credential or protected-path card approves
        that one call, never the tool (fix 2026-10-30); never for a
        never-auto tool, which a human approves per call (ADR-092 MD-5).
        """
        if approval_fence() or tool_name in NEVER_AUTO_APPROVE:
            logger.info("%s: an 'always' on this ask approves this call only", tool_name)
            return
        self._session_approvals[tool_name] = True
        self._record_durable(tool_name)

    def set_approval_recorder(self, recorder: Callable[[str, str], None] | None) -> None:
        """Attach a durable recorder: (tool_name, scope) → server write.

        Wave T3c/item-8: 'always' decisions become per-session server
        records (trust rows) instead of process-only flags. The recorder
        MUST be best-effort — it never blocks or fails the local grant.
        """
        self._approval_recorder = recorder

    def _record_durable(self, tool_name: str, *, scope: str = "system") -> None:
        """Fire the recorder if attached. Never raises to the caller."""
        recorder = self._approval_recorder
        if recorder is None:
            return
        try:
            recorder(tool_name, scope)
        except Exception as exc:  # noqa: BLE001 — durable write is best-effort
            logger.debug("durable approval record skipped for %s: %s", tool_name, exc)

    def is_safe(self, tool_name: str) -> bool:
        return tool_name in SAFE_TOOLS

    def is_risky(self, tool_name: str) -> bool:
        return tool_name in RISKY_TOOLS

    def classify(self, tool_name: str) -> str:
        if tool_name in SAFE_TOOLS:
            return "safe"
        if tool_name in RISKY_TOOLS:
            return "risky"
        return "unknown"

    def reset_session(self) -> None:
        self._session_approvals.clear()
        self._commit_approvals.clear()


def _narrowed_allow(
    inherited: frozenset[str] | None, allow: Iterable[str] | None
) -> frozenset[str] | None:
    """A fork's allowlist: the intersection with its parent's, never wider."""
    if isinstance(allow, str):
        allow = (allow,)
    mine = None if allow is None else frozenset(str(name) for name in allow)
    if inherited is None:
        return mine
    if mine is None:
        return inherited
    return inherited & mine


def _from_parent(name: str) -> property:
    """A read-only attribute that resolves on the parent at every access.

    Read through rather than copied, so a later ``set_mode`` or
    ``set_trust_checker`` on the lead's manager reaches every live fork; no
    setter, so an agent can never rewire what it shares with the lead.
    """
    return property(lambda self: getattr(self._parent, name, None))


class _AgentPermissionFork(PermissionManager):
    """One agent's view of a host :class:`PermissionManager` (ADR-092 D-6).

    Shared with the parent, by reference and read at call time: the mode,
    the rules, the session approvals, the trust checker and its cache, the
    decision gate, the durable recorder and the approval callback.
    Owned by the fork: its taint registry slot (companions attach theirs at
    construction and at every gate, ``agent.py`` ``__init__`` and
    ``_permission_gate``, so a shared slot raced across parallel agents),
    its label and its allowlist.
    """

    _session_approvals = _from_parent("_session_approvals")
    _trust_checker = _from_parent("_trust_checker")
    _trust_check_cache = _from_parent("_trust_check_cache")
    _escalation_check = _from_parent("_escalation_check")
    _approval_recorder = _from_parent("_approval_recorder")
    _card_approvals = _from_parent("_card_approvals")
    _commit_approvals = _from_parent("_commit_approvals")
    ruleset = _from_parent("ruleset")

    def __init__(
        self,
        parent: PermissionManager,
        *,
        label: str,
        allow: Iterable[str] | None,
        run_id: str,
        grants: Iterable[str] = (),
    ) -> None:
        # Deliberately not super().__init__(): it would assign the state a
        # fork reads from its parent (mode, approvals, rules).
        self._parent = parent
        self._label = str(label)
        self._run_id = str(run_id or "")
        self._allow = _narrowed_allow(getattr(parent, "allow", None), allow)
        self._grants = _narrowed_grants(getattr(parent, "grants", None), grants)
        self._taint_registry = TaintRegistry()
        # Its own: the plan broker builds each agent's catalog over its fork,
        # and those tools must not re-describe the lead's calls.
        self._tool_describers = {}

    @property
    def mode(self) -> PermissionMode:
        """The lead's current mode: a fork never runs under a mode of its own."""
        return self._parent.mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        raise AttributeError(
            f"agent {self._label!r} reads its permission mode from the lead's "
            "manager; set it there (ADR-092 D-6)"
        )

    @property
    def label(self) -> str:
        return self._label

    @property
    def allow(self) -> frozenset[str] | None:
        """The tools this agent may call (``None``: all but the deny set)."""
        return self._allow

    @property
    def grants(self) -> frozenset[str]:
        """The SAFE tools an approved plan card granted this agent (MD-6)."""
        return self._grants

    @property
    def _approval_callback(self) -> Callable[[str, str, dict], bool] | None:
        """The parent's callback at call time, with this agent named first."""
        parent_callback = self._parent._approval_callback
        if parent_callback is None:
            return None
        prefix = self.approval_prefix()

        def _labelled(tool_name: str, description: str, arguments: dict) -> bool:
            token = _APPROVING_AGENT.set(self._label)
            try:
                return parent_callback(tool_name, f"{prefix} {description}", arguments)
            finally:
                _APPROVING_AGENT.reset(token)

        return _labelled

    def approval_prefix(self) -> str:
        """``[agent <label> · plan <run_id>]``; the plan part only inside a plan."""
        if self._run_id:
            return f"[agent {self._label} · plan {self._run_id}]"
        return f"[agent {self._label}]"

    def refusal(self, tool_name: str) -> str | None:
        """Why this agent may not call ``tool_name`` at all, or None."""
        if tool_name in TEAM_AGENT_DENY:
            return "team_agent_deny"
        if self._allow is None or tool_name in self._allow or tool_name in _FORK_CHANNEL_TOOLS:
            return None
        return "not_in_allowlist"

    def denies_tool(self, tool_name: str) -> bool:
        return self.refusal(tool_name) is not None or super().denies_tool(tool_name)

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> Decision:
        """Refuse a denied or unlisted tool before any rung of the ladder; a
        tool the approved card granted (MD-6) skips only the final per-call
        ask of the lead's mode — every other rung still decides."""
        refusal = self.refusal(tool_name)
        if refusal is not None:
            logger.info("agent %s: %s refused (%s)", self._label, tool_name, refusal)
            return Decision("deny", "agent_refusal", refusal, tool_wide=True)
        decision = super().decide(tool_name, arguments)
        if decision is _DEFAULT_ASK and tool_name in self._grants:
            return Decision("allow", "card_grant")
        return decision


def _narrowed_grants(inherited: frozenset[str] | None, grants: Iterable[str]) -> frozenset[str]:
    """A fork's card grants (ADR-092 MD-6): SAFE tools only, never the deny
    set, and a fork of a fork never holds a grant its parent lacks."""
    if isinstance(grants, str):
        grants = (grants,)
    mine = frozenset(str(name) for name in grants or ()) & SAFE_TOOLS
    mine -= TEAM_AGENT_DENY
    return mine if inherited is None else mine & inherited


# An approval card carries EVERYTHING that runs (stabilization sweep
# 2026-10-21, w3-py#1): python_exec's card was the first 80 characters of
# the code on one line, so "yes" approved code the user never saw (the
# chat#1 class, there for shell commands). The TUI wraps and scrolls.


def _describe_python_exec(arguments: dict) -> str:
    """The full code, every line, under a header line."""
    return f"Run Python code:\n{arguments.get('code', '')}"


def _described_args(arguments: dict[str, Any], keys: tuple[str, ...]) -> str:
    """`` · key=value`` for each given argument among ``keys``, in order."""
    given = [(key, arguments.get(key)) for key in keys]
    return "".join(f" · {key}={value}" for key, value in given if value not in (None, ""))


def _describe_run_code(arguments: dict) -> str:
    """What runs, with every argument that changes it (``args`` was hidden)."""
    shown = next((k for k in ("executable", "source_path", "path") if arguments.get(k)), "")
    rest = tuple(k for k in ("source_path", "language", "args") if k != shown)
    return f"Run code: {arguments.get(shown) or '?'}{_described_args(arguments, rest)}"


def _describe_compile_code(arguments: dict[str, Any]) -> str:
    """The source plus what it writes (``output_path``) and builds."""
    source = arguments.get("source_path") or arguments.get("path", "?")
    extra = _described_args(arguments, ("language", "target", "output_path"))
    return f"Compile or syntax-check code: {source}{extra}"


# The arguments ``team_plan_run`` takes: its parameter schema in
# ``companion/team_plan_tools.py`` (a test pins the two together).
_PLAN_RUN_ARGUMENTS: frozenset[str] = RUN_ARGUMENTS


def _describe_team_plan_run(arguments: dict) -> str:
    """ADR-092 D-9: the ``team_plan_run`` card when no plan tools are bound.

    Rendered by the plan tools' own ``prepare`` and card (over the shared
    run root, with no session model), so even this card validates a call
    exactly as the tool does; a session's manager renders its bound
    executor's card instead (:meth:`PermissionManager.set_tool_describer`).
    """
    return describe_run_call(arguments)


_TOOL_DESCRIBERS: dict[str, Callable[[dict], str]] = {
    "shell_exec": lambda a: f"Run shell command: {a.get('command', '?')}",
    "knowledge_ingest": lambda a: (
        f"Extend knowledge graph (ingest {len(a.get('files') or []) or 1} source(s))"
    ),
    "knowledge_graph_propose": lambda a: (
        f"Propose extending knowledge graph: {a.get('graph_id', '?')}"
    ),
    "knowledge_graph_decide": lambda a: (
        "Record your decision on graph-extension proposal: "
        f"{a.get('proposal_id', '?')} -> {'approve' if a.get('approved') else 'decline'}"
    ),
    "python_exec": _describe_python_exec,
    "file_write": lambda a: f"Write file: {a.get('path', '?')}",
    "file_edit": lambda a: f"Edit file: {a.get('path', '?')}",
    "hpc_submit": lambda a: f"Submit HPC job: {a.get('script_path', '?')}",
    "compile_code": _describe_compile_code,
    "run_code": _describe_run_code,
    "test_code": lambda a: (
        f"Run local tests: {a.get('path', '.')}{_described_args(a, ('language', 'target'))}"
    ),
    "detect_language_servers": lambda a: f"Inspect language servers: {a.get('path', '.')}",
    "start_language_servers": lambda a: f"Start language servers: {a.get('path', '.')}",
    "team_plan_run": _describe_team_plan_run,
    "team_plan_cancel": lambda a: f"Cancel team plan run: {a.get('run_id', '?')}",
    COMMIT_TOOL: _describe_job_commit,
    GATE_TOOL: lambda a: (
        f"Plan gate {a.get('gate', '?')} ({a.get('class', '?')}) asks you:\n{a.get('prompt', '')}"
    ),
}


# A credential argument is named on the card, never printed (it is sent,
# not run; the rest of the call is shown in full).
_CREDENTIAL_ARG_RE = re.compile(r"(?i)(^|_)(api_?key|token|secret|password|passwd)$")


def _card_value(key: str, value: Any) -> str:
    """One argument as the card shows it: strings verbatim, the rest as JSON."""
    if _CREDENTIAL_ARG_RE.search(key) and value:
        return f"⟨redacted, {len(str(value))} chars⟩"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _describe_generic(tool_name: str, arguments: dict[str, Any]) -> str:
    """A tool without a describer (workflow verbs, MCP tools, …): its name
    and every argument, one per line. It was the name alone, so the card
    approved arguments it never showed (w3-py#1)."""
    given = arguments if isinstance(arguments, dict) else {}
    lines = [f"{key}: {_card_value(str(key), value)}" for key, value in given.items()]
    return "\n".join([f"Call tool: {tool_name}", *lines])


def _describe(tool_name: str, arguments: dict) -> str:
    """Human-readable approval card text: everything the call runs."""
    describer = _TOOL_DESCRIBERS.get(tool_name)
    if describer is not None:
        return describer(arguments)
    return _describe_generic(tool_name, arguments)
