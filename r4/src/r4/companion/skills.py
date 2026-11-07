"""Skill system — named bundles of prompt + tools + activation keywords.

All skills — builtin, user, and project — use the general Agent Skills
format (https://agentskills.io/specification): a directory containing a
``SKILL.md`` file with YAML front-matter and a Markdown body.

Front-matter fields::

    name            required (falls back to the directory name)
    description     required — what the skill does and when to use it
    license         optional, informational
    compatibility   optional, informational
    allowed-tools   optional — space-separated tool names enabled while the
                    skill is active (r4 tool gating)
    metadata:
      x-r4-keywords      comma-separated auto-activation keywords
      x-r4-dependencies  comma-separated skill names activated together

Builtin skills ship in ``companion/builtin/<name>/SKILL.md`` and are loaded
through the exact same parser as user skills, so the registry has a single
code path for every source.

Body placeholders: ``{{contract:<python.module>:<CONSTANT>}}`` is
substituted at load time from r4-contracts modules, keeping procedural
guidance single-sourced (adapters import — they never rephrase).
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection

logger = logging.getLogger(__name__)

# H19(d), 2026-10-24: the code skill no longer steers verification to
# compile_code / run_code / test_code, which no TB 4.0 trial used. With
# R4_COMPANION_PROMPT_HYGIENE=0 the pre-H19 wording comes back, so that arm's
# prompt is byte-identical to the build before the fixes (review 2026-10-24).
_PRE_HYGIENE_WORDING: dict[str, tuple[tuple[str, str], ...]] = {
    "code": (
        (
            "6. Verify with the project's own build and test commands (shell_exec, or "
            "compile_code / run_code / test_code when they are offered).",
            "6. Prefer compile_code, run_code, and test_code for local verification "
            "before falling back to shell_exec.",
        ),
        (
            "- After every edit group, re-verify with the project's build or tests\n"
            "  before presenting the result;",
            "- After every edit group, re-verify with compile_code or test_code before\n"
            "  presenting the result;",
        ),
    ),
}


def _prompt_body(skill: "Skill") -> str:
    """The skill's body as the prompt carries it (pre-H19 wording when hygiene is off)."""
    from .agent_profile import hygiene_enabled

    body = skill.system_prompt
    if hygiene_enabled():
        return body
    for current, previous in _PRE_HYGIENE_WORDING.get(skill.name, ()):
        body = body.replace(current, previous)
    return body


SKILLS_DIR = Path(
    os.environ.get("R4_COMPANION_SKILLS_DIR") or os.path.expanduser("~/.config/r4/companion/skills")
)

# Builtin skills directory (general Agent Skills format, one dir per skill).
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "builtin"

# Pattern for contract placeholder references inside SKILL.md bodies.
_CONTRACT_REF_RE = re.compile(r"\{\{contract:([A-Za-z0-9_.]+):([A-Z0-9_]+)\}\}")


def _one_line(text: str, *, limit: int = 120) -> str:
    """Collapse whitespace + cap for one-line catalog entries."""
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "…"


def _substitute_contract_refs(body: str) -> str:
    """Resolve ``{{contract:<module>:<CONSTANT>}}`` placeholders in a body.

    Unknown module / constant references are left verbatim and logged —
    a skill file must never fail to load because guidance moved.
    """

    def _resolve(match: re.Match[str]) -> str:
        module_name, constant = match.group(1), match.group(2)
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, constant)
        except (ImportError, AttributeError) as exc:
            logger.warning(
                "skill contract reference {%s:%s} unresolved: %s",
                module_name,
                constant,
                exc,
            )
            return match.group(0)
        # Verbatim — callers compare against the canonical constant text.
        return str(value)

    return _CONTRACT_REF_RE.sub(_resolve, body)


