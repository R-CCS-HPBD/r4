"""SQLite persistence for companion sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agent_mode import migrate_metadata, permission_for
from .private_db import ensure_private_db
from .session import CompanionSession, _now_marker
from .session_models import SessionConflictError as SessionConflictError  # re-exported
from .session_models import SessionMessage, metadata_project_id

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from r4.client import Client

DB_PATH = Path(
    os.environ.get("R4_COMPANION_DB")
    or os.environ.get("R4_COMPANION_DB")
    or os.path.expanduser("~/.config/r4/companion/sessions.db")
)

# Session venue grammar (plan: session-venue-selection-and-unified-listing,
# 2026-09-09). local | frontend | cluster:<name>. '' = absent (never
# fabricated). Kept in metadata; mirrored server-side per PUT.
_VENUE_RE = re.compile(r"^(local|frontend|cluster:[a-z0-9][a-z0-9-]{0,62})$")
# Recordable client states; 'archived' is derived from archived_at.
_STATES_STORABLE = frozenset({"ongoing", "stopped", "completed", "error"})
DEFAULT_VENUE = "local"


def validate_venue(value: str) -> str:
    """Validate the venue grammar; ValueError with the rule on violation."""
    if not _VENUE_RE.match(value or ""):
        raise ValueError(f"invalid venue {value!r}: expected local | frontend | cluster:<name>")
    return value


def validate_storable_state(value: str) -> str:
    """Validate a writable state ('archived' is derived, never stored)."""
    if value not in _STATES_STORABLE:
        raise ValueError(
            f"invalid state {value!r}: expected one of "
            f"{sorted(_STATES_STORABLE)} ('archived' is derived, never stored)"
        )
    return value


def computed_state(summary: dict[str, Any]) -> str:
    """One state vocabulary over local rows: archived wins, '' stays absent."""
    if summary.get("archived_at"):
        return "archived"
    return str(summary.get("state") or "")


# create_session() names every session "Session YYYY-MM-DD HH:MM" — a
# timestamp placeholder, useless for picking a session out of a list. While
# the name still matches this pattern it is considered auto-generated and
# may be replaced by a content-derived title; any other name (manual rename,
# fork suffix) is user intent and is never rewritten.
AUTO_SESSION_NAME_RE = re.compile(r"^Session \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class SessionCorruptError(ValueError):
    """A stored session row does not decode (cycle 3, L3). The row is left as is."""

    def __init__(self, session_id: str, column: str, detail: str) -> None:
        super().__init__(
            f"session {session_id} cannot be opened: its stored {column} is corrupt "
            f"({detail}); the stored row was left untouched"
        )
        self.session_id = session_id
        self.column = column
        self.hint = (
            "start a new session (/sessions new, or r4 chat without --session-id), "
            f"or save the raw row first: /sessions export {session_id}"
        )
        self.data = {"code": "session_corrupt", "session_id": session_id, "column": column}


# The row columns load() decodes, with the JSON type each must hold.
_ROW_JSON_TYPES: dict[str, type] = {
    "messages": list,
    "active_skills": list,
    "memory": dict,
    "metadata": dict,
}


_JSON_NAMES: dict[type, str] = {list: "array", dict: "object"}


def _decode_row_column(session_id: str, column: str, raw: Any) -> Any:
    """One stored JSON column, or SessionCorruptError naming the session and column."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SessionCorruptError(session_id, column, f"not JSON: {exc}") from exc
    expected = _ROW_JSON_TYPES[column]
    if not isinstance(value, expected):
        got = type(value).__name__
        raise SessionCorruptError(
            session_id, column, f"expected a JSON {_JSON_NAMES[expected]}, got {got}"
        )
    return value


