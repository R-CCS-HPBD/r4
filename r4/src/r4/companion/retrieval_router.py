"""Harness pre-retrieval router (ADR-093 D-8; C12).

Before round 1 of an eligible turn, the router asks UMRS for evidence
(``client.rag.retrieve``, ``GET /gw/rag/retrieve``) and puts the server's
context pack into the user turn, so a small model sees the passages without
having to pick a search tool first. Both send paths call it before payload
preparation (``session_flow.send_turn`` and ``_send_plan_only_turn``): the
augmented message is what ``fit_payload`` counts and what the model reads,
while the session stores only the raw message.

What R4 decides here, and what it never does (CONSTITUTION §1.3):

- **whether to search**, by deterministic rules and no model call: off by
  default (``R4_RAG_ROUTER=off|auto|on``; ``auto`` routes only a reader
  whose size class is listed in ``R4_RAG_ROUTER_AUTO``, default ``small``,
  so no class is routed by default before its gate); never for a slash command, an
  acknowledgement, a message under three content tokens (English function
  words aside; numbers and identifiers count), a message with
  workspace intent (a code fence, a source path, a traceback), without a
  project, or for a project that had nothing to search in the last ten
  minutes. A fenced output template (a json/yaml fence whose values are all
  placeholders, ``{"verdict": "...", "evidence": []}``) is a format request,
  not code, and is no workspace intent (wave H, with ``R4_RAG_FOCUS=on``;
  off, the default, keeps the old rule). The session does not route a
  harness-built turn (an approved plan's execution);
- **what to search** (wave H, :mod:`query_focus`, opt-in with
  ``R4_RAG_FOCUS=on`` since focus-v1 failed its held-out gate, 2026-10-11):
  the message's question,
  not its instructions. A labelled field (``Claim:``), a quotation, a Latin
  run in a Japanese framing or the tail after a framing colon is searched on
  its own; else the message less its framing and output-format segments;
  else, with no framing, the message exactly as written. ONE query (the
  large profile's expansion is asked about the focused text): once anything
  was extracted the raw message is never a variant, because the server takes
  the best cosine over variants and the framing would come back. The model
  still reads the whole message (the pack's question is unchanged), the
  question rule reads the raw message, and with the switch off (the default)
  the whole message is searched as before;
- **how much to show**: the reading model's D-14 profile sets ``k_max`` and
  the token cap only (:func:`pack_profile`). The session model's scale is
  DETECTED once per session, model and source (``r4.model_scale``: an
  override, what the source reports, else the shared name rule, all read by
  the effective size); ``R4_RAG_PROFILE`` overrides. The stand-alone
  ``llama3.2:3b`` reads 3 passages in 1,200 tokens, rikyu's ``kimi-k3`` and
  ``glm-5.3`` read 4 in 2,000 (the large profile at the medium sizes since
  ADR-093 MD-14, 2026-10-13), a model of unknown scale reads the medium 4
  in 2,000 (with one notice per session on how to set its scale), never more
  than 12% of the context budget. The cap is for the evidence the pack adds,
  not the user's own words: a long question never starves the pack. The same
  profile and the session model travel to the retrieve verb (``profile``,
  ``reader_model``), so UMRS sizes its cut for the same reader and never
  guesses a remote model's size;
- **what to inject**, by the **server's** sufficiency: ``sufficient`` → the
  pack; ``partial`` (and ``unknown``) → at most the profile's ``partial_k``
  passages (2 for a small reader, 6 for a large one), marked
  ``match="partial"`` under an instruction to say what is missing. These
  bounds are the cut's: the graph bridges UMRS appends after it on a
  large-extraction site (D-14) ride after the cut's passages, within the
  token cap;
  ``insufficient`` → a note of at most 300 characters, and only for a
  question;
- **whether to expand the query** (D-14): the large profile asks the session
  model for two alternative search queries, ONE call on the session's own
  route before round 1 (the ADR-062 gate, the session's per-call guard and
  usage accounting); UMRS fuses the variants. Small and medium readers do not
  expand unless ``R4_RAG_EXPAND`` (eval) says so. A failed or malformed
  expansion expands nothing and never blocks the turn;
- **how to frame it**: a question gets the answer recap; a task turn (a plan)
  gets the passages and a citation line only (``frame="task"``), and no note.

The relevance cut, fusion and grading are UMRS's: this module never sorts,
re-ranks, thresholds or grades passages by score. The one thing it removes is
a passage the injection scanner rates HIGH (a security screen, not a grade).
It renders through ``r4_contracts.context_pack.render_pack`` with
session-monotonic ``[S#]`` labels and records what it showed in the
:mod:`evidence_ledger` (taint-registered as ``context_pack``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from r4_contracts.context_pack import NOT_COVERED, RenderedPack, build_pack, estimate_tokens
from r4_contracts.models import RETRIEVAL_PROFILES, profile_for_class
from r4_contracts.models import RetrievalProfile as ReaderProfile
from r4_contracts.retrieval import RetrievedPassage, RetrieveResult, bounded_passages

from r4.stream_assembly import RoundLimits

from .ablation import knowledge_off
from .cancellation import TurnCancelledError
from .citation_check import content_words
from .context_budget import RetrievalProfile, retrieval_profile
from .evidence_ledger import EvidenceLedger, remember_next_label, session_ledger

# The C12 token primitives live in query_focus since wave H; re-exported.
from .query_focus import _CJK_RE as _CJK_RE
from .query_focus import _FUNCTION_WORDS as _FUNCTION_WORDS
from .query_focus import _TOKEN_RE as _TOKEN_RE
from .query_focus import (
    _WORDS_RE,
    FOCUS_RULES_VERSION,
    MARKED_BASES,
    MIN_WORDS,
    Focus,
    content_tokens,
    focus_enabled,
    focus_query,
    has_anaphor,
    is_question_like,
    without_template_fences,
)

logger = logging.getLogger(__name__)

ROUTER_ENV = "R4_RAG_ROUTER"
#: The size classes ``R4_RAG_ROUTER=auto`` routes, as a comma list. Default
#: ``small``: a class is routed by default only once its gate passed (S5 for
#: small); ``unknown`` is a class of its own here, opted in by name.
ROUTER_AUTO_ENV = "R4_RAG_ROUTER_AUTO"
ROUTER_AUTO_DEFAULT: frozenset[str] = frozenset({"small"})
ROUTER_AUTO_CLASSES: tuple[str, ...] = ("small", "medium", "large", "unknown")
#: hyde | llm_rewrite | multi | off. Unset, the D-14 profile decides: multi for
#: a large reader, none otherwise (hyde and llm_rewrite are eval-only).
EXPAND_ENV = "R4_RAG_EXPAND"
ROUTER_MODES: tuple[str, ...] = ("off", "auto", "on")
EXPAND_MODES: tuple[str, ...] = ("hyde", "llm_rewrite", "multi")
EXPAND_OFF = "off"
SESSION_ROUTER_ATTR = "_rag_router"

EMPTY_PROJECT_TTL_SEC = 600.0  # a project with nothing to search is not asked again
NOTE_MAX_CHARS = 300
PARTIAL_MAX_PASSAGES = RETRIEVAL_PROFILES["small"].partial_k  # 2: the small reader's
RETRY_MAX_WORDS = 12  # content words
PREVIOUS_TERMS = 8
QUERY_MAX_CHARS = 2048  # the retrieve verb's limit across all variants
MAX_VARIANTS = 3
VARIANT_MAX_CHARS = 256  # one expansion query ("short alternative search queries")
#: The one expansion call's bound on a streamed route (ADR-087 round limits):
#: rikyu decodes at ~14 tokens/s, so an unbounded deliberation before round 1
#: could hold the turn for minutes. Past a bound the reply is cut, parses as
#: nothing, and the turn goes on with the user's own query.
EXPAND_LIMITS = RoundLimits(think_chars=4_000, think_seconds=30, text_chars=1_000)
MIN_PACK_TOKENS = 160  # below this the pack's own frame leaves no room for evidence
PACK_ORDER = "rank"
PACK_INSTRUCTION = "soft"
TASK_INSTRUCTION = "none"  # a plan is not a question: no answer instruction

NOTE = (
    "[Knowledge base: a search of this project's documents found no passage that "
    "answers this. If the answer has to come from those documents, reply exactly "
    f"{NOT_COVERED}; otherwise say that your answer does not come from them.]"
)

_ACK_WORDS = frozenset(
    """
    ok okay k kk thanks thank you thx ty yes yeah yep yup no nope nah sure great cool
    nice good fine perfect awesome excellent got it understood done please go ahead on
    continue proceed sounds lgtm alright right agreed much so very that works
    appreciate appreciated cheers
    """.split()
)
_ACK_CJK = ("ありがとう", "了解", "はい", "いいえ", "続けて", "お願いします", "どうも")
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*File \".+\", line \d+"
    r"|^\s*at [\w.$<>]+\(.*:\d+\)"
    r"|^\w*(?:Error|Exception): ",
    re.MULTILINE,
)
_CODE_EXT = (
    "py|ipynb|js|mjs|cjs|ts|tsx|jsx|rs|go|c|cc|cpp|h|hpp|java|kt|scala|rb|php|sh|bash|"
    "zsh|ya?ml|toml|json|cfg|ini|lock|sql|f90|cu|jl"
)
_SOURCE_PATH_RE = re.compile(
    r"(?:^|[\s\"'`(])(?:~|\.{1,2})?/[\w.-]+(?:/[\w.-]+)+"
    rf"|(?<![\w/.-])(?:[\w.-]+/)*[\w-]+\.(?:{_CODE_EXT})(?![\w])",
    re.IGNORECASE,
)
_VARIANT_LEAD_RE = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)]|query\s*\d*\s*:)\s*", re.I)


def router_mode() -> str:
    """``R4_RAG_ROUTER``: off (default), auto (the classes
    :func:`router_auto_classes` lists) or on (every class)."""
    raw = os.environ.get(ROUTER_ENV, "").strip().lower()
    return raw if raw in ROUTER_MODES else "off"


def router_auto_classes() -> frozenset[str]:
    """``R4_RAG_ROUTER_AUTO``: the size classes ``auto`` routes.

    A comma list of ``small``, ``medium``, ``large`` and ``unknown`` (case and
    spaces ignored). Unset, empty, or naming no known class: ``small`` only.
    """
    raw = os.environ.get(ROUTER_AUTO_ENV, "")
    named = {part.strip().lower() for part in raw.split(",")}
    picked = frozenset(named.intersection(ROUTER_AUTO_CLASSES))
    return picked or ROUTER_AUTO_DEFAULT


def expand_mode(default: str = "") -> str:
    """The query expansion to run: hyde, llm_rewrite, multi, or '' (none).

    An explicit ``R4_RAG_EXPAND`` wins (``off`` turns a large reader's
    multi-query off); unset or unrecognised, ``default`` (the profile's)."""
    raw = os.environ.get(EXPAND_ENV, "").strip().lower()
    if raw == EXPAND_OFF:
        return ""
    if raw in EXPAND_MODES:
        return raw
    return default if default in EXPAND_MODES else ""


# ------------------------------------------------------------ profiles ---


def reader_profile(size_class: str) -> tuple[str, ReaderProfile]:
    """The D-14 profile a size class reads with; ``unknown`` reads with
    ``medium`` (C22; lead decision 2026-10-10)."""
    return profile_for_class(size_class)


def pack_profile(
    model: str,
    context_budget: int,
    *,
    override: str | None = None,
    size_class: str | None = None,
) -> RetrievalProfile:
    """The pre-retrieval pack's size for the reading ``model`` (ADR-093 D-14).

    ``override`` (else ``R4_RAG_PROFILE``) names the profile outright;
    otherwise ``size_class`` (the scale ``r4.model_scale`` detected) or the
    shared name rule picks it: ``llama3.2:3b`` → small (3 passages, 1,200
    tokens), ``kimi-k3`` and ``glm-5.3`` → large (4, 2,000 since MD-14), a model of
    unknown scale → medium (4, 2,000). The token cap never exceeds 12% of
    ``context_budget``. ``size_class`` keeps the reader's own class
    (``unknown`` included) for ``R4_RAG_ROUTER=auto``.
    """
    return retrieval_profile(model, context_budget, override=override, size_class=size_class)


def reader_fields(model: str | None, *, client: Any = None) -> dict[str, str | None]:
    """``profile`` and ``reader_model`` for a retrieve ``model`` will read.

    Every R4 retrieve made for the session model sends both (D-14), so UMRS
    sizes its cut for that reader, not for the site's default. The profile is
    the one :func:`pack_profile` sizes the pack with: the model's DETECTED
    scale (``r4.model_scale``, memoised per process, so the router's
    detection serves this too), ``R4_RAG_PROFILE`` overriding it. No model:
    nothing is sent, and the site's default applies.
    """
    from r4.model_scale import retrieve_reader_fields

    return retrieve_reader_fields(model, client=client)


def expansion_mode_for(profile: RetrievalProfile) -> str:
    """The expansion a turn read at ``profile`` runs: ``R4_RAG_EXPAND``, else
    the D-14 default (``multi`` for a large reader, none otherwise)."""
    return expand_mode(profile.reader[1].expand)


def _partial_cap(profile: RetrievalProfile) -> int:
    """Passages a partial (or unknown) verdict may show: the profile's
    ``partial_k`` (2 small, 3 medium, 3 large since ADR-093 MD-14 (6 before)),
    never above the pack's k_max."""
    return min(profile.reader[1].partial_k, profile.k_max)


# ----------------------------------------------------------- the rules ---


def _is_acknowledgement(text: str) -> bool:
    words = _WORDS_RE.findall(text.casefold())
    if words and all(word in _ACK_WORDS for word in words):
        return True
    return text.strip(" .!?。！？") in _ACK_CJK


def has_workspace_intent(text: str) -> bool:
    """A code fence, a source path or a traceback: the turn is about files."""
    return "```" in text or bool(_TRACEBACK_RE.search(text) or _SOURCE_PATH_RE.search(text))


def message_skip_reason(message: str) -> str | None:
    """Why this message is not a retrieval turn, or ``None``."""
    text = str(message or "").strip()
    if text.startswith("/"):
        return "slash command"
    if _is_acknowledgement(text):
        return "acknowledgement"
    if len(content_tokens(text)) < MIN_WORDS:
        return "too short"
    if has_workspace_intent(without_template_fences(text) if focus_enabled() else text):
        return "workspace intent"
    return None


def skip_reason(message: str, *, mode: str, size_class: str = "") -> str | None:
    """The router-level and message-level skip rules, in order."""
    if mode not in ("auto", "on"):
        return "router off"
    if knowledge_off():
        return "knowledge ablated"
    if mode == "auto" and (size_class or "unknown") not in router_auto_classes():
        return f"auto: {size_class or 'unknown'} model"
    return message_skip_reason(message)


def _focus(message: str) -> Focus:
    """What to search for ``message`` (:func:`query_focus.focus_query`); any
    failure searches the whole message, as before wave H."""
    try:
        return focus_query(message)
    except Exception as exc:  # noqa: BLE001 - the focus never costs the turn
        logger.debug("query focus skipped: %s", type(exc).__name__)
        return Focus(message, "whole")


def retry_query(message: str, previous_user: str, *, focus: Focus | None = None) -> str | None:
    """The history-aware variant: the previous message's terms, then this one.

    Only for a message of under twelve content words with an anaphor ("what
    about its effect on bone density?"). Both messages are read through their
    focus (wave H): the previous message's terms are its claim's, not its
    instructions'. When this message's content is marked (a field, a quote,
    a script run, a colon tail), a "this" in its framing points forward to
    that content, so only the focused text is tested for an anaphor."""
    focus = focus or _focus(message)
    text = focus.query
    anaphoric = has_anaphor(text if focus.basis in MARKED_BASES else message)
    short = len(content_words(text)) < RETRY_MAX_WORDS
    if not previous_user or not short or not anaphoric:
        return None
    terms = list(dict.fromkeys(content_words(_focus(previous_user).query)))[:PREVIOUS_TERMS]
    return f"{' '.join(terms)} {text.strip()}" if terms else None


def bound_queries(queries: Sequence[str]) -> list[str]:
    """At most three distinct variants, 2,048 characters in all, first kept whole."""
    out: list[str] = []
    room = QUERY_MAX_CHARS
    for raw in dict.fromkeys(" ".join(str(q).split()) for q in queries):
        if not raw or len(out) >= MAX_VARIANTS or (out and room < 16):
            continue
        out.append(raw[:room])
        room -= len(out[-1])
    return out


# ------------------------------------------------------------ decision ---


@dataclass(frozen=True)
class RouteDecision:
    """What the router did with one turn, and the message the model reads."""

    message: str
    action: str  # skip | pack | note | none | error
    reason: str = ""
    status: str = ""  # the server's sufficiency
    sids: tuple[str, ...] = ()
    withheld: int = 0
    retried: bool = False
    variants: int = 1
    searched: int = 0
    frame: str = "question"  # question | task (render_pack's frame)
    profile: str = ""  # the D-14 profile the pack and the server's cut were sized for
    focus: str = "whole"  # what was searched (query_focus basis): whole = the message

    def announcement(self) -> str:
        """The line the TUI shows: ``Retrieved n passages (status)``."""
        if self.action == "error":
            return f"Retrieval unavailable ({self.reason})"
        extras = [self.status or "unknown"]
        if self.withheld:
            extras.append(f"{self.withheld} withheld by the content scanner")
        if self.retried:
            extras.append("retried with the previous message")
        if self.focus != "whole":
            extras.append("searched the question, not its instructions")
        return f"Retrieved {len(self.sids)} passages ({'; '.join(extras)})"

    def event_data(self) -> dict[str, Any]:
        return {
            "kind": "context_pack",
            "action": self.action,
            "reason": self.reason,
            "status": self.status,
            "n": len(self.sids),
            "sids": list(self.sids),
            "withheld": self.withheld,
            "retried": self.retried,
            "variants": self.variants,
            "searched": self.searched,
            "frame": self.frame,
            "profile": self.profile,
            "focus": self.focus,
            "focus_rules": FOCUS_RULES_VERSION,
        }


# ------------------------------------------------------------- scanner ---


def _default_scanner() -> Any:
    try:
        from r4.agent.untrusted_scanner import get_default_scanner
    except ImportError:  # pragma: no cover - the scanner ships with r4
        return None
    return get_default_scanner()


def _rag_source() -> Any:
    from r4.agent.untrusted_scanner import ContentSource

    return ContentSource.RAG_CHUNK


def _rated_high(scanner: Any, passage: RetrievedPassage) -> bool:
    """The T-216 scanner's verdict on a passage: HIGH/CRITICAL or blocked.
    A scanner failure withholds the passage (fail closed)."""
    try:
        result = scanner.scan(
            passage.text,
            source=_rag_source(),
            context={"document_id": passage.artifact_id, "source_id": passage.source_id},
        )
    except Exception as exc:
        logger.warning("content scan failed; passage withheld: %s", exc)
        return True
    level = getattr(result, "risk_level", None)
    value = str(getattr(level, "value", level) or "").lower()
    return bool(getattr(result, "is_blocked", False)) or value in ("high", "critical")


# -------------------------------------------------------------- router ---


class RetrievalRouter:
    """Session-scoped: one per companion session, owning its ledger."""

    def __init__(
        self,
        retrieve: Callable[..., Any],
        ledger: EvidenceLedger,
        *,
        scanner: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._retrieve = retrieve
        self.ledger = ledger
        self._scanner = scanner
        self._clock = clock
        self._empty_at: dict[str, float] = {}

    def route(
        self,
        message: str,
        *,
        mode: str,
        project_id: str | None,
        profile: RetrievalProfile,
        question: str | None = None,
        previous_user: str = "",
        turn_id: str = "",
        fits: Callable[[str], bool] | None = None,
        expand: Callable[[str], list[str]] | None = None,
        cancel: Any = None,
        task: bool = False,
        reader_model: str | None = None,
    ) -> RouteDecision:
        """Decide, retrieve and render. ``message`` is the raw user message
        (it decides and is searched); ``question`` is what the pack asks
        (the plan path's revision prompt), by default the message. ``task``
        frames the pack for a plan rather than an answer: no answer
        instruction, no five-sentence recap, no insufficiency note.

        ``profile`` sizes the pack; its D-14 name and ``reader_model`` (the
        model that will read the pack) go to the retrieve verb, so the
        server's cut is sized for the same reader. ``expand`` (the large
        profile's multi-query) runs only once the turn is a retrieval turn."""
        shown = message if question is None else question
        named = {"frame": "task" if task else "question", "profile": profile.reader[0]}
        reason = skip_reason(message, mode=mode, size_class=profile.size_class)
        reason = reason or self._scope_skip(project_id, profile)
        if reason:
            return RouteDecision(message=shown, action="skip", reason=reason, **named)
        focus = _focus(message)
        named["focus"] = focus.basis
        logger.debug("pre-retrieval searches (%s): %s", focus.basis, focus.query)
        queries = bound_queries([focus.query, *(expand(focus.query) if expand else [])])
        ask = {
            "project_id": str(project_id),
            "k_max": profile.k_max,
            "profile": named["profile"],
            "reader_model": reader_model or None,
            "_turn_cancel": cancel,
        }
        try:
            result, retried = self._search(queries, focus, message, previous_user, ask)
        except TurnCancelledError:
            raise  # the user stopped the turn: not a retrieval failure (r4-harness-10)
        except Exception as exc:  # retrieval never blocks a turn
            return RouteDecision(message=shown, action="error", reason=_short(exc), **named)
        facts = {
            "retried": retried,
            "variants": len(queries),
            "searched": result.searched,
            **named,
        }
        return self._inject(message, shown, result, profile, turn_id, fits, facts)

    def _scope_skip(self, project_id: str | None, profile: RetrievalProfile) -> str | None:
        if not project_id:
            return "no project"
        if profile.max_tokens < MIN_PACK_TOKENS:
            return "no room in the context budget"
        since = self._empty_at.get(project_id)
        if since is not None and self._clock() - since < EMPTY_PROJECT_TTL_SEC:
            return "project had nothing to search"
        return None

    def _call(self, queries: list[str], ask: dict[str, Any]) -> RetrieveResult:
        """One retrieve call; ``ask`` holds the project, k_max, the profile
        name, the reader model and the turn's cancellation token."""
        query: str | list[str] = queries[0] if len(queries) == 1 else list(queries)
        raw = self._retrieve(query, **ask)
        if isinstance(raw, RetrieveResult):
            return raw
        return RetrieveResult.from_dict(dict(raw or {}))

    def _search(self, queries, focus, message, previous_user, ask):
        """One retrieve, plus the one deterministic history-aware retry when the
        server is not sure; the retry's two variants (the focused text and the
        retry) are fused server-side."""
        result = self._call(queries, ask)
        self._note_scope(ask["project_id"], result)
        retry = retry_query(message, previous_user, focus=focus)
        if retry is None or result.sufficiency.status == "sufficient":
            return result, False
        try:
            return self._call(bound_queries([focus.query, retry]), ask), True
        except TurnCancelledError:
            raise
        except Exception as exc:
            logger.info("history-aware retry failed; first result kept: %s", exc)
            return result, False

    def _note_scope(self, project_id: str, result: RetrieveResult) -> None:
        """Remember a project with nothing in scope. An older server's
        fallback counts only what passed its cut, so its 0 proves nothing."""
        if "fallback" in result.lanes:
            return
        if result.searched == 0 and not result.passages:
            self._empty_at[project_id] = self._clock()
        else:
            self._empty_at.pop(project_id, None)

    def _screen(self, passages: Sequence[RetrievedPassage]) -> tuple[list[RetrievedPassage], int]:
        """The server's passages in the server's order, minus any the content
        scanner rates HIGH (withheld, counted)."""
        scanner = self._scanner if self._scanner is not None else _default_scanner()
        kept: list[RetrievedPassage] = []
        for passage in passages:
            if scanner is not None and _rated_high(scanner, passage):
                continue
            kept.append(passage)
        return kept, len(passages) - len(kept)

    def _inject(self, message, shown, result, profile, turn_id, fits, facts) -> RouteDecision:
        """Inject by the server's verdict: sufficient → the pack (k_max);
        partial or unknown → the profile's ``partial_k`` passages marked
        partial; insufficient → the note, for a question only."""
        status = result.sufficiency.status
        kept, withheld = self._screen(result.passages)
        base = {"status": status, "withheld": withheld, **facts}
        if status == "insufficient" or not kept:
            return _without_pack(message, shown, status, base)
        sufficient = status == "sufficient"
        # The bound is the cut's: UMRS's graph bridges (D-14) ride after it,
        # as the server appended them, within the pack's token cap.
        offered = bounded_passages(kept, profile.k_max if sufficient else _partial_cap(profile))
        render_status = "sufficient" if sufficient else "partial"
        pack = self._render(shown, offered, render_status, profile.max_tokens, fits, base["frame"])
        # The ledger (and so the taint fence and the citation check) holds
        # what the model was shown: a passage cut to fit is recorded cut.
        entries = (
            self.ledger.record(pack.citations, pack.shown, turn_id=turn_id, status=status)
            if pack is not None
            else []
        )
        if not entries:
            return RouteDecision(message=shown, action="none", reason="pack did not fit", **base)
        return RouteDecision(
            message=pack.text, action="pack", sids=tuple(e.sid for e in entries), **base
        )

    def _render(self, shown, passages, status, max_tokens, fits, frame) -> RenderedPack | None:
        """The pack, refitted by dropping its last passage while the payload's
        hard limit would be exceeded; ``None`` when nothing fits.

        ``max_tokens`` caps the evidence the pack adds (its passages and
        frame), not the question it carries: the question is the user's own
        turn and costs the same with or without the pack (ADR-093 D-8 Cost,
        "up to 1,200 tokens more per routed turn")."""
        kept = list(passages)
        cap = max_tokens + estimate_tokens(shown.strip())
        instruction = TASK_INSTRUCTION if frame == "task" else PACK_INSTRUCTION
        while kept:
            pack = build_pack(
                shown,
                kept,
                status=status,
                order=PACK_ORDER,
                instruction=instruction,
                label="S",
                start=self.ledger.next_label,
                max_tokens=cap,
                frame=frame,
            )
            if not _has_evidence(pack):
                return None
            if fits is None or fits(pack.text):
                return pack
            kept = kept[: max(0, len(pack.citations) - 1)]
        return None


def _has_evidence(pack: RenderedPack) -> bool:
    """Some passage text survived the cap (not just the cut mark)."""
    return any(passage.text.replace("…", "").strip() for passage in pack.shown)


def _without_pack(message: str, shown: str, status: str, base: dict[str, Any]) -> RouteDecision:
    """No passage to show: the insufficiency note for a question, else nothing.
    A project with nothing to search gets no note (its verdict is vacuous, and
    the next ten minutes skip it anyway), and neither does a task turn (a
    plan is not answered NOT IN KNOWLEDGE BASE)."""
    noted = status == "insufficient" and base.get("searched") and base.get("frame") != "task"
    if noted and is_question_like(message):
        return RouteDecision(message=f"{NOTE}\n\n{shown}", action="note", **base)
    return RouteDecision(message=shown, action="none", **base)


def _short(exc: BaseException) -> str:
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:200]


# ----------------------------------------------------------- expansion ---

_EXPAND_PROMPTS: dict[str, tuple[str, int]] = {
    "hyde": (
        "Write one short passage, at most three sentences, that would answer the "
        "question as a scientific document might. Reply with the passage only.",
        1,
    ),
    "llm_rewrite": (
        "Rewrite the question as one search query that keeps its key terms. "
        "Reply with the query only.",
        1,
    ),
    "multi": (
        "Write two short alternative search queries for the question: each a "
        "different wording of it (synonyms, the technical term, the key entities). "
        "Reply with a JSON list of two strings and nothing else, for example "
        '["first query", "second query"].',
        2,
    ),
}
_FENCE_RE = re.compile(r"^```(?:json)?[ \t]*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)
_THINK_END = "</think>"


def _reply_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        choices = raw.get("choices") or [{}]
        message = (choices[0] or {}).get("message") or {}
        return str(message.get("content") or "")
    return str(getattr(raw, "content", "") or "")


def json_query_list(text: str, count: int) -> list[str]:
    """The queries a ``multi`` reply names, parsed strictly: the reply must be
    a JSON list of strings. Only a reasoning model's leading ``<think>`` block
    and one surrounding code fence are unwrapped; anything else (prose, a
    numbered list, an object, a non-string item) is no variant at all."""
    body = text.rsplit(_THINK_END, 1)[-1].strip()
    fenced = _FENCE_RE.match(body)
    try:
        items = json.loads(fenced.group(1) if fenced else body)
    except (ValueError, RecursionError):  # not JSON, or nested past the parser
        return []
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        return []
    queries = [" ".join(item.split())[:VARIANT_MAX_CHARS] for item in items]
    return [query for query in queries if query][:count]


def _variants_from(mode: str, text: str, count: int) -> list[str]:
    if mode == "multi":
        return json_query_list(text, count)
    if mode == "hyde":
        return [" ".join(text.split())] if text else []
    lines = (_VARIANT_LEAD_RE.sub("", line).strip(" \"'") for line in text.splitlines())
    return [line for line in lines if line][:count]


def expansion_variants(
    mode: str, question: str, request_chat: Callable[[list[dict[str, Any]], Any], Any]
) -> list[str]:
    """LLM query expansion: ONE call on the session's route, before round 1.

    ``multi`` (the large profile's default, D-14) asks for two alternative
    queries as a JSON list; ``hyde`` and ``llm_rewrite`` are eval-only. The
    variants go to ``retrieve`` beside the question (``q=[...]``) and are
    fused server-side; R4 never ranks them. A failed call (the provider, the
    session's call cap) or a malformed reply expands nothing: the turn goes
    on with the user's own query. Only the user's stop propagates."""
    prompt, count = _EXPAND_PROMPTS[mode]
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": question}]
    try:
        text = _reply_text(request_chat(messages, None)).strip()
    except TurnCancelledError:
        raise
    except Exception as exc:
        logger.warning("query expansion (%s) failed; the turn goes on unexpanded: %s", mode, exc)
        return []
    variants = _variants_from(mode, text, count)
    if not variants:
        logger.info("query expansion (%s) gave no usable variant; unexpanded", mode)
    return variants


# -------------------------------------------------------- session glue ---


def _session_model(session: Any) -> str:
    agent = getattr(session, "agent", None)
    effective = getattr(agent, "_effective_model", None)
    try:
        model = effective() if callable(effective) else getattr(agent, "model", "")
    except Exception:
        model = ""
    if not model:
        model = getattr(getattr(getattr(session, "client", None), "config", None), "model", "")
    return str(model or "")


def _session_scale(session: Any, model: str) -> Any:
    """The session model's detected scale (``r4.model_scale``), once per
    session, model and source; ``None`` without a model or on a failure
    (the name rule then sizes the pack)."""
    if not model:
        return None
    try:
        from r4.model_scale import session_model_scale

        return session_model_scale(session, model)
    except Exception as exc:  # noqa: BLE001 - detection never costs the turn
        logger.debug("model scale detection skipped: %s", type(exc).__name__)
        return None


def _session_profile(session: Any, model: str, scale: Any = None) -> RetrievalProfile:
    from .context import DEFAULT_MAX_TOKENS

    budget = getattr(getattr(session, "context", None), "max_context_tokens", DEFAULT_MAX_TOKENS)
    size = getattr(scale, "size_class", None)
    return pack_profile(model, budget if isinstance(budget, int) else 0, size_class=size)


def _announce_scale(session: Any, scale: Any) -> None:
    """The unknown-scale notice, once per session, on the event stream
    (``kind`` ``model_scale``): the model reads with the medium profile until
    its scale is set (``r4 model scale set``)."""
    if scale is None:
        return
    from r4.model_scale import take_session_notice

    notice = take_session_notice(session, scale)
    emit = getattr(getattr(session, "agent", None), "_emit", None)
    if notice is None or not callable(emit):
        return
    from .agent import CompanionEventType

    data = {"kind": "model_scale", "model": scale.model, "source": scale.source}
    data.update(size_class=scale.size_class, profile=scale.profile, basis=scale.basis)
    emit(CompanionEventType.THINKING, notice, data)


def announce_session_scale(session: Any) -> None:
    """The unknown-scale notice with the router off (its default until S5).

    ``search_docs`` sizes every retrieve by the session model's detected
    scale whether or not the router runs (D-14), so the one notice per
    session that says how to set an unknown scale is the session's, not the
    router's. Only a session that can retrieve is told; a failure is
    silent (the notice never costs the turn)."""
    rag = getattr(getattr(session, "client", None), "rag", None)
    if not callable(getattr(rag, "retrieve", None)):
        return
    try:
        _announce_scale(session, _session_scale(session, _session_model(session)))
    except Exception as exc:  # noqa: BLE001 - a notice never costs the turn
        logger.debug("model scale notice skipped: %s", type(exc).__name__)


#: The project label of a loaded bundle whose manifest names none.
LOCAL_BUNDLE_PROJECT = "local-bundle"


def _project_id(session: Any) -> str | None:
    """The session's project; offline (``r4 chat --knowledge`` with none
    configured) the loaded bundle's, as a label only: ``retrieve`` reads the
    bundle and never the platform, and the session's configuration is left
    as it is (ADR-093 D-12, C15)."""
    from .knowledge_tools import _project_id_for

    client = getattr(session, "client", None)
    return _project_id_for(client, None) or _bundle_project(client)


def _bundle_project(client: Any) -> str | None:
    rag = getattr(client, "rag", None)
    if getattr(rag, "has_local_bundle", False) is not True:
        return None
    summary = rag.local_bundle_summary()
    project = summary.get("project_id") if isinstance(summary, dict) else ""
    return str(project or "") or LOCAL_BUNDLE_PROJECT


def _previous_user_message(session: Any) -> str:
    """The last user message before this turn's (``start_turn`` appended it)."""
    messages = list(getattr(session, "messages", None) or [])
    for message in reversed(messages[:-1]):
        if getattr(message, "role", "") == "user":
            return str(getattr(message, "content", "") or "")
    return ""


def _fits_for(session: Any) -> Callable[[str], bool] | None:
    """Whether a user turn this size can fit at all: what is left once
    ``fit_payload`` has trimmed everything it may (history, the extra system
    prompt, tools) is the system prompt, the user turn and the reserve."""
    estimate = getattr(getattr(session, "context", None), "estimate_payload_budget", None)
    if not callable(estimate):
        return None
    from .agent import COMPANION_SYSTEM_PROMPT

    def fits(message: str) -> bool:
        budget = estimate(
            system_prompt=COMPANION_SYSTEM_PROMPT,
            extra_system="",
            history=[],
            user_message=message,
            tools=[],
        )
        return not bool(getattr(budget, "hard_limit_exceeded", False))

    return fits


def session_chat(session: Any) -> Callable[[list[dict[str, Any]], Any], Any] | None:
    """One model call on the session's own route, counted like a round.

    The session agent's ``_chat`` is the route every round takes: the
    client's ADR-062 gate resolves the model, and the session's usage callback
    records the call (a turn LLM call, with its tokens). Before it, the
    agent's ``_pre_llm_call`` (the session's per-turn and per-session call
    caps, or a team broker's run ceilings) is asked, exactly as before a
    round. No second client, no second route. ``EXPAND_LIMITS`` bounds a
    streamed reply."""
    agent = getattr(session, "agent", None)
    chat = getattr(agent, "_chat", None)
    if not callable(chat):
        return None
    guard = getattr(agent, "_pre_llm_call", None)
    call_type = str(getattr(agent, "_call_type", "") or "turn")

    def request(messages: list[dict[str, Any]], tools: Any) -> Any:
        if callable(guard):
            guard(call_type)
        return chat(messages, tools, limits=EXPAND_LIMITS)

    return request


def _expander_for(session: Any, profile: RetrievalProfile) -> Callable[[str], list[str]] | None:
    """The turn's query expander: ``R4_RAG_EXPAND``, else the profile's
    default (multi-query for a large reader only); ``None`` for none."""
    mode = expansion_mode_for(profile)
    chat = session_chat(session) if mode else None
    if chat is None:
        return None
    return lambda question: expansion_variants(mode, question, chat)


def _session_router(session: Any) -> RetrievalRouter | None:
    ledger = session_ledger(session, create=True)  # re-binds the taint registry
    router = getattr(session, SESSION_ROUTER_ATTR, None)
    if isinstance(router, RetrievalRouter):
        router.ledger = ledger
        return router
    retrieve = getattr(getattr(getattr(session, "client", None), "rag", None), "retrieve", None)
    if not callable(retrieve) or ledger is None:
        return None
    router = RetrievalRouter(retrieve, ledger)
    setattr(session, SESSION_ROUTER_ATTR, router)
    return router


def _announce(session: Any, decision: RouteDecision) -> None:
    """``Retrieved n passages (status)`` on the event stream (TUI, transcript)."""
    if decision.action == "skip":
        logger.debug("pre-retrieval skipped: %s", decision.reason)
        return
    emit = getattr(getattr(session, "agent", None), "_emit", None)
    if not callable(emit):
        return
    from .agent import CompanionEventType

    emit(CompanionEventType.THINKING, decision.announcement(), decision.event_data())


def route_turn_decision(
    session: Any, user_message: str, *, question: str | None = None, task: bool = False
) -> RouteDecision:
    """The router's decision for this session's current turn (no event)."""
    shown = user_message if question is None else question
    mode = router_mode()
    router = _session_router(session) if mode != "off" and not knowledge_off() else None
    if router is None:
        return RouteDecision(message=shown, action="skip", reason="router off or no retrieve verb")
    model = _session_model(session)
    scale = _session_scale(session, model)
    _announce_scale(session, scale)
    profile = _session_profile(session, model, scale)
    decision = router.route(
        user_message,
        question=shown,
        mode=mode,
        project_id=_project_id(session),
        profile=profile,
        previous_user=_previous_user_message(session),
        turn_id=str(getattr(session, "_current_turn_id", "") or ""),
        fits=_fits_for(session),
        expand=_expander_for(session, profile),
        cancel=getattr(session, "_turn_cancel", None),
        task=task,
        reader_model=model or None,
    )
    remember_next_label(session, router.ledger)
    return decision


def route_session_turn(
    session: Any, user_message: str, *, question: str | None = None, task: bool = False
) -> str:
    """The send paths' hook: the message the model reads this turn.

    ``user_message`` is the raw message (the session stores it unchanged);
    ``question`` is what the agent would otherwise be sent (the plan path's
    revision prompt); ``task`` frames the pack for a plan. With the router
    off this returns that message as is (after the session's one
    unknown-scale notice, :func:`announce_session_scale`). A stop during
    pre-retrieval raises
    :class:`TurnCancelledError` for the send path to end the turn as stopped.
    """
    shown = user_message if question is None else question
    if knowledge_off():
        return shown
    if router_mode() == "off":
        announce_session_scale(session)
        return shown
    try:
        decision = route_turn_decision(session, user_message, question=question, task=task)
    except TurnCancelledError:
        raise  # a stop ends the turn as stopped (the send path's answer)
    except Exception as exc:  # pre-retrieval never costs the user the turn
        logger.warning("pre-retrieval skipped: %s", exc)
        return shown
    _announce(session, decision)
    return decision.message


__all__ = [
    "EXPAND_ENV",
    "NOTE",
    "ROUTER_AUTO_ENV",
    "ROUTER_ENV",
    "RetrievalRouter",
    "RouteDecision",
    "announce_session_scale",
    "bound_queries",
    "content_tokens",
    "expand_mode",
    "expansion_mode_for",
    "expansion_variants",
    "focus_query",
    "is_question_like",
    "json_query_list",
    "message_skip_reason",
    "pack_profile",
    "reader_fields",
    "reader_profile",
    "retry_query",
    "route_session_turn",
    "route_turn_decision",
    "router_auto_classes",
    "router_mode",
    "session_chat",
    "skip_reason",
]
