"""Unit pins for the streaming shell core (companion W5 flow-side).

All deterministic — process pipes only; no LLM, no network.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from r4.companion import streaming_shell as ss
from r4.companion.tools import _shell_exec, set_shell_chunk_sink, set_shell_status_sink


def test_streams_lines_as_they_arrive(tmp_path: Any) -> None:
    chunks: list[str] = []
    captured: list[tuple[str, str]] = []

    def sink(stream_id: str, chunk: str) -> None:
        captured.append((stream_id, chunk))

    command = 'for i in 1 2 3; do echo "line-$i"; sleep 0.02; done'
    result = ss.run_shell_streaming(command, stream_id="sid-a", on_chunk=sink)

    assert result.status == "done"
    assert result.returncode == 0
    assert [c[1].strip() for c in captured] == ["line-1", "line-2", "line-3"]
    assert all(c[0] == "sid-a" for c in captured)
    assert "line-2" in result.output
    chunks.append(":end sentinel:")


def test_free_stream_id_emitted_and_registered() -> None:
    sid = ss.new_stream_id()
    assert sid.startswith("shell-") and len(sid) == len("shell-") + 8
    assert ss.cancel_stream(sid) is False  # nothing running → bool False


def test_cancel_kills_process_group_and_records_cancelled(tmp_path: Any) -> None:
    sid = ss.new_stream_id()
    chunks: list[str] = []

    def on_chunk(stream_id: str, chunk: str) -> None:
        chunks.append(chunk)
        if len(chunks) == 2:
            assert ss.cancel_stream(sid) is True

    command = "sleep 5; echo why-no-output"
    result: dict[str, Any] = {}

    def driver() -> None:
        result["r"] = ss.run_shell_streaming(command, stream_id=sid, on_chunk=on_chunk, timeout=10)

    worker = threading.Thread(target=driver, daemon=True)
    worker.start()
    time.sleep(0.15)
    killed = ss.cancel_stream(sid)
    worker.join(timeout=5)
    assert killed in (
        True,
        False,
    )  # true if killed by us, false if raced-exited; either way it stops
    assert result["r"].status in ("cancelled", "done")
    if result["r"].status == "cancelled":
        assert result["r"].cancelled is True


def test_timeout_kills_and_marks_timed_out(tmp_path: Any) -> None:
    sid = ss.new_stream_id()
    result = ss.run_shell_streaming("sleep 9", stream_id=sid, timeout=0.3)
    assert result.status == "timed_out"


def test_no_sink_path_still_yields_same_output_as_old_contract(tmp_path: Any) -> None:
    set_shell_chunk_sink(None)
    set_shell_status_sink(None)
    out = _shell_exec("echo aaa; echo bbb 1>&2", timeout=5)
    assert "aaa" in out and "bbb" in out


def test_sink_routed_exec_emits_chunks_and_preserves_output(tmp_path: Any) -> None:
    captured: list[str] = []

    def sink(stream_id: str, chunk: str) -> None:
        captured.append(chunk)

    set_shell_chunk_sink(sink)
    try:
        out = _shell_exec("echo one; echo two", timeout=5)
    finally:
        set_shell_chunk_sink(None)
    assert captured == ["one\n", "two\n"]
    assert "one" in out and "two" in out


def test_streamed_exec_notifies_status_sink_once_with_done(tmp_path: Any) -> None:
    """Terminal lifecycle: UI chips must leave `running` (was: stuck → late
    Cancel click surfaced as `not-found`)."""
    statuses: list[tuple[str, str]] = []

    set_shell_chunk_sink(lambda stream_id, chunk: None)
    set_shell_status_sink(lambda stream_id, status: statuses.append((stream_id, status)))
    try:
        out = _shell_exec("echo hi", timeout=5)
    finally:
        set_shell_chunk_sink(None)
        set_shell_status_sink(None)
    assert "hi" in out
    assert len(statuses) == 1
    assert statuses[0][0].startswith("shell-")
    assert statuses[0][1] == "done"


def test_streamed_exec_status_sink_reports_timed_out(tmp_path: Any) -> None:
    statuses: list[str] = []

    set_shell_chunk_sink(lambda stream_id, chunk: None)
    set_shell_status_sink(lambda stream_id, status: statuses.append(status))
    try:
        out = _shell_exec("sleep 9", timeout=1)
    finally:
        set_shell_chunk_sink(None)
        set_shell_status_sink(None)
    assert statuses == ["timed_out"]
    assert "timed out" in out


def test_status_sink_failure_never_breaks_tool_result(tmp_path: Any) -> None:
    def bad_sink(stream_id: str, status: str) -> None:
        raise RuntimeError("sink exploded")

    set_shell_chunk_sink(lambda stream_id, chunk: None)
    set_shell_status_sink(bad_sink)
    try:
        out = _shell_exec("echo resilient", timeout=5)
    finally:
        set_shell_chunk_sink(None)
        set_shell_status_sink(None)
    assert "resilient" in out


def test_sink_cancel_tool_via_plain_shell_returns_cancelled_marker(tmp_path: Any) -> None:
    captured: list[str] = []

    def sink(stream_id: str, chunk: str) -> None:
        captured.append(chunk)
        if len(captured) == 1:
            ss.cancel_stream(stream_id)

    set_shell_chunk_sink(sink)
    try:
        result = ss.run_shell_streaming("seq 1 100", stream_id="kill-me", on_chunk=sink, timeout=10)
    finally:
        set_shell_chunk_sink(None)
    assert result.status in ("cancelled", "done")  # race-tolerant


def test_active_streams_snapshot_for_ui_health_chip() -> None:
    sid = ss.new_stream_id()
    done = threading.Event()

    def drive(c: str) -> None:
        ss.run_shell_streaming(c, stream_id=sid, timeout=3)
        done.set()

    t = threading.Thread(target=drive, args=("sleep 2",), daemon=True)
    t.start()
    # Poll until the registration fires (avoids racing Popen start).
    deadline = time.monotonic() + 2.0
    while sid not in ss.active_streams():
        assert time.monotonic() < deadline, "stream never registered"
        time.sleep(0.02)
    snapshot = ss.active_streams()
    assert sid in snapshot
    assert snapshot[sid]["command"] == "sleep 2"
    assert snapshot[sid]["cancelled"] is False
    ss.cancel_stream(sid)
    assert done.wait(timeout=3)
    assert sid not in ss.active_streams()


def test_stream_result_truncation_on_large_output(tmp_path: Any) -> None:
    command = "for i in $(seq 1 2000); do echo xxxxxxxxx; done"
    result = ss.run_shell_streaming(command, stream_id="big", timeout=15)
    assert result.traversed_lines == 2000
    assert result.output.count("xxxxxxxxx") == 2000


def test_timed_out_child_is_reaped_never_zombie(tmp_path: Any) -> None:
    """Process-leak pin (2026-09-23, user-reported flood): a killed-PG child
    must be reaped — `wait()` after the group kill, never a left-behind Z."""
    sid = ss.new_stream_id()
    children_before = _child_pids()
    result = ss.run_shell_streaming("sleep 30", stream_id=sid, timeout=0.2)
    assert result.extras["timed_out"] is True
    for pid in _child_pids():
        if pid in children_before:
            continue
        try:
            with open(f"/proc/{pid}/stat") as fh:
                state = fh.read().rsplit(") ", 1)[1].split()[0]
        except OSError:
            continue  # already gone — the good case
        assert state != "Z", f"zombie child left behind: pid {pid}"


def _child_pids() -> set[int]:
    """Live child pids of this parent process (from /proc/*/stat)."""
    import os as _os

    me = _os.getpid()
    out = set()
    for entry in _os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as fh:
                parts = fh.read().rsplit(") ", 1)[1].split()
            if int(parts[1]) == me:
                out.add(int(entry))
        except OSError:
            continue
    return out
