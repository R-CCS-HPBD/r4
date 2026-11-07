"""The session-open web-access question for the CLI (ADR-099 D-2).

``r4 session start``, ``r4 team plan`` (both venues), ``r4 teamup``,
``r4 recipe run``, the Rich REPL and ``r4 -p`` call :func:`choose_web_access`.
The order:

1. the ``--web`` flag (with ``--web-domain``);
2. on a terminal: the question, preselecting ``R4_WEB_ACCESS``, else the
   session's own earlier choice, else the remembered last one (RS-1 fix W5:
   an ambient env var preselects, it does not answer for the user);
3. without one: ``R4_WEB_ACCESS`` (``MODE[:d1,d2]``), else the session's own
   choice (a restart keeps it, RS-1 fix W2), else the remembered last choice;
   the caller prints one notice naming where it came from;
4. else a local session runs with web access off and says so, and a remote
   start is refused: ``choose web access: --web off|allowlist|public``.

Every result is checked against the server's caps (``limits``, the preview
route's effective limits) when the caller has them.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from ..companion.web_access import (
    WebAccess,
    WebAccessError,
    allowed_modes,
    check_within_caps,
    make_web_access,
    remember_web_access,
    remembered_web_access,
    remote_start_choice,
    suggested_domains,
    take_launch_web_access,
    venue_policy_preview,
    web_access_from_env,
)

NO_CHOICE_REMOTE = "choose web access: --web off|allowlist|public"
_ASK_ATTEMPTS = 3

_CHOICE_TEXT = {
    "none": "off        the agent's web tools refuse every call",
    "allowlist": "allowlist  only the domains you list (e.g. .arxiv.org, pypi.org)",
    "public": "public     any web host",
}
_LOCAL_NOTE = (
    "Shell, Python and MCP tools are not filtered locally: they use your network "
    "after you approve them."
)
#: ADR-101 D-6: with the sandbox on, the shell and Python reach the web only
#: through the sandbox proxy, which follows this choice.
_LOCAL_NOTE_SANDBOXED = (
    "The sandbox is on: shell and Python traffic goes through the sandbox proxy "
    "with this choice; local MCP servers use their own sandbox web setting."
)
_REMOTE_NOTE = "The sandbox's proxy filters all of the session's traffic."


def is_interactive() -> bool:
    """True when both ends are a terminal (the question can be asked)."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _err(line: str) -> None:
    print(line, file=sys.stderr)


def _checked(access: WebAccess, limits: Mapping[str, Any] | None) -> WebAccess:
    check_within_caps(access, limits)
    return access


def _session_choice(current: WebAccess | None) -> WebAccess | None:
    """The session's own earlier choice, marked as reused (``source=session``).

    A local ``default_off`` is no choice (D-2, the rule of
    :func:`remote_start_choice`): reusing it promoted an ``r4 -p`` run nobody
    chose for to "this session's choice" (RS-1 round-3 fix N2)."""
    chosen = remote_start_choice(current)
    return replace(chosen, source="session") if chosen is not None else None


def _known_choices(
    env: Mapping[str, str] | None, current: WebAccess | None, remembered: WebAccess | None
) -> list[WebAccess]:
    """The choices known without asking, first wins: ``R4_WEB_ACCESS``, the
    session's own, the remembered one (an invalid env value raises)."""
    candidates = (web_access_from_env(_env(env)), _session_choice(current), remembered)
    return [c for c in candidates if c is not None and c.chosen]


