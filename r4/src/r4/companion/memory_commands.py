"""``/memory`` and ``r4 memory``: the person's view of saved memory (ADR-111).

One implementation for the console's slash command and the CLI. Every verb
returns the lines to show; nothing here prints.

    list                         saved facts of both scopes, plus the lesson count
    show <name>                  one fact's body
    add [--user] [--type T] [--expires YYYY-MM-DD] <text>
    forget <name> [--user]
    lessons [clear | forget <n>] the automatic lessons from your corrections
    promote <name> briefing      append it to R4.md (review with git, then commit)
    promote <name> knowledge <graph>  stage a knowledge proposal (approve it with
                                 ``r4 knowledge review approve <proposal>``)
    path                         where the files are
    off | on                     this session only: no memory read or written
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from r4._atomic import atomic_write_text

from .memory_store import (
    DESCRIPTION_MAX,
    MEMORY_TYPES,
    MemoryFact,
    MemoryStore,
    MemoryStoreError,
    checkout_root,
    slugify,
)

USAGE = (
    "usage: /memory [list] | show <name> | add [--user] [--type T] [--expires DATE] <text>",
    "       /memory forget <name> [--user] | lessons [clear | forget <n>] | path",
    "       /memory promote <name> briefing | promote <name> knowledge <graph> | off | on",
)
PROMOTED_HEADING = "## From saved memory"
SESSION_OFF_KEY = "memory_off"


@dataclass
class MemoryCommandContext:
    """What a verb may use: the store, the lesson loop, a client, a session."""

    store: MemoryStore
    steering: Any = None
    client: Any = None
    session: Any = None
    cwd: Path = field(default_factory=Path.cwd)


def run_memory_command(args: list[str], ctx: MemoryCommandContext) -> list[str]:
    """Run one ``/memory`` verb; errors come back as lines, never raised."""
    verb, rest = (args[0].lower(), args[1:]) if args else ("list", [])
    handler = _VERBS.get(verb)
    if handler is None:
        return list(USAGE)
    try:
        return handler(rest, ctx)
    except (MemoryStoreError, OSError) as exc:
        return [f"memory: {exc}"]


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def _row(fact: MemoryFact) -> str:
    flags = " (expired)" if fact.expired() else (f" (until {fact.expires})" if fact.expires else "")
    return f"  {fact.name} [{fact.type}]{flags}: {fact.description}"


def _list(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    facts = ctx.store.facts()
    lines: list[str] = []
    for scope in ("user", "project"):
        rows = [_row(f) for f in facts if f.scope == scope]
        if rows:
            lines += [f"{scope}:", *rows]
    if not lines:
        lines.append("No saved memory yet. Add a fact: /memory add <text>")
    lessons = _lessons_of(ctx)
    if lessons:
        lines.append(f"lessons from your corrections: {len(lessons)} (/memory lessons)")
    if _session_off(ctx):
        lines.append("memory is off for this session (/memory on)")
    return lines


def _show(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    if not rest:
        return ["usage: /memory show <name>"]
    fact = ctx.store.find(rest[0])
    if fact is None:
        return [f"No saved fact named {rest[0]!r}."]
    head = f"{fact.scope}/{fact.name} [{fact.type}] updated {fact.updated} by {fact.source}"
    return [head, fact.description, "", *(fact.body.splitlines() or ["(no body)"])]


def _take_flags(rest: list[str]) -> tuple[dict[str, str], list[str]]:
    """``--user``, ``--type T`` and ``--expires D``; the remaining words."""
    flags: dict[str, str] = {}
    words: list[str] = []
    items = iter(rest)
    for word in items:
        if word == "--user":
            flags["scope"] = "user"
        elif word in ("--type", "--expires"):
            flags[word[2:]] = next(items, "")
        else:
            words.append(word)
    return flags, words


def _free_name(store: MemoryStore, scope: str, text: str) -> str:
    base = slugify(" ".join(text.split()[:6]))
    for suffix in ("", *(f"-{n}" for n in range(2, 10))):
        name = f"{base[: 64 - len(suffix)]}{suffix}"
        if store.get(name, scope) is None:
            return name
    raise MemoryStoreError("too many facts with that start; use different words")


def _add(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    flags, words = _take_flags(rest)
    text = " ".join(words).strip()
    if not text:
        return ["usage: /memory add [--user] [--type T] [--expires YYYY-MM-DD] <text>"]
    scope = flags.get("scope", "project")
    default_type = "user" if scope == "user" else "project"
    fact = MemoryFact(
        name=_free_name(ctx.store, scope, text),
        type=flags.get("type") or default_type,
        description=text[:DESCRIPTION_MAX],
        body=text if len(text) > DESCRIPTION_MAX else "",
        scope=scope,
        source="user",
        source_session=str(getattr(ctx.session, "session_id", "") or ""),
        expires=flags.get("expires", ""),
    )
    saved = ctx.store.write(fact)
    return [f"Saved {saved.scope}/{saved.name}."]


def _forget(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    flags, words = _take_flags(rest)
    if not words:
        return ["usage: /memory forget <name> [--user]"]
    name = words[0]
    scopes = ("user",) if flags.get("scope") == "user" else ("project", "user")
    for scope in scopes:
        if ctx.store.forget(name, scope):
            return [f"Forgot {scope}/{name}."]
    return [f"No saved fact named {name!r}."]


def _lessons_of(ctx: MemoryCommandContext) -> list[Any]:
    """The lessons on disk now (another tab may have added one): the numbers
    ``lessons forget <n>`` takes are the ones this listing shows."""
    if ctx.steering is None:
        return []
    reload = getattr(ctx.steering, "reload", None)
    if callable(reload):
        reload()
    return list(ctx.steering.list_lessons())


def _lessons(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    if ctx.steering is None:
        return ["Lessons are not available here."]
    if rest[:1] == ["clear"]:
        ctx.steering.clear()
        return ["Cleared every lesson."]
    if rest[:1] == ["forget"]:
        return _forget_lesson(rest[1:], ctx)
    lessons = _lessons_of(ctx)
    if not lessons:
        return ["No lessons yet. They are recorded when you correct the agent."]
    lines = ["Lessons from your corrections (the last 10 reach each turn):"]
    lines += [f"  {n}. {lesson.rule}" for n, lesson in enumerate(lessons, start=1)]
    return lines


def _forget_lesson(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    if not rest or not rest[0].isdigit():
        return ["usage: /memory lessons forget <n>"]
    lesson = ctx.steering.remove(int(rest[0]) - 1)
    return [f"Forgot lesson {rest[0]}: {lesson.rule}"] if lesson else [f"No lesson {rest[0]}."]


def _path(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    return [
        f"user:    {ctx.store.scope_dir('user')}",
        f"project: {ctx.store.scope_dir('project')}",
    ]


# ---------------------------------------------------------------------------
# Promotion (ADR-111 D-5): every exit has its own review
# ---------------------------------------------------------------------------


def _promote(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    if len(rest) < 2 or rest[1] not in _PROMOTIONS:
        return ["usage: /memory promote <name> briefing | knowledge <graph>"]
    fact = ctx.store.find(rest[0])
    if fact is None:
        return [f"No saved fact named {rest[0]!r}."]
    return _PROMOTIONS[rest[1]](fact, rest[2:], ctx)


def _to_briefing(fact: MemoryFact, rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    target = checkout_root(ctx.cwd) / "R4.md"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    block = "" if PROMOTED_HEADING in existing else f"\n{PROMOTED_HEADING}\n\n"
    entry = f"- {fact.description}\n"
    if fact.body and fact.body != fact.description:
        entry += "".join(f"  {line}\n" for line in fact.body.splitlines())
    separator = "" if not existing or existing.endswith("\n") else "\n"
    atomic_write_text(target, existing + separator + block + entry)
    return [
        f"Appended {fact.name} to {target}.",
        "Review it with git diff and commit it with your team; every agent reads R4.md.",
        f"Then drop the private copy: /memory forget {fact.name}",
    ]


def _to_knowledge(fact: MemoryFact, rest: list[str], ctx: MemoryCommandContext) -> list[str]:
    if not rest:
        return ["usage: /memory promote <name> knowledge <graph>"]
    knowledge = getattr(ctx.client, "knowledge", None)
    if knowledge is None:
        return ["Promoting to knowledge needs a platform connection."]
    proposal = knowledge.graph_propose(
        rest[0],
        reason=f"Promote saved memory {fact.name}: {fact.description}",
        missing_information=[fact.description, *([fact.body] if fact.body else [])],
    )
    proposal_id = (proposal or {}).get("proposal_id") or (proposal or {}).get("id") or "?"
    return [
        f"Staged knowledge proposal {proposal_id} on graph {rest[0]}; nothing changed yet.",
        f"A reviewer approves it with: r4 knowledge review approve {proposal_id}",
    ]


_PROMOTIONS: dict[str, Callable[[MemoryFact, list[str], MemoryCommandContext], list[str]]] = {
    "briefing": _to_briefing,
    "knowledge": _to_knowledge,
}


# ---------------------------------------------------------------------------
# Session switch
# ---------------------------------------------------------------------------


def _session_off(ctx: MemoryCommandContext) -> bool:
    metadata = getattr(ctx.session, "metadata", None)
    return bool(isinstance(metadata, dict) and metadata.get(SESSION_OFF_KEY))


def _set_session(value: bool) -> Callable[[list[str], MemoryCommandContext], list[str]]:
    def handler(rest: list[str], ctx: MemoryCommandContext) -> list[str]:
        metadata = getattr(ctx.session, "metadata", None)
        if not isinstance(metadata, dict):
            return ["/memory off and on apply to a chat session."]
        metadata[SESSION_OFF_KEY] = value
        if value:
            return ["Memory is off for this session: nothing is read or saved (/memory on)."]
        return ["Memory is on again for this session."]

    return handler


_VERBS: dict[str, Callable[[list[str], MemoryCommandContext], list[str]]] = {
    "list": _list,
    "ls": _list,
    "show": _show,
    "add": _add,
    "forget": _forget,
    "rm": _forget,
    "lessons": _lessons,
    "promote": _promote,
    "path": _path,
    "off": _set_session(True),
    "on": _set_session(False),
}

__all__ = [
    "MEMORY_TYPES",
    "MemoryCommandContext",
    "SESSION_OFF_KEY",
    "USAGE",
    "run_memory_command",
]
