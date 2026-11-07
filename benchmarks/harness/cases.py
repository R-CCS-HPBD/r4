"""Harness benchmark case model and suite loading.

Suite JSON is harness-agnostic: one prompt, an optional sandboxed setup, and
deterministic graders that evaluate the workspace state left behind. Grader
names are validated by the runner and fail closed on unknown names — never
grade with an unspecified metric.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_GRADERS = ("status_success", "artifact_present")


@dataclass(frozen=True)
class HarnessCase:
    """One harness-agnostic benchmark task."""

    case_id: str
    prompt: str
    setup_files: dict[str, str] = field(default_factory=dict)
    grader_names: tuple[str, ...] = _DEFAULT_GRADERS
    expected_outputs: tuple[str, ...] = ()
    test_command: str | None = None
    expect: dict[str, Any] = field(default_factory=dict)
    repeat: int = 1
    max_sec: int = 900


def load_suite(path: Path) -> list[HarnessCase]:
    """Load a suite JSON file into HarnessCase objects.

    Required per case: ``id`` and ``prompt``. Everything else has defaults so
    suites stay terse. Unknown keys are rejected — fail closed on typos.
    """
    raw = json.loads(Path(path).read_text())
    cases: list[HarnessCase] = []
    known = {
        "id",
        "prompt",
        "setup",
        "graders",
        "expected_outputs",
        "test_command",
        "expect",
        "repeat",
        "max_sec",
    }
    for entry in raw["cases"]:
        unknown = set(entry) - known
        if unknown:
            raise KeyError(f"case {entry.get('id')!r}: unknown keys {sorted(unknown)}")
        cases.append(
            HarnessCase(
                case_id=entry["id"],
                prompt=entry["prompt"],
                setup_files=dict(entry.get("setup", {}).get("files", {})),
                grader_names=tuple(entry.get("graders", _DEFAULT_GRADERS)),
                expected_outputs=tuple(entry.get("expected_outputs", ())),
                test_command=entry.get("test_command"),
                expect=dict(entry.get("expect", {})),
                repeat=int(entry.get("repeat", 1)),
                max_sec=int(entry.get("max_sec", 900)),
            )
        )
    return cases
