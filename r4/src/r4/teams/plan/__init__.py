"""Team plans: the CORE ``plan`` engine (ADR-092 D-2).

A lead-authored, declarative orchestration (phases of agent fan-outs,
per-item pipelines and closed reducers) runs behind the ADR-020 seam as one
more compile target. Every agent step is one governed turn of
``RuntimeServices.agents``; the journal makes a re-run replay whatever did
not change.

Import fence (ADR-092 D-2): modules here import only the standard library,
``r4_contracts`` and ``r4_teamengine_spi``
(``tests/test_plan_engine_import_fence.py``), so the engine can move to
``engines/`` as a file move.
"""

from __future__ import annotations

from .engine import PlanEngine, PlanProgram, model_warnings, spec_from_plan
from .journal import JOURNAL_LABEL, PlanJournal
from .run import ENGINE_NAME, ENGINE_VERSION, PlanRun
from .templates import list_templates, load_template, resolve_plan_source

__all__ = [
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "JOURNAL_LABEL",
    "PlanEngine",
    "PlanJournal",
    "PlanProgram",
    "PlanRun",
    "list_templates",
    "load_template",
    "model_warnings",
    "resolve_plan_source",
    "spec_from_plan",
]
