"""Phase 2m refactor helper: move a command's argparse + cmd_X out of cli.py
into r4/src/r4/_cli/<name>.py.

Usage:
    python tools/refactor_cli_command.py <name> <argparse_var> <cmd_func>

Example:
    python tools/refactor_cli_command.py health health_parser cmd_health

This is a one-shot codemod for the Phase 2m refactor. It:
  1. Locates the inline ``<argparse_var> = subparsers.add_parser(...)`` block.
  2. Locates the ``def <cmd_func>(args):`` function body.
  3. Writes a new ``_cli/<name>.py`` containing both, plus the standard
     CommandHandler wrapper.
  4. Removes both from cli.py and replaces the function with an import shim.
  5. Adds ``<name>`` to the pre-registration tuple.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "r4" / "src" / "r4" / "cli.py"


def _extract_argparse_block(src: str, var: str) -> tuple[str, int, int]:
    """Find ``var = subparsers.add_parser(...)`` and the lines that follow it
    until a blank line that introduces the next command."""
    pattern = rf"^(    {re.escape(var)} = subparsers\.add_parser\()"
    m = re.search(pattern, src, flags=re.MULTILINE)
    if m is None:
        raise SystemExit(f"argparse var {var!r} not found")
    start = m.start()
    # Walk forward line-by-line until we hit a blank line followed by either
    # a comment block describing the next command or another top-level decl.
    lines = src[start:].splitlines(keepends=True)
    out: list[str] = []
    blank_streak = 0
    for ln in lines:
        out.append(ln)
        stripped = ln.strip()
        if stripped == "":
            blank_streak += 1
        else:
            # Stop when we leave the var's block: any line that is at indent
            # level 4 and starts with neither the var name nor a continuation
            # of an earlier add_argument call.
            if (
                blank_streak >= 1
                and ln.startswith("    ")
                and not (stripped.startswith(f"{var}") or stripped.startswith(")"))
            ):
                # Pop the line we just appended; it belongs to the next block.
                out.pop()
                # Also drop the trailing blank lines.
                while out and out[-1].strip() == "":
                    out.pop()
                break
            blank_streak = 0
    block = "".join(out)
    end = start + len(block)
    return block, start, end


def _extract_function(src: str, name: str) -> tuple[str, int, int]:
    """Extract ``def <name>(...):`` plus body up to the next top-level def/class."""
    m = re.search(rf"^def {re.escape(name)}\(", src, flags=re.MULTILINE)
    if m is None:
        raise SystemExit(f"function {name!r} not found")
    start = m.start()
    rest = src[m.end() :]
    nm = re.search(r"^(def |class |if __name__)", rest, flags=re.MULTILINE)
    end = m.end() + (nm.start() if nm else len(rest))
    body = src[start:end].rstrip() + "\n"
    return body, start, end


def _normalize_argparse_block(block: str, var: str) -> str:
    """Indent the body for use inside register() and rewrite var → parser."""
    lines = block.splitlines()
    out: list[str] = []
    for ln in lines:
        # Strip the leading 4-space module-level indent so we can re-indent at
        # method depth (8 spaces).
        if ln.startswith("    "):
            ln = ln[4:]
        # Replace var name with ``parser`` for the local handle.
        ln = re.sub(rf"\b{re.escape(var)}\b", "parser", ln)
        out.append(ln)
    body = "\n".join(out)
    # The first line is now ``parser = subparsers.add_parser(...)``.
    return body


def _normalize_function(body: str) -> str:
    """Rewrite intra-module references for the new module location."""
    # Most cmd_X bodies do ``from .client import ...`` etc. Those need to
    # become ``from ..client import ...`` at module depth two.
    body = re.sub(r"^(\s*)from \.([a-zA-Z_])", r"\1from ..\2", body, flags=re.MULTILINE)
    body = re.sub(r"^(\s*)from \._chat", r"\1from .._chat", body, flags=re.MULTILINE)
    body = re.sub(r"^(\s*)from \._http", r"\1from .._http", body, flags=re.MULTILINE)
    return body


# Names of helper symbols that live in ``r4.cli`` and must be imported
# explicitly when a moved cmd_X function references them. Detected by a
# scan over the function body. Updated as we discover new helpers.
CLI_HELPERS = {
    "_die",
    "_current_project_id",
    "_endpoint_reference_hint",
    "_format_wait_duration",
    "_render_wait_bar",
    "_format_endpoint_wait_line",
    "_endpoint_wait_detail",
    "_friendly_status_detail",
    "_display_status_detail",
    "_wait_for_endpoint_ready",
    "_run_with_provision_progress",
    "_pick_model_interactive",
    "_parse_lora_specs",
    "_resolve_endpoint_models",
    "_print_readiness_report",
    "_print_active_context",
    "_probe_umrs_direct",
    "_configure_profile_auth",
    "_get_current_session",
    "_parse_lookback",
    "_is_http_404",
    "HelpfulArgumentParser",
    # cmd helpers that one command may delegate to another
    "cmd_chat",
    "cmd_chat_teamup",
    "cmd_rag",
    "cmd_model",
    "cmd_config",
    "cmd_up",
    "cmd_endpoint",
    "cmd_health",
    "cmd_project",
    "cmd_job",
    "cmd_mcp",
    "cmd_mcp_hatch",
    "cmd_teamup",
    "cmd_search",
    "cmd_agent",
    "cmd_agent_chat",
    "cmd_agent_create",
    "cmd_agent_run",
    "cmd_agent_sessions",
    "cmd_agent_tools",
    "cmd_agent_scan",
    "cmd_agent_audit",
    "cmd_init",
    "cmd_login",
    "cmd_logout",
    "cmd_companion",
    "cmd_sessions",
    "cmd_artifact",
}


def _detect_helper_imports(func_body: str, own_name: str) -> list[str]:
    """Return the list of CLI_HELPERS that the function body references."""
    used: list[str] = []
    for h in sorted(CLI_HELPERS):
        if h == own_name:
            continue
        if re.search(rf"\b{re.escape(h)}\b", func_body):
            used.append(h)
    return used


MODULE_TEMPLATE = '''"""``r4 {name}`` command (Phase 2m refactor, 2026-04-28).

