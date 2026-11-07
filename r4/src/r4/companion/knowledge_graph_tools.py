"""Companion tools for multi-graph discovery, selection, and extension.

Multi-graph agent loop (identified as missing by the 2026-08-14 audit):

  1. ``knowledge_graph_list`` — discover available graphs (metadata)
  2. ``knowledge_query`` — select graphs via the precedence policy
     (explicit exclude > only > include > discovery > default) and query
     only those; results carry per-hit graph provenance
  3. ``knowledge_graph_propose`` — when knowledge is insufficient, stage a
     graph-extension proposal (NO mutation happens at this step)
  4. ``knowledge_graph_decide`` — record the user's explicit decision;
     only an approved proposal authorizes ``knowledge_ingest`` /
     understand mutations for agent actors (server-enforced, 0069)

The actor header is set here: every Companion-driven mutation is marked
``x-r4-actor: agent`` so the server-side guard applies regardless of the
client-side permission mode (TRUST/autopilot cannot bypass it).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, NamedTuple

from r4_contracts.context_pack import estimate_tokens, sanitize_text
from r4_contracts.models import RETRIEVAL_PROFILES, RetrievalProfile

from ..knowledge_selection import resolve_graph_selection
from .knowledge_tools import (
    _normalize_string_list,
    _project_id_for,
    _structured_error,
    _tool,
)
from .knowledge_write_gate import check_propose

if TYPE_CHECKING:
    from r4.client import Client

# Companion = agent actor for every knowledge mutation (0069 guard).
AGENT_ACTOR = "agent"

#: K3 hits ``knowledge_query`` asks for when no reader sizes it: the fixed
#: limit before ADR-093 D-14 (the large profile's ``k_max`` too until MD-14,
#: 2026-10-13; it is not read from that profile and stays 10).
UNSIZED_LIMIT = 10
#: Characters of one hit's content ``knowledge_query`` shows when no reader
#: sizes it: the fixed snippet before C23b, kept as that character count and
#: cut exactly there (no word boundary, no ``content_clipped``).
UNSIZED_CHARS = 200
#: The most characters one token holds in the pack's own estimator (ADR-085
#: D-2, ``r4_contracts.context_pack.estimate_tokens``: 4 ASCII characters; a
#: non-ASCII one is a token by itself): a budget of n tokens fits at most
#: n times this many characters.
CHARS_PER_TOKEN = 1_000 // estimate_tokens("x" * 1_000)
#: What ``UNSIZED_CHARS`` come to in ASCII text by that estimator (50): the
#: floor of a sized hit's token share, and the figure an unsized query
#: records for its 200 characters (the eval harness's ``tool_sizing``).
UNSIZED_TOKENS = estimate_tokens("x" * UNSIZED_CHARS)


class QuerySizing(NamedTuple):
    """What the reader's profile sizes in ``knowledge_query`` (ADR-093 D-14).

    A sized query counts each hit's content in the pack's estimated tokens,
    as the router's pack is counted, so a Japanese passage (a token a
    character) gets the share an English one does. An unsized one keeps the
    pre-C23b answer byte for byte: the first ``chars`` characters whatever
    the script, and ``\\uXXXX`` escapes; its ``tokens`` is what those
    characters come to in ASCII text. Byte for byte wherever K3's strings
    are data already: since the C23b review every string is read through
    the pack's ``sanitize_text`` (:func:`_as_data`), so a hit holding an
    invisible character, a ``[n]``/``[Sn]`` label look-alike or a
    ``<passages>``/``<instruction>`` tag is shown neutralised, not as sent."""

    limit: int  # K3 hits asked for, and at most this many shown
    tokens: int  # the most estimated tokens of one hit's content shown
    chars: int | None = None  # unsized: the most characters shown instead

    @property
    def sized(self) -> bool:
        """Sized by a reader's profile: content counted in tokens, a cut
        ended at a word and flagged, non-ASCII text shown as itself."""
        return self.chars is None


UNSIZED = QuerySizing(UNSIZED_LIMIT, UNSIZED_TOKENS, UNSIZED_CHARS)


@contextmanager
def agent_mutation(client: "Client") -> Iterator[None]:
    """Mark ONE mutation on the shared client as agent-originated.

    Used at MUTATION time (knowledge_ingest in knowledge_tools), never at
    build time: the same client also serves human-initiated surfaces (Studio
    buttons, the CLI review), and leaked agent identity would gate them. The
    prior actor comes back when the call returns or raises: the flag was
    sticky until K1.5 (knowledge plan 2026-10-24), so after one agent ingest
    every later human mutation in the session went out marked ``agent``.
    The mark lives in the current thread/task only
    (:meth:`r4.knowledge.KnowledgeClient.agent_scope`): a save/restore on the
    shared client let a team run's thread send its next upload unmarked when
    the lead agent's call finished first (review of K1 part A).
    """
    knowledge = client.knowledge
    scope = getattr(knowledge, "agent_scope", None)
    if scope is not None:
        with scope():
            yield
        return
    prior = knowledge.actor  # a stand-in knowledge layer (tests, plugins)
    knowledge.actor = AGENT_ACTOR
    try:
        yield
    finally:
        knowledge.actor = prior


def _graph_brief(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "graph_id": graph.get("graph_id"),
        "name": graph.get("name"),
        "kind": graph.get("kind"),
        "project_id": graph.get("project_id"),
        "status": graph.get("status"),
        "description": (graph.get("description") or "")[:160],
    }


def _available_graphs(client: "Client", project_id: str | None) -> list[dict[str, Any]]:
    """All active graphs; materializes the project default graph first."""
    if project_id:
        client.knowledge.graphs(project_id=project_id)
    return client.knowledge.graphs(status="active")


def _default_graph_for(client: "Client", project_id: str | None) -> dict[str, Any] | None:
    if not project_id:
        return None
    for graph in client.knowledge.graphs(project_id=project_id):
        if graph.get("project_id") == project_id:
            return graph
    return None


def _build_graph_list(client: "Client") -> dict[str, Any]:
    def _list(query: str | None = None, project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        try:
            graphs = _available_graphs(client, target)
            if query:
                lowered = query.lower()
                graphs = [
                    g
                    for g in graphs
                    if lowered in (g.get("name") or "").lower()
                    or lowered in (g.get("description") or "").lower()
                ]
            return json.dumps(
                {
                    "type": "knowledge_graphs",
                    "count": len(graphs),
                    "graphs": [_graph_brief(g) for g in graphs],
                    "hint": (
                        "Pick the graphs relevant to the task and call "
                        "knowledge_query with graphs=[names]. Do NOT query "
                        "every graph — select by relevance."
                    ),
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_graph_list_failed", str(exc))

    return _tool(
        "knowledge_graph_list",
        "Discover available knowledge graphs (ids, names, kind, project, "
        "description). Call FIRST when a question may need knowledge beyond "
        "the current project — then select only the relevant ones.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name/description filter"},
                "project_id": {"type": "string", "description": "Project (default: current)"},
            },
        },
        _list,
    )


def _selection_payload(selection) -> dict[str, Any]:
    return {
        "selected_graphs": [
            {"graph_id": p.graph.get("graph_id"), "name": p.graph.get("name"), "source": p.source}
            for p in selection.picks
        ],
        "excluded": selection.excluded,
        "rejected": selection.rejected,
    }


def _profile_sizing(profile: RetrievalProfile) -> QuerySizing:
    """``k_max`` hits, each with its share of the profile's pack: the pack's
    ``pack_tokens`` over ``k_max``, never under ``UNSIZED_TOKENS``."""
    share = profile.pack_tokens // profile.k_max
    return QuerySizing(profile.k_max, max(UNSIZED_TOKENS, share))


def query_sizing(client: "Client", reader: Any) -> QuerySizing:
    """How many K3 hits ``knowledge_query`` asks for and how much of each it
    shows: the session reader's profile ``k_max`` (ADR-093 D-14; small 4,
    medium 6, large 6 since MD-14, 2026-10-13, 10 before) and that profile's
    pack shared over those hits, in the pack estimator's tokens (small 300,
    medium and large 333, large 800 before MD-14: about 1,200 and 1,332
    characters of English, 300 and 333 of Japanese), so ``k_max`` full hits
    fill the pack as the router's would.

    Resolved once per call exactly as ``search_docs`` resolves its
    ``profile`` (``tools._retrieve_reader``: the model name, or the session's
    model accessor read now, through ``retrieval_router.reader_fields``), so
    a ``/model`` switch resizes the next query, count and content alike. No
    reader, an unresolved profile, or an accessor or detection that fails
    keeps 10 hits of 200 characters (``UNSIZED``), the pre-C23b answer byte
    for byte (2026-10-11, C23b review). Measured 2026-10-10
    (SciFact dev, llama3.2:3b): a fixed 10 pushed 82 of 300 verdicts past
    the small reader's output budget. Measured 2026-10-11 (phase 7, C23b):
    sized to 4 hits but still cut to 200 characters (about 800 characters of
    evidence), the same reader stayed 8.0 points under B0 and gave more
    verdicts on NEI claims (+15.2), where the router's pack gives it whole
    passages within the same profile's budget. The share is counted in
    tokens (2026-10-11, C23b review): a share of 1,200 characters let four
    Japanese hits come to 4,688 estimated tokens against the small pack's
    1,200.

    The reader is whatever the tool builder was given (2026-10-11,
    c23-broker-reader, closed): a team plan agent's catalog is built per
    turn for the model that turn runs on, the agent's own, else the lead
    session's, else the client's configured model
    (``teams.agent_broker.RoleTurnAgentBroker._turn_model``), so a plan
    agent pinned to another size class reads at its own ``k_max``. Teamup
    role agents likewise (2026-10-11, closed): a role's catalog is built
    for its own model, else the team's, else the client's
    (``teamup.role_agent._role_reader``).
    """
    if reader is None:
        return UNSIZED
    from .tools import _retrieve_reader  # lazy: tools registers this family

    try:
        profile = _retrieve_reader(reader, client).get("profile")
    except Exception:  # noqa: BLE001 - sizing never costs the query
        return UNSIZED
    sized = RETRIEVAL_PROFILES.get(profile) if isinstance(profile, str) else None
    return _profile_sizing(sized) if sized else UNSIZED


def query_limit(client: "Client", reader: Any) -> int:
    """K3 hits ``knowledge_query`` asks for: :func:`query_sizing`'s limit."""
    return query_sizing(client, reader).limit


