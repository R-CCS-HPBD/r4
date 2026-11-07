"""Skill lint: builtin skills may only name tools and ``r4`` verbs that exist.

ADR-105 D-7 (2026-11-01). The 2026-11-01 skill audit found 31 drift defects in
17 builtin skills; most are semantic (a wrong argument, a missing step) and
no script catches them. This hermetic lint catches the mechanical classes
only. It fails when a builtin SKILL.md body:

* leaves a ``{{contract:<module>:<CONST>}}`` placeholder unresolved;
* lists an ``allowed-tools`` entry that is not a real tool;
* names, inside backticks, a snake_case tool of a known tool family that does
  not exist (slash shorthand such as ``workflow_run/replay/status`` and globs
  such as ``file_*_platform`` are expanded);
* calls a backticked token a tool or a skill (`` `X` tool``, `` `X` skill``,
  ``(use `X`)``) and no such tool or skill exists: this is what catches
  dotted or foreign names such as ``cron.add`` or ``latex-posters``;
* names an ``r4`` CLI command or subcommand that the full argparse tree
  (pre-registered plus lazily registered commands) lacks: inside backticks,
  or anywhere on a line of a fenced block (``$ r4 ...``, ``uv run r4 ...``,
  ``X=1 r4 ...``, ``a && r4 ...``, ``$(r4 ...)``; ``#`` comments are
  dropped), with root flags before the verb skipped.

Not caught: plain or bold prose mentions (``use r4 job submit``), slash
commands such as ``/loop``, and anything in ``references/*.md`` (below).

Scope notes:

* The inventory is ``get_builtin_tools(client=_serving_client())`` plus
  ``activate_skill`` (added by the companion session, never by
  get_builtin_tools). ``client=None`` would hide every platform tool.
* MCP names (``mcp.<server>.<tool>``, ``mcp:<server>``) are dynamic and need a
  live client, so they are skipped; other dotted names are not.
* The vendored AI-research skills (builtin/LICENSE.AI-research-SKILLs) declare
  no ``allowed-tools`` but ARE linted for backticked references.
* ``references/*.md`` files are NOT linted: skills.py never reads them into a
  prompt (only SKILL.md is parsed); the agent opens them on demand with
  file tools, so they are reading material, not tool instructions.
* Only the substituted, hygiene-on ``Skill.system_prompt`` is linted; the
  pre-H19 wording swapped in when hygiene is off names tools that also exist.
* When the ADR-105 D-1 manifest lands, the inventory should come from it.
"""

from __future__ import annotations

import argparse
import fnmatch
import itertools
import re
from functools import lru_cache

import pytest
from test_companion.test_umrs_skills import _serving_client

from r4.companion.skills import SkillRegistry

# Tools that are not R4 builtins but that a skill may name on purpose.
EXTERNAL_TOOLS = {
    # Exa's MCP search tool, named by the vendored autoresearch skill as an
    # optional external search backend (not an R4 builtin).
    "web_search_exa",
}

# snake_case identifiers that pass the family-prefix gate but are not tools.
NOT_TOOLS = {
    "workflow_name",  # the workflow_run/workflow_describe argument
    "job_id",  # the job_* tools' argument
}

# Retired verbs a skill mentions on purpose (as retired), in backticks.
RETIRED_VERBS = {
    # SPINE_DISAMBIGUATION (r4_contracts.agent_tool_playbook, substituted into
    # research_loop) names it to say it is retired (2026-09-07).
    ("project", "run"),
}

# Top-level command floor (62 measured 2026-11-01): a silent import failure in
# a _cli module would otherwise shrink the tree and hide unknown verbs.
_MIN_TOP_LEVEL = 62

