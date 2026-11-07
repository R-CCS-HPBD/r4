"""``r4 down`` — quick shortcut for stopping an endpoint.

Equivalent to ``r4 endpoint down <instance_id>`` with auto-detection
of the active endpoint when ``--instance`` is omitted and only one is
running.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path

from ._helpers import _die, _endpoint_reference_hint, _is_http_404
from .base import CommandHandler
from .registry import command


def _prompt_down_mode() -> str:
    """Prompt how to stop multiple active endpoints."""
    import sys

    while True:
        try:
            raw = input("Stop [o]ne, [a]ll, or [p]ool file? [o/a/p]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if raw in {"", "o", "one"}:
            return "one"
        if raw in {"a", "all"}:
            return "all"
        if raw in {"p", "pool"}:
            return "pool"
        print("  Enter 'o', 'a', or 'p'.")


def _prompt_pool_file_path(default_name: str = "r4-pool.yaml") -> Path:
    """Prompt for the pool definition file path."""
    import sys

    try:
        raw = input(f"Pool definition file [default: {default_name}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)
    return Path(raw or default_name).expanduser()


def _pick_endpoint_interactive(endpoints: list[dict]) -> str:
    """Prompt the user to choose one active endpoint to stop."""
    import sys

    print("\nActive endpoints:\n")
    print(f"  {'#':<4} {'INSTANCE':<20} {'MODEL':<32} STATUS")
    print(f"  {'─' * 4} {'─' * 20} {'─' * 32} {'─' * 12}")
    for index, endpoint in enumerate(endpoints, start=1):
        print(
            f"  {index:<4} {str(endpoint.get('instance_id', '?'))[:20]:<20} "
            f"{str(endpoint.get('model', ''))[:32]:<32} {endpoint.get('status', '')}"
        )

    while True:
        try:
            raw = input(f"Select endpoint [1-{len(endpoints)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        try:
            choice = int(raw)
        except ValueError:
            print(f"  Enter a number between 1 and {len(endpoints)}.")
            continue
        if 1 <= choice <= len(endpoints):
            return str(endpoints[choice - 1].get("instance_id"))
        print(f"  Enter a number between 1 and {len(endpoints)}.")


def _stop_many_endpoints(
    client,
    endpoints: list[dict],
    *,
    force: bool,
    continue_on_error: bool,
    as_json: bool,
    label: str,
) -> int:
    """Stop several endpoints sequentially and print a short summary."""
    import json

    results: list[dict[str, object]] = []
    fail_count = 0
    for endpoint in endpoints:
        result = _stop_many_endpoint_result(client, endpoint, force=force, as_json=as_json)
        results.append(result)
        if not result.get("ok"):
            fail_count += 1
            if not continue_on_error:
                break

    if as_json:
        print(json.dumps({"label": label, "results": results}, indent=2))
    else:
        _print_stop_many_summary(label, results)

    return 1 if fail_count else 0


def _stop_many_endpoint_result(client, endpoint: dict, *, force: bool, as_json: bool) -> dict:
    instance_id = str(endpoint.get("instance_id", "?"))
    model = str(endpoint.get("model", ""))
    if not as_json:
        print(f"-> Stopping {instance_id} ({model})...")
    try:
        response = client.stop_endpoint(instance_id, force=force)
        return {
            "instance_id": instance_id,
            "model": model,
            "status": response.get("status", "stopped"),
            "ok": True,
        }
    except Exception as exc:  # noqa: BLE001 - summary/continue behaviour
        return {
            "instance_id": instance_id,
            "model": model,
            "error": str(exc),
            "ok": False,
        }


def _print_stop_many_summary(label: str, results: list[dict[str, object]]) -> None:
    print()
    print(f"{label} stop summary:")
    print(f"  {'INSTANCE':<20} {'MODEL':<32} STATUS")
    for result in results:
        print(_stop_many_summary_line(result))


def _stop_many_summary_line(result: dict[str, object]) -> str:
    if result.get("ok"):
        status = str(result.get("status", "stopped"))
    else:
        status = f"FAILED  {str(result.get('error', '?'))[:40]}"
    return (
        f"  {str(result.get('instance_id', '?'))[:20]:<20} "
        f"{str(result.get('model', ''))[:32]:<32} {status}"
    )


_TERMINAL_STATUSES = frozenset({"failed", "terminated", "expired", "deprovisioned"})


def _list_stoppable_endpoints(client) -> list[dict]:
    """Return endpoints that can be stopped (active + provisioning/starting)."""
    response = client.list_endpoints(include_stopped=True)
    return [
        ep
        for ep in response.get("endpoints", [])
        if str(ep.get("status", "")).lower() not in _TERMINAL_STATUSES
    ]


@command
class DownCommand(CommandHandler):
    """``r4 down`` — stop one endpoint or a tracked pool."""

    name = "down"
    help = "Stop one endpoint, all endpoints, or a tracked pool"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Stop a mini-RiVault endpoint.\n"
                "If only one endpoint is active, it is selected automatically.\n"
                "Use --all or --pool-file to stop multiple endpoints.\n"
                "Single-endpoint mode is equivalent to: r4 endpoint down <instance-id>"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        p.add_argument(
            "instance_id",
            nargs="?",
            default=None,
            help="Instance ID to stop (auto-detected if only one is active)",
        )
        p.add_argument(
            "--pool-file",
            help="Stop all endpoints tracked by a pool definition YAML file",
        )
        p.add_argument(
            "--all",
            action="store_true",
            help="Stop all active endpoints",
        )
        p.add_argument(
            "--continue-on-error",
            action="store_true",
            help="With --all/--pool-file, keep going if an endpoint fails to stop",
        )
        p.add_argument(
            "--force",
            "-f",
            action="store_true",
            help="Force stop without graceful shutdown",
        )
        p.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )
        p.add_argument(
            "--public",
            action="store_true",
            help="Remove the public endpoint configuration from the active profile",
        )
        return p

    def handle(self, args: Namespace) -> int:
        if getattr(args, "public", False):
            return _cmd_down_public(args)

        from ._helpers import _preflight_check

        _preflight_check(action="down")

        return cmd_down(args)


def _cmd_down_public(args) -> int:
    """Remove public/cloud endpoint configuration from the active profile."""
    from ..config import ProfileConfigStore

    profile = getattr(args, "profile", None) or "default"
    store = ProfileConfigStore()
    stored = store.load(profile)
    endpoint_url = stored.get("endpoint_url")
    endpoint_provider = stored.get("endpoint_provider")

    if not endpoint_url and not endpoint_provider:
        print(f"No public endpoint configured in profile '{profile}'.")
        return 0

    # Clear endpoint settings and the endpoint-specific API key.
    # The gateway auth (api_key / OIDC) is NOT touched — it stays valid
    # so the user can continue using gateway commands after removing the
    # public endpoint.
    remaining_routes = _remaining_non_public_routes(stored.get("model_routes"))

    store.save(
        profile,
        endpoint_url=None,
        endpoint_provider=None,
        endpoint_api_key=None,
        aws_region=None,
        aws_profile=None,
        model_routes=remaining_routes,
    )
    _print_down_public_result(profile, endpoint_provider, endpoint_url)
    return 0


def _remaining_non_public_routes(model_routes) -> dict[str, str] | None:
    if not isinstance(model_routes, dict):
        return None
    remaining = {
        str(model): str(route) for model, route in model_routes.items() if route != "public"
    }
    return remaining or None


def _print_down_public_result(
    profile: str,
    endpoint_provider: str | None,
    endpoint_url: str | None,
) -> None:
    if endpoint_provider:
        print(f"Cloud endpoint removed from profile '{profile}':")
        print(f"  Provider: {endpoint_provider}")
    elif endpoint_url:
        print(f"Public endpoint removed from profile '{profile}':")
        print(f"  Was: {endpoint_url}")


def cmd_down(args) -> int:
    """``r4 down`` — stop a running endpoint."""
    from ..client import Client

    client = Client()
    instance_id = getattr(args, "instance_id", None)
    pool_file = getattr(args, "pool_file", None)
    stop_all = getattr(args, "all", False)
    continue_on_error = getattr(args, "continue_on_error", False)

    _validate_down_mode(instance_id, pool_file, stop_all)

    if pool_file:
        return _down_pool_file(args, pool_file, continue_on_error)

    if stop_all:
        return _down_all_endpoints(client, args, continue_on_error)

    if not instance_id:
        resolved = _resolve_down_instance(client, args, continue_on_error)
        if isinstance(resolved, int):
            return resolved
        instance_id = resolved

    return _stop_single_endpoint(client, args, instance_id)


def _validate_down_mode(instance_id: str | None, pool_file: str | None, stop_all: bool) -> None:
    if sum(bool(value) for value in (instance_id, pool_file, stop_all)) > 1:
        _die("Choose only one of <instance-id>, --all, or --pool-file.", action="down")


def _down_pool_file(args: Namespace, pool_file: str, continue_on_error: bool) -> int:
    from .pool import _cmd_pool_down

    pool_args = _pool_down_args(args, str(Path(pool_file).expanduser()), continue_on_error)
    return _cmd_pool_down(pool_args)


def _pool_down_args(args: Namespace, file_path: str, continue_on_error: bool) -> Namespace:
    return Namespace(
        pool_command="down",
        file=file_path,
        continue_on_error=continue_on_error,
        force=getattr(args, "force", False),
        as_json=getattr(args, "as_json", False),
    )


def _down_all_endpoints(client, args: Namespace, continue_on_error: bool) -> int:
    return _stop_many_endpoints(
        client,
        _stoppable_endpoints_or_die(client),
        force=getattr(args, "force", False),
        continue_on_error=continue_on_error,
        as_json=getattr(args, "as_json", False),
        label="All active endpoints",
    )


def _stoppable_endpoints_or_die(client) -> list[dict]:
    try:
        endpoints = _list_stoppable_endpoints(client)
    except Exception as exc:
        _die(str(exc), action="down")
    if not endpoints:
        _die("No active endpoints to stop.", action="down")
    return endpoints


def _resolve_down_instance(client, args: Namespace, continue_on_error: bool) -> str | int:
    import sys

    endpoints = _stoppable_endpoints_or_die(client)
    if len(endpoints) == 1:
        return _auto_selected_endpoint(endpoints[0])
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _resolve_interactive_down_choice(client, args, endpoints, continue_on_error)
    _die(_multiple_endpoint_message(endpoints), action="down")


def _auto_selected_endpoint(endpoint: dict) -> str:
    instance_id = str(endpoint.get("instance_id"))
    model = endpoint.get("model", "")
    print(f"Auto-selected endpoint: {instance_id}")
    if model:
        print(f"  Model: {model}")
    return instance_id


def _resolve_interactive_down_choice(
    client,
    args: Namespace,
    endpoints: list[dict],
    continue_on_error: bool,
) -> str | int:
    mode = _prompt_down_mode()
    if mode == "all":
        return _down_all_interactive(client, args, endpoints, continue_on_error)
    if mode == "pool":
        return _down_pool_interactive(args, continue_on_error)
    return _pick_endpoint_interactive(endpoints)


def _down_all_interactive(
    client,
    args: Namespace,
    endpoints: list[dict],
    continue_on_error: bool,
) -> int:
    return _stop_many_endpoints(
        client,
        endpoints,
        force=getattr(args, "force", False),
        continue_on_error=continue_on_error,
        as_json=getattr(args, "as_json", False),
        label="All active endpoints",
    )


def _down_pool_interactive(args: Namespace, continue_on_error: bool) -> int:
    from .pool import _cmd_pool_down

    return _cmd_pool_down(_pool_down_args(args, str(_prompt_pool_file_path()), continue_on_error))


def _multiple_endpoint_message(endpoints: list[dict]) -> str:
    lines = ["Multiple active endpoints — specify one:\n"]
    for endpoint in endpoints:
        lines.append(
            f"  {endpoint.get('instance_id', '?')}  "
            f"{endpoint.get('model', '')}  ({endpoint.get('status', '')})"
        )
    lines.extend(["", "Usage: r4 down <instance-id>", "       r4 down --all"])
    lines.append("       r4 down --pool-file <file>")
    return "\n".join(lines)


def _stop_single_endpoint(client, args: Namespace, instance_id: str) -> int:
    import json

    print(f"Stopping endpoint {instance_id}...")
    try:
        response = client.stop_endpoint(instance_id, force=getattr(args, "force", False))
    except Exception as exc:
        hint = None
        if _is_http_404(exc):
            hint = _endpoint_reference_hint(client, instance_id, command="down")
        _die(str(exc), action="down", hint=hint)

    if getattr(args, "as_json", False):
        print(json.dumps(response, indent=2))
    else:
        print(f"  Status: {response.get('status', 'stopped')}")
    return 0
