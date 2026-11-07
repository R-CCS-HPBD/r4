"""Root-agent prompt tailoring (user bug B3, 2026-09-27).

A teamup session's role prompts were always the built-in templates; the root
agent of the session (PM by default, else the first configured role) should
generate every agent's prompt for THIS project's situation. This module:

- builds one meta-call with the project's real context (name, goal, hardware,
  role specs with their template prompts as baselines);
- parses the answer strictly (``### ROLE: <role>`` sections; any role the
  answer misses keeps its template default — never fabricated);
- writes the parsed prompts into ``config.role_prompts`` and persists the
  snapshot, so both the runtime and the status surface (prompt/prompt_source)
  show what the agents now really run under.
"""

from __future__ import annotations

import re
from typing import Any

from .roles import AgentRole, get_role_spec

_ROLE_BLOCK_RE = re.compile(
    r"###\s*ROLE:\s*([a-zA-Z0-9_:-]+)\s*\n(.*?)(?=\n###\s*ROLE:|\Z)", re.DOTALL
)

_MIN_PROMPT_CHARS = 120


def _role_names(roles: list[AgentRole]) -> list[str]:
    return [r.value for r in roles]


def _meta_system(root_value: str, spec_text: str) -> str:
    return (
        f"You are the root agent (role {root_value.upper()}) of a multi-agent "
        "team. Your job BEFORE the team starts: write each teammate's system "
        "prompt tailored to THIS project's situation. Rules:\n"
        "1. For every role listed under ROLE SPECIFICATIONS, emit exactly one block:\n"
        "   ### ROLE: <role-value>\n"
        "   <the full system prompt that teammate will run under>\n"
        "2. Keep each role's responsibilities from its template baseline, but "
        "make it concrete for THIS project (its name, goal, hardware) —\n"
        "   templates are for generic HPC; tailor them to the work at hand.\n"
        "3. No commentary outside the blocks. No invented roles.\n\n"
        f"ROLE SPECIFICATIONS:\n{spec_text}"
    )


def _spec_baseline(session: Any, roles: list[AgentRole]) -> str:
    """The REAL baseline each role runs under today — the session config's
    role prompt (crew pack or any earlier tailor), falling back to the
    built-in template only when no override exists. Feeding HPC templates
    into every crew's tailor rewrote code/research crews toward HPC-shaped
    prompts (user report 2026-09-28)."""
    parts: list[str] = []
    for role in roles:
        spec = get_role_spec(role)
        current = session.config.role_prompts.get(role.value) or spec.system_prompt
        origin = (
            "crew/spec baseline"
            if session.config.role_prompts.get(role.value)
            else "template baseline"
        )
        parts.append(
            f"[{role.value}] {spec.name}: {spec.description}\n{origin}:\n'''{current[:2000]}'''\n"
        )
    return "\n".join(parts)


def _project_context(session: Any) -> str:
    cfg = session.config
    role_list = ", ".join(_role_names(cfg.roles))
    return (
        f"PROJECT\n"
        f"  team name: {cfg.name}\n"
        f"  goal: {cfg.optimization_goal or '(unset — infer from name and roles)'}\n"
        f"  hardware target: {cfg.hardware_target}\n"
        f"  roles on this team: {role_list}\n"
        f"  base code path: {cfg.base_code_path or '(none)'}"
    )


def parse_tailored_sections(content: str, roles: list[str]) -> dict[str, str]:
    """Strict parser for `### ROLE: x` blocks.

    Only blocks naming a configured role with ≥ _MIN_PROMPT_CHARS of body land.
    Malformed/missing roles are simply absent (callers keep the default).
    """
    out: dict[str, str] = {}
    for match in _ROLE_BLOCK_RE.finditer(content):
        role = match.group(1).strip().lower()
        body = match.group(2).strip()
        if role in roles and len(body) >= _MIN_PROMPT_CHARS:
            out[role] = body
    return out


def _guarded_meta_call(client: Any, *, message: str, meta: str, model: str | None):
    """One guarded tailor meta call → (response, degraded_reason).

    A failed meta call (endpoint down, 503, timeout) must NOT kill the team
    start: the crew-spec baselines in config.role_prompts are honest
    session-authored prompts. The reason rides the payload so every surface
    can name it.
    """
    try:
        return (
            client.chat(message=message, system=meta, temperature=0.3, model=model),
            None,
        )
    except Exception as exc:  # noqa: BLE001 — named degradation, never a dead start
        reason = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
        return None, reason


def _apply_parsed_prompts(session: Any, parsed: dict[str, str]) -> None:
    """Write accepted overrides; persist so resume + status stay truthful."""
    for role_value, prompt in parsed.items():
        session.config.role_prompts[role_value] = prompt
    if parsed:
        session._save_snapshot()


def tailor_role_prompts(session: Any, client: Any, *, model: str | None = None) -> dict[str, Any]:
    """One root-agent meta-call → per-role prompts on ``config.role_prompts``.

    Returns {root, tailored, kept_default, answers_chars[, degraded]}. Any
    role not present in the answer keeps its existing/default prompt (never
    fabricated); a failed meta call degrades to baselines with the reason.
    """
    roles = list(session.config.roles)
    role_values = _role_names(roles)
    root = AgentRole.PM if AgentRole.PM in roles else roles[0]
    if not roles:
        return {"root": None, "tailored": [], "kept_default": [], "answers_chars": 0}

    meta = _meta_system(root.value, _spec_baseline(session, roles))
    response, degraded = _guarded_meta_call(
        client, message=_project_context(session), meta=meta, model=model
    )
    content = str(getattr(response, "content", "") or "")
    parsed = parse_tailored_sections(content, role_values)
    _apply_parsed_prompts(session, parsed)

    kept = [r for r in role_values if r not in parsed]
    result = {
        "root": root.value,
        "tailored": sorted(parsed),
        "kept_default": kept,
        "answers_chars": len(content),
    }
    if degraded is not None:
        result["degraded"] = degraded
    return result
