"""
R4 Project Management.

Handles project CRUD operations and profile switching.
A-406: r4 project command support
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProjectConfig:
    """Configuration for a project."""

    project_id: str
    name: Optional[str] = None
    gateway_url: Optional[str] = None
    model: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def security_level(self) -> str:
        """Project-wide security level: ``standard`` or ``high``.

        When ``high``, all chat sessions in this project default to secure
        mode (equivalent to ``--secure``).  The value is stored in
        ``metadata["security_level"]``.
        """
        return str(self.metadata.get("security_level", "standard")).lower()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "gateway_url": self.gateway_url,
            "model": self.model,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectConfig":
        """Create from dictionary."""
        return cls(
            project_id=data["project_id"],
            name=data.get("name"),
            gateway_url=data.get("gateway_url"),
            model=data.get("model"),
            metadata=data.get("metadata", {}),
        )


class ProjectManager:
    """
    Manage project configurations.

    Stores project configs in:
    - $XDG_CONFIG_HOME/r4/projects.json
    - ~/.config/r4/projects.json (fallback)
    """

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir:
            self._config_dir = config_dir
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            if xdg_config:
                self._config_dir = Path(xdg_config) / "r4"
            else:
                self._config_dir = Path.home() / ".config" / "r4"

        self._projects_file = self._config_dir / "projects.json"
        self._state_file = self._config_dir / "state.json"

    def data_dir(self, project_id: str) -> Path:
        """Per-project workspace for ingested artifacts and extracted knowledge."""
        return self._config_dir / "projects-data" / project_id

    def create(self, project: ProjectConfig) -> None:
        """Create or update a project configuration."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        projects = self._load_projects()
        projects[project.project_id] = project.to_dict()

        self._projects_file.write_text(json.dumps(projects, indent=2))
        logger.info(f"Created project: {project.project_id}")

    def get(self, project_id: str) -> Optional[ProjectConfig]:
        """Get a project configuration."""
        projects = self._load_projects()
        if project_id in projects:
            return ProjectConfig.from_dict(projects[project_id])
        return None

    def list(self) -> List[ProjectConfig]:
        """List all project configurations."""
        projects = self._load_projects()
        return [ProjectConfig.from_dict(p) for p in projects.values()]

    def delete(self, project_id: str) -> bool:
        """Delete a project configuration."""
        projects = self._load_projects()
        if project_id in projects:
            del projects[project_id]
            self._projects_file.write_text(json.dumps(projects, indent=2))

            # Clear the saved selection only when it names the deleted
            # project. get_current() prefers R4_PROJECT_ID, so a tab bridge
            # deleting its own env project cleared every tab's global
            # state.json selection (TUI audit r2 #48).
            if self._load_state().get("current_project") == project_id:
                self.set_current(None)

            logger.info(f"Deleted project: {project_id}")
            return True
        return False

    def get_current(self) -> Optional[str]:
        """Get the current active project ID."""
        # Environment takes priority
        env_project = os.environ.get("R4_PROJECT_ID")
        if env_project:
            return env_project

        # Check state file
        state = self._load_state()
        return state.get("current_project")

    def set_current(self, project_id: Optional[str]) -> None:
        """Set the current active project."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_state()
        if project_id:
            state["current_project"] = project_id
        else:
            state.pop("current_project", None)

        self._state_file.write_text(json.dumps(state, indent=2))

        if project_id:
            logger.info(f"Switched to project: {project_id}")

    def get_current_config(self) -> Optional[ProjectConfig]:
        """Get the current project configuration."""
        current = self.get_current()
        if current:
            return self.get(current)
        return None

    def _load_projects(self) -> Dict[str, Any]:
        """Load projects from file."""
        if not self._projects_file.exists():
            return {}
        try:
            return json.loads(self._projects_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load projects: {e}")
            return {}

    def _load_state(self) -> Dict[str, Any]:
        """Load state from file."""
        if not self._state_file.exists():
            return {}
        try:
            return json.loads(self._state_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load state: {e}")
            return {}


# Global project manager singleton
_project_manager: Optional[ProjectManager] = None


def get_project_manager() -> ProjectManager:
    """Get the global project manager."""
    global _project_manager
    if _project_manager is None:
        _project_manager = ProjectManager()
    return _project_manager
