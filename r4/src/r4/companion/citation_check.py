"""Deterministic citation check of the final answer (ADR-093 D-9; C13, C24).

Runs in the send flow after ``run_agent_turn`` on turns whose evidence ledger
holds passages. It makes **no** model call (ADR-087): every judgement is a
string comparison against the passages the turn was shown.

It reads the citations an answer gives:

- ``[S3]``, ``[3]``, ``[S1, S2]``, ``[S1-S3]`` and ``(S3)``;
- a bare ``S3`` that names a ledger passage;
- ``(Source: name)``, looked up across the whole ledger;
- the source name of a passage **this turn showed**, as the pack header shows
  it (``"evidence": ["scifact-18617259.md"]``; C24);
- a label written against such a name (``[S1] scifact-19058822.md``), which
  cites the name's passage (C24, below).

An unprefixed ``[3]`` is a citation only when every number in its brackets
names a passage this turn showed: ``[0, 1]`` (an interval), ``[12]`` past the
pack (a literature reference) and ``[10, 20]`` are not citations, and on a turn
that also called a tool which numbers its own hits (``search_docs``'s
``[1] …``) no bare number is read as a pack citation at all.

A source name cites its passage when it occurs exactly (case-sensitive) and as
a whole token: not inside ``xname.md``, ``name.mdx`` or ``dir/name.md``, while
a CJK character next to a Latin name bounds it (``資料scifact-1.mdは``). It
must look like a file name or an identifier: five or more characters with an
extension (``trial.pdf``, ``Smith J. 2019 trial.pdf``), or one token with an
underscore, a ``/`` or a run of four digits (``notes_v2``,
``scifact-14920021``). A passage named ``Introduction``, ``COVID-19`` or
``Table 1`` (UMRS falls back to the artifact title) never matches prose. Names
are read whether or not bare numbers are, and a sentence end inside one does
not split it. A name the turn did not show (an earlier turn's) is not matched
bare; its ``(Source: name)`` form still is. The digits inside a matched name
are never checked as numbers.

A bracketed label written directly before the shown name of *another* passage
cites the name's passage, not the label's, and is reported as a mislabel:
``[S1] scifact-19058822.md`` where that name is S2, also with the name in
markdown (``[S1] `name` ``, ``[S1](name)``) or joined by an unspaced ``-`` or
``：``. A spaced colon or dash binds the two only where the label opens its
segment (a line, a list item, a JSON string): mid-clause,
``20% [S1] — name, by contrast, …`` is the clause's citation. A label that
agrees with its name (the name's own, or an earlier turn's label for the same
document) is none.

A piece of nothing but citations (``"X rose. [S1] x.md"``, ``Source: x.md`` on
the next line) belongs to the sentence before it, which is checked with them.

Per cited sentence:

- **phantom**: the label is not in the ledger (never retrieved);
- **mislabel**: ``(label, named)``, a label written against another shown
  passage's name;
- **support**: the share of the sentence's content terms present in the cited
  passages: supported >= 0.5, weak 0.3-0.5, unsupported < 0.3;
- **numbers**: every number must appear in a cited passage or in the user's
  message;
- **quotes**: a quoted span of six or more words must appear verbatim.

``R4_RAG_VERIFY`` picks what happens with the result:

- ``annotate`` (default): append a footer of at most 400 characters naming the
  sources (from UMRS artifact metadata, never passage or answer text) and the
  check; in streaming mode the footer is a trailing chunk. The answer is never
  rewritten.
- ``strict`` (eval or explicit opt-in, MD-12): as annotate, and an answer whose
  cited sentences are all unsupported has its SUPPORT/CONTRADICT verdict
  downgraded to NOT ENOUGH INFO. A streamed answer is already on screen, so
  there the downgrade is a trailing note instead of a rewrite: what is stored
  is always what was streamed.
- ``off``: nothing.

Lexical support cannot tell SUPPORT from CONTRADICT; it flags answers that
cite passages which do not carry their words, and numbers or quotes the
passages do not contain.
"""

from __future__ import annotations

import bisect
import functools
import logging
import os
import re
from collections import OrderedDict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from r4_contracts.taint import TOOL_TAINT_CLASSES

from .evidence_ledger import LEDGER_TAINT_SOURCE, EvidenceLedger, LedgerEntry, session_ledger

logger = logging.getLogger(__name__)