def choose_web_access(
    *,
    flag: str | None,
    domains: Sequence[str],
    remote: bool,
    limits: Mapping[str, Any] | None,
    interactive: bool,
    remembered: WebAccess | None,
    input_fn: Callable[[str], str] = input,
    env: Mapping[str, str] | None = None,
    current: WebAccess | None = None,
) -> WebAccess:
    """The session's web access (order in the module docstring).

    ``current`` is the session's own choice when it has one (a restart).
    Raises :class:`WebAccessError` for an invalid or disallowed choice, and
    for a remote start with no choice at all.
    """
    if flag:
        return _checked(make_web_access(flag, list(domains), source="flag"), limits)
    known = _known_choices(env, current, remembered)
    if interactive:
        preselect = known[0] if known else None
        return ask_web_access(remote=remote, limits=limits, remembered=preselect, input_fn=input_fn)
    if known:
        return _checked(known[0], limits)
    if remote:
        raise WebAccessError(NO_CHOICE_REMOTE)
    return make_web_access("none", source="default_off")


def remember_choice(access: WebAccess, config: Any) -> None:
    """Record an answered question as the profile's last choice (D-2)."""
    if access.source != "prompt":
        return
    try:
        remember_web_access(access, profile=str(getattr(config, "profile", "") or "default"))
    except OSError as exc:  # a convenience: the start goes on with the answer
        _err(f"note: could not remember the web access choice ({exc})")


_NOTICES = {
    "remembered": "web access: {label} (your last choice; change it with --web)",
    "session": "web access: {label} (this session's choice; change it with --web)",
    "env": "web access: {label} (from R4_WEB_ACCESS; change it with --web)",
    "default_off": "web access: off (no choice given; use --web or R4_WEB_ACCESS)",
}


def notice_for(access: WebAccess) -> str | None:
    """The one line a start prints for a choice it did not ask (RS-1 fix W5:
    ``R4_WEB_ACCESS`` is named too, never applied silently)."""
    template = _NOTICES.get(access.source)
    return template.format(label=access.label()) if template else None


def _preview_limits(client: Any, preview_url: str, project: str) -> Mapping[str, Any] | None:
    preview, error = venue_policy_preview(client, preview_url, project)
    if error:
        _err(f"note: the session policy preview failed ({error}); the server checks the choice")
    limits = (preview or {}).get("limits")
    return limits if isinstance(limits, Mapping) else None


def remote_web_access(
    *,
    flag: str | None,
    domains: Sequence[str],
    client: Any,
    project: str,
    preview_url: str = "",
    interactive: bool | None = None,
    input_fn: Callable[[str], str] = input,
    current: WebAccess | None = None,
) -> WebAccess:
    """The web choice for a remote start (``r4 session start``, ``r4 team plan``).

    On a terminal without ``--web`` the question offers what the session
    policy preview allows (when ``preview_url`` is given). ``current`` is the
    session's own choice: kept without a flag or ``R4_WEB_ACCESS``, and
    preselected in the question (RS-1 fix W2). Prints the notice line for a
    choice it did not ask; remembers an answered question.
    """
    asking = is_interactive() if interactive is None else interactive
    limits = None
    if asking and not flag and preview_url:
        limits = _preview_limits(client, preview_url, project)
    config = getattr(client, "config", None)
    access = choose_web_access(
        flag=flag,
        domains=domains,
        remote=True,
        limits=limits,
        interactive=asking,
        remembered=remembered_web_access(config),
        input_fn=input_fn,
        current=current,
    )
    remember_choice(access, config)
    note = notice_for(access)
    if note:
        _err(note)
    return access


def local_web_access(config: Any, *, env: Mapping[str, str] | None = None) -> WebAccess:
    """A non-interactive local session's choice (``r4 -p``): ``R4_WEB_ACCESS``
    (``r4 --web`` sets it), the remembered one, else off, with its notice."""
    access = choose_web_access(
        flag=None,
        domains=(),
        remote=False,
        limits=None,
        interactive=False,
        remembered=remembered_web_access(config),
        env=env,
    )
    note = notice_for(access)
    if note:
        _err(note)
    return access