def _split_csv(value: Any) -> list[str]:
    """Normalize a comma-separated string, whitespace list, or list.

    Comma wins when present (multi-word entries like keywords); plain
    whitespace splitting is used otherwise (e.g. ``allowed-tools``).
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        return []
    value = value.strip().strip('"').strip("'")
    if "," in value:
        parts = value.split(",")
    else:
        parts = value.split()
    return [p for p in (s.strip() for s in parts) if p]


@dataclass
class Skill:
    """A named capability bundle."""

    name: str
    description: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    source: str = "builtin"  # builtin | user | project | mcp
    # Declared ``version:`` front-matter, empty when absent. Part of the
    # catalog pin (gap row D-27 / defect V-06, 2026-09-22).
    version: str = ""
    # "Skills as tested code" (design suggestion 8, D-27): a skill bundle
    # may carry a ``tests/`` directory with an executable entry
    # (``tests/run.sh``). Discovery-only here — the runner is the CLI verb;
    # the merge-on-green rule (loop-proposed edits merge only when tests
    # pass) enforces at the promotion seam.
    tests_dir: str = ""
    has_tests: bool = False

    def matches(self, text: str) -> bool:
        """Check if any activation keyword appears in the text as a whole word.

        W3 (2026-10-05): substring matching fired "ara" inside "compare", "rag"
        inside "average", "gate" inside "investigate". A keyword now needs a
        non-alphanumeric boundary on both sides; a plural "s"/"es" is allowed.
        """
        text_lower = text.lower()
        return any(_keyword_pattern(kw).search(text_lower) for kw in self.keywords if kw)


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Whole-word (plural-tolerant) matcher for one activation keyword."""
    cached = _KEYWORD_PATTERNS.get(keyword)
    if cached is None:
        body = re.escape(keyword.lower())
        cached = re.compile(rf"(?<![a-z0-9]){body}(?:e?s)?(?![a-z0-9])")
        _KEYWORD_PATTERNS[keyword] = cached
    return cached


_KEYWORD_PATTERNS: dict[str, "re.Pattern[str]"] = {}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Meta tools available on every turn even when a skill filter is active.
ALWAYS_ON_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ask_user",
        "todo_write",
        "tool_search",
        "final_output",
        "platform_guide",
        # ADR-111: saved memory is harness plumbing too; a skill filter never
        # hides it (the lead session alone carries these tools).
        "memory_read",
        "memory_write",
        "memory_forget",
        # G9: background-agent orchestration metas (like todo_write, they are
        # harness plumbing, not skill domain tools).
        "async_agent_spawn",
        "async_agent_status",
        # G17b: follow-ups resume a finished agent's context.
        "async_agent_message",
        # G15: the sprint reviewer drives the same harness lane.
        "build_contract_review",
    }
)

# The agent's own workspace tools stay offered under any skill filter: a skill
# narrows the domain tools, never the agent's hands (2026-10-05, harness repair
# W3). Before W3 the substring keyword match activated the ``code`` skill on
# almost every prompt, which hid that a domain-only filter (vision, research,
# data_management) dropped shell_exec and the file tools entirely; whole-word
# matching exposed it on the TB 4.0 re-run (7 tools offered, no shell).
WORKSPACE_TOOL_NAMES: frozenset[str] = frozenset(
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
    }
)

# Skills pre-activated for every new session (baseline knowledge). Applied
# by SkillRegistry.apply_defaults() from the session factories — a bare
# SkillRegistry() (listings, tests) starts with nothing active. W3
# (2026-10-05): skills in PLATFORM_DEFAULT_SKILLS are pre-activated only when
# the session has a live platform; otherwise their keywords still activate them.
DEFAULT_ACTIVE_SKILLS: frozenset[str] = frozenset({"umrs"})
PLATFORM_DEFAULT_SKILLS: frozenset[str] = frozenset({"umrs"})


_NO_YAML_WARNED = False


def _warn_no_yaml() -> None:
    global _NO_YAML_WARNED
    if not _NO_YAML_WARNED:
        _NO_YAML_WARNED = True
        logger.error(
            "PyYAML is not installed: skill metadata (keywords, dependencies) cannot be "
            "parsed and skills will not auto-activate. Install pyyaml>=6.0."
        )