_FENCE = re.compile(r"^[ \t]*(?P<f>```|~~~)[^\n]*\n(?P<body>.*?)^[ \t]*(?P=f)", re.S | re.M)
# Inline spans may wrap a line (`workflow_run/replay/\n  status`), never a paragraph.
_INLINE = re.compile(r"`((?:[^`\n]|\n(?![ \t]*\n))+)`")
_SLASH_WRAP = re.compile(r"\s*/\s*")  # `workflow_run/replay/\n  status`
_TOOLISH = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9_*]+(?:/[a-z0-9_*]+)*$")
_CLI = re.compile(r"(?:^|\s)r4((?:[ \t]+\S+){1,6})")
# In a fenced block r4 anywhere on a line, after a shell boundary.
_CLI_LINE = re.compile(r"(?:^|[\s;&|(`])r4((?:[ \t]+\S+){1,6})", re.M)
_COMMENT = re.compile(r"(?:^|[ \t])#[^\n]*", re.M)
_MAX_CLI_WORDS = 3
# A backticked token the text itself calls a tool or a skill.
_NAMED_REF = re.compile(r"`([^`\n]+)`[ \t]+(tool|skill)s?\b")
_USE_REF = re.compile(r"\(use `([^`\n]+)`")
_WORD = re.compile(r"^[A-Za-z][\w-]*(?:\|[A-Za-z][\w-]*)*$")


@lru_cache(maxsize=1)
def _registry() -> SkillRegistry:
    return SkillRegistry()  # builtins only; never load_user_skills (reads cwd/HOME)


@lru_cache(maxsize=1)
def _skill_names() -> frozenset[str]:
    """Skills name each other in backticks (`model_management`); not tools."""
    return frozenset(s.name for s in _registry().builtin_skills)


@lru_cache(maxsize=1)
def _inventory() -> frozenset[str]:
    from r4.companion.tools import get_builtin_tools

    names = {t["name"] for t in get_builtin_tools(client=_serving_client())}
    return frozenset(names | {"activate_skill"})


def _families(inventory: frozenset[str]) -> frozenset[str]:
    return frozenset(n.split("_", 1)[0] for n in inventory if "_" in n)


# -- tool references --------------------------------------------------------


def _expand(span: str, families: frozenset[str]) -> list[str]:
    """``workflow_run/replay`` -> workflow_run, workflow_replay."""
    head, *rest = span.split("/")
    prefix = head.rsplit("_", 1)[0] + "_"
    out = [head]
    for seg in rest:
        full = "_" in seg and seg.split("_", 1)[0] in families
        out.append(seg if full else prefix + seg)
    return out


def _tool_candidates(text: str, families: frozenset[str]) -> list[str]:
    names: list[str] = []
    for span in _INLINE.findall(_FENCE.sub(" ", text)):
        token = _SLASH_WRAP.sub("/", span.split("(", 1)[0]).strip().strip("<>")
        if _TOOLISH.match(token):
            names.extend(_expand(token, families))
    return names


def _is_dynamic(name: str) -> bool:
    return name.startswith(("mcp.", "mcp:"))


def _resolves(name: str, inventory: frozenset[str]) -> bool:
    if "*" in name:
        return any(fnmatch.fnmatchcase(t, name) for t in inventory)
    return name in inventory or name in EXTERNAL_TOOLS | NOT_TOOLS | _skill_names()


def _unknown_tools(text: str, inventory: frozenset[str]) -> list[str]:
    families = _families(inventory)
    bad = []
    for name in _tool_candidates(text, families):
        if name.split("_", 1)[0] not in families or _is_dynamic(name):
            continue
        if not _resolves(name, inventory):
            bad.append(f"tool `{name}`")
    return sorted(set(bad))


def _named_refs(text: str) -> list[tuple[str, str]]:
    """(token, kind) for `X` tool, `X` skill and (use `X`); kind "use" is either."""
    prose = _FENCE.sub(" ", text)
    refs = [(m.group(1), m.group(2)) for m in _NAMED_REF.finditer(prose)]
    return refs + [(m.group(1), "use") for m in _USE_REF.finditer(prose)]


