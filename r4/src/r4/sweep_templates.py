"""Slurm sweep templates — render parameterized sbatch job bundles.

D2 wave: gives scientists a low-node-cost way to define *sets* of jobs from
built-in templates (llm-bench, generic-mpi) with parameter substitution,
without touching UMRS internals.

Templates live in ``r4/resources/sweep_templates/*.yaml``::

    name: llm-bench
    description: Evaluate one model across prompt suites
    defaults:
      partition: ""
      time: 01:00:00
      mem: 32G
    params:
      model: {required: true}
      suite: {default: unit_test}
    script: |
      #SBATCH --job-name=r4sweep-{{template.name}}
      r4 bench run --model {{model}} --suite {{suite}}

``render_plan`` expands ``params`` over a named ``grid`` (list of dicts or a
``{key: [values...]}`` cartesian product) and produces one rendered script
per combination — pure text substitution, so it is cluster-agnostic. An
``#SBATCH --option=`` line whose value renders empty is dropped, so an unset
``partition`` leaves the choice to ``SBATCH_PARTITION`` or the cluster default.

Submission is intentionally separate (``ExperimentRunner``) so this module
stays side-effect-free apart from writing the rendered bundle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

# r4/src/r4/sweep_templates.py → parents[2] is the r4 package root (r4/)
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "resources" / "sweep_templates"


@dataclass
class SweepTemplate:
    """A single sweep template loaded from YAML."""

    name: str
    description: str = ""
    defaults: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    script: str = ""

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, source: str = "") -> "SweepTemplate":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"template missing 'name' in {source or 'payload'}")
        script = str(raw.get("script") or "")
        if not script.strip():
            raise ValueError(f"template '{name}' missing non-empty 'script'")
        return cls(
            name=name,
            description=str(raw.get("description") or ""),
            defaults=_mapping_value(raw, "defaults", name),
            params=_normalize_params(raw, name),
            script=script,
        )

    def required_params(self) -> List[str]:
        return [k for k, cfg in self.params.items() if cfg.get("required") and "default" not in cfg]

    def all_param_names(self) -> List[str]:
        return list(self.params.keys())


def _mapping_value(raw: Dict[str, Any], key: str, template_name: str) -> Dict[str, Any]:
    """Pull a dict-valued field or raise a template-shaped error."""
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"template '{template_name}' {key} must be a mapping")
    return dict(value)


def _normalize_params(raw: Dict[str, Any], template_name: str) -> Dict[str, Dict[str, Any]]:
    """Coerce param specs into the {dtype}-style cfg mapping."""
    params = _mapping_value(raw, "params", template_name)
    return {str(k): (v if isinstance(v, dict) else {"default": v}) for k, v in params.items()}


@dataclass
class RenderedJob:
    """One expanded parameter combination with its rendered sbatch text."""

    index: int
    combo: Dict[str, Any]
    script: str
    job_name: str


def load_template(name: str, template_dir: Optional[Path] = None) -> SweepTemplate:
    """Load one template by name from the template pack."""
    root = Path(template_dir) if template_dir else TEMPLATE_DIR
    candidate = root / f"{name}.yaml"
    if not candidate.exists():
        raise FileNotFoundError(f"sweep template not found: {name} (looked in {root})")
    raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    return SweepTemplate.from_dict(raw, source=str(candidate))


def list_templates(template_dir: Optional[Path] = None) -> List[SweepTemplate]:
    """List all templates in the pack, sorted by name."""
    root = Path(template_dir) if template_dir else TEMPLATE_DIR
    if not root.exists():
        return []
    templates = []
    for path in sorted(root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            templates.append(SweepTemplate.from_dict(raw, source=str(path)))
        except ValueError:
            continue
    return templates


def _cartesian_combos(grid: Dict[str, Iterable[Any]]) -> List[Dict[str, Any]]:
    """Build the cartesian product of grid values."""
    keys = list(grid.keys())
    values = [list(grid[k]) for k in keys]
    if any(len(v) == 0 for v in values):
        raise ValueError("grid values must be non-empty")
    return [dict(zip(keys, tup)) for tup in product(*values)]


def expand_grid(
    grid: Optional[Dict[str, Iterable[Any]]] = None,
    combos: Optional[List[Dict[str, Any]]] = None,
    max_jobs: int = 256,
) -> List[Dict[str, Any]]:
    """Expand either an explicit list of combos or a dict-of-lists cartesian grid."""
    if combos is not None:
        expanded = [dict(c) for c in combos]
    elif grid:
        expanded = _cartesian_combos(grid)
    else:
        raise ValueError("either --combo or a params grid is required")
    if len(expanded) > max_jobs:
        raise ValueError(f"sweep too large: {len(expanded)} combos > max_jobs={max_jobs}")
    return expanded


_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_EMPTY_SBATCH_OPTION = re.compile(r"^[ \t]*#SBATCH[ \t]+--[\w-]+=[ \t]*$\n?", re.MULTILINE)


def _merge_combo(template: SweepTemplate, combo: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Merge template defaults + param defaults + combo overrides; validate required."""
    merged: Dict[str, Any] = dict(template.defaults)
    merged.update(combo)
    for name, cfg in template.params.items():
        if name not in merged and "default" in cfg:
            merged[name] = cfg["default"]
    for missing in template.required_params():
        if missing not in merged:
            raise ValueError(
                f"combo #{index + 1} missing required param '{missing}' "
                f"(template '{template.name}')"
            )
    return merged


def _render_one_script(template: SweepTemplate, merged: Dict[str, Any]) -> str:
    """Substitute ``{{placeholder}}`` references in the template script."""
    body = template.script
    for key, value in merged.items():
        body = _PLACEHOLDER.sub(
            lambda m, k=key, v=value: str(v) if m.group(1) == k else m.group(0),
            body,
        )
    leftover = _PLACEHOLDER.findall(body)
    if leftover:
        raise ValueError(f"template '{template.name}' has unresolved placeholders: {leftover}")
    return _EMPTY_SBATCH_OPTION.sub("", body)


def render_plan(
    template: SweepTemplate, combos: List[Dict[str, Any]], *, job_name_prefix: str = "r4sweep"
) -> List[RenderedJob]:
    """Render every combo into an sbatch script body."""
    rendered: List[RenderedJob] = []
    for idx, combo in enumerate(combos):
        merged = _merge_combo(template, combo, idx)
        body = _render_one_script(template, merged)
        rendered.append(
            RenderedJob(
                index=idx,
                combo=merged,
                script=body,
                job_name=f"{job_name_prefix}-{template.name}-{idx + 1:03d}",
            )
        )
    return rendered


def write_bundle(plan: List[RenderedJob], out_dir: Path) -> Dict[str, Path]:
    """Write per-job sbatch files + a manifest for later submit."""
    out_dir = Path(out_dir)
    jobs_dir = out_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for job in plan:
        path = jobs_dir / f"{job.job_name}.sbatch"
        path.write_text(job.script, encoding="utf-8")
        entries.append(
            {
                "index": job.index,
                "job_name": job.job_name,
                "params": job.combo,
                "script": path.name,
            }
        )
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps({"jobs": entries}, indent=2), encoding="utf-8")
    return {"jobs_dir": jobs_dir, "manifest": manifest}
