"""Local agent memory: one private file per fact (ADR-111).

Two scopes, both on the user's machine and never on a server:

* ``user``: who the researcher is and how they like to work, for every
  project (``<root>/user/``);
* ``project``: one researcher's working notes for one UMRS project
  (``<root>/projects/<key>/``). The key is the gateway host plus the
  ``project_id``; without a project it is the repository the workspace
  belongs to, so a worktree shares its repository's memory.

``<root>`` is ``R4_MEMORY_DIR``, else ``~/.config/r4/memory``, read at each
call (tests and scratch homes never touch the real one).

Each fact is ``<name>.md`` with a small front matter (name, type,
description, timestamps, source, source session, optional expiry) and a
body. ``MEMORY.md`` in each scope is a view for people, rewritten after
every change; the prompt is built from the fact files, never from that
view, so two sessions writing at once cannot drop each other's entries.
A write that would replace an existing fact must carry the sha it read
(``expected_sha``) unless the caller says ``replace=True``; a changed file
is a :class:`MemoryConflict` and the caller re-reads. Writes refuse text
that looks like a credential.

Team rules belong in ``R4.md`` / ``AGENTS.md`` and domain facts in the
project's knowledge on UMRS; ``promote`` hands a fact to one of those, each
through its own review (ADR-111 D-5).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from r4._atomic import atomic_write_text

try:  # POSIX only; elsewhere writes still land atomically, unlocked.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")
SCOPES: tuple[str, ...] = ("user", "project")
INDEX_FILE = "MEMORY.md"
LEGACY_IMPORT_MARKER = ".imported-memory-json"

NAME_MAX = 64
DESCRIPTION_MAX = 200
BODY_MAX = 4000
DEFAULT_INDEX_CHARS = 6000

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG_DROP = re.compile(r"[^a-z0-9._-]+")
_FRONT_KEYS = (
    "name",
    "type",
    "description",
    "created",
    "updated",
    "source",
    "source_session",
    "expires",
)


class MemoryStoreError(Exception):
    """A memory call the store refuses (bad input, read-only store)."""


class MemoryConflict(MemoryStoreError):
    """The fact exists or changed since it was read: re-read, then retry."""


def memory_root() -> Path:
    """The memory directory, read now: ``R4_MEMORY_DIR`` else the config home."""
    raw = os.environ.get("R4_MEMORY_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".config" / "r4" / "memory"


def index_char_cap() -> int:
    """Characters of saved memory one turn carries (``R4_MEMORY_INDEX_CHARS``)."""
    raw = os.environ.get("R4_MEMORY_INDEX_CHARS", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_INDEX_CHARS


def slugify(text: str) -> str:
    """A fact name from free text: lower case, ``[a-z0-9._-]``, at most 64.

    Text with other characters (Japanese, accents) keeps an 8-hex digest of
    the original, so two such keys never collapse into one name.
    """
    slug = _SLUG_DROP.sub("-", text.strip().lower()).strip("-.")
    if text.strip().isascii():
        return slug[:NAME_MAX] or "fact"
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:8]
    return f"{(slug or 'fact')[: NAME_MAX - 9]}-{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _one_line(value: str) -> str:
    return " ".join(str(value or "").split())


# ---------------------------------------------------------------------------
# Scope keys
# ---------------------------------------------------------------------------


def repository_root(start: Path) -> Path:
    """The main checkout ``start`` belongs to; a linked worktree's ``.git``
    file names it. ``start`` itself outside any repository."""
    start = start.resolve()
    for folder in (start, *start.parents):
        marker = folder / ".git"
        if marker.is_dir():
            return folder
        if marker.is_file():
            return _main_checkout_of(marker) or folder
    return start


def checkout_root(start: Path) -> Path:
    """The checkout ``start`` is in, a linked worktree being its own (where
    the person reviews a change); ``start`` itself outside any repository."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return start


