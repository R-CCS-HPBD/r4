"""R1-5 on the streamed shell path (2026-10-05): no session tokens in tool subprocesses."""

from __future__ import annotations

from r4.companion import streaming_shell


def test_streamed_shell_drops_session_tokens_in_a_venue(monkeypatch) -> None:
    monkeypatch.setattr("r4.venue.in_venue", lambda: True)
    monkeypatch.setenv("R4_SESSION_TOKEN", "secret-token-value")
    monkeypatch.setenv("BENIGN_SETTING", "kept")
    result = streaming_shell.run_shell_streaming("env", stream_id="t-env", timeout=30)
    assert "secret-token-value" not in result.output
    assert "BENIGN_SETTING=kept" in result.output
    assert "PYTHONUNBUFFERED=1" in result.output
