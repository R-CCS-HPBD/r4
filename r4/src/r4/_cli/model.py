"""``r4 model`` command handler (Phase 2m, 2026-04-28).

Owns the argparse subparser tree for `model list/info/stage/register/
publish/delete`. Handler logic still lives in :func:`r4.cli.cmd_model`.
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace, RawDescriptionHelpFormatter
from typing import Any

from .base import CommandHandler
from .registry import command


def _ctx_len_arg(raw: str) -> int:
    """argparse type for context lengths accepting ``65k``/``128K``/``4096``."""
    from ._helpers import _parse_context_len

    value = _parse_context_len(raw)
    if value is None:
        raise ArgumentTypeError(
            f"invalid context length {raw!r} (use an integer or k-suffix like 65k)"
        )
    return value


@command
class ModelCommand(CommandHandler):
    name = "model"
    help = "Model registry operations"

    def register(self, subparsers) -> ArgumentParser:
        from ..models import MODEL_TYPE_CHOICES

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Browse, stage, register, publish, or delete models in the UMRS model registry."
            ),
            epilog=(
                "Examples:\n"
                "  r4 model list --scope system\n"
                "  r4 model info rivault-gpt-oss-static\n"
                "  r4 model stage Qwen/Qwen2.5-7B-Instruct"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="model_command",
            title="subcommands",
            metavar="<model-command>",
        )
        sub.required = True

        # model list (A-601)
        m_list = sub.add_parser("list", help="List available models")
        m_list.add_argument(
            "--source",
            "-S",
            dest="endpoint_source",
            metavar="NAME",
            help="Endpoint source to query (see `r4 source list`)",
        )
        m_list.add_argument(
            "--all-sources",
            "-A",
            action="store_true",
            dest="all_sources",
            help="Query all registered sources and aggregate results",
        )
        m_list.add_argument(
            "--scope",
            choices=["system", "project", "user"],
            help="Filter by scope",
        )
        m_list.add_argument(
            "--type",
            dest="model_type",
            choices=MODEL_TYPE_CHOICES,
            help="Filter by model type",
        )
        m_list.add_argument("--project", "-p", help="Filter by project ID")
        m_list.add_argument(
            "--status",
            choices=["staging", "available", "deprecated"],
            help="Filter by status",
        )
        m_list.add_argument("--limit", "-l", type=int, default=50, help="Max results")
        m_list.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        # model info (A-602)
        m_info = sub.add_parser("info", help="Show model details")
        m_info.add_argument("name", help="Model name or ID")
        m_info.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        # model stage (A-603)
        m_stage = sub.add_parser("stage", help="Download model from HuggingFace")
        m_stage.add_argument(
            "source_id",
            help="HuggingFace repo ID (e.g., meta-llama/Llama-3.2-8B)",
        )
        m_stage.add_argument("--name", "-n", help="Model name (default: derived from source)")
        m_stage.add_argument(
            "--scope",
            choices=["system", "project", "user"],
            default="user",
            help="Target scope (default: user)",
        )
        m_stage.add_argument("--project", "-p", help="Target project (for project scope)")
        m_stage.add_argument(
            "--type",
            dest="model_type",
            choices=MODEL_TYPE_CHOICES,
            default="llm",
            help="Model type (default: llm)",
        )
        m_stage.add_argument("--token", help="HuggingFace token (for gated models)")
        m_stage.add_argument("--revision", help="Specific revision/branch")
        m_stage.add_argument("--description", "-d", help="Model description")
        m_stage.add_argument("--tags", help="Comma-separated tags")

        # model register (A-604)
        m_register = sub.add_parser("register", help="Register existing model")
        m_register.add_argument("name", help="Model name")
        m_register.add_argument("--path", required=True, help="Storage path")
        m_register.add_argument(
            "--scope",
            choices=["system", "project", "user"],
            default="user",
            help="Model scope (default: user)",
        )
        m_register.add_argument("--project", "-p", help="Project ID (for project scope)")
        m_register.add_argument(
            "--type",
            dest="model_type",
            choices=MODEL_TYPE_CHOICES,
            default="llm",
            help="Model type (default: llm)",
        )
        m_register.add_argument(
            "--backend",
            choices=["vllm", "bentoml"],
            dest="serving_backend",
            help="Serving backend for endpoint provisioning",
        )
        m_register.add_argument(
            "--service-kind",
            choices=["openai", "bentoml_vision"],
            help="Service API kind for endpoint provisioning",
        )
        m_register.add_argument(
            "--launch-profile",
            action="append",
            dest="launch_profiles",
            help="Mini-RiVault profile to activate when serving this model",
        )
        m_register.add_argument(
            "--container-image",
            help="Container image override for this model's serving backend",
        )
        m_register.add_argument(
            "--vision-adapter",
            help="Vision adapter name, e.g. sam3 or hmae",
        )
        m_register.add_argument(
            "--vision-task",
            help="Default vision task",
        )
        m_register.add_argument("--source", help="Source URI")
        m_register.add_argument("--hf-id", help="HuggingFace model ID")
        m_register.add_argument("--description", "-d", help="Model description")
        m_register.add_argument("--tags", help="Comma-separated tags")
        m_register.add_argument(
            "--tp", type=int, default=None, help="Tensor parallel size (number of GPUs)"
        )
        m_register.add_argument(
            "--max-model-len",
            type=_ctx_len_arg,
            default=None,
            help="Maximum context length (e.g. 4096 or 65k). Also sets the CTX MAX column.",
        )
        m_register.add_argument(
            "--gpu-mem-util", type=float, default=None, help="GPU memory utilization (0.1-1.0)"
        )
        m_register.add_argument(
            "--quantization",
            choices=["awq", "gptq", "fp8"],
            default=None,
            help="Quantization method",
        )
        m_register.add_argument(
            "--supports-tools",
            action="store_true",
            default=False,
            help="Model supports tool/function calling",
        )
        m_register.add_argument(
            "--supports-vision",
            action="store_true",
            default=False,
            help="Model supports vision/image input",
        )
        m_register.add_argument(
            "--supports-structured-output",
            action="store_true",
            default=False,
            help="Model supports structured (JSON) output",
        )
        m_register.add_argument(
            "--max-output-tokens", type=int, default=None, help="Maximum output tokens"
        )
        m_register.add_argument(
            "--model-family", default=None, help="Model family (e.g. qwen, llama, mistral)"
        )
        m_register.add_argument(
            "--no-detect",
            action="store_true",
            default=False,
            help="Skip auto-detection of capabilities from model files",
        )

        # model publish (A-605)
        m_publish = sub.add_parser("publish", help="Publish model to project")
        m_publish.add_argument("name", help="Model name or ID")
        m_publish.add_argument("--project", "-p", required=True, help="Target project ID")

        # model detect (backfill capabilities from model files)
        m_detect = sub.add_parser("detect", help="Detect capabilities from model files")
        m_detect.add_argument("name", nargs="?", help="Model name or ID (omit for --all)")
        m_detect.add_argument(
            "--all",
            action="store_true",
            dest="detect_all",
            help="Detect capabilities for all models",
        )
        m_detect.add_argument(
            "--dry-run",
            action="store_true",
            help="Show detected capabilities without updating",
        )

        # model use — switch the active model
        m_use = sub.add_parser(
            "use",
            help="Set the active model for chat",
            description=(
                "Save the default model to your profile so subsequent r4 chat calls use it."
            ),
        )
        m_use.add_argument(
            "name",
            nargs="?",
            help="Model name (from 'r4 model list'). Omit to show current model.",
        )
        m_use.add_argument(
            "--source",
            choices=["platform", "public"],
            help="Model source to use when the name exists in both places.",
        )

        # model route — bind a model to a named source
        m_route = sub.add_parser(
            "route",
            help="Bind a model to a named source for cross-source routing",
            description=(
                "Bind a model to a registered source so that chat/inference\n"
                "requests for that model are automatically routed to the\n"
                "correct source.  Without arguments, shows all bindings.\n\n"
                "You can also use model@source notation inline:\n"
                "  r4 chat --model Qwen/Qwen2.5-7B@rccs-cloud 'hello'"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        m_route.add_argument("name", nargs="?", help="Model name")
        m_route.add_argument("target", nargs="?", help="Source name (from `r4 source list`)")
        m_route.add_argument(
            "--clear",
            action="store_true",
            help="Remove the route binding for this model",
        )

        # model delete
        m_delete = sub.add_parser("delete", help="Delete a model")
        m_delete.add_argument("name", help="Model name or ID")
        m_delete.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

        _register_scale_parser(sub)

        return p

    def handle(self, args: Namespace) -> int:
        from ..source import resolve_active_source
        from ..source_context import use_source

        # Source resolution for model list; the source gets only its own
        # credential (onboarding plan S1).
        _source = resolve_active_source(getattr(args, "endpoint_source", None))
        if _source is not None:
            use_source(_source)

        # Skip preflight for purely local operations
        if args.model_command not in ("route", "scale"):
            from ._helpers import _preflight_check

            _preflight_check(action="model")

        return cmd_model(args)


_SCALE_ACTIONS = ("set", "unset", "list")


def _register_scale_parser(sub) -> None:
    """``r4 model scale [MODEL] | set MODEL CLASS | unset MODEL | list`` (ADR-093 C22)."""
    m_scale = sub.add_parser(
        "scale",
        help="Detect or pin a model's size class (small/medium/large)",
        description=(
            "Detect the size class of a model on its source (ollama /api/show, the\n"
            "platform registry, an OpenAI-compatible /v1/models, else the name rule),\n"
            "or pin it when detection cannot tell. The class sizes retrieval packs."
        ),
        epilog=(
            "Examples:\n"
            "  r4 model scale                       # the active model on the active source\n"
            "  r4 model scale llama3.2:3b --source ollama --refresh\n"
            "  r4 model scale set kimi-k3 large --source rikyu --params 1000 --active 32\n"
            "  r4 model scale unset kimi-k3 --source rikyu\n"
            "  r4 model scale list"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    m_scale.add_argument(
        "scale_args",
        nargs="*",
        metavar="[set|unset|list] [MODEL] [CLASS]",
        help="Omit to detect the active model; set MODEL CLASS; unset MODEL; list",
    )
    m_scale.add_argument(
        "--source",
        "-S",
        dest="scale_source",
        metavar="NAME",
        help="Registered source (default: the source the model routes to)",
    )
    m_scale.add_argument("--refresh", action="store_true", help="Re-probe (skip the cache)")
    m_scale.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
    m_scale.add_argument(
        "--params", type=float, dest="scale_params", metavar="B", help="set: total size, billions"
    )
    m_scale.add_argument(
        "--active",
        type=float,
        dest="scale_active",
        metavar="B",
        help="set: active size (MoE), billions",
    )
    m_scale.add_argument(
        "--note", dest="scale_note", metavar="TEXT", help="set: a note kept with the override"
    )


def _warn_duplicate_names(models) -> None:
    """F-023: Print a warning when multiple models share the same name."""
    if not models:
        return
    from collections import Counter, defaultdict

    counts = Counter(m.name for m in models)
    dupes = {name: cnt for name, cnt in counts.items() if cnt > 1}
    if not dupes:
        return

    by_name: dict[str, list] = defaultdict(list)
    for m in models:
        if m.name in dupes:
            by_name[m.name].append(m)

    for name in sorted(dupes):
        _print_duplicate_name_warning(name, dupes[name], by_name[name])


def _print_duplicate_name_warning(name, count, models) -> None:
    """Emit the stderr warning block for a single duplicated model name."""
    print(
        f"warning: model name '{name}' appears {count} times "
        f"across different scopes; use model ID to disambiguate:",
        file=sys.stderr,
    )
    for m in models:
        print(
            f"  {m.model_id}  (scope={m.scope.value})",
            file=sys.stderr,
        )


def _detect_provider_label(cfg) -> str:
    """Derive a short provider label from the active public endpoint config."""
    provider = getattr(cfg, "endpoint_provider", None)
    if provider:
        return provider  # e.g. "bedrock"
    url = getattr(cfg, "endpoint_url", None) or ""
    url_lower = url.lower()
    _URL_TO_PROVIDER = {
        "api.openai.com": "openai",
        "generativelanguage.googleapis.com": "gemini",
        "api.groq.com": "groq",
        "api.together.xyz": "together",
        "openrouter.ai": "openrouter",
        "api.deepseek.com": "deepseek",
        "api.fireworks.ai": "fireworks",
        "api.mistral.ai": "mistral",
        "api.anthropic.com": "anthropic",
    }
    for domain, label in _URL_TO_PROVIDER.items():
        if domain in url_lower:
            return label
    return "public"


def _fetch_public_endpoint_models(cfg) -> list[str]:
    """Fetch model IDs from the configured public endpoint, if any.

    Returns an empty list when no public endpoint is active or on any error.
    """
    if not (cfg.endpoint_url or cfg.endpoint_provider):
        return []
    try:
        from ..client import Client

        c = Client(config=cfg)
        return c.list_public_models()
    except Exception:
        return []


def _cmd_model_list_all_sources(args) -> int:
    """Query all registered sources and print a unified model table."""
    import sys

    from ..source import get_source_store

    store = get_source_store()
    sources = store.list()
    if not sources:
        print("No sources registered. Run `r4 source add` first.", file=sys.stderr)
        return 1

    results = _probe_all_source_models(sources)
    if getattr(args, "as_json", False):
        return _emit_all_sources_json(results)
    rows = _all_source_rows(results)
    return _print_all_sources_table(rows, results, active=store.get_active())


def _probe_all_source_models(sources) -> list[tuple[str, str, list[str], str | None]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..model_routing import probe_source_models

    # (name, kind, model_ids, error)
    results: list[tuple[str, str, list[str], str | None]] = []

    def _probe(src):
        model_ids, error = probe_source_models(src, timeout=8)
        return (src.name, src.kind, model_ids, error)

    with ThreadPoolExecutor(max_workers=min(len(sources), 8)) as pool:
        futures = {pool.submit(_probe, s): s for s in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append((src.name, src.kind, [], str(exc)))

    results.sort(key=lambda r: r[0])
    return results


def _emit_all_sources_json(results: list[tuple[str, str, list[str], str | None]]) -> int:
    import json

    data = [
        {"name": model_id, "source": src_name, "kind": src_kind}
        for src_name, src_kind, model_ids, _error in results
        for model_id in sorted(model_ids)
    ]
    print(json.dumps(data, indent=2))
    return 0


def _all_source_rows(
    results: list[tuple[str, str, list[str], str | None]],
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []  # (name, source, kind)
    for src_name, src_kind, model_ids, _error in results:
        for mid in sorted(model_ids):
            rows.append((mid, src_name, src_kind))
    return rows


def _print_all_source_errors(results: list[tuple[str, str, list[str], str | None]]) -> None:
    """Report every source that contributed no models, with the reason.

    A source that responds successfully but serves nothing was previously
    invisible here, making it indistinguishable from a source that was never
    probed. Both cases are now reported.
    """
    for src_name, _kind, model_ids, error in results:
        if model_ids:
            continue
        if error:
            print(f"  \u2717 {src_name}: {error}", file=sys.stderr)
        else:
            print(f"  \u2013 {src_name}: no models available", file=sys.stderr)


def _print_all_sources_table(
    rows: list[tuple[str, str, str]],
    results: list[tuple[str, str, list[str], str | None]],
    *,
    active: str | None,
) -> int:
    import sys

    if not rows:
        print("No models found across any source.")
        _print_all_source_errors(results)
        return 0

    print(f"{'NAME':<40} {'SOURCE':<18} {'KIND':<10}")
    print("-" * 68)
    for model_name, src_name, src_kind in rows:
        marker = "*" if src_name == active else " "
        print(f"{model_name:<40} {marker}{src_name:<17} {src_kind:<10}")

    source_count = len([r for r in results if r[2]])
    total = len(rows)
    print(
        f"\n{total} model(s) across {source_count} source(s)",
        file=sys.stderr,
    )
    _print_all_source_errors(results)
    return 0


def _matches_platform_model(model, name: str) -> bool:
    return name in {getattr(model, "name", None), getattr(model, "model_id", None)}


def _prompt_model_source(name: str) -> str:
    """Prompt for a model source when the name is ambiguous."""
    import sys

    while True:
        try:
            raw = (
                input(
                    f"Model '{name}' exists in platform and public sources. "
                    "Use [p]latform or p[u]blic? "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if raw in {"p", "platform"}:
            return "platform"
        if raw in {"u", "public"}:
            return "public"
        print("  Enter 'p' for platform or 'u' for public.")


def _resolve_model_use_source(
    *,
    name: str,
    requested_source: str | None,
    platform_models,
    public_model_ids: list[str],
) -> tuple[str, bool]:
    """Resolve the desired model source and whether it was verified."""
    if requested_source:
        return requested_source, True

    in_platform, in_public = _model_source_presence(
        name=name,
        platform_models=platform_models,
        public_model_ids=public_model_ids,
    )
    single_source = _single_verified_model_source(in_platform, in_public)
    if single_source:
        return single_source, True
    if in_platform and in_public:
        return _resolve_ambiguous_model_use_source(name), True
    return _default_unverified_model_source(public_model_ids), False


def _model_source_presence(
    *,
    name: str,
    platform_models,
    public_model_ids: list[str],
) -> tuple[bool, bool]:
    in_platform = any(_matches_platform_model(model, name) for model in platform_models)
    return in_platform, name in set(public_model_ids)


def _single_verified_model_source(in_platform: bool, in_public: bool) -> str | None:
    if in_platform and not in_public:
        return "platform"
    if in_public and not in_platform:
        return "public"
    return None


def _resolve_ambiguous_model_use_source(name: str) -> str:
    import sys

    if sys.stdin.isatty() and sys.stdout.isatty():
        return _prompt_model_source(name)
    print(
        f"Model '{name}' exists in both platform and public sources; "
        "use --source platform or --source public.",
        file=sys.stderr,
    )
    sys.exit(2)


def _default_unverified_model_source(public_model_ids: list[str]) -> str:
    return "public" if public_model_ids else "platform"


def _warn_if_platform_model_unserved(cfg, model_name: str) -> None:
    """Best-effort warning when no running platform endpoint serves the model."""
    import sys

    try:
        from ..client import Client

        served_models = Client(config=cfg).list_gateway_models(timeout=5)
    except Exception:
        print(
            "warning: could not verify whether a platform endpoint is currently "
            "serving this model.",
            file=sys.stderr,
        )
        print(f"hint: if chat fails, start it with `r4 up --model {model_name}`.", file=sys.stderr)
        return

    if model_name not in served_models:
        print(
            f"warning: no running platform endpoint reported model '{model_name}'.",
            file=sys.stderr,
        )
        print(f"hint: start one with `r4 up --model {model_name}`.", file=sys.stderr)


def _model_list_filters(args):
    from ..models import ModelScope, ModelStatus, ModelType

    scope = ModelScope(args.scope) if args.scope else None
    model_type = ModelType(args.model_type) if args.model_type else None
    status = ModelStatus(args.status) if args.status else None
    return scope, model_type, status


def _load_env_config():
    try:
        from ..config import Config

        return Config.from_env()
    except Exception:
        return None


def _public_model_context():
    cfg = _load_env_config()
    has_public = cfg is not None and bool(cfg.endpoint_url or cfg.endpoint_provider)
    if not has_public:
        return cfg, False, [], ""
    return cfg, True, _fetch_public_endpoint_models(cfg), _detect_provider_label(cfg)


def _model_json_entry(model) -> dict:
    entry = {
        "model_id": model.model_id,
        "name": model.name,
        "source": "platform",
        "scope": model.scope.value,
        "type": model.model_type.value,
        "status": model.status.value,
        "storage_path": model.storage_path,
        "capabilities": model.capabilities.to_dict(),
        "created_at": model.created_at.isoformat() if model.created_at else None,
    }
    if model.serving_profile:
        entry["serving_profile"] = model.serving_profile.to_dict()
    return entry


def _emit_model_list_json(models, public_model_ids: list[str], provider_label: str) -> None:
    import json

    data = [_model_json_entry(model) for model in models]
    data.extend(
        {"model_id": model_id, "name": model_id, "source": provider_label}
        for model_id in public_model_ids
    )
    print(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# ANSI styling — suppressed when stdout is not a TTY or NO_COLOR is set
# ---------------------------------------------------------------------------


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _use_color():
        return str(text)
    return f"\033[{code}m{text}\033[0m"


def _bold(t: str) -> str:
    return _c("1", t)


def _dim(t: str) -> str:
    return _c("2", t)


def _green(t: str) -> str:
    return _c("32", t)


def _red(t: str) -> str:
    return _c("31", t)


def _cyan(t: str) -> str:
    return _c("36", t)


def _fmt_cell(text, width: int | None = None, *, style=None) -> str:
    text = "" if text is None else str(text)
    if width is not None:
        # Strip ANSI escape codes to get visible length for padding
        import re

        visible = re.sub(r"\033\[[0-9;]*m", "", text)
        pad = max(0, width - len(visible))
        text = text + " " * pad
    return style(text) if style else text


def _section(title: str) -> str:
    return _bold(_cyan(f"[{title}]"))


def _model_ctx_max(model) -> str:
    from ..model_context import format_context_length, resolve_max_context

    return format_context_length(resolve_max_context(model))


def _model_quant(model) -> str:
    if model.serving_profile and model.serving_profile.quantization:
        return model.serving_profile.quantization
    return "-"


def _model_flag(value) -> str:
    return _green("\u2713") if value else _dim("\u2717")


def _print_platform_model_row(model, *, include_source: bool) -> None:
    caps = model.capabilities
    parts = [
        _fmt_cell(model.name, 22),
    ]
    if include_source:
        parts.append(_fmt_cell("platform", 10))
    parts.extend(
        [
            _fmt_cell(model.scope.value, 8),
            _fmt_cell(model.model_type.value, 8),
            _fmt_cell(model.status.value, 9),
            _fmt_cell(_model_flag(caps.supports_tool_calling), 5),
            _fmt_cell(_model_flag(caps.supports_vision), 6),
            _fmt_cell(_model_ctx_max(model), 6),
            _fmt_cell(_model_quant(model), 5),
        ]
    )
    print("  " + " ".join(parts))


def _print_model_list_without_public(models) -> None:
    header_parts = [
        _fmt_cell("Name", 22, style=_bold),
        _fmt_cell("Scope", 8, style=_bold),
        _fmt_cell("Type", 8, style=_bold),
        _fmt_cell("Status", 9, style=_bold),
        _fmt_cell("Tools", 5, style=_bold),
        _fmt_cell("Vision", 6, style=_bold),
        _fmt_cell("Ctx", 6, style=_bold),
        _bold("Quant"),
    ]
    print(_section("Models"))
    print("  " + " ".join(header_parts))
    print(f"  {_dim('-' * 76)}")
    for model in models:
        _print_platform_model_row(model, include_source=False)


def _print_model_list_with_public(models, public_model_ids: list[str], provider_label: str) -> None:
    header_parts = [
        _fmt_cell("Name", 22, style=_bold),
        _fmt_cell("Source", 10, style=_bold),
        _fmt_cell("Scope", 8, style=_bold),
        _fmt_cell("Type", 8, style=_bold),
        _fmt_cell("Status", 9, style=_bold),
        _fmt_cell("Tools", 5, style=_bold),
        _fmt_cell("Vision", 6, style=_bold),
        _fmt_cell("Ctx", 6, style=_bold),
        _bold("Quant"),
    ]
    print(_section("Models"))
    print("  " + " ".join(header_parts))
    print(f"  {_dim('-' * 76)}")
    for model in models:
        _print_platform_model_row(model, include_source=True)
    for model_id in public_model_ids:
        parts = [
            _fmt_cell(model_id, 22),
            _fmt_cell(provider_label, 10),
            _fmt_cell("-", 8),
            _fmt_cell("-", 8),
            _fmt_cell(_green("available"), 9),
            _fmt_cell("-", 5),
            _fmt_cell("-", 6),
            _fmt_cell("-", 6),
            _dim("-"),
        ]
        print("  " + " ".join(parts))
    if public_model_ids:
        print(
            f"\n  {_dim(f'({len(public_model_ids)} model(s) from {provider_label} endpoint)')}",
            file=sys.stderr,
        )


def _print_model_list_table(
    models,
    public_model_ids: list[str],
    provider_label: str,
    *,
    has_public: bool,
) -> None:
    if not models and not public_model_ids:
        print(f"  {_dim('No models found.')}")
        return
    if has_public:
        _print_model_list_with_public(models, public_model_ids, provider_label)
        return
    _print_model_list_without_public(models)


def _cmd_model_list_public(args, cfg) -> int | None:
    """List models from a public source endpoint (no platform registry).

    Public sources (Ollama, OpenAI-compatible servers, Bedrock, etc.) are not
    reachable through the platform model registry / gateway, so querying
    ``ModelClient`` would fail with a gateway connection error. Instead we
    fetch the model IDs directly from the configured public endpoint.
    """
    from ..client import Client

    provider_label = _detect_provider_label(cfg)
    try:
        # Not _fetch_public_endpoint_models: that one is best-effort (it feeds
        # the platform table) and turns every failure into []. Here the
        # provider IS the listing, so a 401/500/garbage reply printed "No
        # models found." with exit 0 (c2-cli, 2026-10-21).
        public_model_ids = Client(config=cfg).list_public_models()
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, exit 1
        _report_public_list_failure(cfg, exc, getattr(args, "endpoint_source", None))
        return 1
    if args.as_json:
        _emit_model_list_json([], public_model_ids, provider_label)
    else:
        _print_model_list_table(
            [],
            public_model_ids,
            provider_label,
            has_public=True,
        )
    return None


def _report_public_list_failure(cfg: Any, exc: Exception, source_name: str | None) -> None:
    """Say which provider failed and how, with the remedy for a refused key."""
    from ..errors import AuthenticationError
    from ..source import resolve_active_source

    source = resolve_active_source(source_name)
    name = source.name if source is not None else "<source>"
    print(
        f"Error (model list): cannot list models from {cfg.endpoint_url or name}: {exc}",
        file=sys.stderr,
    )
    if isinstance(exc, AuthenticationError):
        print(
            "  hint: the provider refused the API key stored for source "
            f"'{name}'. Replace it: printf '%s' \"$KEY\" | r4 source set {name} --api-key-stdin",
            file=sys.stderr,
        )


def _cmd_model_list(args, client):
    from ..model_routing import count_registered_sources

    explicit_source = getattr(args, "endpoint_source", None)
    aggregate = getattr(args, "all_sources", False) or (
        not explicit_source and count_registered_sources() > 1
    )
    if aggregate:
        return _cmd_model_list_all_sources(args)

    # Public source: the platform model registry / gateway is not the right
    # backend to query. List models directly from the public endpoint instead
    # of letting ``ModelClient`` fail with a gateway connection error.
    from ..config import get_config

    cfg = get_config()
    if cfg.endpoint_url or cfg.endpoint_provider:
        return _cmd_model_list_public(args, cfg)

    scope, model_type, status = _model_list_filters(args)
    models = client.list_models(
        scope=scope,
        model_type=model_type,
        project_id=args.project,
        status=status,
        limit=args.limit,
    )
    _, has_public, public_model_ids, provider_label = _public_model_context()

    if args.as_json:
        _emit_model_list_json(models, public_model_ids, provider_label)
        # JSON mode is a machine channel: never interleave stderr-facing
        # duplicate-name warnings into stdout (callers that merge 2>&1 must
        # get a parseable document).
    else:
        _print_model_list_table(
            models,
            public_model_ids,
            provider_label,
            has_public=has_public,
        )
        _warn_duplicate_names(models)
    return None


def _resolve_model_or_exit(client, name: str):
    import sys

    model = client.get_model_by_name(name)
    if model:
        return model
    try:
        model = client.get_model(name)
        if model is not None:
            return model
    except Exception:
        pass
    # Registry miss — fall back to source/route resolution so names that
    # appear in `r4 model route` / `r4 model list` yield usable info instead
    # of a hard "Model not found".
    fallback = _lookup_model_via_sources(name)
    if fallback is not None:
        return fallback
    print(f"Model not found: {name}", file=sys.stderr)
    sys.exit(1)


def _lookup_model_via_sources(name: str):
    """Best-effort synthetic ModelInfo for public-source models.

    Only invoked when the UMRS registry has no record. Returns None when the
    model is not advertised by any configured route or source.
    """
    try:
        from ..config import Config
        from ..model_routing import parse_model_ref, resolve_model_route
        from ..models import ModelCapabilities, ModelInfo, ModelScope, ModelStatus, ModelType
        from ..source import get_source_store

        ref = parse_model_ref(name)
        cfg = Config.from_env()

        # Stored route binding (model → source) or inline @source.
        bound_source = (cfg.model_routes or {}).get(ref.model) or ref.source
        if bound_source:
            try:
                resolve_model_route(cfg, name)
            except Exception:
                return None
            source = get_source_store().get(bound_source)
            source_name = source.name if source is not None else bound_source
            storage = source.url if source is not None else ""
            kind = source.kind if source is not None else "public"
            if kind == "platform":
                desc, tag = (
                    f"Platform model routed via '{source_name}' (not staged in UMRS registry)",
                    "platform",
                )
            else:
                desc, tag = (
                    f"Public-source model on '{source_name}' (not staged in UMRS registry)",
                    "public",
                )
            return ModelInfo(
                model_id=f"{kind}:{ref.model}",
                name=ref.model,
                scope=ModelScope.SYSTEM,
                model_type=ModelType.LLM,
                status=ModelStatus.AVAILABLE,
                storage_path=storage,
                source_uri=f"source://{source_name}/{ref.model}",
                capabilities=ModelCapabilities(),
                description=desc,
                tags=[tag, f"source:{source_name}"],
            )
        return None
    except Exception:
        return None


def _model_info_data(model) -> dict:
    data = {
        "model_id": model.model_id,
        "name": model.name,
        "scope": model.scope.value,
        "type": model.model_type.value,
        "status": model.status.value,
        "storage_path": model.storage_path,
        "source_uri": model.source_uri,
        "huggingface_id": model.huggingface_id,
        "owner_id": model.owner_id,
        "project_id": model.project_id,
        "description": model.description,
        "tags": model.tags,
        "capabilities": model.capabilities.to_dict(),
        "created_at": model.created_at.isoformat() if model.created_at else None,
    }
    if model.serving_profile:
        data["serving_profile"] = model.serving_profile.to_dict()
    return data


def _yes_no(value: bool | None) -> str:
    return "yes" if value else "no"


def _print_model_optional_fields(model) -> None:
    optional_fields = [
        ("Source URI", model.source_uri),
        ("HuggingFace", model.huggingface_id),
        ("Description", model.description),
        ("Tags", ", ".join(model.tags) if model.tags else None),
    ]
    for label, value in optional_fields:
        if value:
            print(f"  {label + ':':<13} {value}")


def _print_model_capabilities(caps, model=None) -> None:
    print("  Capabilities:")
    print(f"    tool_calling:       {_yes_no(caps.supports_tool_calling)}")
    print(f"    vision:             {_yes_no(caps.supports_vision)}")
    print(f"    structured_output:  {_yes_no(caps.supports_structured_output)}")
    if caps.max_output_tokens:
        print(f"    max_output_tokens:  {caps.max_output_tokens}")
    if caps.max_position_embeddings:
        value = caps.max_position_embeddings
        display = f"{value // 1024}k" if value >= 1024 else str(value)
        print(f"    max_context:        {display} ({value})")
    elif model is not None:
        from ..model_context import resolve_max_context

        value = resolve_max_context(model)
        if value:
            display = f"{value // 1024}k" if value >= 1024 else str(value)
            print(f"    max_context:        {display} ({value}, estimated)")
    if caps.model_family:
        print(f"    model_family:       {caps.model_family}")


def _print_serving_profile(profile) -> None:
    if not profile:
        return
    print("  Serving Profile:")
    print(f"    max_model_len:    {profile.max_model_len}")
    print(f"    tensor_parallel:  {profile.tensor_parallel_size}")
    print(f"    gpu_utilization:  {profile.gpu_memory_utilization}")
    if profile.quantization:
        print(f"    quantization:     {profile.quantization}")


def _print_model_info(model) -> None:
    print(f"Model: {model.name}")
    print(f"  ID:           {model.model_id}")
    print(f"  Scope:        {model.scope.value}")
    print(f"  Type:         {model.model_type.value}")
    print(f"  Status:       {model.status.value}")
    print(f"  Storage Path: {model.storage_path}")
    _print_model_optional_fields(model)
    _print_model_capabilities(model.capabilities, model)
    _print_serving_profile(model.serving_profile)
    if model.created_at:
        print(f"  Created:      {model.created_at.isoformat()}")


def _cmd_model_info(args, client):
    import json

    model = _resolve_model_or_exit(client, args.name)
    if args.as_json:
        print(json.dumps(_model_info_data(model), indent=2))
    else:
        _print_model_info(model)
    return None


def _cmd_model_stage(args, client):
    from ..models import ModelScope, ModelType, StageRequest

    request = StageRequest(
        source="huggingface",
        source_id=args.source_id,
        model_name=args.name or "",
        scope=ModelScope(args.scope) if args.scope else ModelScope.USER,
        project_id=args.project,
        model_type=ModelType(args.model_type) if args.model_type else ModelType.LLM,
        hf_token=args.token,
        revision=args.revision,
        description=args.description or "",
        tags=args.tags.split(",") if args.tags else [],
    )
    job_id = client.stage_model(request)
    print(f"Model staging job submitted: {job_id}")
    print(f"Track progress with: r4 job describe {job_id}")
    return None


def _has_serving_profile_args(args) -> bool:
    fields = (
        "tp",
        "max_model_len",
        "gpu_mem_util",
        "quantization",
        "serving_backend",
        "service_kind",
        "container_image",
        "vision_adapter",
        "vision_task",
    )
    return any(getattr(args, attr, None) is not None for attr in fields) or bool(
        getattr(args, "launch_profiles", None)
    )


def _build_serving_profile(args):
    if not _has_serving_profile_args(args):
        return None
    from r4.models import ServingProfile

    return ServingProfile(
        tensor_parallel_size=args.tp or 1,
        max_model_len=args.max_model_len or 4096,
        gpu_memory_utilization=args.gpu_mem_util or 0.9,
        quantization=args.quantization,
        serving_backend=getattr(args, "serving_backend", None),
        service_kind=getattr(args, "service_kind", None),
        launch_profiles=list(getattr(args, "launch_profiles", None) or []),
        container_image=getattr(args, "container_image", None),
        vision_adapter=getattr(args, "vision_adapter", None),
        vision_task=getattr(args, "vision_task", None),
    )


def _print_detected_registration_capabilities(detected, path: str) -> None:
    print(f"Auto-detected capabilities from {path}:")
    fields = [
        ("model_family", "model_family"),
        ("supports_tool_calling", "tool_calling"),
        ("supports_vision", "vision"),
        ("supports_structured_output", "structured_output"),
        ("max_output_tokens", "max_output_tokens"),
        ("max_position_embeddings", "context_length"),
    ]
    for attr, label in fields:
        value = getattr(detected, attr)
        if value is None:
            continue
        display = _yes_no(value) if isinstance(value, bool) else value
        print(f"  {label + ':':<20} {display}")


def _detect_registration_capabilities(args, serving_profile):
    if args.no_detect:
        return None, serving_profile
    from r4.model_detect import detect_capabilities

    detected = detect_capabilities(args.path)
    if detected.model_family or detected.supports_tool_calling or detected.supports_vision:
        _print_detected_registration_capabilities(detected, args.path)
    return detected, _apply_detected_context_length(detected, serving_profile)


def _apply_detected_context_length(detected, serving_profile):
    if not detected.max_position_embeddings:
        return serving_profile
    if serving_profile and serving_profile.max_model_len != 4096:
        return serving_profile
    if serving_profile is None:
        from r4.models import ServingProfile

        return ServingProfile(max_model_len=detected.max_position_embeddings)
    serving_profile.max_model_len = detected.max_position_embeddings
    return serving_profile


def _has_explicit_capability_args(args) -> bool:
    fields = (
        "supports_tools",
        "supports_vision",
        "supports_structured_output",
        "max_output_tokens",
        "max_model_len",
        "model_family",
    )
    return any(getattr(args, attr, None) for attr in fields) or args.model_type == "vision"


def _capability_bool(cli_value: bool, detected, attr: str, *, default: bool = False) -> bool:
    if cli_value:
        return cli_value
    detected_value = getattr(detected, attr) if detected else False
    return bool(default or detected_value)


def _capability_value(cli_value, detected, attr: str):
    if cli_value is not None:
        return cli_value
    return getattr(detected, attr) if detected else None


def _build_register_capabilities(args, detected):
    if not (_has_explicit_capability_args(args) or detected):
        return None
    from r4.models import ModelCapabilities

    return ModelCapabilities(
        supports_tool_calling=_capability_bool(
            args.supports_tools,
            detected,
            "supports_tool_calling",
        ),
        supports_vision=_capability_bool(
            args.supports_vision,
            detected,
            "supports_vision",
            default=args.model_type == "vision",
        ),
        supports_structured_output=_capability_bool(
            args.supports_structured_output,
            detected,
            "supports_structured_output",
        ),
        max_output_tokens=_capability_value(args.max_output_tokens, detected, "max_output_tokens"),
        max_position_embeddings=_capability_value(
            args.max_model_len, detected, "max_position_embeddings"
        ),
        model_family=_capability_value(args.model_family, detected, "model_family"),
    )


def _cmd_model_register(args, client):
    from ..models import ModelScope, ModelType, RegisterRequest

    serving_profile = _build_serving_profile(args)
    detected, serving_profile = _detect_registration_capabilities(args, serving_profile)
    request = RegisterRequest(
        name=args.name,
        storage_path=args.path,
        scope=ModelScope(args.scope) if args.scope else ModelScope.USER,
        project_id=args.project,
        model_type=ModelType(args.model_type) if args.model_type else ModelType.LLM,
        source_uri=args.source,
        huggingface_id=args.hf_id,
        description=args.description or "",
        tags=args.tags.split(",") if args.tags else [],
        serving_profile=serving_profile,
        capabilities=_build_register_capabilities(args, detected),
    )
    model = client.register_model(request)
    print("Model registered successfully!")
    print(f"  ID:   {model.model_id}")
    print(f"  Name: {model.name}")
    print(f"  Path: {model.storage_path}")
    return None


def _cmd_model_publish(args, client):
    model = _resolve_model_or_exit(client, args.name)
    published = client.publish_model(model.model_id, args.project)
    print(f"Model published to project: {args.project}")
    print(f"  New ID: {published.model_id}")
    print(f"  Name:   {published.name}")
    return None


def _cmd_model_delete(args, client):
    model = _resolve_model_or_exit(client, args.name)
    if not args.force:
        confirm = input(f"Delete model '{model.name}'? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return None
    client.delete_model(model.model_id)
    print(f"Model deleted: {model.name}")
    return None


def _print_current_model(cfg) -> None:
    from ..model_routing import active_model_route_label

    print(f"Current model: {cfg.model}")
    print(f"  Source: {active_model_route_label(cfg)}")
    print(f"  Profile: {cfg.profile}")
    if cfg.endpoint_provider:
        print(f"  Endpoint: {cfg.endpoint_provider} (public)")
    elif cfg.endpoint_url:
        print(f"  Endpoint: {cfg.endpoint_url} (public)")
    else:
        print(f"  Gateway: {cfg.gateway_url}")
    print("\nUsage: r4 model use <model-name>")


def _cmd_model_use(args, client):
    import sys

    from ..config import Config, ProfileConfigStore, reload_config
    from ..model_routing import update_model_route_map

    cfg = Config.from_env()
    if not args.name:
        _print_current_model(cfg)
        return 0

    public_model_ids = _fetch_public_endpoint_models(cfg)
    source, verified = _resolve_model_use_source(
        name=args.name,
        requested_source=getattr(args, "source", None),
        platform_models=client.list_models(limit=500),
        public_model_ids=public_model_ids,
    )
    routes = update_model_route_map(cfg, args.name, source)
    ProfileConfigStore().save(cfg.profile, model=args.name, model_routes=routes)
    updated = reload_config(profile=cfg.profile)
    print(f"Model set to: {updated.model}")
    print(f"  Source: {source}")
    print(f"  Profile: {updated.profile}")
    if not verified:
        print(
            "warning: model source was not found in the current model lists; "
            "saved using the active default route.",
            file=sys.stderr,
        )
    if source == "platform":
        _warn_if_platform_model_unserved(updated, args.name)
    return 0


def _remote_detected_parts(detected: dict) -> list[str]:
    fields = [
        ("model_family", lambda value: f"family={value}"),
        ("supports_tool_calling", lambda _value: "tools=yes"),
        ("supports_vision", lambda _value: "vision=yes"),
        ("supports_structured_output", lambda _value: "structured=yes"),
        ("max_output_tokens", lambda value: f"max_out={value}"),
    ]
    return [formatter(value) for key, formatter in fields if (value := detected.get(key))]


def _detect_remote_model_capabilities(client, model, dry_run: bool) -> bool:
    result = client.detect_model_capabilities(model.model_id, dry_run=dry_run)
    detected = result.get("detected", {})
    parts = _remote_detected_parts(detected)
    if not parts:
        print(f"  {model.name}: no capabilities detected at {model.storage_path or '(no path)'}")
        return False
    print(f"  {model.name}: {', '.join(parts)}")
    return result.get("updated", False) or bool(parts)


def _local_detected_parts(detected) -> list[str]:
    fields = [
        ("model_family", lambda value: f"family={value}"),
        ("supports_tool_calling", lambda _value: "tools=yes"),
        ("supports_vision", lambda _value: "vision=yes"),
        ("supports_structured_output", lambda _value: "structured=yes"),
        ("max_output_tokens", lambda value: f"max_out={value}"),
        ("max_position_embeddings", lambda value: f"ctx={value}"),
    ]
    return [formatter(value) for attr, formatter in fields if (value := getattr(detected, attr))]


def _capabilities_from_detection(detected, existing=None):
    from r4.models import ModelCapabilities

    base = existing or ModelCapabilities()
    return ModelCapabilities(
        supports_tool_calling=detected.supports_tool_calling or base.supports_tool_calling,
        supports_vision=detected.supports_vision or base.supports_vision,
        supports_structured_output=(
            detected.supports_structured_output or base.supports_structured_output
        ),
        max_output_tokens=detected.max_output_tokens or base.max_output_tokens,
        max_position_embeddings=(detected.max_position_embeddings or base.max_position_embeddings),
        model_family=detected.model_family or base.model_family,
    )


def _detect_local_model_capabilities(client, model, dry_run: bool) -> bool:
    from r4.model_detect import detect_capabilities

    if not model.storage_path:
        print(f"  {model.name}: no storage_path, skipping")
        return False
    detected = detect_capabilities(model.storage_path)
    parts = _local_detected_parts(detected)
    if not parts:
        print(f"  {model.name}: no capabilities detected at {model.storage_path}")
        return False
    print(f"  {model.name}: {', '.join(parts)}")
    if not dry_run:
        client.update_model_capabilities(
            model.model_id,
            _capabilities_from_detection(detected, model.capabilities),
        )
    return True


def _detect_one_model(client, model, dry_run: bool) -> bool:
    """Detect capabilities for a single model.

    Detection goes through the Gateway → UMRS API, where the UMRS
    server reads config files from the model's ``storage_ref`` on the
    server filesystem.  This works for all users regardless of SSH
    access because the UMRS server has direct filesystem access to
    the model files.

    Falls back to local filesystem detection if the server-side
    detection fails (e.g. Gateway unreachable).
    """
    try:
        return _detect_remote_model_capabilities(client, model, dry_run)
    except Exception:
        return _detect_local_model_capabilities(client, model, dry_run)


def _models_to_detect(args, client):
    import sys

    if getattr(args, "detect_all", False):
        return client.list_models()
    if args.name:
        return [_resolve_model_or_exit(client, args.name)]
    print("Specify a model name or use --all", file=sys.stderr)
    sys.exit(1)


def _print_detection_summary(updated_count: int, *, dry_run: bool) -> None:
    if updated_count == 0:
        print("No capabilities detected.")
    elif dry_run:
        print(f"\nDry run: {updated_count} model(s) would be updated.")
    else:
        print(f"\n{updated_count} model(s) updated.")


def _cmd_model_detect(args, client):
    updated_count = 0
    for model in _models_to_detect(args, client):
        if _detect_one_model(client, model, args.dry_run):
            updated_count += 1
    _print_detection_summary(updated_count, dry_run=args.dry_run)
    return None


def _show_all_model_routes(cfg) -> int:
    routes = cfg.model_routes
    if not routes:
        print("No model route bindings configured.")
        print("\nUsage: r4 model route <model> <source>")
        return 0
    print(f"{'MODEL':<40} {'SOURCE':<20}")
    print("-" * 60)
    for model_name, source_name in sorted(routes.items()):
        print(f"{model_name:<40} {source_name:<20}")
    return 0


def _clear_model_route_binding(args, cfg, store) -> int:
    from ..model_routing import clear_model_route

    routes = clear_model_route(cfg, args.name)
    store.save(cfg.profile, model_routes=routes)
    print(f"Route binding removed for model '{args.name}'.")
    return 0


def _show_model_route(args, cfg) -> int:
    routes = cfg.model_routes
    if args.name in routes:
        print(f"Model '{args.name}' is routed to source '{routes[args.name]}'.")
    else:
        print(f"No route binding for model '{args.name}'.")
    return 0


def _set_model_route_binding(args, cfg, store) -> int:
    import sys

    from ..model_routing import update_model_route_map
    from ..source import get_source_store

    src = get_source_store().get(args.target)
    if src is None:
        print(
            f"Source '{args.target}' not found. Run `r4 source list` to see registered sources.",
            file=sys.stderr,
        )
        sys.exit(1)
    routes = update_model_route_map(cfg, args.name, args.target)
    store.save(cfg.profile, model_routes=routes)
    print(f"Model '{args.name}' routed to source '{args.target}' ({src.kind}: {src.url}).")
    return 0


def _cmd_model_route(args, _client):
    from ..config import Config, ProfileConfigStore

    cfg = Config.from_env()
    store = ProfileConfigStore()
    if not args.name:
        return _show_all_model_routes(cfg)
    if args.clear:
        return _clear_model_route_binding(args, cfg, store)
    if not args.target:
        return _show_model_route(args, cfg)
    return _set_model_route_binding(args, cfg, store)


def _fmt_b(value) -> str:
    return "-" if value is None else f"{value:.3g}B" if value < 100 else f"{value:.0f}B"


def _scale_params_text(scale) -> str:
    parts = [f"{_fmt_b(scale.params_b)} total"]
    if scale.active_params_b is not None:
        parts.append(f"{_fmt_b(scale.active_params_b)} active")
        parts.append(f"{_fmt_b(scale.effective_params_b)} effective")
    return ", ".join(parts) if scale.params_b is not None else "-"


def _print_scale(scale) -> None:
    """Every string is shown as display text (no control characters): the
    detail and names come from the endpoint, or from a cache an older
    build wrote (r4-scale-router-12)."""
    from ..model_context import format_context_length
    from ..model_scale import display_text as shown

    print(f"{shown(scale.model)} @ {shown(scale.source)}")
    print(f"  class:   {shown(scale.size_class)} (retrieval profile {scale.profile})")
    print(f"  params:  {_scale_params_text(scale)}")
    print(f"  context: {format_context_length(scale.context)}")
    print(f"  basis:   {shown(scale.basis)}")
    print(f"  detail:  {shown(scale.detail, _SCALE_DETAIL_MAX) or '-'}")
    notice = scale.notice()
    if notice:
        print(f"note: {shown(notice, _SCALE_DETAIL_MAX)}", file=sys.stderr)


_SCALE_DETAIL_MAX = 1_000


def _scale_usage(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print("usage: r4 model scale [MODEL] | set MODEL CLASS | unset MODEL | list", file=sys.stderr)
    return 2


def _scale_show(args, operands: list[str]) -> int:
    import json

    from ..config import get_config
    from ..model_scale import detect_model_scale

    if len(operands) > 1:
        return _scale_usage("scale takes at most one MODEL")
    model = operands[0] if operands else get_config().model
    scale = detect_model_scale(model, source=args.scale_source, refresh=args.refresh)
    if args.as_json:
        print(json.dumps({**scale.to_dict(), "profile": scale.profile}, indent=2))
    else:
        _print_scale(scale)
    return 0


def _scale_pin_target(args, operand: str) -> tuple[str, str | None] | None:
    """``MODEL[@SOURCE]`` plus ``--source`` as the pin's ``(model, source)``:
    the ``model@source`` form ``r4 model scale`` shows and sessions name pins
    the key detection reads (``<source>/<model>``), not ``*/<model>@<source>``.
    ``None`` when the two name different sources."""
    from ..model_routing import parse_model_ref

    ref = parse_model_ref(operand)
    if ref.source and args.scale_source and ref.source != args.scale_source:
        return None
    return ref.model, args.scale_source or ref.source


_SCALE_SOURCE_CLASH = "MODEL@SOURCE and --source name different sources"


def _scale_set(args, operands: list[str]) -> int:
    from ..model_scale import SCALE_CLASSES, set_override

    if len(operands) != 2 or operands[1].lower() not in SCALE_CLASSES:
        return _scale_usage(f"set MODEL CLASS, CLASS one of {', '.join(SCALE_CLASSES)}")
    pin = _scale_pin_target(args, operands[0])
    if pin is None:
        return _scale_usage(_SCALE_SOURCE_CLASH)
    size_class = operands[1].lower()
    key = set_override(
        pin[0],
        size_class,
        source=pin[1],
        params_b=args.scale_params,
        active_params_b=args.scale_active,
        note=args.scale_note,
    )
    print(f"Pinned {key} to {size_class}.")
    return 0


def _scale_unset(args, operands: list[str]) -> int:
    from ..model_scale import scale_key, unset_override

    if len(operands) != 1:
        return _scale_usage("unset takes one MODEL")
    pin = _scale_pin_target(args, operands[0])
    if pin is None:
        return _scale_usage(_SCALE_SOURCE_CLASH)
    key = scale_key(*pin)
    if not unset_override(pin[0], source=pin[1]):
        print(f"No override for {key}.", file=sys.stderr)
        return 1
    print(f"Removed the override for {key}.")
    return 0


def _scale_list(args, operands: list[str]) -> int:
    import json

    from ..model_scale import cache_path, load_cached_scales, load_overrides, overrides_path
    from ..model_scale import display_text as shown

    if operands:
        return _scale_usage("list takes no MODEL")
    overrides, cached = load_overrides(), load_cached_scales()
    if args.as_json:
        detections = {k: v.to_dict() for k, v in cached.items()}
        print(json.dumps({"overrides": overrides, "detections": detections}, indent=2))
        return 0
    print(f"Overrides ({overrides_path()}):")
    for key, entry in sorted(overrides.items()):
        size, note = shown(entry.get("size_class", "?")), shown(entry.get("note", ""))
        print(f"  {shown(key):<40} {size:<7} {note}")
    print(f"Detections ({cache_path()}):")
    for key, scale in sorted(cached.items()):
        params = _scale_params_text(scale)
        print(f"  {shown(key):<40} {scale.size_class:<7} {params}  [{shown(scale.basis)}]")
    return 0


def _cmd_model_scale(args, _client):
    """``r4 model scale``: detect, pin, unpin or list model scales (ADR-093 C22)."""
    words = list(getattr(args, "scale_args", None) or [])
    action = words[0] if words and words[0] in _SCALE_ACTIONS else "show"
    operands = words[1:] if action != "show" else words
    handlers = {"show": _scale_show, "set": _scale_set, "unset": _scale_unset}
    return {**handlers, "list": _scale_list}[action](args, operands)


def _cmd_model_removed_download(_args, _client):
    import sys

    print(
        "error: `r4 model download` was removed in Phase 2h. "
        "Use `r4 model stage <huggingface-repo-id>` instead.",
        file=sys.stderr,
    )
    sys.exit(2)


def _cmd_model_removed_provision(_args, _client):
    import sys

    print(
        "error: `r4 model provision` was removed in Phase 2h. "
        "Use `r4 endpoint up --model <name>` instead.",
        file=sys.stderr,
    )
    sys.exit(2)


def _model_command_handlers():
    return {
        "list": _cmd_model_list,
        "info": _cmd_model_info,
        "stage": _cmd_model_stage,
        "register": _cmd_model_register,
        "publish": _cmd_model_publish,
        "delete": _cmd_model_delete,
        "use": _cmd_model_use,
        "detect": _cmd_model_detect,
        "route": _cmd_model_route,
        "scale": _cmd_model_scale,
        "download": _cmd_model_removed_download,
        "provision": _cmd_model_removed_provision,
    }


def cmd_model(args):
    """Handle model registry commands (A-601 - A-605)."""
    import sys

    from ..models import ModelClient

    client = ModelClient()
    handler = _model_command_handlers().get(args.model_command)
    if handler is None:
        print("Unknown model command. Use 'r4 model --help' for usage.", file=sys.stderr)
        sys.exit(1)
    return handler(args, client)