def apply_local_web_access(
    session: Any,
    config: Any,
    *,
    env: Mapping[str, str] | None = None,
    launch_env: MutableMapping[str, str] | None = None,
) -> WebAccess:
    """Set a non-interactive local session's choice (``r4 -p``).

    The ``r4 --web`` flag (handed over in ``R4_LAUNCH_WEB_ACCESS``, taken
    out of the environment here) and an explicit ``R4_WEB_ACCESS`` (with
    its notice) apply; otherwise a resumed session keeps its own choice and
    a session without one gets :func:`local_web_access`.
    """
    flag = take_launch_web_access(launch_env)
    current = WebAccess.from_metadata(session.metadata)
    if flag is None and current.chosen and web_access_from_env(_env(env)) is None:
        return current
    access = flag or local_web_access(config, env=env)
    session.metadata["web_access"] = access.to_metadata()
    return access


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def cli_web_flag(
    args: Any, flag_attr: str = "", domains_attr: str = ""
) -> tuple[str | None, list[str]]:
    """``--web`` and its domains for a command: the command's own flag
    (``flag_attr``), else the root ``r4 --web`` (RS-1 round-2 fix W2-2: the
    root flag was read only by the console launch)."""
    own = getattr(args, flag_attr, None) if flag_attr else None
    if own:
        return str(own), list(getattr(args, domains_attr, None) or [])
    root = getattr(args, "root_web", None)
    return (str(root) if root else None), list(getattr(args, "root_web_domains", None) or [])


def cli_agent_web_access(
    args: Any,
    config: Any,
    *,
    flag_attr: str = "",
    domains_attr: str = "",
    interactive: bool | None = None,
    input_fn: Callable[[str], str] = input,
    env: Mapping[str, str] | None = None,
) -> WebAccess:
    """The web access of a local command-line run whose agents have web
    tools (``r4 team plan`` on the local venue, ``r4 teamup create --start``,
    ``run`` and ``launch``, ``r4 recipe run``; ADR-099 D-2, RS-1 round-2
    fix W2-2).

    ``--web`` (the command's own, else ``r4 --web``) answers; on a terminal
    the question is asked (``R4_WEB_ACCESS``, then the remembered choice,
    preselected); without one ``R4_WEB_ACCESS``, else the remembered choice,
    else off, each with its notice line. An answered question is remembered.
    Raises :class:`WebAccessError` for an invalid choice or a closed input.
    """
    flag, domains = cli_web_flag(args, flag_attr, domains_attr)
    access = choose_web_access(
        flag=flag,
        domains=domains,
        remote=False,
        limits=None,
        interactive=is_interactive() if interactive is None else interactive,
        remembered=remembered_web_access(config),
        input_fn=input_fn,
        env=env,
    )
    remember_choice(access, config)
    note = notice_for(access)
    if note:
        _err(note)
    return access


def cli_agent_web_getter(args: Any, config: Any, **kwargs: Any) -> Callable[[], WebAccess]:
    """:func:`cli_agent_web_access` as the accessor a ``TeamService``, a
    teamup session or the companion tools take; exits 2 on an invalid choice."""
    try:
        access = cli_agent_web_access(args, config, **kwargs)
    except WebAccessError as exc:
        _err(f"--web: {exc}")
        sys.exit(2)
    return lambda: access


def apply_repl_web_access(
    session: Any,
    config: Any,
    *,
    flag: WebAccess | None,
    interactive: bool | None = None,
    input_fn: Callable[[str], str] = input,
    env: Mapping[str, str] | None = None,
) -> WebAccess:
    """The Rich REPL's session choice, kept in the session metadata (RS-1
    round-2 fix W2-3: ``r4 --web`` was exported as ``R4_WEB_ACCESS``, which
    the agent's shells inherited, and an ambient ``R4_WEB_ACCESS`` answered
    unasked).

    ``r4 --web`` answers; a resumed session keeps its own choice; otherwise
    the question on a terminal (``R4_WEB_ACCESS`` and the remembered choice
    preselect), else the non-interactive order with its notice line.
    """
    current = WebAccess.from_metadata(session.metadata)
    # A local ``default_off`` (an ``r4 -p`` run nobody chose for) is no
    # choice: the REPL asks (RS-1 round 4, web R4-3; round-3 N2's rule).
    if flag is None and remote_start_choice(current) is not None:
        return current
    access = flag or cli_agent_web_access(
        None, config, interactive=interactive, input_fn=input_fn, env=env
    )
    session.metadata["web_access"] = access.to_metadata()
    return access


