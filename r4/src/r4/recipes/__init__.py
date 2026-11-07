"""Recipe system — shareable, parameterized agent workflows.

Inspired by goose's recipe YAML format. Recipes are portable, validated
YAML files that define agent workflows with parameters, instructions, tools,
and security constraints. Users can compose, share, fork, and run recipes.

Key differences from UMRS workflows:
- Recipes are r4-side, user-shareable, run on the user's machine
- UMRS workflows are server-side, durable, Slurm-orchestrated
- Recipes target quick, interactive agent sessions (NOT HPC jobs)

Recipe YAML format (v1.0.0):
```yaml
version: "1.0.0"
title: "Summarize Research Papers"
description: "Reads PDFs from a directory and produces summaries"
instructions: "You are a research assistant. Be concise and cite sources."
model: "rivault-gpt-oss-static"
parameters:
  - key: paper_dir
    input_type: string
    requirement: required
    description: "Directory containing PDF files"
  - key: max_papers
    input_type: integer
    requirement: optional
    default: 10
tools:
  - file_read
  - file_write
  - grep_search
  - shell_exec
prompt: |
  Read up to {{ max_papers }} PDF files from {{ paper_dir }}.
  For each paper, write a one-paragraph summary.
  Save all summaries to summaries.md.
```
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RecipeParameter:
    """A single parameter in a recipe."""

    key: str
    input_type: str = "string"  # string, integer, float, boolean
    requirement: str = "optional"  # required, optional
    default: Any = None
    description: str = ""

    def validate_value(self, value: Any) -> List[str]:
        """Validate a value against this parameter's type. Returns list of errors."""
        errors: List[str] = []
        expected = self.input_type
        if expected == "string":
            if not isinstance(value, str):
                errors.append(f"Parameter '{self.key}' expected string, got {type(value).__name__}")
        elif expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(
                    f"Parameter '{self.key}' expected integer, got {type(value).__name__}"
                )
        elif expected == "float":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"Parameter '{self.key}' expected float, got {type(value).__name__}")
        elif expected == "boolean":
            if not isinstance(value, bool):
                errors.append(
                    f"Parameter '{self.key}' expected boolean, got {type(value).__name__}"
                )
        return errors


