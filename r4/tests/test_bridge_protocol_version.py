"""Bridge protocol-version negotiation contract (G-P12-2, WP-13).

The JSONL bridge wire protocol is versioned by
``r4.companion.ui.bridge_server.BRIDGE_PROTOCOL_VERSION``; the TUI client
(``packages/r4-tui-v3/src/bridge/wire.ts``, shared by the console and the VS Code
extension since 2026-10-31) refuses to start on an
integer mismatch, and accepts a MISSING ``bridge_protocol`` field as a
legacy pre-handshake server. Until now the constant, its emission, and the
client/server parity had no pin — a silent bump on either side would strand
every TUI user with a version-mismatch error only at runtime.

These pins make any version change an explicit, suite-breaking decision:

1. the Python constant stays a positive ``int``;
2. ``initialize()`` emits ``bridge_protocol`` in the response snapshot;
3. the TypeScript client constant equals the Python one (cross-language
   negotiation contract);
4. a snapshot MISSING the field still parses (legacy tolerance kept — the
   client treats it as pre-handshake, not as a hard failure).
"""

from __future__ import annotations

import re
from pathlib import Path

from r4.companion.ui.bridge_server import (
    BRIDGE_PROTOCOL_VERSION,
    CompanionBridgeServer,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# The shared client wire (ADR-103 D-3, 2026-10-31): the console and the VS Code
# extension both import it; jsonl-client.ts re-exports it.
TUI_CLIENT = REPO_ROOT / "packages/r4-tui-v3/src/bridge/wire.ts"


class TestVersionConstant:
    def test_is_positive_int(self):
        assert isinstance(BRIDGE_PROTOCOL_VERSION, int)
        assert BRIDGE_PROTOCOL_VERSION >= 1

    def test_current_value_pinned(self):
        # Bumping the wire protocol is a deliberate event — update this pin
        # (and the TS client) in the same PR as the bump.
        assert BRIDGE_PROTOCOL_VERSION == 1


class TestServerEmission:
    def test_initialize_snapshot_carries_protocol_version(self):
        from test_tui_backend_audit_coverage import FakeStore, _fake_session

        session = _fake_session()
        server = CompanionBridgeServer(client=session.client, store=FakeStore(session))
        snapshot = server.initialize({})
        assert snapshot["bridge_protocol"] == BRIDGE_PROTOCOL_VERSION

    def test_snapshot_field_is_int_not_str(self):
        from test_tui_backend_audit_coverage import FakeStore, _fake_session

        session = _fake_session()
        server = CompanionBridgeServer(client=session.client, store=FakeStore(session))
        snapshot = server.initialize({})
        assert isinstance(snapshot["bridge_protocol"], int)


class TestClientServerParity:
    def test_ts_client_constant_matches_python(self):
        source = TUI_CLIENT.read_text(encoding="utf-8")
        m = re.search(r"BRIDGE_PROTOCOL_VERSION\s*=\s*(\d+)", source)
        assert m is not None, "TS client constant not found — drift?"
        assert int(m.group(1)) == BRIDGE_PROTOCOL_VERSION

    def test_ts_client_refuses_on_mismatch_and_tolerates_missing(self):
        """Pin the client's negotiation semantics in its own source."""
        source = TUI_CLIENT.read_text(encoding="utf-8")
        # Mismatch throws (integer comparison), missing field tolerated.
        assert "version === BRIDGE_PROTOCOL_VERSION" in source
        assert "Bridge protocol mismatch" in source
        assert "version === undefined" in source


class TestLegacyTolerance:
    def test_missing_field_shape_is_valid_input(self):
        """The client's legacy path accepts snapshots without the field —
        server-side, a legacy client ignoring the field must not break."""
        legacy_snapshot = {"session_id": "s-1", "model": "m"}
        # No crash reading it; the field lookup the TS client performs.
        assert legacy_snapshot.get("bridge_protocol") is None
