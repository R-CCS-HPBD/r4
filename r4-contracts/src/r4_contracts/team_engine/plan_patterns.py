"""Quality patterns and nested plans, expanded at compile time (ADR-092 D-4).

A ``pattern`` step is sugar: it becomes primitive steps before validation,
so the interpreter runs only primitives and a pattern's agent bound is the
bound of what it expands to. Votes and scores are counted by ``reduce``
code, never judged by a model. Every pattern checks its own keys first, so a
bad value is reported on the step the author wrote, not on a step it would
have expanded to.

``adversarial_verify`` (slice 1)
    ``skeptics`` identical skeptics (1-9, default 3) try to refute each item
    of ``over`` (at most ``max_items``) and vote ``{<field>: bool, reason}``.
    A strict majority of valid refutations kills an item, a strict majority
    of rejections keeps it, and a tie or fewer than ⌈n/2⌉ valid votes leaves
    it undecided. Expands to ``<id>__votes`` (a map: a fan-out, then a
    ``majority``) and ``<id>`` (a ``partition`` into ``survivors``,
    ``killed``, ``undecided``). Bound: ``max_items × skeptics``.

``perspective_verify`` (slice 2)
    The same vote, but each item is checked once from every one of
    ``perspectives`` (1-9 distinct names, e.g. statistics, methodology,
    reproducibility) instead of by identical skeptics: diversity of lens,
    not repetition. The prompt must read ``{{perspective}}``. The verdict is
    the same counted majority with the same quorum, so one lens cannot pass
    or kill an item alone. Expands like ``adversarial_verify`` with a
    ``values`` fan-out. Bound: ``max_items × len(perspectives)``.

``judge_panel`` (slice 2)
    ``judges`` judges (1-9, default 3) score every candidate of ``over`` (at
    most ``max_items``) with an integer 0-10 and a reason. A candidate's
    score is the mean of its valid scores, null without a quorum of ⌈n/2⌉.
    The candidates are ranked by code: every scored candidate within
    ``margin`` (default 1) of the best is competing. One competing candidate
    is the ``winner``; two or more are a near-tie, which raises
    ``competing_hypotheses`` (the ADR-066 class) and picks nothing. Expands
    to ``<id>__scores`` (a map: a fan-out, then a ``mean``), ``<id>`` (a
    ``rank``), and, unless ``on_tie`` is ``report``, ``<id>__tie``: a gate of
    class ``competing_hypotheses`` raised only on a near-tie, so a human
    decides whether the run goes on without a pick. Bound:
    ``max_items × judges``.

``completeness_critic`` (slice 2)
    ``critics`` critics (1-9, default 2) read the result so far (``over``)
    and list what is missing (at most ``max_missing`` items each). Loop until
    dry: a later round shows them everything found so far and runs again,
    at most ``rounds`` times (1-8, default 2), and stops early when a round
    finds nothing outside the seen-set (``key`` names an item's identity).
    Expands to one ``repeat`` (``<id>``) seeded with ``over`` whose body is a
    fan-out (``<id>__critic``) and a ``concat`` (``<id>__gaps``); its output
    is ``{items, iterations, dry, stopped}``, ``items`` holding the new gaps
    only. Bound: ``rounds × critics``.

``multi_modal_sweep`` (slice 2)
    One question asked through several ``modes`` (2-6), each a profile
    (``use``: its tools and instructions, e.g. code reading, project
    knowledge, the literature), a ``model`` or a ``prompt`` variant; a mode
    sets at least one of them. Every mode answers a list (at most
    ``max_results`` items) and the lists are merged, deduplicated by
    ``key`` when one is named. Expands to one agent step per mode
    (``<id>__<mode>``, run one after another) and a ``concat`` over all of
    them (then a ``dedup_by``, with ``key``). Bound: ``len(modes)``.

A ``plan`` step (slice 2) names a built-in ``template`` and its ``args``.
The template is loaded through the caller's loader, bound with the args
(static values; a string may read the parent's ``{{params.x}}``), expanded
and validated on its own, one level deep (``MAX_PLAN_DEPTH``), then inlined
as the step's ``program``. Its profiles join the parent's as
``<step>__<profile>``; its budget and concurrency do not apply, the
parent's govern the one run (D-7).

Stdlib only (INV-10).
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .plan import (
    _PROGRAM_KEYS,
    _TEMPLATE_RE,
    MAX_FANOUT,
    MAX_MAP_ITEMS,
    MAX_PLAN_DEPTH,
    MAX_REPEAT_ITERATIONS,
    PlanProblem,
    _int_in,
    _is_number,
    _valid_id,
    render,
    template_roots,
)

TemplateLoader = Callable[[str], dict[str, Any]]
Expansion = tuple[list[dict[str, Any]], list[PlanProblem]]

MAX_MODES = 6
MAX_PERSPECTIVE_CHARS = 80
_TEXT_ERRORS = (KeyError, ValueError, OSError)

_ADVERSARIAL_KEYS = frozenset(
    {
        "id",
        "kind",
        "pattern",
        "over",
        "use",
        "prompt",
        "skeptics",
        "max_items",
        "field",
        "label",
        "log",
    }
)
_PERSPECTIVE_KEYS = (_ADVERSARIAL_KEYS - {"skeptics"}) | {"perspectives"}
_JUDGE_KEYS = frozenset(
    {
        "id",
        "kind",
        "pattern",
        "over",
        "use",
        "prompt",
        "judges",
        "max_items",
        "margin",
        "on_tie",
        "label",
        "log",
    }
)
_CRITIC_KEYS = frozenset(
    {
        "id",
        "kind",
        "pattern",
        "over",
        "use",
        "prompt",
        "critics",
        "rounds",
        "max_missing",
        "key",
        "item_schema",
        "label",
        "log",
    }
)
_SWEEP_KEYS = frozenset(
    {
        "id",
        "kind",
        "pattern",
        "prompt",
        "modes",
        "max_results",
        "item_schema",
        "key",
        "label",
        "log",
    }
)
_MODE_KEYS = frozenset({"name", "use", "model", "prompt"})
_PLAN_RAW_KEYS = frozenset({"id", "kind", "template", "args", "label", "log"})
# The vote field becomes a schema property beside ``reason``, so it is a plain
# identifier and never ``reason`` itself (that would erase the boolean).
_VOTE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "enum": list(range(11))},
        "reason": {"type": "string", "maxLength": 800},
    },
    "required": ["score", "reason"],
    "additionalProperties": False,
}
_DEFAULT_ITEM_SCHEMA = {"type": "string", "maxLength": 400}
_CRITIC_TAIL = (
    "\n\nAlready covered (do not list these again):\n{{seen|json}}\n"
    "List only what is still missing. An empty list means nothing is missing."
)


@dataclass
class _Context:
    """What one expansion carries: the template loader, the MCP declarations
    a nested template is validated with, how deep it is, the profiles inlined
    templates bring, and every problem found."""

    templates: TemplateLoader | None = None
    capabilities: dict[str, Any] | None = None
    depth: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    profiles: dict[str, Any] = field(default_factory=dict)
    problems: list[PlanProblem] = field(default_factory=list)


def expand_patterns(
    plan: dict[str, Any],
    *,
    templates: TemplateLoader | None = None,
    tool_capabilities: dict[str, Any] | None = None,
    depth: int = 0,
) -> tuple[dict[str, Any], list[PlanProblem]]:
    """``plan`` with every pattern step replaced by its primitives and every
    ``plan`` step's template inlined (a repeat's body included)."""
    out = copy.deepcopy(plan)
    bound = out.get("_bound_params")
    ctx = _Context(templates, tool_capabilities, depth, bound if isinstance(bound, dict) else {})
    phases = out.get("phases")
    for p_index, phase in enumerate(phases if isinstance(phases, list) else []):
        if isinstance(phase, dict) and isinstance(phase.get("steps"), list):
            phase["steps"] = _expand_steps(phase["steps"], f"phases[{p_index}].steps", ctx)
    _merge_profiles(out, ctx)
    return out, ctx.problems


def _expand_steps(steps: list[Any], path: str, ctx: _Context) -> list[Any]:
    out: list[Any] = []
    for s_index, step in enumerate(steps):
        step_path = f"{path}[{s_index}]"
        kind = step.get("kind") if isinstance(step, dict) else None
        if kind == "pattern":
            expanded, problems = _expand_one(step, step_path)
            out.extend(expanded)
            ctx.problems.extend(problems)
        elif kind == "plan":
            out.extend(_expand_plan(step, step_path, ctx))
        else:
            ctx.problems.extend(_reserved_id_problems(step, step_path))
            if kind == "repeat" and isinstance(step.get("steps"), list):
                step["steps"] = _expand_steps(step["steps"], f"{step_path}.steps", ctx)
            out.append(step)
    return out


def _reserved_id_problems(step: Any, path: str) -> list[PlanProblem]:
    """``<id>__<suffix>`` ids are minted by expansion only. Checked here,
    where user steps and expanded steps are still told apart, so a user step
    can neither squat on a pattern's internal id nor pass as one."""
    step_id = step.get("id") if isinstance(step, dict) else None
    if isinstance(step_id, str) and "__" in step_id:
        return [
            PlanProblem(f"{path}.id", "bad_id", "'__' is reserved for ids a pattern expands to")
        ]
    return []