VERIFY_ENV = "R4_RAG_VERIFY"
VERIFY_MODES: tuple[str, ...] = ("annotate", "strict", "off")
FOOTER_MAX_CHARS = 400  # the whole appended suffix, separator included
FOOTER_RULE = "---"
SUPPORTED_AT = 0.5
WEAK_AT = 0.3
QUOTE_MIN_WORDS = 6
# A cited sentence with fewer content terms than this is a bare verdict
# ("Yes [S1].", a JSON verdict): it is checked with the question's terms.
SHORT_SENTENCE_TERMS = 3
_LIST_SHOWN = 4  # phantom labels / mislabels / numbers named in the footer
_RANGE_MAX = 12
#: A source name cites its passage only when it looks like a file name or an
#: identifier (at least this long; see ``_file_like``): a plain word such as
#: ``Introduction`` or a title such as ``COVID-19`` would match prose (C24).
NAME_MIN_CHARS = 5
#: How far before a name a label is looked for (``[S12] — `` and markdown).
_LABEL_REACH = 48
STRICT_NOTE = "NOT ENOUGH INFO (strict check: no cited sentence is supported by its passage)."
STRICT_STREAM_NOTE = (
    "NOT ENOUGH INFO (strict check: no cited sentence is supported by its passage, "
    "so the verdict above does not stand)."
)
METADATA_KEY = "citation_check"
SOURCE_PREFIX = "Source: "  # a phantom (Source: name) citation, as find_citations keys it
#: The session attribute listing the tools this turn called (CompanionSession).
TURN_TOOLS_ATTR = "_turn_tool_names"
#: Tools that read documents: their results number their own hits
#: (``search_docs``'s ``[1] …``) or carry the server's ``[n]`` citations
#: (``rag_query``). On a turn that called one, a bare ``[n]`` is ambiguous.
NUMBERED_TOOLS = frozenset(
    name
    for name, taint_class in TOOL_TAINT_CLASSES.items()
    if taint_class == "rag_chunk" and name != LEDGER_TAINT_SOURCE
)

_STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    few for from further had has have having he her here hers herself him himself his how
    i if in into is it its itself just me more most my myself no nor not now of off on
    once only or other our ours ourselves out over own same she should so some such than
    that the their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves may might must shall however thus therefore
    whether within without upon via per et al eg ie yes
    source sources passage passages cite cited citing according answer question verdict
    evidence support supports supported supporting contradict contradicts contradicted
    claim claims enough info information knowledge base document documents
    """.split()
)
_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*")
# Kana, CJK ideographs, hangul: their terms are bigrams, and such a character
# bounds a Latin token written against it (Japanese puts no space there).
_CJK = "぀-ヿ㐀-䶿一-鿿가-힯"
_CJK_RE = re.compile(f"[{_CJK}]")
_LATIN_TOKEN = rf"[^\W{_CJK}]"  # a word character that is not CJK
_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("ies", "y"),
    ("ing", ""),
    ("ed", ""),
    ("es", ""),
    ("s", ""),
    ("ly", ""),
)
_NO_PLURAL_S = ("ss", "us", "is")

# Labels are session-monotonic, so any length (the pack sanitiser matches).
_ITEM = r"S?\s*\d+(?:\s*[-–]\s*S?\s*\d+)?"
_LIST = rf"{_ITEM}(?:\s*[,;]\s*{_ITEM})*"
_BRACKET_RE = re.compile(rf"(?<!{_LATIN_TOKEN})\[\s*({_LIST})\s*\]", re.IGNORECASE)
_PAREN_RE = re.compile(
    rf"\(\s*(S\s*\d+(?:\s*[-–]\s*S?\s*\d+)?(?:\s*[,;]\s*{_ITEM})*)\s*\)",
    re.IGNORECASE,
)
_BARE_RE = re.compile(r"(?<![\w\[(/.-])S(\d+)(?![\w\])])")
_SOURCE_RE = re.compile(r"\(\s*sources?\s*:\s*([^()\n]{1,160})\)", re.IGNORECASE)
_S_LABEL_RE = re.compile(r"\[\s*S\s*\d+", re.IGNORECASE)
# Source names (C24). A name is one token: never inside ``xname.md``,
# ``name.mdx`` or ``dir/name.md`` (a closing full stop is no part of it); a
# hyphen after a label (``[S1]-name``) is no part either. A Latin edge is
# bounded by a Latin token character only, a CJK edge by any word character.
_EXTENSION_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
_IDENTIFIER_RE = re.compile(r"[_/]|\d{4}")
_BEFORE_LATIN = re.compile(rf"(?<!{_LATIN_TOKEN}|[./\\])(?<!(?<!\])-)")
_BEFORE_CJK = re.compile(r"(?<!\w|[./\\])(?<!(?<!\])-)")
_AFTER_LATIN = rf"(?!{_LATIN_TOKEN}|[/\\-]|\.(?:{_LATIN_TOKEN}|-))"
_AFTER_CJK = r"(?!\w|[/\\-]|\.[\w-])"
# An S-label before a name, as the pack header shows them (``[S1] x.md``).
# Directly before: spaces, markdown around the name (``[S1] `x.md` ``,
# ``[S1](x.md)``) or an unspaced ``-``/``：``. A spaced colon or dash binds
# them only where the label opens its segment (a line, a list item, a JSON
# string); mid-clause, ``20% [S1] — x.md, by contrast, …`` is two clauses.
_ONE_LABEL = r"\[\s*S\s*(\d+)\s*\]"
_NAME_WRAP = '[`*("“]{0,3}'
_SEGMENT_START = r"(?:^|(?<=[.!?。！？\"'“‘\[{(,;:|、，：]))"
_LABEL_BEFORE_RE = re.compile(
    rf"(?:{_SEGMENT_START}\s*{_ONE_LABEL}\s*[:：–—-]\s*|{_ONE_LABEL}(?:[-–:：]|\s*))"
    rf"{_NAME_WRAP}$",
    re.IGNORECASE,
)
_LEAD_LABEL_RE = re.compile(rf"\s*{_ONE_LABEL}\s*(?:[:：–—-]\s*)?{_NAME_WRAP}", re.IGNORECASE)
_SOURCES_LEAD_RE = re.compile(r"^\s*sources?\s*[:：]", re.IGNORECASE)
_HAS_WORD_RE = re.compile(r"[^\W_]")

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_BULLET_RE = re.compile(r"^\s*(?:[-*•+]|\d{1,2}[.)]|#{1,6})\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?。！？])\s+")
_LEAD_CITES_RE = re.compile(
    r"^(?:\s*(?:\[[^\]\n]{1,40}\]|\(\s*S\d[^)\n]{0,40}\)|\(\s*sources?\s*:[^)\n]{1,160}\)))+",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_QUOTE_RE = re.compile(r"\"([^\"\n]{8,600})\"|“([^”\n]{8,600})”")
_JSON_VERDICT_RE = re.compile(r"(\"verdict\"\s*:\s*\")(SUPPORT|CONTRADICT)[A-Z_]*(\")", re.I)
_VERDICT_WORD_RE = re.compile(
    r"\b(?:support(?:s|ed)?|contradict(?:s|ed)?|refute(?:s|d)?)\b", re.IGNORECASE
)
_ABSTAINED_RE = re.compile(r"NOT IN KNOWLEDGE BASE|NOT[ _]ENOUGH[ _]INFO", re.IGNORECASE)


def verify_mode() -> str:
    """``R4_RAG_VERIFY``: annotate (default), strict or off."""
    raw = os.environ.get(VERIFY_ENV, "").strip().lower()
    return raw if raw in VERIFY_MODES else "annotate"


# ---------------------------------------------------------------- terms ---


def _stem(word: str) -> str:
    """A light, deterministic suffix strip so plural and tense agree."""
    for suffix, replacement in _SUFFIXES:
        if not word.endswith(suffix) or len(word) - len(suffix) < 3:
            continue
        if suffix == "s" and word.endswith(_NO_PLURAL_S):
            break
        word = word[: -len(suffix)] + replacement
        break
    return word[:-1] if len(word) > 3 and word.endswith("e") else word


#: The light stem, public for the router's query focus (ADR-093 wave H),
#: which reads framing words the way this check reads terms.
stem = _stem


def _cjk_bigrams(word: str) -> list[str]:
    chars = [ch for ch in word if _CJK_RE.match(ch)]
    if len(chars) < 2:
        return chars
    return [a + b for a, b in zip(chars, chars[1:], strict=False)]


def content_words(text: str) -> list[str]:
    """Lower-cased content words of ``text``, unstemmed, in order.

    Stopwords, citation vocabulary and bare numbers are out (numbers have
    their own rule); a CJK run contributes its character bigrams.
    """
    words: list[str] = []
    for word in _WORD_RE.findall(str(text or "").casefold()):
        if _CJK_RE.search(word):
            words.extend(_cjk_bigrams(word))
        elif len(word) > 1 and not word.isdigit() and word not in _STOPWORDS:
            words.append(word)
    return words


def content_terms(text: str) -> list[str]:
    """``content_words`` lightly stemmed, so plural and tense agree."""
    return [word if _CJK_RE.search(word) else _stem(word) for word in content_words(text)]


# ------------------------------------------------------------ citations ---


def _labels_in(group: str) -> list[int]:
    """``"S1, S3-S5"`` → ``[1, 3, 4, 5]``."""
    numbers: list[int] = []
    for item in re.split(r"[,;]", group):
        bounds = [int(n) for n in re.findall(r"\d+", item)]
        if len(bounds) == 2 and 0 < bounds[1] - bounds[0] < _RANGE_MAX:
            numbers.extend(range(bounds[0], bounds[1] + 1))
        else:
            numbers.extend(bounds[:1])
    return numbers


def _resolve_bare(number: int, shown: Sequence[str]) -> str | None:
    """A bare ``[1]`` is S1 when this turn showed S1; in a later turn (shown
    S4-S6) it is the turn's first passage, S4. A number that names no passage
    this turn showed (0, one past the pack) is not a passage citation."""
    sid = f"S{number}"
    if sid in shown:
        return sid
    return shown[number - 1] if 0 < number <= len(shown) else None


def _bracket_sids(group: str, shown: Sequence[str]) -> list[str]:
    """``[S4]`` is S4 (a phantom when the ledger lacks it). Unprefixed
    numbers count only when every one names a passage this turn showed:
    ``[0, 1]``, ``[10, 20]`` and a reference ``[12]`` are not citations."""
    numbers = _labels_in(group)
    if "s" in group.casefold():
        return [f"S{n}" for n in numbers]
    resolved = [_resolve_bare(n, shown) for n in numbers]
    if not resolved or None in resolved:
        return []
    return [sid for sid in resolved if sid is not None]


def _labelled(sentence: str, shown: Sequence[str]) -> list[str]:
    """Every S-label a bracketed or parenthesised citation names."""
    sids: list[str] = []
    for pattern in (_BRACKET_RE, _PAREN_RE):
        for match in pattern.finditer(sentence):
            sids.extend(_bracket_sids(match.group(1), shown))
    return sids


class _NameHit(NamedTuple):
    """A shown passage's source name, found in a sentence (C24)."""

    start: int
    end: int
    name: str
    sids: tuple[str, ...]  # every passage this turn showed under that name