class SkillRegistry:
    """Manages discovery, activation, and loading of skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._active: set[str] = set()
        # Active skills whose body stays out of the prompt (H19): the session
        # sets it, e.g. platform skills while no platform is reachable.
        self._body_omitted: Callable[[Skill], str] | None = None

        # Register built-in skills from SKILL.md files (same parser as
        # user/project skills — one code path for all sources).
        self._load_builtin_skills()

    # -- registration ---------------------------------------------------------

    def register(self, skill: Skill) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        """List all registered skills."""
        return list(self._skills.values())

    def list_active(self) -> list[Skill]:
        """List currently active skills."""
        return [self._skills[name] for name in sorted(self._active) if name in self._skills]

    @property
    def builtin_skills(self) -> list[Skill]:
        """Built-in skills (source == 'builtin'), in name order."""
        return sorted(
            (s for s in self._skills.values() if s.source == "builtin"),
            key=lambda s: s.name,
        )

    # -- activation ---------------------------------------------------------

    def activate(self, name: str) -> bool:
        """Manually activate a skill."""
        if name in self._skills:
            self._active.add(name)
            return True
        return False

    def deactivate(self, name: str) -> None:
        """Deactivate a skill."""
        self._active.discard(name)

    def apply_defaults(self, *, platform_live: bool = True) -> None:
        """Activate DEFAULT_ACTIVE_SKILLS (called once per new session).

        ``platform_live=False`` (no platform credentials or gateway unreachable)
        skips the platform skills: their tools would only fail (TB 4.0, W3).
        """
        for name in DEFAULT_ACTIVE_SKILLS:
            if not platform_live and name in PLATFORM_DEFAULT_SKILLS:
                continue
            if name in self._skills:
                self._active.add(name)

    def auto_activate(self, text: str, *, only: Collection[str] | None = None) -> list[str]:
        """Activate skills whose keywords match the text.

        Returns list of newly activated skill names.
        Dependencies are resolved recursively across all sources
        (builtin, user, project, mcp, nvidia). ``only`` limits which skills
        keywords may activate (the terminal profile, H2); None means all.
        """
        newly: list[str] = []
        for skill in self._skills.values():
            if only is not None and skill.name not in only:
                continue
            if skill.name not in self._active and skill.matches(text):
                newly.extend(self._activate_with_deps(skill))
        return newly

    def _activate_with_deps(self, skill: Skill) -> list[str]:
        """Activate a skill and all its dependencies recursively.

        Returns the list of newly activated skill names.
        """
        newly: list[str] = []
        if skill.name not in self._active:
            self._active.add(skill.name)
            newly.append(skill.name)
        for dep_name in skill.dependencies:
            dep = self._skills.get(dep_name)
            if not dep or dep_name in self._active:
                continue
            newly.extend(self._activate_with_deps(dep))
        return newly

    # -- context assembly ---------------------------------------------------

    def set_body_filter(self, omitted: Callable[[Skill], str] | None) -> None:
        """Keep active skills' bodies out of :meth:`active_system_prompt` (H19).

        ``omitted(skill)`` returns why the body stays out (one short clause),
        or ``""`` to load it; None loads every active body.
        """
        self._body_omitted = omitted

    def _omission(self, skill: Skill) -> str:
        omitted = getattr(self, "_body_omitted", None)
        return omitted(skill) if omitted is not None else ""

    def active_system_prompt(self) -> str:
        """Combine system prompts of all active skills.

        A body the body filter omits is named on one line with the reason
        instead: the skill stays active, its tools are not offered.
        """
        parts = []
        omitted: dict[str, list[str]] = {}
        for skill in self.list_active():
            reason = self._omission(skill)
            if reason:
                omitted.setdefault(reason, []).append(skill.name)
                continue
            parts.append(f"[Skill: {skill.name}]\n{_prompt_body(skill)}")
        for reason, names in omitted.items():
            parts.append(f"[Skills not loaded: {', '.join(names)} ({reason})]")
        return "\n\n".join(parts)

    def discovery_catalog_prompt(self, *, max_entries: int = 40) -> str:
        """ADR-037 progressive disclosure: the discovery layer.

        Every registered skill listed as ``name — one-line description``;
        FULL bodies stay out (bodies load only for selected/active skills —
        that filter is the whole point; discovery must not leak them).
        One line per skill, stable-sorted; overflow reduces to
        ``+N more skills`` so the catalog never dominates the prompt.
        """
        if not self._skills:
            return ""
        skills = sorted(self._skills.values(), key=lambda s: (s.source or "", s.name))
        lines = [
            f"- {skill.name} — {_one_line(skill.description)}" for skill in skills[:max_entries]
        ]
        overflow = len(skills) - max_entries
        if overflow > 0:
            lines.append(f"- … +{overflow} more skills")
        body = "\n".join(lines)
        return "[Skills available (bodies load on activation only)]\n" + body

    def active_tool_names(self) -> set[str]:
        """Collect tool names from all active skills."""
        names: set[str] = set()
        for skill in self.list_active():
            names.update(skill.tool_names)
        return names

    # -- MCP-derived skills (bug.memo C6, 2026-04-25) -----------------------

    @staticmethod
    def _mcp_server_name(srv: Any) -> str | None:
        if isinstance(srv, dict):
            return srv.get("name")
        return getattr(srv, "name", None)

    @staticmethod
    def _mcp_tool_identity(tool: Any) -> tuple[str | None, str | None]:
        if isinstance(tool, dict):
            return tool.get("server"), tool.get("name")
        return getattr(tool, "server", None), getattr(tool, "name", None)

    @staticmethod
    def _group_mcp_tools_by_server(tools: list[Any]) -> dict[str, list[str]]:
        tools_by_server: dict[str, list[str]] = {}
        for tool in tools:
            server_name, tool_name = SkillRegistry._mcp_tool_identity(tool)
            if server_name and tool_name:
                tools_by_server.setdefault(server_name, []).append(tool_name)
        return tools_by_server

    def load_mcp_skills(self, client: Any) -> int:
        """Auto-register one Skill per registered MCP server.

        Closes the duplication between ``mcp-servers/`` capability
        bundles and the companion's builtin skills: an MCP
        server's tool set becomes selectable as a skill bundle without
        the user editing a SKILL.md by hand.

        Idempotent — re-running just refreshes the synthesized skills.
        Returns the number of skills registered.
        """
        try:
            servers = client.mcp.list_servers()
        except Exception as e:
            logger.debug("load_mcp_skills: list_servers failed: %s", e)
            return 0

        try:
            tools = client.mcp.list_tools()
        except Exception as e:
            logger.debug("load_mcp_skills: list_tools failed: %s", e)
            tools = []

        tools_by_server = self._group_mcp_tools_by_server(tools)

        count = 0
        for srv in servers:
            srv_name = self._mcp_server_name(srv)
            if not srv_name:
                continue
            description = (
                srv.get("description") if isinstance(srv, dict) else ""
            ) or f"Tools from MCP server '{srv_name}'"
            srv_tools = sorted(set(tools_by_server.get(srv_name, [])))
            skill = Skill(
                name=f"mcp:{srv_name}",
                description=description,
                system_prompt=(
                    f"You have access to the MCP server '{srv_name}'. "
                    f"Available tools: {', '.join(srv_tools) or '(none)'}. "
                    "Invoke them via the mcp_invoke tool when relevant."
                ),
                tool_names=srv_tools,
                keywords=[srv_name.lower()],
                source="mcp",
            )
            self.register(skill)
            count += 1
        return count

    # -- skill directories ----------------------------------------------------

    # Directories scanned for user skills.  The companion directory is the
    # primary location; the CLI ``r4 skill install`` directory is also scanned
    # so that NVIDIA Agent Skills (and any other skill installed via the CLI)
    # are visible to the companion session.
    _USER_SKILL_DIRS: list[Path] = [
        SKILLS_DIR,
        Path.home() / ".config" / "r4" / "skills",
    ]

    @staticmethod
    def _project_skill_dirs() -> list[Path]:
        """Return project-scoped skill directories (relative to cwd)."""
        return [
            Path.cwd() / ".r4" / "skills",
            Path.cwd() / "skills",
        ]

    def _user_skill_dirs(self, directory: Path | None = None) -> list[Path]:
        if directory is not None:
            return [directory]
        return list(self._USER_SKILL_DIRS) + list(self._project_skill_dirs())

    def _register_parsed_skill(self, skill: Skill, entry: Path, source: str | None) -> None:
        if source is not None:
            skill.source = source
        else:
            is_project = ".r4" in entry.parts or entry.parts[-2] == "skills"
            skill.source = "project" if is_project else "user"
        self.register(skill)

    def _load_skill_dir(self, directory: Path, seen: set[str], source: str | None = None) -> int:
        if not directory.is_dir():
            return 0

        count = 0
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            key = str(entry.resolve())
            if key in seen:
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.exists():
                continue
            seen.add(key)
            try:
                skill = self._parse_skill_md(skill_file)
                if skill:
                    self._register_parsed_skill(skill, entry, source)
                    count += 1
            except Exception as e:
                logger.warning("Failed to load skill from %s: %s", entry, e)
        return count

    def _load_builtin_skills(self) -> int:
        """Load builtin skills from companion/builtin/*/SKILL.md."""
        return self._load_skill_dir(BUILTIN_SKILLS_DIR, set(), source="builtin")

    def load_user_skills(self, directory: Path | None = None) -> int:
        """Load user-defined skills from disk.

        Each sub-directory holds a general-format ``SKILL.md``
        (front-matter + body; see module docstring).

        Scans both user-level directories (~/.config/r4/companion/skills/
        and ~/.config/r4/skills/) and project-scoped directories
        (./.r4/skills/ and ./skills/) so that skills committed to a
        project repo are auto-discovered.

        Returns number of skills loaded.
        """
        seen: set[str] = set()
        return sum(self._load_skill_dir(d, seen) for d in self._user_skill_dirs(directory))

    # -- SKILL.md parsing --------------------------------------------------------

    @staticmethod
    def _parse_skill_md(path: Path) -> Skill | None:
        """Parse a general Agent Skills format SKILL.md into a Skill.

        Supported front-matter (https://agentskills.io/specification)::

            name            required (default: directory name)
            description     required
            version         optional (semver string; catalog pin — D-27)
            license         optional (informational)
            compatibility   optional (informational)
            allowed-tools   optional; space-separated tool names
            metadata:
              x-r4-keywords      comma/space separated activation keywords
              x-r4-dependencies  comma/space separated skill dependencies

        Legacy keys ``tools`` (inline list), ``keywords``/``tags`` and
        ``dependencies`` (inline lists) are still honored for older files.
        """
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        front = parts[1].strip()
        body = _substitute_contract_refs(parts[2].strip())
        fields = SkillRegistry._parse_frontmatter(front)
        return SkillRegistry._build_skill(path, fields, body)

    @staticmethod
    def _list_field(
        fields: dict[str, Any], metadata: dict[str, Any], meta_key: str, *legacy_keys: str
    ) -> list[str]:
        """First present list-valued field among metadata key and legacy keys.

        The primary key is looked up in BOTH the metadata map and the
        top-level front-matter (top-level wins nothing — metadata wins by
        order), followed by legacy top-level keys.
        """
        candidates = [metadata.get(meta_key), fields.get(meta_key)] + [
            fields.get(k) for k in legacy_keys
        ]
        for value in candidates:
            if value:
                return _split_csv(value)
        return []

    @staticmethod
    def _build_skill(path: Path, fields: dict[str, Any], body: str) -> Skill:
        """Assemble a Skill from parsed front-matter fields and body."""
        name = str(fields.get("name") or path.parent.name).strip()
        if name != path.parent.name:
            # General spec: name should match the parent directory. Warn but
            # accept — never block loading on a naming style difference.
            logger.info(
                "skill name %r does not match directory %r (%s)",
                name,
                path.parent.name,
                path,
            )

        metadata = fields.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return Skill(
            name=name,
            description=str(fields.get("description") or "").strip(),
            system_prompt=body,
            tool_names=SkillRegistry._list_field(
                fields, {}, "allowed-tools", "allowed_tools", "tools"
            ),
            keywords=SkillRegistry._list_field(
                fields, metadata, "x-r4-keywords", "keywords", "tags"
            ),
            dependencies=SkillRegistry._list_field(
                fields, metadata, "x-r4-dependencies", "dependencies"
            ),
            version=str(fields.get("version") or "").strip(),
            tests_dir=str(path.parent / "tests"),
            has_tests=(path.parent / "tests" / "run.sh").is_file(),
        )

    @staticmethod
    def _parse_frontmatter(front: str) -> dict[str, Any]:
        """Parse YAML front-matter into a dict.

        Uses :func:`yaml.safe_load` when PyYAML is available. Falls back to
        a small regex parser (scalars + inline lists) so minimal envs keep
        working.
        """
        try:
            import yaml
        except ImportError:
            yaml = None  # type: ignore[assignment]

        if yaml is not None:
            data = yaml.safe_load(front)
            if isinstance(data, dict):
                return data
            return {}
        # PyYAML is a declared dependency; without it nested metadata
        # (x-r4-keywords, x-r4-dependencies) is lost and auto-activation
        # silently stops working — say so loudly (W3, plan row H14).
        _warn_no_yaml()
        return SkillRegistry._parse_frontmatter_fallback(front)

    @staticmethod
    def _parse_frontmatter_fallback(front: str) -> dict[str, Any]:
        """Regex fallback front-matter parser (no PyYAML): scalars + inline lists."""
        out: dict[str, Any] = {}
        for line in front.splitlines():
            m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
            if not m:
                continue
            key, raw = m.group(1), m.group(2).strip()
            if raw.startswith("[") and raw.endswith("]"):
                items = [s.strip().strip("'\"") for s in raw[1:-1].split(",")]
                out[key] = [s for s in items if s]
            else:
                out[key] = raw.strip("'\"")
        return out
