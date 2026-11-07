"""SteeringLoop — self-improvement from researcher corrections.

When the companion makes a mistake and the researcher corrects it,
the steering loop records the lesson. In future sessions, these
lessons are injected as feedforward guides — the companion never
repeats the same mistake.

Also includes a built-in anti-patterns database for HPC researchers.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from r4._atomic import atomic_write_text

logger = logging.getLogger(__name__)

LESSONS_DIR = Path(
    os.environ.get("R4_COMPANION_LESSONS_DIR")
    or os.environ.get("R4_COMPANION_LESSONS_DIR")
    or os.path.expanduser("~/.config/r4/companion")
)
LESSONS_FILE = LESSONS_DIR / "lessons.json"


@dataclass
class Lesson:
    """A recorded mistake→correction pair, distilled into a rule."""

    context: str  # What was the task
    mistake: str  # What went wrong
    correction: str  # What the researcher said
    rule: str  # Extracted directive for future reference
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# Built-in anti-patterns for HPC researchers
ANTI_PATTERNS: list[dict[str, str]] = [
    {
        "pattern": r"(wget|curl).*huggingface|hf\.co.*download",
        "context": "model_download",
        "rule": (
            "Do not download models directly. Use `r4 model stage <model>` "
            "to download via UMRS durable job."
        ),
    },
    {
        "pattern": r"(export\s+)?[A-Z_]*PATH\s*=\s*/home/\w+",
        "context": "hardcoded_path",
        "rule": (
            "Avoid hardcoding absolute paths from login nodes. "
            "Use environment variables ($HOME, $WORK, etc.) for portability."
        ),
    },
    {
        "pattern": r"pip\s+install\b(?!.*--user)(?!.*venv)(?!.*conda)",
        "context": "system_pip",
        "rule": (
            "Do not install packages into the system Python. Use a virtual environment or conda."
        ),
    },
    {
        "pattern": r"#SBATCH\s+--gres=gpu:([4-9]|\d{2,})",
        "context": "gpu_overalloc",
        "rule": (
            "Start with fewer GPUs and scale up only if needed. "
            "Most 7B models work fine on 1-2 GPUs."
        ),
    },
    {
        "pattern": r"requests\.get|urllib\.request|http\.client",
        "context": "network_in_inference",
        "rule": (
            "Inference containers must be offline (TRANSFORMERS_OFFLINE=1). "
            "All network access goes through EgressGuard."
        ),
    },
]


# Heuristic patterns that indicate the user is correcting the companion
CORRECTION_SIGNALS = [
    r"^no[,.]",
    r"^that'?s (wrong|incorrect|not right)",
    r"^actually[,.]",
    r"^don'?t do that",
    r"^wrong",
    r"^instead[,.]",
    r"^you should",
    r"^i said",
    r"not what i (asked|meant|wanted)",
]


class SteeringLoop:
    """Record and recall lessons from researcher corrections."""

    def __init__(self, lessons_dir: Path | None = None):
        self._dir = lessons_dir or LESSONS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lessons = self._load()
        self._last_response = ""

    def _load(self) -> list[Lesson]:
        lf = LESSONS_FILE if self._dir == LESSONS_DIR else self._dir / "lessons.json"
        if not lf.is_file():
            return []
        try:
            data = json.loads(lf.read_text())
            return [Lesson(**d) for d in data]
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning("Failed to load lessons: %s", e)
            return []

    def _save(self) -> None:
        lf = LESSONS_FILE if self._dir == LESSONS_DIR else self._dir / "lessons.json"
        data = [
            {
                "context": lesson.context,
                "mistake": lesson.mistake,
                "correction": lesson.correction,
                "rule": lesson.rule,
                "created_at": lesson.created_at,
            }
            for lesson in self._lessons
        ]
        # Atomic: parallel bridges (chat tabs) never tear the lessons file.
        atomic_write_text(lf, json.dumps(data, indent=2))

    def observe_turn(
        self,
        user_message: str,
        response: str,
        *,
        confirm: Callable[[str, str], bool] | None = None,
    ) -> None:
        """Observe a turn. If the user is correcting, record a lesson.

        ``confirm(user_message, last_response)`` may veto a rule-matched
        correction (the decision model's lesson check, ADR-076); it can only
        drop a lesson, never create one.
        """
        if (
            self._last_response
            and _is_correction(user_message)
            and (confirm is None or confirm(user_message, self._last_response))
        ):
            lesson = Lesson(
                context=self._last_response[:200],
                mistake=self._last_response[:200],
                correction=user_message[:300],
                rule=_extract_rule(user_message),
            )
            self._lessons.append(lesson)
            # Keep max 50 lessons
            self._lessons = self._lessons[-50:]
            self._save()
            logger.info("Recorded lesson: %s", lesson.rule)

        self._last_response = response

    def check_anti_patterns(self, text: str) -> str | None:
        """Check text against known anti-patterns. Returns warning or None."""
        for ap in ANTI_PATTERNS:
            if re.search(ap["pattern"], text, re.IGNORECASE):
                return f"⚠ Anti-pattern ({ap['context']}): {ap['rule']}"
        return None

    def as_prompt(self) -> str | None:
        """Format lessons as a system prompt fragment."""
        if not self._lessons:
            return None

        parts = ["[Lessons from past corrections — do NOT repeat these mistakes]"]
        for lesson in self._lessons[-10:]:  # Recent 10
            parts.append(f"- {lesson.rule}")
        return "\n".join(parts)

    def add_lesson(self, context: str, mistake: str, correction: str, rule: str) -> None:
        """Manually add a lesson."""
        self._lessons.append(
            Lesson(context=context, mistake=mistake, correction=correction, rule=rule)
        )
        self._lessons = self._lessons[-50:]
        self._save()

    def list_lessons(self) -> list[Lesson]:
        return list(self._lessons)

    def clear(self) -> None:
        self._lessons.clear()
        self._save()


#: Replies that open like a correction but agree ("No, that's great",
#: "no problem") — saving them as lessons taught the agent the opposite
#: of what the user meant (2026-10-02 audit).
NOT_A_CORRECTION = re.compile(
    r"^no[,.!]?\s*((that|it|this)('?s| is)\s+)?(great|fine|good|perfect|ok|okay|correct|right|"
    r"problem|worries|need to apologi[sz]e)\b"
)


def _is_correction(text: str) -> bool:
    """Heuristic: does this message look like the user is correcting the companion?"""
    text_lower = text.lower().strip()
    if NOT_A_CORRECTION.match(text_lower):
        return False
    return any(re.match(pat, text_lower) for pat in CORRECTION_SIGNALS)


def _extract_rule(correction: str) -> str:
    """Extract a concise rule from a correction message.

    Simple extraction: use the correction text itself, trimmed.
    A more sophisticated version could use LLM summarization.
    """
    # Remove common correction prefixes
    text = correction.strip()
    for prefix in ["no, ", "no. ", "actually, ", "actually. ", "wrong. ", "instead, "]:
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    # Capitalize and truncate
    if text:
        text = text[0].upper() + text[1:]
    return text[:200]