def _query_selected_graphs(
    client: "Client", target: str, query: str, selection, sizing: QuerySizing = UNSIZED
) -> dict[str, Any]:
    """K3 over the selected graphs: ``sizing.limit`` hits asked for and at
    most that many shown (a server may send more), each hit's content within
    its budget (:func:`_hit_entry`)."""
    payload = client.knowledge.query(
        query,
        project_id=target,
        graphs=selection.graph_ids,
        limit=sizing.limit,
    )
    query_result = payload.get("query") or {}
    rows = (query_result.get("results") or [])[: sizing.limit]
    hits = [_hit_entry(hit, sizing) for hit in rows]
    result = {
        "type": "knowledge_query",
        "results": hits,
        "total_results": query_result.get("total_results", len(hits)),
        "selection": _selection_payload(selection),
        "wired_backends": query_result.get("wired_backends", []),
        "unavailable_backends": query_result.get("unavailable_backends", {}),
    }
    if isinstance(query_result.get("sufficiency"), dict):
        result["sufficiency"] = query_result["sufficiency"]
    return result


def _hit_entry(hit: dict[str, Any], sizing: QuerySizing = UNSIZED) -> dict[str, Any]:
    """One K3 hit for the agent: its handle, kind and the lanes that found it
    (KGC-04), besides title, score, graph provenance and its content within
    its sizing (:func:`_clip_content`). A sized hit cut to fit says
    ``content_clipped``: the passage goes on, and its ``source_id`` is the
    handle to it.

    Every string of it is K3's, so untrusted, and read as data
    (:func:`_as_data`), the content before it is cut: an invisible run
    spends no budget. A key with no value (None, or an empty string, list or
    mapping) is left out: it says nothing and costs a small reader context
    (ADR-093 D-14)."""
    meta = hit.get("metadata") or {}
    content, clipped = _clip_content(sanitize_text(str(hit.get("content") or "")), sizing)
    entry = _as_data(
        {
            "title": hit.get("title"),
            "score": hit.get("score"),
            "source_id": hit.get("source_id"),
            "node_type": meta.get("node_type"),
            "lanes": _hit_lanes(hit, meta),
            "graph_id": meta.get("graph_id"),
            "graph_name": meta.get("graph_name"),
        }
    )
    entry.update(content=content, content_clipped=clipped or None)
    return {key: value for key, value in entry.items() if _has_value(value)}


