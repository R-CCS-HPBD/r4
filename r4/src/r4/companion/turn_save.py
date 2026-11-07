"""``/save``: keep a chat turn in project knowledge, verbatim (K1.6).

Knowledge plan 2026-10-24, K1.6. The text comes from the session's own
message store by turn id (``t2``), never re-typed by the model, and goes to
UMRS as a ``session_turns`` source (``KnowledgeClient.save_session_turns``),
with a title, tags, a graph and a back-link to the session. UMRS scans it
(a token-shaped string refuses the save by name) and tiers it (K-1):

* your own message (``mine``) lands established;
* an assistant answer lands proposed (origin ``agent_authored_user_kept``)
  unless you vouch for it (``--vouch``): established, you its establisher.

The session's ``/acquisition`` mode applies (``disabled`` refuses, ``pinned``
routes into the pinned graph). Grammar::

    /save                      the last answer
    /save 2 | t2               turn 2's answer
    /save 2 mine               turn 2's message (yours)
    /save 2 --vouch            turn 2's answer, vouched
    /save 2 --title "…" --tag mof --graph methods

``/save <path.md>`` (a first argument that is not a turn) still writes the
transcript to a file.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Iterable

KEPT_ORIGIN = "agent_authored_user_kept"
VOUCHED_ORIGIN = "agent_authored_user_vouched"

_TURN_REF = re.compile(r"^t?(\d+)$", re.IGNORECASE)
_PROMPT_WORDS = frozenset({"mine", "--mine", "prompt", "--prompt"})
_BOOL_FLAGS = {"--vouch": ("vouch", True), **{w: ("part", "prompt") for w in _PROMPT_WORDS}}
_VALUE_FLAGS = {"--title": "title", "--tag": "tags", "--graph": "graph"}
SAVE_USAGE = (
    "usage: /save [n|tN|last] [mine] [--vouch] [--title TEXT] [--tag TAG] [--graph G]"
    " — or /save <path.md> to write the transcript"
)


@dataclass(frozen=True)
class SaveRequest:
    """One ``/save``: which turn, which side of it, and its labels."""

    turn: str = "last"  # "last" or "tN"
    part: str = "answer"  # "answer" (assistant) or "prompt" (yours)
    vouch: bool = False
    title: str = ""
    tags: tuple[str, ...] = ()
    graph: str | None = None


def turn_ref(raw: str) -> str | None:
    """``2`` / ``t2`` / ``T2`` → ``t2``; ``last`` → ``last``; else None."""
    text = raw.strip()
    if text.lower() == "last":
        return "last"
    match = _TURN_REF.match(text)
    return f"t{int(match.group(1))}" if match else None


def is_turn_save(tokens: list[str]) -> bool:
    """True when the arguments ask for a turn save, not a transcript file."""
    if not tokens:
        return True
    first = tokens[0]
    return turn_ref(first) is not None or first.startswith("--") or first.lower() in _PROMPT_WORDS


def _apply_flags(tokens: list[str], fields: dict[str, Any]) -> None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.lower() in _BOOL_FLAGS:
            flag, setting = _BOOL_FLAGS[token.lower()]
            fields[flag] = setting
            index += 1
            continue
        key = _VALUE_FLAGS.get(token.lower())
        if key is None or index + 1 >= len(tokens):
            raise ValueError(f"unknown /save option {token!r}. {SAVE_USAGE}")
        value = tokens[index + 1]
        fields[key] = (*fields[key], value) if key == "tags" else value
        index += 2


def parse_save_args(tokens: Iterable[str]) -> SaveRequest | None:
    """A :class:`SaveRequest`, or None when the arguments name a transcript
    file. Raises ValueError (with the usage) on an unknown option."""
    rest = [t for t in tokens if t != ""]
    if not is_turn_save(rest):
        return None
    fields: dict[str, Any] = {"turn": "last", "tags": ()}
    ref = turn_ref(rest[0]) if rest else None
    if ref is not None:
        fields["turn"] = ref
        rest = rest[1:]
    _apply_flags(rest, fields)
    return SaveRequest(**fields)


def parse_save_line(args: str) -> SaveRequest | None:
    """:func:`parse_save_args` over a raw argument string (quotes honoured)."""
    return parse_save_args(shlex.split(args or ""))


def _turn_id(message: Any) -> str:
    return str((getattr(message, "metadata", None) or {}).get("turn_id") or "")


def last_answered_turn(messages: list[Any]) -> str | None:
    """The newest turn id an assistant message carries."""
    for message in reversed(messages):
        if message.role == "assistant" and _turn_id(message):
            return _turn_id(message)
    return None


def turn_text(messages: list[Any], turn_id: str, role: str) -> str | None:
    """Turn *turn_id*'s text as stored: the user message that opened it, or
    the last assistant message of it (its final answer)."""
    found = [m for m in messages if m.role == role and _turn_id(m) == turn_id]
    if not found:
        return None
    message = found[0] if role == "user" else found[-1]
    return str(message.content or "")


def resolve_turn(messages: list[Any], request: SaveRequest) -> tuple[str, str, str]:
    """``(turn_id, role, text)`` the request names; ValueError when absent."""
    turn = last_answered_turn(messages) if request.turn == "last" else request.turn
    if not turn:
        raise ValueError("nothing to save yet: no answered turn in this session")
    role = "user" if request.part == "prompt" else "assistant"
    text = turn_text(messages, turn, role)
    if not text or not text.strip():
        raise ValueError(f"no {'message' if role == 'user' else 'answer'} for turn {turn} here")
    return turn, role, text


def _origin(role: str, vouch: bool) -> tuple[str, str]:
    """``(tier, origin)`` UMRS records (K-1) for a person's save: the
    client's expectation, used only when the server's receipt names none."""
    if role == "user":
        return "established", "user"
    return ("established", VOUCHED_ORIGIN) if vouch else ("proposed", KEPT_ORIGIN)


def _decided(receipt: Any, role: str, vouch: bool) -> tuple[str, str]:
    """``(tier, origin)`` the server decided (``/k2/ingest`` answers
    ``save: {origin, tier}``), else the client's expectation: the receipt
    prints what UMRS will record, not a guess (review of K1 part B)."""
    save = (receipt or {}).get("save") if isinstance(receipt, dict) else None
    if isinstance(save, dict) and save.get("tier") and save.get("origin"):
        return str(save["tier"]), str(save["origin"])
    return _origin(role, vouch)


#: The /acquisition modes' refusals are written for the model; a person
#: who typed /save gets the remedy instead (review of K1 part B).
_ACQUISITION_REFUSED = (
    "save refused: knowledge acquisition is {mode} for this session "
    "(/acquisition open allows saving{graphs})"
)


def _person_refusal(client: Any) -> str:
    from .knowledge_write_gate import acquisition_constraints

    constraints = acquisition_constraints(client)
    mode = str(constraints.get("mode") or "disabled")
    graphs = ", ".join(str(g) for g in constraints.get("graphs") or [])
    pinned = f"; or --graph one of: {graphs}" if mode == "pinned" and graphs else ""
    label = "disabled" if mode == "disabled" else f"pinned to {graphs or 'other graphs'}"
    return _ACQUISITION_REFUSED.format(mode=label, graphs=pinned)


def _target(client: Any, request: SaveRequest) -> tuple[str, str | None]:
    """The project and graph under the session's /acquisition mode."""
    from .knowledge_tools import _project_id_for
    from .knowledge_write_gate import resolve_ingest_target

    project = _project_id_for(client, None)
    if not project:
        raise ValueError("no project selected: /project switch <id> first")
    graph, refusal = resolve_ingest_target(client, request.graph)
    if refusal:
        raise PermissionError(_person_refusal(client))
    return project, graph


