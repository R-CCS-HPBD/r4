"""``test_fix_loop`` composite tool (catch-up W2/G4).

Competitors (Claude Code, Aider) run "edit → test → analyse → edit" as a
tight semantic loop inside one high-level instruction. The companion
formerly needed one full agent turn per cycle (~6 turns on
implement-add vs. ~2–3 ideal), because test feedback + edit decision are
discrete turns.

This composite runs the whole cycle **inside one tool call**:

1. run the command (bounded ``tail_bytes``),
2. on failure, ask the LLM for a **minimal unified-diff patch** against
   the explicitly-supplied files,
3. apply it via the same ``apply_patch`` machinery (atomic),
4. re-run the command — up to ``max_iterations``.

Invariants:
- guardrails on patches: workdir escape / conflicts stop the loop with a
  report — never a partial write,
- anti-loop guard: identical output hash on two consecutive iterations
  -> early-stop,
- emits a machine-parseable first line (`test_fix_loop`) followed by a
  JSON payload so the event/UI layers can render iteration stats
  (`attempts[{iteration, exit_code, action, fix_note, bytes_applied}]`).

No silent file discovery: the caller passes ``files=[paths]`` — only
those file contents reach the LLM context (least context, token budget).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .apply_patch import apply_patch

if TYPE_CHECKING:
    from r4.client import Client

MAX_FILE_CONTEXT_BYTES = 12_000
RUN_TIMEOUT_S = 180
MAX_LLM_PROPOSAL_CHARS = 12_000

_SYSTEM = (
    "You are a focused code-fixer. You receive a test command's tail output "
    "and the current content of specific files. Reply with ONLY a unified "
    "diff patch (git-apply compatible, no prose, no fences) that minimally "
    "fixes the failure. If you cannot produce a patch, reply with the single "
    "word CANNOT."
)


@dataclass
class Iteration:
    iteration: int
    action: str  # "pass" | "failed" | "patched" | "cannot_fix" | "stop_identical" | "stop_conflict"
    exit_code: int | None = None
    fix_note: str = ""
    bytes_applied: int = 0


@dataclass
class FixLoopReport:
    command: str
    fixed: bool
    iterations: list[Iteration] = field(default_factory=list)
    tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "fixed": self.fixed,
            "attempts": [
                {
                    "iteration": it.iteration,
                    "action": it.action,
                    "exit_code": it.exit_code,
                    "fix_note": it.fix_note,
                    "bytes_applied": it.bytes_applied,
                }
                for it in self.iterations
            ],
        }


def _run(command: str, cwd: Path, tail_bytes: int) -> tuple[int, str]:
    """Run the test command as ``shell_exec`` does (ADR-101 D-4).

    The scrubbed tool env, its own process group killed whole on timeout and
    the local tool sandbox when it is on; :class:`SandboxError` propagates
    (the tool result is that error, nothing runs unconfined).
    """
    from .tools import _run_captured, _tool_spawn_env

    run = _run_captured(
        command, shell=True, cwd=str(cwd), timeout=RUN_TIMEOUT_S, env=_tool_spawn_env()
    )
    if run.timed_out:
        return 124, f"test-fix-loop: command timed out after {RUN_TIMEOUT_S}s"
    blob = run.stdout + ("\n" if run.stdout and run.stderr else "") + run.stderr
    if len(blob) > tail_bytes:
        blob = blob[: tail_bytes // 2] + "\n…[truncated]…\n" + blob[-tail_bytes // 2 :]
    return (1 if run.returncode is None else run.returncode), blob


def _load_files(paths: list[str], workdir: Path) -> str:
    chunks: list[str] = []
    for rel in paths:
        target = (workdir / rel).resolve()
        if not str(target).startswith(str(workdir.resolve())) or not target.is_file():
            return ""
        try:
            body = target.read_text(errors="replace")
        except OSError:
            return ""
        if len(body) > MAX_FILE_CONTEXT_BYTES:
            body = body[:MAX_FILE_CONTEXT_BYTES] + "\n…[file truncated]…"
        chunks.append(f"# {rel}\n{body}")
    return "\n\n".join(chunks)


def _prompt_for_patch(command: str, tail: str, file_context: str, iteration: int) -> str:
    return (
        f"Iteration {iteration} of a test-fix loop.\n"
        f"Command: {command}\n"
        f"Tail output (truncated):\n{tail}\n\n"
        f"Files available for patching:\n{file_context}\n\n"
        "Reply with ONLY the unified diff patch, or the single word CANNOT."
    )


def _extract_patch(text: str) -> str | None:
    """Pull a unified diff out of an LLM reply; None if no plausibly-valid patch."""
    cleaned = text.strip()
    if cleaned.upper().startswith("CANNOT"):
        return None
    if cleaned.startswith("```"):
        inner = cleaned.split("```", 2)[1]
        cleaned = inner.split("```", 1)[0].strip()
        if cleaned.startswith(("diff", "patch")) and "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1].strip()
    if "--- " not in cleaned or "+++ " not in cleaned or "@@" not in cleaned:
        return None
    start = cleaned.index("--- ")
    return cleaned[start:]


def _apply_llm_patch(patch_text: str, workdir: Path) -> tuple[bool, int, str]:
    """Apply an LLM-proposed patch. Returns (applied, bytes, detail)."""
    result = apply_patch(patch_text, workdir=workdir)
    resolved = sum(f["added"] + f["removed"] for f in result.files)
    if result.applied and not result.conflicts:
        return True, resolved, "applied"
    detail = "; ".join(f"{c['path']}: {c['detail']}" for c in result.conflicts[:2])
    return False, 0, f"conflict: {detail or 'invalid patch'}"


def _llm_propose(client: "Client", prompt: str) -> str | None:
    try:
        resp = client.chat(
            prompt,
            system=_SYSTEM,
            temperature=0.0,
            max_tokens=MAX_LLM_PROPOSAL_CHARS // 2,
            task="coding",
        )
        text = getattr(resp, "content", None) or str(resp)
    except Exception as e:  # noqa: BLE001 — upstream flake handled at loop layer
        return f"CANNOT  # llm call failed: {e}"
    return text


def _attempt_fix(
    client: "Client",
    command: str,
    files: list[str],
    tail: str,
    workdir: Path,
    index: int,
) -> Iteration:
    """Try one edit→fix iteration; return the Iteration outcome (any action)."""
    context = _load_files(files, workdir)
    if not context:
        return Iteration(index, action="cannot_fix", fix_note="files unreadable")
    proposal = _llm_propose(client, _prompt_for_patch(command, tail, context, index))
    patch_text = _extract_patch(proposal) if proposal else None
    if not patch_text:
        return Iteration(index, action="cannot_fix", fix_note=(proposal or "")[:200])
    ok, applied_bytes, note = _apply_llm_patch(patch_text, workdir)
    if not ok:
        return Iteration(index, action="stop_conflict", fix_note=note)
    return Iteration(index, action="patched", fix_note=note, bytes_applied=applied_bytes)


def run_test_fix_loop(
    client: "Client",
    command: str,
    files: list[str],
    *,
    cwd: str | None = None,
    max_iterations: int = 4,
    tail_bytes: int = 8000,
) -> FixLoopReport:
    """Drive edit→test→edit inside one tool call until pass or limits hit."""
    workdir = Path(cwd or ".").resolve()
    report = FixLoopReport(command=command, fixed=False)
    if not files:
        report.iterations.append(
            Iteration(0, action="cannot_fix", fix_note="no files supplied for patching")
        )
        return report
    last_hash = ""
    for index in range(1, max_iterations + 1):
        exit_code, tail = _run(command, workdir, tail_bytes)
        report.tail = tail
        if exit_code == 0:
            report.iterations.append(Iteration(index, action="pass", exit_code=0))
            report.fixed = True
            return report
        digest = hashlib.sha256(tail.encode()).hexdigest()
        if digest == last_hash:
            report.iterations.append(Iteration(index, action="stop_identical", exit_code=exit_code))
            return report
        last_hash = digest
        iteration = _attempt_fix(client, command, files, tail, workdir, index)
        iteration.exit_code = exit_code
        report.iterations.append(iteration)
        if iteration.action != "patched":
            return report
    report.iterations.append(
        Iteration(max_iterations, action="cannot_fix", fix_note="max_iterations reached")
    )
    return report


def render_report(report: FixLoopReport, output_limit: int = 2000) -> str:
    """Machine-parseable summary: `test_fix_loop` marker + JSON + brief tail."""
    body = json.dumps(report.to_dict(), indent=1)
    status = "fixed" if report.fixed else "not_fixed"
    tail = report.tail[: output_limit // 2] if report.tail else ""
    return f"test_fix_loop status={status}\n{body}\n\n[last tail]\n{tail}".strip()