def _messages_json(session: CompanionSession) -> str:
    """The stored shape of a session's messages (one serializer for save and digest)."""
    return json.dumps(
        [
            {
                "role": message.role,
                "content": message.content,
                "timestamp": message.timestamp,
                "metadata": message.metadata,
            }
            for message in session.messages
        ]
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _loaded_revision(session: Any) -> int | None:
    """The revision a live copy carries (None: never stored, or a stand-in object)."""
    value = getattr(session, "_store_revision", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_attr(session: Any, name: str) -> str:
    value = getattr(session, name, "")
    return value if isinstance(value, str) else ""


# Compare-and-swap of one live save (T1). Every SET expression reads the OLD
# row, so the revision moves only when the conversation (messages, skills,
# memory) changes: a metadata-only save (a second console opening the
# session) does not make the first console stale.
_CAS_UPDATE_SQL = """UPDATE sessions SET
    name = CASE WHEN :name <> '' THEN :name ELSE name END,
    updated_at = :updated_at,
    messages = :messages,
    active_skills = :active_skills,
    memory = :memory,
    metadata = :metadata,
    model = :model,
    revision = revision + (CASE WHEN messages IS NOT :messages
                                  OR active_skills IS NOT :active_skills
                                  OR memory IS NOT :memory THEN 1 ELSE 0 END)
  WHERE id = :id AND revision = :revision"""

_ROW_INSERT_SQL = """INSERT INTO sessions (
    id, name, created_at, updated_at, messages, active_skills, memory, metadata, model, revision)
  VALUES (:id, :name, :created_at, :updated_at, :messages, :active_skills, :memory,
          :metadata, :model, :revision)"""


def _message_role_content(message: Any) -> tuple[Any, Any]:
    """(role, content) pair from a SessionMessage or a decoded dict row."""
    if isinstance(message, dict):
        return message.get("role"), message.get("content")
    return getattr(message, "role", None), getattr(message, "content", None)


def _truncate_title(text: str, max_len: int) -> str:
    """Word-boundary truncation with ellipsis; hard cut for single tokens."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return (cut if len(cut) >= 20 else text[:max_len]) + "…"


def derive_session_title(messages: Any, *, max_len: int = 60) -> str:
    """Title from the first non-command user message ('' when none qualifies).

    Accepts SessionMessage objects or decoded dict rows. Whitespace is
    collapsed; slash commands and empty messages are skipped (a "/help"
    title identifies nothing). Long messages truncate at a word boundary
    with an ellipsis. Deterministic and LLM-free — no turn cost.
    """
    for message in messages or []:
        role, content = _message_role_content(message)
        if role != "user" or not isinstance(content, str):
            continue
        text = " ".join(content.split())
        if not text or text.startswith("/"):
            continue
        return _truncate_title(text, max_len)
    return ""


def _display_title(stored_name: Any, messages: list[dict[str, Any]]) -> str:
    """Listing name: derived title while the stored name is the placeholder.

    Read-only fallback for rows saved before auto-titling existed — the
    stored name itself is never rewritten here.
    """
    if not AUTO_SESSION_NAME_RE.match(stored_name or ""):
        return str(stored_name or "")
    return derive_session_title(messages) or str(stored_name)


def journal_prefix(client: "Client") -> str:
    """HTTP path prefix for the snapshot journal routes.

    '/gw' via a Platform Gateway, '/umrs' for direct UMRS (client property
    probes). Hard default '/umrs' when the client lacks the probe (test
    doubles) — those callers speak the server path directly.
    """
    return str(getattr(client, "_api_prefix", "") or "") or "/umrs"


_SNAPSHOT_ENABLED: bool | None = None


def _turn_marker(message: dict[str, Any]) -> Any:
    """The message's stamped turn_id (ADR-057), None when unstamped."""
    return (message.get("metadata") or {}).get("turn_id")


def _turn_seq_of_last_row(messages: list[dict[str, Any]]) -> str:
    """Final `t<N>` suffix from message metadata (the fork's next turn id)."""
    for message in reversed(messages):
        turn_id = str((message.get("metadata") or {}).get("turn_id") or "")
        if turn_id.startswith("t") and turn_id[1:].isdigit():
            return turn_id[1:]
    return ""


_FORK_DROPPED_KEYS = ("venue_runtime", "chat_queue")


def _local_fork_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """A fork runs locally (TUI audit r2 #60, 2026-10-17).

    The fork is the documented way out of a stopped frontend session
    ("…or fork the session to run locally"); copying venue=frontend and the
    parent's venue_runtime made the fork refuse the same way, or queue turns
    to a remote lane that no runtime serves. The parent's queued texts stay
    with the parent, and a state mapped from the remote runtime is dropped.
    """
    if str(metadata.get("venue") or DEFAULT_VENUE) != DEFAULT_VENUE:
        metadata.pop("state", None)
    for key in _FORK_DROPPED_KEYS:
        metadata.pop(key, None)
    metadata["venue"] = DEFAULT_VENUE
    return metadata


def _slice_messages_to_turn(messages: list[dict[str, Any]], from_turn: str) -> list[dict[str, Any]]:
    """ADR-057 D-4: prefix-slice at the matched turn (unknown id → KeyError).

    Keep every message up to and including the matched turn's own entries;
    cut at the first subsequent entry whose turn_id differs. Messages
    without any turn marker that precede the match are kept (local chatter
    before the first stamped turn); entries without markers never terminate
    a match either — slicing follows the turn ids, nothing else.
    """
    first_idx = next(
        (i for i, m in enumerate(messages) if _turn_marker(m) == from_turn),
        None,
    )
    if first_idx is None:
        raise KeyError(from_turn)
    end_idx = next(
        (
            i
            for i, m in enumerate(messages)
            if i > first_idx and _turn_marker(m) and _turn_marker(m) != from_turn
        ),
        len(messages),
    )
    return messages[:end_idx]


def _snapshot_enabled() -> bool:
    """Mirror policy (default on; ``R4_SESSION_SNAPSHOT=off`` to opt out)."""
    if _SNAPSHOT_ENABLED is not None:
        return _SNAPSHOT_ENABLED
    flag = os.environ.get("R4_SESSION_SNAPSHOT", "").strip().lower()
    return flag not in {"off", "0", "false", "disabled"}


def set_snapshot_enabled(value: bool | None) -> None:
    """Deliberate per-process override (tests / local mode). None reverts."""
    global _SNAPSHOT_ENABLED
    _SNAPSHOT_ENABLED = value


def _offset_marker(marker: str) -> str:
    """A naive marker (written before 2026-09-25 as this host's local time)
    with this host's offset attached — same instant, now comparable by the
    journal; offset-carrying or unparseable markers pass through."""
    try:
        parsed = datetime.fromisoformat(marker) if marker else None
    except ValueError:
        return marker
    if parsed is None or parsed.tzinfo is not None:
        return marker
    return parsed.astimezone().isoformat()


def _payload_metadata(raw: Any) -> dict[str, Any]:
    """A journal payload's metadata as a dict: a JSON string (save()) or, from
    older state-change mirrors and server queue writes, an object."""
    try:
        metadata = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _state_change_body(session_id: str, record: dict[str, Any], marker: str) -> dict[str, Any]:
    """Snapshot PUT body for a state change, in save()'s field shapes."""
    name = str(record.get("name") or "")
    metadata = record.get("metadata") or {}
    payload = {
        "id": session_id,
        "name": name,
        "created_at": record.get("created_at", ""),
        "updated_at": marker,
        "messages": json.dumps(record.get("messages") or []),
        "active_skills": json.dumps(record.get("active_skills") or []),
        "memory": json.dumps(record.get("memory") or {}),
        "metadata": json.dumps(metadata),
        "model": record.get("model", ""),
    }
    return {
        "name": name,
        "updated_at": marker,
        "payload": json.dumps(payload),
        "venue": str(metadata.get("venue") or DEFAULT_VENUE),
        "state": str(metadata.get("state") or ""),
    }


def _journal_refused(reply: Any) -> bool:
    """The snapshot PUT answered ``applied: false`` (the journal row is newer)."""
    return isinstance(reply, dict) and reply.get("applied") is False


class CompanionSessionStore:
    """Persist and retrieve sessions from sqlite."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else self._default_db_path()
        ensure_private_db(self.db_path)  # owner-only: it holds raw conversations
        self._init_db()
        self._sync_errors: int = 0
        self._last_sync: dict[str, str] = {"mode": "unwired", "error": ""}
        self._reconcile_last: dict[str, Any] = {}
        # One live save at a time per store: the bridge saves the same session
        # from its init thread, the turn and the auto-title worker, and each
        # save must read the revision the previous one wrote (T1).
        self._save_lock = threading.Lock()

    @staticmethod
    def _default_db_path() -> Path:
        session_module = sys.modules.get("r4.companion.session")
        if session_module is not None:
            overridden = getattr(session_module, "DB_PATH", None)
            if overridden is not None:
                return Path(overridden)
        return DB_PATH

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    messages TEXT NOT NULL DEFAULT '[]',
                    active_skills TEXT NOT NULL DEFAULT '[]',
                    memory TEXT NOT NULL DEFAULT '{}',
                    metadata TEXT NOT NULL DEFAULT '{}'
                )"""
            )
            # Add model column if missing (migration for existing DBs)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN model TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists

            # C2: pinned_at column (NULL = unpinned). Kept OUT of the metadata
            # JSON because a live session's save() rewrites that blob wholesale;
            # a dedicated column survives turns.
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN pinned_at TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

            # Cycle 3 T1: row revision for compare-and-swap saves. Existing
            # rows start at 0; a save moves it when the conversation changes.
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # column already exists

            # Delete tombstones (WP-04 G-P6-1 round 2): a mirrored row deleted
            # while the journal is unreachable must NOT reappear at the next
            # reconcile — reconcile retries the journal delete per tombstone
            # and drops it when the server confirms.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS session_delete_tombstones (
                    session_id TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL,
                    retries INTEGER NOT NULL DEFAULT 0
                )"""
            )

    def _conn(self) -> sqlite3.Connection:
        # WAL + busy_timeout (2026-09-30, chat tabs): up to 8 bridge processes
        # share this file; the default rollback journal raises SQLITE_BUSY
        # immediately under parallel turn saves. WAL lets readers and one
        # writer overlap; busy_timeout absorbs short writer bursts.
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _merge_archived_at_stamp(self, session: CompanionSession) -> None:
        """Bring ANY already-stored `archived_at` forward into the live
        session's metadata (F18 merge-semantics: never silent-stomp an
        archived()). Never fabricates one when absent."""
        if "archived_at" in session.metadata:
            return
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM sessions WHERE id = ?", (session.session_id,)
            ).fetchone()
        if row:
            try:
                prior = json.loads(row[0])
            except Exception:
                return
            stamp = prior.get("archived_at")
            if stamp:
                session.metadata["archived_at"] = stamp

    def save(self, session: CompanionSession) -> None:
        """Save a session to the database.

        Compare-and-swap (cycle 3, T1): the row is written only while its
        revision is still the one this copy was loaded at (or last saved as).
        When another console saved the conversation since, nothing is
        overwritten: this copy's unsaved conversation is kept as a separate
        session and :class:`SessionConflictError` names both.
        """
        # F18 audit fix (2026-09-21): merge the remote-stamped `archived_at`
        # into THIS live session's metadata BEFORE the upsert (an archive
        # stamped by another process must never be stomped by a live save's
        # own fresh `json.dumps(session.metadata)`).
        self._merge_archived_at_stamp(session)
        with self._save_lock:
            row = self._row_for_save(session)
            copy_id = self._write_row(session, row)
        if copy_id is not None:
            raise SessionConflictError(session.session_id, copy_id=copy_id)
        # ADR-036 F2 (WP-04 first leg): mirror the save to the server journal
        self._mirror_snapshot(
            session, row["messages"], row["active_skills"], row["memory"], row["metadata"]
        )

    def save_subagent(
        self,
        *,
        session_id: str,
        name: str,
        model: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        """Write one recorder-owned child row locally, never through the parent rail.

        Ordinary live-session loading and listing refuse these rows until S3/S4
        expose read-only inspection. The execution's single writer supplies the
        full message sequence, so an atomic upsert is sufficient without
        manufacturing a writable ``CompanionSession`` or invoking mirror hooks.
        """
        required = ("parent_session_id", "parent_agent_id", "agent_id")
        if metadata.get("session_kind") != "subagent" or not all(metadata.get(k) for k in required):
            raise ValueError("subagent rows require session_kind and complete parent lineage")
        with self._save_lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT updated_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            updated_at = _now_marker(str(previous[0] or "")) if previous else _now_marker()
            conn.execute(
                """INSERT INTO sessions (
                    id, name, created_at, updated_at, messages, active_skills,
                    memory, metadata, model, revision)
                VALUES (?, ?, ?, ?, ?, '[]', '{}', ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, updated_at = excluded.updated_at,
                    messages = excluded.messages, metadata = excluded.metadata,
                    model = excluded.model, revision = sessions.revision + 1""",
                (
                    session_id,
                    name,
                    updated_at,
                    updated_at,
                    json.dumps(messages),
                    json.dumps(metadata),
                    model or "",
                ),
            )

    def _row_for_save(self, session: CompanionSession) -> dict[str, Any]:
        """The row one save writes (named parameters of the CAS statements)."""
        session.updated_at = _offset_marker(session.updated_at)
        session._sync_metadata_usage_state()
        _sync_untrusted_mark(session)
        # Auto-title: while the name is still the create-time placeholder,
        # replace it with a title derived from the first user message. A
        # renamed/forked name no longer matches the pattern, so user intent
        # is never rewritten. Runs before the upsert AND the mirror so both
        # stores pick up the title on the same save.
        if AUTO_SESSION_NAME_RE.match(session.name or ""):
            title = derive_session_title(session.messages)
            if title:
                session.name = title
        config = getattr(session.client, "config", None)
        session.metadata["config"] = {
            "profile": self._config_attr(config, "profile", "default"),
            "gateway_url": self._config_attr(config, "gateway_url", ""),
            "project_id": self._config_attr(config, "project_id", ""),
        }
        return {
            "id": session.session_id,
            "name": session.name,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": _messages_json(session),
            "active_skills": json.dumps(list(session.skills._active)),
            # ADR-111 M0: saved memory lives in its own files, never in a
            # session row (the column stays for older readers, always {}).
            "memory": "{}",
            "metadata": json.dumps(session.metadata),
            "model": session.agent.model or "",
        }

    def _write_row(self, session: CompanionSession, row: dict[str, Any]) -> str | None:
        """CAS-write one row; None when written, else the conflict copy's id ("" = none)."""
        with self._conn() as conn:
            # IMMEDIATE: the revision read and the write share one write lock,
            # so a second bridge cannot slip a save in between.
            conn.execute("BEGIN IMMEDIATE")
            # Upsert WITHOUT touching pinned_at (C2-class). F18 audit fix
            # (2026-09-21): same class: name+archived_at are metadata-merge
            # fields — a rename/archived_at stamped from another process must
            # survive whatever live process next saves.
            revision = self._swap_row(conn, _loaded_revision(session), row)
            if revision is None:
                return self._keep_conflict_copy(conn, session, row)
        session._store_revision = revision
        session._store_digest = _digest(row["messages"])
        return None

    def _swap_row(
        self, conn: sqlite3.Connection, expected: int | None, row: dict[str, Any]
    ) -> int | None:
        """The row's new revision, or None when another writer moved it."""
        if expected is not None:
            swapped = conn.execute(_CAS_UPDATE_SQL, {**row, "revision": expected}).rowcount
            if swapped:
                return self._stored_revision(conn, row["id"])
        if self._stored_revision(conn, row["id"]) is not None:
            return None  # stale copy, or a fresh object over an existing row
        # First save of a new session (or of one deleted elsewhere meanwhile).
        conn.execute(_ROW_INSERT_SQL, {**row, "revision": 1})
        return 1

    @staticmethod
    def _stored_revision(conn: sqlite3.Connection, session_id: str) -> int | None:
        found = conn.execute("SELECT revision FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return None if found is None else int(found[0] or 0)

    def _keep_conflict_copy(
        self, conn: sqlite3.Connection, session: CompanionSession, row: dict[str, Any]
    ) -> str:
        """Keep the losing writer's conversation as its own local session.

        Only when it holds something the store never saw (its messages differ
        from what it loaded or last saved). Returns the copy's id, "" when
        nothing needed keeping.

        The copy is an ordinary session once written: the user may resume it
        and go on there while this stale copy lives on in its tab. A later
        failing save therefore never rewrites a copy that moved on: nothing
        new leaves it alone, and new messages update it only by its own
        compare-and-swap, else go to a fresh copy (cycle 3 review: a stale
        tab's exit save erased the turns continued in its copy).
        """
        digest = _digest(row["messages"])
        if digest == _str_attr(session, "_store_digest"):
            return ""
        copy_id = _str_attr(session, "_conflict_copy_id")
        if copy_id and digest == _str_attr(session, "_conflict_copy_digest"):
            # Already kept; the copy (resumed, renamed, deleted) is the user's now.
            return copy_id if self._stored_revision(conn, copy_id) is not None else ""
        revision = self._swap_copy(conn, session, copy_id, row) if copy_id else None
        if revision is None:
            copy_id, revision = self._insert_copy(conn, session, row), 1
        session._conflict_copy_id = copy_id
        session._conflict_copy_revision = revision
        session._conflict_copy_digest = digest
        return copy_id

    def _swap_copy(
        self, conn: sqlite3.Connection, session: CompanionSession, copy_id: str, row: dict[str, Any]
    ) -> int | None:
        """Update the copy in place while it is still as this copy left it
        (its name and an archive stamp, which the user may have set, are
        kept, like save() keeps them); None otherwise."""
        expected = getattr(session, "_conflict_copy_revision", None)
        if not isinstance(expected, int):
            return None
        metadata = self._copy_metadata(session, self._stored_archived_at(conn, copy_id))
        swapped = conn.execute(
            """UPDATE sessions SET updated_at = :updated_at, messages = :messages,
                   active_skills = :active_skills, memory = :memory, metadata = :metadata,
                   model = :model, revision = revision + 1
                 WHERE id = :id AND revision = :revision""",
            {**row, "id": copy_id, "metadata": metadata, "revision": expected},
        ).rowcount
        return expected + 1 if swapped else None

    @staticmethod
    def _stored_archived_at(conn: sqlite3.Connection, session_id: str) -> str:
        """The row's stored archive stamp ("" when none or undecodable).

        An archive does not move the revision, so a copy archived since this
        tab wrote it still passes the compare-and-swap (cycle 3 verifier r2)."""
        found = conn.execute("SELECT metadata FROM sessions WHERE id = ?", (session_id,)).fetchone()
        try:
            stamp = json.loads(found[0] or "{}").get("archived_at") if found else ""
        except (TypeError, ValueError, AttributeError):
            return ""
        return stamp if isinstance(stamp, str) else ""

    def _insert_copy(
        self, conn: sqlite3.Connection, session: CompanionSession, row: dict[str, Any]
    ) -> str:
        """Write a new conflict copy; its id."""
        copy_id = str(uuid.uuid4())
        conn.execute(
            _ROW_INSERT_SQL,
            {
                **row,
                "id": copy_id,
                "name": f"{session.name or 'session'} (unsaved copy)",
                "metadata": self._copy_metadata(session),
                "revision": 1,
            },
        )
        return copy_id

    @staticmethod
    def _copy_metadata(session: CompanionSession, archived_at: str = "") -> str:
        metadata = _local_fork_metadata(dict(session.metadata))
        metadata.pop("_mirror_marker", None)
        metadata["conflict_copy_of"] = session.session_id
        if archived_at:
            metadata["archived_at"] = archived_at
        return json.dumps(metadata)

    def ensure_current(self, session: CompanionSession) -> None:
        """Refuse up front when another console saved this conversation since
        this copy was loaded (a turn would otherwise run, then fail to save)."""
        expected = _loaded_revision(session)
        if expected is None:
            return
        with self._conn() as conn:
            stored = self._stored_revision(conn, session.session_id)
        if stored is not None and stored != expected:
            raise SessionConflictError(session.session_id)

    def _record_mirror_marker(self, session: CompanionSession) -> None:
        """Persist last-mirrored update_marker (3-way base for reconcile())."""
        self._write_mirror_marker(session.session_id, session.updated_at)
        session.metadata["_mirror_marker"] = session.updated_at

    def _write_mirror_marker(self, session_id: str, marker: str) -> None:
        """UPDATE-only write of the base marker (lives outside the payload)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return
            metadata = self._decode_json_object(row[0])
            metadata["_mirror_marker"] = marker
            conn.execute(
                "UPDATE sessions SET metadata = ? WHERE id = ?",
                (json.dumps(metadata), session_id),
            )

    @staticmethod
    def _ts_newer(a: str, b: str) -> bool:
        """a strictly newer than b — ISO parse first, never throws."""
        if not a:
            return False
        if not b:
            return bool(a)
        try:
            pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
            pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        except ValueError:
            return a > b
        # Rows written before 2026-09-25 carry naive local time; markers now
        # carry an offset. Naive reads as this host's local time (the writer's
        # clock for its own rows) so a mixed pair compares instead of raising.
        return pa.astimezone() > pb.astimezone()

    def _mirror_snapshot(
        self,
        session: CompanionSession,
        messages_json: str,
        active_skills_json: str,
        memory_json: str,
        metadata_json: str,
    ) -> None:
        """Write-through mirror to `/umrs/companion/sessions/{id}/snapshot`.

        Policy: mirroring failures must NEVER break a save(). They count
        against sync_status(mode="degraded") so the operator sees it.
        Off by default via R4_SESSION_SNAPSHOT=off.
        """
        if not _snapshot_enabled():
            self._last_sync = {"mode": "local", "error": ""}
            return
        client = getattr(session, "client", None)
        # A registered mirror poster wins (the venue worker registers the rail's
        # put_snapshot): the sandbox client's own post() targets a gateway path
        # the rail refuses, so the reply never reached the journal (live
        # 2026-09-25 receipt: turn complete, journal empty, 403 on the POST).
        poster = getattr(self, "_mirror_poster", None) or getattr(client, "post", None)
        if poster is None:
            self._last_sync = {"mode": "unwired", "error": ""}
            return
        marker = session.updated_at
        try:
            model = getattr(getattr(session, "agent", None), "model", "") or ""
            blob = json.dumps(
                {
                    "id": session.session_id,
                    "name": session.name,
                    "created_at": session.created_at,
                    "updated_at": marker,
                    "messages": messages_json,
                    "active_skills": active_skills_json,
                    "memory": memory_json,
                    "metadata": metadata_json,
                    "model": model,
                }
            )
            prefix = journal_prefix(client)
            reply = poster(
                f"{prefix}/companion/sessions/{session.session_id}/snapshot",
                json={
                    "name": session.name,
                    "updated_at": marker,
                    "payload": blob,
                    "venue": str(session.metadata.get("venue") or DEFAULT_VENUE),
                    "state": str(session.metadata.get("state") or ""),
                },
            )
            if _journal_refused(reply):
                # The journal kept a newer row: this save is NOT mirrored, and
                # recording its marker as the sync base would let reconcile()
                # adopt the older server copy over it.
                self._sync_errors += 1
                self._last_sync = {
                    "mode": "degraded",
                    "error": "journal kept a newer snapshot",
                    "journal_newer": True,
                }
                return
            self._sync_errors = 0
            self._last_sync = {"mode": "mirrored", "error": ""}
            self._record_mirror_marker(session)
        except Exception as exc:  # noqa: BLE001 — mirror must not break save()
            self._sync_errors += 1
            self._last_sync = {"mode": "degraded", "error": str(exc)[:200]}

    def adopt_newer_journal(self, session: CompanionSession, client: "Client"):
        """After a save the journal refused as older, adopt the journal row.

        Server wins: the journal holds a newer copy (a remote turn this client
        never watched), and a venue worker would restore that copy anyway.
        Returns the reloaded session, or None when there is nothing to adopt.
        """
        if not self._last_sync.get("journal_newer"):
            return None
        sid = str(getattr(session, "session_id", "") or "")
        try:
            detail = client.get(f"{journal_prefix(client)}/companion/sessions/{sid}") or {}
        except Exception:  # noqa: BLE001 — journal optional; caller reports degraded
            return None
        if not isinstance(detail, dict) or not self.adopt_remote_detail(sid, detail):
            return None
        return self.load(sid, client)

    def sync_status(self) -> dict[str, Any]:
        """Local/mirrored/degraded mirror state + accumulated mirror errors."""
        return {
            "mode": self._last_sync.get("mode", "unwired"),
            "error": self._last_sync.get("error", ""),
            "sync_errors": self._sync_errors,
            "reconcile": dict(self._reconcile_last),
            # A refused PUT (the journal holds a newer copy, e.g. a remote turn
            # never watched here) — adoptable, unlike an unreachable journal.
            "journal_newer": bool(self._last_sync.get("journal_newer")),
        }

    def rebuild_from_remote(self, client: "Client") -> int:
        """Restore local rows from the server snapshot journal.

        Owner-pinned reads complement the mirror; existing local rows are
        left untouched (server is authoritative only for absent rows).
        """
        summaries = self._remote_snapshot_summaries(client)
        if summaries is None:
            return 0
        rebuilt = 0
        with self._conn() as conn:
            for summary in summaries:
                sid = str(summary.get("session_id") or "")
                if sid:
                    rebuilt += self._restore_snapshot_row(conn, client, sid)
        return rebuilt

    # ── Reconcile (WP-04 follow-on: full read-model round 1) ───────────────

    def reconcile(self, client: "Client") -> dict[str, int] | None:
        """Full read-model pass against the journal (last-writer-wins).

        3-way against the persisted ``_mirror_marker`` base:
          * remote-only ids → restored (WP-04 behaviour)
          * server moved only → adopt server row
          * local moved only → re-mirror local row up
          * both moved → CONFLICT, newest timestamp wins, entry recorded
          * server-archived adoption → local archived_at stamped (clean only)
        Local-only ids are NEVER deleted (creator-side content stays).

        Returns counters, or None when the journal is unavailable (the
        degradation is recorded in sync_status, never silent).
        """
        try:
            summaries = self._remote_snapshot_summaries(client)
        except Exception:  # noqa: BLE001 — journal optional
            summaries = None
        if summaries is None:
            self._sync_errors += 1
            self._last_sync = {"mode": "degraded", "error": "journal unreachable"}
            return None
        counters = {
            "restored": 0,
            "adopted": 0,
            "reuploaded": 0,
            "archives_adopted": 0,
            "conflicts": 0,
            "skipped": 0,
        }
        with self._conn() as conn:
            local = self._local_row_markers(conn)
            for snap in summaries:
                self._reconcile_row(conn, client, snap, local, counters)
        self._reconcile_last = {
            **counters,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        return counters

    @staticmethod
    def _local_row_markers(conn) -> dict[str, tuple[str, dict[str, Any]]]:
        """id → (updated_at, metadata) for the reconcile pass."""
        markers: dict[str, tuple[str, dict[str, Any]]] = {}
        for row in conn.execute("SELECT id, updated_at, metadata FROM sessions").fetchall():
            sid, updated, metadata_raw = row
            try:
                metadata = json.loads(metadata_raw or "{}")
            except json.JSONDecodeError:
                metadata = {}
            markers[str(sid)] = (str(updated or ""), metadata)
        return markers

    def _reconcile_row(
        self,
        conn,
        client: "Client",
        snap: dict[str, Any],
        local: dict[str, tuple[str, dict[str, Any]]],
        counters: dict[str, int],
    ) -> None:
        """One remote snapshot vs the local row (counters mutated in place)."""
        sid = str(snap.get("session_id") or "")
        if not sid:
            return
        row = local.get(sid)
        if row is None:
            # WP-04 G-P6-1 round 2: tombstoned deletes never resurrect.
            if sid in self._tombstoned_ids(conn):
                try:
                    self._delete_journal_row(client, sid)
                    conn.execute(
                        "DELETE FROM session_delete_tombstones WHERE session_id = ?",
                        (sid,),
                    )
                    counters["tombstones_resolved"] = counters.get("tombstones_resolved", 0) + 1
                except Exception:  # noqa: BLE001 — journal optional, retry next pass
                    conn.execute(
                        (
                            "UPDATE session_delete_tombstones SET retries = retries + 1"
                            " WHERE session_id = ?"
                        ),
                        (sid,),
                    )
                return
            counters["restored"] += self._restore_snapshot_row(conn, client, sid)
            return
        local_updated, metadata = row
        base = str(metadata.get("_mirror_marker") or "")
        remote_marker = str(snap.get("updated_at") or "")
        if self._try_archive_adoption(conn, snap, sid, local_updated, base, metadata, counters):
            return
        moved_remote = remote_marker != base
        moved_local = local_updated != base
        if moved_remote and moved_local:
            self._reconcile_conflict(conn, client, sid, local_updated, remote_marker, counters)
            return
        self._reconcile_clean(conn, client, sid, moved_remote, moved_local, counters)

    def _reconcile_clean(
        self,
        conn,
        client: "Client",
        sid: str,
        moved_remote: bool,
        moved_local: bool,
        counters: dict[str, int],
    ) -> None:
        """No-drift branches: identical → skip; server-only → adopt; local-only → re-up."""
        if not moved_remote and not moved_local:
            counters["skipped"] += 1
        elif moved_remote:
            counters["adopted"] += self._apply_snapshot_row(conn, client, sid)
        else:
            counters["reuploaded"] += self._mirror_row_up(conn, client, sid)

    def _try_archive_adoption(
        self,
        conn,
        snap: dict[str, Any],
        sid: str,
        local_updated: str,
        base: str,
        metadata: dict[str, Any],
        counters: dict[str, int],
    ) -> bool:
        """Server tombstone adoption on clean rows only. True when handled."""
        remote_archived = str(snap.get("archived_at") or "") != "" or (
            str(snap.get("state") or "") == "archived"
        )
        if not (remote_archived and not metadata.get("archived_at") and local_updated == base):
            return False
        self._adopt_archive_row(conn, sid, str(snap.get("archived_at") or ""), metadata)
        counters["archives_adopted"] += 1
        return True

    def _adopt_archive_row(
        self, conn, sid: str, archived_at: str, metadata: dict[str, Any]
    ) -> None:
        """Adopt a server-side tombstone: stamp archived_at locally (advisory)."""
        stamp = archived_at or datetime.now(timezone.utc).isoformat()
        metadata = dict(metadata)
        metadata["archived_at"] = stamp
        conn.execute(
            "UPDATE sessions SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), sid),
        )

    def _reconcile_conflict(
        self,
        conn,
        client: "Client",
        sid: str,
        local_updated: str,
        remote_marker: str,
        counters: dict[str, int],
    ) -> None:
        """Both moved: newest timestamp wins; the entry is RECORDED (never silent)."""
        if self._ts_newer(remote_marker, local_updated):
            counters["adopted"] += self._apply_snapshot_row(conn, client, sid)
            winner = "server"
        else:
            counters["reuploaded"] += self._mirror_row_up(conn, client, sid)
            winner = "local"
        counters["conflicts"] += 1
        self._record_conflict_entry(conn, sid, local_updated, remote_marker, winner)

    def _record_conflict_entry(
        self, conn, sid: str, local_ts: str, remote_ts: str, winner: str
    ) -> None:
        """Append a surface-visible conflict record to the row's metadata."""
        row = conn.execute("SELECT metadata FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return
        metadata = self._decode_json_object(row[0])
        entries = [e for e in metadata.get("_mirror_conflicts", []) if isinstance(e, dict)]
        entries.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "local_updated_at": local_ts,
                "remote_update_marker": remote_ts,
                "winner": winner,
            }
        )
        metadata["_mirror_conflicts"] = entries[-20:]
        conn.execute(
            "UPDATE sessions SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), sid),
        )

    def _remote_snapshot_summaries(self, client: "Client") -> list[dict[str, Any]] | None:
        """Fetch the summary list; None means mirroring is off/unavailable."""
        if not _snapshot_enabled():
            return None
        sync = getattr(client, "get", None)
        if sync is None:
            return None
        listing = sync(f"{journal_prefix(client)}/companion/sessions") or {}
        return listing.get("snapshots") or []

    @classmethod
    def _adopt_journal_fields(cls, payload: dict[str, Any], detail: dict[str, Any]) -> None:
        """An adopted payload carries the journal's LWW marker as its row marker.

        The journal orders by update_marker (detail.updated_at); a payload's own
        updated_at can trail it (state-change mirrors before 2026-09-25 posted
        now() as the marker). Keeping the payload's older value made every
        re-save of the adopted copy lose to the marker it was adopted at.
        """
        marker = str(detail.get("updated_at") or "")
        if marker:
            payload["updated_at"] = marker
        payload["metadata"] = cls._snapshot_metadata(body=payload, detail=detail)

    @staticmethod
    def _snapshot_metadata(*, body: dict[str, Any], detail: dict[str, Any]) -> str:
        """Venue registry (0084): fold server venue/state into payload metadata.

        Archived server rows restore with archived_at stamped; otherwise a
        recorded state (stopped/completed/error) restores as metadata.state.
        Absent fields are never fabricated.
        """
        metadata = _payload_metadata(body.get("metadata"))
        # The adopted copy IS the journal's: its marker is the 3-way base, so a
        # reconcile right after an adoption is clean (a missing base made
        # local == remote read as a both-moved tie that re-uploaded).
        marker = str(detail.get("updated_at") or body.get("updated_at") or "")
        if marker:
            metadata["_mirror_marker"] = marker
        if detail.get("venue"):
            metadata["venue"] = str(detail["venue"])
        if str(detail.get("state") or "") == "archived":
            metadata["archived_at"] = str(detail.get("archived_at") or "")
        elif detail.get("state"):
            metadata["state"] = str(detail["state"])
        return json.dumps(metadata)

    def pull_session(self, client: "Client", session_id: str) -> str:
        """Pull one remote-only session into the local store (plan 2026-09-09 P5).

        Returns: 'pulled' | 'already-local' | 'not-found' | 'unavailable'.
        Local rows always win: an existing id is never overwritten.
        """
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                return "already-local"
            try:
                restored = self._restore_snapshot_row(conn, client, session_id)
            except Exception:  # noqa: BLE001 — journal optional
                return "unavailable"
        return "pulled" if restored else "not-found"

    def _restore_snapshot_row(self, conn, client: "Client", sid: str) -> int:
        """Insert one absent local row from its snapshot; 0 if present/skipped."""
        if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone():
            return 0
        detail = client.get(f"{journal_prefix(client)}/companion/sessions/{sid}") or {}
        # Absent rows (404-shaped empty dict) never insert an empty shell.
        if str(detail.get("session_id") or "") != sid:
            return 0
        try:
            payload = json.loads(detail.get("payload") or "{}")
        except json.JSONDecodeError:
            return 0
        self._adopt_journal_fields(payload, detail)
        self._insert_snapshot_row(conn, sid, payload, fallback_name=str(detail.get("name") or ""))
        return 1

    @staticmethod
    def _insert_snapshot_row(
        conn, sid: str, payload: dict[str, Any], *, fallback_name: str = ""
    ) -> None:
        """One shape, one upsert (restore/adopt/modify-from-server all ride it).

        F4 audit fix (2026-09-21): pinned_at must survive adoptions — the
        whole-point of it as a column was that the user-visible pin stays.
        Preserved across every repair path via the same exclusion rule used
        by save()-side upserts.
        """
        previous = conn.execute(
            "SELECT pinned_at, revision FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        preserved_pin = previous[0] if previous and previous[0] else None
        # An adoption replaces the conversation: a live copy loaded before it
        # is stale from here on (T1), so its next save cannot undo it.
        revision = int(previous[1] or 0) + 1 if previous else 0
        payload = {
            **payload,
            **{
                key: json.dumps(payload[key])
                for key in ("messages", "active_skills", "memory", "metadata")
                if key in payload and not isinstance(payload[key], str)
            },
        }
        conn.execute(
            """INSERT OR REPLACE INTO sessions
               (id, name, created_at, updated_at, messages,
                active_skills, memory, metadata, model, pinned_at, revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                payload.get("name", fallback_name),
                payload.get("created_at", ""),
                payload.get("updated_at", ""),
                payload.get("messages", "[]"),
                payload.get("active_skills", "[]"),
                payload.get("memory", "{}"),
                payload.get("metadata", "{}"),
                payload.get("model", ""),
                preserved_pin or payload.get("pinned_at", None),
                revision,
            ),
        )

    def adopt_remote_detail(self, sid: str, detail: dict[str, Any]) -> bool:
        """Server-wins upsert from an already-fetched journal detail row.

        Used by the frontend-venue rails (worker bootstrap, TUI sync): the
        caller holds the detail from its own transport, so no second GET.
        Absent/mismatched rows never insert (same rule as the pull path).
        """
        if str(detail.get("session_id") or "") != sid:
            return False
        try:
            payload = json.loads(detail.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        self._adopt_journal_fields(payload, detail)
        with self._conn() as conn:
            self._insert_snapshot_row(
                conn, sid, payload, fallback_name=str(detail.get("name") or "")
            )
        return True

    def _apply_snapshot_row(self, conn, client: "Client", sid: str) -> int:
        """Server-wins overwrite of an EXISTING row (reconcile adoptions)."""
        detail = client.get(f"{journal_prefix(client)}/companion/sessions/{sid}") or {}
        if str(detail.get("session_id") or "") != sid:
            return 0
        try:
            payload = json.loads(detail.get("payload") or "{}")
        except json.JSONDecodeError:
            return 0
        self._adopt_journal_fields(payload, detail)
        self._insert_snapshot_row(conn, sid, payload, fallback_name=str(detail.get("name") or ""))
        return 1

    def _mirror_row_up(self, conn, client: "Client", sid: str) -> int:
        """Local-wins re-mirror of one row to the journal (reconcile uploads)."""
        row = conn.execute(
            "SELECT name, created_at, updated_at, messages, active_skills, memory, metadata, model"
            " FROM sessions WHERE id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            return 0
        name, created_at, updated_at, messages, skills, memory, metadata, model = row
        meta_obj = self._decode_json_object(metadata)
        blob = json.dumps(
            {
                "id": sid,
                "name": name,
                "created_at": created_at,
                "updated_at": updated_at,
                "messages": messages,
                "active_skills": skills,
                "memory": memory,
                "metadata": metadata,
                "model": model,
            }
        )
        poster = getattr(client, "post", None)
        if poster is None:
            return 0
        try:
            reply = poster(
                f"{journal_prefix(client)}/companion/sessions/{sid}/snapshot",
                json={
                    "name": name,
                    "updated_at": updated_at,
                    "payload": blob,
                    "venue": str(meta_obj.get("venue") or DEFAULT_VENUE),
                    "state": str(meta_obj.get("state") or ""),
                },
            )
        except Exception:
            return 0
        if _journal_refused(reply):
            return 0
        self._write_mirror_marker(sid, updated_at)
        return 1

    def load(self, session_id: str, client: "Client") -> CompanionSession | None:
        """Load a session from the database.

        A row that does not decode raises :class:`SessionCorruptError` naming
        the session, the column and the recovery (cycle 3, L3: initialize
        failed with a bare "Expecting value: line 1 …"); the row is never
        rewritten here.
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, name, created_at, updated_at, messages,
                    active_skills, memory, metadata, model, revision
                   FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()

        if not row:
            return None

        sid, name, created, updated = row[:4]
        decoded = {
            column: _decode_row_column(str(sid), column, raw)
            for column, raw in zip(("messages", "active_skills", "memory", "metadata"), row[4:8])
        }
        saved_model, revision = row[8], row[9]
        if decoded["metadata"].get("session_kind") == "subagent":
            return None  # child inspection is read-only (S3); never construct a writer
        try:
            session = self._session_from_row(client, sid, name, saved_model, decoded)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise SessionCorruptError(str(sid), "row", f"{type(exc).__name__}: {exc}") from exc
        session.created_at = created
        session.updated_at = updated
        session._store_revision = int(revision or 0)
        session._store_digest = _digest(_messages_json(session))
        return session

    def _session_from_row(
        self,
        client: "Client",
        sid: str,
        name: str,
        saved_model: str,
        decoded: dict[str, Any],
    ) -> CompanionSession:
        """Build the live session from decoded columns (raises on bad shapes)."""
        metadata = decoded["metadata"]
        # ADR-104 D-1: the permission mode follows the session's one mode
        # (retired permission_override / auto_approve_tools folded in), so a
        # resumed autopilot session is TRUST with its server checker wired.
        permission_mode = permission_for(migrate_metadata(metadata))
        session = CompanionSession(
            session_id=sid,
            name=name,
            client=client,
            # Use the saved model if available, otherwise fall back to client config
            model=saved_model or None,
            permission_mode=permission_mode,
        )
        self._restore_messages(session, decoded["messages"])
        self._restore_skills(session, decoded["active_skills"])
        # ADR-111 M0: an older row's memory snapshot is ignored; restoring it
        # brought back facts deleted since and wrote them out for everyone.
        session.metadata = metadata
        _sync_untrusted_mark(session)
        session._load_usage_state_from_metadata()
        session.reload_permission_rules()  # the restored project's rule file
        from .subagent_sessions import bind_subagent_recorder

        bind_subagent_recorder(session, self)
        return session

    def _restore_messages(self, session: CompanionSession, messages: list[Any]) -> None:
        for message in messages:
            session.messages.append(
                SessionMessage(
                    role=message["role"],
                    content=message["content"],
                    timestamp=message.get("timestamp", ""),
                    metadata=message.get("metadata", {}),
                )
            )

    def _restore_skills(self, session: CompanionSession, active_skills: list[Any]) -> None:
        for skill_name in active_skills:
            session.skills.activate(skill_name)

    def raw_row(self, session_id: str) -> dict[str, Any] | None:
        """The stored columns exactly as written (the export of a corrupt row)."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            found = conn.execute(
                """SELECT id, name, created_at, updated_at, model, revision, messages,
                    active_skills, memory, metadata FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()
        return dict(found) if found is not None else None

    def list_sessions(
        self,
        limit: int = 50,
        *,
        include_archived: bool = False,
        endpoint: str | None = None,
        project: str | None = None,
        venue: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """List sessions: pinned first (newest pin first), then most recently updated.

        venue/state compose with the existing filters: venue matches the
        recorded venue (local | frontend | cluster:<name>); state matches the
        computed state (archived wins over recorded; '' matches unrecorded).
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, name, created_at, updated_at, model, messages, metadata, pinned_at
                   FROM sessions
                   ORDER BY (pinned_at IS NOT NULL) DESC, pinned_at DESC, updated_at DESC""",
            ).fetchall()
        sessions: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._decode_json_object(row[6])
            summary = self._summary_from_row(row, metadata=metadata)
            if not self._summary_accepted_summary(summary, include_archived, endpoint, project):
                continue
            if venue is not None and summary["venue"] != venue:
                continue
            if state is not None and computed_state(summary) != state:
                continue
            sessions.append(summary)
            if limit > 0 and len(sessions) >= limit:
                break
        return sessions

    @classmethod
    def _summary_accepted_summary(
        cls,
        summary: dict[str, Any],
        include_archived: bool,
        endpoint: str | None,
        project: str | None,
    ) -> bool:
        """One-line predicate drawing the C2+ filter contract out of the loop."""
        if summary["session_kind"] == "subagent":
            return False  # child rows are never ordinary writable sessions
        if not include_archived and summary["archived_at"]:
            return False
        if endpoint and not cls._matches_endpoint(summary["endpoints"], endpoint):
            return False
        if project and summary["project_id"] != project:
            return False
        return True

    def get_session_record(self, session_id: str) -> dict[str, Any] | None:
        """Return a stored session record without constructing a live session."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, name, created_at, updated_at, model, messages,
                    active_skills, memory, metadata, pinned_at
                   FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        metadata = self._decode_json_object(row[8])
        return {
            **self._summary_from_row(
                (row[0], row[1], row[2], row[3], row[4], row[5], row[8], row[9]), metadata=metadata
            ),
            "messages": self._decode_json_list(row[5]),
            "active_skills": self._decode_json_list(row[6]),
            "memory": self._decode_json_object(row[7]),
            "metadata": metadata,
        }

    def set_pinned(self, session_id: str, pinned: bool) -> bool:
        """Pin/unpin a session (C2). Store-level op: safe for sessions not
        currently loaded anywhere. Returns True when a row was updated."""
        ts = datetime.now(timezone.utc).isoformat() if pinned else None
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET pinned_at = ? WHERE id = ?", (ts, session_id)
            )
            return cursor.rowcount > 0

    def rename_record(self, session_id: str, name: str) -> bool:
        """Rename a session record without loading the live session (C2,
        bridge calls). Stomping on a live session's name is tolerated — the
        next save of that session re-writes name from memory anyway."""
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("session name cannot be empty")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT updated_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return False
            cursor = conn.execute(
                "UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?",
                (cleaned[:120], _now_marker(str(row[0] or "")), session_id),
            )
            return cursor.rowcount > 0

    def archive(self, session_id: str) -> bool:
        """Mark a session as archived."""
        self._catch_up_with_journal(session_id)
        record = self.get_session_record(session_id)
        if record is None:
            return False
        metadata = dict(record["metadata"])
        metadata.setdefault("archived_at", datetime.now(timezone.utc).isoformat())
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET metadata = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(metadata),
                    _now_marker(str(record.get("updated_at") or "")),
                    session_id,
                ),
            )
            if cursor.rowcount == 0:
                return False
        # F12 audit fix (2026-09-21): archive rides the mirror immediately,
        # never stale-on-next-save (server lists filtered by state updated now).
        self._mirror_state_change(session_id)
        return True

    def set_mirror_poster(self, poster, client) -> None:
        """Register mirror's POST callable + client for path-introspection.
        Honest: without a poster set, mirror-of-state-change stays silent
        (save() is the fallback lane).
        """
        self._mirror_poster = poster
        self._mirror_client = client

    def _catch_up_with_journal(self, session_id: str) -> None:
        """Before a state change, adopt the journal copy when it is ahead of this row.

        A state change stamps a fresh marker and mirrors the whole row, so on a
        copy that trails the journal (a remote turn never adopted here) it
        overwrote the newer journal copy. Best effort: without a mirror client,
        or with the journal unreachable, the change proceeds as before.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT updated_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        detail = self._journal_detail_or_none(session_id) if row is not None else None
        if detail and self._ts_newer(str(detail.get("updated_at") or ""), str(row[0] or "")):
            self.adopt_remote_detail(session_id, detail)

    def _journal_detail_or_none(self, session_id: str) -> dict[str, Any] | None:
        """This session's journal detail via the mirror client; None when there
        is no mirror client, the journal is unreachable, or the row is absent."""
        client = getattr(self, "_mirror_client", None)
        if client is None or getattr(self, "_mirror_poster", None) is None:
            return None
        try:
            detail = client.get(
                f"{journal_prefix(client)}/companion/sessions/{session_id}", timeout=15
            )
        except Exception:  # noqa: BLE001 — journal optional
            _logger.debug("journal read before state change skipped", exc_info=True)
            return None
        if not isinstance(detail, dict) or str(detail.get("session_id") or "") != session_id:
            return None
        return detail

    def _mirror_state_change(self, session_id: str) -> None:
        """F12 audit fix: re-mirror the local row after a state-changing
        update (archive / set_state) so server-bridged lists immediately
        reflect the new bridge-side truth.

        Never fabricates. Requires set_mirror_poster() ran first.
        """
        poster = getattr(self, "_mirror_poster", None)
        client = getattr(self, "_mirror_client", None)
        if poster is None or client is None:
            return
        record = self.get_session_record(session_id)
        if record is None:
            return
        # The row's own marker (the state change just stamped it), and the same
        # JSON-string field shapes save() mirrors — journal, local row and 3-way
        # base stay string-equal, and adopters never see object-shaped fields.
        marker = str(record.get("updated_at") or "")
        try:
            reply = poster(
                f"{journal_prefix(client)}/companion/sessions/{session_id}/snapshot",
                json=_state_change_body(session_id, record, marker),
            )
        except Exception:
            _logger.debug("mirror of state change skipped", exc_info=True)
            return
        if not _journal_refused(reply):
            self._write_mirror_marker(session_id, marker)

    def fork(
        self,
        session_id: str,
        new_session_id: str,
        *,
        new_name: str = "",
        from_turn: str = "",
    ) -> dict[str, Any] | None:
        """Duplicate a session row under a new id (fork, G-P9-2).

        The fork shares messages/skills/memory as of now and records
        ``forked_from`` in metadata; it starts unpinned with fresh
        timestamps. Returns a summary dict, or None when the source is
        absent. The forked row mirrors to the server snapshot journal on its
        first live save, like every other session (WP-04).

        ADR-057 D-4: ``from_turn`` slices the copied messages to the prefix
        ending just before the NEXT turn's first message (the boundary is
        the first entry whose turn_id differs from the matched one and comes
        after it). An unknown turn id raises ``KeyError(turn_id)`` — the
        named failure (``fork.turn_not_found`` class); callers translate it.
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT name, created_at, updated_at, messages,
                    active_skills, memory, metadata, model
                   FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            (
                name,
                _created,
                source_updated,
                messages,
                active_skills,
                memory,
                metadata_json,
                model,
            ) = row
            metadata = _local_fork_metadata(json.loads(metadata_json))
            metadata["forked_from"] = session_id
            if from_turn:
                sliced = _slice_messages_to_turn(json.loads(messages), from_turn)
                metadata["forked_from_turn"] = from_turn
                # F6: fork-copied prefix keeps original ids for audit; but the
                # fork's own turn sequence must continue from the last slice turn
                # (NOT restart at t1) so server-side turns accessors never see
                # duplicate ids inside the forked session.
                last_turn = _turn_seq_of_last_row(sliced)
                if last_turn and last_turn.isdigit():
                    metadata["turn_seq"] = int(last_turn)
                else:
                    metadata.pop("turn_seq", None)
                messages = json.dumps(sliced)
            fork_name = new_name or f"{name} (fork)"
            now = _now_marker(str(source_updated or ""))
            conn.execute(
                """INSERT INTO sessions (
                     id, name, created_at, updated_at, messages,
                     active_skills, memory, metadata, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_session_id,
                    fork_name,
                    now,
                    now,
                    messages,
                    active_skills,
                    memory,
                    json.dumps(metadata),
                    model,
                ),
            )
        return {
            "session_id": new_session_id,
            "name": fork_name,
            "forked_from": session_id,
            "messages": len(json.loads(messages)),
        }

    def delete(self, session_id: str, client: "Client | None" = None) -> str:
        """Delete a session (WP-04 G-P6-1 round 2).

        Mirrored rows delete through the journal too (HTTP DELETE on the
        companion snap route, owner-scoped) so a later reconcile/pull can
        never resurrect them. Returns:
          * "deleted"      — local row removed (mirror included when mirrored)
          * None           — no local row
          * "mirror-pending" — local row removed but the journal row could
            NOT be deleted (offline or mirror call failed); a tombstone is
            recorded and reconcile retries the journal delete (no
            resurrection across reconnects).
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            metadata = self._decode_json_object(row[0])
            mirrored = (
                bool(metadata.get("_mirror_marker"))
                or str(metadata.get("venue") or "") in {"frontend"}
                or str(metadata.get("venue") or "").startswith("cluster:")
            )
            outcome = "deleted"
            if mirrored:
                deleter = getattr(client, "delete", None) if client is not None else None
                ok = False
                if deleter is not None:
                    try:
                        self._delete_journal_row(client, session_id)
                        ok = True
                    except Exception:  # noqa: BLE001 — journal optional
                        ok = False
                if not ok:
                    outcome = "mirror-pending"
                    self._sync_errors += 1
                    self._last_sync = {
                        "mode": "degraded",
                        "error": f"mirror delete pending for {session_id}",
                    }
                    conn.execute(
                        (
                            "INSERT OR REPLACE INTO session_delete_tombstones"
                            " (session_id, deleted_at, retries) VALUES (?, ?, 0)"
                        ),
                        (session_id, datetime.now(timezone.utc).isoformat()),
                    )
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return outcome if cursor.rowcount > 0 else None

    # -- delete tombstones (mirror-pending retention) ------------------------

    def _delete_journal_row(self, client: "Client", sid: str) -> None:
        """Raise on any uncertainty: server-confirmed mirror delete only.

        A 404 is terminal-success: the server row is already gone (manual
        delete, prior mirror pass), which is exactly the end state.
        """
        deleter = getattr(client, "delete", None)
        if deleter is None:
            raise RuntimeError("client has no delete verb for the journal")
        try:
            payload = deleter(f"{journal_prefix(client)}/companion/sessions/{sid}")
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if "404" in text or "not found" in text or "snapshot not found" in text:
                return
            raise
        if isinstance(payload, dict) and (payload or {}).get("error"):
            raise RuntimeError(f"journal delete failed: {payload['error']}")

    def _tombstoned_ids(self, conn) -> set[str]:
        return {
            r[0]
            for r in conn.execute("SELECT session_id FROM session_delete_tombstones").fetchall()
        }

    # -- retention sweep (WP-04 follow-on; opt-in by policy) ----------------

    ENV_RETENTION_MAX_DAYS = "R4_COMPANION_RETENTION_MAX_DAYS"

    def _retention_policy_days(self, max_days: int | None) -> int:
        """Resolve the effective max-age (env fallback; ≤0 = OFF)."""
        import os

        if isinstance(max_days, int):
            return max_days
        raw = os.environ.get(self.ENV_RETENTION_MAX_DAYS, "0") or "0"
        try:
            return int(raw)
        except ValueError:
            return 0

    def _sweep_apply_one(
        self,
        record: dict[str, Any],
        cutoff: datetime,
        client: "Client | None",
        include_archived: bool,
        counters: dict[str, int],
    ) -> None:
        """One row's retention decision (helper keeps sweep loop branch-thin)."""
        if record.get("pinned_at") or (record.get("archived_at") and not include_archived):
            counters["skipped"] += 1
            return
        updated = self._parse_iso(record.get("updated_at"))
        if updated is None or updated >= cutoff:
            return
        counters["expired"] += 1
        outcome = self.delete(record["id"], client)
        if outcome == "mirror-pending":
            counters["mirror_pending"] += 1
        elif outcome:
            counters["deleted"] += 1

    def sweep_retention(
        self,
        client: "Client | None" = None,
        *,
        max_days: int | None = None,
        include_archived: bool = False,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Retention sweep: delete sessions older than the policy horizon.

        DEFAULT OFF — ``max_days`` must come from the caller or the
        ``R4_COMPANION_RETENTION_MAX_DAYS`` env var; absent/0/negative =
        no-op (nothing ages out without an explicit policy). Archived rows
        are exempt unless ``include_archived`` is set. Every expired row
        goes through the mirror-aware :meth:`delete` path, so a mirrored
        row leaves tombstones only when the mirror was unreachable (they
        replay on the next reconcile). Counter dict: scanned / expired /
        deleted / mirror_pending / skipped.
        """
        counters = {
            "scanned": 0,
            "expired": 0,
            "deleted": 0,
            "mirror_pending": 0,
            "skipped": 0,
        }
        days = self._retention_policy_days(max_days)
        if days <= 0:
            return counters
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        for record in self.list_sessions(limit=10_000, include_archived=True):
            counters["scanned"] += 1
            self._sweep_apply_one(record, cutoff, client, include_archived, counters)
        return counters

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return None

    @staticmethod
    def _decode_json_object(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _decode_json_list(raw: str) -> list[Any]:
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []

    @staticmethod
    def _turn_count_of(metadata: dict[str, Any]) -> int | None:
        """F17: turn_count surfaces from metadata.turn_seq (never fabricated)."""
        turn_seq = metadata.get("turn_seq")
        return int(turn_seq) if isinstance(turn_seq, int) and turn_seq >= 0 else None

    # 2026-10-24: the @classmethod here sat above _turn_count_of's
    # @staticmethod since 6b12eeba2. Python <= 3.12 chained the two by
    # accident; 3.13 removed that, so every session listing on a 3.13 client
    # failed ("_turn_count_of() takes 1 positional argument but 2 were given").
    @classmethod
    def _summary_from_row(cls, row: tuple[Any, ...], *, metadata: dict[str, Any]) -> dict[str, Any]:
        messages = cls._decode_json_list(row[5]) if len(row) > 5 else []
        endpoints = cls._endpoint_labels(metadata)
        pinned_at = row[7] if len(row) > 7 else None
        return {
            "id": row[0],
            "name": _display_title(row[1], messages),
            "created_at": row[2],
            "updated_at": row[3],
            "model": row[4] or "",
            "message_count": len(messages),
            "project_id": cls._project_id(metadata),
            "endpoints": endpoints,
            "endpoint": ",".join(endpoints),
            "archived_at": str(metadata.get("archived_at") or ""),
            "pinned": bool(pinned_at),
            "pinned_at": str(pinned_at or ""),
            "venue": str(metadata.get("venue") or DEFAULT_VENUE),
            "state": str(metadata.get("state") or ""),
            "turn_count": cls._turn_count_of(metadata),
            "session_kind": str(metadata.get("session_kind") or "conversation"),
        }

    @staticmethod
    def _project_id(metadata: dict[str, Any]) -> str:
        return metadata_project_id(metadata)

    @classmethod
    def _endpoint_labels(cls, metadata: dict[str, Any]) -> list[str]:
        labels: list[str] = []
        for key in ("endpoint", "endpoint_id", "instance_id"):
            if metadata.get(key):
                labels.append(str(metadata[key]))
        endpoints = metadata.get("active_endpoints")
        if isinstance(endpoints, list):
            for endpoint in endpoints:
                if isinstance(endpoint, dict):
                    label = cls._endpoint_label(endpoint)
                    if label:
                        labels.append(label)
        return list(dict.fromkeys(labels))

    @staticmethod
    def _endpoint_label(endpoint: dict[str, Any]) -> str:
        for key in ("instance_id", "endpoint_id", "id", "job_id", "name", "model"):
            value = endpoint.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _matches_endpoint(endpoints: list[str], endpoint_filter: str) -> bool:
        needle = endpoint_filter.lower()
        return any(needle in endpoint.lower() for endpoint in endpoints)

    @staticmethod
    def _config_attr(config: Any, name: str, default: str) -> str:
        value = getattr(config, name, default) if config is not None else default
        return value if isinstance(value, str) else default

    def create_session(
        self,
        client: "Client",
        name: str | None = None,
        *,
        model: str | None = None,
        venue: str = DEFAULT_VENUE,
    ) -> CompanionSession:
        """Create a new session and persist it.

        venue (plan 2026-09-09): recorded once, at creation, and never
        silently re-written by later saves. Defaults to ``local`` — the
        honest venue for every client-created session.
        """
        validate_venue(venue)
        session_id = str(uuid.uuid4())
        session_name = name or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        session = CompanionSession(
            session_id=session_id, name=session_name, client=client, model=model
        )
        session.metadata.setdefault("venue", venue)
        from .subagent_sessions import bind_subagent_recorder

        bind_subagent_recorder(session, self)
        self.save(session)
        return session

    def set_state(self, session_id: str, state: str) -> bool:
        """Record a lifecycle transition (ongoing/stopped/completed/error).

        Writes metadata.state only; archived stays derived from archived_at.
        False when no row exists.
        """
        validate_storable_state(state)
        self._catch_up_with_journal(session_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata, updated_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return False
            metadata = self._decode_json_object(row[0])
            metadata["state"] = state
            conn.execute(
                "UPDATE sessions SET metadata = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(metadata),
                    _now_marker(str(row[1] or "")),
                    session_id,
                ),
            )
        # F12 audit fix: set_state re-mirrors immediately (before: only next save
        # advanced the server's filtered lists).
        self._mirror_state_change(session_id)
        return True


def _sync_untrusted_mark(session: Any) -> None:
    """ADR-111 D-4: carry the sticky "untrusted content entered" mark between
    the taint registry and the row's metadata (stub sessions have no sync)."""
    sync = getattr(session, "sync_untrusted_mark", None)
    if callable(sync):
        sync()
