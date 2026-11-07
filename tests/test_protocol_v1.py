"""D-04 contract pins: protocol version constants + handshake both sides."""

from __future__ import annotations

from r4_contracts.protocol import (
    PROTOCOL_VERSION,
    handshake_reply,
    is_protocol_compatible,
    parse_handshake_reply,
)


def test_version_constant_shape():
    major, minor = PROTOCOL_VERSION.split(".")
    assert major.isdigit() and minor.isdigit()


def test_server_reply_carries_version():
    reply = handshake_reply()
    assert reply == f"READY!:v{PROTOCOL_VERSION}"
    assert reply.startswith("READY!")


def test_client_tolerates_pre_v1_bare_ready():
    assert parse_handshake_reply("READY!") == (True, "")


def test_client_parses_versioned_ready():
    assert parse_handshake_reply("READY!:v1.0") == (True, "1.0")


def test_garbage_is_not_ready():
    assert parse_handshake_reply("NOPE") == (False, "")


def test_major_compat():
    assert is_protocol_compatible("1.0", "1.7")
    assert is_protocol_compatible("", "1.0")  # pre-1.0 peer: v0 lane
    assert not is_protocol_compatible("2.0", "1.0")


def test_umrs_server_handshake_answers_versioned():
    from unittest.mock import MagicMock

    from umrs.core.MessageQueue import MessageQueue

    self_ = MagicMock()
    sent = []
    self_.send_string = lambda msg, who: sent.append((msg, who))
    MessageQueue._parse_handshake(self_, [], "zas")
    assert sent == [(f"READY!:v{PROTOCOL_VERSION}", "zas")]


def test_r4_client_parses_via_shared_helper():
    """Client-side contract: the shared parser is the client's ingestion
    seam — umrs_transport adopts it at its next ratchet-touch (the file's
    pre-existing CCN gates a doc-wave edit; see PR notes / D-04 ledger)."""
    assert parse_handshake_reply("READY!") == (True, "")
    assert parse_handshake_reply(f"READY!:v{PROTOCOL_VERSION}") == (
        True,
        PROTOCOL_VERSION,
    )
    assert parse_handshake_reply("NOPE") == (False, "")