def _clip_content(text: str, sizing: QuerySizing) -> tuple[str, bool]:
    """A hit's content within its sizing, and whether it was cut to fit.
    Unsized, the first ``chars`` characters exactly, never flagged: the
    pre-C23b cut, so an unsized answer is the one it was. Sized, ``tokens``
    estimated tokens ended at a word (:func:`_clip`)."""
    if not sizing.sized:
        return text[: sizing.chars], False
    return _clip(text, sizing.tokens)


def _clip(text: str, budget: int) -> tuple[str, bool]:
    """``text`` within ``budget`` of the pack's estimated tokens, and whether
    it was cut. A cut ends at the last whitespace in the final fifth of the
    characters that fit when there is one (no word is split; trailing
    whitespace dropped), else where the budget runs out (a passage without
    spaces, such as Japanese text)."""
    if estimate_tokens(text) <= budget:
        return text, False
    fits = _fitting_prefix(text, budget)
    floor = fits - fits // 5
    end = next((i for i in range(fits, floor - 1, -1) if text[i].isspace()), fits)
    return text[:end].rstrip(), True


def _fitting_prefix(text: str, budget: int) -> int:
    """The most leading characters of ``text`` whose estimated tokens are
    within ``budget``, ``text`` itself being over it. The estimate grows
    with the prefix and charges a character at least 1/``CHARS_PER_TOKEN``
    of a token, so no more than ``budget * CHARS_PER_TOKEN`` characters can
    fit: the search stops there, whatever the length of the passage."""
    low, high = 0, min(len(text) - 1, budget * CHARS_PER_TOKEN)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return low


