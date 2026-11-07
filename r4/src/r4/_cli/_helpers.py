"""Shared CLI helpers (Phase 3, 2026-04-28).

Re-exported from ``r4.cli`` for backward compatibility with tests and
any external callers that ``from r4.cli import _die`` etc.

Convention: all names are module-private (``_``-prefixed) — the leading
underscore signals "internal API, may change between releases". Tests
are free to import them; downstream users should not.
"""

from __future__ import annotations

import sys
import threading
import time
from argparse import Namespace
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config
    from ..source import Source


def _die(
    message: str,
    *,
    action: str | None = None,
    hint: str | None = None,
    code: int = 1,
    error_id: str = "",
) -> None:
    """Centralized CLI error emitter (bug.memo H2, 2026-04-25).

    Normalizes the shape of user-facing errors:

        Error [error_id]: <message>
          hint: <hint>

    ``action`` lets call sites tag the failing operation ("provisioning
    endpoint") so grepping logs stays easy. Callers don't need to repeat
    that in ``message``.

    ``error_id`` is a machine-stable identifier (e.g. "endpoint.forbidden")
    for automation. When provided, it is displayed in brackets after "Error".

    Hints are bilingual: when ``hint`` is omitted, the ``error_id`` catalog
    in ``r4.i18n`` supplies the localized text; in Japanese locale a catalog
    entry also takes precedence over an English inline hint so parity stays
    auditable in one place.
    """
    from ..i18n import hint_for, hint_label, locale

    localized = hint_for(error_id) if error_id else None
    if locale() == "ja" and localized is not None:
        hint = localized
    elif hint is None:
        hint = localized

    id_part = f" [{error_id}]" if error_id else ""
    prefix = f"Error{id_part}{f' ({action})' if action else ''}: "
    print(f"{prefix}{message}", file=sys.stderr)
    if hint:
        print(f"  {hint_label()}: {hint}", file=sys.stderr)
    sys.exit(code)


def _current_project_id() -> str | None:
    """The selected project: `r4 project switch`, else the active profile's
    project (`r4 auth login` / `r4 config context` — what requests carry).

    Under ``--source`` (or an active source) on another deployment, only that
    source's own project: the active deployment's switched project is never
    sent there (onboarding plan S1, :func:`r4.source_context.selected_project`).
    """
    try:
        from ..source_context import selected_project

        return selected_project()
    except Exception:
        return None


def _is_http_404(exc: Exception) -> bool:
    """Return True when an SDK exception wraps an HTTP 404 response."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details.get("status_code") == 404:
        return True
    return "API error: 404" in str(exc)


def _endpoint_reference_hint(client, instance_ref: str, *, command: str) -> str | None:
    """Suggest the real endpoint id when a row number was passed by mistake."""
    if not instance_ref.isdigit():
        return None

    try:
        endpoints = client.list_endpoints(include_stopped=False).get("endpoints", [])
    except Exception:
        return None

    instance_ids = [str(ep.get("instance_id")) for ep in endpoints if ep.get("instance_id")]
    if instance_ref in instance_ids:
        return None

    index = int(instance_ref)
    if 1 <= index <= len(instance_ids):
        actual_id = instance_ids[index - 1]
        return (
            f"`{instance_ref}` looks like row #{index} from `r4 endpoint list`, "
            f"not the instance ID. Re-run `r4 endpoint {command} {actual_id}`."
        )
    return None


def _format_wait_duration(seconds: float) -> str:
    """Format a duration for short CLI wait updates."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _render_wait_bar(elapsed_sec: float, timeout_sec: int, *, width: int = 24) -> str:
    """Render a simple ASCII timeout bar for endpoint readiness polling."""
    if timeout_sec <= 0:
        return "[" + ("-" * width) + "]"

    ratio = min(max(elapsed_sec / float(timeout_sec), 0.0), 1.0)
    filled = min(width, int(ratio * width))
    if elapsed_sec > 0 and filled == 0:
        filled = 1
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_endpoint_wait_line(
    *,
    elapsed_sec: float,
    timeout_sec: int,
    poll_count: int,
    status: str,
    detail: str | None = None,
) -> str:
    """Build the one-line readiness progress display for endpoint waits."""
    spinner = "|/-\\"[poll_count % 4]
    line = (
        f"  {spinner} {_render_wait_bar(elapsed_sec, timeout_sec)} "
        f"{_format_wait_duration(elapsed_sec)}/{_format_wait_duration(timeout_sec)} "
        f"status={status or 'unknown'}"
    )
    if detail:
        cleaned = " ".join(str(detail).split())
        if len(cleaned) > 72:
            cleaned = cleaned[:69] + "..."
        line += f" detail={cleaned}"
    return line


