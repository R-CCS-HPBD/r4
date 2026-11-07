"""r4 recipe CLI — run/list/validate UMRS recipes.

Usage:
    r4 recipe list
    r4 recipe run <recipe-file.yaml> [--param key=value ...]
    r4 recipe validate <recipe-file.yaml>
"""

from __future__ import annotations

import json
import logging
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any, Dict

from r4._cli.base import CommandHandler
from r4._cli.registry import command

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file. Tries PyYAML, falls back to JSON-subset."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text)  # type: ignore[no-any-return]
    except ImportError:
        return json.loads(text)


@command
class RecipeCommand(CommandHandler):
    name = "recipe"
    help = "Manage and run recipes"

    def register(self, subparsers: Any) -> ArgumentParser:
        parser = subparsers.add_parser(self.name, help=self.help)
        sub = parser.add_subparsers(dest="recipe_action")

        # run
        run_p = sub.add_parser("run", help="Run a recipe file")
        run_p.add_argument("file", help="Path to recipe YAML file")
        run_p.add_argument(
            "--param",
            "-p",
            action="append",
            default=[],
            help="Parameter in key=value format",
        )
        run_p.add_argument(
            "--force",
            action="store_true",
            help="Run despite security warnings",
        )

        # validate
        val_p = sub.add_parser("validate", help="Validate a recipe file")
        val_p.add_argument("file", help="Path to recipe YAML file")

        # list
        list_p = sub.add_parser("list", help="List registered recipes")
        list_p.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

        return parser

    def handle(self, args: Namespace) -> int:
        action = getattr(args, "recipe_action", None)
        if action == "validate":
            return self._validate(args)
        if action == "run":
            return self._run(args)
        if action == "list":
            return self._list(args)
        print("Usage: r4 recipe {run|validate|list}", file=sys.stderr)
        return 1

    def _validate(self, args: Namespace) -> int:
        """Validate a recipe YAML file."""
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            return 1

        try:
            # Shared schema lives in r4-contracts (no UMRS dependency).
            from r4_contracts.recipes import RecipeDefinition

            data = _load_yaml(path)
            recipe = RecipeDefinition.from_dict(data)
            errors = recipe.validate()
            if errors:
                print(f"Validation errors in {path}:")
                for e in errors:
                    print(f"  - {e}")
                return 1
            print(f"Recipe '{recipe.name}' v{recipe.version} is valid.")
            print(f"  Steps: {len(recipe.steps)}")
            print(f"  Params: {len(recipe.params)}")
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    def _run(self, args: Namespace) -> int:
        """Run a recipe — local or via UMRS."""
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            return 1

        params = _parse_recipe_params(args.param)

        # Try local recipe module first (portable, user-shareable YAML)
        result = _try_run_local_recipe(path, params, getattr(args, "force", False), args)
        if result >= 0:
            return result

        # Fall back to UMRS durable workflow path
        print(f"Running recipe via UMRS: {path}")
        print(f"Params: {params}")
        print("(Recipe execution delegates to UMRS durable workflow)")
        return 0

    def _list(self, args: Namespace) -> int:
        """List registered recipes."""
        if getattr(args, "as_json", False):
            import json as _json

            print(_json.dumps([], indent=2))
            return 0
        print("Registered recipes:")
        print("  (Connect to UMRS to list remote recipes)")
        return 0


def _parse_recipe_params(params_arg: list[str]) -> dict[str, str]:
    """Parse ``--param key=value`` arguments into a dict."""
    result: dict[str, str] = {}
    for p in params_arg:
        if "=" in p:
            k, v = p.split("=", 1)
            result[k] = v
    return result


def _try_run_local_recipe(
    path: Path, params: dict[str, str], force: bool, args: Namespace | None = None
) -> int:
    """Try loading + running a local recipe. Returns -1 if not a recipe file."""
    try:
        from r4.recipes import load_recipe  # noqa: F811
    except ImportError:
        return -1

    try:
        recipe = load_recipe(path)
    except (ValueError, FileNotFoundError):
        return -1

    if _fail_on_recipe_warnings(recipe, force):
        return 1

    validation_errors = recipe.validate_params(params)
    if validation_errors:
        print("Parameter errors:")
        for e in validation_errors:
            print(f"  - {e}")
        return 1

    resolved_prompt = recipe.resolve_prompt(params)
    print(f"Running local recipe: {recipe.title}")
    print(f"  Model: {recipe.model}")
    print(f"  Tools: {', '.join(recipe.tools) if recipe.tools else '(none)'}")

    return _run_local_recipe(recipe, resolved_prompt, args)


def _fail_on_recipe_warnings(recipe, force: bool) -> bool:
    """Return True if scan found warnings and user didn't --force."""
    from r4.recipes import scan_recipe  # noqa: F811

    warnings = scan_recipe(recipe)
    if not warnings:
        return False
    print(f"⚠  {len(warnings)} security warning(s):")
    for w in warnings:
        print(f"   - {w}")
    if not force:
        print("Use --force to run anyway.")
        return True
    return False


def _run_local_recipe(recipe, resolved_prompt: str, args: Namespace | None = None) -> int:
    """Execute a local recipe via the R4 companion agent.

    Its web tools follow the user's choice (ADR-099 D-2): ``r4 --web``, the
    question on a terminal, else the non-interactive order with its notice
    (RS-1 round-2 fix W2-2: they refused every call)."""
    try:
        from r4._cli.web_access_prompt import cli_agent_web_getter
        from r4.companion import ResearchCompanion
        from r4.companion.tools import get_builtin_tools
        from r4.config import get_config

        config = get_config()
        if recipe.model and recipe.model != "rivault-gpt-oss-static":
            config.model = recipe.model

        # ADR-101: the agent's processes run in the sandbox r4 --sandbox sets,
        # settled before the web question (whose note reads it).
        from r4._cli.sandbox_prompt import cli_agent_sandbox_and_web

        web_access = cli_agent_sandbox_and_web(
            args, config, lambda: cli_agent_web_getter(args, config)
        )
        companion = ResearchCompanion(config=config)

        all_tools = get_builtin_tools(web_access=web_access)
        if recipe.tools:
            allowed = set(recipe.tools)
            active_tools = [t for t in all_tools if t["name"] in allowed]
        else:
            active_tools = all_tools

        print()
        print("─" * 50)
        result = companion.send(
            resolved_prompt,
            tools=active_tools,
            extra_system=recipe.instructions or "",
        )
        print("─" * 50)
        print(result)
        return 0

    except Exception as exc:
        print(f"Recipe execution failed: {exc}", file=sys.stderr)
        return 1
