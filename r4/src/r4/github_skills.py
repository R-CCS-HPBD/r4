"""Generic GitHub skill installer.

Generalizes the NVIDIA/skills integration to support installing
Agent Skills from **any** GitHub repository. Skills follow the
Agent Skills specification (https://agentskills.io/specification)
and are identified by a directory containing ``SKILL.md``.

Supported source formats:
    github:owner/repo                         Install all skills under skills/
    github:owner/repo/skills/                 Same, explicit path
    github:owner/repo/skills/my-skill         Install a single skill
    owner/repo                                Shorthand (no github: prefix)

Usage (Python):
    from r4.github_skills import install_from_github
    info = install_from_github("nvidia/skills", "skills/cudaq-guide", target_dir)

All network access uses stdlib urllib. An optional GITHUB_TOKEN
environment variable raises the GitHub API rate limit from 60 to
5000 requests/hour.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com/repos"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"


# -- HTTP helpers -----------------------------------------------------------


def _get_github_token() -> str | None:
    """Return optional GitHub token from env."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _github_api_get(path: str, token: str | None = None) -> Any:
    """GET request to GitHub API with optional auth."""
    url = f"{GITHUB_API_BASE}/{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "r4-github-skills")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _github_raw_get(repo: str, branch: str, path: str) -> bytes:
    """Fetch raw file content from GitHub."""
    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "r4-github-skills")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _download_file(download_url: str, fallback_raw: str | None = None) -> bytes:
    """Download a file from GitHub, with optional raw fallback."""
    try:
        req = urllib.request.Request(download_url)
        req.add_header("User-Agent", "r4-github-skills")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        if fallback_raw:
            req = urllib.request.Request(fallback_raw)
            req.add_header("User-Agent", "r4-github-skills")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        raise


# -- Source parsing ---------------------------------------------------------


def parse_source(source: str) -> dict[str, str]:
    """Parse a skill source string into components.

    Supported formats:
        github:owner/repo                          → skills/ prefix
        github:owner/repo/skills/                  → explicit skills dir
        github:owner/repo/skills/my-skill          → single skill
        github:owner/repo/path/to/skills/my-skill  → custom path
        owner/repo                                 → shorthand
        owner/repo/skills/my-skill                 → shorthand with path

    Returns dict with keys:
        repo: "owner/repo"
        branch: "main" (default; could be extended)
        skill_path: "skills/my-skill" or "skills/" for all
        skill_name: "my-skill" or "" for all
    """
    # Strip optional github: prefix
    s = source
    if s.startswith("github:"):
        s = s[len("github:") :]

    # Split repo from path
    parts = s.split("/", 2)
    if len(parts) < 2:
        raise ValueError(f"Invalid source format: {source}")

    repo = f"{parts[0]}/{parts[1]}"
    path = parts[2] if len(parts) > 2 else "skills"

    # If path doesn't start with "skills", prepend it
    if not path.startswith("skills"):
        path = f"skills/{path}"

    # If path ends with /, it's a directory listing
    skill_name = ""
    if path.endswith("/"):
        path = path.rstrip("/")
    else:
        # Extract skill name from path
        path_parts = path.split("/")
        skill_name = path_parts[-1]

    return {
        "repo": repo,
        "branch": "main",
        "skill_path": path,
        "skill_name": skill_name,
    }


# -- Catalog operations -----------------------------------------------------


def list_skills(
    repo: str,
    branch: str = "main",
    skills_subdir: str = "skills",
    token: str | None = None,
) -> list[dict[str, Any]]:
    """List all skills in a GitHub repository.

    Args:
        repo: GitHub repo in owner/name format
        branch: Branch name
        skills_subdir: Subdirectory containing skills (default: "skills")
        token: Optional GitHub token

    Returns:
        List of dicts with keys: name, path, url
    """
    path = f"{repo}/contents/{skills_subdir}?ref={branch}"
    entries = _github_api_get(path, token)

    skills = []
    for entry in entries:
        if entry.get("type") == "dir":
            skills.append(
                {
                    "name": entry["name"],
                    "path": entry["path"],
                    "url": entry.get("html_url", ""),
                }
            )
    return skills


