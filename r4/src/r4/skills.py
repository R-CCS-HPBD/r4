"""r4 skill loader — discovers and loads skill bundles.

Scans local skill directories and fetches from the UMRS skill
registry. Merges local and remote skills into a unified view.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _skill_roots() -> List[tuple[Path, str]]:
    """The roots the agent loads, in its order (later wins by name):
    packaged builtins, the user dirs, then the project dirs.

    Read from the agent's own definitions so the CLI cannot drift from it
    again: it scanned ``~/.agents/skills`` (never loaded by the agent) and
    missed builtins and project skills — "No skills found." on a fresh HOME
    while the agent loaded 29 (defect-wave 2026-10-08 #6).
    """
    from r4.companion.skills import BUILTIN_SKILLS_DIR, SkillRegistry

    roots: List[tuple[Path, str]] = [(BUILTIN_SKILLS_DIR, "builtin")]
    roots += [(path, "user") for path in SkillRegistry._USER_SKILL_DIRS]
    roots += [(path, "project") for path in SkillRegistry._project_skill_dirs()]
    return roots


@dataclass
class LoadedSkill:
    """A skill loaded by r4."""

    name: str
    description: str = ""
    skill_md: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    mcp_config: Optional[Dict[str, Any]] = None
    source: str = "local"  # "builtin", "user", "project", "local" (extra dir), or "umrs"
    path: Optional[Path] = None

    @property
    def has_tests(self) -> bool:
        """D-27: the bundle carries an executable test entry (tests/run.sh)."""
        return self.path is not None and (self.path / "tests" / "run.sh").is_file()

    @property
    def tests_dir(self) -> str:
        return str(self.path / "tests") if self.path else ""

    @property
    def has_mcp(self) -> bool:
        return self.mcp_config is not None


def _first_prose_line(text: str) -> str:
    """First non-heading line of a SKILL.md (description fallback, ≤200 chars)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""


def _agent_parse(skill_md_path: Path) -> Any:
    """The agent's own parse of a SKILL.md (front-matter name and description).

    The prose-line fallback alone showed the front-matter fence ``---`` as
    every builtin's description.
    """
    from r4.companion.skills import SkillRegistry

    try:
        return SkillRegistry._parse_skill_md(skill_md_path)
    except Exception:  # noqa: BLE001 — a malformed SKILL.md falls back to its prose line
        return None


class SkillLoader:
    """Discovers and loads skill bundles from local and remote sources."""

    def __init__(
        self,
        extra_dirs: Optional[List[Path]] = None,
    ) -> None:
        self._roots = _skill_roots() + [(path, "local") for path in extra_dirs or []]
        self._skills: Dict[str, LoadedSkill] = {}

    def scan_local(self) -> List[LoadedSkill]:
        """The skills the agent loads; a later root replaces an earlier one by name."""
        for root, source in self._roots:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir(), key=lambda p: p.name):
                skill = self._load_from_dir(child) if child.is_dir() else None
                if skill:
                    self._skills[skill.name] = replace(skill, source=source)
        return list(self._skills.values())

    def _load_from_dir(self, path: Path) -> Optional[LoadedSkill]:
        """Load a skill from a directory."""
        # Must have SKILL.md or config.json
        skill_md_path = path / "SKILL.md"
        config_path = path / "config.json"
        mcp_path = path / "mcp.json"

        if not skill_md_path.exists() and not config_path.exists():
            return None

        skill_md = self._read_text_if_exists(skill_md_path)
        config = self._read_json_if_exists(config_path, default={})
        mcp_config = self._read_json_if_exists(mcp_path)

        parsed = _agent_parse(skill_md_path) if skill_md else None
        name = config.get("name") or getattr(parsed, "name", "") or path.name
        description = (
            config.get("description", "")
            or getattr(parsed, "description", "")
            or _first_prose_line(skill_md)
        )

        return LoadedSkill(
            name=name,
            description=description,
            skill_md=skill_md,
            config=config,
            mcp_config=mcp_config,
            source="local",
            path=path,
        )

    def get(self, name: str) -> Optional[LoadedSkill]:
        return self._skills.get(name)

    def load_builtin(self, name: str) -> Optional[LoadedSkill]:
        """Load a packaged builtin skill (``companion/builtin/<name>``).

        D-27: the test runner reaches builtin skills through this —
        the bundled skills are not part of the user-skill scan dirs.
        """
        from r4.companion.skills import BUILTIN_SKILLS_DIR

        path = BUILTIN_SKILLS_DIR / name
        if not path.is_dir():
            return None
        skill = self._load_from_dir(path)
        if skill is None:
            return None
        return replace(skill, source="builtin")

    @staticmethod
    def _read_text_if_exists(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _read_json_if_exists(path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Bad %s: %s", path.name, exc)
            return default

    def list_skills(self) -> List[LoadedSkill]:
        return list(self._skills.values())

    def search(self, query: str) -> List[LoadedSkill]:
        """Search loaded skills by name/description."""
        q = query.lower()
        return [
            s for s in self._skills.values() if q in s.name.lower() or q in s.description.lower()
        ]
