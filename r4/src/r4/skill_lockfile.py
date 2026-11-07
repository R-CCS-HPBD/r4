"""Skill lockfile — tracks installed skills and their sources.

The lockfile (``skills-lock.json``) records every skill installed via
``r4 skill install`` or ``r4 skill nvidia install``, along with:

* ``source`` — where the skill came from (e.g. ``github:nvidia/skills``)
* ``sourceType`` — ``github``, ``nvidia``, ``skills-sh``, ``local``, ``umrs``
* ``skillPath`` — path within the source repo (e.g. ``skills/cudaq-guide``)
* ``hash`` — SHA256 of all skill files at install time
* ``version`` — skill version from front-matter
* ``installedAt`` — ISO timestamp of installation

This enables:

1. **Auto-update** — ``r4 skill update [name]`` compares the installed
   hash against the source and re-downloads if newer.
2. **Source tracking** — knowing where each skill came from so updates
   can fetch from the correct source.
3. **Reproducibility** — the lockfile can be committed to a project to
   pin exact skill versions.

Lockfile location: ``~/.config/r4/skills/skills-lock.json``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCKFILE_VERSION = 2
_DEFAULT_SKILLS_DIR = Path.home() / ".config" / "r4" / "skills"


def _lockfile_path() -> Path:
    return Path(os.environ.get("R4_SKILLS_LOCKFILE") or (_DEFAULT_SKILLS_DIR / "skills-lock.json"))


def _load_lockfile() -> dict[str, Any]:
    path = _lockfile_path()
    if not path.exists():
        return {"version": LOCKFILE_VERSION, "skills": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version", 1) < LOCKFILE_VERSION:
            logger.info("Upgrading lockfile from v%s", data.get("version"))
            data["version"] = LOCKFILE_VERSION
            data.setdefault("skills", {})
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load lockfile: %s", e)
        return {"version": LOCKFILE_VERSION, "skills": {}}


def _save_lockfile(data: dict[str, Any]) -> None:
    path = _lockfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file, then rename
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".skills-lock-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _compute_skill_hash(skill_dir: Path) -> str:
    """Compute SHA256 hash of all files in a skill directory."""
    h = hashlib.sha256()
    for file_path in sorted(skill_dir.rglob("*")):
        if file_path.is_file():
            h.update(file_path.read_bytes())
    return f"sha256:{h.hexdigest()}"


# -- Public API -------------------------------------------------------------


def record_install(
    skill_name: str,
    source: str,
    source_type: str,
    skill_path: str = "",
    hash_val: str = "",
    version: str = "",
    installed_path: str = "",
) -> None:
    """Record a skill installation in the lockfile.

    Args:
        skill_name: Skill name
        source: Source URL or repo (e.g. ``github:nvidia/skills``)
        source_type: ``github``, ``nvidia``, ``skills-sh``, ``local``, ``umrs``
        skill_path: Path within the source repo
        hash_val: SHA256 hash of skill files
        version: Skill version
        installed_path: Local installation path
    """
    data = _load_lockfile()
    data["skills"][skill_name] = {
        "source": source,
        "sourceType": source_type,
        "skillPath": skill_path,
        "hash": hash_val,
        "version": version,
        "installedAt": datetime.now(timezone.utc).isoformat(),
        "installedPath": installed_path,
    }
    _save_lockfile(data)


def record_uninstall(skill_name: str) -> None:
    """Remove a skill from the lockfile.

    Args:
        skill_name: Skill name to remove
    """
    data = _load_lockfile()
    data["skills"].pop(skill_name, None)
    _save_lockfile(data)


def get_skill_source(skill_name: str) -> dict[str, Any] | None:
    """Get source metadata for a skill.

    Args:
        skill_name: Skill name

    Returns:
        Dict with source metadata, or None if skill not in lockfile
    """
    data = _load_lockfile()
    return data["skills"].get(skill_name)


def list_installed() -> dict[str, Any]:
    """List all installed skills with their source metadata.

    Returns:
        Dict mapping skill names to source metadata dicts
    """
    data = _load_lockfile()
    return data.get("skills", {})


def compute_current_hash(skill_dir: Path) -> str:
    """Compute the current hash of an installed skill.

    Args:
        skill_dir: Path to the installed skill directory

    Returns:
        SHA256 hash string
    """
    return _compute_skill_hash(skill_dir)


def has_changed(skill_name: str, skill_dir: Path) -> bool:
    """Check if an installed skill has changed since installation.

    Args:
        skill_name: Skill name
        skill_dir: Path to the installed skill directory

    Returns:
        True if the skill has been modified since installation
    """
    source_info = get_skill_source(skill_name)
    if not source_info:
        return False  # Can't determine, assume unchanged

    current_hash = _compute_skill_hash(skill_dir)
    return current_hash != source_info.get("hash", "")
