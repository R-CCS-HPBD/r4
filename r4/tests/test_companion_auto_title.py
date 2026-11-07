"""Auto session titles (bridge-wired first-turn naming) — pure unit pins."""

from __future__ import annotations

from types import SimpleNamespace

from r4.companion.auto_title import (
    build_title_messages,
    first_exchange_contents,
    generate_session_title,
    is_default_name,
    sanitize_title,
)


def _session(name: str, messages: list) -> SimpleNamespace:
    return SimpleNamespace(name=name, messages=messages)


class TestIsDefaultName:
    def test_timestamped_default_matches(self) -> None:
        assert is_default_name("Session 2026-09-29 22:40")

    def test_user_names_do_not_match(self) -> None:
        for name in ("fix the NaN bug", "Session planning", "Headless", "", "  "):
            assert not is_default_name(name), name

    def test_non_string_never_matches(self) -> None:
        assert not is_default_name(None)
        assert not is_default_name(42)


class TestSanitizeTitle:
    def test_strips_quotes_and_punctuation(self) -> None:
        assert sanitize_title('"Fix the auth bug."') == "Fix the auth bug"

    def test_single_line_only(self) -> None:
        assert sanitize_title("First line\nsecond line") == "First line"

    def test_collapses_whitespace(self) -> None:
        assert sanitize_title("  a\t\tb   c ") == "a b c"

    def test_caps_words_at_eight(self) -> None:
        title = sanitize_title("one two three four five six seven eight nine ten")
        assert title == "one two three four five six seven eight"

    def test_caps_chars(self) -> None:
        long_title = "word " * 7 + "tail"
        assert len(sanitize_title(long_title)) <= 60

    def test_marks_empty_unusable(self) -> None:
        for raw in ("", "  ", ".", '"..."', "\n", None, 3):
            assert sanitize_title(raw) is None, raw

    def test_strips_japanese_sentence_punctuation(self) -> None:
        assert sanitize_title("認証バグの修正。") == "認証バグの修正"


class TestFirstExchange:
    def test_first_pair(self) -> None:
        messages = [
            SimpleNamespace(role="user", content="hello there"),
            SimpleNamespace(role="assistant", content="hi, how can I help"),
            SimpleNamespace(role="user", content="later"),
        ]
        assert first_exchange_contents(messages) == ("hello there", "hi, how can I help")

    def test_missing_pair_returns_none(self) -> None:
        assert first_exchange_contents([SimpleNamespace(role="user", content="hi")]) is None
        assert first_exchange_contents([]) is None


class TestGenerateSessionTitle:
    def _chat_ok(self, messages, tools):
        return {"choices": [{"message": {"content": "Fix the NaN crash"}}]}

    def test_titles_default_named_session(self) -> None:
        session = _session(
            "Session 2026-09-29 22:40",
            [
                SimpleNamespace(role="user", content="fix nan in report"),
                SimpleNamespace(role="assistant", content="done"),
            ],
        )
        assert generate_session_title(session, self._chat_ok) == "Fix the NaN crash"

    def test_user_named_session_never_renamed(self) -> None:
        session = _session(
            "my pinned name",
            [
                SimpleNamespace(role="user", content="fix nan"),
                SimpleNamespace(role="assistant", content="done"),
            ],
        )
        assert generate_session_title(session, self._chat_ok) is None

    def test_no_exchange_no_ask(self) -> None:
        session = _session("Session 2026-09-29 22:40", [])

        def explode(messages, tools):  # pragma: no cover - guard test
            raise AssertionError("must not be called")

        assert generate_session_title(session, explode) is None

    def test_chat_failure_is_silent_none(self) -> None:
        session = _session(
            "Session 2026-09-29 22:40",
            [
                SimpleNamespace(role="user", content="fix nan"),
                SimpleNamespace(role="assistant", content="done"),
            ],
        )

        def boom(messages, tools):
            raise RuntimeError("provider down")

        assert generate_session_title(session, boom) is None

    def test_bad_model_output_dropped(self) -> None:
        session = _session(
            "Session 2026-09-29 22:40",
            [
                SimpleNamespace(role="user", content="fix nan"),
                SimpleNamespace(role="assistant", content="done"),
            ],
        )
        for raw in (
            {"choices": [{"message": {"content": "..."}}]},
            {"choices": []},
            {"unexpected": True},
        ):
            assert generate_session_title(session, lambda m, t, r=raw: r) is None, raw

    def test_prompt_carries_excerpts(self) -> None:
        captured: list[dict] = []

        def spy(messages, tools):
            captured.extend(messages)
            return {"choices": [{"message": {"content": "title"}}]}

        session = _session(
            "Session 2026-09-29 22:40",
            [
                SimpleNamespace(role="user", content="u" * 2000),
                SimpleNamespace(role="assistant", content="a" * 2000),
            ],
        )
        generate_session_title(session, spy)
        user_prompt = next(m["content"] for m in captured if m["role"] == "user")
        assert "u" * 800 in user_prompt
        assert "u" * 801 not in user_prompt

    def test_build_title_messages_shape(self) -> None:
        messages = build_title_messages("hello", "world")
        assert [m["role"] for m in messages] == ["system", "user"]
        assert "8 words" in messages[0]["content"]
