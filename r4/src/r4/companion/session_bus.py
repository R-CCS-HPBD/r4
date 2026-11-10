"""Local session bus: live sessions and their inboxes (ADR-114 slice 1).

One directory per user, private (0700), under the r4 config home:

- ``sessions/<session_id>.json`` — one presence entry per live session
  (written by the console bridge, refreshed by its heartbeat);
- ``inbox/<session_id>/<message>.json`` — one file per unread message,
  written to a temporary name and renamed, so a reader never sees half a
  message and two writers never share a file.

A session is live while its heartbeat is fresh and, when its entry was
written on this host, its process still exists. The pid test alone is not
enough: RCCS hosts share ``HOME``, so an entry may come from another host
whose pids mean nothing here.

This is a delivery channel between one user's own sessions, not a job
store, a scheduler or a team engine (CONSTITUTION ownership table; the
companion guide's "one loop, one team engine"). This file remains local-only:
the S2.1 ``session_bus_remote`` adapter talks to UMRS's existing queue without
adding any durable state here.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BUS_ENV = "R4_SESSION_BUS_DIR"
#: A heartbeat older than this marks the entry dead (the bridge beats every 15 s).
STALE_AFTER_SEC = 90.0
HEARTBEAT_EVERY_SEC = 15.0
#: Longest message body, in characters.
MAX_BODY_CHARS = 8000
#: Unread messages one inbox holds; a send to a full inbox is refused.
MAX_UNREAD = 100
#: Shortest id prefix accepted as an address.
MIN_PREFIX = 6

_SEQ = itertools.count()
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LABEL_DROP_RE = re.compile(r"[^\w .:@/-]+")


class SessionBusError(Exception):
    """A refused bus operation; the message is safe to show the agent."""


def bus_root() -> Path:
    """The bus directory, read now: ``R4_SESSION_BUS_DIR`` else the config home."""
    raw = os.environ.get(BUS_ENV, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".config" / "r4" / "session-bus"


def clean_label(value: Any, limit: int = 60) -> str:
    """A one-line label: names reach other sessions' prompts, so no newlines,
    no markup, bounded length."""
    text = _LABEL_DROP_RE.sub(" ", str(value or "")).strip()
    return " ".join(text.split())[:limit]


def check_id(session_id: str) -> str:
    """A session id safe to use as a file name, or :class:`SessionBusError`."""
    if not isinstance(session_id, str) or not _ID_RE.match(session_id):
        raise SessionBusError(f"not a valid session id: {str(session_id)[:40]!r}")
    return session_id


@dataclass(frozen=True)
class LiveSession:
    """One live session's presence entry."""

    session_id: str
    name: str = ""
    pid: int = 0
    host: str = ""
    cwd: str = ""
    project: str = ""
    model: str = ""
    mode: str = ""
    started_at: float = 0.0
    #: Durable venue and transport marker from a verified remote binding.
    venue: str = ""
    remote: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LiveSession":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass(frozen=True)
class BusMessage:
    """One message in an inbox."""

    message_id: str
    sender_id: str
    sender_name: str
    recipient_id: str
    body: str
    sent_at: float
    reply_to: str = ""
    sender_kind: str = "session"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BusMessage":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to ``path`` atomically, readable by the user only."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    except OSError:
        return False
    return True


