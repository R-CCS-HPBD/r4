"""``r4 session`` command handler."""

from __future__ import annotations

import json as _json
import shutil
import sys
from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import session_bus_cli
from .base import CommandHandler
from .registry import command

SESSION_KINDS = ("agent", "companion", "teamup")


@command
class SessionsCommand(CommandHandler):
    """``r4 session`` — inspect and manage local R4 sessions."""

    name = "sessions"
    help = "List, show, archive, and remove local R4 sessions"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser("session", help=self.help)
        parser.add_argument(
            "session_subcommand",
            nargs="?",
            default="list",
            choices=[
                "list",
                "show",
                "archive",
                "remove",
                "delete",
                "conclude",
                "pull",
                "reconcile",
                "fork",
                "start",
                "venue",
                "stop",
                "questions",
                "answer",
                "live",
                "send",
            ],
            metavar="subcommand",
            help="Subcommand (default: list)",
        )
        parser.add_argument("session_id", nargs="?", help="Session ID for show/archive/remove")
        # ADR-099 D-8: `r4 session answer <sid> <question_id> <text…>`.
        parser.add_argument(
            "answer_args",
            nargs="*",
            metavar="question_id text",
            help="answer: the question id, then the answer text; send: the message text",
        )
        parser.add_argument(
            "--kind",
            choices=["all", *SESSION_KINDS],
            default="all",
            help="Filter by session kind (default: all)",
        )
        parser.add_argument("--endpoint", help="Filter sessions by endpoint or instance ID")
        parser.add_argument("--project", help="Filter sessions by project ID")
        parser.add_argument(
            "--venue",
            help="Filter by venue csv: local, frontend, cluster:<name> (companion rows)",
        )
        parser.add_argument(
            "--state",
            help=(
                "Filter by state csv: ongoing, stopped, completed, error, archived "
                "(archived requires --include-archived)"
            ),
        )
        parser.add_argument(
            "--no-remote",
            action="store_true",
            help="Skip the server snapshot journal merge (local rows only)",
        )
        parser.add_argument(
            "--include-archived", action="store_true", help="Include archived sessions"
        )
        parser.add_argument(
            "--limit", type=int, default=50, help="Maximum rows to list (default: 50, 0 for all)"
        )
        parser.add_argument(
            "--older-than-days", type=int, help="Target sessions older than this many days"
        )
        parser.add_argument("--yes", action="store_true", help="Confirm bulk remove operations")
        parser.add_argument(
            "--from-turn",
            help="Fork boundary (ADR-057 D-4): slice the copy at this turn id",
        )
        parser.add_argument(
            "--new-id",
            help="Explicit id for the fork row (default: generated)",
        )
        parser.add_argument(
            "--name",
            help="Explicit display name for the fork row",
        )
        # Frontend-venue runtime (wave 2026-09-21, ADR-046 A2): start/venue/stop.
        parser.add_argument(
            "--source",
            dest="venue_sources",
            help="Pushed source name(s), csv (start): the sandbox's provider(s)",
        )
        parser.add_argument(
            "--default-source",
            dest="venue_default_source",
            help="Default source for the sandbox (start; defaults to the first --source)",
        )
        parser.add_argument(
            "--web",
            dest="venue_web_policy",
            choices=["off", "none", "allowlist", "public"],
            help=(
                "The agent's web access in the sandbox (start; ADR-099): off, allowlist "
                "(with --web-domain) or public. Asked on a terminal when omitted"
            ),
        )
        parser.add_argument(
            "--web-domain",
            dest="venue_web_domains",
            action="append",
            metavar="DOMAIN[,DOMAIN]",
            help="Allowlist domain (start; repeatable, comma-separated; .a.org adds subdomains)",
        )
        parser.add_argument(
            "--json",
            dest="as_json",
            action="store_true",
            help="Emit JSON instead of a table",
        )
        return parser

    def handle(self, args: Namespace) -> int:
        subcommand = getattr(args, "session_subcommand", "list")
        handler = _session_handlers().get(subcommand)
        if handler is None:
            print(f"Unknown session subcommand: {subcommand}", file=sys.stderr)
            return 2
        return handler(args)


def _session_handlers() -> dict[str, Any]:
    """Subcommand → handler (built lazily: handlers are defined below)."""
    return {
        "list": _handle_list,
        "show": _handle_show,
        "archive": _handle_archive,
        "remove": _handle_remove,
        "delete": _handle_remove,
        "conclude": _handle_conclude,
        "pull": _handle_pull,
        "reconcile": _handle_reconcile,
        "fork": _handle_fork,
        "start": _handle_venue_start,
        "venue": _handle_venue_status,
        "stop": _handle_venue_stop,
        "questions": _handle_venue_questions,
        "answer": _handle_venue_answer,
        # ADR-114 D-7: the local session bus.
        "live": session_bus_cli.handle_live,
        "send": session_bus_cli.handle_send,
    }


