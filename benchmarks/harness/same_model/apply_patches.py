"""Apply the R4 harness-gap fixes to a scratch copy of r4/src/r4 (never the repo)."""

import sys
from pathlib import Path

root = Path(sys.argv[1])
fixes = set(sys.argv[2].split(","))


def sub(rel, old, new, count=1):
    p = root / rel
    s = p.read_text()
    n = s.count(old)
    assert n >= count, f"{rel}: pattern not found ({n}): {old[:60]!r}"
    p.write_text(s.replace(old, new) if count == -1 else s.replace(old, new, count))
    print(f"  patched {rel} ({n}x): {old[:50]!r}")


if "F1" in fixes:
    sub(
        "companion/agent.py",
        """    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) < 2:
        return messages
    last_idx = tool_indices[-1]
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i == last_idx or m.get("role") != "tool":""",
        """    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) < 2:
        return messages
    # bench fix F1: protect the WHOLE last tool round (every tool message after the last
    # assistant message that issued tool_calls) and trim older results only once the tool
    # history exceeds a budget, instead of cutting every non-final result immediately.
    last_call = max((i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")), default=-1)
    protected = {i for i in tool_indices if i > last_call} or {tool_indices[-1]}
    if sum(len(messages[i].get("content") or "") for i in tool_indices) <= _TOOL_HISTORY_BUDGET_CHARS:
        return messages
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i in protected or m.get("role") != "tool":""",
    )
    sub(
        "companion/agent.py",
        "_TOOL_RESULT_HEAD_KEEP = 1024\n",
        "_TOOL_RESULT_HEAD_KEEP = 1024\n_TOOL_HISTORY_BUDGET_CHARS = 200_000  # bench fix F1\n",
    )
if "F2" in fixes:
    sub(
        "companion/tools.py",
        'if len(output) > 8000:\n        output = output[:4000] + "\\n…[truncated]…\\n" + output[-2000:]',
        'if len(output) > 32000:  # bench fix F2\n        output = output[:20000] + "\\n…[truncated]…\\n" + output[-8000:]',
        count=-1,
    )
    sub(
        "companion/tools.py",
        'if len(output) > 8000:\n            output = output[:4000] + "\\n…[truncated]…\\n" + output[-2000:]',
        'if len(output) > 32000:  # bench fix F2\n            output = output[:20000] + "\\n…[truncated]…\\n" + output[-8000:]',
    )
    sub(
        "companion/agent.py",
        "COMPANION_TOOL_OUTPUT_TRUNCATE = 12_000",
        "COMPANION_TOOL_OUTPUT_TRUNCATE = 40_000  # bench fix F2",
    )
if "F3" in fixes:
    sub(
        "companion/skills.py",
        "        return any(kw.lower() in text_lower for kw in self.keywords)",
        "        import re as _re  # bench fix F3: whole-word keyword match\n        return any(_re.search(r'(?<![a-z0-9])' + _re.escape(kw.lower()) + r'(?![a-z0-9])', text_lower) for kw in self.keywords)",
    )
    sub(
        "companion/skills.py",
        'DEFAULT_ACTIVE_SKILLS: frozenset[str] = frozenset({"umrs"})',
        "DEFAULT_ACTIVE_SKILLS: frozenset[str] = frozenset()  # bench fix F3: umrs lazy",
    )
if "F4" in fixes:
    sub(
        "companion/agent.py",
        "- Explain what you plan to do before executing\n",
        "- Batch independent reads and commands as parallel tool calls in one response\n",
    )
if "F6" in fixes:
    sub(
        "companion/tools.py",
        "def _shell_exec(command: str, cwd: str | None = None, timeout: int = 60) -> str:",
        "def _shell_exec(command: str, cwd: str | None = None, timeout: int = 180) -> str:  # bench fix F6",
    )
    sub(
        "companion/tools.py",
        "def _python_exec(code: str, timeout: int = 60) -> str:",
        "def _python_exec(code: str, timeout: int = 180) -> str:  # bench fix F6",
    )
    sub(
        "companion/tools.py",
        '''    except subprocess.TimeoutExpired:
        hint = _find_command_hint(command) if command.strip().startswith("find") else ""
        tail = f"  ({hint})" if hint else ""
        return f"Error: command timed out after {timeout}s{tail}"''',
        """    except subprocess.TimeoutExpired as _te:  # bench fix F6: keep partial output
        hint = _find_command_hint(command) if command.strip().startswith("find") else ""
        tail = f"  ({hint})" if hint else ""
        part = _te.stdout if isinstance(_te.stdout, str) else (_te.stdout or b"").decode("utf-8", "replace")
        part = part[-8000:] if part else ""
        return f"Error: command timed out after {timeout}s{tail}" + (f"\\n[partial output before timeout]\\n{part}" if part else "")""",
    )
print("ok")