def _as_data(value: Any) -> Any:
    """``value`` with every string in it, mapping keys included, run through
    the pack's ``sanitize_text`` as ``search_docs`` runs each passage
    (ADR-093 D-4): control and format characters stripped (newlines and tabs
    kept; Unicode tag runs, bidi overrides, zero-width characters), label
    look-alikes and wrapper tags neutralised. A value JSON has no form for
    is spelled as ``default=str`` spells it, then read alike; numbers,
    booleans and None are kept."""
    if isinstance(value, dict):
        return {_as_key(key): _as_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_data(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(str(value))


def _as_key(key: Any) -> Any:
    """A mapping key as data: a string sanitised; JSON spells the rest."""
    return sanitize_text(key) if isinstance(key, str) else key


def _dumps(payload: dict[str, Any], sizing: QuerySizing) -> str:
    """``knowledge_query``'s answer as JSON, every string in it read as data
    (:func:`_as_data`). K3's hit text, titles and graph names are untrusted:
    an invisible character in them (a run of Unicode tag characters
    spelling an instruction, a bidi override, a zero-width or control
    character) would reach the reader, and the tool's result preview, as
    itself (2026-10-11, C23b review). A sized reader reads non-ASCII text as
    itself, not as ``\\uXXXX`` escapes (six characters, several tokens,
    each); an unsized query keeps the escapes, its pre-C23b answer byte for
    byte."""
    return json.dumps(_as_data(payload), indent=2, default=str, ensure_ascii=not sizing.sized)


def _has_value(value: Any) -> bool:
    """Not None and not an empty string, list or mapping (0 is a value)."""
    if value is None:
        return False
    return not isinstance(value, (str, list, tuple, dict)) or bool(value)


def _hit_lanes(hit: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    """``metadata.lanes`` (list, or lane -> score) after K3 fusion; else the
    single ``metadata.lane`` or the hit's ``result_type``."""
    lanes = meta.get("lanes")
    if isinstance(lanes, (list, tuple, dict)):
        return [str(lane) for lane in lanes]
    single = meta.get("lane") or hit.get("result_type")
    return [str(single)] if single else []


def _knowledge_insufficient(result: dict[str, Any]) -> bool:
    """The server's sufficiency verdict when it judged (ADR-093 D-10); else
    (an older server, or ``unknown`` when no dense or lexical lane ran) no
    results at all."""
    status = (result.get("sufficiency") or {}).get("status")
    if status and status != "unknown":
        return status == "insufficient"
    return result.get("total_results", 0) == 0


def _insufficiency_note() -> dict[str, Any]:
    return {
        "knowledge_insufficient": True,
        "next": (
            "The selected graphs do not answer this. Do NOT ingest or modify "
            "knowledge silently — call knowledge_graph_propose with the graph "
            "to extend, the reason, and the source artifacts. The user must "
            "explicitly approve (knowledge_graph_decide) before any mutation."
        ),
        "requires_user_approval": True,
    }


def _merged_constraints(
    client: "Client",
    graphs: str | list[str] | None,
    exclude_graphs: str | list[str] | None,
    only_graphs: str | list[str] | None,
    discovery: bool | None,
) -> tuple[list[str], list[str], list[str], bool]:
    """Explicit args > session constraints > persisted store > defaults."""
    constraints = getattr(client.knowledge, "graph_constraints", {}) or {}
    if not constraints:
        try:
            from ..graph_constraints_store import load_constraints

            constraints = load_constraints()
        except Exception:  # noqa: BLE001
            constraints = {}
    include_list = _normalize_string_list(graphs) or list(constraints.get("include") or [])
    exclude_list = _normalize_string_list(exclude_graphs) or list(constraints.get("exclude") or [])
    only_list = _normalize_string_list(only_graphs)
    if discovery is None:
        discovery = bool(constraints.get("discovery", True))
    return include_list, exclude_list, only_list, discovery


def _label_hits(evidence: Any, result: dict[str, Any], project_id: str) -> None:
    """Give each hit with content its session label (``sid``, K1.4), so the
    answer's citations of it are checked and shown."""
    from .tool_evidence import label_passages, passage_from_hit

    hits = [hit for hit in result.get("results") or [] if hit.get("content")]
    passages = [passage_from_hit(n, hit) for n, hit in enumerate(hits, start=1)]
    for hit, sid in zip(hits, label_passages(evidence, passages, project_id=project_id)):
        hit["sid"] = sid


def _build_knowledge_query(
    client: "Client", reader: Any = None, evidence: Any = None
) -> dict[str, Any]:
    def _query(
        query: str,
        graphs: str | list[str] | None = None,
        exclude_graphs: str | list[str] | None = None,
        only_graphs: str | list[str] | None = None,
        discovery: bool | None = None,
        project_id: str | None = None,
    ) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            include, exclude, only, allow_discovery = _merged_constraints(
                client, graphs, exclude_graphs, only_graphs, discovery
            )
            selection = resolve_graph_selection(
                query_text=query,
                include=include,
                exclude=exclude,
                only=only,
                available=_available_graphs(client, target),
                default_graph=_default_graph_for(client, target),
                allow_discovery=allow_discovery,
            )
            sizing = query_sizing(client, reader)
            if not selection.graph_ids:
                return _dumps(
                    {
                        "type": "knowledge_query",
                        "results": [],
                        "selection": _selection_payload(selection),
                        **_insufficiency_note(),
                    },
                    sizing,
                )
            result = _query_selected_graphs(client, target, query, selection, sizing)
            _label_hits(evidence, result, target)
            if _knowledge_insufficient(result):
                result.update(_insufficiency_note())
            return _dumps(result, sizing)
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_query_failed", str(exc))

    return _tool(
        "knowledge_query",
        "Query knowledge across selected graphs. Selection precedence: "
        "exclude always wins; only_graphs is a hard constraint; graphs are "
        "explicit inclusions that may expand via discovery; otherwise "
        "discovery ranks candidates by relevance and falls back to the "
        "project default graph. Results include per-hit graph provenance. "
        "When nothing answers, the result says knowledge_insufficient — "
        "propose an extension instead of mutating.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "graphs": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Graph ids/names to include (string or list)",
                },
                "exclude_graphs": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Graph ids/names to exclude (wins over include)",
                },
                "only_graphs": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Hard constraint — query ONLY these graphs",
                },
                "discovery": {
                    "type": "boolean",
                    "description": (
                        "Allow relevance-ranked expansion of the set "
                        "(default: session Studio setting, else true)"
                    ),
                },
                "project_id": {"type": "string", "description": "Project (default: current)"},
            },
            "required": ["query"],
        },
        _query,
    )