def _endpoint_wait_detail(status_resp: dict[str, Any]) -> str | None:
    """Select the most useful detail field from an endpoint status payload."""
    for key in (
        "detail",
        "active_attempt_status_detail",
        "status_detail",
        "message",
        "job_status",
    ):
        value = status_resp.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _friendly_status_detail(status: str, ep: dict[str, Any] | None = None) -> str | None:
    """Return a CLI-friendly fallback phrase for known provisioning states."""
    normalized_status = str(status or "").strip().lower()
    host = str((ep or {}).get("host") or "")
    if normalized_status in ("provisioning", "pending"):
        if not host or host in ("pending", ""):
            return "waiting for node allocation"
        return "starting container / loading model"
    if normalized_status == "starting":
        return "starting container / loading model"
    if normalized_status == "ready":
        return "ready"
    return None


def _display_status_detail(
    status: str,
    *,
    status_resp: dict[str, Any] | None = None,
    ep: dict[str, Any] | None = None,
) -> str | None:
    """Choose the most useful detail to render for endpoint progress."""
    normalized_status = str(status or "").strip().lower()
    raw_detail = _endpoint_wait_detail(status_resp or {})
    if isinstance(raw_detail, str):
        cleaned = " ".join(raw_detail.split())
        if cleaned and cleaned.lower() != normalized_status:
            return cleaned
    return _friendly_status_detail(normalized_status, ep)


def _wait_for_endpoint_ready(client, instance_id: str, *, timeout: int, action: str) -> dict:
    """Poll endpoint status and render a visible progress indicator."""
    interactive = sys.stdout.isatty()
    last_rendered_width = 0
    start = time.monotonic()
    poll_count = 0
    last_printed_status: str | None = None

    print("\nWaiting for endpoint to be ready...")

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            if interactive and last_rendered_width:
                sys.stdout.write("\r" + (" " * last_rendered_width) + "\r")
                sys.stdout.flush()
            _die(f"Timeout waiting for endpoint (waited {timeout}s)", action=action)

        status_resp = client.get_endpoint_status(instance_id)
        status = status_resp.get("status", "unknown")
        poll_count += 1
        detail = _endpoint_wait_progress_detail(str(status), status_resp)
        line = _format_endpoint_wait_line(
            elapsed_sec=elapsed,
            timeout_sec=timeout,
            poll_count=poll_count,
            status=str(status),
            detail=detail,
        )
        last_rendered_width, last_printed_status = _render_endpoint_wait_update(
            interactive=interactive,
            line=line,
            last_rendered_width=last_rendered_width,
            last_printed_status=last_printed_status,
            status=str(status),
            detail=detail,
        )

        finished = _finish_endpoint_wait_if_terminal(
            status=str(status),
            status_resp=status_resp,
            interactive=interactive,
            action=action,
        )
        if finished is not None:
            return finished

        remaining = max(0.0, timeout - (time.monotonic() - start))
        time.sleep(min(5.0, remaining))


def _endpoint_wait_progress_detail(
    status: str,
    status_resp: dict[str, Any],
) -> str | None:
    if status in {"ready", "failed"}:
        return None
    return _display_status_detail(status, status_resp=status_resp)


def _render_endpoint_wait_update(
    *,
    interactive: bool,
    line: str,
    last_rendered_width: int,
    last_printed_status: str | None,
    status: str,
    detail: str | None,
) -> tuple[int, str | None]:
    if interactive:
        padded = line.ljust(last_rendered_width)
        sys.stdout.write("\r" + padded)
        sys.stdout.flush()
        return max(last_rendered_width, len(line)), last_printed_status

    status_key = f"{status}|{detail or ''}"
    if status_key != last_printed_status:
        print(line)
        return last_rendered_width, status_key
    return last_rendered_width, last_printed_status


def _finish_endpoint_wait_if_terminal(
    *,
    status: str,
    status_resp: dict,
    interactive: bool,
    action: str,
) -> dict | None:
    if status == "ready":
        _finish_endpoint_wait_line(interactive)
        print("  Endpoint ready!")
        if status_resp.get("endpoint_url"):
            print(f"  URL: {status_resp['endpoint_url']}")
        return status_resp

    if status == "failed":
        _finish_endpoint_wait_line(interactive)
        _die(status_resp.get("error", "unknown error"), action=action)
    return None


def _finish_endpoint_wait_line(interactive: bool) -> None:
    if interactive:
        sys.stdout.write("\n")
        sys.stdout.flush()


