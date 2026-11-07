"""Sidecar engines (ADR-020 phase 4).

Remote/foreign-language engines reachable over the SPI wire protocol
(JSON-RPC 2.0 control + SSE events). ``RemoteEngine`` will implement
TeamEngine so the runtime cannot distinguish sidecar from in-proc.
See protocol.md for the wire spec. Not implemented in phase 0.
"""

from __future__ import annotations
