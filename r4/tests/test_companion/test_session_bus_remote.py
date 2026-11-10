"""ADR-114 S2.1: remote routing composes with, never replaces, the local bus."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from r4.companion.session_bus import BusMessage, LiveSession, SessionBus, SessionBusError
from r4.companion.session_bus_tools import BusPresence, build_session_bus_tools, inbox_reminder


def _message(message_id: str, sender: str, recipient: str, body: str, sent_at: float) -> BusMessage:
    return BusMessage(
        message_id=message_id,
        sender_id=sender,
        sender_name=sender,
        recipient_id=recipient,
        body=body,
        sent_at=sent_at,
    )


class FakeRemote:
    def __init__(self, session_id: str = "sess-local01"):
        self.session_id = session_id
        self.live: list[LiveSession] = []
        self.messages: list[BusMessage] = []
        self.sent: list[dict] = []
        self.calls: list[str] = []
        self.error = ""

    def _check(self):
        if self.error:
            raise SessionBusError(self.error)

    def live_sessions(self):
        self._check()
        return list(self.live)

    def resolve(self, target: str, *, exclude: str = ""):
        self._check()
        matches = [row for row in self.live if row.session_id != exclude and row.name == target]
        if len(matches) == 1:
            return matches[0]
        raise SessionBusError("no remote session matches")

    def send(self, **kwargs):
        self._check()
        self.sent.append(kwargs)
        message = _message(
            kwargs.get("message_id", "msg-remote0001"),
            kwargs["sender_id"],
            kwargs["recipient_id"],
            kwargs["body"],
            4.0,
        )
        return message

    def unread(self, session_id: str):
        self._check()
        assert session_id == self.session_id
        return [m for m in self.messages if m.recipient_id == session_id]

    def take(self, session_id: str, *, message_ids=None):
        self._check()
        assert session_id == self.session_id
        wanted = set(message_ids or [m.message_id for m in self.messages])
        taken = [m for m in self.messages if m.message_id in wanted]
        self.messages = [m for m in self.messages if m.message_id not in wanted]
        return taken

    def heartbeat(self):
        self.calls.append("heartbeat")

    def withdraw(self):
        self.calls.append("withdraw")


def _local_bus(tmp_path) -> SessionBus:
    bus = SessionBus(root=tmp_path / "bus", host="host-a", clock=lambda: 2.0)
    bus.register(
        LiveSession(session_id="sess-local01", name="local", pid=os.getpid(), host="host-a")
    )
    return bus


def test_local_and_remote_presence_and_inboxes_merge_in_send_order(tmp_path):
    bus = _local_bus(tmp_path)
    remote = FakeRemote()
    remote.live.append(LiveSession(session_id="sess-remote1", name="remote", remote=True))
    bus.send(
        sender_id="sess-other01",
        sender_name="other",
        recipient_id="sess-local01",
        body="local-middle",
    )
    remote.messages = [
        _message("msg-remote0001", "sess-remote1", "sess-local01", "remote-first", 1.0),
        _message("msg-remote0002", "sess-remote1", "sess-local01", "remote-last", 3.0),
    ]
    presence = BusPresence(bus, "sess-local01", "local", remote=remote)
    session = SimpleNamespace(bus_presence=presence)
    tools = {tool["name"]: tool["execute"] for tool in build_session_bus_tools(session)}

    listed = json.loads(tools["session_list"]())
    assert [row["session_id"] for row in listed["sessions"]] == ["sess-remote1"]
    reminder = inbox_reminder(session)
    assert "3 unread" in reminder and "remote-first" not in reminder
    output = tools["session_inbox"](limit=3)
    assert output.index("remote-first") < output.index("local-middle") < output.index("remote-last")
    assert remote.messages == [] and bus.unread("sess-local01") == []


def test_remote_send_uses_durable_identity_and_queue_outage_does_not_fallback(tmp_path):
    bus = _local_bus(tmp_path)
    remote = FakeRemote()
    remote.live.append(LiveSession(session_id="sess-remote1", name="remote", remote=True))
    presence = BusPresence(bus, "sess-local01", "local", remote=remote)
    session = SimpleNamespace(bus_presence=presence)
    tools = {tool["name"]: tool["execute"] for tool in build_session_bus_tools(session)}
    sent = json.loads(tools["session_send"](to="remote", message="result"))
    assert sent["to"] == "sess-remote1"
    assert remote.sent[0]["sender_id"] == "sess-local01"

    remote.error = "queue unavailable"
    refused = tools["session_send"](to="msg-offline01", message="do not misdeliver")
    assert refused.startswith("Error: queue unavailable")
    assert bus.unread("sess-local01") == []


def test_remote_adapter_rechecks_the_server_identity_receipt(tmp_path):
    from r4.companion.session_bus_remote import RemoteSessionBus

    entry = LiveSession(session_id="sess-local01", name="local")
    remote = RemoteSessionBus(
        "sess-local01",
        entry,
        lambda _method, _tail, _body: {
            "session_id": "sess-other99",
            "bus_session_id": "sess-other99",
        },
    )
    with pytest.raises(SessionBusError, match="identity"):
        remote.bind()


def test_remote_adapter_translates_queue_outage_and_never_invents_offline_presence(tmp_path):
    from r4.companion.session_bus_remote import RemoteSessionBus

    def unavailable(_method, _tail, _body):
        raise RuntimeError("connection refused with private details")

    entry = LiveSession(session_id="sess-local01", name="local")
    remote = RemoteSessionBus("sess-local01", entry, unavailable)
    with pytest.raises(SessionBusError, match="unavailable") as exc:
        remote.live_sessions()
    assert "private details" not in str(exc.value)
    assert remote.live == []


def test_remote_adapter_offline_send_is_explicit_and_carries_no_notice_body(tmp_path):
    from r4.companion.session_bus_remote import RemoteSessionBus

    calls = []

    def wire(method, tail, body):
        calls.append((method, tail, body))
        if tail == "presence":
            return {"sessions": []}
        if tail == "send":
            return {
                "duplicate": False,
                "message": {
                    "message_id": body["message_id"],
                    "sender_id": "sess-local01",
                    "sender_name": "local",
                    "recipient_id": body["recipient_id"],
                    "body": body["body"],
                    "sent_at": 1.0,
                    "reply_to": "",
                    "sender_kind": "session",
                },
            }
        raise AssertionError(tail)

    entry = LiveSession(session_id="sess-local01", name="local")
    remote = RemoteSessionBus("sess-local01", entry, wire)
    assert remote.live_sessions() == []
    sent = remote.send(
        sender_id="sess-local01",
        sender_name="local",
        recipient_id="sess-offline1",
        body="queued while offline",
    )
    assert sent.message_id.startswith("msg-")
    notice = {"session_id": "sess-offline1", "unread": 1, "from": ["local"]}
    assert "queued while offline" not in json.dumps(notice)
    assert calls[-1][1] == "send"
