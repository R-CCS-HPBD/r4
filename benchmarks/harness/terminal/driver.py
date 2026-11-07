"""In-container Terminal-Bench driver for the R4 Companion agent loop.

Standalone by design: stdlib + the installed ``r4`` package ONLY. Uploaded to
the Harbor task container and executed as a plain script, so it must never spend
imports on ``benchmarks.*`` (the repo is not present in the container).

Driver protocol: the last stdout line is a JSON payload:

    python driver.py --prompt-file PROMPT.txt --workdir DIR [--model MODEL]

SECURITY: the driver runs the companion in PermissionMode.TRUST — tools
execute without interactive approval. Only run inside the disposable Harbor
task container, against pinned task definitions.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

HOST_GATEWAY_PLACEHOLDER = "__R4BENCH_HOST_GATEWAY__"  # = support.HOST_GATEWAY_PLACEHOLDER

#: Nobody answers a benchmark task (= support.DRIVER_ENV_DEFAULTS). Set with
#: setdefault, so harbor ``--ae`` values win:
#: - R4_AUTONOMOUS: stuck-loop guards nudge the model to change approach instead
#:   of ending the turn (r4 agent, 2026-10-06);
#: - R4_ASK_USER_MODE=auto: ask_user gets r4's own auto-answer instead of a
#:   question nobody reads (B5, 2026-10-24; the existing r4 setting, plan H12).
UNATTENDED_ENV = {"R4_AUTONOMOUS": "1", "R4_ASK_USER_MODE": "auto"}


def _set_unattended_defaults() -> None:
    for name, value in UNATTENDED_ENV.items():
        os.environ.setdefault(name, value)


def _default_gateway() -> str | None:
    """This container's default gateway (the bench host), from /proc/net/route."""
    try:
        lines = Path("/proc/net/route").read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000":
            raw = int(fields[2], 16)
            return ".".join(str((raw >> shift) & 0xFF) for shift in (0, 8, 16, 24))
    return None


def _resolve_host_gateway() -> None:
    """Point the decision-model source at the bench host (lane: nimble_proxy.py)."""
    sources = Path.home() / ".config" / "r4" / "sources.json"
    try:
        text = sources.read_text()
    except OSError:
        return
    gateway = _default_gateway()
    if HOST_GATEWAY_PLACEHOLDER in text and gateway:
        sources.write_text(text.replace(HOST_GATEWAY_PLACEHOLDER, gateway))


def _count_calls(session: Any, name: str, counter: dict[str, int]) -> None:
    """Count calls to one tool by wrapping its execute (diagnostics only)."""
    for tool in getattr(session, "tools", None) or []:
        if tool.get("name") == name and callable(tool.get("execute")):
            inner = tool["execute"]

            def counted(*args: Any, _inner: Any = inner, **kwargs: Any) -> Any:
                counter[name] = counter.get(name, 0) + 1
                return _inner(*args, **kwargs)

            tool["execute"] = counted


def _decision_log(session: Any) -> list[dict[str, Any]]:
    return list((getattr(session, "metadata", None) or {}).get("decision_log") or [])