def save_turn(session: Any, client: Any, request: SaveRequest) -> dict[str, Any]:
    """Send the turn the request names to UMRS; the receipt."""
    turn, role, text = resolve_turn(list(session.messages), request)
    project, graph = _target(client, request)
    title = request.title or _default_title(text, turn)
    receipt = client.knowledge.save_session_turns(
        project,
        session_id=str(getattr(session, "session_id", "") or ""),
        turns=[{"turn_id": turn, "role": role, "text": text}],
        title=title,
        tags=list(request.tags),
        vouch=request.vouch and role == "assistant",
        graph_id=graph,
    )
    tier, origin = _decided(receipt, role, request.vouch and role == "assistant")
    return {
        "job_id": str((receipt or {}).get("job_id") or ""),
        "project_id": project,
        "graph_id": graph or "",
        "turn_id": turn,
        "role": role,
        "tier": tier,
        "origin": origin,
        "title": title,
        "chars": len(text),
    }


def _default_title(text: str, turn: str) -> str:
    first = " ".join(text.strip().splitlines()[0].split()) if text.strip() else ""
    return (first[:80] + ("…" if len(first) > 80 else "")) or f"session turn {turn}"


def receipt_line(result: dict[str, Any]) -> str:
    """The console receipt: what was saved, where, in which tier; for a
    kept answer a second line with the way to establish it."""
    side = "your message" if result.get("role") == "user" else "answer"
    where = str(result.get("project_id") or "")
    if result.get("graph_id"):
        where = f"{where}/{result.get('graph_id')}"
    mark = {KEPT_ORIGIN: " (kept)", VOUCHED_ORIGIN: " (vouched)"}.get(str(result.get("origin")), "")
    head = (
        f"saved {result.get('turn_id')} ({side}) → {where} · {result.get('tier')}{mark}"
        f" · job {result.get('job_id')} — r4 knowledge ls"
    )
    if result.get("origin") == KEPT_ORIGIN:
        return f"{head}\n  a teammate establishes it in r4 knowledge review"
    return head


def _text(params: dict[str, Any], key: str) -> str:
    return str(params.get(key) or "")


def _structured_request(params: dict[str, Any]) -> SaveRequest:
    """A message action's fields: the turn, the side, the vouch and labels."""
    turn = turn_ref(_text(params, "turn") or "last") or "last"
    part = "prompt" if params.get("part") == "prompt" else "answer"
    tags = tuple(str(t) for t in params.get("tags") or ())
    graph = _text(params, "graph") or None
    return SaveRequest(turn, part, bool(params.get("vouch")), _text(params, "title"), tags, graph)


def request_from_params(params: dict[str, Any]) -> SaveRequest:
    """The bridge's ``knowledge.save`` params: a raw ``args`` line, or the
    structured fields (a message action names its turn and side)."""
    if params.get("args") is None:
        return _structured_request(params)
    request = parse_save_line(_text(params, "args"))
    if request is None:
        raise ValueError(SAVE_USAGE)
    return request


__all__ = [
    "KEPT_ORIGIN",
    "SAVE_USAGE",
    "VOUCHED_ORIGIN",
    "SaveRequest",
    "is_turn_save",
    "parse_save_args",
    "parse_save_line",
    "receipt_line",
    "request_from_params",
    "resolve_turn",
    "save_turn",
    "turn_ref",
]
