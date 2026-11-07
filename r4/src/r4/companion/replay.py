"""Session replay from the event log.

Replays events from the event log to reconstruct what happened
during a session. This enables:

- ``r4 replay <session-id>`` — chronological event replay
- Crash recovery — reconstruct state from events
- Audit — who did what, when

The replay is purely read-only and does not modify any state.
"""

from __future__ import annotations

from typing import Any

from r4.companion.event_log import EventLog


class SessionReplay:
    """Replay a session from the event log.

    Usage::

        replay = SessionReplay(event_log)
        events = replay.replay_session("session-123")
        for event in events:
            print(f"{event['timestamp']} {event['event_type']}: {event['message']}")
    """

    def __init__(self, event_log: EventLog) -> None:
        self._event_log = event_log

    def replay_session(
        self,
        session_id: str,
        *,
        since: str | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """Replay all events for a session.

        Args:
            session_id: The session ID to replay.
            since: Optional ISO timestamp to start replay from.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts, ordered oldest-to-newest.
        """
        return self._event_log.query(
            session_id,
            since=since,
            limit=limit,
        )

    def replay_agent(
        self,
        session_id: str,
        agent_id: str,
        *,
        since: str | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """Replay all events for a specific agent in a session.

        Args:
            session_id: The session ID to replay.
            agent_id: The agent ID to filter by.
            since: Optional ISO timestamp to start replay from.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts, ordered oldest-to-newest.
        """
        return self._event_log.query(
            session_id,
            agent_id=agent_id,
            since=since,
            limit=limit,
        )

    def summarize(
        self,
        session_id: str,
        *,
        limit: int = 10000,
    ) -> dict[str, Any]:
        """Produce a summary of a session from its events.

        Args:
            session_id: The session ID to summarize.
            limit: Maximum number of events to scan.

        Returns:
            A dict with:
            - ``total_events``: Total number of events
            - ``event_types``: Dict of event_type → count
            - ``agents``: List of unique agent IDs
            - ``tools_used``: List of unique tool names
            - ``first_event``: Timestamp of first event
            - ``last_event``: Timestamp of last event
        """
        events = self.replay_session(session_id, limit=limit)
        if not events:
            return {
                "total_events": 0,
                "event_types": {},
                "agents": [],
                "tools_used": [],
                "first_event": None,
                "last_event": None,
            }

        event_types: dict[str, int] = {}
        agents: set[str] = set()
        tools: set[str] = set()

        for event in events:
            etype = event.get("event_type", "unknown")
            event_types[etype] = event_types.get(etype, 0) + 1
            if event.get("agent_id"):
                agents.add(event["agent_id"])
            if event.get("tool_name"):
                tools.add(event["tool_name"])

        return {
            "total_events": len(events),
            "event_types": event_types,
            "agents": sorted(agents),
            "tools_used": sorted(tools),
            "first_event": events[0].get("timestamp"),
            "last_event": events[-1].get("timestamp"),
        }

    def format_timeline(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> str:
        """Format the event timeline as a readable string.

        Args:
            session_id: The session ID to replay.
            limit: Maximum number of events to return.

        Returns:
            A formatted timeline string.
        """
        events = self.replay_session(session_id, limit=limit)
        if not events:
            return f"(no events found for session {session_id})"

        lines: list[str] = []
        for event in events:
            time_str = event.get("timestamp", "")[:19].replace("T", " ")
            etype = event.get("event_type", "?")
            agent = event.get("agent_id", "")
            msg = event.get("message", "")
            tool = event.get("tool_name", "")

            parts: list[str] = [time_str, etype]
            if agent:
                parts.append(f"[{agent}]")
            if tool:
                parts.append(tool)
            if msg:
                parts.append(msg[:60])

            lines.append("  ".join(parts))

        return "\n".join(lines)
