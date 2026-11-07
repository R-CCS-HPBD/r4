"""Team plans: the declarative orchestration IR (ADR-092 D-1, D-4, D-5).

A plan is JSON. It has phases of steps, plan-local agent profiles,
parameters, a budget and an output map. The step kinds are a closed set:

- ``agent``: one prompt, optionally fanned out into independent replicas.
- ``map``: one chain of stages per item, with no barrier between stages.
- ``reduce``: closed, pure operations.
- ``pattern``: expanded at compile time by ``plan_patterns``.
- ``repeat`` (slice 2): loop-until-dry. Its ``steps`` run again, at most
  ``max_iterations`` times, until the step its ``collect`` path reads
  reports no item outside the seen-set. The body sees ``seen`` (every item
  so far, the ``seed`` included) and ``iteration``.
- ``gate`` (slice 2): a human decision of an ADR-066 class. The engine asks
  through the broker and emits ``human_input_requested``; a gate is never
  auto-approved, and one that is not approved stops the run.
- ``plan`` (slice 2): a built-in template, bound with static ``args`` and
  inlined at compile time, one level deep.
- ``merge_back`` (slice 5): the diffs of earlier isolated steps' workspaces,
  applied to the project root only after a human approves them. An ``agent``
  or ``map`` step's ``isolation`` (``none``, ``worktree`` or ``copy``) runs
  each of its agents in a workspace of its own; a map or fan-out may hold a
  writer only when it is isolated.

Templates are substitution only. Nothing in a plan is evaluated, imported
or executed. Every count is static once parameters bind, so a plan's agent
bound is known before anything is spent.

Stdlib only (INV-10), so a UMRS-hosted interpreter can import the same rules.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

TEAM_PLAN_VERSION = 1

MAX_PLAN_BYTES = 65536
MAX_STEPS = 64
MAX_AGENTS_HARD = 1000
DEFAULT_MAX_AGENTS = 100
MAX_CONCURRENCY = 8
MAX_TOOL_ROUNDS = 12
DEFAULT_TOOL_ROUNDS = 8
MAX_FANOUT = 9
MAX_STAGES = 4
MAX_MAP_ITEMS = 50
MAX_REPEAT_ITERATIONS = 8
MAX_REPEAT_STEPS = 8
MAX_REDUCE_INPUTS = MAX_FANOUT
# A nested ``plan`` step goes one level deep (D-4): a template it names may
# not itself name a template.
MAX_PLAN_DEPTH = 1

# ADR-066 trigger classes: what a gate asks the human about.
# ``physical_action`` and ``direction_change`` are reserved there (named, no
# emitter); a plan author may raise them by hand.
GATE_CLASSES = frozenset({"cost", "competing_hypotheses", "physical_action", "direction_change"})
# The approval a gate asks through: in ``NEVER_AUTO_APPROVE`` on the r4 side,
# so no mode, session approval or remembered pair answers it. It is the
# engine's, never an agent's: a profile may not list it.
GATE_TOOL = "team_plan_gate"
ENGINE_ONLY_TOOLS = frozenset({GATE_TOOL})

# ADR-092 slice 5: workspace isolation. An ``agent`` or ``map`` step may run
# each of its agents in a workspace of its own: ``worktree`` (a ``git worktree``
# on a throwaway branch; the project root must be a git repository) or
# ``copy`` (a size-capped copy of the project tree). ``none`` (the default)
# runs it in the project root. A closed set, checked statically.
ISOLATION_MODES = ("none", "worktree", "copy")
ISOLATED_MODES = frozenset({"worktree", "copy"})
# A ``merge_back`` step names at most this many isolated steps.
MAX_MERGE_FROM = 8
# The class a merge-back's approval carries on the gate path it reuses
# (``team_plan_gate``, never auto-approved). Not an ADR-066 trigger class:
# it labels the card; the ``gate`` step's classes stay GATE_CLASSES.
MERGE_BACK_CLASS = "merge_back"
# The tools that honour a per-agent workspace (r4's companion: relative paths
# and the working directory resolve under it, paths outside it are refused).
# A writer outside this set writes wherever it writes, so isolation cannot
# contain it: it stays refused in a map or fan-out even when the step is
# isolated (fail closed).
WORKSPACE_TOOLS = frozenset(
    {
        "file_read",
        "file_write",
        "file_edit",
        "apply_patch",
        "shell_exec",
        "python_exec",
        "grep_search",
        "glob_search",
        "list_dir",
    }
)

# Writers may not run in parallel unless each runs isolated (ADR-092 D-6, slice 5).
# A writer is any catalog tool that writes the local filesystem or starts a
# local process: replicas of one profile would race on the one shared tree.
# The ADR names the first seven; the rest are the same effects under other
# names (a profile could otherwise swap ``file_write`` for ``apply_patch`` and
# pass the rule). Tools whose writes land in UMRS (workflow runs, uploads,
# knowledge ingest, saved run definitions) are not here: UMRS owns that state
# and its concurrency, and a per-agent workspace would not isolate it anyway.
PARALLEL_UNSAFE_TOOLS = frozenset(
    {
        "file_write",
        "file_edit",
        "shell_exec",
        "python_exec",
        "compile_code",
        "run_code",
        "test_code",
        # Edits files: a unified diff that creates, modifies, deletes, renames.
        "apply_patch",
        # Runs a shell test command and applies model-written patches.
        "test_fix_loop",
        # Creates a project tree on disk.
        "project_scaffold",
        # Writes a downloaded file to a local path.
        "file_download_platform",
        # Spawns language-server processes for the workspace.
        "start_language_servers",
        # Read-modify-write r4's local project state and repoint the client
        # every agent shares.
        "project_use",
        "project_ensure",
    }
)
DEFAULT_PROFILE_TOOLS = ("file_read", "grep_search", "glob_search", "list_dir")

# MCP tools (ADR-092 D-6, slice-1 open item closed 2026-10-11). An MCP tool's
# effect is whatever its server does, so no static name list can say whether
# it writes. It is classified by the capabilities its manifest declares: the
# r4_mcp ``ToolCapability`` flag names a Hatch manifest lists per tool, which
# the custom MCP runner grants and enforces (``ToolDescriptor
# .required_capabilities``). Only a tool every declared capability of which is
# a read is a reader. A tool that declares none, or one this set does not
# know, is a writer: fail closed. ``mcp_invoke`` calls any MCP tool by name,
# so it is always a writer.
MCP_TOOL_PREFIX = "mcp."
MCP_DYNAMIC_TOOLS = frozenset({"mcp_invoke"})
READ_ONLY_TOOL_CAPABILITIES = frozenset(
    {
        "READ_LOCAL_FILE",
        "READ_REMOTE_FILE",
        "READ_DATABASE",
        "READ_ENVIRONMENT",
        "READ_ONLY",  # the r4_mcp preset: the three READ_* file and database flags
        "VIEW_JOB",
    }
)

# ADR-092 D-6: tools a plan agent or a team role is never offered and never
# allowed, whatever its allowlist says. An inner agent is never broader than
# the lead: it cannot start plans, teams or loops (ADR-075 D-2 keeps one driver
# of record), record a human decision, change platform trust or allocation, or
# ask the user (a parallel agent's question has no conversation to land in).
# The set lives here, beside the validator that refuses a profile listing one
# of them, so the plan grammar and the r4 permission fork (which re-exports it
# as ``r4.companion.permissions.TEAM_AGENT_DENY``) read one set and an approval
# card never lists a tool the agent cannot have.
TEAM_AGENT_DENY: frozenset[str] = frozenset(
    {
        "team_plan_templates",
        "team_plan_run",
        "team_plan_result",
        "team_plan_cancel",
        "teamup_start",
        "teamup_run",
        "research_loop_start",
        "research_loop_stop",
        "research_loop_decide",
        "research_session_create",
        "knowledge_graph_decide",
        "workflow_approve_gate",
        "mcp_register_server",
        "mcp_unregister_server",
        "endpoint_provision",
        "endpoint_stop",
        "endpoint_upgrade",
        "pool_up",
        "pool_down",
        "source_add",
        "source_use",
        "source_remove",
        "workflow_upload",
        "hpc_submit",
        # ADR-105 D-4: a compute commit is the session's, approved on its card.
        "job_commit",
        "ask_user",
    }
)

# Values read from these roots came from an agent: they are spliced inside an
# untrusted envelope (ADR-092 D-4) so a downstream agent can tell data from
# its instructions.
UNTRUSTED_ROOTS = frozenset({"steps", "item", "prev", "seen"})

# Template roots the engine puts in scope. A fan-out variable with one of these
# names would shadow it (``as: "steps"`` hides every earlier output).
_SCOPE_ROOTS = frozenset(
    {"params", "steps", "item", "item_index", "prev", "replica", "seen", "iteration"}
)

# Roots the engine binds per map item, for every stage of the chain.
_ITEM_ROOTS = frozenset({"item", "item_index"})

# Where each walk-bound root exists, for the message refusing it elsewhere.
_ROOT_HOMES = {
    "item": "exists only inside a map (its stages and its label)",
    "item_index": "exists only inside a map (its stages and its label)",
    "prev": "exists only from a map's second stage (the first stage's prev is null)",
    "replica": "exists only in a fan-out step's prompt and label",
    "seen": "exists only inside a repeat's steps",
    "iteration": "exists only inside a repeat's steps",
}

# The step kinds each place admits. A map stage is one agent or reduce; a
# repeat body runs the primitives; a nested plan may hold everything but
# another plan (MAX_PLAN_DEPTH). ``pattern`` is admitted where it may be
# written (it is refused as unexpanded by validation).
TOP_KINDS = frozenset({"agent", "map", "reduce", "pattern", "repeat", "gate", "plan", "merge_back"})
# A merge-back writes the project root, so it sits only in the plan's own
# phases, where it can name the isolated steps it merges (slice 5).
NESTED_KINDS = TOP_KINDS - {"plan", "pattern", "merge_back"}
REPEAT_KINDS = frozenset({"agent", "map", "reduce"})
STAGE_KINDS = frozenset({"agent", "reduce"})

# Any spelling of a closing envelope tag inside a value: models read tags
# loosely, so ``</STEP_OUTPUT >`` must not close the envelope either.
_CLOSE_TAG_RE = re.compile(r"<(\s*)/(\s*step_output)", re.IGNORECASE)

_STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*)\s*(?:\|\s*(json|lines|len))?\s*\}\}")
_WHOLE_PARAM_RE = re.compile(r"^\{\{\s*params\.([A-Za-z_]\w*)\s*\}\}$")
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

_TOP_KEYS = frozenset(
    {
        "plan_version",
        "name",
        "description",
        "params",
        "agents",
        "concurrency",
        "budget",
        "phases",
        "output",
    }
)
_PARAM_KEYS = frozenset({"type", "description", "default", "required", "min", "max"})
_PARAM_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}
_PROFILE_KEYS = frozenset({"instructions", "tools", "model", "max_tool_rounds", "reviews"})
_BUDGET_KEYS = frozenset({"max_tokens", "max_agents", "max_wallclock_sec", "max_tool_calls"})
_PHASE_KEYS = frozenset({"id", "title", "steps"})
# A map stage has no id, and its log is not rendered per item (the map's own
# log is): both are refused inside a stage rather than silently ignored. A
# stage runs in its map's workspace, so its isolation is the map's (slice 5).
_STAGE_EXCLUDED = frozenset({"id", "log", "isolation"})
_AGENT_KEYS = frozenset(
    {"id", "kind", "use", "prompt", "schema", "model", "label", "log", "fanout", "isolation"}
)
_MAP_KEYS = frozenset({"id", "kind", "over", "max_items", "stages", "label", "log", "isolation"})
_MERGE_BACK_KEYS = frozenset({"id", "kind", "from", "label", "log"})
_REDUCE_KEYS = frozenset(
    {
        "id",
        "kind",
        "op",
        "input",
        "inputs",
        "items",
        "field",
        "key",
        "quorum",
        "buckets",
        "margin",
        "log",
    }
)
_REPEAT_KEYS = frozenset(
    {"id", "kind", "steps", "max_iterations", "collect", "key", "seed", "label", "log"}
)
_GATE_KEYS = frozenset({"id", "kind", "class", "prompt", "when", "log"})
_PLAN_STEP_KEYS = frozenset({"id", "kind", "template", "args", "program", "label", "log"})
# An inlined template: its own params, phases and outputs. Its profiles join
# the parent's (renamed ``<step>__<profile>``); its budget and concurrency do
# not apply, the parent's govern the one run (D-7).
_PROGRAM_KEYS = frozenset(
    {"plan_version", "name", "description", "params", "_bound_params", "phases", "output"}
)
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "minItems",
        "maxItems",
        "maxLength",
        "additionalProperties",
        "description",
    }
)
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
REDUCE_OPS = frozenset(
    {
        "concat",
        "flatten",
        "dedup_by",
        "count",
        "majority",
        "partition",
        "mean",
        "rank",
        "subtract",
    }
)


@dataclass(frozen=True)
class PlanProblem:
    path: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class PlanInvalid(ValueError):
    """A plan failed validation; ``problems`` lists every problem found."""

    def __init__(self, problems: list[PlanProblem]) -> None:
        super().__init__("; ".join(str(p) for p in problems[:5]))
        self.problems = list(problems)


@dataclass(frozen=True)
class _RefScope:
    """What a template or a path may read where the engine renders it (ADR-092 D-4).

    ``seen`` are the earlier step ids and ``params`` the declared params.
    ``roots`` are the roots the walk binds there: ``item`` and ``item_index``
    in a map, ``prev`` from a map's second stage, and ``replica`` (plus a
    ``values`` fan-out's ``as`` variable) in a fan-out step's prompt and label.
    ``stage`` is the index inside a map, None for a top-level step. A reference
    outside this scope does not fail at run time, it renders null, so the
    validator refuses it before any spend.
    """

    seen: tuple[str, ...]
    params: frozenset[str]
    roots: frozenset[str] = frozenset()
    stage: int | None = None
    # The step kinds this place admits, and how deep in nested plans it is.
    kinds: frozenset[str] = TOP_KINDS
    depth: int = 0
    # Not a reference scope: the MCP tools' declared capabilities, which the
    # parallel-writer rule reads where the walk reaches a step (D-6).
    capabilities: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    # Slice 5: inside an isolated map, every item runs in its own workspace;
    # and the earlier steps of this program that run isolated (what a
    # ``merge_back`` may name).
    isolated: bool = False
    isolated_steps: tuple[str, ...] = ()

    @property
    def in_map(self) -> bool:
        return self.stage is not None

    def bind(self, names: Iterable[str]) -> _RefScope:
        return replace(self, roots=self.roots | frozenset(names))

    def in_stage(self, index: int) -> _RefScope:
        """A map stage: every stage sees the item; only a later stage has ``prev``."""
        names = _ITEM_ROOTS | ({"prev"} if index else frozenset())
        return replace(self.bind(names), stage=index, kinds=STAGE_KINDS)

    def in_repeat(self) -> _RefScope:
        """A repeat body: the seen-set and the iteration are bound there."""
        return replace(self.bind({"seen", "iteration"}), kinds=REPEAT_KINDS)

    def in_program(self, program: dict[str, Any]) -> _RefScope:
        """An inlined template: its own params and steps, nothing of the parent's."""
        return _RefScope(
            (),
            _declared_params(program),
            capabilities=self.capabilities,
            kinds=NESTED_KINDS,
            depth=self.depth + 1,
        )