def _build_graph_propose(client: "Client") -> dict[str, Any]:
    def _propose(
        graph_id: str,
        reason: str,
        missing_information: str | list[str] | None = None,
        source_artifacts: str | list[str] | None = None,
        proposed_operations: str | list[str] | None = None,
    ) -> str:
        if not graph_id or not reason:
            return _structured_error("proposal_invalid", "graph_id and reason are required.")
        # Session acquisition mode (write gate) — pinned/disabled refuse
        # before any server call.
        gate_error = check_propose(client, graph_id)
        if gate_error:
            return _structured_error("knowledge_acquisition_blocked", gate_error)
        try:
            proposal = client.knowledge.graph_propose(
                graph_id,
                reason=reason,
                missing_information=_normalize_string_list(missing_information),
                source_artifacts=_normalize_string_list(source_artifacts),
                proposed_operations=_normalize_string_list(proposed_operations),
            )
            return json.dumps(
                {
                    "type": "knowledge_graph_proposal",
                    "proposal": proposal,
                    "next": (
                        "Show this proposal to the user and STOP. Do NOT call "
                        "knowledge_ingest or modify knowledge until the user "
                        "explicitly approves. Then record their decision with "
                        "knowledge_graph_decide."
                    ),
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_graph_propose_failed", str(exc))

    return _tool(
        "knowledge_graph_propose",
        "Stage a graph-extension proposal when knowledge is insufficient: "
        "which graph to extend, why, what is missing, and which sources to "
        "use. This does NOT mutate anything — it asks the user for "
        "permission. Use whenever the user asks to build/extend knowledge "
        "you were not explicitly told to create.",
        {
            "type": "object",
            "properties": {
                "graph_id": {"type": "string", "description": "Target graph id or name"},
                "reason": {"type": "string", "description": "Why extension is needed"},
                "missing_information": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                },
                "source_artifacts": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Files/URLs/artifact refs to ingest if approved",
                },
                "proposed_operations": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                },
            },
            "required": ["graph_id", "reason"],
        },
        _propose,
    )


