"""
Reasoning Trace Output (A-911)

Provides full decision trace for transparency in agentic search.
Captures all steps, decisions, tool calls, and reasoning for
debugging and explanation purposes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TraceEventType(Enum):
    """Type of trace event."""

    # Search lifecycle
    SEARCH_START = "search_start"
    SEARCH_END = "search_end"

    # Query processing
    QUERY_RECEIVED = "query_received"
    QUERY_DECOMPOSED = "query_decomposed"

    # Planning
    PLAN_CREATED = "plan_created"
    STEP_PLANNED = "step_planned"

    # Execution
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"

    # Synthesis
    SYNTHESIS_START = "synthesis_start"
    SYNTHESIS_PROGRESS = "synthesis_progress"
    SYNTHESIS_END = "synthesis_end"

    # Iteration
    GAP_DETECTED = "gap_detected"
    ITERATION_START = "iteration_start"
    ITERATION_END = "iteration_end"

    # Decision points
    DECISION = "decision"
    REASONING = "reasoning"

    # Other
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class TraceEvent:
    """Single event in the reasoning trace."""

    event_type: TraceEventType
    timestamp: datetime
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None
    parent_event_id: Optional[str] = None
    event_id: str = ""

    def __post_init__(self) -> None:
        """Generate event ID if not provided."""
        if not self.event_id:
            self.event_id = f"{self.event_type.value}_{self.timestamp.timestamp()}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "data": self.data,
            "duration_ms": self.duration_ms,
            "parent_event_id": self.parent_event_id,
        }


@dataclass
class ToolCallTrace:
    """Trace of a single tool call."""

    tool_name: str
    arguments: dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class DecisionTrace:
    """Trace of a decision point."""

    decision_type: str
    options: list[str]
    chosen: str
    reasoning: str
    confidence: float = 0.0
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "decision_type": self.decision_type,
            "options": self.options,
            "chosen": self.chosen,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "factors": self.factors,
        }


@dataclass
class ReasoningTrace:
    """Complete reasoning trace for a search session."""

    session_id: str
    query: str
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    events: list[TraceEvent] = field(default_factory=list)
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    decisions: list[DecisionTrace] = field(default_factory=list)
    sub_questions: list[str] = field(default_factory=list)
    iterations: int = 0
    final_confidence: float = 0.0
    success: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "query": self.query,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "events": [e.to_dict() for e in self.events],
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "decisions": [d.to_dict() for d in self.decisions],
            "sub_questions": self.sub_questions,
            "iterations": self.iterations,
            "final_confidence": self.final_confidence,
            "success": self.success,
            "error": self.error,
        }

    @property
    def duration_ms(self) -> Optional[float]:
        """Get total duration in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() * 1000
        return None

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Path) -> None:
        """Save trace to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())


class TraceCollector:
    """
    Collects reasoning trace during agentic search (A-911).

    Provides methods to record events, tool calls, and decisions
    for full transparency into the search process.
    """

    def __init__(self, session_id: str, query: str):
        """
        Initialize trace collector.

        Args:
            session_id: Unique session identifier
            query: Original search query
        """
        self.trace = ReasoningTrace(
            session_id=session_id,
            query=query,
        )
        self._event_stack: list[str] = []  # For nested events

    def event(
        self,
        event_type: TraceEventType,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> TraceEvent:
        """
        Record an event.

        Args:
            event_type: Type of event
            message: Event message
            data: Additional event data

        Returns:
            Created event
        """
        event = TraceEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            message=message,
            data=data or {},
            parent_event_id=self._event_stack[-1] if self._event_stack else None,
        )
        self.trace.events.append(event)
        return event

    def start_event(
        self,
        event_type: TraceEventType,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Start a nested event.

        Args:
            event_type: Type of event
            message: Event message
            data: Additional event data

        Returns:
            Event ID for ending later
        """
        event = self.event(event_type, message, data)
        self._event_stack.append(event.event_id)
        return event.event_id

    def end_event(
        self,
        event_id: str,
        message: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        End a nested event.

        Args:
            event_id: Event ID from start_event
            message: Optional completion message
            data: Optional completion data
        """
        if self._event_stack and self._event_stack[-1] == event_id:
            self._event_stack.pop()

        # Find the start event and calculate duration
        for event in self.trace.events:
            if event.event_id == event_id:
                duration = (datetime.now() - event.timestamp).total_seconds() * 1000
                event.duration_ms = duration

                if message:
                    # Add completion event
                    self.event(
                        TraceEventType.INFO,
                        message,
                        {"duration_ms": duration, **(data or {})},
                    )
                break

    def tool_call_start(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolCallTrace:
        """
        Record start of tool call.

        Args:
            tool_name: Name of tool
            arguments: Tool arguments

        Returns:
            ToolCallTrace for completion
        """
        trace = ToolCallTrace(
            tool_name=tool_name,
            arguments=arguments,
            start_time=datetime.now(),
        )
        self.trace.tool_calls.append(trace)

        self.event(
            TraceEventType.TOOL_CALL_START,
            f"Calling tool: {tool_name}",
            {"arguments": arguments},
        )

        return trace

    def tool_call_end(
        self,
        trace: ToolCallTrace,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Record end of tool call.

        Args:
            trace: ToolCallTrace from start
            result: Tool result
            error: Error if failed
        """
        trace.end_time = datetime.now()
        trace.result = result
        trace.error = error

        if trace.start_time:
            trace.duration_ms = (trace.end_time - trace.start_time).total_seconds() * 1000

        if error:
            self.event(
                TraceEventType.TOOL_ERROR,
                f"Tool {trace.tool_name} failed: {error}",
                {"duration_ms": trace.duration_ms},
            )
        else:
            self.event(
                TraceEventType.TOOL_CALL_END,
                f"Tool {trace.tool_name} completed",
                {
                    "duration_ms": trace.duration_ms,
                    "result_preview": str(result)[:200] if result else None,
                },
            )

    def decision(
        self,
        decision_type: str,
        options: list[str],
        chosen: str,
        reasoning: str,
        confidence: float = 0.0,
        factors: Optional[dict[str, Any]] = None,
    ) -> DecisionTrace:
        """
        Record a decision point.

        Args:
            decision_type: Type of decision
            options: Available options
            chosen: Chosen option
            reasoning: Reasoning for choice
            confidence: Confidence in decision
            factors: Factors considered

        Returns:
            DecisionTrace
        """
        trace = DecisionTrace(
            decision_type=decision_type,
            options=options,
            chosen=chosen,
            reasoning=reasoning,
            confidence=confidence,
            factors=factors or {},
        )
        self.trace.decisions.append(trace)

        self.event(
            TraceEventType.DECISION,
            f"Decision ({decision_type}): {chosen}",
            {
                "reasoning": reasoning,
                "confidence": confidence,
                "options": options,
            },
        )

        return trace

    def reasoning(self, step: str, explanation: str) -> None:
        """
        Record reasoning step.

        Args:
            step: Step description
            explanation: Detailed explanation
        """
        self.event(
            TraceEventType.REASONING,
            f"Reasoning: {step}",
            {"explanation": explanation},
        )

    def decomposition(self, sub_questions: list[str]) -> None:
        """
        Record query decomposition.

        Args:
            sub_questions: Decomposed sub-questions
        """
        self.trace.sub_questions = sub_questions
        self.event(
            TraceEventType.QUERY_DECOMPOSED,
            f"Query decomposed into {len(sub_questions)} sub-questions",
            {"sub_questions": sub_questions},
        )

    def gap_detected(self, description: str, importance: float) -> None:
        """
        Record detected knowledge gap.

        Args:
            description: Gap description
            importance: Gap importance
        """
        self.event(
            TraceEventType.GAP_DETECTED,
            f"Gap detected: {description}",
            {"importance": importance},
        )

    def iteration(self, iteration_number: int, reason: str) -> None:
        """
        Record iteration start.

        Args:
            iteration_number: Current iteration
            reason: Reason for iteration
        """
        self.trace.iterations = iteration_number
        self.event(
            TraceEventType.ITERATION_START,
            f"Starting iteration {iteration_number}: {reason}",
        )

    def complete(
        self,
        success: bool = True,
        confidence: float = 0.0,
        error: Optional[str] = None,
    ) -> ReasoningTrace:
        """
        Complete the trace.

        Args:
            success: Whether search succeeded
            confidence: Final confidence
            error: Error message if failed

        Returns:
            Complete reasoning trace
        """
        self.trace.end_time = datetime.now()
        self.trace.success = success
        self.trace.final_confidence = confidence
        self.trace.error = error

        self.event(
            TraceEventType.SEARCH_END,
            f"Search {'completed' if success else 'failed'}",
            {
                "success": success,
                "confidence": confidence,
                "duration_ms": self.trace.duration_ms,
                "tool_calls": len(self.trace.tool_calls),
                "iterations": self.trace.iterations,
            },
        )

        return self.trace

    def get_trace(self) -> ReasoningTrace:
        """Get the current trace."""
        return self.trace


class TraceFormatter:
    """
    Format reasoning traces for display.

    Provides multiple output formats: text, markdown, JSON, HTML.
    """

    @staticmethod
    def to_text(trace: ReasoningTrace) -> str:
        """Format trace as plain text."""
        lines = [
            f"=== Reasoning Trace: {trace.session_id} ===",
            f"Query: {trace.query}",
            f"Duration: {trace.duration_ms:.0f}ms" if trace.duration_ms else "",
            f"Success: {trace.success}",
            f"Confidence: {trace.final_confidence:.2f}",
            "",
            "--- Events ---",
        ]

        for event in trace.events:
            indent = "  " if event.parent_event_id else ""
            time_str = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
            lines.append(f"{indent}[{time_str}] {event.event_type.value}: {event.message}")

        if trace.tool_calls:
            lines.extend(["", "--- Tool Calls ---"])
            for tc in trace.tool_calls:
                status = "✓" if tc.result else ("✗" if tc.error else "?")
                lines.append(
                    f"  {status} {tc.tool_name} ({tc.duration_ms:.0f}ms)"
                    if tc.duration_ms
                    else f"  {status} {tc.tool_name}"
                )

        if trace.decisions:
            lines.extend(["", "--- Decisions ---"])
            for dec in trace.decisions:
                lines.append(f"  [{dec.decision_type}] Chose: {dec.chosen}")
                lines.append(f"    Reasoning: {dec.reasoning}")

        return "\n".join(lines)

    @staticmethod
    def to_markdown(trace: ReasoningTrace) -> str:
        """Format trace as Markdown."""
        lines = [
            f"# Reasoning Trace: `{trace.session_id}`",
            "",
            f"**Query:** {trace.query}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Duration | {trace.duration_ms:.0f}ms |" if trace.duration_ms else "",
            f"| Success | {'✓' if trace.success else '✗'} |",
            f"| Confidence | {trace.final_confidence:.2f} |",
            f"| Iterations | {trace.iterations} |",
            f"| Tool Calls | {len(trace.tool_calls)} |",
            "",
        ]

        if trace.sub_questions:
            lines.extend(
                [
                    "## Sub-Questions",
                    "",
                ]
            )
            for i, sq in enumerate(trace.sub_questions, 1):
                lines.append(f"{i}. {sq}")
            lines.append("")

        if trace.decisions:
            lines.extend(
                [
                    "## Key Decisions",
                    "",
                ]
            )
            for dec in trace.decisions:
                lines.extend(
                    [
                        f"### {dec.decision_type}",
                        "",
                        f"**Chosen:** {dec.chosen}  ",
                        f"**Confidence:** {dec.confidence:.2f}",
                        "",
                        f"> {dec.reasoning}",
                        "",
                    ]
                )

        lines.extend(
            [
                "## Timeline",
                "",
                "```",
            ]
        )

        for event in trace.events[:50]:  # Limit for readability
            time_str = event.timestamp.strftime("%H:%M:%S")
            lines.append(f"[{time_str}] {event.event_type.value}: {event.message}")

        if len(trace.events) > 50:
            lines.append(f"... and {len(trace.events) - 50} more events")

        lines.append("```")

        return "\n".join(lines)

    @staticmethod
    def to_html(trace: ReasoningTrace) -> str:
        """Format trace as HTML."""
        events_html = ""
        for event in trace.events:
            event_class = event.event_type.value.replace("_", "-")
            events_html += f'<div class="event {event_class}">'
            events_html += f'<span class="time">{event.timestamp.strftime("%H:%M:%S")}</span>'
            events_html += f'<span class="type">{event.event_type.value}</span>'
            events_html += f'<span class="message">{event.message}</span>'
            events_html += "</div>\n"

        return f"""<!DOCTYPE html>
<html>
<head>
    <title>Reasoning Trace: {trace.session_id}</title>
    <style>
        body {{
            font-family: system-ui, sans-serif; max-width: 900px;
            margin: 0 auto; padding: 20px;
        }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 15px; border-radius: 5px; }}
        .events {{ margin-top: 20px; }}
        .event {{ padding: 8px; border-left: 3px solid #ddd; margin: 5px 0; }}
        .event.tool-call-start {{ border-color: #3498db; }}
        .event.tool-call-end {{ border-color: #2ecc71; }}
        .event.tool-error {{ border-color: #e74c3c; }}
        .event.decision {{ border-color: #9b59b6; }}
        .event.gap-detected {{ border-color: #f39c12; }}
        .time {{ color: #888; margin-right: 10px; }}
        .type {{ background: #eee; padding: 2px 6px; border-radius: 3px; margin-right: 10px; }}
    </style>
</head>
<body>
    <h1>Reasoning Trace: {trace.session_id}</h1>
    <div class="summary">
        <p><strong>Query:</strong> {trace.query}</p>
        <p><strong>Duration:</strong> {trace.duration_ms:.0f}ms</p>
        <p><strong>Confidence:</strong> {trace.final_confidence:.2f}</p>
        <p><strong>Success:</strong> {"✓" if trace.success else "✗"}</p>
    </div>
    <div class="events">
        <h2>Timeline</h2>
        {events_html}
    </div>
</body>
</html>"""


def create_tracer(session_id: str, query: str) -> TraceCollector:
    """
    Create a new trace collector.

    Convenience function for creating tracers.

    Args:
        session_id: Session identifier
        query: Search query

    Returns:
        TraceCollector instance
    """
    return TraceCollector(session_id, query)
