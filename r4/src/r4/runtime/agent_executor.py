"""Milestone 3: agentic iteration executor for the LocalRuntime.

Each iteration of a local research loop becomes one bounded agent turn against
the caller's configured chat endpoint (`Client.chat_completion` — gateway or
public-source path, no full Companion session needed). Every iteration:

1. Retrieves the passages UMRS keeps for the goal plus the last hypothesis
   (``client.rag.retrieve``, k_max 4) and renders them with the shared
   context-pack renderer (ADR-093 C11). When retrieval is unavailable, or
   the project had nothing to search (``searched == 0``), it falls back to
   a digest of the project knowledge graph (tolerates offline/UMRS-down
   graphs).
2. Asks the model for ONE hypothesis + evidence ids + evaluation + next step
   grounded in that evidence (JSON-only response). Cited passage labels
   ([n]) are recorded as the passages' node ids.
3. Records the iteration summary (LLM output when parseable, digest fallback
   otherwise) — cross-iteration memory accumulates hypotheses on this
   instance so later turns build on earlier ones instead of repeating them.

Falls back gracefully: if the chat call fails or the reply is not parseable,
the iteration records a knowledge digest with ``llm: false`` and keeps the
loop durable rather than wedging the runtime.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from r4_contracts.context_pack import render_pack

from .api import LoopState  # noqa: F401  (documentation anchor for the contract)

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_PASSAGE_LABEL_RE = re.compile(r"^\[?\s*(\d{1,3})\s*\]?$")
_PASSAGES_END = "</passages>"
#: Passages per iteration, the pack's token cap and the GET query length cap.
_LOOP_K_MAX = 4
_LOOP_PACK_TOKENS = 2000
_RETRIEVE_QUERY_CHARS = 2000

SYSTEM_PROMPT = (
    "You are a research-loop iteration agent for an autonomous scientific "
    "research runtime. On each iteration you are given: passages retrieved "
    "from the project's documents for the goal (or, when retrieval is "
    "unavailable, a digest of the project knowledge graph), the hypotheses "
    "proposed so far, and the research goal. Propose exactly ONE new "
    "hypothesis grounded in that evidence, answer with a JSON object only — "
    "no prose outside the JSON. Schema:\n"
    '{"hypothesis": "one-sentence hypothesis",'
    ' "evidence_ids": ["[n] labels of the supporting passages, or ids of knowledge nodes"],'
    ' "evaluation": "1-3 sentence progress evaluation vs the goal",'
    ' "next_step": "the next concrete action (ingest/experiment/query)",'
    ' "requires": {} or {"gpu": 1, "cores": N, "cluster": true} — optional, '
    "only when THIS step would actually need cluster-class compute}"
)


_CLUSTER_REQUIRE_KEYS = {"gpu", "cores", "cluster", "slurm", "mpi", "fugaku"}


def _normalize_iteration_requires(raw: Any) -> Dict[str, Any]:
    """Cluster-flavored subset of the LLM's per-iteration requires.

    Keeps only truthy cluster keys (mirrors the loop-level HybridRuntime
    routing rule). Never routes a running loop across venues — one authority
    per loop is hard — it just makes the step's needs durable so future
    waves (UMRS authoring) can move the next seed to the right venue.
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        key = str(key).lower()
        if key in _CLUSTER_REQUIRE_KEYS and value not in (None, False, "", 0, "0", "false"):
            out[key] = value
    return out


