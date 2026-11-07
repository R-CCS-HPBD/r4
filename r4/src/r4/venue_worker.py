"""Frontend-venue worker loop (wave 2026-09-21, ADR-046 A2; G-RS-13).

Runs inside the sandbox container (``r4 venue worker``):

1. D-7 isolation self-test — direct egress and external DNS must fail,
   ``egress`` must resolve; otherwise exit 78 ``venue_isolation_failed``.
2. Bootstrap — pull the owner's mirrored snapshot through the rail, restore
   it into a throw-away local store, load the CompanionSession, point its
   LLM route at the sidecar (``R4_ENDPOINT_URL``; api key = session token).
3. Loop — heartbeat (lease renew + runtime projection), pop the next queued
   turn (F9 owner-scope queue), run it with streaming so partial text rides
   the heartbeats, mirror the snapshot through the rail, repeat.

Tool approvals become durable T4 intents raised through the rail; the
worker polls the decision the owner makes from any client. ``ask_user``
questions become durable questions the owner answers from any client
(ADR-099 D-8), unless the user's own ``ask_user_mode`` is ``auto``. The
owner's agent settings (permission and interaction mode, workflow arming,
approved tools) are read through the rail at bootstrap and before each
queued turn, and applied with the local bridge's own rule
(``r4.companion.agent_settings``). The worker never holds a user JWT or a
provider key.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import R4Error
from .venue_rail import VenueRailClient, VenueRailError

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_LEASE_REFUSED = 3
EXIT_BOOTSTRAP_FAILED = 4
EXIT_ISOLATION_FAILED = 78

DEFAULT_POLL_SEC = 2.0
DEFAULT_HEARTBEAT_SEC = 600.0
DEFAULT_APPROVAL_TIMEOUT_SEC = 900.0
DEFAULT_APPROVAL_POLL_SEC = 2.0
DEFAULT_QUESTION_TIMEOUT_SEC = 1800.0
# A client that never sent agent settings (rev 0) predates remote questions
# and cannot answer one: wait this long, then say so (ADR-099 D-8).
DEFAULT_QUESTION_TIMEOUT_OLD_CLIENT_SEC = 120.0
DEFAULT_QUESTION_POLL_SEC = 2.0
QUESTION_POLL_MAX_BACKOFF_SEC = 30.0
# Poll refusals that end the wait: the lease is gone or the question is.
_QUESTION_POLL_FATAL = (401, 404)
PARTIAL_FLUSH_SEC = 1.0
RAIL_BACKOFF_SEC = 5.0


@dataclass(frozen=True)
class WorkerEnv:
    session_id: str
    rail_url: str
    token_file: str
    endpoint_url: str = "http://egress:8080"
    poll_sec: float = DEFAULT_POLL_SEC
    heartbeat_sec: float = DEFAULT_HEARTBEAT_SEC
    approval_timeout_sec: float = DEFAULT_APPROVAL_TIMEOUT_SEC
    question_timeout_sec: float = DEFAULT_QUESTION_TIMEOUT_SEC
    question_timeout_old_client_sec: float = DEFAULT_QUESTION_TIMEOUT_OLD_CLIENT_SEC
    skip_selftest: bool = False
    db_path: str = "/tmp/home/venue-sessions.db"
    # ADR-099 D-9: the web access the server started the sandbox with
    # (``R4_WEB_ACCESS``, ``MODE[:d1,d2]``; "" from a server before RS-1).
    web_access: str = ""

    @classmethod
    def from_environ(cls, env: Mapping[str, str] | None = None) -> WorkerEnv:
        env = os.environ if env is None else env
        session_id = env.get("R4_VENUE_SESSION_ID", "").strip()
        rail_url = env.get("R4_VENUE_RAIL_URL", "").strip()
        token_file = env.get("R4_VENUE_SESSION_TOKEN_FILE", "").strip()
        missing = [
            name
            for name, value in (
                ("R4_VENUE_SESSION_ID", session_id),
                ("R4_VENUE_RAIL_URL", rail_url),
                ("R4_VENUE_SESSION_TOKEN_FILE", token_file),
            )
            if not value
        ]
        if missing:
            raise R4Error("venue worker env incomplete: " + ", ".join(missing))

        def _float(name: str, default: float) -> float:
            try:
                return float(env.get(name, "") or default)
            except ValueError:
                return default

        return cls(
            session_id=session_id,
            rail_url=rail_url,
            token_file=token_file,
            endpoint_url=env.get("R4_ENDPOINT_URL", "").strip() or "http://egress:8080",
            poll_sec=_float("R4_VENUE_POLL_SEC", DEFAULT_POLL_SEC),
            heartbeat_sec=_float("R4_VENUE_HEARTBEAT_SEC", DEFAULT_HEARTBEAT_SEC),
            approval_timeout_sec=_float(
                "R4_VENUE_APPROVAL_TIMEOUT_SEC", DEFAULT_APPROVAL_TIMEOUT_SEC
            ),
            question_timeout_sec=_float(
                "R4_VENUE_QUESTION_TIMEOUT_SEC", DEFAULT_QUESTION_TIMEOUT_SEC
            ),
            question_timeout_old_client_sec=_float(
                "R4_VENUE_QUESTION_TIMEOUT_OLD_CLIENT_SEC",
                DEFAULT_QUESTION_TIMEOUT_OLD_CLIENT_SEC,
            ),
            skip_selftest=env.get("R4_VENUE_SKIP_SELFTEST", "").strip() in {"1", "true", "yes"},
            db_path=env.get("R4_COMPANION_DB", "").strip() or "/tmp/home/venue-sessions.db",
            web_access=env.get("R4_WEB_ACCESS", "").strip(),
        )


def isolation_selftest(
    *,
    connect: Callable[..., Any] = socket.create_connection,
    resolve: Callable[..., Any] = socket.getaddrinfo,
    timeout: float = 3.0,
) -> str | None:
    """D-7: return the failure reason, or None when the sandbox is isolated."""
    try:
        conn = connect(("1.1.1.1", 443), timeout=timeout)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return "direct tcp to 1.1.1.1:443 succeeded"
    except OSError:
        pass
    try:
        resolve("example.com", 443)
        return "external DNS resolved"
    except OSError:
        pass
    try:
        resolve("egress", 8080)
    except OSError:
        return "egress does not resolve"
    return None


class VenueWorker:
    """One session's sandbox loop; every collaborator is injectable."""

    def __init__(
        self,
        env: WorkerEnv,
        rail: VenueRailClient,
        *,
        token: str,
        store: Any | None = None,
        session_loader: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.env = env
        self.rail = rail
        self._token = token
        self.store = store
        self._session_loader = session_loader
        self._clock = clock
        self._sleep = sleep
        self.session: Any = None
        self._stop = False
        self._paused = False
        self._last_heartbeat = -1e9
        self._last_partial_flush = -1e9
        self._partial = ""
        # Bounded live tool-activity lines the owner sees next to the remote
        # partial text (TUI visibility, 2026-09-27). Cap 12; result events
        # update their call line in place. [] on a fresh turn (response_start).
        self._tool_activity: list[str] = []
        # The owner's agent-settings revision applied last (-1 = none yet:
        # the first read applies the local rule's defaults even at rev 0).
        self._settings_rev = -1
        self.exit_code = EXIT_OK

    # ── bootstrap ─────────────────────────────────────────────────────

    def _build_client(self, model: str) -> Any:
        """Platform verbs (workflows, jobs) ride the rail gw prefix with the
        session token; inference rides the sidecar proxy — the sandbox holds
        no JWT and no provider key either way."""
        from .venue_rail import build_rail_platform_client

        return build_rail_platform_client(
            rail_url=self.env.rail_url,
            session_id=self.env.session_id,
            token=self._token,
            endpoint_url=self.env.endpoint_url,
            model=model,
        )

    def bootstrap(self) -> Any:
        if self._session_loader is not None:
            self.session = self._session_loader()
        else:
            self.session = self._bootstrap_from_rail()
        restore_started_web_access(self.session, self.env.web_access)
        self._configure(self.session)
        return self.session

    def _snapshot_payload(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Rail snapshot → (detail, parsed payload); typed refusals on mismatch."""
        detail = self.rail.get_snapshot()
        if str(detail.get("session_id") or "") != self.env.session_id:
            raise R4Error("venue worker: snapshot for this session is not mirrored")
        try:
            payload = json.loads(detail.get("payload") or "{}")
        except json.JSONDecodeError as exc:
            raise R4Error("venue worker: snapshot payload is not JSON") from exc
        return detail, payload

    def _bootstrap_from_rail(self) -> Any:
        from .companion.session_store import CompanionSessionStore
        from .model_routing import parse_model_ref

        detail, payload = self._snapshot_payload()
        sid = self.env.session_id
        store = self.store or CompanionSessionStore(db_path=self.env.db_path)
        self.store = store
        if not store.adopt_remote_detail(sid, {**detail, "payload": json.dumps(payload)}):
            raise R4Error("venue worker: could not restore the snapshot locally")
        model = parse_model_ref(str(payload.get("model") or "")).model
        client = self._build_client(model)
        session = store.load(sid, client)
        if session is None:
            raise R4Error("venue worker: restored row did not load")
        # The sidecar is the only LLM route: a `model@source` ref cannot
        # resolve here (no sources.json) — strip to the bare model name.
        session.agent.model = model or session.agent.model
        session.metadata["venue"] = "frontend"
        return session

    def _configure(self, session: Any) -> None:
        permissions = getattr(session, "permissions", None)
        if permissions is not None and hasattr(permissions, "set_approval_callback"):
            permissions.set_approval_callback(self._approve)
        try:
            from .agent.tools import set_ask_user_callback

            set_ask_user_callback(self._ask_user)
        except Exception:  # noqa: BLE001 — optional in test doubles
            logger.debug("ask_user callback not wired", exc_info=True)
        agent = getattr(session, "agent", None)
        if agent is not None and hasattr(agent, "on_event"):
            agent.on_event(self._on_event)
        if self.store is not None and hasattr(self.store, "set_mirror_poster"):
            self.store.set_mirror_poster(self._rail_poster, getattr(session, "client", None))
        # Mirror the user row as soon as a turn starts so the TUI adopts it
        # (F7 hook — same durability window the local bridge covers).
        session._on_turn_started_hook = self._on_turn_started
        self._sync_settings()

    # ── the owner's agent settings (ADR-099 D-8) ──────────────────────

    def _read_settings(self) -> tuple[int, dict[str, Any]] | None:
        """``(rev, settings)`` from the rail; None when unreadable (kept as is).
        A refused lease (401) propagates: the loop exits on it."""
        try:
            answer = self.rail.get_settings()
        except VenueRailError as exc:
            if exc.status == 401:
                raise
            logger.warning("agent settings unreadable (%s); keeping the current ones", exc.reason)
            return None
        settings = answer.get("settings")
        rev = answer.get("rev")
        pushed = settings if isinstance(settings, dict) else {}
        return (rev if isinstance(rev, int) else 0), pushed

    def _sync_settings(self) -> bool:
        """Apply the owner's settings when their ``rev`` grew. True = applied."""
        from .companion.agent_settings import apply_agent_settings, effective_agent_settings

        read = self._read_settings()
        if read is None and self._settings_rev >= 0:
            return False
        rev, pushed = read if read is not None else (0, {})
        if rev <= self._settings_rev:
            return False
        apply_agent_settings(self.session, {**effective_agent_settings(self.session), **pushed})
        self._settings_rev = rev
        return True

    # ── rail glue ─────────────────────────────────────────────────────

    def _rail_poster(self, _url: str, json: Any = None, **_kw: Any) -> dict[str, Any]:
        body = dict(json or {})
        return self.rail.put_snapshot(
            name=str(body.get("name") or ""),
            updated_at=str(body.get("updated_at") or ""),
            payload=str(body.get("payload") or ""),
            state=str(body.get("state") or ""),
        )

    def _turn_seq(self) -> int:
        try:
            return int((getattr(self.session, "metadata", None) or {}).get("turn_seq") or 0)
        except (TypeError, ValueError):
            return 0

    def _heartbeat(self, phase: str, *, detail: str = "", partial: str | None = None) -> None:
        response = self.rail.heartbeat(
            phase=phase,
            turn_seq=self._turn_seq(),
            partial=self._partial if partial is None else partial,
            detail=detail,
            tool_activity=list(self._tool_activity),
        )
        self._last_heartbeat = self._clock()
        if response.get("stop_requested"):
            self._stop = True
        self._paused = bool(response.get("paused"))

    def _bump_marker(self) -> None:
        from .companion.session import _now_marker

        marker = getattr(self.session, "updated_at", None)
        if marker is not None:
            self.session.updated_at = _now_marker(str(marker or ""))

    def _save(self) -> None:
        if self.store is not None and self.session is not None:
            self.store.save(self.session)

    def _on_turn_started(self, _turn_id: str) -> None:
        try:
            self._save()
            self._heartbeat("running", detail="turn started")
        except Exception:  # noqa: BLE001 — never blocks a turn
            logger.debug("turn-start mirror skipped", exc_info=True)

    # ── live tool-activity (TUI visibility, 2026-09-27) ─────────────────

    _TOOL_ACTIVITY_CAP = 12
    _TOOL_ARGS_CLIP = 72

    def _tool_call_line(self, data: dict[str, Any]) -> str:
        """One bounded 'tool_name args-clip' line from a tool_call payload."""
        name = str(data.get("name") or "?")
        raw = data.get("arguments")
        summary = str(raw)
        if len(summary) > self._TOOL_ARGS_CLIP:
            summary = summary[: self._TOOL_ARGS_CLIP - 1] + "…"
        flat = summary.replace("\n", " ").strip()
        return f"▸ {name} {flat}".rstrip()

    def _on_tool_call(self, data: dict[str, Any]) -> None:
        self._tool_activity.append(self._tool_call_line(data))
        if len(self._tool_activity) > self._TOOL_ACTIVITY_CAP:
            self._tool_activity = self._tool_activity[-self._TOOL_ACTIVITY_CAP :]

    def _on_tool_result(self, data: dict[str, Any]) -> None:
        """Mark the most recent call line with its real outcome, never invented."""
        if not self._tool_activity:
            return
        status = str(data.get("status") or "")
        dur = data.get("duration_ms")
        tail = ""
        if status:
            glyph = {"ok": "✓", "done": "✓", "error": "✗", "cancelled": "⛔"}.get(status, status)
            tail = f" {glyph}"
        if isinstance(dur, (int, float)) and dur >= 0:
            tail += f" {dur / 1000:.1f}s"
        if tail:
            self._tool_activity[-1] = self._tool_activity[-1] + tail

    def _apply_event(self, event_type: str, event: Any) -> bool:
        """Fold one companion event into partial/activity state. False = ignore."""
        data = getattr(event, "data", None) or {}
        if event_type == "response_chunk":
            self._partial += str(getattr(event, "message", "") or "")
            return True
        if event_type == "tool_call":
            self._on_tool_call(data if isinstance(data, dict) else {})
            return True
        if event_type == "tool_result":
            self._on_tool_result(data if isinstance(data, dict) else {})
            return True
        return False

    def _flush_partial_tick(self) -> None:
        now = self._clock()
        if now - self._last_partial_flush < PARTIAL_FLUSH_SEC:
            return
        self._last_partial_flush = now
        try:
            self._heartbeat("running", detail="streaming")
        except VenueRailError:
            logger.debug("partial heartbeat refused", exc_info=True)

    def _on_event(self, event: Any) -> None:
        event_type = str(getattr(getattr(event, "event_type", None), "value", "") or "")
        if event_type == "response_start":
            self._partial = ""
            self._tool_activity = []
            return
        if self._apply_event(event_type, event):
            self._flush_partial_tick()

    # ── approvals / ask_user ──────────────────────────────────────────

    def _register_intent(self, tool_name: str, description: str, arguments: Any) -> str:
        """Raise the durable T4 intent through the rail; "" when refused.

        A fenced ask (fix 2026-10-30) carries ``fenced`` + ``fence_reason`` in
        the action, so the owner's card offers no a/p and the owner's bridge
        turns no "always" on it into a session grant. The callback runs
        inside the fence context, so :func:`approval_fence` names it here.
        """
        from .companion.permissions import approval_fence

        turn_id = str(getattr(self.session, "_current_turn_id", "") or "")
        fence = approval_fence()
        action = {
            "tool": tool_name,
            "description": description,
            "arguments": arguments if isinstance(arguments, dict) else {"value": str(arguments)},
            "fenced": bool(fence),
            "fence_reason": fence,
        }
        try:
            record = self.rail.request_approval(turn_id=turn_id, action=action)
        except VenueRailError as exc:
            logger.warning("approval intent refused by the rail: %s", exc.reason)
            return ""
        return str(record.get("approval_id") or "")

    def _settle_decision(self, tool_name: str, current: dict[str, Any]) -> bool:
        status = str(current.get("status") or "pending")
        decision = str(current.get("decision") or "")
        if decision == "always" and hasattr(self.session, "permissions"):
            self.session.permissions.approve_for_session(tool_name)
        self._heartbeat("running", detail=f"approval {decision or status}")
        return status == "approved"

    def _await_decision(self, approval_id: str, tool_name: str) -> bool:
        """Poll the owner's decision until decided, stop, or timeout (= denied)."""
        deadline = self._clock() + self.env.approval_timeout_sec
        while self._clock() < deadline and not self._stop:
            try:
                current = self.rail.get_approval(approval_id)
            except VenueRailError as exc:
                logger.warning("approval poll refused: %s", exc.reason)
                return False
            if str(current.get("status") or "pending") != "pending":
                return self._settle_decision(tool_name, current)
            self._sleep(DEFAULT_APPROVAL_POLL_SEC)
        self._heartbeat("running", detail="approval timed out (denied)")
        return False

    def _approve(self, tool_name: str, description: str, arguments: Any) -> bool:
        approval_id = self._register_intent(tool_name, description, arguments)
        if not approval_id:
            return False
        # The detail names the tool and the intent, never the description:
        # admin views show the runtime row, and a description carries the
        # call's arguments (a shell command, an ask_user question).
        self._quiet_heartbeat("awaiting_approval", detail=_approval_detail(tool_name, approval_id))
        return self._await_decision(approval_id, tool_name)

    def _ask_user(self, question: str, options: list[str]) -> str:
        """Ask the owner through a durable question (ADR-099 D-8); auto-answer
        only when the user's own ``ask_user_mode`` is ``auto``."""
        from .companion.agent_settings import (
            applied_agent_settings,
            ask_user_auto_answer,
            normalize_ask_user_mode,
        )

        mode = applied_agent_settings(self.session).get("ask_user_mode")
        if normalize_ask_user_mode(mode) == "auto":
            return ask_user_auto_answer()
        turn_id = str(getattr(self.session, "_current_turn_id", "") or "")
        try:
            record = self.rail.ask_question(
                turn_id=turn_id, question=_clip_question(question), options=_clip_options(options)
            )
        except VenueRailError as exc:
            logger.warning("question refused by the rail: %s", exc.reason)
            return _unasked(f"the rail refused it: {exc.reason}")
        question_id = str(record.get("question_id") or "")
        if not question_id:
            return _unasked("the rail returned no question id")
        old_client = not self._client_answers_questions()
        # The detail names the question, never its text: admin views show
        # the runtime row (ADR-099 D-8; the text is conversation content).
        # A refused heartbeat never orphans the question: the wait below
        # retries, and every exit without an answer expires it.
        self._quiet_heartbeat("awaiting_user", detail=_question_detail(question_id))
        return self._await_answer(question_id, old_client=old_client)

    def _client_answers_questions(self) -> bool:
        """False when no RS-1 client ever sent this session's settings (rev
        0): such a client predates remote questions and never shows them.
        Read without applying (a change applies from the next turn)."""
        if self._settings_rev > 0:
            return True
        try:
            read = self._read_settings()
        except VenueRailError:
            return False
        return read is not None and read[0] > 0

    def _await_answer(self, question_id: str, *, old_client: bool = False) -> str:
        """Poll until answered, closed, stop, or the timeout. A transient
        poll failure (rail unreachable, 5xx) is retried with backoff; only a
        refused lease or a vanished question ends the wait early. Every exit
        without an answer expires the question and reports ``running``."""
        timeout = (
            self.env.question_timeout_old_client_sec
            if old_client
            else self.env.question_timeout_sec
        )
        deadline = self._clock() + timeout
        delay = DEFAULT_QUESTION_POLL_SEC
        while self._clock() < deadline and not self._stop:
            try:
                current = self.rail.get_question(question_id)
            except VenueRailError as exc:
                if exc.status in _QUESTION_POLL_FATAL:
                    logger.warning("question poll refused: %s", exc.reason)
                    break
                logger.info("question poll failed (%s); retrying", exc.reason)
                self._sleep(min(delay, max(deadline - self._clock(), 0.0)))
                delay = min(delay * 2, QUESTION_POLL_MAX_BACKOFF_SEC)
                continue
            delay = DEFAULT_QUESTION_POLL_SEC
            status = str(current.get("status") or "pending")
            if status != "pending":
                return self._settle_answer(status, current)
            self._keep_alive_awaiting(question_id)
            self._sleep(DEFAULT_QUESTION_POLL_SEC)
        return self._give_up_question(question_id, old_client=old_client)

    def _settle_answer(self, status: str, record: dict[str, Any]) -> str:
        from .companion.agent_settings import ASK_USER_NO_ANSWER

        self._quiet_heartbeat("running", detail=f"question {status}")
        if status == "answered":
            return str(record.get("answer") or "")
        return ASK_USER_NO_ANSWER

    def _keep_alive_awaiting(self, question_id: str) -> None:
        """A long wait still renews the lease and notices a stop request."""
        if self._clock() - self._last_heartbeat >= self.env.heartbeat_sec:
            self._quiet_heartbeat("awaiting_user", detail=_question_detail(question_id))

    def _give_up_question(self, question_id: str, *, old_client: bool) -> str:
        """Expire the question; an answer that won the race still counts."""
        from .companion.agent_settings import ASK_USER_NO_ANSWER, ASK_USER_OLD_CLIENT

        record = self._expire_question(question_id)
        if str(record.get("status") or "") == "answered":
            return str(record.get("answer") or "")
        return ASK_USER_OLD_CLIENT if old_client else ASK_USER_NO_ANSWER

    def _expire_question(self, question_id: str) -> dict[str, Any]:
        """The server's record after the expiry ({} when refused)."""
        record: dict[str, Any] = {}
        try:
            record = self.rail.expire_question(question_id)
        except VenueRailError as exc:
            logger.warning("question expiry refused: %s", exc.reason)
        answered = str(record.get("status") or "") == "answered"
        detail = "question answered" if answered else "question expired (no answer)"
        self._quiet_heartbeat("running", detail=detail)
        return record

    def _quiet_heartbeat(self, phase: str, *, detail: str = "") -> None:
        try:
            self._heartbeat(phase, detail=detail)
        except VenueRailError:
            logger.debug("heartbeat refused", exc_info=True)

    # ── loop ──────────────────────────────────────────────────────────

    def run_once(self) -> bool:
        """One tick: heartbeat when due, pop one turn, run it. True = progressed."""
        if self._clock() - self._last_heartbeat >= self.env.heartbeat_sec:
            self._heartbeat("idle")
        if self._stop:
            return False
        # A settings change between turns applies from the next turn (ADR-099
        # D-8). Read BEFORE the pop: a refused lease (401) then exits with the
        # turn still queued instead of losing a popped one.
        self._sync_settings()
        item = self.rail.pop_queue()
        if item is None:
            return False
        self._run_turn(str(item.get("content") or ""))
        return True

    def _run_turn(self, content: str) -> None:
        self._partial = ""
        self._heartbeat("running", detail="turn accepted")
        error = ""
        try:
            self.session.send(content, stream=True)
        except Exception as exc:  # noqa: BLE001 — the loop reports, never dies
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.exception("remote turn failed")
        finally:
            try:
                # A failed or cancelled turn never reached _finish_turn: the
                # post-turn copy still outranks the turn-start one.
                self._bump_marker()
                self._save()
            except Exception:  # noqa: BLE001
                logger.exception("post-turn mirror failed")
        if error:
            self._heartbeat("error", detail=error, partial="")
        else:
            self._heartbeat("idle", detail="turn complete", partial="")
        self._partial = ""

    @staticmethod
    def _rail_exit_code(exc: VenueRailError) -> int:
        return EXIT_LEASE_REFUSED if exc.status == 401 else EXIT_BOOTSTRAP_FAILED

    def _prelude(self) -> int | None:
        """Self-test + bootstrap + ready heartbeat; an exit code when refused."""
        if not self.env.skip_selftest:
            reason = isolation_selftest()
            if reason:
                print(f"venue_isolation_failed: {reason}", file=sys.stderr)
                return EXIT_ISOLATION_FAILED
        try:
            self.bootstrap()
            self._heartbeat("idle", detail="worker ready")
        except VenueRailError as exc:
            print(f"venue worker: rail refused bootstrap ({exc.reason})", file=sys.stderr)
            return self._rail_exit_code(exc)
        except Exception as exc:  # noqa: BLE001 — every bootstrap failure is reported, not hidden
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            print(f"venue worker: bootstrap failed — {reason}", file=sys.stderr)
            self._report_exit(reason)
            return EXIT_BOOTSTRAP_FAILED
        return None

    def _report_exit(self, reason: str) -> None:
        """Best-effort `exited` heartbeat so the owner sees WHY the worker left
        (podman logs are not reachable from the client side)."""
        try:
            self.rail.heartbeat(
                phase="exited", turn_seq=self._turn_seq(), partial="", detail=reason
            )
        except Exception:  # noqa: BLE001
            logger.debug("exit report skipped", exc_info=True)

    def _wait_while_paused(self) -> None:
        """D-09 pause: wait between turns until resumed; keep the lease warm
        (heartbeats renew it) and honor stop at any moment."""
        first = True
        while self._paused and not self._stop:
            if first:
                logger.info("venue worker: paused between turns (D-09)")
                first = False
            self._sleep(self.env.poll_sec)
            try:
                self._heartbeat("paused", detail="waiting for resume")
            except VenueRailError:
                logger.debug("paused heartbeat refused", exc_info=True)

    def _loop(self) -> int:
        while not self._stop:
            self._wait_while_paused()
            if self._stop:
                break
            try:
                progressed = self.run_once()
            except VenueRailError as exc:
                if exc.status == 401:
                    print(f"venue worker: lease refused ({exc.reason}); exiting", file=sys.stderr)
                    return EXIT_LEASE_REFUSED
                logger.warning("rail error (%s); backing off", exc.reason)
                self._sleep(RAIL_BACKOFF_SEC)
                continue
            if not progressed:
                self._sleep(self.env.poll_sec)
        return EXIT_OK

    def run(self) -> int:
        refused = self._prelude()
        self.exit_code = self._loop() if refused is None else refused
        return self.exit_code


def restore_started_web_access(session: Any, spec: str) -> None:
    """A restored copy without a web choice gets the one the sandbox was
    started with (RS-1 fix W1): ``spec`` (the server's ``R4_WEB_ACCESS``),
    else the runtime row the owner recorded. Recorded on the session, so its
    web tools and the mirrored copy the owner adopts carry it; a copy with
    its own choice keeps it.
    """
    from dataclasses import replace

    from .companion.web_access import WEB_ACCESS_ENV, WebAccess, session_web_access

    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict) or WebAccess.from_metadata(metadata).chosen:
        return
    started = session_web_access(metadata, {WEB_ACCESS_ENV: spec} if spec else {})
    if started.chosen:
        metadata["web_access"] = replace(started, source="server").to_metadata()


# The rail's bounds on a question (umrs ``chat_turn_questions``): the worker
# clips to them, the way a local overlay shows a long question, instead of
# letting the rail refuse it (a 422 read as "could not reach the user").
QUESTION_MAX_CHARS = 4000
QUESTION_MAX_OPTIONS = 24
OPTION_MAX_CHARS = 500
_CLIPPED = "…"


def _clip_question(question: str) -> str:
    text = str(question or "").strip() or "(no question text)"
    if len(text) <= QUESTION_MAX_CHARS:
        return text
    return text[: QUESTION_MAX_CHARS - len(_CLIPPED)] + _CLIPPED


def _clip_options(options: Any) -> list[str]:
    cleaned = [str(o)[:OPTION_MAX_CHARS] for o in (options or []) if str(o).strip()]
    return cleaned[:QUESTION_MAX_OPTIONS]


def _approval_detail(tool_name: str, approval_id: str) -> str:
    """The heartbeat detail while an approval waits: tool + intent id only."""
    return f"{tool_name} (approval {approval_id})"[:400]


def _question_detail(question_id: str) -> str:
    """The heartbeat detail while waiting: the question's id, never its text."""
    return f"question {question_id}"[:400]


def _unasked(reason: str) -> str:
    """The tool result when the question never reached the user."""
    return (
        f"[ask_user: the question could not reach the user ({reason[:200]}); "
        "do not assume an answer: end the turn and restate the question]"
    )


def main(argv: list[str] | None = None) -> int:
    """``r4 venue worker`` / ``python -m r4.venue_worker`` entrypoint."""
    del argv
    from .venue import read_venue_token

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        env = WorkerEnv.from_environ()
    except R4Error as exc:
        print(f"venue worker: {exc}", file=sys.stderr)
        return EXIT_BOOTSTRAP_FAILED
    token = read_venue_token()
    if not token:
        print("venue worker: session token file unreadable", file=sys.stderr)
        return EXIT_BOOTSTRAP_FAILED
    rail = VenueRailClient(env.rail_url, env.session_id, token)
    worker = VenueWorker(env, rail, token=token)
    try:
        return worker.run()
    finally:
        rail.close()


if __name__ == "__main__":
    raise SystemExit(main())