def _named_unknown(token: str, kind: str, inventory: frozenset[str]) -> list[str]:
    name = _SLASH_WRAP.sub("/", token.split("(", 1)[0]).strip().strip("<>")
    if _is_dynamic(name):
        return []
    if kind == "skill":
        return [] if name in _skill_names() else [f"skill `{name}`"]
    names = _expand(name, _families(inventory)) if _TOOLISH.match(name) else [name]
    label = "tool" if kind == "tool" else "tool/skill"
    return [f"{label} `{n}`" for n in names if not _resolves(n, inventory)]


def _unknown_named(text: str, inventory: frozenset[str]) -> list[str]:
    bad = {b for tok, kind in _named_refs(text) for b in _named_unknown(tok, kind, inventory)}
    return sorted(bad)


# -- CLI references ---------------------------------------------------------


def _register_lazy(sub: argparse._SubParsersAction, reg) -> None:
    for name in list(reg.names()):
        if name in sub.choices:
            continue
        try:
            reg.get(name).register(sub)
        except argparse.ArgumentError:
            pass  # registers a parser name already present ('sessions' -> 'session')


def _tree(parser: argparse.ArgumentParser) -> dict | None:
    from r4._cli.completion import _subparser_action

    action = _subparser_action(parser)
    if action is None:
        return None
    return {name: _tree(child) for name, child in action.choices.items()}


@lru_cache(maxsize=1)
def _root_parser() -> argparse.ArgumentParser:
    from r4.cli import _build_parser, _load_dispatch_registry

    parser = _build_parser()
    _register_lazy(parser._lazy_subparsers_action, _load_dispatch_registry())
    return parser


@lru_cache(maxsize=1)
def _cli_tree() -> dict:
    return _tree(_root_parser()) or {}


@lru_cache(maxsize=1)
def _root_bool_flags() -> frozenset[str]:
    """Root options that take no value (``-v``, ``--version``, ``-h``)."""
    acts = _root_parser()._actions
    return frozenset(o for a in acts if a.nargs == 0 for o in a.option_strings)


def _skip_root_flags(tokens: list[str]) -> list[str]:
    """Drop ``r4 --flag [value]`` before the verb; an unknown flag takes a value."""
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        flag = tokens[i]
        i += 1 if "=" in flag or flag in _root_bool_flags() else 2
    return tokens[i:]


def _cli_words(rest: str) -> list[list[str]]:
    """Leading bare words after ``r4``, each split on ``|`` alternation."""
    words = []
    for token in _skip_root_flags(rest.split())[:_MAX_CLI_WORDS]:
        token = token.rstrip(".,;:)")
        if token[:1] in "<[-{" or "=" in token or not _WORD.match(token):
            break
        words.append(token.split("|"))
    return words


def _fenced_code(text: str) -> list[str]:
    """Fenced block bodies (``` or ~~~) with ``#`` comments dropped."""
    return [_COMMENT.sub("", m.group("body")) for m in _FENCE.finditer(text)]


def _cli_matches(text: str) -> list[re.Match]:
    inline = _INLINE.findall(_FENCE.sub(" ", text))
    matches = [m for span in inline for m in _CLI.finditer(span)]
    return matches + [m for block in _fenced_code(text) for m in _CLI_LINE.finditer(block)]


def _retired(words: tuple[str, ...]) -> bool:
    """`r4 project run foo` is still the retired `project run`."""
    return any(words[: len(v)] == v for v in RETIRED_VERBS)


def _cli_refs(text: str) -> list[tuple[str, ...]]:
    refs = [w for m in _cli_matches(text) for w in itertools.product(*_cli_words(m.group(1)))]
    return [r for r in refs if r and not _retired(r)]


def _cli_known(words: tuple[str, ...], tree: dict) -> bool:
    node: dict | None = tree
    for word in words:
        if node is None:
            return True  # a leaf: the rest are positional arguments
        if word not in node:
            return False
        node = node[word]
    return True