class _Found(NamedTuple):
    """Everything one sentence cites."""

    cited: list[str]
    phantom: list[str]
    mislabels: list[tuple[str, str]]  # (label, named)
    names: list[tuple[int, int]]  # spans of the matched source names


def _file_like(name: str) -> bool:
    """A file name or an identifier, never a prose term: an extension
    (``trial.pdf``, ``Smith J. 2019 trial.pdf``), or one token with an
    underscore, a ``/`` or a run of four digits (``notes_v2``,
    ``scifact-14920021``). ``COVID-19``, ``Phase 3`` and ``Smith 2021``,
    titles UMRS may show as names, are prose."""
    if len(name) < NAME_MIN_CHARS:
        return False
    if _EXTENSION_RE.search(name):
        return True
    return len(name.split()) == 1 and _IDENTIFIER_RE.search(name) is not None


def _shown_names(ledger: EvidenceLedger, shown: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """``{source name: labels}`` of this turn's passages whose names look like
    files. Two passages of one document share a name, and it cites both."""
    names: dict[str, list[str]] = {}
    for entry in (ledger.get(sid) for sid in dict.fromkeys(shown)):
        if entry is not None and _file_like(entry.name):
            names.setdefault(entry.name, []).append(entry.sid)
    return {name: tuple(sids) for name, sids in names.items()}


@functools.lru_cache(maxsize=512)
def _name_pattern(name: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """``(the name with its end bound, its start bound)``: the literal leads,
    so the search is a fast scan; each edge is bounded by its own script."""
    after = _AFTER_CJK if _CJK_RE.match(name[-1]) else _AFTER_LATIN
    before = _BEFORE_CJK if _CJK_RE.match(name[0]) else _BEFORE_LATIN
    return re.compile(re.escape(name) + after), before


def _name_hits(sentence: str, names: Mapping[str, tuple[str, ...]]) -> list[_NameHit]:
    """Exact, case-sensitive, token-bounded occurrences of ``names``, in
    order; where two overlap the longer name wins. Linear in the sentence:
    a mask of the characters taken, not a scan of the earlier hits."""
    hits: list[_NameHit] = []
    taken = bytearray(len(sentence))
    for name in sorted(names, key=len, reverse=True):
        pattern, before = _name_pattern(name)
        for match in pattern.finditer(sentence):
            start, end = match.span()
            if before.match(sentence, start) and taken.find(1, start, end) < 0:
                taken[start:end] = b"\x01" * (end - start)
                hits.append(_NameHit(start, end, name, names[name]))
    return sorted(hits)


def _agrees(sid: str, hit: _NameHit, ledger: EvidenceLedger) -> bool:
    """The label is the name's own, or an earlier turn's label for the same
    document (labels are session-monotonic: a follow-up shows it anew)."""
    if sid in hit.sids:
        return True
    entry = ledger.get(sid)
    return entry is not None and entry.name == hit.name


def _mislabels(
    sentence: str, hits: Sequence[_NameHit], ledger: EvidenceLedger
) -> tuple[list[tuple[str, str]], list[tuple[int, int]]]:
    """``(label, named)`` pairs for a label written directly before another
    passage's name, and those labels' spans: the label cites the name's
    passage, not its own. The label is looked for just before the name."""
    pairs: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []
    for hit in hits:
        label = _LABEL_BEFORE_RE.search(sentence, max(0, hit.start - _LABEL_REACH), hit.start)
        if label is None:
            continue
        sid = f"S{int(label.group(1) or label.group(2))}"
        if not _agrees(sid, hit, ledger):
            pairs.extend((sid, named) for named in hit.sids)
            spans.append(label.span())
    return pairs, spans


def _blank(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """``text`` with each span replaced by a space, in one pass."""
    parts: list[str] = []
    done = 0
    for start, end in sorted(spans):
        start = max(start, done)
        if end > start:
            parts += [text[done:start], " "]
            done = end
    parts.append(text[done:])
    return "".join(parts)


def _label_citations(
    text: str, ledger: EvidenceLedger, shown: Sequence[str]
) -> tuple[list[str], list[str]]:
    """``(cited, phantom)`` of the bracketed, parenthesised and bare labels."""
    cited: list[str] = []
    phantom: list[str] = []
    for sid in _labelled(text, shown):
        (cited if ledger.get(sid) is not None else phantom).append(sid)
    for match in _BARE_RE.finditer(text):
        sid = f"S{match.group(1)}"
        if ledger.get(sid) is not None:
            cited.append(sid)
    return cited, phantom


def _source_citations(sentence: str, ledger: EvidenceLedger) -> tuple[list[str], list[str]]:
    """``(cited, phantom)`` of ``(Source: name)``, looked up across the ledger."""
    cited: list[str] = []
    phantom: list[str] = []
    for match in _SOURCE_RE.finditer(sentence):
        name = " ".join(match.group(1).split())
        entries = ledger.by_name(name)
        cited.extend(entry.sid for entry in entries)
        if not entries:
            phantom.append(f"{SOURCE_PREFIX}{name}")
    return cited, phantom


def _find(
    sentence: str, ledger: EvidenceLedger, shown: Sequence[str], bare_numbers: bool
) -> _Found:
    """Labels are read with the matched names and mislabelled labels blanked
    out, so a label inside a name (``S2-protocol.md``) is no citation."""
    hits = _name_hits(sentence, _shown_names(ledger, shown))
    mislabels, mislabelled = _mislabels(sentence, hits, ledger)
    names = [(hit.start, hit.end) for hit in hits]
    text = _blank(sentence, mislabelled + names)
    cited, phantom = _label_citations(text, ledger, shown if bare_numbers else ())
    by_source, unknown = _source_citations(sentence, ledger)
    cited += by_source + [sid for hit in hits for sid in hit.sids]
    return _Found(
        cited=list(dict.fromkeys(cited)),
        phantom=list(dict.fromkeys(phantom + unknown)),
        mislabels=list(dict.fromkeys(mislabels)),
        names=names,
    )


def find_citations(
    sentence: str,
    ledger: EvidenceLedger,
    shown: Sequence[str] = (),
    *,
    bare_numbers: bool = True,
) -> tuple[list[str], list[str]]:
    """``(cited ledger labels, phantom labels)`` of one sentence, in order.

    ``shown`` is the labels this turn's pack carried: their file-like source
    names are citations, and (unless ``bare_numbers`` is off) so is a bare
    ``[n]`` naming one of them; with none shown, neither is. A bare ``S3``
    counts only when the ledger holds S3: outside brackets it may be
    something else (an S3 bucket), so it is never called a phantom.
    """
    found = _find(sentence, ledger, shown, bare_numbers)
    return found.cited, found.phantom


def _strip_markers(sentence: str) -> str:
    text = sentence
    for pattern in (_SOURCE_RE, _BRACKET_RE, _PAREN_RE):
        text = pattern.sub(" ", text)
    return re.sub(r"(?<![\w.-])S\d+(?!\w)", " ", text)


# ------------------------------------------------------------ sentences ---


def _name_at(text: str, pos: int, name: str) -> bool:
    pattern, before = _name_pattern(name)
    return pattern.match(text, pos) is not None and before.match(text, pos) is not None


def _labels_a_name(piece: str, names: Collection[str]) -> bool:
    """``[S1] scifact-19058822.md: …`` opening a line: the label is the
    name's (the pack header's form, copied into a list)."""
    label = _LEAD_LABEL_RE.match(piece)
    return label is not None and any(_name_at(piece, label.end(), name) for name in names)


def _citation_only(piece: str, names: Mapping[str, tuple[str, ...]]) -> bool:
    """Whether ``piece`` is nothing but citations: labels, shown names and
    ``(Source: …)``, after an optional ``Source(s):``."""
    text = _SOURCES_LEAD_RE.sub(" ", piece, count=1)
    spans = [(hit.start, hit.end) for hit in _name_hits(text, names)]
    rest = _strip_markers(_blank(text, spans))
    return (bool(spans) or rest != text) and _HAS_WORD_RE.search(rest) is None


def _append_sentence(
    sentences: list[str], piece: str, names: Mapping[str, tuple[str, ...]], opens_line: bool
) -> None:
    """Citations that open a piece belong to the sentence before it
    (``"X rose. [S1] Y fell."``), and a piece of nothing but citations
    (``"X rose. [S1] x.md"``, ``Source: x.md`` on the next line) joins it
    whole: alone it has no terms of its own and would pass on the question's.
    A label opening a line before a shown name and its claim stays with it."""
    piece = piece.strip()
    if sentences and _citation_only(piece, names):
        sentences[-1] = f"{sentences[-1]} {piece}"
        return
    lead = _LEAD_CITES_RE.match(piece)
    if lead and sentences and not (opens_line and _labels_a_name(piece, names)):
        sentences[-1] = f"{sentences[-1]} {lead.group(0).strip()}"
        piece = piece[lead.end() :].strip()
    if piece:
        sentences.append(piece)


def _split_line(line: str, names: Mapping[str, tuple[str, ...]]) -> list[str]:
    """``line`` cut at its sentence ends, never inside a shown name
    (``Smith J. 2019 trial.pdf``)."""
    hits = _name_hits(line, names)
    starts = [hit.start for hit in hits]
    pieces: list[str] = []
    done = 0
    for gap in _SENTENCE_END_RE.finditer(line):
        index = bisect.bisect_right(starts, gap.start()) - 1
        if index < 0 or hits[index].end <= gap.start():
            pieces.append(line[done : gap.start()])
            done = gap.end()
    pieces.append(line[done:])
    return pieces


def _name_map(names: Collection[str]) -> dict[str, tuple[str, ...]]:
    """``{file-like name: labels}`` from ``_shown_names`` or a plain list."""
    if isinstance(names, Mapping):
        return {name: tuple(names[name]) for name in names if _file_like(name)}
    return {name: () for name in names if _file_like(name)}


def split_sentences(text: str, names: Collection[str] = ()) -> list[str]:
    """Sentences of an answer: code fences out, list markers stripped.

    ``names`` are the source names this turn showed: a sentence end inside
    one does not split it, and a label opening a line directly before one
    stays with it."""
    shown = _name_map(names)
    sentences: list[str] = []
    for line in _FENCE_RE.sub(" ", str(text or "")).splitlines():
        line = _BULLET_RE.sub("", line).strip()
        for index, piece in enumerate(_split_line(line, shown)):
            _append_sentence(sentences, piece, shown, opens_line=index == 0)
    return sentences


# ---------------------------------------------------------------- check ---


@dataclass(frozen=True)
class SentenceCheck:
    """The check of one cited sentence."""

    text: str
    cited: tuple[str, ...]
    phantom: tuple[str, ...]
    support: float | None
    verdict: str  # supported | weak | unsupported | phantom | unscored
    missing_numbers: tuple[str, ...] = ()
    quote_mismatches: int = 0
    # (label, named): a label written directly before another passage's name
    mislabels: tuple[tuple[str, str], ...] = ()


def _entry_text(entry: LedgerEntry) -> str:
    """What the model was shown for a passage: its header and its text."""
    passage = entry.passage
    return f"{entry.name} {passage.section} {passage.text}"


def _numbers(text: str) -> list[str]:
    return [match.replace(",", "") for match in _NUMBER_RE.findall(str(text or ""))]


def _support(bare: str, evidence: str, question: str) -> float | None:
    terms = set(content_terms(bare))
    if len(terms) < SHORT_SENTENCE_TERMS:
        terms |= set(content_terms(question))
    if not terms:
        return None
    return len(terms & set(content_terms(evidence))) / len(terms)


def _verdict(cited: bool, support: float | None) -> str:
    if not cited:
        return "phantom"
    if support is None:
        return "unscored"
    if support >= SUPPORTED_AT:
        return "supported"
    return "weak" if support >= WEAK_AT else "unsupported"


def _missing_numbers(bare: str, evidence: str, question: str) -> tuple[str, ...]:
    allowed = set(_numbers(evidence)) | set(_numbers(question))
    return tuple(n for n in dict.fromkeys(_numbers(bare)) if n not in allowed)


def _norm_quote(text: str) -> str:
    flat = str(text).casefold().translate(str.maketrans("‘’–—", "''--"))
    return " ".join(flat.split()).strip(" .,;:!?'\"")


def _quote_mismatches(bare: str, evidence: str) -> int:
    haystack = _norm_quote(evidence)
    count = 0
    for match in _QUOTE_RE.finditer(bare):
        span = match.group(1) or match.group(2) or ""
        if len(span.split()) >= QUOTE_MIN_WORDS and _norm_quote(span) not in haystack:
            count += 1
    return count


def check_sentence(
    sentence: str,
    ledger: EvidenceLedger,
    *,
    question: str = "",
    shown: Sequence[str] = (),
    bare_numbers: bool = True,
) -> SentenceCheck | None:
    """The check of one sentence, or ``None`` when it cites nothing.

    Matched source names are stripped with the markers before support,
    numbers and quotes are scored: a name's digits are not a claim."""
    found = _find(sentence, ledger, shown, bare_numbers)
    if not found.cited and not found.phantom:
        return None
    bare = _strip_markers(_blank(sentence, found.names))
    entries = [entry for entry in (ledger.get(sid) for sid in found.cited) if entry is not None]
    evidence = " ".join(_entry_text(entry) for entry in entries)
    support = _support(bare, evidence, question) if found.cited else None
    return SentenceCheck(
        text=sentence,
        cited=tuple(found.cited),
        phantom=tuple(found.phantom),
        support=support,
        verdict=_verdict(bool(found.cited), support),
        missing_numbers=_missing_numbers(bare, evidence, question),
        quote_mismatches=_quote_mismatches(bare, evidence),
        mislabels=tuple(found.mislabels),
    )


@dataclass(frozen=True)
class CitationReport:
    """Every cited sentence of one answer, and the passages its turn showed."""

    sentences: tuple[SentenceCheck, ...] = ()
    shown: tuple[str, ...] = ()
    bare_numbers: bool = True  # were bare [n] read as pack citations

    def count(self, verdict: str) -> int:
        return sum(1 for check in self.sentences if check.verdict == verdict)

    @property
    def cited_sids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(sid for check in self.sentences for sid in check.cited))

    @property
    def phantom(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(sid for check in self.sentences for sid in check.phantom))

    @property
    def mislabels(self) -> tuple[tuple[str, str], ...]:
        """``(label, named)``: a label written against another shown name."""
        return tuple(dict.fromkeys(pair for check in self.sentences for pair in check.mislabels))

    @property
    def missing_numbers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(n for check in self.sentences for n in check.missing_numbers))

    @property
    def quote_mismatches(self) -> int:
        return sum(check.quote_mismatches for check in self.sentences)

    @property
    def answer_supported(self) -> bool:
        """At least one cited sentence carries its passage's words."""
        return any(check.verdict in ("supported", "weak") for check in self.sentences)

    @property
    def flagged(self) -> bool:
        """Anything the footer reports as a problem."""
        problems = self.count("unsupported") + len(self.phantom) + len(self.missing_numbers)
        return bool(problems + len(self.mislabels) + self.quote_mismatches)

    def summary(self) -> dict[str, Any]:
        return {
            "cited": list(self.cited_sids),
            "cited_sentences": len(self.sentences),
            "supported": self.count("supported"),
            "weak": self.count("weak"),
            "unsupported": self.count("unsupported"),
            "phantom": list(self.phantom),
            "mislabels": [list(pair) for pair in self.mislabels],
            "missing_numbers": list(self.missing_numbers),
            "quote_mismatches": self.quote_mismatches,
            "shown": list(self.shown),
            "bare_numbers": self.bare_numbers,
        }


def check_answer(
    answer: str,
    ledger: EvidenceLedger,
    *,
    question: str = "",
    shown: Sequence[str] = (),
    bare_numbers: bool = True,
) -> CitationReport:
    """Check every cited sentence of ``answer`` against ``ledger``.

    ``bare_numbers=False`` (a turn that also read numbered tool hits) reads
    no bare ``[n]``, which may be the tool's first hit; ``[S#]`` labels and
    the shown passages' source names are still read."""
    names = _shown_names(ledger, shown)
    checks = (
        check_sentence(sentence, ledger, question=question, shown=shown, bare_numbers=bare_numbers)
        for sentence in split_sentences(answer, names)
    )
    return CitationReport(
        sentences=tuple(check for check in checks if check is not None),
        shown=tuple(shown),
        bare_numbers=bare_numbers,
    )


# --------------------------------------------------------------- footer ---


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _sources_line(report: CitationReport, ledger: EvidenceLedger) -> str:
    """Cited sources, grouped by their UMRS name; when the answer cites none,
    the passages the turn was shown, as ``Retrieved:`` (never as its sources)."""
    groups: OrderedDict[str, list[str]] = OrderedDict()
    for sid in report.cited_sids or report.shown:
        entry = ledger.get(sid)
        if entry is not None:
            groups.setdefault(entry.name, []).append(f"[{sid}]")
    parts = [f"{', '.join(sids)} {name}" for name, sids in groups.items()]
    lead = "Sources" if report.cited_sids else "Retrieved"
    return f"{lead}: {'; '.join(parts)}" if parts else ""


def _phantom_text(phantom: Sequence[str]) -> str:
    """Phantom labels by label; a phantom ``(Source: …)`` only by count, since
    its name is the model's text and the footer names only trusted metadata
    (D-9)."""
    labels = [f"[{label}]" for label in phantom if not label.startswith(SOURCE_PREFIX)]
    named = len(phantom) - len(labels)
    parts = labels[:_LIST_SHOWN]
    if named:
        parts.append(f"{named} named source{'s' if named > 1 else ''}")
    return ", ".join(parts)


def _mislabel_text(mislabels: Sequence[tuple[str, str]]) -> str:
    """``[S1] for [S2], …``: labels only (normalised ``S<n>``), never names."""
    return ", ".join(f"[{label}] for [{named}]" for label, named in mislabels[:_LIST_SHOWN])


def _problems(report: CitationReport) -> list[str]:
    parts: list[str] = []
    if report.phantom:
        parts.append(f"not among the retrieved passages: {_phantom_text(report.phantom)}")
    if report.mislabels:
        parts.append(f"label mismatches: {_mislabel_text(report.mislabels)}")
    if report.missing_numbers:
        parts.append(
            "numbers not in the cited passage: " + ", ".join(report.missing_numbers[:_LIST_SHOWN])
        )
    if report.quote_mismatches:
        parts.append(f"{report.quote_mismatches} quote(s) not found verbatim")
    return parts


def _check_line(report: CitationReport) -> str:
    total = len(report.sentences)
    if not total:
        return "Check: the answer cites no passage."
    head = f"{report.count('supported')} of {total} cited sentences supported by their passage"
    weak, unsupported = report.count("weak"), report.count("unsupported")
    head += f", {weak} partly" if weak else ""
    head += f", {unsupported} not supported" if unsupported else ""
    return "Check: " + "; ".join([head, *_problems(report)]) + "."


def footer_suffix(report: CitationReport, ledger: EvidenceLedger) -> str:
    """``"\\n\\n---\\nSources: …\\nCheck: …"``, at most 400 characters in all."""
    check = _check_line(report)
    sources = _sources_line(report, ledger)
    head = f"\n\n{FOOTER_RULE}\n"
    room = FOOTER_MAX_CHARS - len(head) - len(check) - 1
    if sources and room >= 24:
        return _clip(f"{head}{_clip(sources, room)}\n{check}", FOOTER_MAX_CHARS)
    return _clip(f"{head}{check}", FOOTER_MAX_CHARS)


def downgrade(answer: str, report: CitationReport) -> str:
    """Strict mode: an unsupported SUPPORT/CONTRADICT becomes NOT ENOUGH INFO."""
    if report.answer_supported:
        return answer
    if _JSON_VERDICT_RE.search(answer):
        return _JSON_VERDICT_RE.sub(r"\1NOT_ENOUGH_INFO\3", answer)
    if _ABSTAINED_RE.search(answer) or not _VERDICT_WORD_RE.search(answer):
        return answer
    return f"{STRICT_NOTE}\n\n{answer}"


# ---------------------------------------------------------- session glue ---


def _checkable(response: str) -> bool:
    from .cancellation import is_stopped_reply

    body = str(response or "").strip()
    return bool(body) and not is_stopped_reply(body) and not body.startswith("Budget limit reached")


def _turn_scope(session: Any, response: str) -> tuple[EvidenceLedger, str, tuple[str, ...]] | None:
    """(ledger, turn id, labels shown this turn) when this turn is checked:
    it was shown passages, or it cites an S-label the ledger may hold."""
    ledger = session_ledger(session)
    if ledger is None or not len(ledger) or not _checkable(response):
        return None
    turn_id = str(getattr(session, "_current_turn_id", "") or "")
    shown = ledger.turn_sids(turn_id)
    if not shown and not _S_LABEL_RE.search(response):
        return None
    return ledger, turn_id, shown


def _emit_chunk(session: Any, text: str) -> None:
    """Stream the footer as the reply's trailing chunk."""
    emit = getattr(getattr(session, "agent", None), "_emit", None)
    if not callable(emit):
        return
    from .agent import CompanionEventType

    emit(CompanionEventType.RESPONSE_CHUNK, text)


def _read_numbered_hits(session: Any) -> bool:
    """Whether this turn called a tool that numbers its own hits."""
    names = getattr(session, TURN_TOOLS_ATTR, None) or ()
    return any(str(name) in NUMBERED_TOOLS for name in names)


def _remember(
    session: Any, turn_id: str, mode: str, report: CitationReport, downgraded: bool
) -> None:
    metadata = getattr(session, "metadata", None)
    if isinstance(metadata, dict):
        metadata[METADATA_KEY] = {
            "turn_id": turn_id,
            "mode": mode,
            "downgraded": downgraded,
            **report.summary(),
        }


_CITATION_PREVIEW_CHARS = 200
#: At most this many retrieved (uncited) passages are listed for a turn.
RETRIEVED_ROWS_MAX = 8


def turn_citations(session: Any, turn_id: str) -> list[dict[str, str]]:
    """The passages turn ``turn_id``'s answer cited, for the TUI's citation
    block: ``[{sid, artifact_id, title, kind, preview}]``.

    An answer that cites none lists the passages the turn was shown, as the
    footer's ``Retrieved:`` line does, with ``kind: "retrieved"`` (never as
    its sources; at most :data:`RETRIEVED_ROWS_MAX`). Empty unless the check
    ran on that turn: it needs passages the turn was shown (the router, or a
    knowledge tool since K1.4) or a cited S-label. The TUI's block was never
    fed (TUI audit r2 #66, 2026-10-17).
    """
    record = _turn_record(session, turn_id)
    ledger = session_ledger(session)
    if record is None or ledger is None:
        return []
    cited = list(record.get("cited") or [])
    kind = "passage" if cited else "retrieved"
    sids = cited or list(record.get("shown") or [])[:RETRIEVED_ROWS_MAX]
    return [_citation_row(entry, kind) for entry in map(ledger.get, sids) if entry]


def _turn_record(session: Any, turn_id: str) -> dict[str, Any] | None:
    """The check's record when it ran on ``turn_id``, else ``None``."""
    metadata = getattr(session, "metadata", None)
    record = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    return record if isinstance(record, dict) and record.get("turn_id") == turn_id else None


def _citation_row(entry: Any, kind: str = "passage") -> dict[str, str]:
    """One row; ``source_id``/``project_id`` let ``citation.open`` fetch the
    passage once the session ledger no longer holds the label (evicted, or
    the companion restarted; review of K1 part A, 2026-10-24)."""
    passage = entry.passage
    return {
        "sid": entry.sid,
        "artifact_id": str(passage.artifact_id or passage.source_id),
        "title": entry.name or entry.sid,
        "kind": kind,
        "preview": " ".join(str(passage.text).split())[:_CITATION_PREVIEW_CHARS],
        "source_id": str(passage.source_id or ""),
        "project_id": str(getattr(entry, "project_id", "") or ""),
    }


def check_session_turn(
    session: Any, user_message: str, response: str, *, stream: bool = False
) -> str:
    """The send flow's hook: the answer with its check footer (or unchanged).

    ``user_message`` is the raw message (numbers the user gave are allowed).
    No model call; a failure here never costs the user the answer. Streamed,
    the reply is what was streamed: the answer as it went out, then the
    trailing chunk (a strict downgrade's note, the footer).
    """
    mode = verify_mode()
    scope = _turn_scope(session, response) if mode != "off" else None
    if scope is None:
        return response
    ledger, turn_id, shown = scope
    bare = not _read_numbered_hits(session)
    try:
        report = check_answer(
            response, ledger, question=user_message, shown=shown, bare_numbers=bare
        )
        body = downgrade(response, report) if mode == "strict" else response
        suffix = footer_suffix(report, ledger)
    except Exception as exc:  # pragma: no cover - defensive: the answer always lands
        logger.warning("citation check skipped: %s", exc)
        return response
    _remember(session, turn_id, mode, report, body != response)
    if not stream:
        return body + suffix
    tail = _streamed_tail(body != response, suffix)
    _emit_chunk(session, tail)
    return response + tail


def _streamed_tail(downgraded: bool, suffix: str) -> str:
    """The trailing chunk: the streamed answer cannot be rewritten, so a strict
    downgrade is said after it, then the footer."""
    return f"\n\n{STRICT_STREAM_NOTE}{suffix}" if downgraded else suffix


__all__ = [
    "FOOTER_MAX_CHARS",
    "NAME_MIN_CHARS",
    "NUMBERED_TOOLS",
    "VERIFY_ENV",
    "VERIFY_MODES",
    "CitationReport",
    "SentenceCheck",
    "check_answer",
    "check_sentence",
    "check_session_turn",
    "content_terms",
    "content_words",
    "downgrade",
    "find_citations",
    "footer_suffix",
    "split_sentences",
    "stem",
    "turn_citations",
    "verify_mode",
]