Self-contained module owning both the argparse subparser and the
dispatch implementation. The ``cmd_{name}`` symbol is re-exported from
``r4.cli`` for back-compat.
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, Namespace
from typing import Any

from .base import CommandHandler
from .registry import command


@command
class {camel}Command(CommandHandler):
    """``r4 {name}`` handler."""

    name = "{name}"
    help = {help_repr}

    def register(self, subparsers) -> ArgumentParser:
{register_body}
        return parser

    def handle(self, args: Namespace) -> int:
        rc = {cmd_func}(args)
        return rc if isinstance(rc, int) else 0


{function_body}
'''


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def refactor(name: str, argparse_var: str, cmd_func: str, help_text: str) -> None:
    src = CLI_PATH.read_text()
    block, b_start, b_end = _extract_argparse_block(src, argparse_var)
    func, f_start, f_end = _extract_function(src, cmd_func)

    # Normalize block for handler.register
    norm_block = _normalize_argparse_block(block, argparse_var)
    # Indent for inside register() at 8 spaces.
    indented = "\n".join("        " + ln if ln else "" for ln in norm_block.splitlines())

    func_norm = _normalize_function(func)
    helpers_used = _detect_helper_imports(func_norm, cmd_func)
    if helpers_used:
        # Inject a lazy import inside the function body, just after the
        # signature/docstring. We add a one-line import at the top of the
        # function body to keep the rewrite simple.
        helper_import = f"    from ..cli import {', '.join(helpers_used)}  # noqa: F401\n"
        # Insert after the first line that is the def signature + optional
        # docstring (we just put it right after the def line).
        lines = func_norm.splitlines(keepends=True)
        # locate first def-line, insert helper import after it
        for idx, ln in enumerate(lines):
            if ln.lstrip().startswith("def "):
                insert_idx = idx + 1
                # Skip docstring lines
                if insert_idx < len(lines) and lines[insert_idx].lstrip().startswith(
                    ('"""', "'''")
                ):
                    docline = lines[insert_idx].lstrip()
                    if docline.count('"""') >= 2 or docline.count("'''") >= 2:
                        insert_idx += 1
                    else:
                        # Multi-line docstring; walk forward.
                        insert_idx += 1
                        while insert_idx < len(lines):
                            if '"""' in lines[insert_idx] or "'''" in lines[insert_idx]:
                                insert_idx += 1
                                break
                            insert_idx += 1
                lines.insert(insert_idx, helper_import)
                break
        func_norm = "".join(lines)

    module_text = MODULE_TEMPLATE.format(
        name=name,
        camel=_camel(name),
        help_repr=repr(help_text),
        register_body=indented,
        cmd_func=cmd_func,
        function_body=func_norm,
    )

    # Replace in cli.py: remove argparse block (preserving blank lines around)
    # Replace function with shim.
    out_path = ROOT / "r4" / "src" / "r4" / "_cli" / f"{name}.py"
    if out_path.exists():
        out_path.unlink()
    out_path.write_text(module_text)

    # Remove function from cli.py FIRST (since indices change after removing
    # the argparse block).
    new_src = (
        src[:f_start]
        + (f"from ._cli.{name} import {cmd_func}  # noqa: E402, F401  (Phase 2m re-export)\n\n\n")
        + src[f_end:]
    )

    # Now find the argparse block in the updated source and remove it.
    block2, b2_start, b2_end = _extract_argparse_block(new_src, argparse_var)
    # Replace with a 1-line marker comment.
    marker = (
        f"    # ``r4 {name}`` lives in ``_cli/{name}.py`` "
        f"({_camel(name)}Command). Registered via the pre-registration loop.\n"
    )
    new_src = new_src[:b2_start] + marker + new_src[b2_end:]

    CLI_PATH.write_text(new_src)
    print(f"Moved {name!r}: -{f_end - f_start} chars + -{b_end - b_start} chars")


def add_to_preregistration(name: str) -> None:
    src = CLI_PATH.read_text()
    # The tuple is e.g. ``("workflow", "rag", "pool", "map", "chat")``.
    pattern = r"(for _name in \()([^)]+)(\):)"
    m = re.search(pattern, src)
    if not m:
        raise SystemExit("pre-registration loop not found")
    items = m.group(2)
    if f'"{name}"' in items:
        return
    new_items = items.rstrip() + f', "{name}"'
    new_src = src[: m.start(2)] + new_items + src[m.end(2) :]
    # Also add the import line to the try-block.
    import_pattern = r"(from \._cli import chat as _cli_chat_mod  # noqa: F401)"
    m2 = re.search(import_pattern, new_src)
    if m2 is None:
        # Fall back: add right after the registry import line.
        m2 = re.search(r"(from \._cli import registry as _cli_registry_pre\n)", new_src)
        if m2 is None:
            raise SystemExit("import anchor not found")
    insert_at = m2.end()
    import_line = f"        from ._cli import {name} as _cli_{name}_mod  # noqa: F401\n"
    if import_line not in new_src:
        new_src = new_src[:insert_at] + import_line + new_src[insert_at:]
    CLI_PATH.write_text(new_src)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit("usage: refactor_cli_command.py <name> <argparse_var> <cmd_func> <help>")
    name, var, cmd, help_text = sys.argv[1:5]
    refactor(name, var, cmd, help_text)
    add_to_preregistration(name)
