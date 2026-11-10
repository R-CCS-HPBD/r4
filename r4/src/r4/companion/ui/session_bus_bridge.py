"""The console bridge's place on the local session bus (ADR-114 D-2, D-6).

The bridge's active session is on the bus while the bridge runs: it is
registered when it becomes active (create, resume, journal adopt), the
previous one is withdrawn, and a watcher thread refreshes the heartbeat
and tells the console when the inbox changes::

    {"session_inbox": {"session_id": ..., "unread": n, "from": [names], "latest": id}}

The console shows a notice and, when the tab is idle, starts a turn so
the agent reads the messages (``session_inbox`` tool). ``R4_SESSION_BUS=0``
keeps the bridge off the bus.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from ..session_bus import HEARTBEAT_EVERY_SEC, LiveSession, SessionBus, SessionBusError, clean_label
from ..session_bus_tools import BusPresence

logger = logging.getLogger(__name__)

BUS_SWITCH_ENV = "R4_SESSION_BUS"
POLL_EVERY_SEC = 2.0


def bus_enabled() -> bool:
    return os.environ.get(BUS_SWITCH_ENV, "").strip().lower() not in ("0", "off", "false", "no")


def _entry_for(session: Any, host: str) -> LiveSession:
    from ..agent_mode import session_mode

    metadata = getattr(session, "metadata", None) or {}
    agent = getattr(session, "agent", None)
    model_of = getattr(agent, "_effective_model", None)
    return LiveSession(
        session_id=str(session.session_id),
        name=clean_label(getattr(session, "name", "") or ""),
        pid=os.getpid(),
        host=host,
        cwd=os.getcwd(),
        project=str((metadata.get("config") or {}).get("project_id") or ""),
        model=str((model_of() if callable(model_of) else "") or ""),
        mode=str(session_mode(session) or ""),
        started_at=time.time(),
        venue=str(metadata.get("venue") or "local"),
    )


class BusAttachment:
    """Keeps one bridge's active session on the bus and watches its inbox."""

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        bus: SessionBus | None = None,
    ) -> None:
        self._emit = emit
        self._bus = bus
        self._session: Any = None
        self._lock = threading.Lock()
        self._last_seen: tuple[int, str] = (0, "")
        self._last_beat = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def bus(self) -> SessionBus:
        if self._bus is None:
            self._bus = SessionBus()
        return self._bus

    def attach(self, session: Any) -> None:
        """Put ``session`` on the bus (withdrawing the previous one)."""
        with self._lock:
            previous = self._session
            if previous is session and getattr(session, "bus_presence", None) is not None:
                return
            self._withdraw(previous, keep_if=getattr(session, "session_id", None))
            if str((getattr(session, "metadata", None) or {}).get("venue") or "") == "frontend":
                session.bus_presence = None
                self._session = session
                return
            try:
                entry = _entry_for(session, self.bus.host)
                self.bus.register(entry)
                remote = self._bind_remote(session, entry)
            except (SessionBusError, OSError) as exc:
                logger.warning("session bus: could not register %s: %s", session, exc)
                return
            session.bus_presence = BusPresence(
                self.bus, entry.session_id, entry.name, remote=remote
            )
            self._session = session
            self._last_seen = (0, "")

    def _bind_remote(self, session: Any, entry: LiveSession) -> Any:
        """Bind the durable mirror when enabled; a remote outage keeps local-only service."""
        from ..session_bus_remote import for_owner_client
        from ..session_store import _snapshot_enabled

        if not _snapshot_enabled() or getattr(session, "client", None) is None:
            return None
        try:
            remote = for_owner_client(session, entry)
            remote.bind()
            return remote
        except (SessionBusError, OSError):
            logger.debug(
                "session bus: durable binding unavailable; keeping local-only", exc_info=True
            )
            return None

    def _withdraw(self, session: Any, *, keep_if: Any = None) -> None:
        if session is None:
            return
        presence = getattr(session, "bus_presence", None)
        session.bus_presence = None
        if presence is None or presence.session_id == keep_if:
            return
        try:
            if presence.bus is not None:
                self.bus.unregister(presence.session_id)
            presence.withdraw_remote()
        except (SessionBusError, OSError):
            logger.debug("session bus: unregister failed", exc_info=True)

    def detach(self) -> None:
        """Leave the bus (bridge shutdown) and stop the watcher."""
        self._stop.set()
        with self._lock:
            self._withdraw(self._session)
            self._session = None

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self._watch, daemon=True, name="bridge-session-bus")
        self._thread.start()
        return self._thread

    def _watch(self) -> None:
        while not self._stop.wait(POLL_EVERY_SEC):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the watcher never takes the bridge down
                logger.debug("session bus watcher tick failed", exc_info=True)

    def tick(self, now: float | None = None) -> dict[str, Any] | None:
        """One watcher step: heartbeat when due, then the inbox notice when
        the unread set changed. Returns the emitted payload (tests)."""
        presence = getattr(self._session, "bus_presence", None)
        if presence is None:
            return None
        now = time.monotonic() if now is None else now
        if now - self._last_beat >= HEARTBEAT_EVERY_SEC:
            presence.heartbeat()
            self._last_beat = now
        return self._notice_if_changed(presence)

    def _notice_if_changed(self, presence: BusPresence) -> dict[str, Any] | None:
        unread = presence.unread()
        latest = unread[-1].message_id if unread else ""
        seen = (len(unread), latest)
        if seen == self._last_seen:
            return None
        self._last_seen = seen
        names = sorted({m.sender_name or m.sender_id for m in unread})[:5]
        payload = {
            "session_inbox": {
                "session_id": presence.session_id,
                "unread": len(unread),
                "from": names,
                "latest": latest,
            }
        }
        self._emit(payload)
        return payload


# -- bridge hooks ---------------------------------------------------------------


def start_bridge_bus(server: Any) -> BusAttachment | None:
    """Called once from the bridge's ``main()``: create the attachment, start
    its watcher, leave the bus at exit. Off with ``R4_SESSION_BUS=0``."""
    if not bus_enabled():
        return None
    import atexit

    attachment = BusAttachment(server._emit_line)
    server._bus_attachment = attachment
    session = server.__dict__.get("_session")
    if session is not None:
        attachment.attach(session)
    attachment.start()
    atexit.register(attachment.detach)
    return attachment


def attach_bridge_session(server: Any, session: Any) -> None:
    """Called whenever a session becomes the bridge's active one."""
    attachment = server.__dict__.get("_bus_attachment")
    if attachment is not None:
        attachment.attach(session)


def detach_bridge_bus(server: Any) -> None:
    """Called at bridge shutdown."""
    attachment = server.__dict__.get("_bus_attachment")
    if attachment is not None:
        attachment.detach()