class LocalAgentIterationExecutor:
    """Per-iteration agent for LocalRuntime loops (callable as executor)."""

    def __init__(
        self,
        client: Any,
        project_id: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 700,
        max_nodes: int = 30,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._model = model
        self._max_tokens = max_tokens
        self._max_nodes = max_nodes
        self.hypotheses: List[Dict[str, Any]] = []

    def __call__(self, loop_id: str, iteration_number: int, research_goal: str) -> Dict[str, Any]:
        del loop_id  # identity tracked via hypotheses/state instead
        graph = self._load_graph()
        knowledge = self._knowledge_context(research_goal, graph)
        reply = self._call_llm(research_goal, knowledge, iteration_number)
        return self._build_summary(iteration_number, research_goal, graph, reply, knowledge)

    # ── knowledge ─────────────────────────────────────────────────────

    def _load_graph(self) -> Dict[str, Any]:
        knowledge = getattr(self._client, "knowledge", None)
        if knowledge is None:
            return {"nodes": [], "edges": [], "note": "knowledge client unavailable"}
        try:
            return knowledge.get_graph(self._project_id)
        except Exception as exc:  # noqa: BLE001 - loop must not die on offline knowledge
            return {"nodes": [], "edges": [], "note": f"knowledge unavailable: {exc}"}

    def _retrieval_query(self, goal: str) -> str:
        """The goal plus the last hypothesis, so each iteration reads ahead."""
        query = goal
        if self.hypotheses:
            query = f"{goal} {self.hypotheses[-1]['hypothesis']}"
        return query[:_RETRIEVE_QUERY_CHARS]

    def _retrieve(self, goal: str) -> tuple[Any, str]:
        """``(RetrieveResult, "")``, or ``(None, why retrieval is unavailable)``.

        The retrieve names the model that reads the passages (the loop's,
        else the client's configured one) and its D-14 profile, so UMRS sizes
        the pool, merge and partial cap for that reader (r4-scale-router-10).
        """
        from r4.graph_scope import retrieve_graph_args
        from r4.model_scale import configured_reader_fields

        rag = getattr(self._client, "rag", None)
        if rag is None:
            return None, "rag client unavailable"
        reader = configured_reader_fields(self._model, self._client)
        try:
            result = rag.retrieve(
                self._retrieval_query(goal),
                project_id=self._project_id,
                k_max=_LOOP_K_MAX,
                # ADR-108 D-8: the user's graph constraints, as knowledge_query.
                **retrieve_graph_args(self._client),
                **reader,
            )
        except Exception as exc:  # noqa: BLE001 - the loop must not die on offline retrieval
            logger.warning("research loop: retrieval unavailable, graph digest instead: %s", exc)
            return None, f"retrieval unavailable: {exc}"
        return result, ""

    def _knowledge_context(self, goal: str, graph: Dict[str, Any]) -> Dict[str, Any]:
        """``{text, citations, basis}``: the goal-ranked passages UMRS keeps,
        rendered by the shared context pack; nothing when the server says no
        passage addresses the goal; the graph digest when retrieval is down or
        the project had nothing to search (``searched == 0`` is no verdict on
        the goal, the router's vacuous rule)."""
        result, reason = self._retrieve(goal)
        if result is None or _searched_nothing(result):
            digest = "Knowledge graph digest:\n" + self._graph_context(graph)
            note = reason or _searched_nothing_note(result)
            return {"text": digest, "citations": {}, "basis": "graph_digest", "note": note}
        status = result.sufficiency.status
        if status == "insufficient" or not result.passages:
            note = f"Project documents: no passage addresses this goal (sufficiency: {status})."
            return {"text": note, "citations": {}, "basis": status}
        pack, citations = render_pack(
            goal, result.passages, status=status, instruction="none", max_tokens=_LOOP_PACK_TOKENS
        )
        block = pack[: pack.find(_PASSAGES_END) + len(_PASSAGES_END)]
        return {"text": block, "citations": citations, "basis": status}

    # ── llm ────────────────────────────────────────────────────────────

    def _messages(self, goal: str, knowledge: Dict[str, Any], number: int) -> list:
        """Evidence first, the goal last (ADR-093 D-4)."""
        from r4.chat_types import ChatMessage

        context = knowledge["text"]
        if self.hypotheses:
            context += "\nHypotheses already proposed (do NOT repeat these):\n"
            context += "\n".join(f"- {h['hypothesis']}" for h in self.hypotheses[-5:])
        return [
            ChatMessage.system(SYSTEM_PROMPT),
            ChatMessage.user(f"{context}\n\nResearch goal: {goal}\nIteration: {number}"),
        ]

    def _graph_context(self, graph: Dict[str, Any]) -> str:
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        lines = [f"nodes={len(nodes)} edges={len(edges)}"]
        for node in nodes[: self._max_nodes]:
            label = node.get("name") or node.get("title") or node.get("id") or "?"
            node_type = node.get("type", "knowledge")
            lines.append(f"- [{node_type}] {label}")
        return "\n".join(lines)

    def _call_llm(
        self, goal: str, knowledge: Dict[str, Any], number: int
    ) -> Optional[Dict[str, Any]]:
        from r4.client import gate_project

        try:
            # ADR-108 D-9: the data-class gate asks the LOOP's project.
            with gate_project(self._project_id):
                response = self._client.chat_completion(
                    self._messages(goal, knowledge, number),
                    model=self._model,
                    stream=False,
                    temperature=0.3,
                    max_tokens=self._max_tokens,
                )
        except Exception as exc:  # noqa: BLE001 - fall back to digest, keep the loop durable
            return {"_error": str(exc)}
        return self._parse_reply(getattr(response, "content", "") or "")

    @staticmethod
    def _parse_reply(text: str) -> Dict[str, Any]:
        match = _JSON_BLOCK_RE.search(text)
        if match is None:
            return {"_parse_error": True, "raw": text[:200]}
        try:
            payload = json.loads(match.group())
        except json.JSONDecodeError:
            return {"_parse_error": True, "raw": text[:200]}
        return payload if isinstance(payload, dict) else {"_parse_error": True, "raw": text[:200]}

    # ── summary ───────────────────────────────────────────────────────

    def _build_summary(
        self,
        number: int,
        goal: str,
        graph: Dict[str, Any],
        reply: Optional[Dict[str, Any]],
        knowledge: Dict[str, Any],
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "iteration": number,
            "phase": "agent_iteration",
            "knowledge_basis": knowledge["basis"],
            **_digest_fields(graph, goal),
        }
        if knowledge.get("note"):
            summary["knowledge_note"] = knowledge["note"]
        if reply and "hypothesis" in reply:
            hypothesis = str(reply.get("hypothesis", "")).strip()
            requires = _normalize_iteration_requires(reply.get("requires"))
            evidence = _evidence_node_ids(reply.get("evidence_ids"), knowledge["citations"])
            if hypothesis:
                entry: Dict[str, Any] = {
                    "iteration": number,
                    "hypothesis": hypothesis,
                    "evidence_ids": evidence,
                }
                if requires:
                    entry["requires"] = requires
                self.hypotheses.append(entry)
            summary.update(
                {
                    "llm": True,
                    "hypothesis": hypothesis,
                    "evidence_ids": evidence,
                    "evaluation": reply.get("evaluation", ""),
                    "next_step": reply.get("next_step", ""),
                }
            )
            if requires:
                summary["requires"] = requires
            return summary
        summary["llm"] = False
        summary["note"] = reply.get("_error") or "LLM reply unusable; digest-only iteration"
        return summary


def _searched_nothing(result: Any) -> bool:
    """The project had nothing in scope to search (no chunk vectors, or the
    query encoder was down). An older server's fallback counts only what
    passed its legacy cut, so its 0 is a verdict, not an empty scope."""
    return result.searched == 0 and not result.passages and "fallback" not in result.lanes


def _searched_nothing_note(result: Any) -> str:
    sufficiency = result.sufficiency
    facts = [sufficiency.status, sufficiency.basis, sufficiency.detail]
    return "retrieval searched nothing: " + "; ".join(fact for fact in facts if fact)


def _evidence_node_ids(raw: Any, citations: Dict[str, str]) -> Any:
    """Cited passage labels (``[2]``, ``2``) become the passages' node ids;
    anything else (a node id from the graph digest) is kept as given."""
    if not isinstance(raw, list):
        return [] if raw is None else raw
    return [_node_id_for(item, citations) for item in raw]


def _node_id_for(item: Any, citations: Dict[str, str]) -> Any:
    match = _PASSAGE_LABEL_RE.match(str(item).strip())
    if match is None:
        return item
    return citations.get(f"[{match.group(1)}]", item)


def _goal_tokens(goal: str) -> set:
    """Long, distinctive tokens from the user's goal text."""
    return {tok.lower() for tok in goal.split() if len(tok) > 3}


def _goal_matches(nodes: list, tokens: set) -> int:
    """Count nodes whose label shares at least one goal token."""
    if not tokens:
        return 0
    return sum(1 for node in nodes if _node_has_token(node, tokens))


def _node_has_token(node: dict, tokens: set) -> bool:
    label = str(node.get("name") or node.get("title") or "").lower()
    return any(token in label for token in tokens)


def _digest_fields(graph: Dict[str, Any], goal: str) -> Dict[str, Any]:
    """Low-level digest shared by LLM and fallback paths."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    return {
        "graph_nodes": len(nodes),
        "graph_edges": len(edges),
        "goal_matches": _goal_matches(nodes, _goal_tokens(goal)),
    }
