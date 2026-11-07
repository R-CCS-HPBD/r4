"""``r4 sweep`` — render parameterized sbatch job bundles from templates.

D2 moat feature. Two verbs this wave:
- ``r4 sweep templates`` — list the built-in template pack
- ``r4 sweep render``  — expand a param grid and write per-job .sbatch files
                         plus a manifest (no cluster submission)

Submission via `ExperimentRunner` stays deferred (D2b) while the cluster
budget watch is active — the node-cost stance from 2026-07-30.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

from ..sweep_templates import (
    expand_grid,
    list_templates,
    load_template,
    render_plan,
    write_bundle,
)
from .base import CommandHandler
from .registry import command


def _parse_combo_arg(raw: str | None) -> list[dict] | None:
    if raw is None:
        return None
    if raw == "-":
        raw = sys.stdin.read()
    combos = json.loads(raw)
    if isinstance(combos, dict) and "combos" in combos:
        combos = combos["combos"]
    if not isinstance(combos, list):
        raise ValueError("--combos payload must be a JSON list or {'combos': [...]} object")
    return combos


def _parse_grid_params(kvs: list[str]) -> dict[str, list[str]]:
    grid: dict[str, list[str]] = {}
    for kv in kvs or []:
        if "=" not in kv:
            raise ValueError(f"--param expects name=v1,v2,... (got {kv!r})")
        name, values = kv.split("=", 1)
        name = name.strip()
        parts = [v.strip() for v in values.split(",") if v.strip()]
        if not name or not parts:
            raise ValueError(f"--param expects non-empty name and values (got {kv!r})")
        grid[name] = parts
    return grid


def _render(args: Namespace) -> int:
    from ._helpers import _die

    try:
        template = load_template(args.template)
    except FileNotFoundError as exc:
        _die(str(exc), action="sweep render", error_id="sweep.template_not_found")

    try:
        combos = _parse_combo_arg(getattr(args, "combos", None))
        grid = _parse_grid_params(getattr(args, "param", []) or [])
        expanded = expand_grid(grid=grid, combos=combos, max_jobs=args.max_jobs)
        plan = render_plan(template, expanded, job_name_prefix=args.job_name_prefix)
    except (ValueError, FileNotFoundError) as exc:
        _die(str(exc), action="sweep render", error_id="sweep.render_invalid")

    out_dir = Path(args.out)
    paths = write_bundle(plan, out_dir)

    if args.json:
        print(
            json.dumps(
                {
                    "template": template.name,
                    "jobs": len(plan),
                    "manifest": str(paths["manifest"]),
                    "jobs_dir": str(paths["jobs_dir"]),
                }
            )
        )
        return 0

    print(f"✓ {len(plan)} job script(s) rendered → {paths['jobs_dir']}")
    print(f"  manifest: {paths['manifest']}")
    for job in plan[:5]:
        print(f"  - {job.job_name}.sbatch")
    if len(plan) > 5:
        print(f"  … {len(plan) - 5} more")
    print(
        "\n  submit manually:  cd {jobs_dir} && sbatch <script>.sbatch".format(
            jobs_dir=paths["jobs_dir"]
        )
    )
    return 0


def _templates(args: Namespace) -> int:
    templates = list_templates()
    # Bare `r4 sweep` lands here without the templates parser's --json.
    if getattr(args, "json", False):
        print(
            json.dumps(
                [
                    {
                        "name": t.name,
                        "description": t.description,
                        "params": t.all_param_names(),
                        "defaults": t.defaults,
                    }
                    for t in templates
                ]
            )
        )
        return 0
    if not templates:
        print("No sweep templates installed.")
        return 0
    for t in templates:
        params = ", ".join(t.all_param_names()) or "(none)"
        print(f"  {t.name:<16} {t.description or '(no description)'}  params: {params}")
    return 0


@command
class SweepCommand(CommandHandler):
    """``r4 sweep`` — parameterized sbatch bundles from templates."""

    name = "sweep"
    help = "Render parameterized sbatch job bundles from sweep templates"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        sub = p.add_subparsers(dest="sweep_action", metavar="<action>")
        sub.required = False

        tpl = sub.add_parser("templates", help="List installed sweep templates")
        tpl.add_argument("--json", "-j", action="store_true")

        rnd = sub.add_parser("render", help="Render per-job .sbatch scripts")
        rnd.add_argument("template", help="Template name (see `r4 sweep templates`)")
        rnd.add_argument(
            "--param",
            "-p",
            action="append",
            metavar="NAME=v1,v2,...",
            help="Grid axis; repeat to add axes (cartesian product)",
        )
        rnd.add_argument(
            "--combos",
            metavar="JSON|-",
            help="Explicit combo list as JSON (or '-' for stdin); overrides --param grid",
        )
        rnd.add_argument(
            "--max-jobs", type=int, default=256, help="Safety cap on expanded combos (default 256)"
        )
        rnd.add_argument(
            "--job-name-prefix",
            default="r4sweep",
            help="Prefix for generated job names (default r4sweep)",
        )
        rnd.add_argument(
            "--out", required=True, help="Output dir for the rendered bundle (required)"
        )
        rnd.add_argument("--json", "-j", action="store_true")
        return p

    def handle(self, args: Namespace) -> int:
        action = getattr(args, "sweep_action", None)
        if action == "render":
            return _render(args)
        if action == "templates" or action is None:
            return _templates(args)
        from ._helpers import _die

        _die(f"unknown sweep action: {action}", error_id="sweep.action_unknown")
        return 1
