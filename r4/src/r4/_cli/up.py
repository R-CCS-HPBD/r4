"""``r4 up`` — quick shortcut for interactive endpoint provisioning.

Equivalent to ``r4 endpoint up --wait`` with the interactive model picker
when ``--model`` is omitted.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path

from ._helpers import (
    _die,
    _parse_context_len,
    _pick_model_interactive,
    _prompt_max_model_len,
    _run_with_provision_progress,
    _wait_for_endpoint_ready,
)
from .base import CommandHandler
from .registry import command

_PROVIDER_SHORTCUTS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai",
    "together": "https://api.together.xyz",
    "openrouter": "https://openrouter.ai/api",
    "deepseek": "https://api.deepseek.com",
    "fireworks": "https://api.fireworks.ai/inference",
    "mistral": "https://api.mistral.ai",
}

_BEDROCK_MODEL_SHORTCUTS: dict[str, str] = {
    "1": "anthropic.claude-3-sonnet-20240229-v1:0",
    "2": "anthropic.claude-3-haiku-20240307-v1:0",
    "3": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "4": "us.amazon.nova-lite-v1:0",
    "5": "us.amazon.nova-pro-v1:0",
    "6": "meta.llama3-1-70b-instruct-v1:0",
}


def _prompt_up_mode() -> str:
    """Prompt for single vs multi-endpoint provisioning mode."""
    import sys

    while True:
        try:
            raw = (
                input("Provision [s]ingle, [m]ultiple, or [p]ublic endpoint? [s/m/p]: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if raw in {"", "s", "single"}:
            return "single"
        if raw in {"m", "multi", "multiple"}:
            return "multiple"
        if raw in {"p", "pub", "public"}:
            return "public"
        print("  Enter 's' for one endpoint, 'm' for a pool, or 'p' for a public endpoint.")


def _prompt_pool_file_path(default_name: str = "r4-pool.yaml") -> Path:
    """Prompt for the pool definition file path."""
    import sys

    try:
        raw = input(f"Pool definition file [default: {default_name}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)
    return Path(raw or default_name).expanduser()


def _prompt_text(prompt: str, *, default: str) -> str:
    """Prompt for free text with a default value."""
    import sys

    try:
        raw = input(f"{prompt} [default: {default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)
    return raw or default


def _prompt_choice(prompt: str, choices: tuple[str, ...], *, default: str) -> str:
    """Prompt for a constrained textual choice."""
    import sys

    allowed = {choice.lower() for choice in choices}
    while True:
        try:
            raw = input(f"{prompt} [default: {default}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if not raw:
            return default
        if raw in allowed:
            return raw
        print(f"  Enter one of: {', '.join(choices)}")


def _prompt_positive_int(prompt: str, *, default: int) -> int:
    """Prompt for a positive integer with default."""
    import sys

    while True:
        try:
            raw = input(f"{prompt} [default: {default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("  Enter a positive integer.")
            continue
        if value > 0:
            return value
        print("  Enter a positive integer.")


def _format_ctx_literal(value: int) -> str:
    """Format context length for generated YAML."""
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024}k"
    return str(value)


def _yaml_value(value: object) -> str:
    """Format a value for inline YAML output."""
    import json

    return json.dumps(value) if isinstance(value, str) else str(value)


def _render_endpoint_yaml(entry: dict[str, object]) -> list[str]:
    """Render a single endpoint entry as YAML lines."""
    import json

    lines = [f"  - name: {json.dumps(str(entry['name']))}"]
    lines.append(f"    model: {json.dumps(str(entry['model']))}")
    tier = entry.get("tier")
    if tier and tier != "user":
        lines.append(f"    tier: {json.dumps(str(tier))}")
    if entry.get("max_model_len") is not None:
        lines.append(f"    max_model_len: {json.dumps(str(entry['max_model_len']))}")
    return lines


def _render_pool_roster_yaml(roster: dict[str, object]) -> str:
    """Render a simple YAML pool definition without external dependencies."""
    import json

    lines = [f"pool: {json.dumps(str(roster['pool']))}"]
    defaults = roster.get("defaults")
    if isinstance(defaults, dict) and defaults:
        lines.append("defaults:")
        for key, value in defaults.items():
            lines.append(f"  {key}: {_yaml_value(value)}")
    lines.append("endpoints:")
    for entry in roster.get("endpoints", []):
        if not isinstance(entry, dict):
            continue
        lines.extend(_render_endpoint_yaml(entry))
    lines.append("")
    return "\n".join(lines)


def _build_guided_pool_roster(*, default_timeout: int) -> dict[str, object]:
    """Interactively build a multi-endpoint roster definition."""
    print("\nGuided pool setup:\n")
    pool_name = _prompt_text("Pool label", default="research-team")
    endpoint_count = _prompt_positive_int("Number of endpoints", default=2)

    endpoints: list[dict[str, object]] = []
    for index in range(endpoint_count):
        print(f"\nEndpoint {index + 1}/{endpoint_count}:")
        model_info = _pick_model_interactive()
        default_name = model_info.name
        name = _prompt_text("Endpoint name", default=default_name)
        tier = _prompt_choice(
            "Tier (user/project/system)",
            ("user", "project", "system"),
            default="user",
        )

        default_ctx = 4096
        if model_info.serving_profile and model_info.serving_profile.max_model_len:
            default_ctx = model_info.serving_profile.max_model_len
        max_allowed = model_info.capabilities.max_position_embeddings
        max_model_len = _prompt_max_model_len(default=default_ctx, max_allowed=max_allowed)

        entry: dict[str, object] = {
            "name": name,
            "model": model_info.name,
        }
        if tier != "user":
            entry["tier"] = tier
        if max_model_len is not None:
            entry["max_model_len"] = _format_ctx_literal(max_model_len)
        endpoints.append(entry)

    return {
        "pool": pool_name,
        "defaults": {
            "tier": "user",
            "timeout": default_timeout,
        },
        "endpoints": endpoints,
    }


def _cmd_up_pool(args, *, interactive: bool) -> int:
    """Provision multiple endpoints via a pool definition file."""
    from .pool import _cmd_pool_up, provision_pool_roster

    if getattr(args, "model", None):
        _die("--model cannot be used with --pool-file", action="up")
    if getattr(args, "max_model_len", None):
        _die(
            "--max-model-len is single-endpoint only; set max_model_len per pool entry instead.",
            action="up",
        )

    pool_path = getattr(args, "pool_file", None)
    path = Path(pool_path).expanduser() if pool_path else _prompt_pool_file_path()

    if path.exists():
        print(f"Using existing pool definition: {path}")
        return _cmd_pool_up(
            Namespace(
                pool_command="up",
                file=str(path),
                continue_on_error=getattr(args, "continue_on_error", False),
                as_json=getattr(args, "as_json", False),
                dry_run=getattr(args, "dry_run", False),
                default_timeout=getattr(args, "timeout", 600),
                sequential=getattr(args, "sequential", False),
            )
        )

    if not interactive:
        _die(
            f"Pool definition file not found: {path}",
            action="up",
            hint="Create it first, or run `r4 up` interactively to generate one.",
            code=2,
        )

    roster = _build_guided_pool_roster(default_timeout=getattr(args, "timeout", 600))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_pool_roster_yaml(roster))
    print(f"\nSaved pool definition: {path}\n")
    return provision_pool_roster(
        roster,
        roster_path=path,
        continue_on_error=getattr(args, "continue_on_error", False),
        as_json=getattr(args, "as_json", False),
        dry_run=getattr(args, "dry_run", False),
        default_timeout=getattr(args, "timeout", 600),
        parallel=not getattr(args, "sequential", False),
    )


def _cmd_up_public(args, *, interactive: bool) -> int:
    """Configure r4 to use a public or cloud endpoint (OpenAI-compat or Bedrock)."""
    settings = _public_endpoint_settings(args)
    _prompt_public_endpoint_settings(settings, interactive=interactive)
    profile = _save_public_endpoint_settings(args, settings)
    _print_public_endpoint_settings(profile, settings)
    return 0


def _public_endpoint_settings(args) -> dict[str, object]:
    public_url = _provider_url(getattr(args, "public", None))
    aws_region = getattr(args, "aws_region", None)
    is_bedrock, public_url, aws_region = _detect_bedrock_public_url(
        public_url,
        aws_region,
    )
    return {
        "public_url": None if is_bedrock else public_url,
        "api_key": getattr(args, "api_key", None),
        "model": getattr(args, "model", None),
        "aws_region": aws_region,
        "aws_profile_name": getattr(args, "aws_profile", None),
        "is_bedrock": is_bedrock,
        "endpoint_provider": "bedrock" if is_bedrock else None,
    }


def _provider_url(public_url: str | None) -> str | None:
    if public_url and public_url.lower() in _PROVIDER_SHORTCUTS:
        return _PROVIDER_SHORTCUTS[public_url.lower()]
    return public_url


def _detect_bedrock_public_url(
    public_url: str | None,
    aws_region: str | None,
) -> tuple[bool, str | None, str | None]:
    if public_url and public_url.lower() == "bedrock":
        return True, None, aws_region
    if not _is_bedrock_runtime_url(public_url):
        return False, public_url, aws_region
    return True, None, aws_region or _bedrock_region_from_url(public_url)


def _is_bedrock_runtime_url(public_url: str | None) -> bool:
    if not public_url:
        return False
    lowered = public_url.lower()
    return "bedrock-runtime" in lowered and "amazonaws.com" in lowered


def _bedrock_region_from_url(public_url: str | None) -> str | None:
    import re

    if not public_url:
        return None
    match = re.search(r"bedrock-runtime\.([a-z0-9-]+)\.amazonaws\.com", public_url.lower())
    return match.group(1) if match else None


def _prompt_public_endpoint_settings(settings: dict[str, object], *, interactive: bool) -> None:
    if settings["is_bedrock"]:
        _prompt_bedrock_settings(settings, interactive=interactive)
        return
    _prompt_openai_compatible_settings(settings, interactive=interactive)


def _prompt_bedrock_settings(settings: dict[str, object], *, interactive: bool) -> None:
    if interactive and not settings["model"]:
        settings["model"] = _prompt_bedrock_model()
    if interactive and not settings["aws_region"]:
        settings["aws_region"] = _prompt_optional_input("AWS region [default: us-east-1]: ")


def _prompt_bedrock_model() -> str:
    print("\nCommon Bedrock model IDs:")
    print("  1  anthropic.claude-3-sonnet-20240229-v1:0")
    print("  2  anthropic.claude-3-haiku-20240307-v1:0")
    print("  3  anthropic.claude-3-5-sonnet-20241022-v2:0")
    print("  4  us.amazon.nova-lite-v1:0")
    print("  5  us.amazon.nova-pro-v1:0")
    print("  6  meta.llama3-1-70b-instruct-v1:0")
    print()
    raw = _prompt_required_input("Model ID [1-6 or full ID]: ")
    return _BEDROCK_MODEL_SHORTCUTS.get(raw, raw) or _BEDROCK_MODEL_SHORTCUTS["1"]


def _prompt_openai_compatible_settings(settings: dict[str, object], *, interactive: bool) -> None:
    if interactive and not settings["public_url"]:
        settings["public_url"] = _prompt_public_provider_url()
    if not settings["public_url"]:
        _die("--public URL is required (or use --public bedrock for AWS Bedrock)", action="up")
    settings["public_url"] = _normalize_public_url(str(settings["public_url"]))
    if interactive and not settings["api_key"]:
        settings["api_key"] = _prompt_optional_input(
            "API key (bearer token, leave blank if none): "
        )
    if interactive and not settings["model"]:
        settings["model"] = _prompt_optional_input("Model name (e.g. gpt-4): ")


def _prompt_public_provider_url() -> str:
    print("\nSupported provider shortcuts:")
    print("  openai      https://api.openai.com")
    print("  gemini      https://generativelanguage.googleapis.com/v1beta/openai")
    print("  groq        https://api.groq.com/openai")
    print("  together    https://api.together.xyz")
    print("  openrouter  https://openrouter.ai/api")
    print("  deepseek    https://api.deepseek.com")
    print("  fireworks   https://api.fireworks.ai/inference")
    print("  mistral     https://api.mistral.ai")
    print("  (or enter a full URL for any OpenAI-compatible endpoint)")
    print()
    raw_url = _prompt_required_input("Provider or URL: ")
    return _PROVIDER_SHORTCUTS.get(raw_url.lower(), raw_url)


def _prompt_required_input(prompt: str) -> str:
    import sys

    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)


def _prompt_optional_input(prompt: str) -> str | None:
    return _prompt_required_input(prompt) or None


def _normalize_public_url(public_url: str) -> str:
    if "://" not in public_url:
        return f"https://{public_url}"
    return public_url


def _save_public_endpoint_settings(args, settings: dict[str, object]) -> str:
    from ..config import ProfileConfigStore

    store = ProfileConfigStore()
    profile = getattr(args, "profile", None) or "default"
    stored = store.load(profile)
    store.save(profile, **_public_endpoint_save_values(stored, settings))
    return profile


def _public_endpoint_save_values(
    stored: dict[str, object],
    settings: dict[str, object],
) -> dict[str, object]:
    save_values: dict[str, object] = {"endpoint_api_key": settings["api_key"] or None}
    public_url = settings["public_url"]
    if public_url:
        save_values.update(
            endpoint_url=public_url,
            endpoint_provider=None,
            aws_region=None,
            aws_profile=None,
        )
    if settings["endpoint_provider"]:
        save_values.update(endpoint_provider=settings["endpoint_provider"], endpoint_url=None)
    if settings["model"]:
        save_values["model"] = settings["model"]
        save_values["model_routes"] = _public_model_routes(stored, str(settings["model"]))
    if settings["aws_region"]:
        save_values["aws_region"] = settings["aws_region"]
    if settings["aws_profile_name"]:
        save_values["aws_profile"] = settings["aws_profile_name"]
    return save_values


def _public_model_routes(stored: dict[str, object], model: str) -> dict[str, object]:
    stored_routes = stored.get("model_routes")
    routes = dict(stored_routes or {}) if isinstance(stored_routes, dict) else {}
    routes[model] = "public"
    return routes


def _print_public_endpoint_settings(profile: str, settings: dict[str, object]) -> None:
    print(f"Endpoint configured (profile: {profile}):")
    if settings["is_bedrock"]:
        print("  Provider: AWS Bedrock")
        print(f"  Region:   {settings['aws_region'] or 'us-east-1 (default)'}")
        if settings["aws_profile_name"]:
            print(f"  AWS Profile: {settings['aws_profile_name']}")
    else:
        print(f"  URL:   {settings['public_url']}")
    if settings["api_key"]:
        print(f"  Key:   {_masked_api_key(str(settings['api_key']))}")
    if settings["model"]:
        print(f"  Model: {settings['model']}")
    print()
    print('  Chat:     r4 chat "Hello"')
    print("  TUI:      r4")
    print("  Remove:   r4 down --public")


def _masked_api_key(api_key: str) -> str:
    return f"{api_key[:8]}{'*' * max(0, len(api_key) - 8)}"


@command
class UpCommand(CommandHandler):
    """``r4 up`` — start one endpoint or a guided multi-endpoint pool."""

    name = "up"
    help = "Start one endpoint or a pool of endpoints"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Quick shortcut to start one mini-RiVault endpoint or a guided pool.\n"
                "Single-endpoint mode is equivalent to: r4 endpoint up [--model ...] --wait"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        p.add_argument(
            "--model",
            "-m",
            help="Model name or path (interactive picker if omitted)",
        )
        p.add_argument(
            "--source",
            "-s",
            metavar="NAME",
            help="Endpoint source to target (see `r4 source list`)",
        )
        p.add_argument(
            "--pool-file",
            help="Provision multiple endpoints from a pool definition YAML file",
        )
        p.add_argument(
            "--max-model-len",
            default=None,
            metavar="N",
            help=(
                "Override vLLM max context length, e.g. 4096 or 32k "
                "(default: model's serving profile)"
            ),
        )
        p.add_argument(
            "--continue-on-error",
            action="store_true",
            help="With --pool-file, keep going if an endpoint fails to provision",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="With --pool-file, print the resolved pool entries without provisioning",
        )
        p.add_argument(
            "--timeout",
            type=int,
            default=600,
            help="Provision/wait timeout in seconds (default: 600)",
        )
        p.add_argument(
            "--ttl-sec",
            type=int,
            default=None,
            metavar="N",
            help=(
                "Endpoint time-to-live in seconds (0 = no expiry / manual teardown). "
                "(default: server-side TTL, typically 3600)"
            ),
        )
        p.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )
        p.add_argument(
            "--sequential",
            action="store_true",
            help="With --pool-file, submit jobs one-by-one instead of in parallel",
        )
        p.add_argument(
            "--public",
            metavar="URL_OR_PROVIDER",
            help=(
                "Use a public endpoint directly (no UMRS provisioning). "
                "Pass a URL for OpenAI-compatible APIs, or 'bedrock' for AWS Bedrock."
            ),
        )
        p.add_argument(
            "--api-key",
            metavar="KEY",
            help="Bearer API key for the public endpoint",
        )
        p.add_argument(
            "--aws-region",
            metavar="REGION",
            help="AWS region for Bedrock (default: us-east-1)",
        )
        p.add_argument(
            "--aws-profile",
            metavar="PROFILE",
            help="AWS credentials profile name for Bedrock",
        )
        p.add_argument(
            "--knowledge",
            "-k",
            metavar="BUNDLE",
            help=(
                "Load a knowledge bundle (.kb file or project ID) for "
                "RAG-augmented inference (ADR-013)"
            ),
        )
        p.add_argument(
            "--local",
            action="store_true",
            help=(
                "Start in local inference mode — no UMRS provisioning, "
                "connect directly to a local or public LLM backend (ADR-013)"
            ),
        )
        return p

    def handle(self, args: Namespace) -> int:
        return cmd_up(args)


def cmd_up(args) -> int:
    """``r4 up`` — interactive shortcut for endpoint provisioning.

    Picks a model interactively (unless --model is given), then provisions
    an endpoint and waits for readiness.
    """
    from ..client import Client

    model = getattr(args, "model", None)
    interactive = _up_is_interactive()

    # --local: skip UMRS provisioning, connect to local LLM backend
    if getattr(args, "local", False):
        return _cmd_up_local(args, model, interactive=interactive)

    if getattr(args, "public", None):
        return _cmd_up_public(args, interactive=interactive)

    # Resolve --source before preflight/provisioning
    _resolve_up_source(args)

    _preflight_up()

    routed = _route_up_pool_or_public(args, model=model, interactive=interactive)
    if routed is not None:
        return routed

    model, model_info = _resolve_up_model(model, interactive=interactive)
    client = Client()
    timeout = getattr(args, "timeout", 600)
    ttl_sec = getattr(args, "ttl_sec", None)
    max_model_len = _resolve_up_context_len(args, model_info)
    as_json = bool(getattr(args, "as_json", False))
    _print_up_request(model, max_model_len, as_json=as_json)
    response = _provision_up_endpoint(client, model, timeout, max_model_len, ttl_sec, quiet=as_json)
    _save_up_model_route(args, model)
    return _finish_up_response(client, response, model, timeout, as_json=as_json)


def _cmd_up_local(args, model: str | None, *, interactive: bool) -> int:
    """``r4 up --local`` — local inference mode (ADR-013).

    No UMRS provisioning. Connect directly to a local or public LLM backend.
    Optionally load a knowledge bundle for RAG-augmented inference.
    """
    from ..config import get_config

    cfg = get_config()

    # Resolve the local endpoint URL
    endpoint_url = getattr(args, "public", None) or getattr(cfg, "endpoint_url", None)
    if not endpoint_url:
        _die(
            "Local mode requires a public endpoint URL.",
            action="up",
            hint="Pass --public <url> or configure with: r4 up --public <url>",
        )

    # Resolve model name
    if not model:
        if interactive:
            model = _pick_model_interactive()
        else:
            _die("Local mode requires --model <name> when non-interactive.", action="up")

    # Load knowledge bundle if specified
    knowledge_bundle = getattr(args, "knowledge", None)
    if knowledge_bundle:
        _print_local_kb_loading(knowledge_bundle, model)

    _print_up_request(model, None, as_json=False)
    print(f"  Endpoint: {endpoint_url}")
    print("  Mode: local (no UMRS provisioning)")
    if knowledge_bundle:
        print(f"  Knowledge: {knowledge_bundle}")
    print()

    # Launch chat
    from dataclasses import replace as _replace

    from ..config import set_config

    # Temporarily set endpoint URL for this session
    cfg = _replace(cfg, endpoint_url=endpoint_url)
    set_config(cfg)

    # Build chat args namespace with all attributes cmd_chat expects
    from .chat import cmd_chat

    chat_args = Namespace(
        model=model,
        message=None,
        system=None,
        stream=True,
        temperature=0.7,
        max_tokens=None,
        tools=None,
        tool_choice=None,
        interactive=False,
        secure=False,
        companion=False,
        teamup=False,
        no_discover=True,  # local mode — skip source auto-discovery
        task=None,
        agent=None,
        source=None,
        fs_root=None,
        resume=None,
        skill=None,
        knowledge=knowledge_bundle,
    )
    return cmd_chat(chat_args)


def _print_local_kb_loading(bundle: str, model: str | None) -> None:
    """Print knowledge bundle loading message."""
    from pathlib import Path

    bundle_path = Path(bundle)
    if bundle_path.exists() and bundle_path.suffix == ".kb":
        print(f"Loading knowledge bundle: {bundle_path}")
    elif bundle_path.is_dir() and (bundle_path / "manifest.json").exists():
        print(f"Loading knowledge bundle: {bundle_path}")
    else:
        # Assume it's a project ID
        print(f"Loading knowledge for project: {bundle}")


def _up_is_interactive() -> bool:
    import sys

    return sys.stdin.isatty() and sys.stdout.isatty()


def _resolve_up_source(args) -> None:
    """Apply --source flag to the global config, same pattern as endpoint commands."""
    source_name = getattr(args, "source", None)
    if not source_name:
        return
    from ..source import resolve_active_source
    from ..source_context import use_source

    source = resolve_active_source(source_name)
    if source is None:
        _die(
            f"Source '{source_name}' not found.",
            action="up",
            hint="Use `r4 source list` to see available sources.",
        )
    use_source(source)  # its own credential only (onboarding plan S1)


def _preflight_up() -> None:
    from ._helpers import _preflight_check

    _preflight_check(action="up")


def _route_up_pool_or_public(args, *, model: str | None, interactive: bool) -> int | None:
    pool_file = getattr(args, "pool_file", None)
    if model and pool_file:
        _die("--model and --pool-file are mutually exclusive", action="up")
    if pool_file:
        return _cmd_up_pool(args, interactive=interactive)
    _reject_pool_only_flags(args)
    if not model and interactive:
        return _route_interactive_up_mode(args)
    return None


def _reject_pool_only_flags(args) -> None:
    if getattr(args, "dry_run", False) or getattr(args, "continue_on_error", False):
        _die(
            "--dry-run and --continue-on-error require --pool-file or "
            "interactive multi-endpoint mode.",
            action="up",
        )


def _route_interactive_up_mode(args) -> int | None:
    up_mode = _prompt_up_mode()
    if up_mode == "multiple":
        return _cmd_up_pool(args, interactive=True)
    if up_mode == "public":
        return _cmd_up_public(args, interactive=True)
    return None


def _apply_picker_source(model_info) -> None:
    """If model was picked from a non-active source, apply it to the global config."""
    source_name = getattr(model_info, "_picker_source", None)
    if not source_name:
        return
    from ..source import resolve_active_source
    from ..source_context import use_source

    source = resolve_active_source(source_name)
    if source is not None and source.kind == "platform":
        use_source(source)  # its own credential only (onboarding plan S1)


def _resolve_up_model(model: str | None, *, interactive: bool):
    if interactive and not model:
        model_info = _pick_model_interactive()
        _apply_picker_source(model_info)
        return model_info.name, model_info
    if model:
        return model, None
    _die(
        "--model is required when not running interactively.",
        action="up",
        hint="r4 up --model <name>",
    )


def _resolve_up_context_len(args, model_info) -> int | None:
    raw_len = getattr(args, "max_model_len", None)
    max_model_len = _parse_up_context_flag(raw_len)
    max_allowed = _up_model_context_ceiling(model_info)
    max_model_len = _clamp_up_context_len(max_model_len, max_allowed)
    if model_info is not None and max_model_len is None:
        max_model_len = _prompt_up_context_len(model_info, max_allowed)
    return max_model_len


def _parse_up_context_flag(raw_len) -> int | None:
    max_model_len = _parse_context_len(raw_len) if raw_len else None
    if raw_len and max_model_len is None:
        _die(f"Invalid --max-model-len value: {raw_len!r}", action="up")
    return max_model_len


def _up_model_context_ceiling(model_info) -> int | None:
    if model_info is None:
        return None
    return model_info.capabilities.max_position_embeddings


def _clamp_up_context_len(max_model_len: int | None, max_allowed: int | None) -> int | None:
    if max_model_len is None or max_allowed is None or max_model_len <= max_allowed:
        return max_model_len
    import sys

    limit_display = f"{max_allowed // 1024}k" if max_allowed >= 1024 else str(max_allowed)
    print(
        f"  Warning: --max-model-len {max_model_len} exceeds model's "
        f"max_position_embeddings ({limit_display}). Clamping to {limit_display}.",
        file=sys.stderr,
    )
    return max_allowed


def _prompt_up_context_len(model_info, max_allowed: int | None) -> int | None:
    default_ctx = 4096
    if model_info.serving_profile and model_info.serving_profile.max_model_len:
        default_ctx = model_info.serving_profile.max_model_len
    return _prompt_max_model_len(default=default_ctx, max_allowed=max_allowed)


def _print_up_request(model: str, max_model_len: int | None, *, as_json: bool) -> None:
    if as_json:
        return
    print("Provisioning endpoint via UMRS...")
    print(f"  Model: {model}")
    if max_model_len is not None:
        print(f"  Context Length: {max_model_len}")
    print()


def _provision_up_endpoint(
    client,
    model: str,
    timeout: int,
    max_model_len: int | None,
    ttl_sec: int | None,
    *,
    quiet: bool,
):
    try:
        return _run_with_provision_progress(
            lambda: client.provision_endpoint(
                model=model,
                timeout=timeout,
                max_model_len=max_model_len,
                ttl_sec=ttl_sec,
            ),
            timeout=timeout,
            action="up",
            client=client,
            model=model,
            quiet=quiet,
        )
    except Exception as e:
        _die(str(e), action="up")


def _save_up_model_route(args, model: str) -> None:
    from ..config import Config, ProfileConfigStore
    from ..model_routing import update_model_route_map

    cfg = Config.from_env(profile=getattr(args, "profile", None))
    ProfileConfigStore().save(
        cfg.profile,
        model=model,
        model_routes=update_model_route_map(cfg, model, "platform"),
    )


def _finish_up_response(client, response: dict, model: str, timeout: int, *, as_json: bool) -> int:
    import json

    instance_id = response.get("instance_id", "unknown")

    if as_json:
        print(json.dumps(response, indent=2))
        return 0

    print(f"  Instance ID: {instance_id}")
    print(f"  Status: {response.get('status', 'pending')}")

    if response.get("endpoint_url"):
        print(f"  URL: {response['endpoint_url']}")

    _wait_for_endpoint_ready(client, instance_id, timeout=timeout, action="up")
    print(f"\n  Use it with:  r4 companion --model {model}")
    return 0
