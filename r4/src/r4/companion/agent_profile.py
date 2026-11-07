"""Prompt-assembly switches: prompt hygiene (H19) and the terminal profile (H2).

Plan: ``docs/development/tb4-harness-efficiency-2026-10-24.md`` §3.1.

**Prompt hygiene** (``R4_COMPANION_PROMPT_HYGIENE``, default on) fixes defects
every user saw in the assembled prompt: the skill catalog listed twice, bodies
of platform skills whose tools are not offered, and the code skill steering to
tools no one used. In unattended runs (``R4_AUTONOMOUS=1``) it also drops the
"ask the user" line and the ``web_fetch``/``web_search`` tools the session's
web access refuses. ``0`` restores the old prompt and tool descriptions
byte for byte (with ``R4_AUTONOMOUS`` unset), so a benchmark arm can measure
the fix.

**Terminal profile** (``R4_AGENT_PROFILE=terminal``, default unset) is an
ablation for unattended terminal tasks, never a product default and never
implied by ``R4_AUTONOMOUS``: a short core prompt, a fixed tool allowlist,
keyword activation limited to the coding skills, and no skill catalog,
research spine, project state or workflow templates. With the variable unset
(or any other value) nothing in this module changes the prompt or the tools.

Every function reads the environment at call time; the module holds no
state and imports nothing from the companion, so ``tools.py`` and
``agent.py`` can read it with ``getattr(module, name, default)``. Under the
profile ``agent.py`` replaces the companion prompt with :func:`core_prompt`,
leaves out ``<project_state>`` and offers only :func:`tool_allowlist`; the
session leaves out the catalog, the spine and the workflow templates.
"""

from __future__ import annotations

import os

from .env_switch import env_switch

PROFILE_ENV = "R4_AGENT_PROFILE"
TERMINAL = "terminal"
HYGIENE_ENV = "R4_COMPANION_PROMPT_HYGIENE"

#: Shell output chars kept per call under the profile (H11; ``tools.py`` keeps
#: head and tail, 10k + 6k).
SHELL_OUTPUT_CAP = 16_000

_TOOL_ALLOWLIST = frozenset(
    {
        "shell_exec",
        "python_exec",
        "file_read",
        "file_write",
        "file_edit",
        "apply_patch",
        "list_dir",
        "grep_search",
        "glob_search",
        "todo_write",
        "tool_result_fetch",
        # G9: background-agent metas ride beside tool_result_fetch (harness
        # plumbing, same lane).
        "async_agent_spawn",
        "async_agent_status",
        "async_agent_message",
    }
)
_SKILL_ALLOWLIST = frozenset({"code", "polyglot_coding"})

_CORE_PROMPT = """\
You are R4, an autonomous coding agent working in a terminal on one task. \
No human is in the loop: nobody answers questions or confirms steps, so \
decide, act and finish on your own.

How to work:
- Read the task first, then inspect the workspace (list_dir, file_read, \
grep_search, glob_search). Note every path, file name, format and \
constraint the task names.
- Build, verify, finish. Make the change, then check it the way a grader \
would: run the project's own tests or build, run the program on the stated \
inputs and compare the output with the required paths and formats exactly.
- Run commands with shell_exec. Chain related commands in one call \
(cmd1 && cmd2) rather than one command per call.
- Long jobs (builds, training, downloads, servers): start them in the \
background with output to a log file (nohup CMD > /tmp/job.log 2>&1 &), \
then poll with short calls (tail -n 50 /tmp/job.log). Never pipe a long \
command into tail or head: their output appears only when the command ends.
- Use the interpreter and packages the task environment provides (python3 \
on PATH, the project's own virtualenv if it has one).
- Edit with file_edit (a unique old_text snippet) or file_write; apply_patch \
takes a unified diff.
- For a multi-step task keep a short plan with todo_write and update it as \
steps complete.
- When a result was truncated, read the rest with tool_result_fetch instead \
of running the command again.
- If an approach fails twice the same way, change the approach; never repeat \
an identical command hoping for a different result.
- Stay inside the task's workspace unless the task names another path.

Finishing:
- Before you finish, re-read the task and check each requirement against the \
actual files and outputs.
- Leave outputs exactly where the task says; remove scratch files you created \
in output directories.
- When the work is done and verified, reply with a short summary of what you \
did and how you checked it, with no tool call. That reply ends the task.
"""


def active() -> bool:
    """True only when ``R4_AGENT_PROFILE=terminal``."""
    return (os.environ.get(PROFILE_ENV) or "").strip().lower() == TERMINAL


def core_prompt() -> str:
    """The profile's system prompt; it replaces the companion prompt (agent.py)."""
    return _CORE_PROMPT


def tool_allowlist() -> frozenset[str]:
    """The only tools the profile offers (``agent.py`` applies it after the
    availability gate and records the rest as ``profile``)."""
    return _TOOL_ALLOWLIST


def skill_allowlist() -> frozenset[str]:
    """The only skills the profile activates by keyword and loads bodies for."""
    return _SKILL_ALLOWLIST


def hygiene_enabled() -> bool:
    """Prompt hygiene (H19, H15) is on unless ``R4_COMPANION_PROMPT_HYGIENE=0``."""
    return env_switch(HYGIENE_ENV, True)
