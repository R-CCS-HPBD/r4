"""Wire the ``tool_search`` meta-tool to the session's full catalog (G18).

The ``tool_search`` meta-tool (``r4/agent/tools.py``) always sat in the
always-on set but its callback was never injected, so the availability gate
dropped it as dead-by-omission. This module is the missing callback:

- :func:`search_catalog` ranks catalog tools against a natural-language
  query (whole-word terms, mirroring the skills keyword fix — no substring
  bleed such as "ara" matching "compare").
- :func:`register_session_tool_search` closes over the session's full tool
  catalog and records every returned name in the session's opt-in set, so a
  skill-gated turn sees the discovered tools from the next turn (wave-80
  stable array: visibility changes through the refusal set, never a reshaped
  array).
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Callable

from .tool_availability import platform_live

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .session import CompanionSession

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 5
_TERM_RE = re.compile(r"[a-z0-9_]+")
# Stopwords drown the signal: "the status of a job" otherwise gives every
# long description three free hits (observed 2026-10-28: python_exec outranked
# hpc_job_status on a job-status query).
_STOPWORDS = frozenset(
    "a an and are as at be by for from i in is it me my of on or that the this to with".split()
)


def _search_limit() -> int:
    raw = os.environ.get("R4_TOOL_SEARCH_LIMIT", "").strip()
    try:
        return max(1, int(raw)) if raw else _DEFAULT_LIMIT
    except ValueError:
        logger.warning("R4_TOOL_SEARCH_LIMIT=%r is not an int", raw)
        return _DEFAULT_LIMIT


def _term_hits(terms: list[str], words: set[str]) -> int:
    return sum(1 for term in terms if term in words)


def _score(tool: dict[str, Any], terms: list[str]) -> int:
    """Whole-word term hits: name hits weigh double (names are canonical)."""
    name_words = set(_TERM_RE.findall(str(tool.get("name") or "").lower()))
    description_words = set(_TERM_RE.findall(str(tool.get("description") or "").lower()))
    name_hits = _term_hits(terms, name_words)
    hits = name_hits + _term_hits(terms, description_words)
    return hits * 2 if name_hits else hits


def search_catalog(
    tools: list[dict[str, Any]], query: str, *, limit: int = _DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """The ``limit`` best-matching tool descriptors for ``query`` (name,
    description and JSON schema — the model needs argument shapes to call
    what it discovered)."""
    terms = [term for term in _TERM_RE.findall(query.lower()) if term not in _STOPWORDS]
    if not terms:
        return []
    scored = [(_score(tool, terms), tool.get("name", ""), tool) for tool in tools]
    ranked = sorted(
        (entry for entry in scored if entry[0] > 0),
        key=lambda entry: (-entry[0], entry[1]),
    )
    return [
        {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        }
        for _, _, tool in ranked[:limit]
    ]


# ADR-105 D-3: registered workflows are searchable too, so "run my
# relaxation" finds workflow_run AND the workflow. Only with a live platform
# (cached TCP probe; no catalog call can then hang on a dead gateway), only
# for a query with terms, and cached per session; any failure means no
# workflow hits, never a broken search. Name and description are indexed
# (not tags: scope tags such as "user" would match every workflow).
_WORKFLOW_HIT_LIMIT = 3
_WORKFLOW_CACHE_TTL_SEC = 60.0
_WORKFLOW_TOOLS = ("workflow_run", "workflow_describe")


def _query_terms(query: str) -> list[str]:
    return [term for term in _TERM_RE.findall(query.lower()) if term not in _STOPWORDS]


def _workflow_row(wf: Any) -> dict[str, str]:
    name = str(getattr(wf, "workflow_id", "") or getattr(wf, "name", "") or "")
    description = str(getattr(wf, "description", "") or "")
    return {"name": name, "description": description, "text": f"{name} {description}"}


def _fetch_workflow_rows(client: Any) -> list[dict[str, str]]:
    if not platform_live(client):
        return []
    try:
        return [_workflow_row(wf) for wf in client.workflows.list_workflows() or []]
    except Exception as exc:  # transport error, auth, server down
        logger.debug("tool_search: workflow catalog unavailable: %s", exc)
        return []


def _workflow_rows(session: Any) -> list[dict[str, str]]:
    cached = getattr(session, "_tool_search_workflow_cache", None)
    now = time.monotonic()
    if cached and now - cached[0] < _WORKFLOW_CACHE_TTL_SEC:
        rows: list[dict[str, str]] = cached[1]
        return rows
    rows = _fetch_workflow_rows(getattr(session, "client", None))
    session._tool_search_workflow_cache = (now, rows)
    return rows


def _workflow_hit(row: dict[str, str], run_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "workflow_run",
        "workflow_name": row["name"],
        "description": (
            f"Registered workflow '{row['name']}': {row['description']} "
            "Call workflow_describe for its parameter_schema, then workflow_run."
        ),
        "parameters": run_schema,
    }


def search_workflows(
    rows: list[dict[str, str]], query: str, run_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Registered workflows matching ``query``, as ``workflow_run`` hits."""
    terms = _query_terms(query)
    scored = []
    for row in rows:
        hits = _term_hits(terms, set(_TERM_RE.findall(row["text"].lower())))
        if hits and row["name"]:
            scored.append((-hits, row["name"], row))
    return [_workflow_hit(row, run_schema) for _, _, row in sorted(scored)[:_WORKFLOW_HIT_LIMIT]]


def _session_workflow_hits(session: Any, query: str) -> list[dict[str, Any]]:
    run_tool = next((t for t in session.tools if t.get("name") == "workflow_run"), None)
    if run_tool is None or not _query_terms(query):
        return []
    return search_workflows(_workflow_rows(session), query, run_tool.get("parameters", {}))


def register_session_tool_search(
    session: "CompanionSession",
) -> Callable[[str], list[dict[str, Any]]]:
    """Register the session's catalog as the ``tool_search`` backend.

    Returns the registered callback (tests call it directly). Each hit is
    also recorded in ``session.tool_search_opt_ins`` so the discovered tools
    leave the skill refusal set from the next turn.
    """

    def _search(query: str) -> list[dict[str, Any]]:
        hits = search_catalog(session.tools, query, limit=_search_limit())
        workflow_hits = _session_workflow_hits(session, query)
        hits = hits + workflow_hits
        names = {hit["name"] for hit in hits}
        if workflow_hits:
            names.update(_WORKFLOW_TOOLS)
        newly = names - session.tool_search_opt_ins
        session.tool_search_opt_ins.update(names)
        return [{**hit, "activated_for_next_turn": hit["name"] in newly} for hit in hits]

    from r4.agent.tools import set_tool_search_callback

    set_tool_search_callback(_search)
    return _search