def _handle_fork(args: Namespace) -> int:
    """Duplicate a session row (ADR-057 D-4 fork leg).

    Mirror rides the fork row's first live save, like every session (WP-04).
    Unknown --from-turn ids fail closed with the named turn error.
    """
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session fork requires a session ID", file=sys.stderr)
        return 2
    from uuid import uuid4

    from ..companion.session_store import CompanionSessionStore

    new_id = getattr(args, "new_id", None) or f"sess-{uuid4().hex[:12]}"
    store = CompanionSessionStore()
    try:
        summary = store.fork(
            session_id,
            new_id,
            new_name=getattr(args, "name", "") or "",
            from_turn=getattr(args, "from_turn", "") or "",
        )
    except KeyError as exc:
        print(f"fork.turn_not_found: {exc.args[0]}", file=sys.stderr)
        return 1
    if summary is None:
        print(f"session not found: {session_id}", file=sys.stderr)
        return 1
    if getattr(args, "as_json", False):
        print(_json.dumps(summary, indent=2, default=str))
        return 0
    name = summary["name"]
    msgs = summary["messages"]
    print(f"Forked {session_id} → {new_id} ({name}, {msgs} messages).")
    return 0


def _handle_list(args: Namespace) -> int:
    error = _validate_filter_tokens(args)
    if error:
        print(error, file=sys.stderr)
        return 2
    rows = _collect_rows(args)
    if getattr(args, "as_json", False):
        print(_json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("No sessions found.")
        return 0
    _print_rows(rows)
    return 0


def _handle_conclude(args: Namespace) -> int:
    """Explicit completion (plan 2026-09-09): records state='completed'.

    Never inferred by the engine. Companion rows only for round 1; remote
    journal mirroring rides the next save/mirror window (stored locally
    first — absent remote rows must be pulled before concluding).
    """
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session conclude requires a session ID", file=sys.stderr)
        return 2
    from ..companion.session_store import CompanionSessionStore

    store = CompanionSessionStore()
    if store.set_state(session_id, "completed"):
        print(f"Concluded companion session {session_id} (state=completed).")
        return 0
    print(
        f"Conclude failed: no local row for {session_id}. "
        "Remote-only sessions must be pulled before concluding "
        "(rebuild via `r4 chat` attach or a fresh-machine rebuild).",
        file=sys.stderr,
    )
    return 1


# ── frontend-venue runtime verbs (wave 2026-09-21, ADR-046 A2) ─────────────


def _venue_client_and_store() -> tuple[Any, Any]:
    from ..client import Client
    from ..companion.session_store import CompanionSessionStore

    return Client(), CompanionSessionStore()


def _venue_url(client, session_id: str, tail: str = "") -> str:
    from ..companion.session_store import journal_prefix

    return f"{journal_prefix(client)}/companion/sessions/{session_id}/venue{tail}"


def _venue_error(exc: Exception) -> str:
    code = str((getattr(exc, "details", None) or {}).get("code") or "")
    return f"{code}: {exc}" if code else str(exc)


def _field(payload: dict[str, Any], key: str, default: str = "-") -> Any:
    return payload.get(key) or default


def _job_lines(payload: dict[str, Any]) -> list[str]:
    """The durable session job behind the row (ADR-046 A2 P10), when linked."""
    job = payload.get("job") or {}
    if not isinstance(job, dict) or not job.get("job_id"):
        return []
    attempt = job.get("attempt_status") or "-"
    detail = f"  ({job.get('attempt_detail')})" if job.get("attempt_detail") else ""
    return [
        f"job:      {job.get('job_id')}  status: {job.get('status')}  attempt: {attempt}{detail}"
    ]


def _venue_lines(payload: dict[str, Any]) -> list[str]:
    """Human rendering of one venue status payload."""
    lease = payload.get("lease") or {}
    runtime = payload.get("runtime") or {}
    sources = ", ".join(payload.get("source_names") or [])
    lines = [
        f"session:  {payload.get('session_id')}",
        f"venue:    {payload.get('venue')}  state: {payload.get('state')}",
    ]
    if payload.get("reason"):
        lines.append(f"reason:   {payload.get('reason')}")
    lines.extend(_job_lines(payload))
    lines.append(f"sources:  {sources or '-'}  (default: {_field(payload, 'default_source')})")
    lines.append(
        f"web:      {payload.get('web_policy')}  egress ip: {_field(payload, 'egress_ip')}"
    )
    lines.append(f"lease:    expires {_field(lease, 'expires_at')}  valid: {lease.get('is_valid')}")
    if runtime:
        lines.append(
            f"runtime:  phase={_field(runtime, 'phase')} turn_seq={runtime.get('turn_seq', 0)}"
        )
    return lines


def _print_venue(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(_json.dumps(payload, indent=2, default=str))
        return
    for line in _venue_lines(payload):
        print(line)


def _csv_tokens(value: Any) -> list[str]:
    return [s.strip() for s in str(value or "").split(",") if s.strip()]


def _venue_start_session(client, store, session_id: str):
    """Load the session (pull-then-load for remote-only rows); None when absent."""
    session = store.load(session_id, client)
    if session is None and store.pull_session(client, session_id) == "pulled":
        session = store.load(session_id, client)
    return session


def _session_project(session: Any) -> str:
    return str((session.metadata.get("config") or {}).get("project_id") or "")


def _venue_start_body(args: Namespace, sources: list[str], session) -> dict[str, Any]:
    from ..companion.web_access import WebAccess

    access = WebAccess.from_metadata(session.metadata)
    return {
        "source_refs": sources,
        "default_source": getattr(args, "venue_default_source", None) or "",
        "web_policy": access.mode,
        "web_allow_domains": list(access.domains),
        "project": _session_project(session),
    }


def _venue_web_access(args: Namespace, client: Any, session_id: str, session: Any) -> Any:
    """The session's web access for this start (ADR-099 D-2); None after
    printing why there is none (exit 2)."""
    from ..companion.web_access import WebAccess, WebAccessError
    from .web_access_prompt import remote_web_access

    try:
        return remote_web_access(
            flag=getattr(args, "venue_web_policy", None),
            domains=getattr(args, "venue_web_domains", None) or [],
            client=client,
            project=_session_project(session),
            preview_url=_venue_url(client, session_id, "/policy"),
            # RS-1 fix W2: a restart keeps the session's own choice unless
            # --web or R4_WEB_ACCESS says otherwise; the question preselects it.
            current=WebAccess.from_metadata(session.metadata),
        )
    except WebAccessError as exc:
        print(f"start failed: {exc}", file=sys.stderr)
        return None


def _mark_frontend(session: Any, access: Any) -> None:
    """Pin the session to the frontend venue with its web choice; the worker
    restores both from the mirrored copy (its local guard reads the choice)."""
    session.metadata["venue"] = "frontend"
    session.metadata["web_access"] = access.to_metadata()


def _venue_start_target(client: Any, store: Any, session_id: str) -> Any:
    """The session to start, or None after printing why it is missing."""
    session = _venue_start_session(client, store, session_id)
    if session is None:
        print(
            f"start failed: session {session_id} not found locally or in the journal",
            file=sys.stderr,
        )
    return session


def _handle_venue_start(args: Namespace) -> int:
    """``r4 session start <id> --source NAME[,NAME]`` → provision the sandbox."""
    session_id = getattr(args, "session_id", None)
    sources = _csv_tokens(getattr(args, "venue_sources", None))
    if not session_id or not sources:
        print("session start requires a session ID and --source NAME[,NAME]", file=sys.stderr)
        return 2
    try:
        client, store = _venue_client_and_store()
    except Exception as exc:  # noqa: BLE001 — profile optional
        print(f"start failed: client profile unavailable: {exc}", file=sys.stderr)
        return 1
    session = _venue_start_target(client, store, session_id)
    if session is None:
        return 1
    access = _venue_web_access(args, client, session_id, session)
    if access is None:
        return 2
    _mark_frontend(session, access)
    store.save(session)
    # A copy older than the journal (a remote turn never adopted here): the
    # journal wins, and the restarted worker restores that copy anyway.
    adopted = store.adopt_newer_journal(session, client)
    if adopted is not None:
        session = adopted
        _mark_frontend(session, access)
        store.save(session)
    sync = store.sync_status()
    if sync.get("mode") == "degraded":
        print(f"start failed: mirror degraded: {sync.get('error')}", file=sys.stderr)
        return 1
    body = _venue_start_body(args, sources, session)
    _push_agent_settings(client, session)
    try:
        payload = client.post(_venue_url(client, session_id, "/start"), json=body, timeout=240)
    except Exception as exc:  # noqa: BLE001 — typed code on the error when present
        print(f"start failed: {_venue_error(exc)}", file=sys.stderr)
        return 1
    _report_started(payload, session_id, as_json=bool(args.as_json))
    return 0


def _push_agent_settings(client: Any, session: Any) -> None:
    """The worker runs with the user's local settings (ADR-099 D-8); a failed
    push is named, never fatal (the worker applies the same rule's defaults)."""
    from ..companion.agent_settings import configured_ask_user_mode, effective_agent_settings
    from ..venue_settings import try_push_venue_agent_settings

    settings = effective_agent_settings(
        session, ask_user_mode=configured_ask_user_mode(getattr(client, "config", None))
    )
    reason = try_push_venue_agent_settings(client, str(session.session_id), settings)
    if reason:
        print(f"warning: agent settings not sent to the remote session: {reason}", file=sys.stderr)


def _report_started(payload: Any, session_id: str, *, as_json: bool) -> None:
    payload = payload if isinstance(payload, dict) else {}
    _print_venue(payload, as_json=as_json)
    if payload.get("state") == "provisioning" and not as_json:
        print(f"provisioning on the server — poll with `r4 session venue {session_id}`")


def _handle_venue_status(args: Namespace) -> int:
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session venue requires a session ID", file=sys.stderr)
        return 2
    try:
        client, _ = _venue_client_and_store()
        payload = client.get(_venue_url(client, session_id), timeout=15)
    except Exception as exc:  # noqa: BLE001
        print(f"venue status failed: {_venue_error(exc)}", file=sys.stderr)
        return 1
    _print_venue(payload if isinstance(payload, dict) else {}, as_json=bool(args.as_json))
    return 0


def _handle_venue_stop(args: Namespace) -> int:
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session stop requires a session ID", file=sys.stderr)
        return 2
    try:
        client, _ = _venue_client_and_store()
        payload = client.post(_venue_url(client, session_id, "/stop"), json={}, timeout=120)
    except Exception as exc:  # noqa: BLE001
        print(f"stop failed: {_venue_error(exc)}", file=sys.stderr)
        return 1
    _print_venue(payload if isinstance(payload, dict) else {}, as_json=bool(args.as_json))
    return 0


def _questions_url(client: Any, session_id: str, tail: str = "") -> str:
    from ..companion.session_store import journal_prefix

    return f"{journal_prefix(client)}/companion/sessions/{session_id}/questions{tail}"


def _question_lines(rows: list[Any]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(f"{row.get('question_id')}  ({row.get('created_at') or '-'})")
        lines.append(f"  {row.get('question')}")
        options = row.get("options") or []
        if options:
            lines.append("  options: " + " | ".join(str(o) for o in options))
    return lines or ["no pending questions"]


def _handle_venue_questions(args: Namespace) -> int:
    """``r4 session questions <sid>``: the remote worker's pending questions."""
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session questions requires a session ID", file=sys.stderr)
        return 2
    try:
        client, _ = _venue_client_and_store()
        payload = client.get(
            _questions_url(client, session_id), params={"status": "pending"}, timeout=15
        )
    except Exception as exc:  # noqa: BLE001
        print(f"questions failed: {_venue_error(exc)}", file=sys.stderr)
        return 1
    rows = payload.get("questions", []) if isinstance(payload, dict) else []
    if args.as_json:
        print(_json.dumps({"questions": rows}, indent=2, default=str))
    else:
        print("\n".join(_question_lines(rows)))
    return 0


def _handle_venue_answer(args: Namespace) -> int:
    """``r4 session answer <sid> <question_id> <text…>``: answer from any client."""
    session_id = getattr(args, "session_id", None)
    rest = list(getattr(args, "answer_args", None) or [])
    text = " ".join(rest[1:]).strip()
    if not session_id or not rest or not text:
        print("usage: r4 session answer <session_id> <question_id> <text>", file=sys.stderr)
        return 2
    try:
        client, _ = _venue_client_and_store()
        payload = client.post(
            _questions_url(client, session_id, f"/{rest[0]}/answer"),
            json={"answer": text},
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 — typed code (question_closed) on the error
        print(f"answer failed: {_venue_error(exc)}", file=sys.stderr)
        return 1
    question = payload.get("question") if isinstance(payload, dict) else None
    if args.as_json:
        print(_json.dumps({"question": question}, indent=2, default=str))
    else:
        print(f"answered {rest[0]}; the remote session continues")
    return 0


def _handle_pull(args: Namespace) -> int:
    """Pull a remote-only session into the local store (plan P5.

    Local rows always win — never overwrites an existing local row.
    Outcomes map one-to-one on store.pull_session's vocabulary.
    """
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session pull requires a session ID", file=sys.stderr)
        return 2
    from ..client import Client
    from ..companion.session_store import CompanionSessionStore

    try:
        client = Client()
    except Exception as exc:  # noqa: BLE001 — profile optional
        print(f"pull failed: client profile unavailable: {exc}", file=sys.stderr)
        return 1
    outcome = CompanionSessionStore().pull_session(client, session_id)
    if outcome == "pulled":
        print(f"Pulled session {session_id} from the server journal.")
        return 0
    if outcome == "already-local":
        print(f"Session {session_id} already local (no pull performed).")
        return 0
    if outcome == "not-found":
        print(
            f"Not found remotely: {session_id} (absent in the journal, or owned by another user).",
            file=sys.stderr,
        )
        return 1
    print(f"pull failed: snapshot journal unreachable for {session_id}", file=sys.stderr)
    return 1


def _handle_reconcile(args: Namespace) -> int:
    """Full read-model pass against the journal (WP-04 follow-on).

    Last-writer-wins with 3-way _mirror_marker base; conflicts are
    recorded on the row metadata (never silent). None from the store
    means the journal is unreachable.
    """
    from ..client import Client
    from ..companion.session_store import CompanionSessionStore

    try:
        client = Client()
    except Exception as exc:  # noqa: BLE001 — profile optional
        print(f"reconcile failed: client profile unavailable: {exc}", file=sys.stderr)
        return 1
    counters = CompanionSessionStore().reconcile(client)
    if counters is None:
        print("reconcile failed: snapshot journal unreachable", file=sys.stderr)
        return 1
    if getattr(args, "as_json", False):
        print(_json.dumps(counters, indent=2))
        return 0
    print(
        "reconcile: " + ", ".join(f"{key}={value}" for key, value in counters.items() if value)
        if any(counters.values())
        else "reconcile: no drift (counters all zero)"
    )
    if counters.get("conflicts"):
        print(
            "note: "
            f"{counters['conflicts']} conflict(s) recorded on row metadata "
            "(_mirror_conflicts) — winners per timestamp, loser retained by upload/adopt",
            file=sys.stderr,
        )
    return 0


def _validate_filter_tokens(args: Namespace) -> str | None:
    from ..companion.session_store import validate_venue

    venue_tokens = _csv(getattr(args, "venue", None))
    for token in venue_tokens:
        try:
            validate_venue(token)
        except ValueError as exc:
            return str(exc)
    state_tokens = _csv(getattr(args, "state", None))
    allowed = {"ongoing", "stopped", "completed", "error", "archived"}
    bad = [token for token in state_tokens if token not in allowed]
    if bad:
        return f"invalid state filter: {bad} (allowed: {sorted(allowed)})"
    if "archived" in state_tokens and not getattr(args, "include_archived", False):
        return "state=archived requires --include-archived"
    return None


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _handle_show(args: Namespace) -> int:
    session_id = getattr(args, "session_id", None)
    if not session_id:
        print("session show requires a session ID", file=sys.stderr)
        return 2
    record = _find_record(session_id, getattr(args, "kind", "all"))
    if record is None:
        print(f"Session not found: {session_id}", file=sys.stderr)
        return 1
    if getattr(args, "as_json", False):
        print(_json.dumps(record, indent=2, default=str))
        return 0
    _print_record(record)
    return 0


def _handle_archive(args: Namespace) -> int:
    targets, error = _action_targets(args, include_archived=False)
    if error:
        print(error, file=sys.stderr)
        return 2
    if not targets:
        print("No matching sessions to archive.")
        return 0
    archived = 0
    for target in targets:
        if _archive_session(target["kind"], target["id"]):
            archived += 1
            print(f"Archived {target['kind']} session {target['id']}")
    return 0 if archived == len(targets) else 1


def _handle_remove(args: Namespace) -> int:
    targets, error = _action_targets(args, include_archived=True)
    if error:
        print(error, file=sys.stderr)
        return 2
    if not targets:
        print("No matching sessions to remove.")
        return 0
    if len(targets) > 1 and not getattr(args, "yes", False):
        print("Bulk remove requires --yes.", file=sys.stderr)
        return 2
    removed = 0
    for target in targets:
        if _remove_session(target["kind"], target["id"]):
            removed += 1
            print(f"Removed {target['kind']} session {target['id']}")
    return 0 if removed == len(targets) else 1


def _action_targets(
    args: Namespace, *, include_archived: bool
) -> tuple[list[dict[str, Any]], str | None]:
    session_id = getattr(args, "session_id", None)
    older_than_days = getattr(args, "older_than_days", None)
    if session_id:
        record = _find_record(session_id, getattr(args, "kind", "all"))
        return ([record] if record else []), None
    if older_than_days is None:
        return [], "session archive/remove requires a session ID or --older-than-days"
    rows = _collect_rows(
        args, include_archived=include_archived, apply_limit=False, merge_remote=False
    )
    return [
        session_row
        for session_row in rows
        if _older_than(session_row.get("updated_at", ""), older_than_days)
    ], None


def _collect_rows(
    args: Namespace,
    *,
    include_archived: bool | None = None,
    apply_limit: bool = True,
    merge_remote: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind in _selected_kinds(getattr(args, "kind", "all")):
        try:
            rows.extend(_rows_for_kind(kind))
        except Exception as exc:
            print(f"warning: {kind} registry unavailable: {exc}", file=sys.stderr)
    wants_companion = "companion" in _selected_kinds(getattr(args, "kind", "all"))
    if merge_remote and wants_companion and not getattr(args, "no_remote", False):
        rows = _merge_remote_companion(rows)
    rows = _filter_rows(rows, args, include_archived=include_archived)
    rows.sort(key=lambda session_row: session_row.get("updated_at", ""), reverse=True)
    limit = int(getattr(args, "limit", 50) or 0)
    if apply_limit and limit > 0:
        rows = rows[:limit]
    return rows


def _fetch_session_journal() -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the snapshot journal; (snapshots, error) — error means unreachable."""
    try:
        from ..client import Client
        from ..companion.session_store import journal_prefix

        client = Client()
        listing = client.get(f"{journal_prefix(client)}/companion/sessions?limit=200") or {}
        return listing.get("snapshots") or [], None
    except Exception as exc:  # noqa: BLE001 — journal optional for listing
        return [], str(exc)


def _merge_remote_companion(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge server snapshot journal rows with local companion rows.

    One unified listing (plan 2026-09-09): local row wins when an id is on
    both sides (SQLite is the read-model); absent-only-remote rows enter
    with sync='remote-only'; badges use the §8a vocabulary — local-only /
    remote-only / mirrored. Journal unreachable → local rows keep
    sync='local-only' and a warning is printed (never silent emulation).
    """
    snapshots, error = _fetch_session_journal()
    if error is not None:
        print(f"warning: remote session journal unreachable: {error}", file=sys.stderr)
        _mark_sync(rows, set(), degraded=False)
        return rows
    remote_ids = {str(snap.get("session_id") or "") for snap in snapshots}
    _mark_sync(rows, remote_ids, degraded=False)
    local_ids = {row["id"] for row in rows if row.get("kind") == "companion"}
    for snap in snapshots:
        remote_row = _remote_snapshot_row(snap, local_ids)
        if remote_row is not None:
            rows.append(remote_row)
    return rows


def _mark_sync(rows: list[dict[str, Any]], remote_ids: set[str], *, degraded: bool) -> None:
    """Assign the §8a badge to every companion row (mutates in place)."""
    for row in rows:
        if row.get("kind") != "companion":
            continue
        if row["id"] in remote_ids:
            row["sync"] = "degraded" if degraded else "mirrored"
        else:
            row["sync"] = "local-only"


def _remote_snapshot_row(snap: dict[str, Any], local_ids: set[str]) -> dict[str, Any] | None:
    """Row for a journal snapshot absent locally; None when mirrored/junk."""
    sid = str(snap.get("session_id") or "")
    if not sid or sid in local_ids:
        return None
    row = _base_row(
        kind="companion",
        session_id=sid,
        name=str(snap.get("name") or ""),
        updated_at=str(snap.get("updated_at") or ""),
        created_at="",
        project="",
        endpoints=[],
        info="",
        archived_at=str(snap.get("archived_at") or ""),
    )
    row.update(
        {
            "venue": str(snap.get("venue") or ""),
            "state": str(snap.get("state") or ""),
            "sync": "remote-only",
        }
    )
    return row


def _rows_for_kind(kind: str) -> list[dict[str, Any]]:
    if kind == "agent":
        return _agent_rows()
    if kind == "companion":
        return _companion_rows()
    if kind == "teamup":
        return _teamup_rows()
    return []


def _agent_rows() -> list[dict[str, Any]]:
    from ..agent.session import SESSIONS_DIR

    rows: list[dict[str, Any]] = []
    if not SESSIONS_DIR.exists():
        return rows
    for session_path in SESSIONS_DIR.glob("*.json"):
        data = _read_json_object(session_path)
        if data:
            rows.append(_agent_row(data))
    return rows


def _companion_rows() -> list[dict[str, Any]]:
    from ..companion.session import CompanionSessionStore

    rows: list[dict[str, Any]] = []
    for session in CompanionSessionStore().list_sessions(limit=0, include_archived=True):
        row = _base_row(
            kind="companion",
            session_id=session["id"],
            name=session.get("name", ""),
            updated_at=session.get("updated_at", ""),
            created_at=session.get("created_at", ""),
            project=session.get("project_id", ""),
            endpoints=session.get("endpoints", []),
            info=f"{session.get('message_count', 0)} msgs",
            archived_at=session.get("archived_at", ""),
        )
        row["venue"] = session.get("venue", "local")
        row["state"] = session.get("state", "")
        rows.append(row)
    return rows


def _teamup_rows() -> list[dict[str, Any]]:
    from ..teamup.session import TEAMUP_SESSIONS_DIR

    rows: list[dict[str, Any]] = []
    if not TEAMUP_SESSIONS_DIR.exists():
        return rows
    for session_dir in TEAMUP_SESSIONS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        data = _read_json_object(session_dir / "session.json")
        if data:
            rows.append(_teamup_row(data))
    return rows


def _agent_row(data: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict_value(data.get("metadata"))
    messages = _list_value(data.get("messages"))
    return _base_row(
        kind="agent",
        session_id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        updated_at=str(data.get("updated_at") or ""),
        created_at=str(data.get("created_at") or ""),
        project=_project_from_data(data),
        endpoints=_endpoint_labels(metadata),
        info=f"{len(messages)} msgs",
        archived_at=_archived_at(metadata),
    )


def _teamup_row(data: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict_value(data.get("metadata"))
    config = _dict_value(data.get("config"))
    return _base_row(
        kind="teamup",
        session_id=str(data.get("session_id", "")),
        name=str(config.get("name") or data.get("name") or ""),
        updated_at=str(data.get("updated_at") or ""),
        created_at=str(data.get("started_at") or data.get("created_at") or ""),
        project=_project_from_data(data),
        endpoints=_endpoint_labels(metadata),
        info=f"{data.get('status', '?')} iter={data.get('iteration', 0)}",
        archived_at=_archived_at(metadata),
    )


def _base_row(
    *,
    kind: str,
    session_id: str,
    name: str,
    updated_at: str,
    created_at: str,
    project: str,
    endpoints: list[str],
    info: str,
    archived_at: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": session_id,
        "name": name,
        "created_at": created_at,
        "updated_at": updated_at,
        "project": project,
        "project_id": project,
        "endpoint": ",".join(endpoints),
        "endpoints": endpoints,
        "info": info,
        "archived_at": archived_at,
    }


def _row_kept(
    session_row: dict[str, Any],
    *,
    show_archived: bool,
    endpoint_filter: str | None,
    project_filter: str | None,
    older_than_days: int | None,
    venue_filter: list[str],
    state_filter: list[str],
) -> bool:
    """Filter predicate for one row (extracted for the complexity gate)."""
    if not show_archived and session_row.get("archived_at"):
        return False
    if endpoint_filter and not _matches_endpoint(session_row.get("endpoints", []), endpoint_filter):
        return False
    if project_filter and session_row.get("project_id") != project_filter:
        return False
    if older_than_days is not None and not _older_than(
        session_row.get("updated_at", ""), older_than_days
    ):
        return False
    return _venue_state_kept(session_row, venue_filter, state_filter)


def _venue_state_kept(
    session_row: dict[str, Any], venue_filter: list[str], state_filter: list[str]
) -> bool:
    """Venue/state csv filter predicate; archived wins over recorded state."""
    if venue_filter and session_row.get("venue", "") not in venue_filter:
        return False
    if state_filter:
        computed = "archived" if session_row.get("archived_at") else session_row.get("state", "")
        if computed not in state_filter:
            return False
    return True


def _filter_rows(
    rows: list[dict[str, Any]],
    args: Namespace,
    *,
    include_archived: bool | None,
) -> list[dict[str, Any]]:
    show_archived = (
        getattr(args, "include_archived", False) if include_archived is None else include_archived
    )
    return [
        row
        for row in rows
        if _row_kept(
            row,
            show_archived=show_archived,
            endpoint_filter=getattr(args, "endpoint", None),
            project_filter=getattr(args, "project", None),
            older_than_days=getattr(args, "older_than_days", None),
            venue_filter=_csv(getattr(args, "venue", None)),
            state_filter=_csv(getattr(args, "state", None)),
        )
    ]


def _find_record(session_id: str, kind_filter: str) -> dict[str, Any] | None:
    for kind in _selected_kinds(kind_filter):
        record = _record_for_kind(kind, session_id)
        if record is not None:
            return record
    return None


def _record_for_kind(kind: str, session_id: str) -> dict[str, Any] | None:
    if kind == "agent":
        return _agent_record(session_id)
    if kind == "companion":
        return _companion_record(session_id)
    if kind == "teamup":
        return _teamup_record(session_id)
    return None


def _agent_record(session_id: str) -> dict[str, Any] | None:
    from ..agent.session import SESSIONS_DIR

    data = _read_json_object(SESSIONS_DIR / f"{session_id}.json")
    if not data:
        return None
    metadata = _dict_value(data.get("metadata"))
    return {
        **_agent_row(data),
        "messages": _list_value(data.get("messages")),
        "metadata": metadata,
        "context": _dict_value(data.get("context")),
        "config": _dict_value(data.get("config")),
        "workflows": _list_value(data.get("workflows")),
    }


def _companion_record(session_id: str) -> dict[str, Any] | None:
    from ..companion.session import CompanionSessionStore

    record = CompanionSessionStore().get_session_record(session_id)
    if record is None:
        return None
    return {
        **_base_row(
            kind="companion",
            session_id=record["id"],
            name=record.get("name", ""),
            updated_at=record.get("updated_at", ""),
            created_at=record.get("created_at", ""),
            project=record.get("project_id", ""),
            endpoints=record.get("endpoints", []),
            info=f"{record.get('message_count', 0)} msgs",
            archived_at=record.get("archived_at", ""),
        ),
        "messages": record.get("messages", []),
        "metadata": record.get("metadata", {}),
        "active_skills": record.get("active_skills", []),
        "memory": record.get("memory", {}),
        "model": record.get("model", ""),
        "venue": record.get("venue", ""),
        "state": record.get("state", ""),
    }


def _teamup_record(session_id: str) -> dict[str, Any] | None:
    from ..teamup.session import TEAMUP_SESSIONS_DIR

    data = _read_json_object(TEAMUP_SESSIONS_DIR / session_id / "session.json")
    if not data:
        return None
    return {
        **_teamup_row(data),
        "messages": [],
        "metadata": {
            "config": _dict_value(data.get("config")),
            "status": data.get("status"),
            "agent_states": _dict_value(data.get("agent_states")),
            **_dict_value(data.get("metadata")),
        },
    }


def _archive_session(kind: str, session_id: str) -> bool:
    if kind == "companion":
        from ..companion.session import CompanionSessionStore

        archived = CompanionSessionStore().archive(session_id)
        if archived:
            # ADR-035 item-8: server owns the trust rows → converge on the
            # server archive endpoint (tombstone + cascade + audit, one tx).
            # Best-effort; the 86400s TTL is the crash bound.
            try:
                from ..client import Client

                Client().companion_session_archive(session_id)
            except Exception:  # noqa: BLE001 — offline archives stay TTL-bound
                print(
                    f"note: server-side trust cascade unreachable for {session_id}; "
                    "session-scoped grants expire by TTL (≤24h)",
                    file=sys.stderr,
                )
        return archived
    return _update_file_metadata(kind, session_id, archive=True)


def _remove_session(kind: str, session_id: str) -> bool:
    if kind == "companion":
        from ..client import Client
        from ..companion.session import CompanionSessionStore

        try:
            client = Client()
        except Exception:  # noqa: BLE001 — tombstone survives offline delete
            client = None
        outcome = CompanionSessionStore().delete(session_id, client=client)
        if outcome == "mirror-pending":
            print(
                f"  note: journal delete pending for {session_id} "
                "(tombstone recorded; retried at next reconcile)",
                file=sys.stderr,
            )
        return outcome is not None
    session_path = _session_path(kind, session_id)
    if session_path is None or not session_path.exists():
        return False
    if kind == "teamup":
        shutil.rmtree(session_path.parent)
    else:
        session_path.unlink()
    return True


def _update_file_metadata(kind: str, session_id: str, *, archive: bool) -> bool:
    session_path = _session_path(kind, session_id)
    if session_path is None:
        return False
    data = _read_json_object(session_path)
    if not data:
        return False
    metadata = _dict_value(data.get("metadata"))
    if archive:
        metadata.setdefault("archived_at", datetime.now(timezone.utc).isoformat())
    data["metadata"] = metadata
    session_path.write_text(_json.dumps(data, indent=2, default=str) + "\n")
    return True


def _session_path(kind: str, session_id: str) -> Path | None:
    if kind == "agent":
        from ..agent.session import SESSIONS_DIR

        return SESSIONS_DIR / f"{session_id}.json"
    if kind == "teamup":
        from ..teamup.session import TEAMUP_SESSIONS_DIR

        return TEAMUP_SESSIONS_DIR / session_id / "session.json"
    return None


def _print_rows(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'KIND':<10} {'VENUE':<9} {'STATE':<10} {'SYNC':<12} {'ID':<36} {'NAME':<22} "
        f"{'PROJECT':<14} {'ENDPOINT':<18} {'INFO':<12} UPDATED"
    )
    print("-" * 152)
    for session_row in rows:
        updated = session_row.get("updated_at", "")[:19]
        computed = "archived" if session_row.get("archived_at") else session_row.get("state", "")
        print(
            f"{session_row['kind']:<10} "
            f"{session_row.get('venue', '-'):<9} "
            f"{computed or '-':<10} "
            f"{session_row.get('sync', '-'):<12} "
            f"{session_row['id'][:36]:<36} "
            f"{session_row['name'][:20]:<22} "
            f"{session_row.get('project_id', '')[:12]:<14} "
            f"{session_row.get('endpoint', '')[:16]:<18} "
            f"{session_row.get('info', '')[:10]:<12} {updated}"
        )


def _print_venue_state(record: dict[str, Any]) -> None:
    """Print venue/state lines only when recorded (never fabricated)."""
    if record.get("venue"):
        print(f"Venue: {record['venue']}")
    if record.get("state") or record.get("archived_at"):
        print(f"State: {'archived' if record.get('archived_at') else record.get('state', '')}")


def _print_record_header(record: dict[str, Any]) -> None:
    """Header + metadata block (extracted for the complexity gate)."""
    print(f"Kind: {record.get('kind', '')}")
    print(f"ID: {record.get('id', '')}")
    print(f"Name: {record.get('name', '')}")
    _print_venue_state(record)
    print(f"Project: {record.get('project_id', '') or '-'}")
    print(f"Endpoint: {record.get('endpoint', '') or '-'}")
    print(f"Created: {record.get('created_at', '') or '-'}")
    print(f"Updated: {record.get('updated_at', '') or '-'}")
    if record.get("archived_at"):
        print(f"Archived: {record['archived_at']}")
    print("\nMetadata:")
    print(_json.dumps(record.get("metadata", {}), indent=2, default=str))


def _print_record_message(message: dict[str, Any]) -> None:
    """One message block (extracted for the complexity gate)."""
    role = str(message.get("role", "message"))
    timestamp = str(message.get("timestamp", ""))
    content = str(message.get("content", ""))
    header = f"[{timestamp}] {role}" if timestamp else role
    print(f"\n{header}:")
    print(content)


def _print_record(record: dict[str, Any]) -> None:
    _print_record_header(record)
    print("\nMessages:")
    messages = _list_value(record.get("messages"))
    if not messages:
        print("No message history recorded.")
        return
    for message in messages:
        if isinstance(message, dict):
            _print_record_message(message)


def _selected_kinds(kind_filter: str) -> tuple[str, ...]:
    return SESSION_KINDS if kind_filter == "all" else (kind_filter,)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = _json.loads(path.read_text())
    except (_json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _project_from_data(data: dict[str, Any]) -> str:
    metadata = _dict_value(data.get("metadata"))
    context = _dict_value(data.get("context"))
    config = _dict_value(data.get("config"))
    metadata_config = _dict_value(metadata.get("config"))
    for candidate in (
        metadata.get("project_id"),
        metadata.get("project"),
        metadata_config.get("project_id"),
        metadata_config.get("project"),
        context.get("project_id"),
        config.get("project_id"),
    ):
        if candidate:
            return str(candidate)
    return ""


def _endpoint_labels(metadata: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("endpoint", "endpoint_id", "instance_id"):
        if metadata.get(key):
            labels.append(str(metadata[key]))
    endpoints = metadata.get("active_endpoints") or metadata.get("endpoints")
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if isinstance(endpoint, dict):
                label = _endpoint_label(endpoint)
                if label:
                    labels.append(label)
            elif endpoint:
                labels.append(str(endpoint))
    return list(dict.fromkeys(labels))


def _endpoint_label(endpoint: dict[str, Any]) -> str:
    for key in ("instance_id", "endpoint_id", "id", "job_id", "name", "model"):
        if endpoint.get(key):
            return str(endpoint[key])
    return ""


def _matches_endpoint(endpoints: list[str], endpoint_filter: str) -> bool:
    needle = endpoint_filter.lower()
    return any(needle in endpoint.lower() for endpoint in endpoints)


def _archived_at(metadata: dict[str, Any]) -> str:
    archived_at = metadata.get("archived_at")
    if archived_at:
        return str(archived_at)
    return "archived" if metadata.get("archived") else ""


def _older_than(updated_at: str, days: int) -> bool:
    parsed = _parse_datetime(updated_at)
    if parsed is None:
        return False
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return now - parsed >= timedelta(days=days)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def cmd_sessions(args: Namespace) -> int:
    return SessionsCommand().handle(args)
