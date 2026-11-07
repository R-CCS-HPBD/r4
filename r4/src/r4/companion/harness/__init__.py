"""Research harness — invisible quality and context layer.

The harness makes the companion effective *without the researcher knowing
anything about agentic tools*. It assembles context, validates work,
self-corrects, and bridges sessions — all transparently.

Components:
  EnvironmentProbe  — auto-discover the researcher's environment
  FeedforwardGuides — assemble context layers for the system prompt
  QualitySensor     — pre/post-execution validation
  ProgressTracker   — cross-session continuity and milestone tracking
  SteeringLoop      — self-improvement from corrections
  HarnessTemplate   — pre-configured workflow bundles
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import agent_profile
from ..ablation import guides_off, steering_off
from .guides import FeedforwardGuides
from .probe import EnvironmentProbe, EnvironmentSnapshot
from .progress import ProgressTracker
from .sensors import QualitySensor
from .steering import SteeringLoop
from .templates import HarnessTemplate, get_builtin_templates

__all__ = [
    "ResearchHarness",
    "EnvironmentProbe",
    "EnvironmentSnapshot",
    "FeedforwardGuides",
    "QualitySensor",
    "ProgressTracker",
    "SteeringLoop",
    "HarnessTemplate",
    "get_builtin_templates",
]


class ResearchHarness:
    """Orchestrates all harness components into a single facçade.

    CompanionSession talks only to this class; internal wiring is opaque.
    """

    def __init__(self, *, client, context_manager, skill_registry):
        self._client = client
        #: Where harness-side decisions log (the session points it at its own).
        self.decision_log: list = []
        self.probe = EnvironmentProbe(client=client)
        self._skill_registry = skill_registry
        self.guides = FeedforwardGuides(
            context_manager=context_manager, skill_registry=skill_registry
        )
        self.sensors = QualitySensor()
        self.progress = ProgressTracker()
        self.steering = SteeringLoop()
        #: ADR-111: the session's ``/memory off`` switch; when it answers
        #: False, lessons are neither shown nor recorded for this session.
        self.memory_enabled: Callable[[], bool] = lambda: True
        self._templates = get_builtin_templates()
        self._active_template: HarnessTemplate | None = None

        # Run initial probe
        self._snapshot = self.probe.run()

    def feedforward(self, messages: list) -> str:
        """Assemble all feedforward context for system prompt injection."""
        parts: list[str] = []

        # Environment snapshot
        env_prompt = self.probe.as_prompt(self._snapshot)
        if env_prompt:
            parts.append(env_prompt)

        # Progress orientation
        orientation = self.progress.orientation()
        if orientation:
            parts.append(orientation)

        # Lessons from steering (benchmark ablation: R4_HARNESS_ABLATE_STEERING)
        if not steering_off() and self._memory_on():
            lessons = self.steering.as_prompt()
            if lessons:
                parts.append(lessons)

        parts.extend(self._guides(messages))
        return "\n\n".join(parts)

    def _guides(self, messages: list[Any]) -> list[str]:
        """Domain guides + workflow templates (ablation: R4_HARNESS_ABLATE_GUIDES;
        the terminal profile, H2, carries neither)."""
        if guides_off() or agent_profile.active():
            return []
        parts: list[str] = []
        # Domain guides (lazy — only after enough messages)
        if len(messages) >= 2:
            domain = self.guides.detect_domain(messages)
            if domain:
                parts.append(domain)
        # Workflow template matching
        template_prompt = self._template_for_messages(messages)
        if template_prompt:
            parts.append(template_prompt)
        return parts

    @staticmethod
    def _last_user_content(messages: list) -> str | None:
        """Extract content of the last user message from a message list."""
        for msg in reversed(messages):
            if hasattr(msg, "role") and msg.role == "user":
                return getattr(msg, "content", "") or None
        return None

    def list_templates(self) -> list[HarnessTemplate]:
        return list(self._templates)

    def find_template(self, name: str) -> HarnessTemplate | None:
        normalized = name.strip().lower().replace("-", "_")
        for template in self._templates:
            if template.name.lower() == normalized:
                return template
        return None

    def active_template(self) -> HarnessTemplate | None:
        return self._active_template

    def clear_active_template(self) -> None:
        self._active_template = None

    def apply_template(self, name: str) -> dict[str, Any]:
        """Select a workflow template for this session."""
        template = self.find_template(name)
        if template is None:
            raise ValueError(f"unknown template: {name}")

        self._active_template = template

        activated_skills: list[str] = []
        missing_skills: list[str] = []
        if self._skill_registry is not None:
            for skill_name in template.skills:
                if self._skill_registry.activate(skill_name):
                    activated_skills.append(skill_name)
                else:
                    missing_skills.append(skill_name)

        existing = {milestone.label for milestone in self.progress._state.milestones}
        milestones_added: list[str] = []
        for label in template.milestones:
            if label in existing:
                continue
            self.progress.add_milestone(label)
            milestones_added.append(label)

        return {
            "template": template.name,
            "description": template.description,
            "activated_skills": activated_skills,
            "missing_skills": missing_skills,
            "milestones_added": milestones_added,
        }

    @staticmethod
    def _template_prompt(template: HarnessTemplate) -> str:
        lines = [f"[Workflow: {template.name} — {template.description}]"]
        if template.guidance:
            lines.append(template.guidance)
        if template.milestones:
            lines.append("Milestones: " + " → ".join(template.milestones))
        return "\n".join(lines)

    def _template_for_messages(self, messages: list) -> str | None:
        """Return the active or auto-detected workflow template prompt."""
        if self._active_template is not None:
            return self._template_prompt(self._active_template)

        if not messages or not self._templates:
            return None

        last_user = self._last_user_content(messages)
        if not last_user:
            return None

        best = max(self._templates, key=lambda t: t.matches(last_user))
        score = best.matches(last_user)
        if score < 2:
            return None

        return self._template_prompt(best)

    def pre_tool(self, tool_name: str, arguments: dict) -> str | None:
        """Run pre-execution sensors. Returns warning/block message or None."""
        return self.sensors.pre_execution(tool_name, arguments)

    def post_tool(self, tool_name: str, arguments: dict, result: str) -> str | None:
        """Run post-execution sensors. Returns diagnostic or None."""
        return self.sensors.post_execution(tool_name, arguments, result)

    def post_turn(self, *, user_message: str, response: str, messages: list) -> None:
        """Run post-turn hooks: progress and steering updates."""
        self.progress.record_turn(user_message, response)
        if not steering_off() and self._memory_on():
            self.steering.observe_turn(user_message, response, confirm=self._lesson_is_real)

    def _memory_on(self) -> bool:
        """The session's ``/memory`` switch; on when nothing wired one."""
        switch = getattr(self, "memory_enabled", None)
        return bool(switch()) if callable(switch) else True

    def _lesson_is_real(self, user_message: str, last_response: str) -> bool:
        """Decision-model veto for a rule-matched correction (ADR-076; True when off)."""
        from r4.decisions.harness import lesson_is_real

        return lesson_is_real(self._client, user_message, last_response, log=self.decision_log)

    def end_session(self, messages: list) -> dict:
        """Generate end-of-session summary for progress tracker."""
        return self.progress.generate_summary(messages)

    def refresh_probe(self) -> None:
        """Re-run environment probe (e.g., after /probe command)."""
        self._snapshot = self.probe.run()
