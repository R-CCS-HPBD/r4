"""K1.6 ``/save`` (knowledge plan 2026-10-24), r4 side.

``/save [n]`` keeps a chat turn in project knowledge, verbatim: the text is
read from the session's messages by turn id (never re-typed by the model)
and sent as a ``session_turns`` source. Before K1.6, ``/save`` only wrote
the transcript to a file (``/save 2`` wrote a file named ``2``) and there was
no user verb for keeping an answer.

The tier and the secret scan are UMRS's (umrs ``tests/test_k1_session_turns``
and ``_pg``); here: the right turn and side are sent, a vouch only for an
answer, the /acquisition mode applies, and an agent-initiated save goes out
marked ``x-r4-actor: agent``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from r4.companion.session_models import SessionMessage
from r4.companion.turn_save import SaveRequest, parse_save_args, parse_save_line
from r4.knowledge import KnowledgeClient
from r4.tui.commands import _cmd_save

T2_ANSWER = "  MOF-5 binds CO2 at 0.8 mmol/g [S1].\n\nKept exactly, spaces and all.\n"


def _messages() -> list[SessionMessage]:
    def msg(role: str, content: str, turn: str = "") -> SessionMessage:
        return SessionMessage(
            role=role, content=content, metadata={"turn_id": turn} if turn else {}
        )

    return [
        msg("user", "first question", "t1"),
        msg("assistant", "first answer", "t1"),
        msg("user", "How much CO2 does MOF-5 take up?", "t2"),
        msg("assistant", "Let me search.", ""),  # interim, before the tool call
        msg("tool", "search_docs: 3 hits", ""),
        msg("assistant", T2_ANSWER, "t2"),
        msg("user", "thanks", "t3"),
        msg("assistant", "you're welcome", "t3"),
    ]


class _Knowledge:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []
        self.write_constraints: dict[str, Any] = {"mode": "open", "graphs": []}

    def save_session_turns(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
        self.saved.append({"project_id": project_id, **kwargs})
        return {"job_id": "job-k16", "status": "queued"}

    def graphs(self) -> list[dict[str, Any]]:
        return [{"graph_id": "g-methods", "name": "methods"}]


class _Renderer:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __getattr__(self, kind: str) -> Any:
        return lambda text: self.lines.append((kind, str(text)))


def _app(tmp_path: Path | None = None) -> Any:
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="example-project-b"), knowledge=_Knowledge()
    )
    session = SimpleNamespace(session_id="sess-42", messages=_messages(), client=client)
    adapter = SimpleNamespace(secure=False, to_markdown=lambda: "# transcript\n")
    return SimpleNamespace(
        client=client, _companion_session=session, session=adapter, renderer=_Renderer()
    )


def _sent(app: Any) -> dict[str, Any]:
    (saved,) = app.client.knowledge.saved
    return saved


def test_save_2_sends_exactly_turn_2s_answer() -> None:
    app = _app()
    _cmd_save(app, ["2"])
    saved = _sent(app)
    assert saved["turns"] == [{"turn_id": "t2", "role": "assistant", "text": T2_ANSWER}]
    assert (saved["project_id"], saved["session_id"], saved["vouch"]) == (
        "example-project-b",
        "sess-42",
        False,
    )
    (kind, line) = app.renderer.lines[-1]
    head, hint = line.split("\n")
    assert kind == "system" and "· proposed (kept) ·" in head
    assert hint.strip() == "a teammate establishes it in r4 knowledge review"
    assert "--vouch" not in line  # a vouch after a keep changes nothing (umrs refuses it)


def test_bare_save_keeps_the_last_answer() -> None:
    app = _app()
    _cmd_save(app, [])
    assert _sent(app)["turns"][0] == {
        "turn_id": "t3",
        "role": "assistant",
        "text": "you're welcome",
    }


def test_vouching_sends_the_vouch_and_reads_established() -> None:
    app = _app()
    _cmd_save(
        app, ["t2", "--vouch", "--title", "MOF-5 uptake", "--tag", "mof", "--graph", "methods"]
    )
    saved = _sent(app)
    assert (saved["vouch"], saved["title"], saved["tags"], saved["graph_id"]) == (
        True,
        "MOF-5 uptake",
        ["mof"],
        "methods",
    )
    assert "· established (vouched) ·" in app.renderer.lines[-1][1]


def test_mine_sends_your_own_message_and_never_a_vouch() -> None:
    app = _app()
    _cmd_save(app, ["2", "mine", "--vouch"])
    saved = _sent(app)
    assert saved["turns"] == [
        {"turn_id": "t2", "role": "user", "text": "How much CO2 does MOF-5 take up?"}
    ]
    assert saved["vouch"] is False
    assert "(your message) → example-project-b · established · job" in app.renderer.lines[-1][1]


def test_a_path_still_writes_the_transcript(tmp_path: Path) -> None:
    app = _app()
    target = tmp_path / "notes.md"
    _cmd_save(app, [str(target)])
    assert target.read_text() == "# transcript\n"
    assert app.client.knowledge.saved == []


def test_a_missing_turn_is_said_and_nothing_is_sent() -> None:
    app = _app()
    _cmd_save(app, ["9"])
    assert app.client.knowledge.saved == []
    assert app.renderer.lines[-1] == ("error", "save failed: no answer for turn t9 here")


def test_acquisition_disabled_refuses_before_any_call() -> None:
    app = _app()
    app.client.knowledge.write_constraints = {"mode": "disabled", "graphs": []}
    _cmd_save(app, ["2"])
    assert app.client.knowledge.saved == []
    # The person's remedy, not the model-facing "do not ingest" text.
    assert app.renderer.lines[-1][1] == (
        "save failed: save refused: knowledge acquisition is disabled for this "
        "session (/acquisition open allows saving)"
    )


def test_a_refusal_from_umrs_is_shown(monkeypatch) -> None:
    app = _app()

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("refused: the text holds a credential-like string (github_token: ghp_…)")

    app.client.knowledge.save_session_turns = _refuse
    _cmd_save(app, ["2"])
    assert "github_token" in app.renderer.lines[-1][1]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("", SaveRequest()),
        ("2", SaveRequest(turn="t2")),
        ("T7 prompt", SaveRequest(turn="t7", part="prompt")),
        ('--title "two words" --tag a --tag b', SaveRequest(title="two words", tags=("a", "b"))),
        ("last --vouch", SaveRequest(vouch=True)),
        ("notes.md", None),
    ],
)
def test_the_grammar(line: str, expected: SaveRequest | None) -> None:
    assert parse_save_line(line) == expected


def test_an_unknown_option_names_the_usage() -> None:
    with pytest.raises(ValueError, match="usage: /save"):
        parse_save_args(["2", "--bogus"])


# ── an agent-initiated save goes out as the agent's ───────────────────────


class _Http:
    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.posts: list[tuple[str, Any, dict[str, str]]] = []

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        self.posts.append((url, json, kwargs.get("headers") or {}))
        return {"job_id": "job-a"}


def test_an_agent_initiated_save_is_marked_agent() -> None:
    http = _Http()
    knowledge = KnowledgeClient(http)  # type: ignore[arg-type]
    turns = [{"turn_id": "t2", "role": "assistant", "text": T2_ANSWER}]
    with knowledge.agent_scope():
        knowledge.save_session_turns("example-project-b", session_id="s", turns=turns, vouch=True)
    knowledge.save_session_turns("example-project-b", session_id="s", turns=turns)
    (url, body, headers), (_url2, _body2, human) = http.posts
    assert url == "/gw/knowledge/ingest/ingest"
    assert body["sources"][0]["kind"] == "session_turns"
    assert body["sources"][0]["turns"] == turns
    assert headers.get("x-r4-actor") == "agent"
    assert "x-r4-actor" not in human


# ── the bridge verb the v3 console calls ──────────────────────────────────


def test_the_bridge_verb_takes_a_line_or_a_message_action() -> None:
    from r4.companion.ui.turn_save_bridge import knowledge_save

    app = _app()
    session = app._companion_session
    by_line = knowledge_save(session, {"args": "2 --vouch"})
    by_action = knowledge_save(session, {"turn": "t1", "part": "prompt"})
    assert (by_line["turn_id"], by_line["tier"], by_line["origin"]) == (
        "t2",
        "established",
        "agent_authored_user_vouched",
    )
    assert (by_action["role"], by_action["tier"]) == ("user", "established")
    assert by_line["line"].startswith("saved t2 (answer")
    with pytest.raises(ValueError, match="usage"):
        knowledge_save(session, {"args": "notes.md"})


# ── review of K1 part B (2026-10-24) ──────────────────────────────────────


def test_the_receipt_prints_the_servers_decision() -> None:
    """The client printed its own tier guess: an agent-scoped or
    server-changed save read "established" while UMRS recorded proposed."""
    app = _app()

    def _save(project_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"job_id": "job-x", "save": {"origin": "agent", "tier": "proposed"}}

    app.client.knowledge.save_session_turns = _save
    _cmd_save(app, ["2", "mine"])
    line = app.renderer.lines[-1][1]
    assert "· proposed · job job-x" in line and "established" not in line


def test_the_receipt_is_short() -> None:
    from r4.companion.turn_save import receipt_line

    line = receipt_line(
        {
            "turn_id": "t3",
            "role": "assistant",
            "project_id": "example-b",
            "graph_id": "",
            "tier": "established",
            "origin": "agent_authored_user_vouched",
            "job_id": "job-0123456789abcdef",
        }
    )
    assert line == (
        "saved t3 (answer) → example-b · established (vouched) · job "
        "job-0123456789abcdef — r4 knowledge ls"
    )
    assert len(line) <= 100


def test_a_pinned_session_names_its_graphs() -> None:
    app = _app()
    app.client.knowledge.write_constraints = {"mode": "pinned", "graphs": ["methods"]}
    _cmd_save(app, ["2", "--graph", "other"])
    assert app.client.knowledge.saved == []
    assert app.renderer.lines[-1][1] == (
        "save failed: save refused: knowledge acquisition is pinned to methods for this "
        "session (/acquisition open allows saving; or --graph one of: methods)"
    )
