"""``r4 project data-class`` — read or change a project's data class (ADR-062).

    r4 project data-class get [project]
    r4 project data-class set [project] sensitive|non_sensitive [--confirm "P C"]

The class decides which inference providers the project's sessions and
jobs may use (ADR-062 + the 2026-10-15 amendment):

- ``sensitive`` (the site default): platform models, institutional
  providers (the site's allowlist — Rikyu by default) and loopback
  models only.
- ``non_sensitive`` (the stand-alone default): public and commercial
  providers are admitted too.

Owner or admin only (the server enforces and records it). WIDENING
sensitive → non_sensitive is the explicit, attributable act ADR-062
requires: a typed confirmation naming the project and the class the
change allows — ``--confirm "myproj non_sensitive"``, or the same line
typed at an interactive prompt. Narrowing needs no confirmation.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from typing import Any

VALID_CLASSES = ("sensitive", "non_sensitive")

_ALLOWED = {
    "sensitive": (
        "platform models; institutional providers (the site's allowlist, "
        "Rikyu by default); loopback models"
    ),
    "non_sensitive": "all of the above, plus public/commercial providers (your own key)",
}


_DESCRIPTION = """\
Show or change a project's data class. The class decides which inference
providers the project's sessions and jobs may use:

  sensitive      platform models, institutional providers (the site's
                 allowlist, Rikyu by default) and models on this machine
  non_sensitive  all of the above, plus public and commercial providers

Only the project owner or an admin may change it. Widening (sensitive to
non_sensitive) needs a typed confirmation naming the project and the class;
narrowing does not."""

_EXAMPLES = """\
examples:
  r4 project data-class get
  r4 project data-class get myproj --json
  r4 project data-class set myproj sensitive
  r4 project data-class set myproj non_sensitive --confirm 'myproj non_sensitive'"""


def add_data_class_parser(sub: Any) -> None:
    """Register ``r4 project data-class`` (``data_class`` an alias, listed once — as ask_policy)."""
    from argparse import RawDescriptionHelpFormatter

    parser = sub.add_parser(
        "data-class",
        aliases=["data_class"],
        help="Show or change the project's data class",
        description=_DESCRIPTION,
        epilog=_EXAMPLES,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "data_class_action",
        choices=("get", "set"),
        help="get: show the class; set: change it",
    )
    parser.add_argument(
        "project_id",
        nargs="?",
        default=None,
        metavar="project",
        help="Project ID (defaults to the current project)",
    )
    parser.add_argument(
        "new_class",
        nargs="?",
        default=None,
        metavar="class",
        help=f"With set: {' | '.join(VALID_CLASSES)}",
    )
    parser.add_argument(
        "--confirm",
        metavar="'PROJECT CLASS'",
        help="Widening: the typed confirmation, e.g. --confirm 'myproj non_sensitive'",
    )
    parser.add_argument(
        "--json", "-j", action="store_true", dest="json", help="Print one JSON document"
    )


# -- helpers -----------------------------------------------------------------


def _project_id(args: Namespace, pm: Any) -> str:
    from ..source_context import selected_project

    # Under --source on another deployment, only that source's own project
    # (onboarding plan S1): never the active deployment's switched project.
    project_id = getattr(args, "project_id", None) or selected_project(config_fallback=False)
    if not project_id:
        from ._helpers import _die

        _die(
            "No project specified. Pass <project-id> or run: r4 project switch <id>",
            error_id="project.data_class.no_project",
        )
    return str(project_id)


def _split_set_args(args: Namespace) -> tuple[str | None, str | None]:
    """(project_id, new_class); ``set non_sensitive`` aims at the current project."""
    project_id = getattr(args, "project_id", None)
    new_class = getattr(args, "new_class", None)
    if new_class is None and project_id in VALID_CLASSES:
        return None, project_id
    return project_id, new_class


def _class_of(client: Any, project_id: str) -> str:
    """The row's class via the server envelope; '' when the row hides it."""
    reply = client.get_platform_project(project_id)
    project = reply.get("project") if isinstance(reply, dict) else None
    return str(project.get("data_class", "") or "") if isinstance(project, dict) else ""


def _confirm_token(project_id: str, new_class: str) -> str:
    """The exact line a widening must be confirmed with."""
    return f"{project_id} {new_class}"