# ── preparation: bind → expand → validate ───────────────────────────────────


def prepare_plan(
    raw: Any,
    args: dict[str, Any] | None = None,
    *,
    tool_capabilities: dict[str, Any] | None = None,
    templates: Callable[[str], dict[str, Any]] | None = None,
    depth: int = 0,
) -> tuple[dict[str, Any], list[PlanProblem]]:
    """The runnable program for ``raw`` with ``args`` bound, or every problem.

    ``templates`` loads a built-in template by name for a nested ``plan``
    step (the r4 side passes its loader; without one a nested plan is
    refused by name). ``depth`` is how deep this plan is nested (a template
    being inlined is prepared at depth 1, where a ``plan`` step is refused).

    The size limit applies twice (ADR-092 D-4: "at most 64 KB ... after
    expansion"): to the raw plan, before anything is parsed further, and to
    the bound, expanded program, which binding (whole-string ``{{params.x}}``
    values, ``_bound_params``) and pattern expansion both grow. The second
    measure is the program a run writes as ``plan.json``, so an admitted plan
    is never refused as too large when a resume re-validates it.
    ``tool_capabilities`` is :func:`validate_plan`'s.
    """
    problems = _shape_problems(raw)
    if problems:
        return {}, problems
    bound, problems = bind_params(raw, args or {})
    if problems:
        return {}, problems
    from .plan_patterns import expand_patterns

    program, problems = expand_patterns(
        bound, templates=templates, tool_capabilities=tool_capabilities, depth=depth
    )
    if problems:
        return {}, problems
    problems = _size_problems(program, " after binding and expansion")
    if problems:
        return {}, problems
    return program, validate_plan(program, tool_capabilities=tool_capabilities)


def _shape_problems(raw: Any) -> list[PlanProblem]:
    if not isinstance(raw, dict):
        return [PlanProblem("$", "not_object", "a plan is a JSON object")]
    return _size_problems(raw, "")


def _size_problems(plan: dict[str, Any], when: str) -> list[PlanProblem]:
    """UTF-8 bytes of the plan's JSON, measured as the engine measures a
    written program (``json.dumps`` with ``ensure_ascii=False``)."""
    size = len(json.dumps(plan, ensure_ascii=False).encode("utf-8"))
    if size > MAX_PLAN_BYTES:
        message = f"{size} bytes{when}; the limit is {MAX_PLAN_BYTES}"
        return [PlanProblem("$", "too_large", message)]
    return []


# ── parameters ──────────────────────────────────────────────────────────────


def bind_params(
    raw: dict[str, Any], args: dict[str, Any]
) -> tuple[dict[str, Any], list[PlanProblem]]:
    """Bind ``args`` to the declared params; whole-string ``{{params.x}}``
    values anywhere in the plan become the typed value (so counts are static)."""
    decls = raw.get("params") or {}
    problems = _param_decl_problems(decls)
    if problems:
        return {}, problems
    values: dict[str, Any] = {}
    for name, decl in decls.items():
        value, problem = _bind_one(name, decl, args)
        if problem:
            problems.append(problem)
        values[name] = value
    for name in sorted(set(args) - set(decls)):
        problems.append(
            PlanProblem(f"args.{name}", "unknown_param", f"the plan declares no param {name!r}")
        )
    if problems:
        return {}, problems
    bound = _substitute_params(copy.deepcopy(raw), values)
    bound["_bound_params"] = values
    return bound, []


def _param_decl_problems(decls: Any) -> list[PlanProblem]:
    if not isinstance(decls, dict):
        return [PlanProblem("params", "not_object", "params is an object of declarations")]
    problems: list[PlanProblem] = []
    for name, decl in decls.items():
        problems += _decl_problems(decl, f"params.{name}")
    return problems


def _decl_problems(decl: Any, path: str) -> list[PlanProblem]:
    """One declaration. Its type, bounds and default are checked before any
    binding, so a bad declaration is a problem rather than a crash or an
    untyped value reaching a count field (ADR-092 D-4: counts are static)."""
    if not isinstance(decl, dict):
        return [PlanProblem(path, "not_object", "a param declaration is an object")]
    problems = _unknown_keys(decl, _PARAM_KEYS, path)
    kind = decl.get("type")
    if not isinstance(kind, str) or kind not in _PARAM_TYPES:
        return problems + [
            PlanProblem(f"{path}.type", "bad_type", f"one of {sorted(_PARAM_TYPES)}")
        ]
    for key in ("min", "max"):
        if decl.get(key) is not None and not _is_number(decl[key]):
            problems.append(PlanProblem(f"{path}.{key}", "bad_value", f"{key} is a finite number"))
    return problems or _default_problems(decl, f"{path}.default")


def _default_problems(decl: dict[str, Any], path: str) -> list[PlanProblem]:
    default = decl.get("default")
    if default is None:
        return []
    if not _is_kind(default, decl["type"]):
        return [PlanProblem(path, "bad_default", f"the default is not a {decl['type']}")]
    problem = _range_problem(path, default, decl)
    return [PlanProblem(path, "bad_default", problem.message)] if problem else []