def _build_graph_decide(client: "Client") -> dict[str, Any]:
    def _decide(proposal_id: str, approved: bool) -> str:
        if not proposal_id:
            return _structured_error("decision_invalid", "proposal_id is required.")
        try:
            proposal = client.knowledge.graph_decide(proposal_id, approved=approved)
            status = proposal.get("status", "unknown")
            note = (
                "Approved — you may now execute the proposed operations "
                "(knowledge_ingest with proposal_id); the server records "
                "provenance. This approval prompt is the user's authorization."
                if status == "approved"
                else "Declined — do NOT mutate the graph; answer with existing knowledge."
            )
            return json.dumps(
                {"type": "knowledge_graph_decision", "proposal": proposal, "next": note},
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_graph_decide_failed", str(exc))

    return _tool(
        "knowledge_graph_decide",
        "Record the USER's explicit decision on a graph-extension proposal "
        "(after the user answered your proposal). approved=true authorizes "
        "the mutation; approved=false forbids it. Never call this on your "
        "own initiative — only after the user answered.",
        {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "approved": {"type": "boolean"},
            },
            "required": ["proposal_id", "approved"],
        },
        _decide,
    )


def build_knowledge_graph_tools(
    client: "Client", reader: Any = None, evidence: Any = None
) -> list[dict[str, Any]]:
    """Multi-graph tools for Companion (discovery/selection/proposal/decision).

    Actor marking happens per-mutation (agent_mutation), not here — the
    same client also backs human-initiated Studio actions. ``reader`` is the
    session's model (a name or its accessor): ``knowledge_query`` asks K3
    for that reader's profile ``k_max`` and shows each hit within its share
    of the profile's pack (ADR-093 D-14, :func:`query_sizing`); ``evidence``
    (the session's ToolEvidence) labels its hits in the ledger (K1.4).
    """
    return [
        _build_graph_list(client),
        _build_knowledge_query(client, reader, evidence),
        _build_graph_propose(client),
        _build_graph_decide(client),
    ]
