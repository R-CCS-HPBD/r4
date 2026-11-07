"""Durable build contracts (G16, 2026-10-29).

Cross-session handoff for multi-build work, after Anthropic's long-running
harness pattern (2025-11 + 2026-03 posts): an agent-edited **JSON** feature
list where the only allowed mutation is flipping ``passes``, a progress log,
and an optional smoke command, all under ``.r4/build/<slug>/`` in the
workspace. JSON, not Markdown — models overwrite Markdown checklists far
more readily; the tools physically cannot edit or delete a feature, which
prompt rules alone cannot guarantee.

Contract layout::

    .r4/build/<slug>/features.json   {"goal", "created_at", "features": [...]}
    .r4/build/<slug>/progress.md     append-only PASS log
    .r4/build/<slug>/init.sh         optional smoke command (user/model-supplied)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUILD_DIR = ".r4/build"
_FEATURES_FILE = "features.json"
_PROGRESS_FILE = "progress.md"
_SMOKE_FILE = "init.sh"
_MAX_FEATURES = 500
_MAX_EVIDENCE_CHARS = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(goal: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")
    return (slug or "build")[:48]


def _contract_dirs(root: str) -> list[Path]:
    base = Path(root) / BUILD_DIR
    if not base.is_dir():
        return []
    return sorted((d for d in base.iterdir() if (d / _FEATURES_FILE).is_file()))


def _load(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((directory / _FEATURES_FILE).read_text())
    return data


def _active(root: str) -> tuple[Path, dict[str, Any]] | tuple[None, None]:
    """The newest incomplete contract, else the newest one at all."""
    dirs = _contract_dirs(root)
    for directory in reversed(dirs):
        data = _load(directory)
        if not all(f.get("passes") for f in data.get("features", [])):
            return directory, data
    return (dirs[-1], _load(dirs[-1])) if dirs else (None, None)


def _normalize_feature(entry: Any, index: int) -> dict[str, Any]:
    description = str(entry.get("description") or "").strip() if isinstance(entry, dict) else ""
    if not description:
        raise ValueError(f"features[{index}]: description is required")
    steps = entry.get("steps") if isinstance(entry, dict) else None
    return {
        "id": f"F-{index + 1:02d}",
        "description": description[:1_000],
        "steps": [str(step)[:300] for step in (steps or [])][:20],
        "passes": False,
    }


def _normalize_features(features: Any) -> list[dict[str, Any]]:
    if not isinstance(features, list) or not features or len(features) > _MAX_FEATURES:
        raise ValueError("features must be a list of 1..500 entries")
    return [_normalize_feature(entry, index) for index, entry in enumerate(features)]


def _active_conflict(root: str) -> str:
    """Why a new contract may not start, or "" — clobbering an active build
    destroys the cross-session handoff trail."""
    directory, active = _active(root)
    if active is None:
        return ""
    remaining = [f["id"] for f in active.get("features", []) if not f.get("passes")]
    return (
        f"an active build contract already exists at {directory}"
        f" ({len(remaining)} features failing); finish it or pass force=true"
    )


def _next_contract_dir(root: str, slug_root: str) -> Path:
    target = Path(root) / BUILD_DIR / slug_root
    suffix = 1
    while (target / _FEATURES_FILE).exists():
        suffix += 1
        target = Path(root) / BUILD_DIR / f"{slug_root}-{suffix}"
    return target


def start_build_contract(
    root: str,
    goal: str,
    features: Any,
    *,
    smoke_command: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create ``.r4/build/<slug>/`` with every feature failing.

    Refuses to clobber an active (incomplete) contract unless ``force`` —
    starting over mid-build destroys the handoff trail.
    """
    goal = (goal or "").strip()
    if not goal:
        return {"error": "goal is required"}
    if not force:
        conflict = _active_conflict(root)
        if conflict:
            return {"error": conflict}
    try:
        normalized = _normalize_features(features)
    except ValueError as exc:
        return {"error": str(exc)}
    target = _next_contract_dir(root, _slugify(goal))
    target.mkdir(parents=True)
    data = {"goal": goal, "created_at": _now(), "features": normalized}
    (target / _FEATURES_FILE).write_text(json.dumps(data, indent=2) + "\n")
    created_line = f"- [{_now()}] contract created ({len(normalized)} features)\n"
    (target / _PROGRESS_FILE).write_text(f"# Build progress — {goal}\n\n{created_line}")
    smoke = ""
    if smoke_command:
        (target / _SMOKE_FILE).write_text(f"#!/bin/sh\n{smoke_command}\n")
        (target / _SMOKE_FILE).chmod(0o755)
        smoke = str(target / _SMOKE_FILE)
    return {
        "contract_dir": str(target),
        "features": len(normalized),
        "failing": [f["id"] for f in normalized],
        "smoke": smoke,
    }