def _expand_one(step: dict[str, Any], path: str) -> Expansion:
    name = step.get("pattern")
    expander = PATTERNS.get(name) if isinstance(name, str) else None
    if expander is None:
        return [], [PlanProblem(f"{path}.pattern", "unknown_pattern", f"one of {sorted(PATTERNS)}")]
    return expander(step, path)


# ── shared checks ───────────────────────────────────────────────────────────


def _unknown(step: dict[str, Any], allowed: frozenset[str], path: str) -> list[PlanProblem]:
    return [
        PlanProblem(f"{path}.{key}", "unknown_key", f"unknown key {key!r}")
        for key in sorted(set(step) - allowed)
    ]


def _texts_problems(
    step: dict[str, Any], path: str, keys: tuple[str, ...], pattern: str
) -> list[PlanProblem]:
    """Each of ``keys`` a non-empty string, and ``id`` a plain step id."""
    problems: list[PlanProblem] = []
    for key in keys:
        if not isinstance(step.get(key), str) or not step[key].strip():
            problems.append(PlanProblem(f"{path}.{key}", "required", f"{pattern} needs {key}"))
    step_id = step.get("id")
    if isinstance(step_id, str) and step_id.strip() and not _valid_id(step_id):
        problems.append(
            PlanProblem(
                f"{path}.id", "bad_id", "a step id matches [a-z][a-z0-9_]* ('__' is reserved)"
            )
        )
    return problems