def _main_checkout_of(git_file: Path) -> Path | None:
    """``gitdir: <main>/.git/worktrees/<name>`` → ``<main>``."""
    try:
        line = git_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (git_file.parent / gitdir).resolve()
    parts = gitdir.parts
    if "worktrees" in parts:
        dot_git = Path(*parts[: parts.index("worktrees")])
        return dot_git.parent
    return None


def project_key(project_id: str, gateway_url: str = "", workspace: Path | None = None) -> str:
    """Directory name of a project scope.

    ``<gateway host>--<project_id>`` for a UMRS project, so one project id on
    two gateways stays apart; otherwise ``workspace--<repo name>-<hash>``.
    """
    if project_id:
        host = urlparse(gateway_url).hostname or "local"
        return f"{slugify(host)}--{slugify(project_id)}"
    root = repository_root(workspace or Path.cwd())
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return f"workspace--{slugify(root.name)}-{digest}"


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryFact:
    """One saved fact (a file ``<name>.md``)."""

    name: str
    type: str
    description: str
    body: str
    scope: str = "project"
    created: str = ""
    updated: str = ""
    source: str = "user"
    source_session: str = ""
    expires: str = ""
    sha: str = ""

    def expired(self, today: date | None = None) -> bool:
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < (today or date.today())
        except ValueError:
            return False

    def to_text(self) -> str:
        lines = ["---"]
        for key in _FRONT_KEYS:
            value = getattr(self, key)
            if value:
                lines.append(f"{key}: {_one_line(value)}")
        lines.append("---")
        return "\n".join(lines) + "\n\n" + self.body.strip() + "\n"

    def as_dict(self) -> dict[str, Any]:
        data = {key: getattr(self, key) for key in (*_FRONT_KEYS, "scope", "sha", "body")}
        data["expired"] = self.expired()
        return data