def get_skill_info(
    repo: str,
    skill_path: str,
    branch: str = "main",
    token: str | None = None,
) -> dict[str, Any] | None:
    """Get metadata and file list for a specific skill.

    Args:
        repo: GitHub repo in owner/name format
        skill_path: Path to skill directory (e.g. "skills/cudaq-guide")
        branch: Branch name
        token: Optional GitHub token

    Returns:
        Dict with keys: name, files (dict of filename → download_url),
        or None if skill not found.
    """
    path = f"{repo}/contents/{skill_path}?ref={branch}"
    try:
        entries = _github_api_get(path, token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning("Skill not found at %s", skill_path)
            return None
        raise

    # Handle single file response
    if isinstance(entries, dict) and entries.get("type") == "file":
        return {
            "name": entries.get("name", ""),
            "files": {entries["name"]: entries.get("download_url", "")},
        }

    files = {}
    skill_name = skill_path.split("/")[-1]
    for entry in entries:
        if entry.get("type") == "file":
            files[entry["name"]] = entry.get("download_url", "")

    return {"name": skill_name, "files": files}


def install_from_github(
    repo: str,
    skill_path: str,
    target_dir: Path,
    branch: str = "main",
    token: str | None = None,
) -> dict[str, Any] | None:
    """Install a skill from any GitHub repository.

    Args:
        repo: GitHub repo in owner/name format
        skill_path: Path to skill directory (e.g. "skills/cudaq-guide")
        target_dir: Local directory to install the skill to
        branch: Branch name
        token: Optional GitHub token

    Returns:
        Dict with skill info (name, files, installed_path) on success,
        None on failure.
    """
    info = get_skill_info(repo, skill_path, branch, token)
    if not info:
        return None

    target_dir.mkdir(parents=True, exist_ok=True)

    for filename, download_url in info["files"].items():
        try:
            fallback_raw = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_path}/{filename}"
            content = _download_file(download_url, fallback_raw)
            (target_dir / filename).write_bytes(content)
            logger.debug("Downloaded %s", filename)
        except Exception as e:
            logger.error("Failed to download %s: %s", filename, e)
            return None

    # Create companion-compatible config.json from SKILL.md front-matter
    _create_config_json(target_dir)

    # Compute hash for lockfile
    skill_hash = _compute_skill_hash(target_dir)

    info["installed_path"] = target_dir
    info["hash"] = skill_hash
    info["source"] = f"github:{repo}"
    info["skill_path"] = skill_path
    info["branch"] = branch
    return info


def install_all_from_github(
    repo: str,
    target_base: Path,
    branch: str = "main",
    skills_subdir: str = "skills",
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Install all skills from a GitHub repository.

    Args:
        repo: GitHub repo in owner/name format
        target_base: Base directory to install skills to
        branch: Branch name
        skills_subdir: Subdirectory containing skills
        token: Optional GitHub token

    Returns:
        List of install info dicts (one per successfully installed skill)
    """
    skills = list_skills(repo, branch, skills_subdir, token)
    results = []
    for skill in skills:
        target = target_base / skill["name"]
        if target.exists():
            logger.info("Skill '%s' already installed, skipping", skill["name"])
            continue
        info = install_from_github(repo, skill["path"], target, branch, token)
        if info:
            results.append(info)
    return results


# -- Config generation (shared with nvidia_skills.py) -----------------------


def _extract_frontmatter(skill_md_text: str) -> dict[str, Any]:
    """Extract YAML front-matter from a SKILL.md file.

    Returns a dict with parsed values. Only simple key-value and
    inline list ``[a, b, c]`` formats are supported.
    """
    if not skill_md_text.startswith("---"):
        return {}
    parts = skill_md_text.split("---", 2)
    if len(parts) < 3:
        return {}
    front = parts[1].strip()

    result: dict[str, Any] = {}

    # Simple key: value (handle quoted strings)
    for m in re.finditer(r"^(\w[\w\s-]*?):\s*(.+)$", front, re.MULTILINE):
        key = m.group(1).strip()
        val = m.group(2).strip().strip("'\"")
        result[key] = val

    # Inline list: key: [a, b, c]
    for m in re.finditer(r"^(\w[\w\s-]*?):\s*\[(.+?)\]$", front, re.MULTILINE):
        key = m.group(1).strip()
        items = [s.strip().strip("'\"") for s in m.group(2).split(",")]
        result[key] = items

    return result


def _create_config_json(target_dir: Path) -> None:
    """Create a companion-compatible config.json from SKILL.md front-matter.

    This normalizes Agent Skills metadata into the format expected by
    the companion's SkillRegistry, making GitHub-installed skills
    equivalent to other user-installed skills.
    """
    skill_md_path = target_dir / "SKILL.md"
    if not skill_md_path.exists():
        return

    md_text = skill_md_path.read_text(encoding="utf-8", errors="replace")
    fm = _extract_frontmatter(md_text)

    # Map front-matter to companion config
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    config: dict[str, Any] = {
        "name": fm.get("name", target_dir.name),
        "version": fm.get("version", "1.0.0"),
        "description": fm.get("description", ""),
        "author": fm.get("author", ""),
        "keywords": tags,
        "tags": tags,
    }

    config_path = target_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")


def _compute_skill_hash(skill_dir: Path) -> str:
    """Compute SHA256 hash of all files in a skill directory."""
    h = hashlib.sha256()
    for file_path in sorted(skill_dir.rglob("*")):
        if file_path.is_file():
            h.update(file_path.read_bytes())
    return f"sha256:{h.hexdigest()}"
