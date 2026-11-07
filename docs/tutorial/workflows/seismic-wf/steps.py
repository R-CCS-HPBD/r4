"""UMRS steps: two model-search steps fan out, one rank step gathers.

``search_low`` and ``search_high`` each score a block of candidate 1-D models
(their config holds the value lists) and write ``<step>.json``. ``rank``
receives both keys as ``ctx.input_keys`` (the parents' ``output_keys``), ranks
all models, and writes ``report.md`` and ``best_model.json``.
"""

from __future__ import annotations

import json
import time

import seismic_core as core
from umrs.pipeline_sdk import Step, StepContext, StepResult


def _inputs(ctx: StepContext, cfg: dict):
    event = core.parse_event(ctx.download_bytes(cfg["event_key"]).decode("utf-8"))
    picks = core.parse_picks(ctx.download_bytes(cfg["picks_key"]).decode("utf-8"))
    return event, picks


class ModelSearchStep(Step):
    """Fan-out: score one block of candidate models."""

    name = "search"

    def setup(self, config: dict) -> None:
        self.config = config or {}

    def run(self, ctx: StepContext) -> StepResult:
        time.sleep(float(self.config.get("debug_delay_seconds", 0)))
        event, picks = _inputs(ctx, self.config)
        h = float(self.config.get("grid_km", 4.0))
        scores = [core.score(event, picks, m, h) for m in core.candidates(self.config)]
        if self.config.get("include_ak135"):
            scores.append({**core.score(event, picks, core.AK135_CRUST, h), "baseline": True})
        key = f"{self.config['step_name']}.json"
        ctx.upload_bytes(key, json.dumps(scores).encode(), content_type="application/json")
        return StepResult(output_keys=[key], extra={"models": len(scores)})


class RankStep(Step):
    """Gather: rank every scored model and write the report."""

    name = "rank"

    def setup(self, config: dict) -> None:
        self.config = config or {}

    def run(self, ctx: StepContext) -> StepResult:
        scores = []
        for key in ctx.input_keys:
            scores.extend(json.loads(ctx.download_parent_bytes(key)))
        baseline = next(s for s in scores if s.get("baseline"))
        ranked = sorted((s for s in scores if not s.get("baseline")), key=lambda s: s["rms_s"])
        event, picks = _inputs(ctx, self.config)
        h = float(self.config.get("grid_km", 4.0))
        report = core.report_markdown(event, ranked, baseline, len(picks), h)
        ctx.upload_bytes("report.md", report.encode(), content_type="text/markdown")
        ctx.upload_bytes(
            "best_model.json",
            json.dumps(ranked[0], indent=2).encode(),
            content_type="application/json",
        )
        return StepResult(
            output_keys=["report.md", "best_model.json"],
            extra={"best_rms_s": ranked[0]["rms_s"], "ak135_rms_s": baseline["rms_s"]},
        )
