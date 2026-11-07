"""W6 (2026-10-05, D-52): headless session transcripts."""

from __future__ import annotations

import json
from types import SimpleNamespace

from r4.companion.agent import CompanionEvent, CompanionEventType
from r4.companion.transcript import TranscriptWriter, attach_transcript, transcript_path_for


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class TestPathSelection:
    def test_disabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_TRANSCRIPT_PATH", raising=False)
        monkeypatch.delenv("R4_TRANSCRIPT_DIR", raising=False)
        assert transcript_path_for("s1") is None

    def test_explicit_file_then_directory(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("R4_TRANSCRIPT_DIR", str(tmp_path))
        assert transcript_path_for("s1") == tmp_path / "s1.jsonl"
        monkeypatch.setenv("R4_TRANSCRIPT_PATH", str(tmp_path / "t.jsonl"))
        assert transcript_path_for("s1") == tmp_path / "t.jsonl"


class TestWriter:
    def test_events_rounds_and_turns(self, tmp_path) -> None:
        writer = TranscriptWriter(tmp_path / "t.jsonl", "s1")
        writer.write("turn_start", "solve the task")
        writer.on_event(
            CompanionEvent(
                event_type=CompanionEventType.TOOL_CALL,
                message="Calling tool: shell_exec",
                data={"name": "shell_exec", "arguments": '{"command": "ls"}', "tool_call_id": "c1"},
            )
        )
        writer.model_round("chat", {"prompt_tokens": 10, "completion_tokens": 3})
        writer.write("turn_end", "done")
        rows = _lines(tmp_path / "t.jsonl")
        assert [r["kind"] for r in rows] == ["turn_start", "tool_call", "model_round", "turn_end"]
        assert rows[1]["data"]["tool_call_id"] == "c1" and rows[2]["data"]["prompt_tokens"] == 10
        assert all(r["session_id"] == "s1" and r["ts"] for r in rows)

    def test_long_text_is_capped(self, tmp_path) -> None:
        writer = TranscriptWriter(tmp_path / "t.jsonl", "s1")
        writer.write("turn_end", "x" * 20_000)
        message = _lines(tmp_path / "t.jsonl")[0]["message"]
        assert len(message) < 8_100 and message.endswith("chars]")

    def test_unwritable_path_never_raises(self, tmp_path) -> None:
        writer = TranscriptWriter(tmp_path / "t.jsonl", "s1")
        writer.path = tmp_path  # a directory: open() fails
        writer.write("turn_end", "still fine")


class TestAttach:
    def test_subscribes_to_agent_events(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("R4_TRANSCRIPT_PATH", str(tmp_path / "t.jsonl"))
        listeners = []
        session = SimpleNamespace(session_id="s9", agent=SimpleNamespace(on_event=listeners.append))
        writer = attach_transcript(session)
        assert writer is not None and listeners == [writer.on_event]

    def test_disabled_returns_none(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_TRANSCRIPT_PATH", raising=False)
        monkeypatch.delenv("R4_TRANSCRIPT_DIR", raising=False)
        assert attach_transcript(SimpleNamespace(session_id="s", agent=None)) is None