def _bind_one(
    name: str, decl: dict[str, Any], args: dict[str, Any]
) -> tuple[Any, PlanProblem | None]:
    """A null argument counts as absent: it takes the default, and it never
    satisfies a required param."""
    path = f"params.{name}"
    supplied = args.get(name)
    if supplied is None:
        if decl.get("required"):
            return None, PlanProblem(path, "required", f"param {name!r} is required")
        return decl.get("default"), None
    value = _coerce(supplied, decl["type"])
    if value is None:
        return None, PlanProblem(path, "bad_value", f"{supplied!r} is not a {decl['type']}")
    return value, _range_problem(path, value, decl)


def _coerce(value: Any, kind: str) -> Any:
    """CLI arguments arrive as strings; coerce them to the declared type.
    ``None`` means the value is not a ``kind``."""
    if _is_kind(value, kind):
        return value
    if not isinstance(value, str):
        return None
    parsed = _COERCERS.get(kind, _no_coercion)(value)
    return parsed if _is_kind(parsed, kind) else None


def _is_kind(value: Any, kind: str) -> bool:
    """``value`` already is a ``kind``: a bool is never a number, and a number
    is finite (NaN would slip past every min/max comparison)."""
    if isinstance(value, bool) and kind in ("integer", "number"):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return isinstance(value, _PARAM_TYPES[kind])


def _is_number(value: Any) -> bool:
    return _is_kind(value, "number")


def _no_coercion(_text: str) -> None:
    return None


def _parse_json_of(kind: type) -> Callable[[str], Any]:
    def parse(text: str) -> Any:
        try:
            value = json.loads(text)
        except ValueError:
            return None
        return value if isinstance(value, kind) else None

    return parse


def _parse_int(text: str) -> int | None:
    return int(text) if re.fullmatch(r"-?\d+", text.strip()) else None


def _parse_number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


_COERCERS: dict[str, Callable[[str], Any]] = {
    "integer": _parse_int,
    "number": _parse_number,
    "boolean": lambda text: {"true": True, "false": False}.get(text.strip().lower()),
    "array": _parse_json_of(list),
    "object": _parse_json_of(dict),
}


def _range_problem(path: str, value: Any, decl: dict[str, Any]) -> PlanProblem | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    low, high = decl.get("min"), decl.get("max")
    if (low is not None and value < low) or (high is not None and value > high):
        return PlanProblem(path, "out_of_range", f"{value} is outside [{low}, {high}]")
    return None


