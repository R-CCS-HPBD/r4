"""NVIDIA Agent Skills integration.

Fetches and installs skills from the NVIDIA/skills GitHub repository
(https://github.com/nvidia/skills). Skills follow the Agent Skills
specification (https://agentskills.io/specification) and ship with:
  - SKILL.md       (skill instructions, YAML frontmatter + body)
  - skill-card.md  (identity and governance card)
  - skill.oms.sig  (detached OMS signature, verifiable against
                    nv-agent-root-cert.pem)

Usage (CLI):
    r4 skill nvidia list                Browse the NVIDIA catalog
    r4 skill nvidia install <name>      Install a skill from the catalog
    r4 skill nvidia search <query>      Search the NVIDIA catalog
    r4 skill nvidia info <name>         Show details of a catalog skill

Usage (Python):
    from r4.nvidia_skills import NvidiaSkillsCatalog
    catalog = NvidiaSkillsCatalog()
    for skill in catalog.list_skills():
        print(skill["name"], skill["description"])

All network access uses stdlib urllib. An optional GITHUB_TOKEN
environment variable raises the GitHub API rate limit from 60 to
5000 requests/hour.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -- Constants --------------------------------------------------------------

NVIDIA_SKILLS_REPO = "nvidia/skills"
NVIDIA_SKILLS_BRANCH = "main"
NVIDIA_SKILLS_SUBDIR = "skills"

GITHUB_API_BASE = "https://api.github.com/repos"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"


# -- HTTP helpers ------------------------------------------------------------


def _get_github_token() -> str | None:
    """Return optional GitHub token from env."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _github_api_get(path: str, token: str | None = None) -> Any:
    """GET request to GitHub API with optional auth.

    Args:
        path: API path after /repos/, e.g. "nvidia/skills/contents/skills"
        token: Optional GitHub token for higher rate limits

    Returns:
        Parsed JSON response

    Raises:
        urllib.error.URLError: On network or HTTP error
    """
    url = f"{GITHUB_API_BASE}/{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "r4-nvidia-skills")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _github_raw_get(repo: str, branch: str, path: str) -> bytes:
    """Fetch raw file content from GitHub.

    Args:
        repo: GitHub repo in owner/name format
        branch: Branch name
        path: Path to file within repo

    Returns:
        Raw file content as bytes

    Raises:
        urllib.error.URLError: On network or HTTP error
    """
    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "r4-nvidia-skills")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# -- Catalog operations ------------------------------------------------------


def list_catalog(token: str | None = None) -> list[dict[str, Any]]:
    """List all available NVIDIA skills from the GitHub repo.

    Returns a list of dicts with keys: name, path, url.
    Each entry represents a skill directory under skills/.
    """
    path = f"{NVIDIA_SKILLS_REPO}/contents/{NVIDIA_SKILLS_SUBDIR}?ref={NVIDIA_SKILLS_BRANCH}"
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


def get_skill_info(skill_name: str, token: str | None = None) -> dict[str, Any] | None:
    """Get metadata and file list for a specific NVIDIA skill.

    Args:
        skill_name: Name of the skill (directory name under skills/)
        token: Optional GitHub token

    Returns:
        Dict with keys: name, files (dict of filename → download_url),
        or None if skill not found.
    """
    path = (
        f"{NVIDIA_SKILLS_REPO}/contents/{NVIDIA_SKILLS_SUBDIR}/{skill_name}"
        f"?ref={NVIDIA_SKILLS_BRANCH}"
    )
    try:
        entries = _github_api_get(path, token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning("NVIDIA skill '%s' not found", skill_name)
            return None
        raise

    files = {}
    for entry in entries:
        if entry.get("type") == "file":
            files[entry["name"]] = entry.get("download_url", "")

    return {"name": skill_name, "files": files}


def install_skill(
    skill_name: str,
    target_dir: Path,
    token: str | None = None,
) -> dict[str, Any] | None:
    """Download and install a NVIDIA skill.

    Fetches all files from skills/<skill_name>/ in the NVIDIA/skills repo
    and saves them to target_dir.

    Args:
        skill_name: Name of the skill (directory name under skills/)
        target_dir: Local directory to install the skill to
        token: Optional GitHub token

    Returns:
        Dict with skill info (name, files, installed_path) on success,
        None on failure.
    """
    info = get_skill_info(skill_name, token)
    if not info:
        return None

    target_dir.mkdir(parents=True, exist_ok=True)

    for filename, download_url in info["files"].items():
        try:
            if download_url:
                req = urllib.request.Request(download_url)
                req.add_header("User-Agent", "r4-nvidia-skills")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read()
            else:
                # Fallback: use raw.githubusercontent.com
                raw_path = f"{NVIDIA_SKILLS_SUBDIR}/{skill_name}/{filename}"
                content = _github_raw_get(NVIDIA_SKILLS_REPO, NVIDIA_SKILLS_BRANCH, raw_path)
            (target_dir / filename).write_bytes(content)
            logger.debug("Downloaded %s", filename)
        except Exception as e:
            logger.error("Failed to download %s: %s", filename, e)
            return None

    # Create a companion-compatible config.json from the SKILL.md
    # front-matter so the skill is visible to ``r4 skill list`` and
    # loadable by the companion's SkillRegistry.
    _create_config_json(target_dir)

    info["installed_path"] = target_dir
    return info


def search_catalog(
    query: str,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Search the NVIDIA skills catalog by name.

    Args:
        query: Search query (case-insensitive substring match)
        token: Optional GitHub token

    Returns:
        List of matching skill dicts with keys: name, path, url.
    """
    all_skills = list_catalog(token)
    q = query.lower()
    return [s for s in all_skills if q in s["name"].lower()]


# -- Config generation ------------------------------------------------------

# Fields from the SKILL.md front-matter that should be mapped into
# the companion-compatible config.json.  The companion's SkillRegistry
# uses ``name``, ``description``, ``keywords``, ``tools``, and
# ``dependencies``.  NVIDIA skills use ``tags`` instead of ``keywords``.


def _extract_frontmatter(skill_md_text: str) -> dict[str, Any]:
    """Extract YAML front-matter from a SKILL.md file.

    Returns a dict with parsed values.  Only simple key-value and
    inline list ``[a, b, c]`` formats are supported — this mirrors the
    companion's regex parser.
    """
    if not skill_md_text.startswith("---"):
        return {}
    parts = skill_md_text.split("---", 2)
    if len(parts) < 3:
        return {}
    front = parts[1].strip()

    result: dict[str, Any] = {}

    # Simple key: value
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

    This normalizes NVIDIA Agent Skills metadata into the format
    expected by the companion's SkillRegistry, making NVIDIA skills
    equivalent to other user-installed skills.
    """
    skill_md_path = target_dir / "SKILL.md"
    if not skill_md_path.exists():
        return

    md_text = skill_md_path.read_text(encoding="utf-8", errors="replace")
    fm = _extract_frontmatter(md_text)

    # Map NVIDIA front-matter to companion config
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    config: dict[str, Any] = {
        "name": fm.get("name", target_dir.name),
        "version": fm.get("version", "1.0.0"),
        "description": fm.get("description", ""),
        "author": fm.get("author", ""),
        "keywords": tags,  # Use tags as keywords for auto-activation
        "tags": tags,
    }

    config_path = target_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