def _unknown_cli(text: str, tree: dict) -> list[str]:
    bad = {f"cli `r4 {' '.join(w)}`" for w in _cli_refs(text) if not _cli_known(w, tree)}
    return sorted(bad)


def _lint(text: str) -> list[str]:
    tools = sorted(set(_unknown_tools(text, _inventory()) + _unknown_named(text, _inventory())))
    return tools + _unknown_cli(text, _cli_tree())


# -- tests ------------------------------------------------------------------

_SKILLS = {s.name: s for s in _registry().builtin_skills}


def test_skills_and_tree_are_populated():
    assert len(_SKILLS) >= 25
    assert len(_cli_tree()) >= _MIN_TOP_LEVEL
    assert {"workflow_run", "hpc_submit", "job_list"} <= _inventory()


@pytest.mark.parametrize("name", sorted(_SKILLS))
def test_contract_placeholders_resolve(name):
    assert "{{contract:" not in _SKILLS[name].system_prompt


@pytest.mark.parametrize("name", sorted(_SKILLS))
def test_allowed_tools_exist(name):
    tools = [t for t in _SKILLS[name].tool_names if not _is_dynamic(t)]
    assert [t for t in tools if not _resolves(t, _inventory())] == []


@pytest.mark.parametrize("name", sorted(_SKILLS))
def test_body_references_exist(name):
    assert _lint(_SKILLS[name].system_prompt) == []


def test_lint_flags_bogus_references():
    text = (
        "Run `workflow_frobnicate` and `file_*_nope`, then `r4 job submit <spec>`.\n"
        "```bash\n# r4 is the CLI\n$ r4 frobnicate now\n```\n"
    )
    expected = [
        "tool `file_*_nope`",
        "tool `workflow_frobnicate`",
        "cli `r4 frobnicate now`",
        "cli `r4 job submit`",
    ]
    assert _lint(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "```bash\nR4_PROFILE=rccs r4 job submit spec.yaml\n```\n",
        "```bash\nuv run r4 job submit spec.yaml\n```\n",
        "```bash\ncd x && r4 job submit spec.yaml\n```\n",
        "```bash\nID=$(r4 job submit spec.yaml)\n```\n",
        "~~~bash\nr4 job submit spec.yaml\n~~~\n",
        "Use `r4 --profile x job submit`.",
        "Use `r4 -v job submit`.",
    ],
)
def test_lint_flags_cli_in_any_shell_position(text):
    assert _lint(text) == ["cli `r4 job submit`"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Use the `cron.add` tool.", "tool `cron.add`"),
        ("See the `latex-posters` skill.", "skill `latex-posters`"),
        ("(use `scientific-skills:lit-review`)", "tool/skill `scientific-skills:lit-review`"),
        ("Call the `WebSearch` tool.", "tool `WebSearch`"),
    ],
)
def test_lint_flags_named_tool_and_skill_references(text, expected):
    assert _lint(text) == [expected]


def test_lint_passes_clean_text():
    text = (
        "Use `workflow_run/replay/\n  status`, `file_*_platform`, `hpc_sites(refresh=true)`, "
        "`<hpc_sites>`, `mcp.fugaku.submit`, `unknown_site`, `r4 workflow trigger <name>`, "
        "`r4 hpc cancel|retry <id>[@site]`; r4 drives the platform.\n"
        "```bash\n# r4 drives the platform\nr4 mcp register exa --url http://x\n```\n"
        "then `r4 mcp list`.\n"
        "The `research_loop` skill, the `workflow_run` tool, the `mcp.fugaku.submit` tool "
        "(use `brainstorming-research-ideas`); `r4 project run foo` is retired.\n"
        "~~~bash\ncd x && r4 -v workflow list  # r4 lists them\n~~~\n"
    )
    assert _lint(text) == []