def _read_confirmation(args: Namespace, project_id: str, current: str, new_class: str) -> str:
    """The --confirm value, or the interactively typed line (TTY only).

    The warning and the prompt go to stderr: stdout carries only the
    command's result (one JSON document under --json).
    """
    from ._helpers import _die

    token = _confirm_token(project_id, new_class)
    print(
        f"Widening project '{project_id}' from {current or 'unknown'} to {new_class} "
        f"admits PUBLIC and commercial inference providers for its sessions and "
        f"jobs — research data may leave the site toward those providers, under "
        f"the provider key you hold. This act is recorded with your identity.",
        file=sys.stderr,
    )
    provided = getattr(args, "confirm", None)
    if provided is not None:
        return str(provided)
    if sys.stdin.isatty():
        print(f"Type '{token}' to confirm: ", end="", file=sys.stderr, flush=True)
        return input().strip()
    _die(
        f"Refusing to widen '{project_id}' to {new_class} without the typed "
        f"confirmation. Re-run with: --confirm '{token}'",
        error_id="project.data_class.confirm_required",
    )
    return ""  # unreachable


def _print_get(project_id: str, data_class: str) -> None:
    cls = data_class or "unknown"
    print(f"{project_id}: data_class={cls}")
    if data_class in _ALLOWED:
        other = "non_sensitive" if data_class == "sensitive" else "sensitive"
        print(f"  inference allowed: {_ALLOWED[data_class]}")
        print(f"  change: r4 project data-class set {project_id} {other}")


def _print_unchanged(project_id: str, data_class: str, args: Namespace) -> None:
    """A no-op set: nothing was written (one JSON document under --json)."""
    if getattr(args, "json", False):
        reply = {"project_id": project_id, "data_class": data_class, "changed": False}
        print(json.dumps(reply, indent=2))
        return
    print(f"{project_id}: already data_class={data_class} — nothing to do")


def _handle_get(client: Any, project_id: str, args: Namespace) -> None:
    data_class = _class_of(client, project_id)
    if getattr(args, "json", False):
        print(json.dumps({"project_id": project_id, "data_class": data_class}, indent=2))
        return
    _print_get(project_id, data_class)


def _handle_set(client: Any, project_id: str, new_class: str, args: Namespace) -> None:
    from ._helpers import _die

    if new_class not in VALID_CLASSES:
        _die(
            f"invalid data class {new_class!r} — expected one of: {', '.join(VALID_CLASSES)}",
            error_id="project.data_class.invalid",
        )
    current = _class_of(client, project_id)
    if current == new_class:
        _print_unchanged(project_id, new_class, args)
        return
    if new_class == "non_sensitive":  # the only widening of two classes
        token = _confirm_token(project_id, new_class)
        if _read_confirmation(args, project_id, current, new_class).strip() != token:
            _die(
                f"Confirmation did not match — expected exactly: {token}",
                error_id="project.data_class.confirm_mismatch",
            )
    reply = client.set_project_data_class(project_id, new_class)
    if getattr(args, "json", False):
        print(json.dumps(reply, indent=2, default=str))
        return
    print(f"{project_id}: data_class {current or 'unknown'} → {new_class}")
    print(f"  inference allowed: {_ALLOWED[new_class]}")


def project_data_class(
    args: Namespace,
    pm: Any,
    _active_profile: Any = None,
    _reload_config: Any = None,
    _project_config: Any = None,
) -> None:
    """``r4 project data-class get|set [project] [class]``."""
    from ..client import Client
    from ._helpers import _die

    if getattr(args, "data_class_action", "get") == "set":
        given_project, new_class = _split_set_args(args)
        args.project_id = given_project
        project_id = _project_id(args, pm)
        if not new_class:
            _die(
                "set needs a class: r4 project data-class set [project] "
                f"{' | '.join(VALID_CLASSES)}",
                error_id="project.data_class.missing_class",
            )
            return
        try:
            _handle_set(Client(project_id=project_id), project_id, str(new_class), args)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 — named CLI failure
            _die(
                f"project data-class set failed: {exc}",
                action="project.data_class.set",
                error_id="project.data_class.set.failed",
            )
        return
    project_id = _project_id(args, pm)
    try:
        _handle_get(Client(project_id=project_id), project_id, args)
    except Exception as exc:  # noqa: BLE001 — named CLI failure
        _die(
            f"project data-class get failed: {exc}",
            action="project.data_class.get",
            error_id="project.data_class.get.failed",
        )


__all__ = [
    "VALID_CLASSES",
    "add_data_class_parser",
    "project_data_class",
]