# ── the question ───────────────────────────────────────────────────────────


def _question_lines(modes: Sequence[str], remote: bool) -> list[str]:
    lines = ["Web access for the agent in this session:"]
    lines += [f"  {i}. {_CHOICE_TEXT[m]}" for i, m in enumerate(modes, 1)]
    lines.append(_REMOTE_NOTE if remote else local_note())
    return lines


def local_note(sandboxed: bool | None = None) -> str:
    """The question's local note: what this choice filters (ADR-101 D-6).

    ``sandboxed`` None reads the current session's sandbox."""
    if sandboxed is None:
        sandboxed = _sandbox_on()
    return _LOCAL_NOTE_SANDBOXED if sandboxed else _LOCAL_NOTE


def _sandbox_on() -> bool:
    try:
        from ..companion.sandbox import current_sandbox

        return current_sandbox().setting.enabled
    except Exception:  # noqa: BLE001 — an unreadable setting reads as off (today's note)
        return False


def _preselected(modes: Sequence[str], remembered: WebAccess | None) -> str:
    if remembered is not None and remembered.mode in modes:
        return remembered.mode
    return ""


def _mode_answer(answer: str, modes: Sequence[str], default: str) -> str:
    """A number, a mode name (``off`` too) or Enter for the preselection."""
    text = answer.strip().lower()
    if not text:
        return default
    if text.isdigit() and 1 <= int(text) <= len(modes):
        return modes[int(text) - 1]
    text = "none" if text == "off" else text
    return text if text in modes else ""


def _domain_prefill(limits: Mapping[str, Any] | None, remembered: WebAccess | None) -> str:
    if remembered is not None and remembered.domains:
        return ",".join(remembered.domains)
    return ",".join(suggested_domains(limits))


def _ask_mode(modes: Sequence[str], default: str, input_fn: Callable[[str], str]) -> str:
    hint = f" (Enter = {'off' if default == 'none' else default})" if default else ""
    for _ in range(_ASK_ATTEMPTS):
        mode = _mode_answer(input_fn(f"Choose 1-{len(modes)}{hint}: "), modes, default)
        if mode:
            return mode
        _err(f"choose one of 1-{len(modes)}")
    raise WebAccessError("no web access chosen")


def _ask_allowlist(
    limits: Mapping[str, Any] | None, prefill: str, input_fn: Callable[[str], str]
) -> WebAccess:
    hint = f" [{prefill}]" if prefill else ""
    for _ in range(_ASK_ATTEMPTS):
        text = input_fn(f"Domains, comma-separated (.example.org = with subdomains){hint}: ")
        try:
            return _checked(
                make_web_access("allowlist", text.strip() or prefill, source="prompt"), limits
            )
        except WebAccessError as exc:
            _err(str(exc))
    raise WebAccessError("no allowlist domains given")


def ask_web_access(
    *,
    remote: bool,
    limits: Mapping[str, Any] | None,
    remembered: WebAccess | None,
    input_fn: Callable[[str], str] = input,
) -> WebAccess:
    """Ask on the terminal; only the modes the caps allow are offered."""
    modes = list(allowed_modes(limits))
    if not modes:
        raise WebAccessError("the session policy allows no web access mode")
    for line in _question_lines(modes, remote):
        _err(line)
    try:
        mode = _ask_mode(modes, _preselected(modes, remembered), input_fn)
        if mode == "allowlist":
            return _ask_allowlist(limits, _domain_prefill(limits, remembered), input_fn)
    except EOFError as exc:
        raise WebAccessError("no web access chosen (input closed)") from exc
    return make_web_access(mode, source="prompt")
