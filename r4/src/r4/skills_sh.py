"""Skills.sh marketplace integration.

Queries the Skills.sh marketplace (https://skills.sh) to discover,
search, and install Agent Skills. Skills.sh aggregates skills from
multiple sources including NVIDIA, community contributors, and
enterprise publishers.

If the Skills.sh API is unavailable, this module falls back to the
Vercel skills CLI (``npx skills add``) which is the reference
implementation of the Agent Skills specification.

Usage (Python):
    from r4.skills_sh import list_skills, search_skills, install_skill
    skills = list_skills()
    info = install_skill("cudaq-guide", target_dir)

All network access uses stdlib urllib.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILLS_SH_WEB = "https://skills.sh"
SKILLS_SH_API = os.environ.get("R4_SKILLS_SH_API", f"{SKILLS_SH_WEB}/api/v1")

_VERCEL_TOKEN_ENV = ("VERCEL_OIDC_TOKEN", "SKILLS_SH_TOKEN", "R4_SKILLS_SH_TOKEN")


class SkillsShAuthError(RuntimeError):
    """Raised when the Skills.sh API requires a Vercel OIDC token we don't have."""


def _vercel_token() -> str | None:
    """Locate a Vercel OIDC token for the Skills.sh API."""
    for var in _VERCEL_TOKEN_ENV:
        token = os.environ.get(var)
        if token:
            return token
    return None


def auth_status() -> bool:
    """True when a token capable of querying the marketplace is configured."""
    return _vercel_token() is not None


# -- HTTP helpers -----------------------------------------------------------


def _api_get(endpoint: str) -> Any:
    """GET request to Skills.sh API (Vercel OIDC bearer when configured)."""
    url = f"{SKILLS_SH_API}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "r4-skills-sh")
    token = _vercel_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SkillsShAuthError(
                "Skills.sh API requires a Vercel OIDC token. "
                "Set VERCEL_OIDC_TOKEN (see https://skills.sh/docs/api#authentication)."
            ) from e
        raise


# -- Catalog operations -----------------------------------------------------


def list_skills() -> list[dict[str, Any]]:
    """List all skills from the Skills.sh marketplace.

    Returns:
        List of dicts with keys: name, description, source, url
    """
    try:
        data = _api_get("skills")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items", data.get("skills", []))
    except SkillsShAuthError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("Skills.sh API unavailable: %s", e)
    return []


def search_skills(query: str) -> list[dict[str, Any]]:
    """Search the Skills.sh marketplace.

    Args:
        query: Search query

    Returns:
        List of matching skill dicts
    """
    try:
        data = _api_get(f"skills/search?q={urllib.parse.quote(query)}")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items", data.get("skills", []))
    except SkillsShAuthError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("Skills.sh search unavailable: %s", e)
    return []


def get_skill_info(skill_name: str) -> dict[str, Any] | None:
    """Get details for a specific skill from Skills.sh.

    Args:
        skill_name: Skill name

    Returns:
        Dict with skill info, or None if not found
    """
    try:
        return _api_get(f"skills/{skill_name}")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("Skills.sh info unavailable: %s", e)
        return None


# -- Installation -----------------------------------------------------------


def install_skill(
    skill_name: str,
    target_dir: Path,
) -> dict[str, Any] | None:
    """Install a skill from the Skills.sh marketplace.

    Tries the Skills.sh API first, then falls back to the Vercel
    skills CLI (``npx skills add``).

    Args:
        skill_name: Skill name on Skills.sh
        target_dir: Local directory to install the skill to

    Returns:
        Dict with skill info on success, None on failure
    """
    # Try Skills.sh API direct download
    info = get_skill_info(skill_name)
    if info and info.get("download_url"):
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            content = _download_url(info["download_url"])
            # If it's a zip/tar, extract; if it's a single file, save
            if info["download_url"].endswith((".zip", ".tar.gz", ".tgz")):
                _extract_archive(content, target_dir)
            else:
                (target_dir / "SKILL.md").write_bytes(content)
            return {"name": skill_name, "installed_path": target_dir, **info}
        except Exception as e:
            logger.warning("Skills.sh direct download failed: %s", e)

    # Fall back to Vercel skills CLI
    if _has_npx():
        return _install_via_vercel_cli(skill_name, target_dir)

    # Last resort: try GitHub
    logger.warning(
        "Skills.sh API unavailable and npx not found. "
        "Try: pip install r4[skills-sh] or install npx."
    )
    return None


def _download_url(url: str) -> bytes:
    """Download content from a URL."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "r4-skills-sh")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _has_npx() -> bool:
    """Check if npx is available."""
    return shutil.which("npx") is not None


def _install_via_vercel_cli(
    skill_name: str,
    target_dir: Path,
) -> dict[str, Any] | None:
    """Install a skill using the Vercel skills CLI.

    Uses ``npx skills add <source> --skill <name> --yes`` to install
    skills from any Agent Skills-compatible source.

    The Vercel CLI handles all marketplace interaction, including
    skills.sh, GitHub repos, and direct URLs.
    """
    if not _has_npx():
        return None

    # Use a temp directory for the Vercel CLI to install to,
    # then move the skill to the target directory.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        try:
            cmd = [
                "npx",
                "skills",
                "add",
                skill_name,
                "--yes",
                "--cwd",
                str(tmp),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error("Vercel skills CLI failed: %s", result.stderr)
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error("Vercel skills CLI failed: %s", e)
            return None

        # Find the installed skill directory
        skill_dir = _find_skill_dir(tmp, skill_name)
        if not skill_dir:
            logger.error("Could not find installed skill in %s", tmp)
            return None

        # Move to target
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_dir, target_dir, dirs_exist_ok=True)

    return {
        "name": skill_name,
        "installed_path": target_dir,
        "source": "skills.sh",
    }


def _find_skill_dir(base: Path, skill_name: str) -> Path | None:
    """Find the installed skill directory after Vercel CLI install."""
    # Vercel CLI installs to .skills/<name>/ or skills/<name>/
    candidates = [
        base / ".skills" / skill_name,
        base / "skills" / skill_name,
        base / skill_name,
    ]
    for c in candidates:
        if c.is_dir():
            return c

    # Search recursively
    for p in base.rglob("SKILL.md"):
        d = p.parent
        if d.name == skill_name or skill_name in d.name:
            return d
        # If only one skill was found, use it
        return d

    return None


def _extract_archive(content: bytes, target: Path) -> None:
    """Extract a zip/tar archive to target directory."""
    import io
    import tarfile
    import zipfile

    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            zf.extractall(target)
    elif content[:2] == b"\x1f\x8b" or content[:5] == b"ustar":
        with tarfile.open(fileobj=io.BytesIO(content)) as tf:
            tf.extractall(target)
    else:
        # Unknown format, write as-is
        (target / "skill.bin").write_bytes(content)
