"""Built-in plan templates and plan sources (ADR-092 D-10).

Built-in templates are package data in ``templates/``. A user may also run
a plan file by path, and the lead may pass a plan inline; neither is a
registry. Saved project templates become UMRS skills with tests and human
review, never a client-side workflow registry (CONSTITUTION §6).

Stdlib only (ADR-092 D-2), with one optional parser: a ``.yaml`` plan is
read only when PyYAML imports, and is refused by name otherwise.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from r4_contracts.team_engine.plan import MAX_PLAN_BYTES

TEMPLATES_DIR = Path(__file__).parent / "templates"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
_PLAN_SUFFIXES = frozenset({".json"}) | _YAML_SUFFIXES
# A plan file is refused before parsing past this size; prepare_plan then
# applies the real 64 KB limit to the parsed plan.
_MAX_FILE_BYTES = 4 * MAX_PLAN_BYTES


def _template_paths() -> dict[str, Path]:
    return {
        path.stem: path
        for path in sorted(TEMPLATES_DIR.glob("*.json"))
        if _NAME_RE.match(path.stem)
    }


def list_templates() -> list[dict[str, Any]]:
    """``[{name, description, params}]`` for every built-in template."""
    rows = []
    for name, path in _template_paths().items():
        plan = _read_json(path)
        rows.append(
            {
                "name": name,
                "description": plan.get("description", ""),
                "params": plan.get("params") or {},
            }
        )
    return rows


def load_template(name: str) -> dict[str, Any]:
    """The raw (unbound) plan of a built-in template."""
    paths = _template_paths()
    if name not in paths:
        raise KeyError(f"no plan template {name!r}; the built-in templates are {sorted(paths)}")
    return _read_json(paths[name])


def resolve_plan_source(ref: str | Path | dict[str, Any]) -> dict[str, Any]:
    """A raw plan from a template name, a ``.json`` (or ``.yaml``) path, or an inline dict.

    A ref with a path separator or a plan suffix is a file; anything else
    is a template name. Raises KeyError for an unknown template name,
    FileNotFoundError for a missing file and ValueError for a file that is
    not one plan object.
    """
    if isinstance(ref, dict):
        return copy.deepcopy(ref)
    text = str(ref)
    path = Path(text).expanduser()
    if path.suffix.lower() in _PLAN_SUFFIXES or "/" in text or "\\" in text:
        return _read_plan_file(path)
    return load_template(text)


def _read_plan_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"no plan file at {path}")
    suffix = path.suffix.lower()
    if suffix not in _PLAN_SUFFIXES:
        raise ValueError(f"{path}: a plan file is .json (or .yaml with PyYAML installed)")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError(f"{path}: larger than {_MAX_FILE_BYTES} bytes")
    if suffix in _YAML_SUFFIXES:
        return _read_yaml(path)
    return _read_json(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    return _as_plan(plan, path)


def _read_yaml(path: Path) -> dict[str, Any]:
    """``yaml.safe_load`` when PyYAML imports. The only non-stdlib import in
    the package: a pure data parser that holds no authority (the import
    fence test names it)."""
    try:
        import yaml
    except ImportError as exc:
        raise ValueError(f"{path}: PyYAML is not installed; write the plan as .json") from exc
    try:
        plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML ({exc})") from exc
    return _as_plan(plan, path)


def _as_plan(plan: Any, path: Path) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError(f"{path}: a plan is one JSON object")
    return plan