def _run_with_provision_progress(
    operation,
    *,
    timeout: int,
    action: str,
    client=None,
    model: str | None = None,
    quiet: bool = False,
):
    """Run the blocking provision request while rendering live progress."""
    return _ProvisionProgressRunner(
        operation,
        timeout=timeout,
        action=action,
        client=client,
        model=model,
        quiet=quiet,
    ).run()


class _ProvisionProgressRunner:
    """Stateful renderer for the initial endpoint provisioning request."""

    _poll_interval_sec = 5.0

    def __init__(
        self,
        operation,
        *,
        timeout: int,
        action: str,
        client=None,
        model: str | None = None,
        quiet: bool = False,
    ) -> None:
        self.operation = operation
        self.timeout = timeout
        self.action = action
        self.client = client
        self.model = model
        self.quiet = quiet
        self.interactive = sys.stdout.isatty()
        self.done = threading.Event()
        self.state: dict[str, object] = {}
        self.last_rendered_width = 0
        self.last_noninteractive_report_sec = -1
        self.start = time.monotonic()
        self.next_poll_at = self.start + self._poll_interval_sec
        self.poll_count = 0
        self.last_printed_status: str | None = None
        self.live: dict[str, str | None] = {
            "status": "submitting",
            "detail": "contacting UMRS",
        }

    def run(self):
        if not self.quiet:
            print("\nSubmitting provisioning request to UMRS...")

        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()

        if not self.quiet:
            self._render(0.0)

        wait_interval = 0.1 if self.interactive else 1.0
        while not self.done.wait(wait_interval):
            self._tick()

        elapsed = time.monotonic() - self.start
        self._finish_interactive_line()
        self._raise_worker_error()
        if not self.quiet:
            print(f"  Provision request accepted after {_format_wait_duration(elapsed)}.")
        return self.state.get("result")

    def _worker(self) -> None:
        try:
            self.state["result"] = self.operation()
        except Exception as exc:
            self.state["error"] = exc
        finally:
            self.done.set()

    def _tick(self) -> None:
        elapsed = time.monotonic() - self.start
        if elapsed >= self.timeout:
            self._clear_interactive_line()
            _die(
                f"Timeout while waiting for provisioning request (waited {self.timeout}s)",
                action=self.action,
            )

        if not self.quiet:
            self._poll_endpoint_progress(time.monotonic())
            self.poll_count += 1
            self._render(elapsed)

    def _poll_endpoint_progress(self, now: float) -> None:
        if self.client is None or self.model is None:
            return
        if now < self.next_poll_at:
            return
        self.next_poll_at = now + self._poll_interval_sec
        try:
            resp = self.client.list_endpoints(include_stopped=True)
            for ep in resp.get("endpoints", []):
                if ep.get("model") != self.model:
                    continue
                ep_status = str(ep.get("status") or "").lower()
                if ep_status in ("provisioning", "starting", "pending", "ready"):
                    self._set_endpoint_progress(ep_status, ep)
                    break
        except Exception:
            pass

    def _set_endpoint_progress(self, ep_status: str, ep: dict[str, Any]) -> None:
        self.live["status"] = ep_status
        instance_id = ep.get("instance_id") or ep.get("job_id")
        if not instance_id:
            self.live["detail"] = _display_status_detail(ep_status, ep=ep)
            return
        try:
            detail_resp = self.client.get_endpoint_status(str(instance_id))
            self.live["detail"] = _display_status_detail(
                ep_status,
                status_resp=detail_resp,
                ep=ep,
            )
        except Exception:
            self.live["detail"] = _display_status_detail(ep_status, ep=ep)

    def _render(self, elapsed_sec: float) -> None:
        line = _format_endpoint_wait_line(
            elapsed_sec=elapsed_sec,
            timeout_sec=self.timeout,
            poll_count=self.poll_count,
            status=self.live["status"] or "submitting",
            detail=self.live["detail"],
        )

        if self.interactive:
            padded = line.ljust(self.last_rendered_width)
            sys.stdout.write("\r" + padded)
            sys.stdout.flush()
            self.last_rendered_width = max(self.last_rendered_width, len(line))
            return

        self._render_noninteractive(line, elapsed_sec)

    def _render_noninteractive(self, line: str, elapsed_sec: float) -> None:
        status_key = f"{self.live['status']}|{self.live['detail'] or ''}"
        current_sec = int(elapsed_sec)
        if status_key != self.last_printed_status:
            self.last_printed_status = status_key
            print(line)
            self.last_noninteractive_report_sec = current_sec
        elif self._should_emit_heartbeat(current_sec):
            print(line)
            self.last_noninteractive_report_sec = current_sec

    def _should_emit_heartbeat(self, current_sec: int) -> bool:
        return (
            self.last_noninteractive_report_sec < 0
            or current_sec - self.last_noninteractive_report_sec >= 30
        )

    def _clear_interactive_line(self) -> None:
        if self.interactive and self.last_rendered_width:
            sys.stdout.write("\r" + (" " * self.last_rendered_width) + "\r")
            sys.stdout.flush()

    def _finish_interactive_line(self) -> None:
        if self.interactive:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _raise_worker_error(self) -> None:
        if "error" in self.state:
            raise self.state["error"]