@dataclass
class RecipeDefinition:
    """A validated recipe definition."""

    version: str
    title: str
    description: str = ""
    instructions: str = ""
    model: str = "rivault-gpt-oss-static"
    parameters: List[RecipeParameter] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    prompt: str = ""
    min_r4_version: Optional[str] = None
    max_iterations: int = 20
    yolo_mode: bool = False  # auto-approve all tools
    source_path: Optional[Path] = None  # where this was loaded from

    def resolve_prompt(self, param_values: Dict[str, Any]) -> str:
        """Substitute ``{{ key }}`` placeholders in the prompt template."""
        result = self.prompt
        for param in self.parameters:
            value = param_values.get(param.key, param.default)
            result = result.replace(f"{{{{ {param.key} }}}}", str(value))
        return result

    def validate_params(self, param_values: Dict[str, Any]) -> List[str]:
        """Validate provided parameter values against the recipe definition."""
        errors: List[str] = []

        for param in self.parameters:
            if param.key not in param_values:
                if param.requirement == "required":
                    errors.append(f"Missing required parameter: '{param.key}'")
                continue
            errors.extend(param.validate_value(param_values[param.key]))

        return errors

    def validate_references(self) -> List[str]:
        """Validate that referenced tools exist. Returns list of warnings."""
        from r4.agent.tools import get_builtin_registry

        known = set(t.name for t in get_builtin_registry().definitions())
        return [f"Unknown tool: '{t}'" for t in self.tools if t not in known]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_recipe(path: Path) -> RecipeDefinition:
    """Load and validate a recipe from a YAML file.

    Raises:
        FileNotFoundError: If *path* doesn't exist.
        ValueError: If the YAML is invalid or the recipe schema is wrong.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Recipe file not found: {path}")

    with path.open(encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Recipe {path} must be a YAML object, got {type(raw).__name__}")

    return _parse_recipe(raw, source_path=path)


def load_recipe_string(yaml_str: str, *, source_name: str = "<string>") -> RecipeDefinition:
    """Parse a recipe from a YAML string."""
    try:
        raw = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {source_name}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Recipe must be a YAML object, got {type(raw).__name__}")

    return _parse_recipe(raw, source_path=Path(source_name))


# ---------------------------------------------------------------------------
# Scanning (security + structure)
# ---------------------------------------------------------------------------


def scan_recipe(recipe: RecipeDefinition) -> List[str]:
    """Security-scan a recipe for suspicious patterns.

    Returns a list of warnings. An empty list means no issues found.
    Inspired by goose's recipe scanner.
    """
    warnings: List[str] = []

    # Check version
    if not recipe.version:
        warnings.append("Missing recipe version")
    elif not re.match(r"^\d+\.\d+\.\d+$", recipe.version):
        warnings.append(f"Suspicious version format: '{recipe.version}'")

    # Check prompt for dangerous patterns
    dangerous = [
        (r"rm\s+-rf\s+/", "recursive root delete"),
        (r"sudo\s+", "sudo usage"),
        (r"curl.*\|.*sh", "pipe-to-shell"),
        (r"eval\s+\$", "eval with variable"),
    ]
    for pattern, desc in dangerous:
        if re.search(pattern, recipe.prompt):
            warnings.append(f"Dangerous pattern in prompt ({desc}): '{pattern}'")

    # Validate tool references
    warnings.extend(recipe.validate_references())

    # Check for suspicious shell patterns in tools
    if "shell_exec" in recipe.tools and recipe.yolo_mode:
        warnings.append("Recipe uses shell_exec with yolo_mode — all commands auto-approved")

    return warnings


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_recipes(search_paths: List[Path]) -> Dict[str, RecipeDefinition]:
    """Discover all ``*.recipe.yaml`` files under the given paths.

    Returns a dict mapping recipe title to its definition.
    Duplicate titles get a ``(2)`` suffix.
    """
    recipes: Dict[str, RecipeDefinition] = {}
    for sp in search_paths:
        if not sp.exists():
            continue
        for yaml_path in sp.rglob("*.recipe.yaml"):
            try:
                recipe = load_recipe(yaml_path)
            except Exception as exc:
                logger.warning("Skipping %s: %s", yaml_path, exc)
                continue

            # Deduplicate titles
            key = recipe.title
            counter = 1
            while key in recipes:
                counter += 1
                key = f"{recipe.title} ({counter})"
            recipes[key] = recipe

    return recipes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_recipe(raw: Dict[str, Any], *, source_path: Optional[Path]) -> RecipeDefinition:
    """Parse a raw YAML dict into a RecipeDefinition."""
    version = str(raw.get("version", "1.0.0"))
    title = str(raw.get("title", ""))
    if not title:
        raise ValueError("Recipe must have a 'title' field")

    params = []
    for p in raw.get("parameters", []) or []:
        params.append(
            RecipeParameter(
                key=str(p.get("key", "")),
                input_type=str(p.get("input_type", "string")),
                requirement=str(p.get("requirement", "optional")),
                default=p.get("default"),
                description=str(p.get("description", "")),
            )
        )

    return RecipeDefinition(
        version=version,
        title=title,
        description=str(raw.get("description", "")),
        instructions=str(raw.get("instructions", "")),
        model=str(raw.get("model", "rivault-gpt-oss-static")),
        parameters=params,
        tools=[str(t) for t in (raw.get("tools", []) or [])],
        prompt=str(raw.get("prompt", "")),
        min_r4_version=raw.get("min_r4_version"),
        max_iterations=int(raw.get("max_iterations", 20)),
        yolo_mode=bool(raw.get("yolo_mode", False)),
        source_path=source_path,
    )
