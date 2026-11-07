"""C4: session_ops.compact_history — the shared /compact routine."""

from types import SimpleNamespace

from r4.companion.session_models import SessionMessage
from r4.companion.session_ops import DEFAULT_KEEP_RECENT, compact_history


def _make_session(messages, context_compact=None, agent=None):
    return SimpleNamespace(
        messages=messages,
        context=SimpleNamespace(
            compact=context_compact or (lambda payload, keep_recent=6: payload)
        ),
        agent=agent,
    )


def _msg(role, content):
    return SessionMessage(role=role, content=content)


class TestCompactHistoryNoOp:
    def test_small_history_unchanged(self):
        session = _make_session([_msg("user", "hello"), _msg("assistant", "hi")])
        result = compact_history(session)
        assert result["changed"] is False
        assert result["message_count"] == 2
        assert result["keep_recent"] == DEFAULT_KEEP_RECENT
        assert len(session.messages) == 2

    def test_system_only_messages_respected(self):
        session = _make_session([_msg("system", "seed"), _msg("system", "rules")])
        result = compact_history(session)
        assert result["changed"] is False


def _summary_compact(messages, keep_recent=6):
    return (
        [m for m in messages if m["role"] == "system"]
        + [{"role": "system", "content": "[Conversation summary]\nshort"}]
        + [m for m in messages[-keep_recent:]]
    )


def _payload_session(keep_recent=4):
    payload = [_msg("system", "seed")] + [_msg("user", f"u{i}") for i in range(10)]
    session = _make_session(
        payload, context_compact=lambda msgs, keep_recent=6: _summary_compact(msgs, keep_recent)
    )
    return session, len(payload)


class TestCompactHistoryApplies:
    def test_compact_rewrites_messages(self):
        session, original_count = _payload_session()
        result = compact_history(session, keep_recent=4)

        assert result["changed"] is True
        assert result["from"] == original_count
        assert result["to"] == 2 + 4
        contents = [m.content for m in session.messages]
        assert "[Conversation summary]" in contents[1]
        assert contents[-1] == "u9"

    def test_compact_emits_context_compact(self):
        payload = [_msg("system", "seed")] + [_msg("user", f"u{i}") for i in range(10)]
        hits = []
        # The mark carries the kept count (TUI audit r2 #20: the TUI's
        # "context compacted (kept N msgs)" line reads data.kept_messages).
        agent = SimpleNamespace(_emit=lambda kind, msg, data=None: hits.append((kind, msg, data)))
        session = _make_session(
            payload,
            context_compact=lambda msgs, keep_recent=6: msgs,
            agent=agent,
        )

        compact_history(session, keep_recent=4)
        assert hits, "expected CONTEXT_COMPACT emission"
        assert hits[0][2] == {"kept_messages": len(session.messages)}

    def test_empty_content_messages_filtered(self):
        payload = [_msg("user", "x" * 10 + str(i)) for i in range(10)]

        def fake_compact(messages, keep_recent=6):
            return [
                {"role": "system", "content": "[Conversation summary]\nshort"},
                *[{"role": "assistant", "content": ""}, {"role": "user", "content": "keep"}],
            ]

        session = _make_session(payload, context_compact=fake_compact)
        result = compact_history(session, keep_recent=3)
        assert result["changed"] is True
        assert all(m.content for m in session.messages)


class TestCompactHistoryKeepRecentParam:
    def test_explicit_keep_recent(self):
        payload = [_msg("user", f"u{i}") for i in range(10)]
        session = _make_session(payload)
        result = compact_history(session, keep_recent=3)
        assert result["keep_recent"] == 3

    def test_minimum_one(self):
        payload = [_msg("user", f"u{i}") for i in range(5)]
        session = _make_session(payload)
        result = compact_history(session, keep_recent=0)
        assert result["changed"] is True
        assert result["keep_recent"] == 1