def _format_ctx_max(m) -> str:
    """Format max context length for the model list ``CTX MAX`` column.

    Uses the shared resolver, which prefers declared
    ``max_position_embeddings`` and otherwise combines a curated known value
    with the serving length so public / HF-id-registered models still show a
    real value instead of ``-`` or the misleading 4096 serving default.
    """
    from ..model_context import format_context_length, resolve_max_context

    return format_context_length(resolve_max_context(m))


def _format_quant(m) -> str:
    """Format quantization label."""
    if m.serving_profile and m.serving_profile.quantization:
        return m.serving_profile.quantization
    return "-"


def _flag(v: bool) -> str:
    return "\u2713" if v else "\u2717"


def _parse_context_len_numeric(raw: int | float) -> int | None:
    """Validate a numeric context length value."""
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if raw.is_integer() and raw > 0:
        return int(raw)
    return None


def _parse_context_len(raw: str | int | float | None) -> int | None:
    """Parse a context length value like ``32k``, ``128K``, or ``4096``.

    Returns the integer value, or *None* if the string is not valid.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return _parse_context_len_numeric(raw)

    raw = raw.strip().lower()
    if not raw:
        return None
    multiplier = 1
    if raw.endswith("k"):
        multiplier = 1024
        raw = raw[:-1]
    try:
        num = int(raw)
    except ValueError:
        return None
    return num * multiplier if num > 0 else None


def _preflight_check(*, action: str = "up") -> None:
    """Verify credentials and gateway connectivity before interactive flows.

    Gives clear, actionable errors instead of letting the user type through
    several interactive prompts only to hit a confusing API failure.

    Skipped entirely when a public/cloud endpoint is configured (no gateway
    needed).
    """
    from ..auth import get_auth_manager
    from ..config import get_config

    config = get_config()

    # Public / cloud endpoint — gateway is not needed.
    if config.endpoint_url or config.endpoint_provider:
        return

    auth = get_auth_manager(config.profile)
    _preflight_credentials(config, auth, action=action)
    _preflight_gateway(config, action=action)


def _bound_source_login_hint(config) -> str:
    """How to log in to the source a bound config targets (onboarding plan S1)."""
    from ..source_context import bound_login_hint

    return bound_login_hint(config)


def _preflight_credentials(config, auth, *, action: str) -> None:
    """Step 1 of ``_preflight_check``: local credentials present?"""
    # Also accept config.api_key which may come from source overlay or env.
    if config.api_key:
        return
    if getattr(config, "bound_source", None):
        # Another deployment's source: the profile's login is never sent
        # there, so it does not count (onboarding plan S1).
        _die(
            f"No credential for source '{config.bound_source}'.",
            action=action,
            hint=_bound_source_login_hint(config),
        )
    if auth.is_authenticated():
        return
    _die(
        "No credentials configured.",
        action=action,
        hint=(
            "Set up credentials first:\n"
            "  1. Generate a token on the server:\n"
            "       umrs-server admin token generate-jwt "
            "--user <you> --project <project-id>\n"
            "  2. Save it locally (from a pipe, never an argument):\n"
            "       printf '%s' \"$TOKEN\" | r4 auth login --api-key-stdin\n"
            "  3. Verify:\n"
            "       r4 health"
        ),
    )


def _preflight_failure(config: Any, exc: Exception) -> tuple[str, str]:
    """(message, hint) for a readiness probe that raised.

    Only a transport failure means "unreachable". A gateway that answered
    401/403 or 5xx was reached: telling that user to check their SSH tunnel
    sends them after the wrong fix (c2-cli, 2026-10-21).
    """
    from ..errors import AuthenticationError, R4ConnectionError, R4TimeoutError
    from ..source_context import auth_failure_hint

    url = config.gateway_url
    if isinstance(exc, AuthenticationError):
        return (
            f"Gateway rejected the credential: {exc}",
            f"Gateway URL: {url}\n  {auth_failure_hint(config, exc) or exc.hint}",
        )
    if isinstance(exc, (R4ConnectionError, R4TimeoutError)):
        return (
            f"Gateway unreachable: {exc}",
            f"Gateway URL: {url}\n"
            "  Check that your SSH tunnel or gateway process is running.\n"
            "  Quick diagnosis: `r4 health`",
        )
    return (
        f"Gateway readiness check failed: {exc}",
        f"Gateway URL: {url}\n"
        "  The gateway answered but could not serve the request; "
        "run `r4 health` or ask the operator.",
    )


def _preflight_gateway(config, *, action: str) -> None:
    """Step 2 of ``_preflight_check``: gateway reachable and ready?"""
    from ..client import Client
    from ..errors import R4Error

    client = Client(config=config)
    try:
        readiness = client.readiness()
    except R4Error as exc:
        message, hint = _preflight_failure(config, exc)
        _die(message, action=action, hint=hint)

    if not readiness.get("ready"):
        checks = readiness.get("checks", {})
        failed = [f"{k}: {v}" for k, v in checks.items() if str(v) != "ok"]
        detail = "; ".join(failed) if failed else readiness.get("message", "unknown")
        _die(
            f"Gateway is not ready ({detail}).",
            action=action,
            hint="Run `r4 health --direct-umrs` to diagnose the backend.",
        )


def _pick_model_interactive(model_type=None):
    """Interactive model picker for ``r4 endpoint up`` (no --model).

    Returns a :class:`ModelInfo` object so callers can inspect the
    selected model's serving profile, capabilities, etc.
    """
    models = _available_models_for_picker(model_type)
    _print_model_picker(models)
    return _read_model_selection(models)


def _available_models_for_picker(model_type=None):
    from ..source import get_platform_sources

    # When multiple platform sources are registered, fetch from all of them
    # and tag each model with its source name so the picker can display it.
    sources = get_platform_sources()
    if len(sources) > 1:
        return _available_models_all_sources(sources, model_type)
    return _fetch_models_single_source(model_type)


def _fetch_models_single_source(model_type):
    from ..errors import AuthenticationError, R4ConnectionError
    from ..models import ModelClient, ModelStatus, ModelType

    try:
        mc = ModelClient()
        selected_type = model_type or ModelType.LLM
        models = mc.list_models(status=ModelStatus.AVAILABLE, model_type=selected_type)
    except AuthenticationError as exc:
        _die(
            f"Authentication failed: {exc}",
            action="model picker",
            hint=(
                "Run `printf '%s' \"$TOKEN\" | r4 auth login --api-key-stdin` to set "
                "credentials.\n"
                "  Generate a token on the server with:\n"
                "    umrs-server admin token generate-jwt --user <you> --project <project-id>"
            ),
        )
    except R4ConnectionError as exc:
        _die(
            f"Cannot reach gateway: {exc}",
            action="model picker",
            hint="Check that your SSH tunnel or gateway is up with `r4 health`.",
        )
    except Exception as exc:
        _die_on_model_fetch_error(exc)

    if not models:
        _die(
            "No available models in the registry.",
            action="model picker",
            hint="register one with `r4 model register` or `r4 model stage`",
        )
    for m in models:
        m._picker_source = None
    return models


def _die_on_model_fetch_error(exc: Exception) -> None:
    status_code = getattr(exc, "status_code", None) or (getattr(exc, "details", {}) or {}).get(
        "status_code"
    )
    if status_code == 503:
        _die(
            "Gateway returned 503 — UMRS backend is unreachable.",
            action="model picker",
            hint=(
                "The gateway is running but cannot reach UMRS.\n"
                "  Check that UMRS is started and the gateway's UMRS_BASE_URL\n"
                "  matches the actual UMRS HTTP sidecar port (default 8082).\n"
                "  Quick check: `r4 health --direct-umrs`"
            ),
        )
    _die(
        f"Could not fetch models: {exc}",
        action="model picker",
        hint="Run `r4 health` to diagnose connectivity.",
    )


def _warn_source_failure(source, config, exc: Exception) -> None:
    """One ``[warn]`` row per failing source, with how to fix it (2026-10-15)."""
    from ..source_context import auth_failure_hint

    hint = auth_failure_hint(config, exc)
    print(f"  [warn] {source.name}: {exc}", file=sys.stderr)
    if hint:
        print(f"         hint: {hint}", file=sys.stderr)


def _available_models_all_sources(sources, model_type):
    """Fetch available models from all platform sources and tag each with its source name."""
    from ..config import get_config
    from ..errors import AuthenticationError, R4ConnectionError
    from ..models import ModelClient, ModelStatus, ModelType
    from ..source_context import config_for_source

    selected_type = model_type or ModelType.LLM
    all_models = []
    for source in sources:
        config = config_for_source(source, get_config())  # its own credential only (S1)
        try:
            models = ModelClient(config=config).list_models(
                status=ModelStatus.AVAILABLE, model_type=selected_type
            )
            for m in models:
                m._picker_source = source.name
            all_models.extend(models)
        except (AuthenticationError, R4ConnectionError) as exc:
            _warn_source_failure(source, config, exc)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] {source.name}: could not fetch models: {exc}", file=sys.stderr)

    if not all_models:
        _die(
            "No available models across any source.",
            action="model picker",
            hint="Check each source with `r4 health --all-sources`.",
        )
    return all_models


def _print_model_picker(models) -> None:
    print("\nAvailable models:\n")
    has_sources = any(getattr(m, "_picker_source", None) for m in models)
    if has_sources:
        print(
            f"  {'#':<4} {'NAME':<30} {'SOURCE':<10} {'SCOPE':<10} {'TYPE':<10} "
            f"{'TOOLS':<6} {'VISION':<7} {'CTX MAX':<8} {'QUANT':<6}"
        )
        print(f"  {'─' * 4} " + "─" * 87)
    else:
        print(
            f"  {'#':<4} {'NAME':<30} {'SCOPE':<10} {'TYPE':<10} "
            f"{'TOOLS':<6} {'VISION':<7} {'CTX MAX':<8} {'QUANT':<6}"
        )
        print(f"  {'─' * 4} " + "─" * 77)
    for i, m in enumerate(models, start=1):
        caps = m.capabilities
        if has_sources:
            src = str(getattr(m, "_picker_source", "") or "")[:8]
            print(
                f"  {i:<4} {m.name:<30} {src:<10} {m.scope.value:<10} {m.model_type.value:<10} "
                f"{_flag(caps.supports_tool_calling):<6} {_flag(caps.supports_vision):<7} "
                f"{_format_ctx_max(m):<8} {_format_quant(m):<6}"
            )
        else:
            print(
                f"  {i:<4} {m.name:<30} {m.scope.value:<10} {m.model_type.value:<10} "
                f"{_flag(caps.supports_tool_calling):<6} {_flag(caps.supports_vision):<7} "
                f"{_format_ctx_max(m):<8} {_format_quant(m):<6}"
            )

    print()


def _read_model_selection(models):
    while True:
        try:
            raw = input(f"Select model [1-{len(models)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if not raw:
            continue
        try:
            idx = int(raw) - 1
        except ValueError:
            print(f"  Enter a number between 1 and {len(models)}.")
            continue
        if 0 <= idx < len(models):
            selected = models[idx]
            print(f"\n  Selected: {selected.name}\n")
            return selected
        print(f"  Enter a number between 1 and {len(models)}.")


def _prompt_max_model_len(
    default: int = 4096,
    max_allowed: int | None = None,
) -> int | None:
    """Prompt the user for a context length override.

    Accepts plain integers (``4096``) or shorthand (``32k``, ``128K``).
    *max_allowed* is the model's ``max_position_embeddings`` — values
    above it will be rejected with a message.
    Returns the chosen value, or *None* if the user accepts the default
    (meaning "don't override, use the model's serving profile").
    """
    default_display = f"{default // 1024}k" if default >= 1024 else str(default)
    limit_display = ""
    if max_allowed is not None:
        limit_display = f"{max_allowed // 1024}k" if max_allowed >= 1024 else str(max_allowed)
        prompt = f"Context length [default: {default_display}, max: {limit_display}]: "
    else:
        prompt = f"Context length [default: {default_display}]: "
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)
    if not raw:
        return None  # accept default
    value = _parse_context_len(raw)
    if value is None:
        print(f"  Invalid value — using default ({default_display}).")
        return None
    if max_allowed is not None and value > max_allowed:
        print(f"  Error: {value} exceeds model's max_position_embeddings ({limit_display}).")
        print(f"  Using maximum allowed: {limit_display}")
        return max_allowed
    return value


def _parse_lora_specs(lora_args, action: str):
    """Parse ``--lora name=source`` flags into a list of dicts."""
    if not lora_args:
        return []
    specs: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in lora_args:
        if "=" not in raw:
            _die(f"--lora expects name=source (got {raw!r}) for `{action}`")
        name, _, source = raw.partition("=")
        name = name.strip()
        source = source.strip()
        if not name or not source:
            _die(f"--lora name and source must both be non-empty (got {raw!r})")
        if name in seen:
            _die(f"--lora name {name!r} is duplicated in `{action}` arguments")
        seen.add(name)
        specs.append({"name": name, "source": source})
    return specs


def _resolve_endpoint_models(args, action: str):
    """Normalise ``--model`` / ``--mode`` / ``--lora`` for ``up`` & ``endpoint up``."""
    models = _endpoint_model_values(getattr(args, "model", None))
    requested_mode = getattr(args, "mode", None)
    loras = _parse_lora_specs(getattr(args, "lora", None), action)
    _validate_endpoint_model_request(models, requested_mode, loras, action)
    return models, _endpoint_serving_mode(models, requested_mode, loras, action), loras


def _endpoint_model_values(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]

    models: list[str] = []
    for item in raw:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                models.append(part)
    return models


def _validate_endpoint_model_request(models, requested_mode, loras, action: str) -> None:
    if loras and len(models) > 1:
        _die(f"--lora can only be used with a single base --model for `{action}`")

    if requested_mode == "multi-lora" and not loras:
        _die(f"--mode multi-lora requires at least one --lora for `{action}`")


def _endpoint_serving_mode(models, requested_mode, loras, action: str) -> str:
    if requested_mode is None:
        if loras:
            mode = "multi-lora"
        elif len(models) > 1:
            _die(f"`{action}` with multiple models requires --mode (co-located or multi-lora)")
        else:
            mode = "single"
    else:
        mode = requested_mode

    if mode == "single" and len(models) > 1:
        _die(f"--mode single is incompatible with multiple --model values for `{action}`")

    return mode


def _print_readiness_report(readiness: dict) -> None:
    """Print a human-readable Gateway readiness report."""
    ready = bool(readiness.get("ready"))
    status_marker = "✓" if ready else "✗"
    status_text = "Gateway is ready" if ready else "Gateway is not ready"
    print(f"{status_marker} {status_text}")

    checks = readiness.get("checks", {})
    for name, result in checks.items():
        text = str(result)
        marker = "✓" if text.startswith("ok") else "✗"
        print(f"  {marker} {name}: {text}")

    message = readiness.get("message")
    if message:
        print(f"  {message}")


def active_source_view(args: Namespace | None = None) -> tuple[Config, Source | None]:
    """``(config, public_source)`` describing the source a command acts on.

    ``get_config()`` overlays only an active PLATFORM source; a public one is
    applied per command (``r4 chat``: ``use_source``). Read-only views
    (``config show``, ``health``) use this so an active public source is shown
    as itself, not as the localhost platform default (K1, 2026-10-21).
    ``public_source`` is None when the active source is a platform source or
    none is active; ``config`` is then ``get_config()`` unchanged.
    """
    from ..config import get_config
    from ..source import resolve_active_source
    from ..source_context import config_for_source

    config = get_config()
    try:
        source = resolve_active_source(getattr(args, "source", None))
    except Exception:  # noqa: BLE001 — a broken source registry never blocks a view
        return config, None
    if source is None or source.kind == "platform":
        return config, None
    return config_for_source(source, config), source


def _print_endpoint_target(active_config: Config) -> None:
    """The Endpoint (public) or Gateway line(s) of the active context."""
    url = active_config.endpoint_url
    provider = active_config.endpoint_provider
    if provider and not url:
        print(f"  Endpoint: {provider} (public)")
    elif url:
        print(f"  Endpoint: {url} (public)")
        if provider:
            print(f"  Provider: {provider}")
    else:
        from ..source_locality import display_url

        print(f"  Gateway: {display_url(active_config.gateway_url)}")
    if (url or provider) and active_config.aws_region:
        print(f"  Region:   {active_config.aws_region}")


def _context_has_auth(active_config: Config) -> bool:
    """A public endpoint counts its own key; the platform its login or api_key."""
    from ..auth import get_auth_manager

    if active_config.endpoint_url or active_config.endpoint_provider:
        if active_config.endpoint_api_key:
            return True
    if get_auth_manager(active_config.profile).is_authenticated():
        return True
    return bool(getattr(active_config, "api_key", None))


def _print_active_context(config=None, *, heading: str = "Active context") -> None:
    """Print the runtime context used by R4 commands."""
    from ..config import get_config
    from ..model_routing import active_model_route_label

    active_config = config or get_config()

    print(f"{heading}:")
    print(f"  Profile: {active_config.profile}")
    print(f"  Project: {active_config.project_id or '(not set)'}")
    _print_endpoint_target(active_config)
    print(f"  Model: {active_config.model} ({active_model_route_label(active_config)})")
    has_auth = _context_has_auth(active_config)
    print(f"  Auth: {'configured' if has_auth else '(not set)'}")


def _try_zmq_session_bootstrap(jwt_token: str, gateway_url: str | None = None) -> None:
    """Eagerly bootstrap a ZMQ session-CURVE credential after OIDC login.

    This is UX polish: instead of discovering ZMQ failures on the first
    real operation, we register the ephemeral CURVE session immediately
    so the user gets fast feedback.  Failures are non-fatal (warnings).
    """
    import os

    deploy_mode = os.environ.get("R4_DEPLOY_MODE", "").lower()
    transport_mode = os.environ.get("R4_TRANSPORT_MODE", "").lower()

    # Only relevant when ZMQ transport is possible.
    if deploy_mode == "cloud" or transport_mode == "gateway":
        return

    try:
        from ..curve_session import CurveSessionManager

        # The deployment the user just logged in to — before 2026-10-21 the
        # fresh OIDC token was POSTed to UMRS_BASE_URL or 127.0.0.1:8082.
        mgr = CurveSessionManager(umrs_base_url=gateway_url or None)
        session = mgr.ensure_session(jwt_token)
        if session:
            print("✓ ZMQ session established")
    except Exception as exc:
        print(f"⚠ ZMQ session bootstrap skipped: {exc}")


def _configure_profile_auth(
    profile: str,
    *,
    api_key: str | None = None,
    oidc: bool = False,
    timeout: int = 300,
    prompt_if_missing: bool = False,
) -> bool:
    """Configure or reuse profile authentication."""
    import os

    from ..auth import get_auth_manager

    auth = get_auth_manager(profile)

    if api_key:
        auth.save_api_key(api_key)
        print(f"✓ Saved API key for profile: {profile}")
        return True

    if oidc or os.environ.get("R4_OIDC_ISSUER"):
        return _configure_profile_auth_oidc(profile, auth, timeout=timeout)

    if prompt_if_missing and not auth.is_authenticated():
        import getpass

        entered_key = getpass.getpass("Enter API key (leave blank to skip): ")
        if entered_key:
            auth.save_api_key(entered_key)
            print(f"✓ Saved API key for profile: {profile}")

    return auth.is_authenticated()


def _configure_profile_auth_oidc(profile: str, auth, *, timeout: int) -> bool:
    """OIDC branch of ``_configure_profile_auth``."""
    try:
        from ..config import ProfileConfigStore
        from ..oidc import OIDCConfig, oidc_login

        # Load OIDC settings from profile store as fallbacks
        stored = ProfileConfigStore().load(profile)
        stored_issuer = str(stored["oidc_issuer"]) if stored.get("oidc_issuer") else None
        stored_client_id = str(stored["oidc_client_id"]) if stored.get("oidc_client_id") else None
        stored_gateway_url = str(stored["gateway_url"]) if stored.get("gateway_url") else None
        stored_scope = str(stored["oidc_scope"]) if stored.get("oidc_scope") else None

        config = OIDCConfig.from_env(
            issuer=stored_issuer,
            client_id=stored_client_id,
            scope=stored_scope,
        )
        result = oidc_login(
            auth,
            config=config,
            timeout=timeout,
            gateway_url=stored_gateway_url,
        )
        if result.success and result.token:
            auth.api_key_manager.delete_api_key(profile)
            ProfileConfigStore().save(profile, api_key=None)
            from ..source import clear_platform_source_api_keys

            clear_platform_source_api_keys(gateway_url=stored_gateway_url)
            _try_zmq_session_bootstrap(result.token.access_token, stored_gateway_url)
        return result.success
    except ImportError:
        print("✗ OIDC support requires httpx: pip install httpx", file=sys.stderr)
        return False
    except Exception as e:
        print(f"✗ OIDC login failed: {e}", file=sys.stderr)
        return False


def _probe_umrs_direct() -> None:
    """Probe UMRS directly for health status."""
    import os

    umrs_url = os.environ.get("UMRS_BASE_URL", "http://localhost:8000")
    print(f"\nDirect UMRS probe ({umrs_url}):")

    try:
        import urllib.request

        req = urllib.request.Request(f"{umrs_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode()
        print(f"  ✓ UMRS healthy: {data[:200]}")
    except Exception as e:
        print(f"  ✗ UMRS unreachable: {e}")


def _get_current_session():
    """Get the current teamup session or die."""
    from ..teamup import TeamupSession

    session = TeamupSession.get_current()
    if not session:
        _die(
            "No active teamup session",
            action="teamup",
            hint="create one with `r4 teamup create <name>`",
        )
    return session


def _parse_lookback(spec: str) -> datetime:
    """Parse a lookback specification like '24h', '7d', '1h' into a datetime."""
    import re
    from datetime import timedelta, timezone

    m = re.fullmatch(r"(\d+)\s*([hHdDmM])", spec.strip())
    if not m:
        _die(
            f"Invalid lookback spec: {spec!r}",
            hint="Use a format like 1h, 24h, 7d, or 30m",
        )
    amount = int(m.group(1))
    unit = m.group(2).lower()
    delta = {
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
        "m": timedelta(minutes=amount),
    }[unit]
    return datetime.now(timezone.utc) - delta