def _find_contract_feature(
    data: dict[str, Any], feature_id: str
) -> tuple[dict[str, Any] | None, str]:
    features = data.get("features", [])
    feature = next((f for f in features if f.get("id") == feature_id), None)
    if feature is None:
        known = ", ".join(str(f.get("id")) for f in features)
        return None, f"unknown feature {feature_id!r}; known ids: {known}"
    if feature.get("passes"):
        return None, f"{feature_id} already passes; passes is one-way (no un-marking)"
    return feature, ""


def mark_feature_passed(root: str, feature_id: str, evidence: str) -> dict[str, Any]:
    """Flip exactly one feature's ``passes`` flag — the only mutation allowed."""
    directory, data = _active(root)
    if directory is None or data is None:
        return {"error": "no build contract found; start one with build_contract_start"}
    feature, problem = _find_contract_feature(data, feature_id)
    if problem or feature is None:
        return {"error": problem}
    evidence = (evidence or "").strip()[:_MAX_EVIDENCE_CHARS]
    if not evidence:
        return {"error": "evidence is required — name the check that proves it (test, run, diff)"}
    feature["passes"] = True
    (directory / _FEATURES_FILE).write_text(json.dumps(data, indent=2) + "\n")
    with (directory / _PROGRESS_FILE).open("a") as handle:
        handle.write(f"- [{_now()}] {feature_id} PASS — {evidence}\n")
    remaining = [f["id"] for f in data["features"] if not f["passes"]]
    return {
        "passed": feature_id,
        "remaining": remaining,
        "complete": not remaining,
    }


def contract_status(root: str) -> dict[str, Any]:
    directory, data = _active(root)
    if directory is None or data is None:
        return {"active": False}
    failing = [f["id"] for f in data.get("features", []) if not f.get("passes")]
    return {
        "active": bool(failing),
        "contract_dir": str(directory),
        "goal": data.get("goal", ""),
        "passing": len(data.get("features", [])) - len(failing),
        "total": len(data.get("features", [])),
        "next": failing[0] if failing else None,
        "failing": failing,
        "smoke": str(directory / _SMOKE_FILE) if (directory / _SMOKE_FILE).exists() else "",
    }


def contract_payload(root: str) -> dict[str, Any] | None:
    """The active contract's directory, full feature data, and status (G15)."""
    directory, data = _active(root)
    if data is None:
        return None
    return {"dir": str(directory), "data": data, "status": contract_status(root)}


def contract_orientation(root: str) -> str:
    """The per-turn startup-ritual reminder while a contract is active."""
    status = contract_status(root)
    if not status.get("active"):
        return ""
    smoke_line = (
        f"Run the recorded smoke check first (`{status['smoke']}`) and fix "
        "any breakage before new work."
        if status["smoke"]
        else "No smoke command recorded — run the project's basic check before new work."
    )
    return (
        f"[Build contract: {status['goal']}]  {status['passing']}/{status['total']} features "
        f"passing. Next up: {status['next']}. {smoke_line} Work one feature at a time and "
        "mark it only with build_contract_pass(feature_id, evidence) — never by editing "
        "features.json."
    )