def parse_fact(text: str, scope: str) -> MemoryFact | None:
    """A fact from its file text, or None when the front matter is missing."""
    if not text.startswith("---\n"):
        return None
    head, sep, body = text[4:].partition("\n---")
    if not sep:
        return None
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon and key.strip() in _FRONT_KEYS:
            fields[key.strip()] = value.strip()
    if not fields.get("name"):
        return None
    return MemoryFact(
        name=fields["name"],
        type=fields.get("type", "project"),
        description=fields.get("description", ""),
        body=body.lstrip("\n").rstrip() if body else "",
        scope=scope,
        created=fields.get("created", ""),
        updated=fields.get("updated", ""),
        source=fields.get("source", ""),
        source_session=fields.get("source_session", ""),
        expires=fields.get("expires", ""),
        sha=_sha(text),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _secret_kind(text: str) -> str | None:
    """The first credential shape in ``text``, or None.

    The scanner's ``aws_secret_key`` row matches any 40-character token (a
    git sha), so it is left out here.
    """
    from r4.agent.scanner import SECRET_PATTERNS

    for kind, pattern in SECRET_PATTERNS:
        if kind != "aws_secret_key" and pattern.search(text):
            return kind
    return None


def _shape_problem(fact: MemoryFact) -> str | None:
    if not _NAME_RE.fullmatch(fact.name) or len(fact.name) > NAME_MAX:
        return (
            f"name {fact.name!r}: use 1-{NAME_MAX} of a-z 0-9 . _ - (start with a letter or digit)"
        )
    if fact.scope not in SCOPES:
        return f"scope must be one of {', '.join(SCOPES)}"
    if fact.type not in MEMORY_TYPES:
        return f"type must be one of {', '.join(MEMORY_TYPES)}"
    if fact.expires and not _DATE_RE.fullmatch(fact.expires):
        return "expires must be YYYY-MM-DD"
    return None


def _text_problem(fact: MemoryFact) -> str | None:
    description = _one_line(fact.description)
    if not description:
        return "description is required (one line)"
    if len(description) > DESCRIPTION_MAX:
        return f"description longer than {DESCRIPTION_MAX} characters"
    if len(fact.body) > BODY_MAX:
        return f"body longer than {BODY_MAX} characters"
    kind = _secret_kind(f"{fact.description}\n{fact.body}")
    if kind:
        return f"refused: the text looks like a credential ({kind}); never save secrets"
    return None


def validate_fact(fact: MemoryFact) -> None:
    """Raise :class:`MemoryStoreError` naming the first problem."""
    problem = _shape_problem(fact) or _text_problem(fact)
    if problem:
        raise MemoryStoreError(problem)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MemoryStore:
    """The user scope plus one project scope, under one root."""

    def __init__(self, root: Path, project: str, *, read_only: bool = False) -> None:
        self.root = root
        self.project = project
        self.read_only = read_only

    def scope_dir(self, scope: str) -> Path:
        if scope == "user":
            return self.root / "user"
        if scope == "project":
            return self.root / "projects" / self.project
        raise MemoryStoreError(f"scope must be one of {', '.join(SCOPES)}")

    # -- reading -------------------------------------------------------------

    def get(self, name: str, scope: str) -> MemoryFact | None:
        if not _NAME_RE.fullmatch(name or ""):
            return None
        path = self.scope_dir(scope) / f"{name}.md"
        try:
            return parse_fact(path.read_text(encoding="utf-8"), scope)
        except (OSError, UnicodeDecodeError):
            return None  # unreadable (a hand-edited non-UTF-8 file): skipped, never fatal

    def facts(self, scope: str | None = None) -> list[MemoryFact]:
        """Every readable fact, user scope first, by name."""
        facts: list[MemoryFact] = []
        for each in (scope,) if scope else SCOPES:
            facts.extend(self._scope_facts(each))
        return facts

    def _scope_facts(self, scope: str) -> list[MemoryFact]:
        folder = self.scope_dir(scope)
        if not folder.is_dir():
            return []
        facts = []
        for path in sorted(folder.glob("*.md")):
            if path.name == INDEX_FILE:
                continue
            fact = self.get(path.stem, scope)
            if fact is not None:
                facts.append(fact)
        return facts

    def find(self, name: str) -> MemoryFact | None:
        """``name`` in the project scope, else the user scope."""
        return self.get(name, "project") or self.get(name, "user")

    # -- writing -------------------------------------------------------------

    def write(
        self,
        fact: MemoryFact,
        *,
        expected_sha: str | None = None,
        replace_existing: bool = False,
    ) -> MemoryFact:
        """Save ``fact``; see the module docstring for the conflict rule."""
        self._writable()
        validate_fact(fact)
        with self._locked(fact.scope):
            current = self.get(fact.name, fact.scope)
            _check_precondition(current, expected_sha, replace_existing)
            now = _now()
            saved = replace(
                fact,
                description=_one_line(fact.description),
                created=current.created if current else (fact.created or now),
                updated=now,
            )
            text = saved.to_text()
            atomic_write_text(self.scope_dir(fact.scope) / f"{fact.name}.md", text, mode=0o600)
            self._write_index(fact.scope)
        return replace(saved, sha=_sha(text))

    def forget(self, name: str, scope: str, *, expected_sha: str | None = None) -> bool:
        """Delete one fact; False when there was none."""
        self._writable()
        with self._locked(scope):
            current = self.get(name, scope)
            if current is None:
                return False
            if expected_sha and expected_sha != current.sha:
                raise MemoryConflict(f"{scope}/{name} changed since it was read; read it again")
            (self.scope_dir(scope) / f"{name}.md").unlink(missing_ok=True)
            self._write_index(scope)
        return True

    def _writable(self) -> None:
        if self.read_only:
            raise MemoryStoreError("memory is read-only here; the lead session writes it")

    @contextmanager
    def _locked(self, scope: str) -> Iterator[None]:
        folder = self.scope_dir(scope)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if fcntl is None:  # pragma: no cover - Windows
            yield
            return
        with open(folder / ".lock", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _write_index(self, scope: str) -> None:
        """``MEMORY.md``: the human view of one scope (never read back)."""
        lines = [f"# Memory ({scope})", ""]
        lines += [_index_line(fact) for fact in self._scope_facts(scope)]
        atomic_write_text(self.scope_dir(scope) / INDEX_FILE, "\n".join(lines) + "\n", mode=0o600)

    # -- the prompt ----------------------------------------------------------

    def index_prompt(self, cap: int | None = None) -> str:
        """The per-turn memory block: one line per live fact, capped."""
        sections = []
        for scope in SCOPES:
            lines = [_index_line(f) for f in self._scope_facts(scope) if not f.expired()]
            if lines:
                sections.append(f"## {scope}\n" + "\n".join(lines))
        if not sections:
            return ""
        text = "\n".join(sections)
        limit = index_char_cap() if cap is None else cap
        if len(text) > limit:
            text = (
                text[:limit].rsplit("\n", 1)[0] + "\n[… more saved memory: memory_read() lists it]"
            )
        return (
            "[Saved memory: this user's private notes, written in earlier sessions. "
            "They may be stale; check before relying on one. memory_read(name) shows a "
            "fact's body.]\n" + text
        )


def _index_line(fact: MemoryFact) -> str:
    expiry = f" (until {fact.expires})" if fact.expires else ""
    return f"- {fact.name} [{fact.type}]{expiry}: {fact.description}"


def _check_precondition(
    current: MemoryFact | None, expected_sha: str | None, replace_existing: bool
) -> None:
    if current is None or replace_existing:
        return
    if expected_sha is None:
        raise MemoryConflict(
            f"{current.scope}/{current.name} already exists (sha {current.sha}); "
            "read it, then write again with expected_sha to update it"
        )
    if expected_sha != current.sha:
        raise MemoryConflict(
            f"{current.scope}/{current.name} changed since it was read "
            f"(sha now {current.sha}); read it again"
        )


# ---------------------------------------------------------------------------
# Wiring helpers
# ---------------------------------------------------------------------------


def store_for(
    client: Any = None, *, workspace: Path | None = None, read_only: bool = False
) -> MemoryStore:
    """The store for a client's current project (read per call: a project
    switch reaches the next call)."""
    config = getattr(client, "config", None)
    project_id = str(getattr(config, "project_id", "") or "")
    gateway = str(getattr(config, "gateway_url", "") or "")
    store = MemoryStore(
        memory_root(), project_key(project_id, gateway, workspace), read_only=read_only
    )
    import_legacy_memory(store)
    return store


def legacy_memory_file() -> Path:
    """The pre-ADR-111 single file (``R4_COMPANION_MEMORY_DIR``)."""
    raw = os.environ.get("R4_COMPANION_MEMORY_DIR", "").strip()
    folder = Path(raw).expanduser() if raw else Path.home() / ".config" / "r4" / "companion"
    return folder / "memory.json"


def import_legacy_memory(store: MemoryStore, legacy: Path | None = None) -> int:
    """Copy ``memory.json`` into the user scope once; the old file stays.

    Its entries were global (every project saw them), so they land in the
    user scope. Returns how many facts were written.
    """
    marker = store.root / LEGACY_IMPORT_MARKER
    source = legacy or legacy_memory_file()
    if store.read_only or marker.exists() or not source.is_file():
        return 0
    import json

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}  # unreadable or not a mapping: nothing to copy, never retried
    written = sum(_import_entry(store, key, value) for key, value in sorted(data.items()))
    store.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_text(marker, f"{_now()} {source}\n", mode=0o600)
    return written


def _import_entry(store: MemoryStore, key: str, value: Any) -> int:
    text = str(value.get("value", "")) if isinstance(value, dict) else str(value)
    fact = MemoryFact(
        name=slugify(key),
        type="project",
        description=_one_line(f"{key}: {text}")[:DESCRIPTION_MAX],
        body=text,
        scope="user",
        source="import",
    )
    try:
        store.write(fact, replace_existing=False)
    except MemoryStoreError:
        return 0  # an invalid or secret-shaped entry stays only in the old file
    return 1
