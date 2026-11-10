"""``r4 session live`` and ``r4 session send`` (ADR-114 D-7).

``live`` lists the user's R4 sessions running on this machine (the local
session bus); ``send <id|name> <text…>`` puts a message into one's inbox
from the user's terminal. The receiving console shows a notice and, when
idle, lets its agent read it.
"""

from __future__ import annotations

import json as _json
import sys
from argparse import Namespace
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from r4.companion.session_bus import SessionBus

TERMINAL_SENDER = "terminal"


def _bus() -> SessionBus:
    from r4.companion.session_bus import SessionBus

    return SessionBus()


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M") if epoch else "-"


def _line(entry: Any) -> str:
    return (
        f"{entry.session_id:<28} {entry.name[:24]:<24} {entry.project[:16] or '-':<16} "
        f"{entry.mode or '-':<9} {_when(entry.started_at):<16} {entry.cwd}"
    )


def handle_live(args: Namespace) -> int:
    from r4.companion.session_bus import SessionBusError

    try:
        live = _bus().live_sessions()
    except (SessionBusError, OSError) as exc:
        print(f"session bus unavailable: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "as_json", False):
        print(_json.dumps([e.as_dict() for e in live], indent=2))
        return 0
    if not live:
        print("No R4 sessions are running on this machine.")
        return 0
    print(f"{'SESSION':<28} {'NAME':<24} {'PROJECT':<16} {'MODE':<9} {'STARTED':<16} CWD")
    for entry in live:
        print(_line(entry))
    return 0


def handle_send(args: Namespace) -> int:
    from r4.companion.session_bus import SessionBusError

    target = str(getattr(args, "session_id", "") or "")
    text = " ".join(getattr(args, "answer_args", None) or []).strip()
    if not target or not text:
        print("usage: r4 session send <session id|prefix|name> <message…>", file=sys.stderr)
        return 2
    bus = _bus()
    try:
        entry = bus.resolve(target)
        sent = bus.send(
            sender_id=TERMINAL_SENDER,
            sender_name="you (terminal)",
            recipient_id=entry.session_id,
            body=text,
            sender_kind="user",
        )
    except (SessionBusError, OSError) as exc:
        print(f"not sent: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "as_json", False):
        print(_json.dumps({"sent": sent.message_id, "to": entry.session_id}))
    else:
        print(f"sent {sent.message_id} to {entry.session_id} ({entry.name or 'unnamed'})")
    return 0