@dataclass
class SessionBus:
    """The bus at ``root`` (default :func:`bus_root`, read at construction)."""

    root: Path = field(default_factory=bus_root)
    host: str = field(default_factory=socket.gethostname)
    clock: Any = time.time

    # -- layout ---------------------------------------------------------------

    def _dir(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _entry_path(self, session_id: str) -> Path:
        return self._dir("sessions") / f"{check_id(session_id)}.json"

    def _inbox(self, session_id: str) -> Path:
        return self._dir("inbox", check_id(session_id))

    def ensure_private(self) -> None:
        """Create the root 0700 and tighten it when it already exists."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # -- presence -------------------------------------------------------------

    def register(self, entry: LiveSession) -> None:
        """Announce ``entry`` (overwrites the same id's previous entry)."""
        self.ensure_private()
        _write_private(self._entry_path(entry.session_id), entry.as_dict())
        self._inbox(entry.session_id)

    def heartbeat(self, session_id: str) -> None:
        """Refresh a registered entry's heartbeat (its mtime)."""
        path = self._entry_path(session_id)
        if path.exists():
            os.utime(path, None)

    def unregister(self, session_id: str) -> None:
        """Remove the entry and drop its unread messages."""
        try:
            self._entry_path(session_id).unlink(missing_ok=True)
        except SessionBusError:
            return
        self._drop_inbox(session_id)

    def _drop_inbox(self, session_id: str) -> None:
        inbox = self.root / "inbox" / session_id
        for path in inbox.glob("*.json"):
            path.unlink(missing_ok=True)
        try:
            inbox.rmdir()
        except OSError:
            pass

    def _is_live(self, path: Path, entry: LiveSession) -> bool:
        try:
            age = self.clock() - path.stat().st_mtime
        except OSError:
            return False
        if age > STALE_AFTER_SEC:
            return False
        return entry.host != self.host or _pid_alive(entry.pid)

    def live_sessions(self) -> list[LiveSession]:
        """Every live entry, oldest first; dead entries are pruned."""
        found: list[LiveSession] = []
        for path in sorted((self.root / "sessions").glob("*.json")):
            data = _read_json(path)
            entry = LiveSession.from_dict(data) if data else None
            if entry is not None and self._is_live(path, entry):
                found.append(entry)
            elif entry is not None:
                self.unregister(entry.session_id)
        return sorted(found, key=lambda e: (e.started_at, e.session_id))

    def get(self, session_id: str) -> LiveSession | None:
        """The live entry for an exact id, or None."""
        return next((e for e in self.live_sessions() if e.session_id == session_id), None)

    def resolve(self, target: str, *, exclude: str = "") -> LiveSession:
        """A live session by exact id, unique id prefix or unique name."""
        target = str(target or "").strip()
        if not target:
            raise SessionBusError("name the session to send to (id, id prefix or name)")
        live = [e for e in self.live_sessions() if e.session_id != exclude]
        exact = [e for e in live if e.session_id == target]
        return exact[0] if exact else _one_match(live, target)

    # -- messages -------------------------------------------------------------

    def send(
        self,
        *,
        sender_id: str,
        sender_name: str,
        recipient_id: str,
        body: str,
        reply_to: str = "",
        sender_kind: str = "session",
    ) -> BusMessage:
        """Put one message into a live session's inbox."""
        body = _checked_body(body)
        if self.get(recipient_id) is None:
            raise SessionBusError(f"session {recipient_id} is not running")
        inbox = self._inbox(recipient_id)
        if len(list(inbox.glob("*.json"))) >= MAX_UNREAD:
            raise SessionBusError(f"session {recipient_id}'s inbox is full ({MAX_UNREAD} unread)")
        message = BusMessage(
            message_id=f"msg-{uuid.uuid4().hex[:12]}",
            sender_id=sender_id,
            sender_name=clean_label(sender_name),
            recipient_id=recipient_id,
            body=body,
            sent_at=self.clock(),
            reply_to=str(reply_to or ""),
            sender_kind=sender_kind,
        )
        # Names sort in arrival order: nanoseconds, then this process's counter
        # (two sends inside one clock tick keep their order).
        name = f"{time.time_ns():020d}-{next(_SEQ):08d}-{message.message_id}.json"
        _write_private(inbox / name, message.as_dict())
        return message

    def _unread_paths(self, session_id: str) -> list[Path]:
        return sorted((self.root / "inbox" / check_id(session_id)).glob("*.json"))

    def unread(self, session_id: str) -> list[BusMessage]:
        """Unread messages, oldest first, left in the inbox."""
        rows = (_read_json(path) for path in self._unread_paths(session_id))
        return [BusMessage.from_dict(row) for row in rows if row]

    def unread_count(self, session_id: str) -> int:
        return len(self._unread_paths(session_id))

    def take(
        self,
        session_id: str,
        limit: int = 20,
        *,
        message_ids: set[str] | list[str] | None = None,
    ) -> list[BusMessage]:
        """Read/remove selected unread messages, oldest first (all by default)."""
        taken: list[BusMessage] = []
        wanted = set(message_ids) if message_ids is not None else None
        for path in self._unread_paths(session_id):
            if len(taken) >= max(1, limit):
                break
            row = _read_json(path)
            if wanted is not None and str((row or {}).get("message_id") or "") not in wanted:
                continue
            path.unlink(missing_ok=True)
            if row:
                taken.append(BusMessage.from_dict(row))
        return taken


def _one_match(live: list[LiveSession], target: str) -> LiveSession:
    matches = _matches(live, target)
    if len(matches) == 1:
        return matches[0]
    if matches:
        ids = ", ".join(f"{e.session_id} ({e.name})" for e in matches[:5])
        raise SessionBusError(f"{target!r} names several live sessions: {ids}")
    raise SessionBusError(
        f"no live session matches {target!r} (see session_list, or r4 session live)"
    )


def _matches(live: list[LiveSession], target: str) -> list[LiveSession]:
    if len(target) >= MIN_PREFIX:
        by_prefix = [e for e in live if e.session_id.startswith(target)]
        if by_prefix:
            return by_prefix
    return [e for e in live if e.name and e.name == target]


def _checked_body(body: Any) -> str:
    text = str(body or "").strip()
    if not text:
        raise SessionBusError("the message is empty")
    if len(text) > MAX_BODY_CHARS:
        raise SessionBusError(f"the message is longer than {MAX_BODY_CHARS} characters")
    return text
