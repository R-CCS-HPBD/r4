"""Companion UI — frontend entry-point for the research companion.

The JavaScript (Ink) TUI has been removed (ADR-014, 2026-09-11 deletion leg).
The interactive surface is Console v3 (OpenTUI). When the console is
unavailable, or when ``force_repl=True``, a Rich-based Python REPL is
auto-started as a fallback."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def _cli_flag_value(flag: str) -> str | None:
    """Read a companion CLI flag from ``sys.argv`` when not passed explicitly."""
    argv = sys.argv[1:]
    prefix = f"{flag}="
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def launch(
    *,
    client,
    session_id: str | None = None,
    force_repl: bool = False,
    model: str | None = None,
    skill: str | None = None,
    system: str | None = None,
    source_name: str | None = None,
    ui: str | None = None,
    prompt: str | None = None,
    secure: bool = False,
    fs_root: str | None = None,
    allow_all_tools: bool = False,
    mode: str | None = None,
    **kwargs,
) -> None:
    """Launch the companion interactive UI.

    Post-Ink-deletion (ADR-014, 2026-09-11): the interactive surface is
    Console v3 (OpenTUI) when available. ``force_repl=True`` or
    ``--legacy-tui`` picks the Rich REPL; the R4_COMPANION_TUI=ink env knob
    no longer has meaning (Ink was deleted).
    """
    if prompt and prompt.strip() and not force_repl:
        _launch_headless(
            client=client,
            prompt=prompt.strip(),
            session_id=session_id,
            model=model,
            skill=skill,
            system=system,
        )
        return

    if force_repl:
        logger.info("Launching REPL (force_repl=True)")
        _launch_repl(
            client=client,
            session_id=session_id,
            model=model,
            skill=skill,
            system=system,
        )
        return

    # Ink TUI deleted (ADR-014): the only interactive surfaces are Console v3
    # (OpenTUI), or REPL when it is unavailable.
    if _should_launch_v3(kwargs.get("legacy_tui", False)):
        if _launch_v3_console() is not None:
            return
        logger.warning("v3 console launch failed")

    logger.warning("falling back to REPL")
    _launch_repl(
        client=client,
        session_id=session_id,
        model=model,
        skill=skill,
        system=system,
    )


def _should_launch_v3(legacy_tui: bool) -> bool:
    """Surface decision: v3 (OpenTUI) availability; legacy flag keeps REPL.

    Ink is gone (ADR-014), so `--legacy-tui` no longer selects Ink; it makes
    the companion fall through to the Rich REPL instead. R4_COMPANION_TUI=ink
    support removed with it (unknown values are ignored).
    """
    if legacy_tui:
        return False
    try:
        from ..._cli.console import console_available

        return console_available()
    except Exception:  # pragma: no cover - probe must never break launch
        logger.debug("console_available probe failed", exc_info=True)
        return False


def _launch_v3_console() -> int | None:
    """Launch Console v3 via the existing console command path.

    Returns the exit code, or None when the launch failed before exec so the
    caller can fall back to the Ink TUI.
    """
    try:
        from argparse import Namespace

        from ..._cli.console import cmd_console

        return cmd_console(Namespace(demo=False, offline=False, backend=None))
    except Exception:  # noqa: BLE001 - fall back rather than crash the UI
        logger.exception("v3 console launch failed")
        return None


def _launch_headless(
    *,
    client,
    prompt: str,
    session_id: str | None,
    model: str | None,
    skill: str | None,
    system: str | None,
) -> None:
    """Run a single headless Companion turn (``r4 -p`` golden path)."""
    from ..session_store import CompanionSessionStore
    from .repl import open_cli_session

    session = open_cli_session(
        client, name="Headless", session_id=session_id, model=model, skill=skill, system=system
    )
    # ADR-101 D-1/D-2: r4 --sandbox (R4_LAUNCH_SANDBOX), the session's own
    # choice, R4_SANDBOX, the profile default, else off, with one notice line.
    # ADR-099 D-2: no question here — r4 --web (R4_LAUNCH_WEB_ACCESS),
    # R4_WEB_ACCESS, the remembered choice, else off, with a notice line.
    from ..._cli.sandbox_prompt import apply_local_sandbox
    from ..._cli.web_access_prompt import apply_local_web_access

    apply_local_sandbox(session)
    apply_local_web_access(session, client.config)
    response = session.send(prompt)
    print(response)
    CompanionSessionStore().save(session)


def _launch_repl(
    client,
    session_id: str | None,
    model: str | None,
    skill: str | None,
    system: str | None,
) -> None:
    from ..web_access import take_launch_web_access
    from .repl import launch_repl

    # ADR-099 D-2 (RS-1 round-2 fix W2-3): r4 --web answers the REPL's
    # question and stays in this process (taken out of the environment, kept
    # in the session metadata), never exported to the agent's shells.
    launch_repl(
        client=client,
        session_id=session_id,
        model=model,
        skill=skill,
        system=system,
        web_flag=take_launch_web_access(),
    )


def _companion_js_cmd(*_: object, **__: object) -> None:
    """Command composer for the removed Ink launcher — REMOVED with it
    (ADR-014 deletion leg, 2026-09-11)."""
    raise RuntimeError("Ink JS TUI was removed 2026-09-11 (ADR-014)")


def _launch_js_ui(
    **_: object,
) -> None:
    """Ink TUI launcher — REMOVED (ADR-014 deletion leg, 2026-09-11).

    Kept only as an error truth for stale call sites; the Ink tree no longer
    exists in the repo.
    """
    raise RuntimeError(
        "the Ink JS TUI was removed 2026-09-11 (ADR-014); use the OpenTUI console "
        "(`r4 console`, install via `r4 tui install`) or force the Rich REPL."
    )