def _substitute_params(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _substitute_params(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute_params(v, values) for v in node]
    if isinstance(node, str):
        match = _WHOLE_PARAM_RE.match(node)
        if match and match.group(1) in values:
            return values[match.group(1)]
    return node


# ── tool classification ─────────────────────────────────────────────────────


def is_mcp_tool(name: str) -> bool:
    """An MCP tool: a first-class ``mcp.<server>.<tool>`` or ``mcp_invoke``."""
    return isinstance(name, str) and (name.startswith(MCP_TOOL_PREFIX) or name in MCP_DYNAMIC_TOOLS)


def is_writer_tool(name: str, capabilities: Iterable[str] | None = None) -> bool:
    """True when ``name`` may write the workspace or start a process (D-6).

    A builtin is a writer when it is in :data:`PARALLEL_UNSAFE_TOOLS`. An MCP
    tool is a writer unless it declares ``capabilities`` and every one is in
    :data:`READ_ONLY_TOOL_CAPABILITIES`: an undeclared (unknown) MCP tool is
    a writer, fail closed. ``capabilities`` are flag names, any case.
    """
    if name in PARALLEL_UNSAFE_TOOLS:
        return True
    if not is_mcp_tool(name):
        return False
    if name in MCP_DYNAMIC_TOOLS:
        return True
    declared = {str(flag).strip().upper() for flag in capabilities or () if str(flag).strip()}
    return not declared or not declared <= READ_ONLY_TOOL_CAPABILITIES


# ── validation ──────────────────────────────────────────────────────────────


def validate_plan(
    program: dict[str, Any], *, tool_capabilities: dict[str, Any] | None = None
) -> list[PlanProblem]:
    """Every problem in a bound, expanded program, at once.

    ``tool_capabilities`` maps an MCP tool name to the capabilities its
    manifest declares (:func:`is_writer_tool`); an MCP tool missing from it is
    a writer, so the parallel-writer rule refuses it in a map or a fan-out.
    """
    problems = _unknown_keys(program, _TOP_KEYS | {"_bound_params"}, "$")
    problems += _header_problems(program)
    profiles = program.get("agents") or {}
    problems += _profiles_problems(profiles)
    if not isinstance(profiles, dict):
        profiles = {}  # reported above; steps then name an unknown profile
    problems += _budget_problems(program)
    scope = _RefScope((), _declared_params(program), capabilities=dict(tool_capabilities or {}))
    walked, seen = _program_problems(program, "", scope, profiles)
    problems += walked
    problems += _output_problems(program.get("output"), replace(scope, seen=seen))
    problems += _bound_problems(program, count_steps(program))
    return problems


def _program_problems(
    program: dict[str, Any], prefix: str, scope: _RefScope, profiles: dict[str, Any]
) -> tuple[list[PlanProblem], tuple[str, ...]]:
    """Every step of ``program``'s phases, each judged against the steps
    before it; and the ids it declares, in order (what its outputs may read)."""
    problems: list[PlanProblem] = []
    seen: list[str] = []
    isolated: list[str] = []
    for path, step in _walk_steps(program, problems, prefix):
        here = replace(scope, seen=tuple(seen), isolated_steps=tuple(isolated))
        problems += _step_problems(step, path, here, profiles)
        if isinstance(step.get("id"), str):
            seen.append(step["id"])
            if step.get("kind") in ("agent", "map") and step_isolation(step) in ISOLATED_MODES:
                isolated.append(step["id"])
    return problems, tuple(seen)


def _declared_params(program: dict[str, Any]) -> frozenset[str]:
    """The names ``{{params.<name>}}`` may read: the plan's declarations.
    Binding gives every declared name a value (null without a default), and
    an undeclared name renders null, so only declared names are references."""
    decls = program.get("params")
    return frozenset(decls) if isinstance(decls, dict) else frozenset()


def _header_problems(program: dict[str, Any]) -> list[PlanProblem]:
    problems: list[PlanProblem] = []
    if program.get("plan_version") != TEAM_PLAN_VERSION:
        problems.append(PlanProblem("plan_version", "bad_version", f"must be {TEAM_PLAN_VERSION}"))
    for key in ("name", "description"):
        if not isinstance(program.get(key), str) or not program.get(key, "").strip():
            problems.append(PlanProblem(key, "required", f"{key} is a non-empty string"))
    concurrency = program.get("concurrency", 4)
    if not _int_in(concurrency, 1, MAX_CONCURRENCY):
        problems.append(PlanProblem("concurrency", "out_of_range", f"1-{MAX_CONCURRENCY}"))
    return problems


def _profiles_problems(profiles: Any) -> list[PlanProblem]:
    if not isinstance(profiles, dict):
        return [PlanProblem("agents", "not_object", "agents is an object of profiles")]
    problems: list[PlanProblem] = []
    for name, profile in profiles.items():
        path = f"agents.{name}"
        if not isinstance(profile, dict):
            problems.append(PlanProblem(path, "not_object", "a profile is an object"))
            continue
        problems += _unknown_keys(profile, _PROFILE_KEYS, path)
        if not isinstance(profile.get("instructions", ""), str):
            problems.append(
                PlanProblem(f"{path}.instructions", "bad_value", "instructions is a string")
            )
        problems += _tools_problems(
            profile.get("tools", list(DEFAULT_PROFILE_TOOLS)), f"{path}.tools"
        )
        problems += _model_problems(profile.get("model"), f"{path}.model")
        problems += _reviews_problems(profile.get("reviews"), name, f"{path}.reviews", profiles)
        rounds = profile.get("max_tool_rounds", DEFAULT_TOOL_ROUNDS)
        if not _int_in(rounds, 1, MAX_TOOL_ROUNDS):
            problems.append(
                PlanProblem(f"{path}.max_tool_rounds", "out_of_range", f"1-{MAX_TOOL_ROUNDS}")
            )
    return problems


def _tools_problems(tools: Any, path: str) -> list[PlanProblem]:
    """A profile's allowlist. A tool in ``TEAM_AGENT_DENY`` is refused here
    (ADR-092 D-6): the fork would refuse every call to it anyway, so listing
    it would only put a tool on the approval card the agent can never use.
    The gate's approval (``ENGINE_ONLY_TOOLS``) is the engine's, never an
    agent's: an agent able to call it could raise its own human question."""
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        return [PlanProblem(path, "bad_value", "tools is a list of tool names")]
    return [
        PlanProblem(
            f"{path}[{index}]",
            "denied_tool",
            f"{tool!r} is never offered to a plan agent (ADR-092 D-6)",
        )
        for index, tool in enumerate(tools)
        if tool in TEAM_AGENT_DENY or tool in ENGINE_ONLY_TOOLS
    ]


def _reviews_problems(
    reviews: Any, name: str, path: str, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """``reviews`` names the profiles whose work this one reviews; when a
    reviewer runs on a model one of them runs on, :func:`independence_warnings`
    says so (a model reviewing its own output is a weaker check)."""
    if reviews is None:
        return []
    if not isinstance(reviews, list) or not all(isinstance(r, str) for r in reviews):
        return [PlanProblem(path, "bad_value", "reviews is a list of profile names")]
    return [
        PlanProblem(f"{path}[{index}]", "unknown_profile", f"no other profile {other!r}")
        for index, other in enumerate(reviews)
        if other == name or other not in profiles
    ]


def _model_problems(model: Any, path: str) -> list[PlanProblem]:
    if model is None:
        return []
    if not isinstance(model, str) or not model.strip():
        return [PlanProblem(path, "bad_value", "a model is a catalog name")]
    if _URL_RE.match(model):
        return [
            PlanProblem(path, "url_model", "models are named, never addressed by URL (ADR-068)")
        ]
    return []


def _budget_problems(program: dict[str, Any]) -> list[PlanProblem]:
    budget = program.get("budget")
    if not isinstance(budget, dict):
        return [PlanProblem("budget", "required", "budget with max_tokens is required")]
    problems = _unknown_keys(budget, _BUDGET_KEYS, "budget")
    if not _int_in(budget.get("max_tokens"), 1, 10**9):
        problems.append(PlanProblem("budget.max_tokens", "required", "a positive integer"))
    if not _int_in(budget.get("max_agents", DEFAULT_MAX_AGENTS), 1, MAX_AGENTS_HARD):
        problems.append(PlanProblem("budget.max_agents", "out_of_range", f"1-{MAX_AGENTS_HARD}"))
    for key in ("max_wallclock_sec", "max_tool_calls"):
        if budget.get(key) is not None and not _int_in(budget.get(key), 1, 10**9):
            problems.append(PlanProblem(f"budget.{key}", "bad_value", "a positive integer"))
    return problems


def _walk_steps(
    program: dict[str, Any], problems: list[PlanProblem], prefix: str = ""
) -> Iterator[tuple[str, dict[str, Any]]]:
    phases = program.get("phases")
    if not isinstance(phases, list) or not phases:
        problems.append(PlanProblem(f"{prefix}phases", "required", "at least one phase"))
        return
    phase_ids: list[str] = []
    for p_index, phase in enumerate(phases):
        yield from _phase_steps(phase, f"{prefix}phases[{p_index}]", phase_ids, problems)


def _phase_steps(
    phase: Any, path: str, phase_ids: list[str], problems: list[PlanProblem]
) -> Iterator[tuple[str, dict[str, Any]]]:
    if not isinstance(phase, dict):
        problems.append(PlanProblem(path, "not_object", "a phase is an object"))
        return
    problems.extend(_unknown_keys(phase, _PHASE_KEYS, path))
    problems.extend(_phase_id_problems(phase.get("id"), f"{path}.id", phase_ids))
    steps = phase.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append(PlanProblem(f"{path}.steps", "required", "at least one step"))
        return
    for s_index, step in enumerate(steps):
        if isinstance(step, dict):
            yield f"{path}.steps[{s_index}]", step
        else:
            problems.append(
                PlanProblem(f"{path}.steps[{s_index}]", "not_object", "a step is an object")
            )


def _phase_id_problems(phase_id: Any, path: str, phase_ids: list[str]) -> list[PlanProblem]:
    """Phase ids name progress events and the ``--from-phase`` resume point
    (ADR-092 D-8), so two phases may not share one."""
    if not _valid_id(phase_id):
        return [PlanProblem(path, "bad_id", "a phase id matches [a-z][a-z0-9_]*")]
    if phase_id in phase_ids:
        return [PlanProblem(path, "duplicate_id", f"phase id {phase_id!r} is used twice")]
    phase_ids.append(phase_id)
    return []


def _step_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    problems = [] if scope.in_map else _id_problems(step, path, scope.seen)
    kind = step.get("kind")
    checker = _KIND_CHECKERS.get(kind) if isinstance(kind, str) else None
    if checker is None or kind not in scope.kinds:
        return problems + [_bad_kind(kind, f"{path}.kind", scope)]
    return problems + checker(step, path, scope, profiles)


def _bad_kind(kind: Any, path: str, scope: _RefScope) -> PlanProblem:
    """Why a kind is refused here; a nested plan past the depth limit by name."""
    if kind == "plan" and scope.depth >= MAX_PLAN_DEPTH:
        message = f"a nested plan goes {MAX_PLAN_DEPTH} level deep; this one is inside another"
        return PlanProblem(path, "nested_too_deep", message)
    return PlanProblem(path, "bad_kind", f"kind is one of {sorted(scope.kinds)} here")


def _id_problems(step: dict[str, Any], path: str, seen: Collection[str]) -> list[PlanProblem]:
    step_id = step.get("id")
    if not _valid_id(step_id) and not _valid_expanded_id(step_id):
        return [
            PlanProblem(
                f"{path}.id", "bad_id", "a step id matches [a-z][a-z0-9_]* ('__' is reserved)"
            )
        ]
    if step_id in seen:
        return [PlanProblem(f"{path}.id", "duplicate_id", f"step id {step_id!r} is used twice")]
    return []


def _agent_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    keys = _AGENT_KEYS - (_STAGE_EXCLUDED if scope.in_map else set())
    problems = _unknown_keys(step, keys, path)
    if not isinstance(step.get("prompt"), str) or not step["prompt"].strip():
        problems.append(PlanProblem(f"{path}.prompt", "required", "an agent step has a prompt"))
    problems += _use_problems(step.get("use"), f"{path}.use", profiles)
    problems += _model_problems(step.get("model"), f"{path}.model")
    if step.get("schema") is not None:
        problems += schema_problems(step["schema"], f"{path}.schema")
    problems += _fanout_problems(step.get("fanout"), f"{path}.fanout")
    if not scope.in_map:
        problems += _isolation_problems(step.get("isolation"), f"{path}.isolation")
    problems += _parallel_problems(step, path, profiles, scope)
    return problems + _agent_ref_problems(step, path, scope)


def _agent_ref_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    """An agent's prompt and label render once per replica, so they may read
    the fan-out's ``replica`` and ``as`` variable. Its ``log`` renders once,
    before the step starts and outside any replica, so it may not: there the
    fan-out roots would render null (ADR-092 D-4)."""
    each = scope.bind(_fanout_roots(step.get("fanout")))
    problems = _template_ref_problems(step.get("prompt"), f"{path}.prompt", each)
    problems += _template_ref_problems(step.get("label"), f"{path}.label", each)
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _fanout_roots(fanout: Any) -> frozenset[str]:
    """The roots one replica binds: ``replica``, and a ``values`` fan-out's
    ``as`` name. A malformed fan-out is reported on its own; its names still
    bind here so the one mistake is not reported again on every reference."""
    if not isinstance(fanout, dict):
        return frozenset()
    name = fanout.get("as")
    return frozenset({"replica", name}) if isinstance(name, str) else frozenset({"replica"})


def _use_problems(use: Any, path: str, profiles: dict[str, Any]) -> list[PlanProblem]:
    if use is None or (isinstance(use, str) and use in profiles):
        return []
    return [PlanProblem(path, "unknown_profile", f"no agent profile {use!r}")]


def _parallel_problems(
    step: dict[str, Any], path: str, profiles: dict[str, Any], scope: _RefScope
) -> list[PlanProblem]:
    """A step that runs as several agents at once (a map stage, a fan-out above
    one) may hold a writer only when each of its agents runs isolated
    (ADR-092 D-6, slice 5): the step's (or its map's) ``isolation`` is
    ``worktree`` or ``copy``, and every writer is one that honours the
    agent's workspace (:data:`WORKSPACE_TOOLS`)."""
    use = step.get("use")
    parallel = scope.in_map or _fanout_count(step) > 1
    if not parallel or not isinstance(use, str) or use not in profiles:
        return []
    isolated = scope.isolated if scope.in_map else step_isolation(step) in ISOLATED_MODES
    return _writer_problems(profiles[use], path, scope.capabilities, isolated=isolated)


def _writer_problems(
    profile: Any, path: str, capabilities: dict[str, Any], *, isolated: bool = False
) -> list[PlanProblem]:
    writers = _profile_writers(profile, capabilities)
    if isolated:
        writers = [name for name in writers if name not in WORKSPACE_TOOLS]
        message = (
            f"{writers} do not honour a per-agent workspace, so isolation cannot "
            "contain them; they may not run in a map or fan-out"
        )
    else:
        message = (
            f"{writers} may not run in a map or fan-out unless each agent runs "
            "isolated (isolation: worktree or copy)"
        )
    return [PlanProblem(path, "parallel_writer", message)] if writers else []


def _profile_writers(profile: Any, capabilities: dict[str, Any]) -> list[str]:
    tools = profile.get("tools", DEFAULT_PROFILE_TOOLS) if isinstance(profile, dict) else ()
    names = {t for t in tools if isinstance(t, str)} if isinstance(tools, (list, tuple)) else set()
    return sorted(name for name in names if is_writer_tool(name, capabilities.get(name)))


def step_isolation(step: dict[str, Any]) -> str:
    """An agent or map step's isolation mode; ``none`` when it names none."""
    value = step.get("isolation", "none") if isinstance(step, dict) else "none"
    return value if isinstance(value, str) else "none"


def _isolation_problems(value: Any, path: str) -> list[PlanProblem]:
    if value is None or value in ISOLATION_MODES:
        return []
    return [PlanProblem(path, "bad_value", f"isolation is one of {list(ISOLATION_MODES)}")]


def _fanout_problems(fanout: Any, path: str) -> list[PlanProblem]:
    if fanout is None:
        return []
    if not _fanout_shape_ok(fanout):
        return [PlanProblem(path, "bad_value", f"{{count: 1-{MAX_FANOUT}}} or {{values, as}}")]
    if fanout.get("as") in _SCOPE_ROOTS:
        return [
            PlanProblem(
                f"{path}.as", "reserved_name", f"{fanout['as']!r} would shadow a template root"
            )
        ]
    return []


def _fanout_shape_ok(fanout: Any) -> bool:
    if not isinstance(fanout, dict):
        return False
    if "count" in fanout:
        return _int_in(fanout["count"], 1, MAX_FANOUT) and set(fanout) == {"count"}
    values = fanout.get("values")
    return (
        isinstance(values, list)
        and 1 <= len(values) <= MAX_FANOUT
        and _valid_id(fanout.get("as"))
        and set(fanout) == {"values", "as"}
    )


def _map_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """A map's ``over`` and ``log`` resolve at plan scope, before any item
    exists. Its ``label`` names the agents of every stage, the first one
    included, so it may read the item but not ``prev`` (null there)."""
    problems = _unknown_keys(step, _MAP_KEYS, path)
    problems += _source_path_problems(step.get("over"), f"{path}.over", scope, required=True)
    problems += _isolation_problems(step.get("isolation"), f"{path}.isolation")
    if not _int_in(step.get("max_items"), 1, MAX_MAP_ITEMS):
        problems.append(PlanProblem(f"{path}.max_items", "required", f"1-{MAX_MAP_ITEMS}"))
    stages = step.get("stages")
    if not isinstance(stages, list) or not 1 <= len(stages) <= MAX_STAGES:
        return problems + [PlanProblem(f"{path}.stages", "bad_value", f"1-{MAX_STAGES} stages")]
    isolated = replace(scope, isolated=step_isolation(step) in ISOLATED_MODES)
    problems += _stages_problems(stages, path, isolated, profiles)
    problems += _template_ref_problems(step.get("label"), f"{path}.label", scope.bind(_ITEM_ROOTS))
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _stages_problems(
    stages: list[Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """Each stage in its own scope: a stage's ``prev`` is the previous stage's
    output, so the first stage has none (ADR-092 D-4)."""
    problems: list[PlanProblem] = []
    for index, stage in enumerate(stages):
        stage_path = f"{path}.stages[{index}]"
        if not isinstance(stage, dict):
            problems.append(PlanProblem(stage_path, "not_object", "a stage is an object"))
            continue
        problems += _step_problems(stage, stage_path, scope.in_stage(index), profiles)
    return problems


def _reduce_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    keys = _REDUCE_KEYS - (_STAGE_EXCLUDED if scope.in_map else set())
    problems = _unknown_keys(step, keys, path)
    op = step.get("op")
    if not isinstance(op, str) or op not in REDUCE_OPS:
        return problems + [PlanProblem(f"{path}.op", "bad_op", f"one of {sorted(REDUCE_OPS)}")]
    if "inputs" in step:
        problems += _reduce_inputs_problems(step, path, scope)
    else:
        problems += _reduce_input_problems(step.get("input"), f"{path}.input", scope)
    problems += _OP_CHECKERS.get(op, _no_op_problems)(step, path, scope)
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _reduce_inputs_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    """``inputs``: several paths, reduced as one list of their values in the
    order written (a sweep's modes, D-4). Never beside ``input``."""
    inputs = step.get("inputs")
    if "input" in step:
        return [PlanProblem(f"{path}.inputs", "bad_value", "input or inputs, not both")]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_REDUCE_INPUTS:
        return [PlanProblem(f"{path}.inputs", "bad_value", f"1-{MAX_REDUCE_INPUTS} paths")]
    problems: list[PlanProblem] = []
    for index, value in enumerate(inputs):
        problems += _source_path_problems(value, f"{path}.inputs[{index}]", scope, required=True)
    return problems


def _reduce_input_problems(value: Any, path: str, scope: _RefScope) -> list[PlanProblem]:
    """A top-level reduce names its input. A stage reduce without one reduces
    ``prev``, which the first stage does not have: it would reduce null."""
    if value is None and scope.stage == 0:
        return [
            PlanProblem(path, "bad_ref", "a first-stage reduce has no prev to read; name its input")
        ]
    return _source_path_problems(value, path, scope, required=not scope.in_map)


def _no_op_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    return []


def _majority_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    problems = []
    if not isinstance(step.get("field"), str):
        problems.append(
            PlanProblem(f"{path}.field", "required", "majority counts one boolean field")
        )
    # An omitted quorum is ⌈n/2⌉ of the votes counted at reduce time (D-4).
    if "quorum" in step and not _int_in(step["quorum"], 1, MAX_FANOUT):
        problems.append(PlanProblem(f"{path}.quorum", "out_of_range", f"1-{MAX_FANOUT}"))
    return problems


def _dedup_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    if isinstance(step.get("key"), str):
        return []
    return [PlanProblem(f"{path}.key", "required", "dedup_by names the key field")]


def _partition_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    problems = _source_path_problems(step.get("items"), f"{path}.items", scope, required=True)
    buckets = step.get("buckets")
    if not isinstance(buckets, dict) or not buckets:
        problems.append(PlanProblem(f"{path}.buckets", "required", "bucket name → verdict value"))
    if not isinstance(step.get("field"), str):
        problems.append(
            PlanProblem(f"{path}.field", "required", "the verdict field to partition on")
        )
    return problems


def _mean_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    """``mean``: the mean of one numeric field over the votes, under the
    same quorum rule as ``majority`` (D-4)."""
    problems = []
    if not isinstance(step.get("field"), str):
        problems.append(PlanProblem(f"{path}.field", "required", "mean averages one number field"))
    if "quorum" in step and not _int_in(step["quorum"], 1, MAX_FANOUT):
        problems.append(PlanProblem(f"{path}.quorum", "out_of_range", f"1-{MAX_FANOUT}"))
    return problems


def _rank_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    """``rank``: candidates (``items``) ordered by a score ``field`` of the
    input; scores within ``margin`` of the best are a near-tie."""
    problems = _source_path_problems(step.get("items"), f"{path}.items", scope, required=True)
    if not isinstance(step.get("field"), str):
        problems.append(PlanProblem(f"{path}.field", "required", "the score field to rank by"))
    margin = step.get("margin", 0)
    if not _is_number(margin) or margin < 0:
        problems.append(PlanProblem(f"{path}.margin", "bad_value", "a number ≥ 0"))
    return problems


def _subtract_problems(step: dict[str, Any], path: str, scope: _RefScope) -> list[PlanProblem]:
    """``subtract``: the input's items whose key is not among ``items``'."""
    problems = _source_path_problems(step.get("items"), f"{path}.items", scope, required=True)
    if step.get("key") is not None and not isinstance(step.get("key"), str):
        problems.append(PlanProblem(f"{path}.key", "bad_value", "key is a field path"))
    return problems


_OP_CHECKERS: dict[str, Callable[[dict[str, Any], str, _RefScope], list[PlanProblem]]] = {
    "majority": _majority_problems,
    "dedup_by": _dedup_problems,
    "partition": _partition_problems,
    "mean": _mean_problems,
    "rank": _rank_problems,
    "subtract": _subtract_problems,
}


def _pattern_left(step: dict[str, Any], path: str, *_rest: Any) -> list[PlanProblem]:
    return [PlanProblem(path, "unexpanded_pattern", "patterns expand before validation")]


# ── slice 2: repeat, gate, nested plan ──────────────────────────────────────


def _repeat_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """A loop-until-dry (D-4): statically bounded by ``max_iterations``.

    Its ``seed`` and ``log`` resolve at the repeat's own scope; its body
    sees the earlier steps plus ``seen`` and ``iteration``, and its body
    ids only inside the body. ``collect`` names the body step whose list
    output is checked against the seen-set after every iteration.
    """
    problems = _unknown_keys(step, _REPEAT_KEYS, path)
    if not _int_in(step.get("max_iterations"), 1, MAX_REPEAT_ITERATIONS):
        problems.append(
            PlanProblem(f"{path}.max_iterations", "required", f"1-{MAX_REPEAT_ITERATIONS}")
        )
    problems += _source_path_problems(step.get("seed"), f"{path}.seed", scope, required=False)
    if step.get("key") is not None and not isinstance(step.get("key"), str):
        problems.append(PlanProblem(f"{path}.key", "bad_value", "key is a field path"))
    body = step.get("steps")
    if not isinstance(body, list) or not 1 <= len(body) <= MAX_REPEAT_STEPS:
        message = f"1-{MAX_REPEAT_STEPS} steps"
        return problems + [PlanProblem(f"{path}.steps", "bad_value", message)]
    inner = scope.in_repeat()
    body_problems, body_ids = _body_problems(body, f"{path}.steps", inner, profiles)
    problems += body_problems
    problems += _collect_problems(step.get("collect"), f"{path}.collect", inner, body_ids)
    problems += _template_ref_problems(step.get("label"), f"{path}.label", scope)
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _body_problems(
    body: list[Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> tuple[list[PlanProblem], tuple[str, ...]]:
    problems: list[PlanProblem] = []
    own: list[str] = []
    for index, sub in enumerate(body):
        sub_path = f"{path}[{index}]"
        if not isinstance(sub, dict):
            problems.append(PlanProblem(sub_path, "not_object", "a step is an object"))
            continue
        problems += _step_problems(
            sub, sub_path, replace(scope, seen=scope.seen + tuple(own)), profiles
        )
        if isinstance(sub.get("id"), str):
            own.append(sub["id"])
    return problems, tuple(own)


def _collect_problems(
    value: Any, path: str, scope: _RefScope, body_ids: tuple[str, ...]
) -> list[PlanProblem]:
    problems = _source_path_problems(
        value, path, replace(scope, seen=scope.seen + body_ids), required=True
    )
    if not problems and _path_root_step(value) not in body_ids:
        problems.append(PlanProblem(path, "bad_ref", "collect reads a step of this repeat"))
    return problems


def _gate_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """A human decision (ADR-066 class): a prompt, and an optional ``when``
    path that raises the gate only when it reads a non-empty value."""
    problems = _unknown_keys(step, _GATE_KEYS, path)
    if step.get("class") not in GATE_CLASSES:
        message = f"one of {sorted(GATE_CLASSES)} (ADR-066)"
        problems.append(PlanProblem(f"{path}.class", "bad_value", message))
    if not isinstance(step.get("prompt"), str) or not step["prompt"].strip():
        problems.append(PlanProblem(f"{path}.prompt", "required", "a gate asks a question"))
    problems += _template_ref_problems(step.get("prompt"), f"{path}.prompt", scope)
    problems += _source_path_problems(step.get("when"), f"{path}.when", scope, required=False)
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _nested_plan_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """An inlined template (``program``), judged in a scope of its own: its
    declared params, its own steps, and the parent's profiles table, where
    expansion put its profiles."""
    problems = _unknown_keys(step, _PLAN_STEP_KEYS, path)
    if not isinstance(step.get("template"), str) or not step["template"].strip():
        problems.append(PlanProblem(f"{path}.template", "required", "a template name"))
    if step.get("args") is not None and not isinstance(step.get("args"), dict):
        problems.append(PlanProblem(f"{path}.args", "not_object", "args maps params to values"))
    program = step.get("program")
    if not isinstance(program, dict):
        message = "a plan step is expanded (its template inlined) before validation"
        return problems + [PlanProblem(f"{path}.program", "unexpanded_plan", message)]
    problems += _unknown_keys(program, _PROGRAM_KEYS, f"{path}.program")
    inner = scope.in_program(program)
    walked, seen = _program_problems(program, f"{path}.program.", inner, profiles)
    problems += walked
    output = program.get("output")
    problems += _output_problems(output, replace(inner, seen=seen), f"{path}.program.output")
    problems += _template_ref_problems(step.get("label"), f"{path}.label", scope)
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _merge_back_problems(
    step: dict[str, Any], path: str, scope: _RefScope, profiles: dict[str, Any]
) -> list[PlanProblem]:
    """An approved merge-back (slice 5): ``from`` names 1-8 earlier isolated
    ``agent`` or ``map`` steps of this plan, whose workspaces' diffs a human
    approves before they are applied to the project root. It renders no
    prompt and starts no agent; nothing in it is executed (D-11)."""
    problems = _unknown_keys(step, _MERGE_BACK_KEYS, path)
    sources = step.get("from")
    if (
        not isinstance(sources, list)
        or not 1 <= len(sources) <= MAX_MERGE_FROM
        or not all(isinstance(s, str) for s in sources)
        or len(set(sources)) != len(sources)
    ):
        message = f"from lists 1-{MAX_MERGE_FROM} distinct step ids"
        return problems + [PlanProblem(f"{path}.from", "bad_value", message)]
    problems += [
        PlanProblem(
            f"{path}.from[{index}]",
            "bad_ref",
            f"{source!r} names no earlier agent or map step of this plan that runs "
            "isolated (isolation: worktree or copy)",
        )
        for index, source in enumerate(sources)
        if source not in scope.isolated_steps
    ]
    problems += _template_ref_problems(step.get("label"), f"{path}.label", scope)
    return problems + _template_ref_problems(step.get("log"), f"{path}.log", scope)


def _path_root_step(path: Any) -> str | None:
    """The step id a ``steps.<id>…`` path reads, or None."""
    if not isinstance(path, str):
        return None
    parts = path.strip().split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "steps" else None


_KIND_CHECKERS: dict[str, Callable[..., list[PlanProblem]]] = {
    "agent": _agent_problems,
    "map": _map_problems,
    "reduce": _reduce_problems,
    "pattern": _pattern_left,
    "repeat": _repeat_problems,
    "gate": _gate_problems,
    "plan": _nested_plan_problems,
    "merge_back": _merge_back_problems,
}


def _source_path_problems(
    value: Any, path: str, scope: _RefScope, *, required: bool
) -> list[PlanProblem]:
    if value is None:
        return (
            [PlanProblem(path, "required", "a path such as steps.<id>.output")] if required else []
        )
    if not isinstance(value, str) or not value.strip():
        return [PlanProblem(path, "bad_value", "a dotted path")]
    return _ref_problems(value.strip(), path, scope)


def _template_ref_problems(text: Any, path: str, scope: _RefScope) -> list[PlanProblem]:
    if not isinstance(text, str):
        return []
    problems: list[PlanProblem] = []
    for ref in _TEMPLATE_RE.findall(text):
        problems += _ref_problems(ref[0], path, scope)
    return problems


def _ref_problems(ref: str, path: str, scope: _RefScope) -> list[PlanProblem]:
    """One reference, judged against what the engine binds where it renders.

    ``render`` and ``resolve_path`` turn anything they cannot reach into
    null rather than an error (ADR-092 D-4: no evaluation, nothing raises),
    so a reference the walk never binds would silently feed an agent
    ``null``. Every root is therefore checked here: ``steps`` must name an
    earlier step, ``params`` a declared param, and any other root must be
    bound at this place (item, prev, replica, a fan-out's ``as`` name).
    """
    parts = ref.split(".")
    root = parts[0]
    if root == "steps":
        return _steps_ref_problems(parts, ref, path, scope.seen)
    if root == "params":
        return _params_ref_problems(parts, ref, path, scope.params)
    if root in scope.roots:
        return []
    home = _ROOT_HOMES.get(root, "is not bound here (a fan-out 'as' name exists only in its step)")
    return [PlanProblem(path, "bad_ref", f"{root!r} {home}; it would render null")]


def _steps_ref_problems(
    parts: list[str], ref: str, path: str, seen: Collection[str]
) -> list[PlanProblem]:
    if len(parts) < 2 or parts[1] not in seen:
        return [PlanProblem(path, "bad_ref", f"{ref!r} does not name an earlier step")]
    return []


def _params_ref_problems(
    parts: list[str], ref: str, path: str, params: Collection[str]
) -> list[PlanProblem]:
    """``{{params}}`` is the whole bound object; ``params.<name>`` must be declared."""
    if len(parts) < 2 or parts[1] in params:
        return []
    message = f"{ref!r}: the plan declares no param {parts[1]!r}; it would render null"
    return [PlanProblem(path, "bad_ref", message)]


def _output_problems(output: Any, scope: _RefScope, path: str = "output") -> list[PlanProblem]:
    if output is None:
        return []
    if not isinstance(output, dict):
        return [PlanProblem(path, "not_object", "output maps names to paths")]
    problems: list[PlanProblem] = []
    for name, ref in output.items():
        problems += _source_path_problems(ref, f"{path}.{name}", scope, required=True)
    return problems


def _bound_problems(program: dict[str, Any], step_count: int) -> list[PlanProblem]:
    problems = []
    if step_count > MAX_STEPS:
        problems.append(
            PlanProblem("phases", "too_many_steps", f"{step_count} steps; the limit is {MAX_STEPS}")
        )
    budget = program.get("budget")
    limit = (
        budget.get("max_agents", DEFAULT_MAX_AGENTS)
        if isinstance(budget, dict)
        else DEFAULT_MAX_AGENTS
    )
    try:
        bound = static_agent_bound(program)
    except (TypeError, ValueError, AttributeError):
        return problems  # structural problems were already reported
    if isinstance(limit, int) and bound > limit:
        problems.append(
            PlanProblem(
                "budget.max_agents",
                "bound_exceeded",
                f"the plan can start {bound} agents; max_agents is {limit}",
            )
        )
    return problems


def _unknown_keys(
    node: dict[str, Any], allowed: frozenset[str] | set[str], path: str
) -> list[PlanProblem]:
    return [
        PlanProblem(f"{path}.{key}", "unknown_key", f"unknown key {key!r}")
        for key in sorted(set(node) - set(allowed))
    ]


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_STEP_ID_RE.match(value)) and "__" not in value


def _valid_expanded_id(value: Any) -> bool:
    """``<id>__<suffix>``: ids a pattern expansion mints (users may not)."""
    if not isinstance(value, str) or "__" not in value:
        return False
    head, _, tail = value.partition("__")
    return _valid_id(head) and _valid_id(tail)


def _int_in(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


# ── the static agent bound ──────────────────────────────────────────────────


def _fanout_count(step: dict[str, Any]) -> int:
    """Replicas of an agent step; a malformed fan-out (already a validation
    problem) counts as one rather than raising mid-validation."""
    fanout = step.get("fanout")
    if not isinstance(fanout, dict):
        return 1
    if "count" in fanout:
        count = fanout["count"]
        return count if isinstance(count, int) and not isinstance(count, bool) else 1
    values = fanout.get("values")
    return len(values) if isinstance(values, list) else 1


def step_agent_bound(step: dict[str, Any]) -> int:
    """The most agents one step can start. A repeat multiplies its body by
    ``max_iterations``; a nested plan adds its own bound; a reduce or a gate
    starts none."""
    bound = _BOUNDS.get(step.get("kind"))
    return bound(step) if bound is not None else 0


def _map_bound(step: dict[str, Any]) -> int:
    per_item = sum(step_agent_bound(stage) for stage in step.get("stages") or [])
    return int(step.get("max_items") or 0) * per_item


def _repeat_bound(step: dict[str, Any]) -> int:
    body = [sub for sub in step.get("steps") or [] if isinstance(sub, dict)]
    return int(step.get("max_iterations") or 0) * sum(step_agent_bound(sub) for sub in body)


def _plan_bound(step: dict[str, Any]) -> int:
    program = step.get("program")
    return static_agent_bound(program) if isinstance(program, dict) else 0


_BOUNDS: dict[Any, Callable[[dict[str, Any]], int]] = {
    "agent": _fanout_count,
    "map": _map_bound,
    "repeat": _repeat_bound,
    "plan": _plan_bound,
}


def static_agent_bound(program: dict[str, Any]) -> int:
    """The most agents a plan can start: its validator's ``max_agents`` check."""
    return sum(step_agent_bound(step) for _phase, step in iter_steps(program))


def count_steps(program: dict[str, Any]) -> int:
    """Steps after expansion, a repeat's body and an inlined plan's included (D-4)."""
    total = 0
    for _phase, step in iter_steps(program):
        total += 1
        if step.get("kind") == "repeat":
            total += len(step.get("steps") or [])
        elif step.get("kind") == "plan" and isinstance(step.get("program"), dict):
            total += count_steps(step["program"])
    return total


def iter_steps(program: dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """(phase, step) in plan order (top-level steps of ``program``'s phases)."""
    phases = program.get("phases")
    for phase in phases if isinstance(phases, list) else []:
        steps = phase.get("steps") if isinstance(phase, dict) else None
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict):
                yield phase, step


@dataclass(frozen=True)
class AgentPart:
    """One place a plan starts agents: an agent step or a map's agent stage,
    wherever it sits (a repeat's body, an inlined template).

    ``name`` is its path (``hunt.find``, ``review/find``), ``step`` the step
    holding it (the map, for a stage), ``part`` the agent step or stage
    itself, ``stage`` the stage index or None, and ``times`` how often its
    enclosing repeats may run it (1 outside a repeat).
    """

    name: str
    step: dict[str, Any]
    part: dict[str, Any]
    stage: int | None = None
    times: int = 1


def iter_agent_parts(program: dict[str, Any]) -> Iterator[AgentPart]:
    """Every :class:`AgentPart` of a bound program, in plan order.

    What the approval card, the model preflight (D-6) and the model warnings
    walk: an agent inside a repeat or an inlined template spends like any
    other, so a walk over the top-level steps alone would hide it.
    """
    for _phase, step in iter_steps(program):
        yield from _parts_of(step, str(step.get("id")), 1)


def _parts_of(step: dict[str, Any], name: str, times: int) -> Iterator[AgentPart]:
    walker = _PART_WALKERS.get(step.get("kind"))
    if walker is not None:
        yield from walker(step, name, times)


def _agent_parts(step: dict[str, Any], name: str, times: int) -> Iterator[AgentPart]:
    yield AgentPart(name, step, step, None, times)


def _body_parts(step: dict[str, Any], name: str, times: int) -> Iterator[AgentPart]:
    loops = times * int(step.get("max_iterations") or 1)
    for sub in step.get("steps") or []:
        if isinstance(sub, dict):
            yield from _parts_of(sub, f"{name}.{sub.get('id')}", loops)


def _program_parts(step: dict[str, Any], name: str, times: int) -> Iterator[AgentPart]:
    program = step.get("program")
    for _phase, sub in iter_steps(program if isinstance(program, dict) else {}):
        yield from _parts_of(sub, f"{name}/{sub.get('id')}", times)


def _stage_parts(step: dict[str, Any], name: str, times: int) -> Iterator[AgentPart]:
    for index, stage in enumerate(step.get("stages") or []):
        if isinstance(stage, dict) and stage.get("kind") == "agent":
            yield AgentPart(name, step, stage, index, times)


_PART_WALKERS: dict[Any, Callable[[dict[str, Any], str, int], Iterator[AgentPart]]] = {
    "agent": _agent_parts,
    "map": _stage_parts,
    "repeat": _body_parts,
    "plan": _program_parts,
}


def iter_gates(program: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """``(path, gate step)`` for every gate, an inlined template's included."""
    for _phase, step in iter_steps(program):
        name = str(step.get("id"))
        if step.get("kind") == "gate":
            yield name, step
        elif step.get("kind") == "plan" and isinstance(step.get("program"), dict):
            for sub_name, gate in iter_gates(step["program"]):
                yield f"{name}/{sub_name}", gate


def profile_for(program: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """The effective profile of an agent step: defaults, the profile, the step's model."""
    profile = dict((program.get("agents") or {}).get(step.get("use") or "", {}))
    profile.setdefault("instructions", "")
    profile.setdefault("tools", list(DEFAULT_PROFILE_TOOLS))
    profile.setdefault("model", None)
    profile.setdefault("max_tool_rounds", DEFAULT_TOOL_ROUNDS)
    if step.get("model"):
        profile["model"] = step["model"]
    return profile


# ── schemas: a JSON-Schema subset (ADR-092 D-5) ─────────────────────────────


def schema_problems(schema: Any, path: str) -> list[PlanProblem]:
    """Static check of a step schema: only the supported keywords, each with a
    value of the right shape. ``validate_output`` trusts a schema that passed
    here, so a malformed keyword is refused before any spend instead of
    raising (or being silently ignored) when an agent's output is checked."""
    if not isinstance(schema, dict):
        return [PlanProblem(path, "bad_schema", "a schema is an object")]
    problems = [
        PlanProblem(
            f"{path}.{key}", "unsupported_keyword", f"schema keyword {key!r} is not supported"
        )
        for key in sorted(set(schema) - _SCHEMA_KEYS)
    ]
    for key in sorted(set(schema) & _SCHEMA_KEYS):
        problems += _KEYWORD_CHECKS[key](schema[key], f"{path}.{key}")
    return problems


def _schema_type_problems(value: Any, path: str) -> list[PlanProblem]:
    if isinstance(value, str) and value in _SCHEMA_TYPES:
        return []
    return [PlanProblem(path, "bad_schema", f"type is one of {sorted(_SCHEMA_TYPES)}")]


def _schema_properties_problems(value: Any, path: str) -> list[PlanProblem]:
    if not isinstance(value, dict):
        return [PlanProblem(path, "bad_schema", "properties maps names to schemas")]
    problems: list[PlanProblem] = []
    for name, sub in value.items():
        problems += schema_problems(sub, f"{path}.{name}")
    return problems


def _schema_names_problems(value: Any, path: str) -> list[PlanProblem]:
    if isinstance(value, list) and all(isinstance(name, str) for name in value):
        return []
    return [PlanProblem(path, "bad_schema", "required is a list of property names")]


def _schema_enum_problems(value: Any, path: str) -> list[PlanProblem]:
    if isinstance(value, list) and value:
        return []
    return [PlanProblem(path, "bad_schema", "enum is a non-empty list of values")]


def _schema_count_problems(value: Any, path: str) -> list[PlanProblem]:
    if _int_in(value, 0, 10**9):
        return []
    return [PlanProblem(path, "bad_schema", "a non-negative integer")]


def _schema_flag_problems(value: Any, path: str) -> list[PlanProblem]:
    if isinstance(value, bool):
        return []
    return [
        PlanProblem(
            path, "bad_schema", "additionalProperties is true or false (schemas are not supported)"
        )
    ]


def _schema_text_problems(value: Any, path: str) -> list[PlanProblem]:
    return (
        []
        if isinstance(value, str)
        else [PlanProblem(path, "bad_schema", "description is a string")]
    )


# ``items`` takes one schema for every element; the tuple form is not supported.
_KEYWORD_CHECKS: dict[str, Callable[[Any, str], list[PlanProblem]]] = {
    "type": _schema_type_problems,
    "properties": _schema_properties_problems,
    "required": _schema_names_problems,
    "items": schema_problems,
    "enum": _schema_enum_problems,
    "minItems": _schema_count_problems,
    "maxItems": _schema_count_problems,
    "maxLength": _schema_count_problems,
    "additionalProperties": _schema_flag_problems,
    "description": _schema_text_problems,
}


_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate_output(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Errors of ``value`` against the supported schema subset (empty = valid)."""
    kind = schema.get("type")
    if kind is not None and not _JSON_TYPES[kind](value):
        return [f"{path}: expected {kind}"]
    errors: list[str] = []
    if "enum" in schema and not _in_enum(value, schema["enum"]):
        errors.append(f"{path}: not one of {schema['enum']}")
    if isinstance(value, dict):
        errors += _object_errors(value, schema, path)
    if isinstance(value, list):
        errors += _array_errors(value, schema, path)
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        errors.append(f"{path}: longer than {schema['maxLength']}")
    return errors


def _in_enum(value: Any, options: list[Any]) -> bool:
    return any(_json_equal(value, option) for option in options)


def _json_equal(left: Any, right: Any) -> bool:
    """Equality as JSON means it: ``true`` is not ``1`` (Python's ``True == 1``
    is), while ``1`` and ``1.0`` are the same number."""
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (list, dict)) or isinstance(right, (list, dict)):
        return _json_container_equal(left, right)
    return bool(left == right)


def _json_container_equal(left: Any, right: Any) -> bool:
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(_json_equal, left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    return False


def _object_errors(value: dict[str, Any], schema: dict[str, Any], path: str) -> list[str]:
    errors = [f"{path}.{key}: required" for key in schema.get("required") or [] if key not in value]
    properties = schema.get("properties") or {}
    for key, sub in properties.items():
        if key in value:
            errors += validate_output(value[key], sub, f"{path}.{key}")
    if schema.get("additionalProperties") is False:
        errors += [f"{path}.{key}: not allowed" for key in sorted(set(value) - set(properties))]
    return errors


def _array_errors(value: list[Any], schema: dict[str, Any], path: str) -> list[str]:
    errors = []
    if len(value) < schema.get("minItems", 0):
        errors.append(f"{path}: fewer than {schema['minItems']} items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{path}: more than {schema['maxItems']} items")
    if isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors += validate_output(item, schema["items"], f"{path}[{index}]")
    return errors


# ── templates and paths ─────────────────────────────────────────────────────


def resolve_path(scope: dict[str, Any], path: str) -> Any:
    """A dotted path through dicts and lists (integer parts index lists).
    Missing → None; nothing is evaluated."""
    value: Any = scope
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and re.fullmatch(r"\d+", part):
            index = int(part)
            value = value[index] if index < len(value) else None
        else:
            return None
    return value


def template_refs(text: str) -> set[str]:
    """The step ids a template reads through ``steps.<id>``."""
    refs = set()
    for path, _fmt in _TEMPLATE_RE.findall(text or ""):
        parts = path.split(".")
        if parts[0] == "steps" and len(parts) > 1:
            refs.add(parts[1])
    return refs


def template_roots(text: str) -> set[str]:
    """The roots a template reads (``steps``, ``seen``, ``item``, …)."""
    return {path.split(".", 1)[0] for path, _fmt in _TEMPLATE_RE.findall(text or "")}


def render(template: str, scope: dict[str, Any], *, envelope: bool = True) -> str:
    """Substitute ``{{path}}``, ``{{path|json}}``, ``{{path|lines}}``, ``{{path|len}}``.

    With ``envelope``, a value read from an agent (``steps``, ``item``,
    ``prev``) is wrapped in ``<step_output source=… untrusted="true">`` so
    the receiving agent can tell data from instructions.
    """

    def substitute(match: re.Match[str]) -> str:
        path, fmt = match.group(1), match.group(2)
        text = _format(resolve_path(scope, path), fmt)
        if envelope and path.split(".", 1)[0] in UNTRUSTED_ROOTS:
            safe = _CLOSE_TAG_RE.sub(r"<\1\\/\2", text)
            return f'<step_output source="{path}" untrusted="true">\n{safe}\n</step_output>'
        return text

    return _TEMPLATE_RE.sub(substitute, template or "")


def _format(value: Any, fmt: str | None) -> str:
    if fmt == "len":
        return str(len(value)) if isinstance(value, (list, dict, str)) else "0"
    if fmt == "lines" and isinstance(value, list):
        return "\n".join(
            v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value
        )
    if fmt == "json" or not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, indent=1)
    return value


# ── reducers (closed and pure) ──────────────────────────────────────────────


def apply_reduce(step: dict[str, Any], value: Any, scope: dict[str, Any]) -> Any:
    """Run a reduce step over ``value`` (its resolved input)."""
    return _REDUCERS[step["op"]](step, value, scope)


def _as_list(value: Any) -> list[Any]:
    """A reducer's items. An input that is not a list (null, or an agent that
    answered in the wrong shape without a schema) carries no items, so the
    reduction is empty rather than an exception or a split string."""
    return value if isinstance(value, list) else []


def _concat(_step: dict[str, Any], value: Any, _scope: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    for part in _as_list(value):
        if isinstance(part, list):
            out.extend(part)
    return out


def _flatten(_step: dict[str, Any], value: Any, _scope: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    for part in _as_list(value):
        out.extend(part if isinstance(part, list) else [part])
    return out


def _dedup_by(step: dict[str, Any], value: Any, _scope: dict[str, Any]) -> list[Any]:
    seen, out = set(), []
    for item in _as_list(value):
        key = canonical_hash(resolve_path(item, step["key"]) if isinstance(item, dict) else item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _count(_step: dict[str, Any], value: Any, _scope: dict[str, Any]) -> int:
    return sum(1 for v in _as_list(value) if v is not None)


def _majority(step: dict[str, Any], value: Any, _scope: dict[str, Any]) -> dict[str, Any]:
    """True/False by strict majority of the non-null votes; ``None`` (undecided)
    on a tie or when fewer than the quorum returned a valid vote, so a model
    that fails its schema can never pass a finding by default (ADR-092 D-4)."""
    field_name = step["field"]
    slots = _as_list(value)
    votes = [v.get(field_name) for v in slots if isinstance(v, dict)]
    votes = [v for v in votes if isinstance(v, bool)]
    yes, no = votes.count(True), votes.count(False)
    verdict: bool | None = None
    if len(votes) >= _quorum(step, len(slots)) and yes != no:
        verdict = yes > no
    return {"verdict": verdict, "true": yes, "false": no, "non_null": len(votes)}


def _quorum(step: dict[str, Any], slots: int) -> int:
    """ADR-092 D-4: fewer than ⌈n/2⌉ valid votes is undecided. ``n`` is every
    voter slot, the null of a failed replica included, and is only known at
    reduce time, so an omitted quorum is computed here: a default of 1 would
    let one surviving vote of five decide. A quorum the author wrote is kept,
    even a lower one: that is a choice on the page, not a silent default."""
    written = step.get("quorum")
    return int(written) if written is not None else max(1, math.ceil(slots / 2))


def _partition(step: dict[str, Any], value: Any, scope: dict[str, Any]) -> dict[str, list[Any]]:
    items = _as_list(resolve_path(scope, step["items"]))
    out: dict[str, list[Any]] = {name: [] for name in step["buckets"]}
    by_verdict = {json.dumps(v): name for name, v in step["buckets"].items()}
    for index, item in enumerate(items):
        result = value[index] if isinstance(value, list) and index < len(value) else None
        verdict = result.get(step["field"]) if isinstance(result, dict) else None
        bucket = by_verdict.get(json.dumps(verdict))
        if bucket is not None:
            out[bucket].append(item)
    return out


def _mean(step: dict[str, Any], value: Any, _scope: dict[str, Any]) -> dict[str, Any]:
    """The mean of one numeric field over the votes (judge scores, D-4).

    ``None`` when fewer than the quorum of the voter slots gave a number:
    the same rule as ``majority``, so a judge that fails its schema cannot
    move a candidate's score by default. An omitted quorum is ⌈n/2⌉ of the
    slots, nulls included.
    """
    slots = _as_list(value)
    scores = [v.get(step["field"]) for v in slots if isinstance(v, dict)]
    numbers = [float(x) for x in scores if _is_number(x)]
    mean = None
    if numbers and len(numbers) >= _quorum(step, len(slots)):
        mean = sum(numbers) / len(numbers)
    return {"mean": mean, "n": len(numbers), "of": len(slots)}


def _rank(step: dict[str, Any], value: Any, scope: dict[str, Any]) -> dict[str, Any]:
    """Candidates by score, best first; a near-tie is never resolved by code.

    The input is one result per candidate (``items``, same order) carrying
    the score ``field``; a candidate whose score is null (no quorum) is
    ``unscored`` and never picked. Every scored candidate within ``margin``
    of the best is ``competing``: with two or more of them the status is
    ``competing_hypotheses`` (the ADR-066 class) and no winner is picked;
    with one, it is ``picked``; with none scored, ``undecided``.
    """
    items = _as_list(resolve_path(scope, step["items"]))
    scored = _scores_of(len(items), _as_list(value), step["field"])
    ranking = sorted(scored, key=lambda pair: (-pair[0], pair[1]))
    out: dict[str, Any] = {
        "status": "undecided",
        "winner": None,
        "winner_index": None,
        "ranking": [{"index": i, "score": score} for score, i in ranking],
        "competing": [],
        "unscored": sorted(set(range(len(items))) - {i for _s, i in scored}),
    }
    if not ranking:
        return out
    best = ranking[0][0]
    close = [i for score, i in ranking if best - score <= float(step.get("margin", 0))]
    if len(close) > 1:
        out.update(status="competing_hypotheses", competing=[items[i] for i in close])
        return out
    out.update(status="picked", winner=items[close[0]], winner_index=close[0])
    return out


def _scores_of(count: int, results: list[Any], field_name: str) -> list[tuple[float, int]]:
    """``(score, candidate index)`` for every candidate with a numeric score."""
    scored = []
    for index in range(count):
        result = results[index] if index < len(results) else None
        score = result.get(field_name) if isinstance(result, dict) else None
        if _is_number(score):
            scored.append((float(score), index))
    return scored


def _subtract(step: dict[str, Any], value: Any, scope: dict[str, Any]) -> list[Any]:
    """The input's items whose key is not among ``items``' keys, deduplicated
    in input order (a seen-set difference, D-4)."""
    taken = {
        item_key(item, step.get("key")) for item in _as_list(resolve_path(scope, step["items"]))
    }
    out = []
    for item in _as_list(value):
        key = item_key(item, step.get("key"))
        if key not in taken:
            taken.add(key)
            out.append(item)
    return out


def item_key(item: Any, key: str | None) -> str:
    """An item's identity for a seen-set: the hash of its ``key`` field (a
    dotted path) when one is named and the item is an object, else of the
    whole item."""
    if key and isinstance(item, dict):
        return canonical_hash(resolve_path(item, key))
    return canonical_hash(item)


_REDUCERS: dict[str, Callable[[dict[str, Any], Any, dict[str, Any]], Any]] = {
    "concat": _concat,
    "flatten": _flatten,
    "dedup_by": _dedup_by,
    "count": _count,
    "majority": _majority,
    "partition": _partition,
    "mean": _mean,
    "rank": _rank,
    "subtract": _subtract,
}


# ── identity ────────────────────────────────────────────────────────────────


def canonical_hash(obj: Any) -> str:
    """sha256 of the canonical JSON of ``obj`` (key order never matters)."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def plan_summary(program: dict[str, Any]) -> dict[str, Any]:
    """What an approval card or a dry run shows: phases, bound, profiles, budget."""
    profiles = {
        name: {"tools": list(p.get("tools", DEFAULT_PROFILE_TOOLS)), "model": p.get("model")}
        for name, p in (program.get("agents") or {}).items()
    }
    phases = [
        {
            "id": ph.get("id"),
            "title": ph.get("title", ""),
            "steps": [s.get("id") for s in ph.get("steps") or []],
        }
        for ph in program.get("phases") or []
    ]
    return {
        "name": program.get("name"),
        "phases": phases,
        "agent_bound": static_agent_bound(program),
        "profiles": profiles,
        "budget": program.get("budget"),
        "concurrency": program.get("concurrency", 4),
        "params": program.get("_bound_params", {}),
        "gates": [
            {"gate": name, "class": gate.get("class"), "when": gate.get("when")}
            for name, gate in iter_gates(program)
        ],
        "merge_backs": merge_back_rows(program),
        "warnings": independence_warnings(program) + discard_warnings(program),
    }


def merge_back_rows(program: dict[str, Any]) -> list[dict[str, Any]]:
    """``{step, from}`` for every ``merge_back`` step (slice 5): where the run
    will stop and ask before writing isolated workspaces into the project."""
    return [
        {"step": step.get("id"), "from": list(step.get("from") or [])}
        for _phase, step in iter_steps(program)
        if step.get("kind") == "merge_back"
    ]


def discard_warnings(program: dict[str, Any]) -> list[str]:
    """A warning per isolated step that may write while no ``merge_back``
    names it (slice 5): its workspace is removed at run end, so whatever its
    agents wrote is lost. An isolated step inside a ``repeat`` or an inlined
    template can never be named by a merge-back, so it always warns.
    "May write" is :func:`is_writer_tool` without declarations: an MCP tool
    counts as a writer (fail closed: a needless warning, never a silent loss).
    """
    covered = {str(source) for row in merge_back_rows(program) for source in row["from"]}
    warned: list[str] = []
    for part in iter_agent_parts(program):
        if part.name not in warned and part.name not in covered and _isolated_writer(program, part):
            warned.append(part.name)
    return [
        f"changes made in {name}'s isolated workspace are discarded at run end "
        "(no merge_back step names it)"
        for name in warned
    ]


def _isolated_writer(program: dict[str, Any], part: AgentPart) -> bool:
    if step_isolation(part.step) not in ISOLATED_MODES:
        return False
    tools = profile_for(program, part.part).get("tools") or ()
    return any(is_writer_tool(str(tool)) for tool in tools)


def independence_warnings(program: dict[str, Any], default_model: str | None = None) -> list[str]:
    """A warning per reviewer that runs on a model its reviewed profile runs on.

    A profile's ``reviews`` names the profiles whose work it reviews. When a
    reviewing agent and a reviewed one resolve to the same model (their own,
    else their profile's, else ``default_model``; two unnamed models are the
    same default), the review is the producer checking itself, a weaker check
    than an independent model (the research design's reviewer rule). Never an
    error: a site with one model can still run the plan, and is told so.
    """
    models = _models_by_profile(program, default_model)
    warnings = []
    for name, other in _review_pairs(program):
        shared = sorted(models.get(name, set()) & models.get(other, set()))
        if shared:
            warnings.append(
                f"reviewer {name!r} runs on the model {other!r} runs on ({', '.join(shared)}): "
                "the review is not independent; name a different reviewer model"
            )
    return warnings


def _models_by_profile(program: dict[str, Any], default_model: str | None) -> dict[str, set[str]]:
    """Profile name → every model its agents resolve to (``""``: the default profile)."""
    models: dict[str, set[str]] = {}
    for part in iter_agent_parts(program):
        model = profile_for(program, part.part).get("model") or default_model
        key = str(part.part.get("use") or "")
        models.setdefault(key, set()).add(str(model or "(default model)"))
    return models


def _review_pairs(program: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """``(reviewer, reviewed)`` for every profile's ``reviews`` entry."""
    for name, profile in sorted((program.get("agents") or {}).items()):
        reviews = profile.get("reviews") if isinstance(profile, dict) else None
        for other in reviews if isinstance(reviews, list) else []:
            yield str(name), str(other)
