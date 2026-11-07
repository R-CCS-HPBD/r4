"""``r4 endpoint`` command handler (bug.memo D3 Phase 2i, 2026-04-25).

Ported from ``r4.cli.cmd_endpoint``. Behaviour is byte-identical; the
handler function still lives in ``r4.cli`` and is imported lazily.
The argparse parser stays in ``_build_parser`` in ``r4.cli``.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from ._helpers import (
    _die,
    _endpoint_reference_hint,
    _is_http_404,
    _parse_context_len,
    _pick_model_interactive,
    _prompt_max_model_len,
    _run_with_provision_progress,
    _wait_for_endpoint_ready,
)
from .base import CommandHandler
from .registry import command


def _endpoint_list_cell(value: object, default: str = "") -> str:
    """Return a display-safe endpoint table cell for nullable API fields."""
    if value is None:
        return default
    text = str(value)
    return text if text else default


@command
class EndpointCommand(CommandHandler):
    """``r4 endpoint`` — Mini-RiVault endpoint management."""

    name = "endpoint"
    help = "Mini-RiVault endpoint management"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Provision, inspect, and stop Mini-RiVault endpoints.",
            epilog=(
                "Examples:\n"
                "  r4 endpoint up                  # interactive model picker\n"
                "  r4 endpoint up --model Qwen/... # explicit model\n"
                "  r4 endpoint list\n"
                "  r4 endpoint describe <instance-id>"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="endpoint_command",
            title="subcommands",
            metavar="<endpoint-command>",
        )
        sub.required = True

        # endpoint up
        ep_up = sub.add_parser("up", help="Provision a mini-RiVault endpoint")
        ep_up.add_argument(
            "--model",
            "-m",
            help="Model name or path (interactive picker if omitted on a TTY)",
        )
        ep_up.add_argument(
            "--tier",
            "-t",
            choices=["system", "project", "user"],
            default="user",
            help="Deployment tier (default: user)",
        )
        ep_up.add_argument(
            "--mount-mcp",
            action="append",
            metavar="SRC:DST",
            help="Mount custom MCP server (source:target). Can be used multiple times.",
        )
        ep_up.add_argument(
            "--mount-data",
            metavar="SRC:DST",
            help="Mount project data directory for RAG (source:target)",
        )
        ep_up.add_argument(
            "--mount-rag",
            metavar="SRC:DST",
            help="Mount pre-built RAG index (source:target)",
        )
        ep_up.add_argument(
            "--egress-domains",
            metavar="DOMAINS",
            help="Comma-separated list of allowed egress domains",
        )
        ep_up.add_argument(
            "--max-model-len",
            default=None,
            metavar="N",
            help=(
                "Override vLLM max context length, e.g. 4096 or 32k "
                "(default: model's serving profile)"
            ),
        )
        ep_up.add_argument(
            "--ttl-sec",
            type=int,
            default=None,
            metavar="N",
            help=(
                "Endpoint time-to-live in seconds (0 = no expiry / manual teardown). "
                "(default: server-side TTL, typically 3600)"
            ),
        )
        ep_up.add_argument(
            "--gpu-count",
            type=int,
            default=None,
            metavar="N",
            help=("Number of GPUs per node (default: model's recommended_gpu_count from catalog)"),
        )
        ep_up.add_argument(
            "--node-count",
            type=int,
            default=None,
            metavar="N",
            help=(
                "Number of Slurm nodes for multi-node pipeline parallelism "
                "(default: model's recommended_node_count from catalog)"
            ),
        )
        ep_up.add_argument(
            "--partition",
            default=None,
            metavar="NAME",
            help=(
                "Override the Slurm partition for this endpoint "
                "(e.g. 4n4gpu-p; default: auto-selected from node/GPU count)"
            ),
        )
        ep_up.add_argument(
            "--nodelist",
            default=None,
            metavar="NODES",
            help=(
                "Slurm --nodelist hint pinning the job to specific node(s) "
                "(e.g. dgx-spark-1); forwarded to the launcher as #SBATCH --nodelist"
            ),
        )
        ep_up.add_argument(
            "--parallel-strategy",
            choices=["tp_pp", "tep"],
            default=None,
            dest="parallel_strategy",
            help=(
                "Multi-node parallelism layout: tp_pp (default; TP=GPUs/node, "
                "PP=nodes) or tep (tensor+expert parallel: TP=world size, PP=1). "
                "tep must be canary-validated for KV fit before production use."
            ),
        )
        ep_up.add_argument(
            "--backend",
            choices=["vllm", "bentoml"],
            dest="serving_backend",
            help="Serving backend to launch (default: inferred from model type/profile)",
        )
        ep_up.add_argument(
            "--service-kind",
            choices=["openai", "bentoml_vision"],
            help="Service API kind (default: inferred from backend/model type)",
        )
        ep_up.add_argument(
            "--launch-profile",
            action="append",
            dest="launch_profiles",
            help="Mini-RiVault profile to activate. Repeat to pass multiple profiles.",
        )
        ep_up.add_argument(
            "--co-locate-vllm",
            action="store_true",
            help="For BentoML vision, also launch the vLLM profile",
        )
        ep_up.add_argument(
            "--container-image",
            help="Container image override for the selected backend",
        )
        ep_up.add_argument(
            "--vision-adapter",
            help="Vision adapter name, e.g. sam3 or hmae",
        )
        ep_up.add_argument(
            "--vision-task",
            help="Default vision task for the endpoint",
        )
        ep_up.add_argument(
            "--env-var",
            action="append",
            metavar="KEY=VALUE",
            dest="env_vars",
            help=(
                "Environment variable to pass to the vLLM runtime "
                "(repeatable). Example: --env-var VLLM_MM_ENCODER_TP_MODE=data"
            ),
        )
        ep_up.add_argument(
            "--wait",
            "-w",
            action="store_true",
            help="Wait for endpoint to be ready",
        )
        ep_up.add_argument(
            "--timeout",
            type=int,
            default=600,
            help="Provision/wait timeout in seconds (default: 600)",
        )
        ep_up.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )
        ep_up.add_argument(
            "--public",
            metavar="URL_OR_PROVIDER",
            help=(
                "Use a public endpoint directly (no UMRS provisioning). "
                "Pass a URL for OpenAI-compatible APIs, or 'bedrock' for AWS Bedrock."
            ),
        )
        ep_up.add_argument(
            "--api-key",
            metavar="KEY",
            help="Bearer API key for the public endpoint",
        )
        ep_up.add_argument(
            "--aws-region",
            metavar="REGION",
            help="AWS region for Bedrock (default: us-east-1)",
        )
        ep_up.add_argument(
            "--aws-profile",
            metavar="PROFILE",
            help="AWS credentials profile name for Bedrock",
        )
        ep_up.add_argument(
            "--source",
            "-s",
            metavar="NAME",
            help="Endpoint source to use (see `r4 source list`)",
        )
        ep_up.add_argument(
            "--access",
            action="append",
            metavar="SCOPE",
            help=(
                "Who else may use the endpoint (repeatable): user:<id>, project:<id>, "
                "circle:<proj>/<id>, or system (everyone; admin only). Default: the recipe's "
                "access, else your project only."
            ),
        )

        ep_access = sub.add_parser(
            "access",
            help="Show or set who may use an endpoint (user/project/group/system)",
        )
        ep_access.add_argument("instance_id", help="Endpoint ID")
        ep_access.add_argument(
            "--set",
            dest="access_set",
            action="append",
            metavar="SCOPE",
            help="Replace list (repeatable): user:<id>, project:<id>, circle:<proj>/<id>, system",
        )
        ep_access.add_argument(
            "--private",
            action="store_true",
            help="Clear the list: only the owning project may use the endpoint",
        )

        ep_down = sub.add_parser("down", help="Stop a mini-RiVault endpoint")
        ep_down.add_argument(
            "instance_id",
            nargs="?",
            help="Instance ID to stop",
        )
        ep_down.add_argument(
            "--public",
            action="store_true",
            help="Remove the public endpoint configuration from the active profile",
        )
        ep_down.add_argument(
            "--force",
            "-f",
            action="store_true",
            help="Force stop without graceful shutdown",
        )
        ep_down.add_argument(
            "--source",
            "-s",
            metavar="NAME",
            help=(
                "Endpoint source that owns the instance (see `r4 source list`). "
                "If omitted, the owning source is auto-detected across all "
                "registered sources."
            ),
        )

        ep_list = sub.add_parser("list", help="List active endpoints")
        ep_list.add_argument(
            "--source",
            "-s",
            metavar="NAME",
            help="Endpoint source to query (see `r4 source list`). "
            "If omitted, queries all registered sources.",
        )
        ep_list.add_argument(
            "--all-sources",
            action="store_true",
            help="List endpoints from all registered sources (default when multiple sources exist)",
        )
        ep_list.add_argument(
            "--tier",
            "-t",
            choices=["system", "project", "user"],
            help="Filter by tier",
        )
        ep_list.add_argument(
            "--all",
            "-a",
            action="store_true",
            help="Show all endpoints (including stopped)",
        )
        ep_list.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        ep_status = sub.add_parser("status", help="Get endpoint status")
        ep_status.add_argument("instance_id", help="Instance ID")
        ep_status.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        ep_describe = sub.add_parser("describe", help="Describe an endpoint (alias of `status`)")
        ep_describe.add_argument("instance_id", help="Instance ID")
        ep_describe.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        ep_logs = sub.add_parser("logs", help="Get endpoint logs")
        ep_logs.add_argument("instance_id", help="Instance ID")
        ep_logs.add_argument("--follow", "-f", action="store_true", help="Follow log output")
        ep_logs.add_argument(
            "--tail",
            "-n",
            type=int,
            default=100,
            help="Number of lines to show (default: 100)",
        )

        ep_upgrade = sub.add_parser(
            "upgrade",
            help=(
                "Rolling model-version upgrade of a running endpoint. "
                "Provisions replacement, health-checks chat/completions, "
                "flips the endpoint registry, and retires the old instance "
                "without downtime."
            ),
        )
        ep_upgrade.add_argument(
            "endpoint_id",
            help="Existing endpoint ID (must be active)",
        )
        ep_upgrade.add_argument(
            "--model-path",
            required=True,
            help="New model path (must be registered)",
        )
        ep_upgrade.add_argument(
            "--wait",
            "-w",
            action="store_true",
            help="Wait for upgrade to complete (default: auto)",
        )
        ep_upgrade.add_argument(
            "--timeout",
            type=int,
            default=900,
            help="Health-check timeout in seconds (default: 900)",
        )
        ep_upgrade.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )
        ep_upgrade.add_argument(
            "--source",
            "-s",
            metavar="NAME",
            help=(
                "Endpoint source that owns the instance (see `r4 source list`). "
                "If omitted, auto-detects across all registered sources."
            ),
        )

        return p

    def handle(self, args: Namespace) -> int:

        return cmd_endpoint(args) or 0


def cmd_endpoint(args):
    """Handle endpoint commands for multi-tenant mini-rivault management."""
    shortcut = _handle_public_endpoint_shortcut(args)
    if shortcut is not _NO_ENDPOINT_SHORTCUT:
        return shortcut

    cfg = _resolve_endpoint_config(args)
    public_result = _handle_public_endpoint_config(args, cfg)
    if public_result is not _NO_ENDPOINT_SHORTCUT:
        return public_result

    from ..client import Client
    from ._helpers import _preflight_check

    _preflight_check(action=f"endpoint {args.endpoint_command}")
    client = Client()
    handler = _endpoint_command_handlers().get(args.endpoint_command)
    if handler is None:
        print(
            "Unknown endpoint command. Use: up, down, list, status, logs, access", file=sys.stderr
        )
        sys.exit(1)
    return handler(args, client)


_NO_ENDPOINT_SHORTCUT = object()


def _handle_public_endpoint_shortcut(args):
    if args.endpoint_command == "up" and getattr(args, "public", None):
        from .up import _cmd_up_public

        return _cmd_up_public(args, interactive=sys.stdin.isatty() and sys.stdout.isatty())
    if args.endpoint_command == "down" and getattr(args, "public", False):
        from .down import _cmd_down_public

        return _cmd_down_public(args)
    return _NO_ENDPOINT_SHORTCUT


def _resolve_endpoint_config(args):
    from ..config import get_config, set_config
    from ..source import resolve_active_source

    cfg = get_config()
    source = resolve_active_source(getattr(args, "source", None))
    if source is None:
        return cfg
    cfg = _apply_endpoint_source_config(cfg, source)
    set_config(cfg)
    return cfg


def _apply_endpoint_source_config(cfg, source):
    """``cfg`` re-targeted at ``source`` with only its own credential (S1)."""
    from ..source_context import config_for_source

    return config_for_source(source, cfg)


def _handle_public_endpoint_config(args, cfg):
    if not bool(cfg.endpoint_url or cfg.endpoint_provider):
        return _NO_ENDPOINT_SHORTCUT
    # When multiple sources are registered, let `list` fall through to the
    # multi-source aggregation path so the user sees every source, not just
    # this active public one. Use --source NAME to scope to one source.
    if args.endpoint_command == "list" and _multiple_sources_registered():
        return _NO_ENDPOINT_SHORTCUT
    if args.endpoint_command == "list":
        return _print_public_endpoint_list(args, cfg)
    if args.endpoint_command in ("status", "describe"):
        return _print_public_endpoint_status(cfg)
    return _NO_ENDPOINT_SHORTCUT


def _multiple_sources_registered() -> bool:
    from ..model_routing import count_registered_sources

    return count_registered_sources() > 1


def _public_endpoint_entry(cfg, source=None) -> dict:
    """The configured public endpoint as one ``endpoint list`` row.

    With an active public source the row is that source's: its name
    (``_source``) and only its own model (``default_model`` / ``R4_MODEL``),
    never ``cfg.model``'s built-in profile default, which that source does
    not serve — the TUI's /models offered it as a provisioning row filed
    under "unknown" (stabilization 2026-10-21, slash-a#1 / resize-render#8),
    the shape ``_public_source_endpoints`` already has.
    """
    provider = cfg.endpoint_provider
    entry = {
        "instance_id": "public",
        "tier": "public" if not provider else provider,
        "model": cfg.model,
        "status": "configured",
        "endpoint_url": cfg.endpoint_url or "(native)",
    }
    if source is not None and getattr(source, "kind", None) != "platform":
        from ..source_context import source_model

        entry["model"] = source_model(source, None)
        entry["_source"] = source.name
    return entry


def _print_public_endpoint_list(args, cfg) -> int:
    import json

    from ..source import resolve_active_source

    entry = _public_endpoint_entry(cfg, resolve_active_source(getattr(args, "source", None)))
    if getattr(args, "as_json", False):
        print(json.dumps({"endpoints": [entry]}, indent=2))
        return 0
    print(f"{'INSTANCE ID':<30} {'TIER':<10} {'MODEL':<25} {'STATUS':<12} {'URL'}")
    print("-" * 100)
    print(
        f"{'public':<30} {entry['tier']:<10} {str(entry['model'] or '')[:23]:<25} "
        f"{'configured':<12} {entry['endpoint_url']}"
    )
    return 0


def _print_public_endpoint_status(cfg) -> int:
    print("Public endpoint configured:")
    if cfg.endpoint_provider:
        print(f"  Provider: {cfg.endpoint_provider}")
    if cfg.endpoint_url:
        print(f"  URL: {cfg.endpoint_url}")
    if cfg.model:
        print(f"  Model: {cfg.model}")
    print("  Status: configured")
    return 0


def _endpoint_info_printer(as_json: bool):
    def _info(message: str = "") -> None:
        if not as_json:
            print(message)

    return _info


def _endpoint_picker_type(args):
    if not _endpoint_args_request_vision(args):
        return None
    from ..models import ModelType

    return ModelType.VISION


def _endpoint_args_request_vision(args) -> bool:
    return (
        getattr(args, "serving_backend", None) == "bentoml"
        or getattr(args, "service_kind", None) == "bentoml_vision"
    )


def _resolve_endpoint_up_model(args):
    model = getattr(args, "model", None)
    if model:
        return model, None
    if sys.stdin.isatty() and sys.stdout.isatty():
        model_info = _pick_model_interactive(model_type=_endpoint_picker_type(args))
        return model_info.name, model_info
    _die(
        "--model is required when not running interactively.",
        action="endpoint up",
        hint="r4 endpoint up --model <name>",
    )


def _is_vision_endpoint(args, model_info) -> bool:
    if _endpoint_args_request_vision(args):
        return True
    model_type = getattr(getattr(model_info, "model_type", None), "value", None)
    return model_info is not None and model_type == "vision"


def _model_context_ceiling(model_info) -> int | None:
    if model_info is None:
        return None
    value = model_info.capabilities.max_position_embeddings
    return value or None


def _parse_endpoint_max_model_len(args) -> int | None:
    raw_len = getattr(args, "max_model_len", None)
    max_model_len = _parse_context_len(raw_len) if raw_len else None
    if raw_len and max_model_len is None:
        _die(f"Invalid --max-model-len value: {raw_len!r}", action="endpoint up")
    return max_model_len


def _clamp_max_model_len(max_model_len: int | None, max_allowed: int | None) -> int | None:
    if max_model_len is None or max_allowed is None or max_model_len <= max_allowed:
        return max_model_len
    limit_display = f"{max_allowed // 1024}k" if max_allowed >= 1024 else str(max_allowed)
    print(
        f"  Warning: --max-model-len {max_model_len} exceeds model's "
        f"max_position_embeddings ({limit_display}). Clamping to {limit_display}.",
        file=sys.stderr,
    )
    return max_allowed


def _interactive_context_default(model_info) -> int:
    profile = getattr(model_info, "serving_profile", None)
    if profile and profile.max_model_len:
        return profile.max_model_len
    return 4096


def _resolve_endpoint_context_len(args, model_info) -> int | None:
    max_model_len = _parse_endpoint_max_model_len(args)
    max_allowed = _model_context_ceiling(model_info)
    max_model_len = _clamp_max_model_len(max_model_len, max_allowed)
    if model_info is None or max_model_len is not None or _is_vision_endpoint(args, model_info):
        return max_model_len
    return _prompt_max_model_len(
        default=_interactive_context_default(model_info),
        max_allowed=max_allowed,
    )


def _print_endpoint_up_request(args, model: str, max_model_len: int | None, info) -> None:
    info("Provisioning endpoint via UMRS...")
    info(f"  Model: {model}")
    if max_model_len is not None:
        info(f"  Context Length: {max_model_len}")
    if getattr(args, "serving_backend", None):
        info(f"  Backend: {args.serving_backend}")
    if getattr(args, "service_kind", None):
        info(f"  Service Kind: {args.service_kind}")


def _parse_mount_pair(value: str, *, option: str, hint: str) -> tuple[str, str]:
    parts = value.split(":", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    _die(f"Invalid {option} value: {value!r}", action="endpoint up", hint=hint)


def _parse_mcp_mounts(args, info) -> list[dict[str, str]]:
    mounts = []
    for mount in args.mount_mcp or []:
        source, target = _parse_mount_pair(
            mount,
            option="--mount-mcp",
            hint="Use SRC:DST, for example /home/me/tools:/mcp-servers/tools.",
        )
        mounts.append({"source": source, "target": target})
        info(f"  MCP Mount: {source} -> {target}")
    return mounts


def _parse_named_mount(value: str | None, *, option: str, hint: str, label: str, info):
    if not value:
        return None
    source, target = _parse_mount_pair(value, option=option, hint=hint)
    info(f"  {label}: {source} -> {target}")
    return {"source": source, "target": target}


def _parse_endpoint_mounts(args, info) -> dict:
    mounts = {}
    mcp_mounts = _parse_mcp_mounts(args, info)
    if mcp_mounts:
        mounts["mcp_servers"] = mcp_mounts
    data_mount = _parse_named_mount(
        args.mount_data,
        option="--mount-data",
        hint="Use SRC:DST, for example /project/data:/project.",
        label="Data Mount",
        info=info,
    )
    rag_mount = _parse_named_mount(
        args.mount_rag,
        option="--mount-rag",
        hint="Use SRC:DST, for example /project/rag-index:/rag-index.",
        label="RAG Mount",
        info=info,
    )
    if data_mount:
        mounts["project_data"] = data_mount
    if rag_mount:
        mounts["rag_index"] = rag_mount
    return mounts


def _parse_endpoint_egress_domains(args, info) -> list[str]:
    if not args.egress_domains:
        info("  Egress: offline (no external access)")
        return []
    domains = [domain.strip() for domain in args.egress_domains.split(",")]
    info(f"  Egress Domains: {', '.join(domains)}")
    return domains


def _provision_endpoint(
    args,
    client,
    *,
    model: str,
    mounts: dict,
    egress_domains: list[str],
    max_model_len: int | None,
    ttl_sec: int | None = None,
    quiet: bool,
):
    # Parse --env-var KEY=VALUE entries into a dict for the provision request.
    env_vars: dict[str, str] | None = None
    raw_env_vars = getattr(args, "env_vars", None)
    if raw_env_vars:
        env_vars = {}
        for entry in raw_env_vars:
            if "=" not in entry:
                raise ValueError(f"Invalid --env-var value (expected KEY=VALUE): {entry}")
            key, value = entry.split("=", 1)
            env_vars[key] = value
    return _run_with_provision_progress(
        lambda: client.provision_endpoint(
            model=model,
            tier=args.tier,
            mounts=mounts if mounts else None,
            egress_domains=egress_domains if egress_domains else None,
            timeout=args.timeout,
            max_model_len=max_model_len,
            gpu_count=getattr(args, "gpu_count", None),
            node_count=getattr(args, "node_count", None),
            partition=getattr(args, "partition", None),
            nodelist=getattr(args, "nodelist", None),
            parallel_strategy=getattr(args, "parallel_strategy", None),
            serving_backend=getattr(args, "serving_backend", None),
            service_kind=getattr(args, "service_kind", None),
            launch_profiles=getattr(args, "launch_profiles", None),
            co_locate_vllm=bool(getattr(args, "co_locate_vllm", False)),
            container_image=getattr(args, "container_image", None),
            vision_adapter=getattr(args, "vision_adapter", None),
            ttl_sec=ttl_sec,
            vision_task=getattr(args, "vision_task", None),
            env_vars=env_vars,
            access=getattr(args, "access", None),
        ),
        timeout=args.timeout,
        action="endpoint up",
        client=client,
        model=model,
        quiet=quiet,
    )


def _endpoint_up_result_code(response: dict) -> int:
    return 1 if str(response.get("status", "")).lower() == "failed" else 0


def _print_endpoint_up_response(args, client, response: dict, *, as_json: bool) -> int:
    import json

    result_code = _endpoint_up_result_code(response)
    if as_json:
        print(json.dumps(response, indent=2))
        return result_code
    instance_id = response.get("instance_id", "unknown")
    print(f"\n  Instance ID: {instance_id}")
    print(f"  Status: {response.get('status', 'pending')}")
    if response.get("endpoint_url"):
        print(f"  URL: {response['endpoint_url']}")
    if args.wait:
        _wait_for_endpoint_ready(client, instance_id, timeout=args.timeout, action="endpoint up")
        return 0
    return result_code


def _die_endpoint_up_exception(error: Exception) -> None:
    hint = getattr(error, "hint", None)
    if not hint and "403" in str(error):
        hint = (
            "Access denied. Check that your token has endpoint provisioning "
            "permissions for this project. Re-run `r4 auth status` to verify."
        )
    _die(str(error), action="endpoint up", hint=hint)


def _cmd_endpoint_up(args, client):
    as_json = bool(getattr(args, "as_json", False))
    info = _endpoint_info_printer(as_json)
    model, model_info = _resolve_endpoint_up_model(args)
    max_model_len = _resolve_endpoint_context_len(args, model_info)
    _print_endpoint_up_request(args, model, max_model_len, info)
    mounts = _parse_endpoint_mounts(args, info)
    egress_domains = _parse_endpoint_egress_domains(args, info)
    try:
        response = _provision_endpoint(
            args,
            client,
            model=model,
            mounts=mounts,
            egress_domains=egress_domains,
            ttl_sec=getattr(args, "ttl_sec", None),
            max_model_len=max_model_len,
            quiet=as_json,
        )
        return _print_endpoint_up_response(args, client, response, as_json=as_json)
    except Exception as error:
        _die_endpoint_up_exception(error)


def _cmd_endpoint_down(args, client):
    if not getattr(args, "instance_id", None):
        _die(
            "instance id is required unless using --public",
            action="endpoint down",
            hint="Use `r4 endpoint down <instance-id>` or `r4 endpoint down --public`.",
        )
    client = _resolve_down_client_for_instance(args, client)
    print(f"Stopping endpoint {args.instance_id}...")
    try:
        response = client.stop_endpoint(args.instance_id, force=args.force)
        print(f"  Status: {response.get('status', 'stopped')}")
    except Exception as error:
        hint = (
            _endpoint_reference_hint(client, args.instance_id, command="down")
            if _is_http_404(error)
            else None
        )
        _die(str(error), action="endpoint down", hint=hint)


def _source_owning_instance(candidate, instance_id: str):
    """``candidate`` when its endpoint list holds ``instance_id`` (best-effort probe)."""
    try:
        response = candidate.list_endpoints(include_stopped=True)
    except Exception:  # noqa: BLE001 - probing sources is best-effort
        return None
    endpoints = response.get("endpoints", [])
    if any(str(endpoint.get("instance_id")) == instance_id for endpoint in endpoints):
        return candidate
    return None


def _resolve_down_client_for_instance(args, client):
    """Return the Client whose source actually owns ``args.instance_id``.

    ``r4 endpoint list`` aggregates endpoints across every registered source,
    but ``r4 endpoint down <id>`` historically sent the deprovision to the
    active/default source only. When the instance lived on a *different*
    source (e.g. a ``rccs-cloud`` endpoint while the active source is
    ``ai4s``), the deprovision hit the wrong registry and returned a spurious
    ``404``. To avoid that, when no explicit ``--source`` is given and more
    than one source is registered, scan each platform source's endpoint list
    (including still-provisioning ones) and target the source that owns the id.
    """
    # An explicit --source was already applied to the active config by
    # cmd_endpoint()/_resolve_endpoint_config(); honour it as-is.
    if getattr(args, "source", None):
        return client

    from ..model_routing import count_registered_sources

    if count_registered_sources() <= 1:
        return client

    from ..client import Client
    from ..config import get_config
    from ..source import get_source_store
    from ..source_context import config_for_source

    instance_id = str(args.instance_id)
    base_config = get_config()
    for source in get_source_store().list():
        if source.kind != "platform":
            continue
        cfg = config_for_source(source, base_config)  # its own credential only (S1)
        candidate = _source_owning_instance(Client(config=cfg), instance_id)
        if candidate is not None:
            if source.name:
                print(f"  (endpoint {instance_id} is on source '{source.name}')")
            return candidate

    # Not found on any source — fall back to the default client so the user
    # still gets a clear 404 with a reference hint listing valid instance ids.
    return client


def _endpoint_status_value(endpoint: dict) -> str:
    status = _endpoint_list_cell(endpoint.get("status"), "unknown")
    expires_at = endpoint.get("expires_at")
    if not expires_at or status != "ready":
        return status
    from datetime import datetime, timezone

    try:
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return status
    return "expired" if exp_dt < datetime.now(timezone.utc) else status


def _print_endpoint_list_table(endpoints: list[dict]) -> None:
    if not endpoints:
        print("No active endpoints")
        return
    print(f"{'INSTANCE ID':<30} {'TIER':<10} {'MODEL':<25} {'STATUS':<10} {'URL'}")
    print("-" * 100)
    for endpoint in endpoints:
        instance_id = _endpoint_list_cell(endpoint.get("instance_id"), "unknown")[:28]
        tier = _endpoint_list_cell(endpoint.get("tier"), "n/a")
        model = _endpoint_list_cell(
            endpoint.get("model") or endpoint.get("served_model_name"),
            "",
        )[:23]
        status = _endpoint_status_value(endpoint)
        url = _endpoint_list_cell(endpoint.get("endpoint_url"), "")
        print(f"{instance_id:<30} {tier:<10} {model:<25} {status:<10} {url}")


def _cmd_endpoint_list(args, client):
    import json

    from ..model_routing import count_registered_sources

    # When multiple sources are registered, default to showing endpoints from
    # ALL sources so the user sees everything at once. Use --source NAME to
    # query a single source explicitly.
    explicit_source = getattr(args, "source", None)
    all_sources_flag = getattr(args, "all_sources", False)
    use_all_sources = all_sources_flag or (not explicit_source and count_registered_sources() > 1)

    if use_all_sources:
        return _cmd_endpoint_list_all_sources(args)

    try:
        if getattr(args, "tier", None):
            print("Note: current UMRS endpoint listing ignores the tier filter")
        response = client.list_endpoints(
            tier=getattr(args, "tier", None),
            include_stopped=getattr(args, "all", False),
        )
        if getattr(args, "as_json", False):
            print(json.dumps(response, indent=2))
        else:
            _print_endpoint_list_table(response.get("endpoints", []))
    except Exception as error:
        _die(str(error), action="endpoint list", hint=getattr(error, "hint", None))


def _cmd_endpoint_list_all_sources(args) -> int:
    """List endpoints from all registered sources (platform + public)."""
    import json

    from ..config import get_config
    from ..source import get_source_store

    sources = get_source_store().list()
    if not sources:
        print("No sources registered.")
        print("  Add one with: r4 source add <name> --url <url> --kind platform")
        return 1

    base_config = get_config()
    all_endpoints: list[dict] = []
    errors: list[dict] = []

    for source in sources:
        if source.kind == "platform":
            endpoints, error = _list_endpoints_for_source(source, args, base_config)
        else:
            endpoints, error = _public_source_endpoints(source), None
        all_endpoints.extend(endpoints)
        if error:
            errors.append(error)

    if getattr(args, "as_json", False):
        payload = {"endpoints": all_endpoints}
        if errors:
            payload["errors"] = errors
        print(json.dumps(payload, indent=2))
    else:
        _print_endpoint_list_table_multi_source(all_endpoints)
        _print_endpoint_source_errors(errors)

    return 0


def _public_source_endpoints(source) -> list[dict]:
    """Represent a public source as a single configured endpoint row."""
    return [
        {
            "instance_id": "public",
            "tier": source.provider or "public",
            "model": None,
            "status": "configured",
            "endpoint_url": source.url or "(native)",
            "_source": source.name,
        }
    ]


def _list_endpoints_for_source(source, args, base_config) -> tuple[list[dict], dict | None]:
    from ..client import Client
    from ..source_context import config_for_source

    config = config_for_source(source, base_config)  # its own credential only (S1)
    try:
        response = Client(config=config).list_endpoints(
            include_stopped=getattr(args, "all", False),
        )
    except Exception as exc:
        from ..source_context import auth_failure_hint

        error = {"source": source.name, "url": source.url, "error": str(exc)}
        hint = auth_failure_hint(config, exc)
        if hint:
            error["hint"] = hint
        return [], error

    endpoints = response.get("endpoints", [])
    for endpoint in endpoints:
        endpoint["_source"] = source.name
    return endpoints, None


def _print_endpoint_source_errors(errors: list[dict]) -> None:
    for error in errors:
        print(f"  ✗ {error['source']}: {error['error']}", file=sys.stderr)
        if error.get("hint"):
            print(f"    hint: {error['hint']}", file=sys.stderr)


def _print_endpoint_list_table_multi_source(endpoints: list[dict]) -> None:
    if not endpoints:
        print("No active endpoints across any source")
        return
    print(f"{'INSTANCE ID':<28} {'SOURCE':<14} {'TIER':<10} {'MODEL':<25} {'STATUS':<10} {'URL'}")
    print("-" * 115)
    for endpoint in endpoints:
        instance_id = _endpoint_list_cell(endpoint.get("instance_id"), "unknown")[:26]
        source = _endpoint_list_cell(endpoint.get("_source"), "?")[:12]
        tier = _endpoint_list_cell(endpoint.get("tier"), "n/a")
        model = _endpoint_list_cell(
            endpoint.get("model") or endpoint.get("served_model_name"),
            "",
        )[:23]
        status = _endpoint_status_value(endpoint)
        url = _endpoint_list_cell(endpoint.get("endpoint_url"), "")
        print(f"{instance_id:<28} {source:<14} {tier:<10} {model:<25} {status:<10} {url}")


def _print_endpoint_expiration(response: dict) -> None:
    expires_at = response.get("expires_at")
    if not expires_at:
        return
    from datetime import datetime, timezone

    print(f"  Expires: {expires_at}")
    try:
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return
    if exp_dt < datetime.now(timezone.utc):
        print("  ⚠ This endpoint has EXPIRED. Run `r4 endpoint up` to provision a new one.")


def _print_endpoint_status_response(args, response: dict) -> None:
    print(f"Instance: {args.instance_id}")
    print(f"  Status: {response.get('status', 'unknown')}")
    print(f"  Model: {response.get('model', 'unknown')}")
    optional_fields = [
        ("URL", response.get("endpoint_url")),
        ("Created", response.get("created_at")),
        ("Backend", response.get("backend")),
    ]
    for label, value in optional_fields:
        if value:
            print(f"  {label}: {value}")
    _print_endpoint_expiration(response)


def _cmd_endpoint_status(args, client):
    import json

    try:
        response = client.get_endpoint_status(args.instance_id)
        if getattr(args, "as_json", False):
            print(json.dumps(response, indent=2))
        else:
            _print_endpoint_status_response(args, response)
    except Exception as error:
        hint = None
        if _is_http_404(error):
            hint = _endpoint_reference_hint(client, args.instance_id, command=args.endpoint_command)
        _die(str(error), action="endpoint status", hint=hint)


def _cmd_endpoint_logs(args, client):
    try:
        response = client.get_endpoint_logs(
            args.instance_id,
            tail=args.tail,
            follow=args.follow,
        )
        lines = response if args.follow else response.get("logs", [])
        for line in lines:
            print(line)
    except Exception as error:
        hint = (
            _endpoint_reference_hint(client, args.instance_id, command="logs")
            if _is_http_404(error)
            else None
        )
        _die(str(error), action="endpoint logs", hint=hint)


def _cmd_endpoint_upgrade(args, client):
    """Rolling model-version upgrade of a running endpoint (Q-13).

    Phase 1: provision replacement, health-check chat/completions,
    flip the endpoint registry record, retire the old instance.
    Concurrency-gate (Q-13 acceptance) lands in a follow-up wave;
    today one upgrade at a time per endpoint is the supported window.
    """
    from ._helpers import _die

    if not getattr(args, "endpoint_id", None):
        _die(
            "endpoint_id is required",
            action="endpoint upgrade",
            hint="Use `r4 endpoint list` to see running endpoint IDs.",
        )
    if not getattr(args, "model_path", None):
        _die(
            "--model-path is required and must be a registered model path",
            action="endpoint upgrade",
            hint="Register the new model first: `r4 model register <path>`.",
        )

    client = _resolve_down_client_for_instance(
        Namespace(**{**vars(args), "instance_id": args.endpoint_id}),
        client,
    )
    as_json = bool(getattr(args, "as_json", False))

    try:
        response = client.upgrade_endpoint(
            endpoint_id=args.endpoint_id,
            model_path=args.model_path,
            wait=getattr(args, "wait", False),
            timeout=getattr(args, "timeout", 900),
        )
        if as_json:
            import json

            print(json.dumps(response, indent=2))
        else:
            print(f"  Endpoint: {args.endpoint_id}")
            print(f"  Old model: {response.get('old_model_path', '?')}")
            print(f"  New model: {response.get('model_path', '?')}")
            print(f"  Status: {response.get('status', 'provisioning')}")
        return 0 if response.get("status") in ("provisioning", "ready") else 1
    except Exception as error:
        hint = None
        if "404" in str(error):
            hint = (
                "Endpoint not found or model path not registered. "
                "Use `r4 endpoint list` for the endpoint and "
                "`r4 model register <path>` for the new model path."
            )
        _die(str(error), action="endpoint upgrade", hint=hint)


def _endpoint_command_handlers():
    return {
        "up": _cmd_endpoint_up,
        "down": _cmd_endpoint_down,
        "upgrade": _cmd_endpoint_upgrade,
        "list": _cmd_endpoint_list,
        "status": _cmd_endpoint_status,
        "describe": _cmd_endpoint_status,
        "logs": _cmd_endpoint_logs,
        "access": _cmd_endpoint_access,
    }


def _cmd_endpoint_access(args, client):
    """Show or replace an endpoint's ADR-078 access list."""
    if args.private or args.access_set:
        data = client.set_endpoint_access(args.instance_id, [] if args.private else args.access_set)
    else:
        data = client.get_endpoint_status(args.instance_id)
    endpoint = data.get("endpoint", data) if isinstance(data, dict) else {}
    scopes = endpoint.get("access") or []
    owner = endpoint.get("project_id") or "?"
    print(f"{args.instance_id}  owner project: {owner}")
    print("  access: " + (", ".join(scopes) if scopes else "owning project only"))
    return 0