def _median_ms(log: list[dict[str, Any]], key: str) -> int | None:
    values = sorted(e[key] for e in log if isinstance(e.get(key), int))
    return values[len(values) // 2] if values else None


#: Gate entries that never reached the decision model (r4 decisions/harness.py, H13).
_NO_CALL_OUTCOMES = ("skipped", "cached")


def _gate_counts(gate: list[dict[str, Any]]) -> dict[str, int]:
    """Tool-gate entries by outcome: model calls, skips (coalesced counts), cache
    hits, and the escalations (asked or cached) that added a question."""
    counts = dict.fromkeys(
        ("tool_gate_calls", "tool_gate_skipped", "tool_gate_cached", "tool_gate_escalations"), 0
    )
    for entry in gate:
        outcome = entry.get("outcome")
        if outcome == "skipped":
            counts["tool_gate_skipped"] += int(entry.get("count") or 1)
        elif outcome == "cached":
            counts["tool_gate_cached"] += 1
        else:  # asked, error, or an entry logged before outcomes existed
            counts["tool_gate_calls"] += 1
        counts["tool_gate_escalations"] += bool(entry.get("escalated"))
    return counts


def _decision_summary(session: Any, counter: dict[str, int]) -> dict[str, Any]:
    log = _decision_log(session)
    called = [e for e in log if e.get("outcome") not in _NO_CALL_OUTCOMES]
    errors = [e["error"] for e in log if e.get("error")]
    return {
        "calls": len(called),
        "errors": len(errors),
        **_gate_counts([e for e in log if e.get("purpose") == "tool_gate"]),
        "latency_ms_median": _median_ms(log, "latency_ms"),
        "gate_ms_median": _median_ms(called, "gate_ms"),
        "decision_evaluate_calls": counter.get("decision_evaluate", 0),
        "first_error": next(iter(errors), None),
    }


def drive(prompt_file: Path, workdir: Path, model: str | None) -> dict[str, Any]:
    """Run one task instruction through CompanionSession; return a payload."""
    os.chdir(workdir)
    _set_unattended_defaults()
    _resolve_host_gateway()

    # Local imports keep the module importable (and testable) without r4.
    from r4.client import Client
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession

    client = Client()
    session = CompanionSession(
        session_id=f"tb-{int(time.time())}",
        name="terminal-bench",
        client=client,
        model=model,
        permission_mode=PermissionMode.TRUST,
        # No endpoint churn mid-task; a bench run must not wait on provisioning.
        endpoint_refresh_interval_sec=86_400,
    )
    # Bench driver: pre-approve every tool call for the duration of a task.
    # Without a callback the permission engine (ADR-035) fails closed, killing
    # every tool while the payload still reports "success".
    session.permissions.set_approval_callback(lambda *_args, **_kwargs: True)
    # The ADR-035 server trust allowlist cannot be reached from a task
    # container, so every TRUST call fell to explicit approval and the
    # decision gate (which sits on the auto-approve path) never ran. Drop the
    # unreachable checker so TRUST auto-approves as with a reachable
    # allowlist; outcomes are unchanged (the callback approves everything).
    session.permissions.set_trust_checker(None)
    counter: dict[str, int] = {}
    _count_calls(session, "decision_evaluate", counter)
    prompt = prompt_file.read_text()
    started = time.monotonic()
    response = session.send(prompt)
    elapsed = time.monotonic() - started
    usage = session.usage_summary.to_dict()
    # Diagnostics: what the model actually saw (skill filter narrows the
    # builtin catalog when active skills carry tool whitelists).
    try:
        skills_active = sorted(skill.name for skill in session.skills.list_active())
        tools_offered = sorted(session.skills.active_tool_names())
    except Exception:  # noqa: BLE001 — diagnostics never break a run
        skills_active, tools_offered = [], []
    try:
        import yaml

        yaml_version = yaml.__version__
    except Exception:  # noqa: BLE001
        yaml_version = None
    # Honesty: a tool-denied session is NOT a success (SWE lane precedent).
    payload_status = (
        "failed" if "[tool_contract:permission_denied" in (response or "") else "success"
    )
    transcript = getattr(session, "_transcript", None)
    return {
        "status": payload_status,
        "response_text": response,
        # W6: the session's JSONL transcript; r4_agent downloads it per trial.
        "transcript_path": str(transcript.path) if transcript is not None else None,
        "metrics": {
            "wallclock_sec": round(elapsed, 3),
            "llm_calls": usage["llm_calls"],
            "total_tokens": usage["total_tokens"],
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "compaction_calls": usage["compaction_calls"],
            "subagent_calls": usage["subagent_calls"],
            "skills_active": skills_active,
            "n_skill_tools_offered": len(tools_offered),
            "yaml_version": yaml_version,
            "decision_model": getattr(client.config, "decision_model", ""),
            "decisions": _decision_summary(session, counter),
        },
        "error": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r4-companion-terminal-bench-driver")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--result-file",
        default=None,
        help="optional path to persist the full JSON payload (stdout keeps the verdict line too)",
    )
    args = parser.parse_args(argv)
    result_dir = Path(args.result_file).parent if args.result_file else None
    if result_dir is not None and result_dir.is_dir() and not os.environ.get("R4_TRANSCRIPT_PATH"):
        # W6: keep the transcript beside the result so one download dir holds both
        # (never create the dir: an unusable result path stays an error in the payload).
        os.environ["R4_TRANSCRIPT_PATH"] = str(result_dir / "transcript.jsonl")
    try:
        payload = drive(Path(args.prompt_file), Path(args.workdir), args.model)
    except Exception as exc:  # noqa: BLE001 — bench isolates agent failures
        payload = {"status": "error", "response_text": "", "metrics": {}, "error": str(exc)}
    if args.result_file:
        _write_result_file(Path(args.result_file), payload)
    print(json.dumps(payload))
    return 0


def _write_result_file(path: Path, payload: dict[str, Any]) -> None:
    """Persist the payload; a failed write is recorded in it, never fatal: the
    verdict line on stdout is the protocol (B1, 2026-10-05: a root-owned bench
    dir under a non-root agent user killed the line with a PermissionError)."""
    try:
        path.write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        payload["result_file_error"] = f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    raise SystemExit(main())