def _range_problem(
    step: dict[str, Any], key: str, default: Any, high: int, path: str
) -> list[PlanProblem]:
    if _int_in(step.get(key, default), 1, high):
        return []
    code = "required" if default is None else "out_of_range"
    return [PlanProblem(f"{path}.{key}", code, f"1-{high}")]


def _field_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    field_name = step.get("field", "refuted")
    if isinstance(field_name, str) and _VOTE_FIELD_RE.match(field_name) and field_name != "reason":
        return []
    return [PlanProblem(f"{path}.field", "bad_value", "an identifier other than 'reason'")]


def _optional_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    """``key`` (a field path) and ``item_schema`` (an object) when given."""
    problems: list[PlanProblem] = []
    if step.get("key") is not None and not isinstance(step.get("key"), str):
        problems.append(PlanProblem(f"{path}.key", "bad_value", "key is a field path"))
    if step.get("item_schema") is not None and not isinstance(step.get("item_schema"), dict):
        problems.append(PlanProblem(f"{path}.item_schema", "bad_schema", "a schema object"))
    return problems


def _with_optional(target: dict[str, Any], source: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        if source.get(key):
            target[key] = source[key]
    return target


# ── counted votes: adversarial_verify, perspective_verify ──────────────────


def _vote_steps(step: dict[str, Any], fanout: dict[str, Any], voters: int) -> list[dict[str, Any]]:
    """``<id>__votes`` (a map: the voters per item, then a counted majority)
    and ``<id>`` (a partition into survivors / killed / undecided)."""
    field_name = step.get("field", "refuted")
    votes_id = f"{step['id']}__votes"
    schema = {
        "type": "object",
        "properties": {
            field_name: {"type": "boolean"},
            "reason": {"type": "string", "maxLength": 800},
        },
        "required": [field_name, "reason"],
        "additionalProperties": False,
    }
    voter = _with_optional(
        {"kind": "agent", "prompt": step["prompt"], "fanout": fanout, "schema": schema},
        step,
        "use",
    )
    majority = {
        "kind": "reduce",
        "op": "majority",
        "field": field_name,
        "quorum": math.ceil(voters / 2),
    }
    votes: dict[str, Any] = {
        "id": votes_id,
        "kind": "map",
        "over": step["over"],
        "max_items": step["max_items"],
        "label": step.get("label") or step["id"],
        "stages": [voter, majority],
    }
    split: dict[str, Any] = {
        "id": step["id"],
        "kind": "reduce",
        "op": "partition",
        "input": f"steps.{votes_id}.output",
        "items": step["over"],
        "field": "verdict",
        "buckets": {"survivors": False, "killed": True, "undecided": None},
    }
    return [_with_optional(votes, step, "log"), split]


def _adversarial_verify(step: dict[str, Any], path: str) -> Expansion:
    """N skeptics try to refute each item (see the module docstring).
    Bound: ``max_items × skeptics``."""
    problems = _adversarial_problems(step, path)
    if problems:
        return [], problems
    skeptics = int(step.get("skeptics", 3))
    return _vote_steps(step, {"count": skeptics}, skeptics), []


def _adversarial_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    """Every problem of one ``adversarial_verify`` step, before it expands:
    a bad value here would otherwise raise while building the expansion or
    surface as a confusing problem on a step the author never wrote."""
    problems = _unknown(step, _ADVERSARIAL_KEYS, path)
    problems += _texts_problems(step, path, ("id", "over", "prompt"), "adversarial_verify")
    problems += _range_problem(step, "skeptics", 3, MAX_FANOUT, path)
    problems += _range_problem(step, "max_items", None, MAX_MAP_ITEMS, path)
    return problems + _field_problems(step, path)


def _perspective_verify(step: dict[str, Any], path: str) -> Expansion:
    """One verifier per perspective per item (see the module docstring).
    Bound: ``max_items × len(perspectives)``."""
    problems = _perspective_problems(step, path)
    if problems:
        return [], problems
    lenses = list(step["perspectives"])
    return _vote_steps(step, {"values": lenses, "as": "perspective"}, len(lenses)), []


def _perspective_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    problems = _unknown(step, _PERSPECTIVE_KEYS, path)
    problems += _texts_problems(step, path, ("id", "over", "prompt"), "perspective_verify")
    problems += _range_problem(step, "max_items", None, MAX_MAP_ITEMS, path)
    problems += _field_problems(step, path) + _lenses_problems(step.get("perspectives"), path)
    prompt = step.get("prompt")
    if isinstance(prompt, str) and "perspective" not in template_roots(prompt):
        message = "the prompt reads {{perspective}}; without it every verifier is the same"
        problems.append(PlanProblem(f"{path}.prompt", "bad_value", message))
    return problems


def _lenses_problems(lenses: Any, path: str) -> list[PlanProblem]:
    ok = (
        isinstance(lenses, list)
        and 1 <= len(lenses) <= MAX_FANOUT
        and all(isinstance(x, str) and x.strip() for x in lenses)
        and all(len(x) <= MAX_PERSPECTIVE_CHARS for x in lenses)
        and len(set(lenses)) == len(lenses)
    )
    if ok:
        return []
    message = f"1-{MAX_FANOUT} distinct names, each at most {MAX_PERSPECTIVE_CHARS} characters"
    return [PlanProblem(f"{path}.perspectives", "bad_value", message)]


# ── judge_panel ─────────────────────────────────────────────────────────────


def _judge_panel(step: dict[str, Any], path: str) -> Expansion:
    """Judges score candidates; code ranks them; a near-tie picks nothing and
    raises ``competing_hypotheses`` (see the module docstring)."""
    problems = _judge_problems(step, path)
    if problems:
        return [], problems
    judges = int(step.get("judges", 3))
    scores_id = f"{step['id']}__scores"
    judge = _with_optional(
        {
            "kind": "agent",
            "prompt": step["prompt"],
            "fanout": {"count": judges},
            "schema": copy.deepcopy(_SCORE_SCHEMA),
        },
        step,
        "use",
    )
    mean = {"kind": "reduce", "op": "mean", "field": "score", "quorum": math.ceil(judges / 2)}
    scores = {
        "id": scores_id,
        "kind": "map",
        "over": step["over"],
        "max_items": step["max_items"],
        "label": step.get("label") or step["id"],
        "stages": [judge, mean],
    }
    rank = {
        "id": step["id"],
        "kind": "reduce",
        "op": "rank",
        "input": f"steps.{scores_id}.output",
        "items": step["over"],
        "field": "mean",
        "margin": step.get("margin", 1),
    }
    steps = [_with_optional(scores, step, "log"), rank]
    if step.get("on_tie", "gate") == "gate":
        steps.append(_tie_gate(step))
    return steps, []


def _tie_gate(step: dict[str, Any]) -> dict[str, Any]:
    ref = f"steps.{step['id']}.output.competing"
    prompt = (
        f"The judges of {step['id']} could not separate these candidates (scores within "
        f"{step.get('margin', 1)} of the best): competing hypotheses (ADR-066).\n"
        "{{" + ref + "|json}}\n"
        "Approve to go on with no pick (the result lists them); reject to stop the run here."
    )
    return {
        "id": f"{step['id']}__tie",
        "kind": "gate",
        "class": "competing_hypotheses",
        "when": ref,
        "prompt": prompt,
    }


def _judge_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    problems = _unknown(step, _JUDGE_KEYS, path)
    problems += _texts_problems(step, path, ("id", "over", "prompt"), "judge_panel")
    problems += _range_problem(step, "judges", 3, MAX_FANOUT, path)
    problems += _range_problem(step, "max_items", None, MAX_MAP_ITEMS, path)
    margin = step.get("margin", 1)
    if not _is_number(margin) or not 0 <= margin <= 10:
        problems.append(PlanProblem(f"{path}.margin", "bad_value", "a score margin, 0-10"))
    if step.get("on_tie", "gate") not in ("gate", "report"):
        problems.append(PlanProblem(f"{path}.on_tie", "bad_value", "gate or report"))
    return problems


# ── completeness_critic ─────────────────────────────────────────────────────


def _completeness_critic(step: dict[str, Any], path: str) -> Expansion:
    """Critics list what is missing, round after round until dry (see the
    module docstring). Bound: ``rounds × critics``."""
    problems = _critic_problems(step, path)
    if problems:
        return [], problems
    critic_id, gaps_id = f"{step['id']}__critic", f"{step['id']}__gaps"
    critic = _with_optional(
        {
            "id": critic_id,
            "kind": "agent",
            "prompt": step["prompt"] + _CRITIC_TAIL,
            "label": step.get("label") or step["id"],
            "fanout": {"count": int(step.get("critics", 2))},
            "schema": {
                "type": "array",
                "maxItems": int(step.get("max_missing", 10)),
                "items": copy.deepcopy(step.get("item_schema") or _DEFAULT_ITEM_SCHEMA),
            },
        },
        step,
        "use",
    )
    gaps = {"id": gaps_id, "kind": "reduce", "op": "concat", "input": f"steps.{critic_id}.output"}
    loop = {
        "id": step["id"],
        "kind": "repeat",
        "max_iterations": int(step.get("rounds", 2)),
        "seed": step["over"],
        "collect": f"steps.{gaps_id}.output",
        "steps": [critic, gaps],
    }
    return [_with_optional(loop, step, "key", "log")], []


def _critic_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    problems = _unknown(step, _CRITIC_KEYS, path)
    problems += _texts_problems(step, path, ("id", "over", "prompt"), "completeness_critic")
    problems += _range_problem(step, "critics", 2, MAX_FANOUT, path)
    problems += _range_problem(step, "rounds", 2, MAX_REPEAT_ITERATIONS, path)
    problems += _range_problem(step, "max_missing", 10, MAX_MAP_ITEMS, path)
    return problems + _optional_problems(step, path)


# ── multi_modal_sweep ───────────────────────────────────────────────────────


def _multi_modal_sweep(step: dict[str, Any], path: str) -> Expansion:
    """One question through several modes, the answers merged (see the
    module docstring). Bound: ``len(modes)``."""
    problems = _sweep_problems(step, path)
    if problems:
        return [], problems
    schema = {
        "type": "array",
        "maxItems": int(step.get("max_results", 10)),
        "items": copy.deepcopy(step.get("item_schema") or _DEFAULT_ITEM_SCHEMA),
    }
    base = step.get("label") or step["id"]
    steps = [_mode_step(step, mode, schema, base) for mode in step["modes"]]
    if step.get("log"):
        steps[0]["log"] = step["log"]
    inputs = [f"steps.{s['id']}.output" for s in steps]
    if not step.get("key"):
        return steps + [{"id": step["id"], "kind": "reduce", "op": "concat", "inputs": inputs}], []
    merged = f"{step['id']}__all"
    return steps + [
        {"id": merged, "kind": "reduce", "op": "concat", "inputs": inputs},
        {
            "id": step["id"],
            "kind": "reduce",
            "op": "dedup_by",
            "input": f"steps.{merged}.output",
            "key": step["key"],
        },
    ], []


def _mode_step(
    step: dict[str, Any], mode: dict[str, Any], schema: dict[str, Any], base: str
) -> dict[str, Any]:
    agent = {
        "id": f"{step['id']}__{mode['name']}",
        "kind": "agent",
        "prompt": mode.get("prompt") or step["prompt"],
        "label": f"{base}:{mode['name']}",
        "schema": copy.deepcopy(schema),
    }
    return _with_optional(agent, mode, "use", "model")


def _sweep_problems(step: dict[str, Any], path: str) -> list[PlanProblem]:
    problems = _unknown(step, _SWEEP_KEYS, path)
    problems += _texts_problems(step, path, ("id", "prompt"), "multi_modal_sweep")
    problems += _range_problem(step, "max_results", 10, MAX_MAP_ITEMS, path)
    problems += _optional_problems(step, path)
    modes = step.get("modes")
    if not isinstance(modes, list) or not 2 <= len(modes) <= MAX_MODES:
        return problems + [PlanProblem(f"{path}.modes", "bad_value", f"2-{MAX_MODES} modes")]
    names: list[str] = []
    for index, mode in enumerate(modes):
        problems += _mode_problems(mode, f"{path}.modes[{index}]", names)
    return problems


def _mode_problems(mode: Any, path: str, names: list[str]) -> list[PlanProblem]:
    """A mode: a plain, unique ``name`` and at least one of use, model, prompt."""
    if not isinstance(mode, dict):
        return [PlanProblem(path, "not_object", "a mode is an object")]
    problems = _unknown(mode, _MODE_KEYS, path)
    name = mode.get("name")
    if not _valid_id(name) or name in names:
        problems.append(PlanProblem(f"{path}.name", "bad_id", "a unique id, [a-z][a-z0-9_]*"))
    names.append(str(name))
    if not any(
        isinstance(mode.get(key), str) and mode[key].strip() for key in ("use", "model", "prompt")
    ):
        message = "a mode sets use, model or prompt; otherwise it is the same agent twice"
        problems.append(PlanProblem(path, "bad_value", message))
    return problems


# ── nested plans ────────────────────────────────────────────────────────────


def _expand_plan(step: dict[str, Any], path: str, ctx: _Context) -> list[dict[str, Any]]:
    """The step with its template bound, expanded, validated and inlined."""
    problems = _plan_step_problems(step, path, ctx)
    args, arg_problems = _static_args(step.get("args"), f"{path}.args", ctx.params)
    problems += arg_problems
    if problems or ctx.templates is None:
        ctx.problems.extend(problems)
        return []
    try:
        raw = ctx.templates(step["template"])
    except _TEXT_ERRORS as exc:
        message = str(exc)[:300] or type(exc).__name__
        ctx.problems.append(PlanProblem(f"{path}.template", "unknown_template", message))
        return []
    from .plan import prepare_plan

    program, sub = prepare_plan(
        raw,
        args,
        tool_capabilities=ctx.capabilities,
        templates=ctx.templates,
        depth=ctx.depth + 1,
    )
    if sub:
        where = f"{path}.template[{step['template']}]"
        ctx.problems.extend(PlanProblem(f"{where}.{p.path}", p.code, p.message) for p in sub)
        return []
    inlined, profiles = _inline(program, step["id"])
    ctx.profiles.update(profiles)
    node = {key: step[key] for key in _PLAN_RAW_KEYS if key in step}
    node["args"] = args
    node["program"] = inlined
    return [node]


def _plan_step_problems(step: dict[str, Any], path: str, ctx: _Context) -> list[PlanProblem]:
    problems = _unknown(step, _PLAN_RAW_KEYS, path)
    problems += _texts_problems(step, path, ("id", "template"), "a plan step")
    if ctx.depth >= MAX_PLAN_DEPTH:
        message = f"a nested plan goes {MAX_PLAN_DEPTH} level deep; this one is inside another"
        problems.append(PlanProblem(f"{path}.kind", "nested_too_deep", message))
    elif ctx.templates is None:
        message = "no template source here: a nested plan names a built-in template"
        problems.append(PlanProblem(f"{path}.template", "no_templates", message))
    return problems


def _static_args(
    raw: Any, path: str, params: dict[str, Any]
) -> tuple[dict[str, Any], list[PlanProblem]]:
    """The args, with ``{{params.x}}`` inside strings rendered from the
    parent's bound params. Anything else a string reads would only exist at
    run time, and a template's counts are bound now (D-4): it is refused."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [PlanProblem(path, "not_object", "args maps params to values")]
    out: dict[str, Any] = {}
    problems: list[PlanProblem] = []
    for name, value in raw.items():
        out[name], problem = _static_value(value, f"{path}.{name}", params)
        problems += problem
    return out, problems


def _static_value(value: Any, path: str, params: dict[str, Any]) -> tuple[Any, list[PlanProblem]]:
    if not isinstance(value, str) or "{{" not in value:
        return value, []
    roots = {ref.split(".", 1)[0] for ref, _fmt in _TEMPLATE_RE.findall(value)}
    if roots - {"params"}:
        message = "args are bound before the run: a string may read {{params.x}} only"
        return None, [PlanProblem(path, "dynamic_arg", message)]
    return render(value, {"params": params}, envelope=False), []


def _inline(program: dict[str, Any], prefix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The template as a step's ``program`` and its profiles renamed
    ``<prefix>__<name>`` (every ``use`` and ``reviews`` follows)."""
    agents = program.get("agents") or {}
    rename = {name: f"{prefix}__{name}" for name in agents}
    profiles = {rename[name]: _renamed(profile, rename) for name, profile in agents.items()}
    inlined = {key: copy.deepcopy(value) for key, value in program.items() if key in _PROGRAM_KEYS}
    for part in _agent_parts(inlined):
        if isinstance(part.get("use"), str):
            part["use"] = rename.get(part["use"], part["use"])
    return inlined, profiles


def _renamed(profile: Any, rename: dict[str, str]) -> Any:
    """A copy of ``profile`` whose ``reviews`` name the renamed profiles."""
    copied = copy.deepcopy(profile)
    if isinstance(copied, dict) and isinstance(copied.get("reviews"), list):
        copied["reviews"] = [rename.get(r, r) for r in copied["reviews"]]
    return copied


def _agent_parts(program: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every step and stage of an expanded program that can name a profile."""
    for phase in program.get("phases") or []:
        for step in phase.get("steps") or []:
            yield from _step_parts(step)


def _step_parts(step: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield step
    yield from step.get("stages") or []
    for sub in step.get("steps") or []:
        yield from _step_parts(sub)


def _merge_profiles(out: dict[str, Any], ctx: _Context) -> None:
    """The inlined templates' profiles join the plan's; a name the plan
    already uses is refused rather than overwritten."""
    if not ctx.profiles:
        return
    agents = out.setdefault("agents", {})
    if not isinstance(agents, dict):
        return  # reported by validation
    for name, profile in ctx.profiles.items():
        if name in agents:
            message = f"profile {name!r} is reserved for a nested plan's profile"
            ctx.problems.append(PlanProblem(f"agents.{name}", "reserved_profile", message))
            continue
        agents[name] = profile


PATTERNS: dict[str, Callable[[dict[str, Any], str], Expansion]] = {
    "adversarial_verify": _adversarial_verify,
    "perspective_verify": _perspective_verify,
    "judge_panel": _judge_panel,
    "completeness_critic": _completeness_critic,
    "multi_modal_sweep": _multi_modal_sweep,
}
