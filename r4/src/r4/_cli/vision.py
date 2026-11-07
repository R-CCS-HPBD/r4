"""``r4 vision`` command handler."""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import Any

from .base import CommandHandler
from .registry import command


@command
class VisionCommand(CommandHandler):
    name = "vision"
    help = "Vision model inference"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Submit and inspect BentoML vision inference tasks through Gateway/UMRS.",
            epilog=(
                "Examples:\n"
                "  r4 vision submit image.png --model sam3 --adapter sam3\n"
                "  r4 vision status <task-id> --endpoint-id <job-id>\n"
                "  r4 vision get <task-id> --endpoint-id <job-id> --output result.pkl\n"
                "  r4 vision ui --adapter sam3 --endpoint-id <job-id>"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="vision_command",
            title="subcommands",
            metavar="<vision-command>",
        )
        sub.required = True

        submit = sub.add_parser("submit", help="Submit a vision inference task")
        submit.add_argument("image", help="Local image path, S3 URI, or project-scoped object key")
        submit.add_argument("--model", help="Endpoint model name to use")
        submit.add_argument("--endpoint-id", help="Specific endpoint job_id to use")
        submit.add_argument("--adapter", help="Vision adapter name, e.g. sam3 or hmae")
        submit.add_argument("--config-json", help="Inference config as a JSON object")
        submit.add_argument(
            "--config",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Inference config entry. Repeat for multiple values.",
        )
        submit.add_argument("--key", help="Storage key to use when uploading a local image")
        submit.add_argument("--output-bucket", help="Bucket for output artifacts")
        submit.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        status = sub.add_parser("status", help="Show task status")
        status.add_argument("task_id", help="BentoML task ID")
        status.add_argument("--model", help="Endpoint model name")
        status.add_argument("--endpoint-id", help="Specific endpoint job_id")
        status.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        get = sub.add_parser("get", help="Fetch task result")
        get.add_argument("task_id", help="BentoML task ID")
        get.add_argument("--model", help="Endpoint model name")
        get.add_argument("--endpoint-id", help="Specific endpoint job_id")
        get.add_argument("--output", "-o", help="Write raw result bytes to this path")
        get.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output metadata/result as JSON",
        )

        cancel = sub.add_parser("cancel", help="Cancel a pending task")
        cancel.add_argument("task_id", help="BentoML task ID")
        cancel.add_argument("--model", help="Endpoint model name")
        cancel.add_argument("--endpoint-id", help="Specific endpoint job_id")
        cancel.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        ui = sub.add_parser(
            "ui",
            help="Launch the local Gradio inference UI",
            description=(
                "Launch a local Gradio UI for interactive vision inference "
                "against a running endpoint through Gateway/UMRS. "
                "Requires the optional 'gradio' dependency (pip install gradio)."
            ),
        )
        ui.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
        ui.add_argument("--port", type=int, default=7860, help="Bind port (default 7860)")
        ui.add_argument(
            "--adapter", default="hmae", help="Default vision adapter, e.g. sam3 or hmae"
        )
        ui.add_argument("--model", default="", help="Default endpoint model name")
        ui.add_argument("--endpoint-id", default="", help="Default endpoint job_id")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="vision")
        return cmd_vision(args)


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_config(args: Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if getattr(args, "config_json", None):
        data = json.loads(args.config_json)
        if not isinstance(data, dict):
            raise SystemExit("--config-json must be a JSON object")
        config.update(data)
    for item in getattr(args, "config", []) or []:
        if "=" not in item:
            raise SystemExit(f"Invalid --config value {item!r}; expected KEY=VALUE")
        key, value = item.split("=", 1)
        config[key] = _parse_scalar(value)
    return config


def _print(data: Any, *, as_json: bool = False) -> None:
    if as_json or isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str))
        return
    print(data)


def _task_id_from_submit(response: dict[str, Any]) -> str | None:
    task = response.get("task") if isinstance(response, dict) else None
    if isinstance(task, dict):
        return str(task.get("task_id") or task.get("id") or "") or None
    return None


def cmd_vision(args: Namespace) -> int:
    from ..vision import VisionClient

    if args.vision_command == "ui":
        from ..vision_ui import launch_ui

        launch_ui(
            host=args.host,
            port=args.port,
            default_adapter=args.adapter,
            default_model=args.model,
            default_endpoint_id=args.endpoint_id,
        )
        return 0

    client = VisionClient()

    if args.vision_command == "submit":
        result = client.submit(
            args.image,
            model=args.model,
            endpoint_id=args.endpoint_id,
            adapter=args.adapter,
            inference_config=_parse_config(args),
            key=args.key,
            output_bucket=args.output_bucket,
        )
        if args.as_json:
            _print(result, as_json=True)
            return 0
        task_id = _task_id_from_submit(result) or "(unknown)"
        print(f"Task submitted: {task_id}")
        print(f"Endpoint: {result.get('endpoint_id')} ({result.get('endpoint_model')})")
        if result.get("outputs"):
            print("Outputs:")
            for artifact in result["outputs"]:
                print(f"  {artifact.get('uri')}")
        return 0

    if args.vision_command == "status":
        result = client.status(args.task_id, model=args.model, endpoint_id=args.endpoint_id)
        _print(result, as_json=args.as_json)
        return 0

    if args.vision_command == "get":
        result = client.get(
            args.task_id,
            model=args.model,
            endpoint_id=args.endpoint_id,
            output=args.output,
        )
        if isinstance(result, bytes):
            if args.output:
                print(f"Wrote {len(result)} bytes to {Path(args.output)}")
            else:
                print(result.decode("utf-8", errors="replace"))
            return 0
        _print(result, as_json=args.as_json)
        return 0

    if args.vision_command == "cancel":
        result = client.cancel(args.task_id, model=args.model, endpoint_id=args.endpoint_id)
        _print(result, as_json=args.as_json)
        return 0

    return 1
