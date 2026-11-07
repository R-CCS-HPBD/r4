"""What the pre-retrieval router searches: the question, not its instructions.

ADR-093 D-8, wave H (``focus-v2``). A researcher's message often wraps the
thing to look up in framing: "Using the literature in my project's knowledge
graph, is this claim supported? Reply with one JSON object ...", an output
schema, a few-shot example, a polite preamble, Japanese around an English
claim. Measured 2026-10-11 on SciFact (retrieval only, small profile), the
framing dominates the e5 query embedding: the gold abstract fell from 165 to
100 of 188 packs and only 9 of 30 nonsense probes stayed insufficient.

:func:`focus_query` returns the text the router should search. It is
deterministic (stdlib only, no I/O, no model call) and conservative:

- a message with no framing is returned **unchanged** (``basis="whole"``);
- framing is recognised by structure first: a labelled field (``Claim:``,
  ``## Claim``, ``<claim>``), a quotation, a Latin run inside a Japanese
  framing, the tail after a framing colon; then by subtraction (dropping the
  segments that are framing or output-format requests); then by trimming the
  framing clauses of a one-sentence message;
- focus-v2 adds three structures of the same kind: a quotation the sentence
  reports as said (``my PI says "X"``, ``「X」という指摘を受けました``), a
  plain fence holding one paragraph of prose the message names (``the claim
  in the code block``), and a colon after a personal lead that asks the
  reader for something and points ahead to what follows (``..., so give me
  the evidence: X``); and subtraction never drops an impersonal statement;
- structural extraction is gated: what is left behind must be more framing
  than what is kept, with at least two framing units, so ``In mice: ...`` and
  a quoted term inside a plain question stay whole;
- any doubt, cap hit or failure gives the whole message (today's behaviour),
  and the module is off unless ``R4_RAG_FOCUS=on``.

It ranks spans of the user's own text, never passages (CONSTITUTION §1.3):
there is no sort, no ``max``/``min`` with a key, no filter, and no score.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from .citation_check import stem

FOCUS_RULES_VERSION = "focus-v2"
FOCUS_ENV = "R4_RAG_FOCUS"  # "on" enables; unset, or anything else, is off
MARKED_BASES = frozenset({"field", "quote", "script", "colon"})

FOCUS_MAX_CHARS = 4000
MAX_MARKS = 16
MAX_SEGMENTS = 40
MAX_FIELDS = 3
FRAME_RATIO = 0.5
FORMAT_RATIO = 1 / 3
GATE_FLOOR = 0.25
GATE_MIN_FRAME = 2
MIN_WORDS = 3  # content tokens (C12: "fewer than 3 content tokens")
LEAD_MAX_CHARS = 300  # a framing lead before a colon is one short clause

WHOLE = "whole"
FIELD = "field"
QUOTE = "quote"
SCRIPT = "script"
COLON = "colon"
SEGMENTS = "segments"
CLAUSE = "clause"

# Unit kinds.
_FORMAT = "format"
_FRAME = "frame"
_THIN = "thin"
_CONTENT = "content"
_FRAMING = (_FRAME, _FORMAT)

# Label roles.
_DROP = "drop"
_SLOT = "slot"  # answer/verdict with an empty or placeholder value
_SOFT = "soft"
_PLAIN = "plain"  # any other label: its value may be the content

# ------------------------------------------------------------ primitives ---

#: C12's "content tokens" for the skip rule: plain English function words
#: only. The citation check's stop list also drops its own vocabulary
#: (evidence, claim, document, source, knowledge...) and bare numbers, which
#: are what knowledge-base questions are made of (fix-regressions-2).
_FUNCTION_WORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    few for from further had has have having he her here hers herself him himself his how
    i if in into is it its itself just me more most my myself no nor not now of off on
    once only or other our ours ourselves out over own same she should so some such than
    that the their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves may might must shall however thus therefore
    whether within without upon via per hi hello hey please
    """.split()
)
#: A token: letters and digits, joined by ``-``, ``'`` or ``/`` (``ZSM-5``,
#: ``mmol/g`` stay whole; ``2.1`` is two number tokens, as ``session.py`` is
#: two words).
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-'/][^\W_]+)*")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_WORDS_RE = re.compile(r"[^\W_]+")
_ANAPHORS = frozenset(
    """
    it its this that these those they them their theirs he she him his her former
    latter same such there above aforementioned previous earlier
    """.split()
)
_ANAPHORS_CJK = ("それ", "これ", "あれ", "その", "この", "あの", "前述", "上記")
_QUESTION_OPENERS = frozenset(
    """
    what why how when where which who whom whose is are was were does do did can could
    should would will has have had may might explain describe compare summarize
    summarise list define tell find show
    """.split()
)


def content_tokens(text: str) -> list[str]:
    """The message's content tokens, as the skip rule counts them (C12): every
    token that is not an English function word, numbers and identifiers
    included; a CJK run counts its character bigrams."""
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(str(text or "").casefold()):
        if _CJK_RE.search(token):
            tokens.extend(token[i : i + 2] for i in range(max(1, len(token) - 1)))
        elif token not in _FUNCTION_WORDS and (len(token) > 1 or token.isdigit()):
            tokens.append(token)
    return tokens


def is_question_like(text: str) -> bool:
    """A question mark, a question opener, or a Japanese question ending."""
    body = str(text or "").strip()
    if "?" in body or "？" in body or body.endswith("か"):
        return True
    words = _WORDS_RE.findall(body.casefold())
    return bool(words) and words[0] in _QUESTION_OPENERS


def has_anaphor(text: str) -> bool:
    """A pronoun or deictic that points at something outside the text."""
    words = set(_WORDS_RE.findall(text.casefold()))
    return bool(words & _ANAPHORS) or any(mark in text for mark in _ANAPHORS_CJK)


# -------------------------------------------------------------- lexicons ---

#: Framing vocabulary, by role, in declaration order (``rules_digest``). A
#: Latin content token is a framing unit when it (less a possessive ``'s``)
#: or its light stem matches the stem of an entry.
META_EN: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "discourse",
        tuple(
            """
            i'm you're let's what's thanks thank thx kindly hope wonder wondering curious
            quick help assistant appreciate cheers sorry dear
            """.split()
        ),
    ),
    (
        "task",
        tuple(
            """
            check verify fact-check tell say determine decide assess evaluate judge answer
            reply respond return output give provide cite consult use using based according
            regarding confirm corroborate
            """.split()
        ),
    ),
    (
        "source",
        tuple(
            """
            literature paper papers article articles document documents docs corpus library
            collection knowledge graph kg kb base database project sources references files
            notes abstract abstracts pdf pdfs publication publications uploaded attached
            indexed
            """.split()
        ),
    ),
    (
        "object",
        tuple(
            """
            claim statement hypothesis assertion question query sentence text passage
            following given idea notion
            """.split()
        ),
    ),
    (
        "verdict",
        tuple(
            """
            support supported supports contradict contradicted contradicts refute refuted
            true false correct accurate valid hold holds consistent enough information
            insufficient sufficient verdict label yes unsure unknown nei backed evidence
            """.split()
        ),
    ),
    (
        "shape",
        tuple(
            """
            json yaml csv markdown table bullet bullets format object schema key keys field
            fields word words line lines paragraph brief concise short nothing else exactly
            english japanese one single
            """.split()
        ),
    ),
    ("persona", tuple("expert reviewer pretend act role".split())),
    ("adjectives", tuple("scientific research academic relevant solely".split())),
)
#: Japanese framing vocabulary, longest first (an alternation takes the first
#: entry that matches, so a longer word must come before its prefix).
META_JA: tuple[str, ...] = (
    "データベース",
    "コレクション",
    "プロジェクト",
    "エビデンス",
    "ナレッジ",
    "箇条書",
    "日本語",
    "科学的",
    "あなた",
    "グラフ",
    "我々",
    "知識",
    "文献",
    "論文",
    "資料",
    "文書",
    "出典",
    "引用",
    "根拠",
    "証拠",
    "裏付",
    "主張",
    "質問",
    "仮説",
    "命題",
    "以下",
    "上記",
    "下記",
    "支持",
    "反証",
    "矛盾",
    "否定",
    "判定",
    "判断",
    "評価",
    "確認",
    "検証",
    "回答",
    "返答",
    "出力",
    "形式",
    "書式",
    "簡潔",
    "説明",
    "英語",
    "十分",
    "情報",
    "正し",
    "妥当",
    "基づ",
    "参照",
    "下さ",
    "私",
    "次",
    "答",
    "願",
    "教",
)
#: Labels whose field is dropped with its section (output formats, rules,
#: few-shot examples), matched by the whole label or its last word.
DROP_LABELS: tuple[str, ...] = tuple(
    """
    format output instructions instruction rules rule constraints schema template example
    examples sample system expected 出力 出力形式 形式 フォーマット 回答形式 指示 ルール 制約 例
    出力例 入力例 条件
    """.split()
)
#: Answer slots: a field with one of these labels and an empty or
#: placeholder value is part of the format, not the content.
SLOT_LABELS: tuple[str, ...] = ("answer", "verdict", "response", "reply", "rationale")
#: Context labels: their value joins the query only beside another field.
SOFT_LABELS: tuple[str, ...] = tuple(
    "note notes context background abstract 背景 前提 注意".split()
)
#: Nouns that name the user's knowledge container; a quoted name right after
#: one ("knowledge graph 'scifact'") is framing.
CORPUS_NOUNS: tuple[str, ...] = tuple(
    "graph kg kb base project collection corpus library folder database".split()
)
CORPUS_NOUNS_JA: tuple[str, ...] = ("プロジェクト", "グラフ", "コレクション", "データベース")
#: Reported speech (focus-v2): a quotation framed as what someone says,
#: claims or wrote is the thing to check, however much the sentence around
#: it says about who said it. English reporting verbs (before the quote:
#: ``my PI says "X"``; after it: ``"X" is what my PI claims``) and, after a
#: Japanese quote, the quotative と with a reporting verb (``「X」と言われた``)
#: or a noun of saying (``「X」という指摘``); a naming noun (``「X」という材料``,
#: ``「X」という用語``) is no report. The words are the closed classes of
#: verbs and nouns of saying, not any template's wording; the sentence
#: around the quote must still be light (REPORTER_MAX, COMMENT_MAX), so a
#: quoted claim followed by a question of its own stays whole. Longest first.
REPORT_VERBS_EN: tuple[str, ...] = tuple(
    """
    says said say claims claimed claim insists insisted insist argues argued argue wrote
    writes write states stated state suggests suggested suggest believes believe thinks
    think maintains maintain asserts asserted assert reported reports
    """.split()
)
PROPOSITION_NOUNS_JA: tuple[str, ...] = (
    "コメント",
    "指摘",
    "主張",
    "報告",
    "意見",
    "仮説",
    "結論",
    "記述",
    "発言",
    "見解",
    "考え",
    "こと",
    "話",
    "説",
)
#: After the quotative と. Not ``いっ``: ``「X」といった`` is mostly "such as
#: X" (``「A や B」といった材料``), a list, not a report; ``といった`` with a
#: noun of saying is still a report through PROPOSITION_NOUNS_JA.
REPORT_VERBS_JA: tuple[str, ...] = (
    "おっしゃ",
    "いわ",
    "述べ",
    "主張",
    "指摘",
    "報告",
    "考え",
    "結論",
    "記載",
    "言",
    "書",
    "聞",
    "思",
)
TYPE_NAMES: tuple[str, ...] = tuple(
    "string number bool boolean int float list array object null".split()
)
TEMPLATE_INFOS: tuple[str, ...] = ("", "json", "jsonc", "yaml", "yml")
#: Info strings of a fence that may hold quoted prose (focus-v2): none, or
#: plain text. A fence of prose is a quotation only when the message names
#: an object (``the claim in the code block``), and only one short paragraph.
PROSE_INFOS: tuple[str, ...] = ("", "text", "txt", "plain", "plaintext")
PROSE_MAX_CHARS = 600
#: What a message calls a proposition it quotes or presents. It names one
#: only as the head of its noun phrase (``the claim in the code block``,
#: ``this statement:``), never as a modifier (``the claim file``).
QUOTED_OBJECTS: tuple[str, ...] = tuple(
    "claim statement hypothesis assertion sentence passage quotation quote finding".split()
)
#: Prepositions and place adverbs the function-word list lacks: after one
#: the object noun still heads its phrase (``the statement inside the
#: block``, ``the claim here``). A closed class, like the function words.
PREPOSITIONS: tuple[str, ...] = tuple(
    """
    inside outside beneath beside besides near among across along around behind beyond
    toward towards onto like here
    """.split()
)
PROSE_MIN_WORDS = 4
PROSE_WORD_SHARE = 0.8
#: First- and second-person words: a paragraph with one speaks to or for
#: someone (discourse). A paragraph set apart as the content may hold "I"
#: or "US" (complex I, type-I, the US health care system) but none of the
#: unambiguous ones (ADDRESS_WORDS).
PERSONAL_WORDS: tuple[str, ...] = tuple("i me my we our us you your yours".split())
ADDRESS_WORDS: tuple[str, ...] = tuple("me my we our you your yours".split())
#: A personal lead that points ahead to what follows its colon (focus-v2):
#: ``... whether our collection supports it: X`` (cataphora). Only as a
#: pronoun: ``it``, or another one standing alone (followed by a function
#: word or nothing: ``put this in my proposal``), not a determiner before a
#: noun (``this alloy``, ``the following protocol``).
FORWARD_REFS: tuple[str, ...] = ("it", "this", "these", "following", "below")
#: A lead that presents a claim asks the reader for something (a request
#: is addressed: second person, an object "me", or a politeness marker).
REQUEST_WORDS: tuple[str, ...] = ("you", "your", "me", "please", "kindly")
#: Words that make a sentence lean on what came before when they open it
#: (anaphora): such a tail is not a claim that stands on its own.
BACK_REFS: tuple[str, ...] = tuple("this these those they them their its he she his her".split())
ABBREVIATIONS: tuple[str, ...] = tuple("al e.g i.e vs fig eq ref cf approx ca no dr".split())

_META_WORDS = frozenset(word for _, words in META_EN for word in words)
_META_STEMS = frozenset(stem(word) for word in _META_WORDS)
_OBJECT_STEMS = frozenset(stem(word) for word in QUOTED_OBJECTS)
#: Framing that is about a request (what to do, with what, in what shape),
#: not about the writer: META_EN less its discourse, persona and adjectives.
REQUEST_ROLES: tuple[str, ...] = ("task", "source", "object", "verdict", "shape")
_REQUEST_FRAME_STEMS = frozenset(
    stem(word) for role, words in META_EN if role in REQUEST_ROLES for word in words
)
_TYPE_SET = frozenset(TYPE_NAMES)
_PRONOUNS = frozenset(PERSONAL_WORDS)
_ADDRESS = frozenset(ADDRESS_WORDS)
_DROP_SET = frozenset(DROP_LABELS)
_SLOT_SET = frozenset(SLOT_LABELS)
_SOFT_SET = frozenset(SOFT_LABELS)
_ABBREVIATION_SET = frozenset(ABBREVIATIONS)
_FORWARD_SET = frozenset(FORWARD_REFS)
_POSTNOMINAL = _FUNCTION_WORDS | frozenset(PREPOSITIONS)
_BACK_SET = frozenset(BACK_REFS)
_REQUEST_SET = frozenset(REQUEST_WORDS)

# --------------------------------------------------------------- patterns ---

_CJK_CLASS = "぀-ヿ㐀-䶿一-鿿"
_HIRAGANA_RE = re.compile("[぀-ゟ]")
_META_JA_RE = re.compile("|".join(re.escape(word) for word in META_JA))
_POSSESSIVE_RE = re.compile(r"['’]s$")
_FORMAT_OBJECT = (
    r"\b(?:json|yaml|csv|markdown|table|bullets?|list|object|format|only|nothing else"
    r"|one (?:word|sentence|line|paragraph)|\d+ (?:words?|sentences?|lines?|bullets?))\b"
)
_FORMAT_REQUEST = (
    r"\b(?:reply|respond|answer|return|output|format|write|give|provide)\b[^.?!\n]{0,60}"
    + _FORMAT_OBJECT
)
_FORMAT_PLAIN = (
    r"[{\[]\s*\"[^\"]{1,40}\"\s*[:,\]]"
    r"|\b(?:json|yaml|csv)\b"
    r"|(?:JSON|YAML|CSV|箇条書き|形式|フォーマット|のみ|だけ)[^。\n]{0,12}(?:回答|答え|出力|返)"
)
#: An output-format request: a JSON key, "reply ... in JSON / one word / 3
#: bullets", a format name, or the Japanese "JSONで回答". Matched as two
#: halves (:func:`_asks_format`) so a long run of verbs with no format word
#: costs one linear scan.
FORMAT_RE = re.compile(f"{_FORMAT_PLAIN}|{_FORMAT_REQUEST}", re.IGNORECASE)
_FORMAT_PLAIN_RE = re.compile(_FORMAT_PLAIN, re.IGNORECASE)
_FORMAT_OBJECT_RE = re.compile(_FORMAT_OBJECT, re.IGNORECASE)
_FORMAT_REQUEST_RE = re.compile(_FORMAT_REQUEST, re.IGNORECASE)
_FENCE_LINE_RE = re.compile(r"^[ \t]*(```|~~~)[ \t]*([^\n]*)$", re.MULTILINE)
_CODE_MARKER_RE = re.compile(
    r"\bdef |\bimport |\bclass |function|=>|;[ \t]*$|print\(|#include|\breturn\b",
    re.MULTILINE,
)
_PAIR_RE = re.compile(
    r"(?:\"[^\"\n]{1,40}\"|'[^'\n]{1,40}'|\b[A-Za-z_][\w-]{0,39})[ \t]*:[ \t]*([^,\n}]*)"
)
_ANGLE_RE = re.compile(r"<[^<>\n]{0,40}>")
#: Not prose: a code character, an identifier with ``_``, a method or empty
#: call, a line opening with a code keyword, a shell or REPL prompt, an
#: indented line, or a ``label:`` line (a log or a record). Scientific words
#: (``function``, ``class``, ``return``) inside a sentence are prose.
_NOT_PROSE_RE = re.compile(
    r"[{}\[\]<>=;\\|#@~^*`]|\w_\w|\w\(\)|\w\.[^\W\d_]\w*\("
    r"|^[ \t]*(?:def|import|from|class|return|function|var|let|const|print)\b[ \t(]"
    r"|^[ \t]*(?:\$|%|>>>)[ \t]|^[ \t]{2,}\S|^[ \t]*[^\W\d_][\w -]{0,24}:[ \t]",
    re.MULTILINE,
)
_PROSE_WORD_RE = re.compile(r"\(?[^\W_]+(?:[-'’/][^\W_]+)*[.,:!?)%]*")
_SCHEMA_RE = re.compile(r"\{[^{}]{0,400}\}|\[[^\[\]]{0,200}\]")
_QUOTED_KEY_RE = re.compile(r"[\"'][^\"'\n]{1,40}[\"'][ \t]*:")
_PLACEHOLDER_IN_RE = re.compile(r"\.\.\.|…|\||<[^<>\n]{1,40}>")
_CONTAINER_RE = re.compile(
    rf"\b(?:{'|'.join(CORPUS_NOUNS)})(?:\s+(?:named|called))?\s+"
    r"(?P<name>'[^'\n]{1,58}'|\"[^\"\n]{1,58}\"|“[^”\n]{1,58}”|‘[^’\n]{1,58}’|`[^`\n]{1,58}`)",
    re.IGNORECASE,
)
_CONTAINER_JA_RE = re.compile(rf"(?:{'|'.join(CORPUS_NOUNS_JA)})(?P<name>「[^」\n]{{1,58}}」)")
_SEGMENT_MARK_RE = re.compile(r"\n|[?!。！？]|\.(?=\s)")
_AFTER_PERIOD_RE = re.compile(r"\s+(\S)")
_WORD_BEFORE_RE = re.compile(r"[\w.]*$")
_OPENERS = "\"'“‘「『«([{（【"
_LABEL_RE = re.compile(
    r"^\s*(?:[-*>]+\s*|#{1,6}\s*)?(?:\*\*|__)?"
    rf"(?P<label>[^\W\d_][\w '’/()\-]{{0,27}}?|[{_CJK_CLASS}]{{1,8}})"
    r"(?:\*\*|__)?\s*(?P<sep>:(?=\s|$|\*\*|__)|：|->|=>|→)(?:\*\*|__)?\s*(?P<value>.*)$"
)
# The tail's whitespace runs never overlap (each optional part needs its own
# mark), so a failed match backtracks linearly; ``_line_mark`` also tries it
# only on short lines (2026-10-11, wave H review: ``\s*#*\s*[:：]?\s*$`` was
# cubic on a long space run, 9 s on a 370-character message).
_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*(?P<md>[^#\n]{1,40}?)(?:[ \t]*#+)?"
    r"|(?:\*\*|__)(?P<bold>[^*_\n]{1,40}?)(?:\*\*|__))"
    r"(?:[ \t]*[:：])?[ \t]*$"
)
HEADING_MAX_CHARS = 60
_TAG_OPEN_RE = re.compile(r"<(?P<label>[A-Za-z][\w-]{0,19})>")
_QUOTE_RE = re.compile(
    r"“(?P<a>[^“”\n]{1,600})”"
    r"|(?<!\w)\"(?P<b>[^\"\n]{1,600})\"(?!\w)"
    r"|「(?P<c>[^「」\n]{1,600})」"
    r"|『(?P<d>[^『』\n]{1,600})』"
    r"|«(?P<e>[^«»\n]{1,600})»"
    r"|(?<![\w'])'(?P<f>[^'\n]{12,600})'(?!\w)"
)
_BLOCKQUOTE_RE = re.compile(r"(?:^[ \t]*>[^\n]*(?:\n|$))+", re.MULTILINE)
_QUOTE_MARK_RE = re.compile(r"[ \t]*>[ \t]?")
_QUOTED_LINE_RE = re.compile(r"^[ \t]*>[ \t]?([^\n]*)$", re.MULTILINE)
_LATIN_RUN_RE = re.compile(rf"[^{_CJK_CLASS}가-힯　-〿＀-￯]+")
_COLON_RE = re.compile(r":(?=\s)|：")
_PARAGRAPH_END_RE = re.compile(r"\n[ \t]*\n")
_PAREN_RE = re.compile(r"\(([^()\n]{0,80})\)|（([^（）\n]{0,80})）")
_LEAD_CLAUSE_RE = re.compile(r"[,;、，；]|\s(?:—|–|--|-|and|but|so|because)\s", re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(r"[,;、，；]| — | – | -- | - ")
REPORTED_RE = re.compile(
    r"^(?:(?:my|our|a|the)\s+)?(?:pi|supervisor|advis[eo]r|colleague|collaborator|reviewer"
    r"|referee|friend|someone|people|i|we)\s+(?:says?|said|claim(?:s|ed)?|insist(?:s|ed)?"
    r"|told me|thinks?|believes?|argue(?:s|d)?|heard|read|mentioned|suggest(?:s|ed)?)\s+"
    r"(?:that\s+)?",
    re.IGNORECASE,
)
YOU_SAY_RE = re.compile(
    r"^(?:would|do|does|did|can|could)\s+you\s+(?:say|think|agree|believe|reckon)\s+"
    r"(?:that\s+)?",
    re.IGNORECASE,
)
_COMPLEMENTIZER_RE = re.compile(r"\b(?:that|whether|if)\b", re.IGNORECASE)
_COMPLEMENTIZER_WINDOW = 60
_QUOTE_OPENERS = "\"“「『«'"
_QUOTE_CLOSERS = "\"”」』»'"
_REPORT_EN = "|".join(REPORT_VERBS_EN)
#: Searched in the 60 characters before an opening quote: the slice is
#: bounded, so each attempt is.
REPORT_BEFORE_RE = re.compile(
    rf"(?:\b(?:{_REPORT_EN})|\btold\s+(?:me|us)|\baccording\s+to(?:\s+[^\s\"“”«「:,]+){{1,6}})"
    r"\s*(?:[:,]\s*)?$",
    re.IGNORECASE,
)
REPORT_WINDOW = 60
#: Matched right after a closing quote: at most three words before the verb.
REPORT_AFTER_RE = re.compile(
    r"[\s,.]{0,3}(?:(?:is|was|are|were)\s+what\s+)?(?:[^\W\d_]+\s+){0,3}?"
    rf"(?:(?:{_REPORT_EN})|told\s+(?:me|us)|according\s+to)\b",
    re.IGNORECASE,
)
#: Matched right after a Japanese quote (or a Latin run): と with a reporting
#: verb, or という/といった/との with a noun of saying.
REPORT_JA_RE = re.compile(
    '[」』”"]?[ \t　]{0,2}と(?:(?:いう|いった|の)(?:'
    + "|".join(PROPOSITION_NOUNS_JA)
    + ")|(?:"
    + "|".join(REPORT_VERBS_JA)
    + "))"
)
_KANJI_KANA_RE = re.compile("[ァ-ヺ㐀-䶿一-鿿]")
#: The word right after a token, when only spaces separate them (anchored
#: at the token's end, so each attempt is linear in the space run).
_NEXT_WORD_RE = re.compile(r"[ \t]+([^\W_]+(?:[-'/][^\W_]+)*)")
#: Who said it (before a reported quote) and the comment after it may hold
#: this much substance. A reporter is one noun phrase naming a person or a
#: source: two Latin words of substance (``a senior reviewer``), or three
#: two-character kanji/katakana compounds (``学会の座長``, ``共同研究者``); the
#: comment is a verb of receiving or a hedge (``を受けました``, ``, apparently``)
#: and no more than one word of substance. A heavier sentence asks something
#: of its own (``... — does that hold for SAPO-34?``) and stays whole.
REPORTER_MAX = 2
REPORTER_MAX_JA = 6
COMMENT_MAX = 1
COMMENT_MAX_JA = 2


# ---------------------------------------------------------------- the API ---


@dataclass(frozen=True)
class Focus:
    """What to search. ``basis``: whole | field | quote | script | colon |
    segments | clause; ``trimmed``: framing clauses were cut from the span."""

    query: str
    basis: str
    trimmed: bool = False


def focus_enabled() -> bool:
    """``R4_RAG_FOCUS=on`` searches the focused question; unset, or any other
    value, searches the whole message, as before wave H.

    Off by default (2026-10-11): focus-v1 recovered the bare claim's
    retrieval on every tuning template but failed its held-out gate (eval
    Phase 12; ADR-093 D-8), and a gate that fails leaves its knob off.
    focus-v2 (four structural fixes for those failures) stays off too until
    it passes a held-out set it was never tuned on."""
    return os.environ.get(FOCUS_ENV, "").strip().lower() == "on"


def rules_digest() -> str:
    """sha256 over the rules version, the lexicons in declaration order and
    the thresholds: a result records the rules it was measured with."""
    digest = hashlib.sha256()
    for part in _digest_parts():
        digest.update(part.encode("utf-8") + b"\0")
    return digest.hexdigest()


def _digest_parts() -> Iterable[str]:
    yield FOCUS_RULES_VERSION
    for role, words in META_EN:
        yield f"{role}:{' '.join(words)}"
    lexicons = (META_JA, DROP_LABELS, SLOT_LABELS, SOFT_LABELS, CORPUS_NOUNS, CORPUS_NOUNS_JA)
    extra = (TYPE_NAMES, TEMPLATE_INFOS, ABBREVIATIONS, PERSONAL_WORDS, ADDRESS_WORDS)
    v2 = (
        PROSE_INFOS,
        QUOTED_OBJECTS,
        REQUEST_ROLES,
        FORWARD_REFS,
        REQUEST_WORDS,
        BACK_REFS,
        REPORT_VERBS_EN,
        PROPOSITION_NOUNS_JA,
        PREPOSITIONS,
    )
    for lexicon in (*lexicons, *extra, *v2, REPORT_VERBS_JA, _QUOTE_OPENERS, _QUOTE_CLOSERS):
        yield " ".join(lexicon)
    for pattern in (FORMAT_RE, REPORTED_RE, YOU_SAY_RE, _LABEL_RE, _CLAUSE_SPLIT_RE):
        yield pattern.pattern
    yield from (pattern.pattern for pattern in _v2_patterns())
    limits = (FOCUS_MAX_CHARS, MAX_MARKS, MAX_SEGMENTS, MAX_FIELDS, FRAME_RATIO, FORMAT_RATIO)
    yield repr((*limits, GATE_FLOOR, GATE_MIN_FRAME, MIN_WORDS, LEAD_MAX_CHARS))
    prose = (PROSE_MAX_CHARS, PROSE_MIN_WORDS, PROSE_WORD_SHARE)
    yield repr((*prose, REPORT_WINDOW, REPORTER_MAX, REPORTER_MAX_JA, COMMENT_MAX, COMMENT_MAX_JA))


def _v2_patterns() -> tuple[re.Pattern[str], ...]:
    """The patterns focus-v2 added, in declaration order."""
    return (
        _NOT_PROSE_RE,
        _PROSE_WORD_RE,
        _QUOTE_MARK_RE,
        _LEAD_CLAUSE_RE,
        REPORT_BEFORE_RE,
        REPORT_AFTER_RE,
        REPORT_JA_RE,
        _KANJI_KANA_RE,
        _NEXT_WORD_RE,
    )


def focus_query(message: str) -> Focus:
    """The text to search for ``message``: its marked content, else the
    message less its framing segments or clauses, else the whole message
    (returned exactly as given)."""
    whole = Focus(message, WHOLE)
    doc = _prepare(message) if _in_bounds(message) else None
    if doc is None or len(_segment_spans(doc.text)) > MAX_SEGMENTS:
        return whole
    found = _find(doc)
    if found is None:
        return whole
    query = " ".join(found.query.split())
    if len(content_tokens(query)) < MIN_WORDS or query == " ".join(message.split()):
        return whole
    return Focus(query, found.basis, found.trimmed)


def _in_bounds(message: object) -> bool:
    """Step 0: the switch is on and the message is a non-empty string of at
    most FOCUS_MAX_CHARS characters."""
    if not focus_enabled() or not isinstance(message, str):
        return False
    return bool(message.strip()) and len(message) <= FOCUS_MAX_CHARS


def without_template_fences(text: str) -> str:
    """``text`` less its template fences and the fence lines around quoted
    prose: a fence (no info string, json or yaml) that holds only placeholder
    values is an output schema, and a plain fence that holds one sentence the
    message names (``the claim in the code block``) is a quotation; neither
    is code. A code fence, a data fence and an unclosed fence stay."""
    if len(text) > FOCUS_MAX_CHARS:
        return text
    fences = _fences(text)
    if not fences:
        return text
    templates, prose = _fence_kinds(text, fences)
    lines = [span for fence in prose for span in fence.marker_lines()]
    spans = [(fence.start, fence.end) for fence in templates] + lines
    return _blank(text, spans) if spans else text


# ------------------------------------------------------------------ units ---


@lru_cache(maxsize=8192)
def _is_meta(token: str) -> bool:
    bare = _POSSESSIVE_RE.sub("", token)
    return bare in _META_WORDS or bare in _META_STEMS or stem(bare) in _META_STEMS


def _frame_mask(folded: str) -> set[int]:
    """Positions of framing CJK characters: every hiragana, every META_JA hit."""
    marked = {m.start() for m in _HIRAGANA_RE.finditer(folded)}
    for match in _META_JA_RE.finditer(folded):
        marked.update(range(match.start(), match.end()))
    return marked


def _cjk_units(start: int, token: str, mask: set[int]) -> tuple[int, int]:
    """(content, frame) bigrams of a CJK token; a bigram is framing when both
    of its characters are."""
    if len(token) < 2:
        return 1, int(start in mask)
    pairs = range(start, start + len(token) - 1)
    return len(pairs), sum(1 for i in pairs if i in mask and i + 1 in mask)


@lru_cache(maxsize=2048)
def _units(text: str) -> tuple[int, int]:
    """(content units, framing units): content units are ``content_tokens``."""
    folded = str(text or "").casefold()
    mask = _frame_mask(folded) if _CJK_RE.search(folded) else set()
    content = frame = 0
    for match in _TOKEN_RE.finditer(folded):
        token = match.group()
        if _CJK_RE.search(token):
            more, framing = _cjk_units(match.start(), token, mask)
        elif token in _FUNCTION_WORDS or (len(token) < 2 and not token.isdigit()):
            continue
        else:
            more, framing = 1, int(_is_meta(token))
        content += more
        frame += framing
    return content, frame


def _ratio(content: int, frame: int) -> float:
    return frame / content if content else 1.0


def _asks_format(text: str) -> bool:
    """``FORMAT_RE.search(text)``, the format-word half first."""
    if _FORMAT_PLAIN_RE.search(text):
        return True
    return bool(_FORMAT_OBJECT_RE.search(text) and _FORMAT_REQUEST_RE.search(text))


@lru_cache(maxsize=2048)
def _kind(text: str) -> str:
    content, frame = _units(text)
    ratio = _ratio(content, frame)
    if ratio >= FORMAT_RATIO and _asks_format(text):
        return _FORMAT
    if ratio >= FRAME_RATIO and frame >= 1:
        return _FRAME
    if content - frame < 2:
        return _THIN
    return _CONTENT


def _substantive(text: str) -> int:
    content, frame = _units(text)
    return content - frame


def _content_ok(text: str) -> bool:
    """CONTENT with at least three content units (no cleaning)."""
    return _kind(text) == _CONTENT and _units(text)[0] >= MIN_WORDS


# --------------------------------------------------------------- blanking ---


@dataclass(frozen=True)
class _Doc:
    """``text`` is the working copy W (same length as the message);
    ``removed`` counts the spans blanked from it, each one framing unit."""

    text: str
    removed: int = 0
    had_format: bool = False
    quoted: tuple[Span, ...] = ()  # bodies of prose fences (quotations)


@dataclass(frozen=True)
class _Found:
    query: str
    basis: str
    trimmed: bool


Span = tuple[int, int]


def _blank(text: str, spans: Iterable[Span]) -> str:
    """``text`` with each span replaced by spaces, by offset."""
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


@dataclass(frozen=True)
class _Fence:
    """A fenced block: its whole span, its casefolded info string and its
    body's span (between the fence lines)."""

    start: int
    end: int
    info: str
    body_start: int
    body_end: int

    def body(self, text: str) -> str:
        return text[self.body_start : self.body_end]

    def marker_lines(self) -> list[Span]:
        return [(self.start, self.body_start), (self.body_end, self.end)]


def _fences(text: str) -> list[_Fence] | None:
    """Every fenced block; None when a fence is left open, mismatched, or
    past the bound (the message is then opaque)."""
    marks = []
    for match in _FENCE_LINE_RE.finditer(text):
        marks.append(match)
        if len(marks) > 2 * MAX_MARKS:
            return None
    if len(marks) % 2:
        return None
    out = []
    for opening, closing in zip(marks[::2], marks[1::2], strict=True):
        if closing.group(1) != opening.group(1) or closing.group(2).strip():
            return None
        info = opening.group(2).strip().casefold()
        out.append(_Fence(opening.start(), closing.end(), info, opening.end(), closing.start()))
    return out


def _is_placeholder(value: str) -> bool:
    """A schema value that holds no content: empty, ``...``, ``<…>``, a
    ``|`` choice, a type name, ``[]``/``{}``, or a string or bracket with no
    content token."""
    raw = value.strip().rstrip(",").strip()
    bare = raw.strip("\"'` ")
    if not bare or bare in ("...", "…") or "|" in raw or raw[:1] == "{":
        return True
    if _ANGLE_RE.fullmatch(bare) or bare.casefold() in _TYPE_SET or bare in ("[]", "{}"):
        return True
    return raw[:1] in "[\"'" and not content_tokens(bare.strip("[]"))


def _is_template(info: str, body: str) -> bool:
    """An output schema: a json/yaml (or bare) fence with no code marker whose
    every ``key: value`` value is a placeholder (at least one pair)."""
    if info not in TEMPLATE_INFOS or _CODE_MARKER_RE.search(body):
        return False
    values = [match.group(1) for match in _PAIR_RE.finditer(body)]
    return bool(values) and all(_is_placeholder(value) for value in values)


def _names_an_object(text: str) -> bool:
    """The text names a proposition to check: a claim, a statement, a
    sentence, a quotation (QUOTED_OBJECTS, by stem) as the head of its noun
    phrase, followed by punctuation, a function word or nothing (``the claim
    in the code block``, ``this hypothesis:``). A noun that modifies the next
    one (``the claim file``, ``a hypothesis test``) names no proposition."""
    folded = text.casefold()
    for match in _TOKEN_RE.finditer(folded):
        if stem(match.group()) in _OBJECT_STEMS and _heads_its_phrase(folded, match.end()):
            return True
    return False


def _heads_its_phrase(text: str, end: int) -> bool:
    following = _NEXT_WORD_RE.match(text, end)
    return following is None or following.group(1) in _POSTNOMINAL


def _is_prose(info: str, body: str) -> bool:
    """A quoted sentence, not code: a plain (or text) fence whose body is one
    short paragraph of words with no code marker, code character, prompt,
    label or indentation, impersonal, and CONTENT with three units or more."""
    bare = body.strip()
    if info not in PROSE_INFOS or not _one_paragraph(bare) or not _worded(bare):
        return False
    return not _personal(bare, _ADDRESS) and _content_ok(" ".join(bare.split()))


def _one_paragraph(bare: str) -> bool:
    """One short paragraph with no code character, prompt, label or indent."""
    if not bare or len(bare) > PROSE_MAX_CHARS or "\n\n" in bare:
        return False
    return _NOT_PROSE_RE.search(bare) is None


def _worded(bare: str) -> bool:
    """At least PROSE_MIN_WORDS words, PROSE_WORD_SHARE of them plain words."""
    words = bare.split()
    worded = sum(1 for word in words if _PROSE_WORD_RE.fullmatch(word))
    return len(words) >= PROSE_MIN_WORDS and worded >= PROSE_WORD_SHARE * len(words)


def _fence_kinds(text: str, fences: list[_Fence]) -> tuple[list[_Fence], list[_Fence]]:
    """(templates, prose quotations): a prose fence counts only when the text
    outside every fence names an object (``the claim in the code block``)."""
    templates = [fence for fence in fences if _is_template(fence.info, fence.body(text))]
    outside = _blank(text, [(fence.start, fence.end) for fence in fences])
    if not _names_an_object(outside):
        return templates, []
    prose = [
        fence
        for fence in fences
        if fence not in templates and _is_prose(fence.info, fence.body(text))
    ]
    return templates, prose


def _is_schema(region: str) -> bool:
    inner = region[1:-1]
    if _QUOTED_KEY_RE.search(inner) or _PLACEHOLDER_IN_RE.search(inner):
        return True
    return not inner.strip() or inner.strip().casefold() in _TYPE_SET


def _schema_spans(text: str) -> list[Span]:
    return [m.span() for m in _SCHEMA_RE.finditer(text) if _is_schema(m.group())]


def _container_spans(text: str) -> list[Span]:
    spans = [m.span("name") for m in _CONTAINER_RE.finditer(text)]
    return spans + [m.span("name") for m in _CONTAINER_JA_RE.finditer(text)]


def _prepare(message: str) -> _Doc | None:
    """Step 1: blank template fences and inline schemas (output format) and
    container names, and the fence lines around quoted prose (its body is a
    quotation). Any other fence makes the message opaque."""
    fences = _fences(message)
    if fences is None:
        return None
    templates, prose = _fence_kinds(message, fences) if fences else ([], [])
    if len(templates) + len(prose) < len(fences):
        return None
    lines = [span for fence in prose for span in fence.marker_lines()]
    text = _blank(message, [(fence.start, fence.end) for fence in templates] + lines)
    schemas = _schema_spans(text)
    text = _blank(text, schemas)
    containers = _container_spans(text)
    text = _blank(text, containers)
    removed = len(templates) + len(schemas) + len(containers)  # fence lines are quote marks
    quoted = tuple((fence.body_start, fence.body_end) for fence in prose)
    return _Doc(text, removed, bool(templates or schemas), quoted)


# --------------------------------------------------------------- segments ---


def _opens_sentence(char: str) -> bool:
    return char.isupper() or char.isdigit() or (bool(char) and char in _OPENERS)


def _period_breaks(text: str, index: int) -> bool:
    """A full stop at ``index`` (followed by whitespace) ends a sentence when
    the next character opens one, or the word before it is no abbreviation."""
    after = _AFTER_PERIOD_RE.match(text, index + 1)
    if _opens_sentence(after.group(1) if after else ""):
        return True
    word = _WORD_BEFORE_RE.search(text, max(0, index - 40), index)
    before = (word.group() if word else "").strip(".")
    letters = sum(1 for ch in before if ch.isalpha())
    return letters >= 3 and before.casefold() not in _ABBREVIATION_SET


def _push(spans: list[Span], text: str, start: int, end: int) -> None:
    piece = text[start:end]
    lead = len(piece) - len(piece.lstrip())
    trail = len(piece) - len(piece.rstrip())
    if end - trail > start + lead:
        spans.append((start + lead, end - trail))


def _segment_spans(text: str) -> list[Span]:
    """Sentences and lines, as offsets; at most MAX_SEGMENTS + 1 are found."""
    spans: list[Span] = []
    start = 0
    for match in _SEGMENT_MARK_RE.finditer(text):
        mark = match.group()
        if mark == "." and not _period_breaks(text, match.start()):
            continue
        _push(spans, text, start, match.start() if mark == "\n" else match.end())
        start = match.end()
        if len(spans) > MAX_SEGMENTS:
            return spans
    _push(spans, text, start, len(text))
    return spans


def _segments(text: str) -> list[str]:
    return [text[s:e] for s, e in _segment_spans(text)]


# ------------------------------------------------------------------ clean ---


def _keep_content_segments(span: str) -> str:
    """When a span holds framing segments beside CONTENT ones, only its
    CONTENT segments (a span with none is trimmed clause by clause instead)."""
    pieces = _segments(span)
    kinds = [_kind(piece) for piece in pieces]
    if not any(kind in _FRAMING for kind in kinds) or _CONTENT not in kinds:
        return span
    return " ".join(p for p, k in zip(pieces, kinds, strict=True) if k == _CONTENT)


def _drop_frame_parens(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        framing = _kind(inner) in _FRAMING and _substantive(inner) <= 1
        return " " if framing else match.group()

    return _PAREN_RE.sub(replace, text)


def _droppable(clause: str) -> bool:
    """A framing clause with at most one substantive unit; a clause that asks
    (``Is there a JSON schema for X``, ``which papers use CIF?``) keeps its
    one substantive word, however framing the rest of it is."""
    if _kind(clause) not in _FRAMING:
        return False
    substance = _substantive(clause)
    return substance <= 0 or (substance <= 1 and not is_question_like(clause))


def _trim_once(text: str) -> str | None:
    """The span less its leading, else its trailing, framing clause."""
    cuts = list(_CLAUSE_SPLIT_RE.finditer(text))
    if not cuts:
        return None
    rest = text[cuts[0].end() :]
    if _droppable(text[: cuts[0].start()]) and _units(rest)[0] >= MIN_WORDS:
        return rest
    rest = text[: cuts[-1].start()]
    if _droppable(text[cuts[-1].end() :]) and _units(rest)[0] >= MIN_WORDS:
        return rest
    return None


def _trim_clauses(text: str) -> str:
    for _ in range(4):
        trimmed = _trim_once(text)
        if trimmed is None:
            break
        text = trimmed
    return text


def _cut_complementizer(text: str) -> str | None:
    """``is it true that X`` -> ``X``: the first that/whether/if in the first
    60 characters, when what comes before it is framing."""
    match = _COMPLEMENTIZER_RE.search(text, 0, _COMPLEMENTIZER_WINDOW + 8)
    if match is None or match.start() >= _COMPLEMENTIZER_WINDOW:
        return None
    head = text[: match.start()]
    return text[match.end() :] if head.strip() and _kind(head) == _FRAME else None


def _lead_cuts(text: str) -> str:
    """Reported speech, "would you say", then a framing head before a
    complementizer, each once and only when what remains is CONTENT."""
    for cut in (
        lambda t: REPORTED_RE.sub("", t, count=1),
        lambda t: YOU_SAY_RE.sub("", t, count=1),
        _cut_complementizer,
    ):
        rest = cut(text)
        if rest is not None and rest != text and _content_ok(rest):
            text = rest.strip()
    return text


def _clean(span: str) -> tuple[str, bool]:
    """Step 6: a chosen span less its framing segments, parentheses, edge
    clauses and lead; the span itself when under three content units remain."""
    original = " ".join(span.split())
    text = _keep_content_segments(span)
    text = _drop_frame_parens(text)
    text = " ".join(_trim_clauses(" ".join(text.split())).split())
    text = " ".join(_lead_cuts(text).split())
    if _units(text)[0] < MIN_WORDS:
        return original, False
    return text, text != original


def _acceptable(span: str) -> tuple[str, bool] | None:
    """The cleaned span when it is CONTENT with at least three content units."""
    query, trimmed = _clean(span)
    return (query, trimmed) if _content_ok(query) else None


# ------------------------------------------------------------------- gate ---


def _has_format_unit(text: str) -> bool:
    return any(_kind(piece) == _FORMAT for piece in _segments(text))


def _rest_units(doc: _Doc, kept: str) -> tuple[int, int]:
    """(content, framing) units of what stays behind R: the working copy less
    the kept text (a framing segment or clause cut from a chosen span stays
    behind), plus one framing unit per span blanked in step 1."""
    total_content, total_frame = _units(doc.text)
    kept_content, kept_frame = _units(kept)
    content = max(0, total_content - kept_content) + doc.removed
    return content, min(content, max(0, total_frame - kept_frame) + doc.removed)


def _gate(
    doc: _Doc, spans: Sequence[Span], kept: str, *, discourse: bool = False, presented: bool = False
) -> bool:
    """Step 4: extract only when what stays behind is more framing than what
    is kept, holds at least two framing units, and is framing enough (or asks
    for an output format). With ``discourse`` (a paragraph set apart from
    personal discourse, focus-v2) a sentence that speaks for the writer or to
    the reader counts as framing for "framing enough"; with ``presented`` (a
    claim a personal lead presents, :func:`_presents`) its units also count
    toward the two framing units, since the lead's structure, not its
    vocabulary, marks it as framing."""
    rest_ratio, frame = _rest_framing(doc, spans, kept, discourse or presented, presented)
    if rest_ratio <= _ratio(*_units(kept)) or frame < GATE_MIN_FRAME:
        return False
    if rest_ratio >= GATE_FLOOR or doc.had_format:
        return True
    return any(_has_format_unit(text) for text in (_blank(doc.text, spans), *_texts(doc, spans)))


def _rest_framing(
    doc: _Doc, spans: Sequence[Span], kept: str, discourse: bool, presented: bool
) -> tuple[float, int]:
    """(framing ratio, framing units) of what stays behind, read through its
    discourse sentences when asked to."""
    content, frame = _rest_units(doc, kept)
    rest_ratio = _ratio(content, frame)
    if not discourse:
        return rest_ratio, frame
    units, framing = _discourse_units(_blank(doc.text, spans))
    return max(rest_ratio, _ratio(units, framing)), max(frame, framing) if presented else frame


def _discourse_units(rest: str) -> tuple[int, int]:
    """The rest's content units, and those that are framing or lie in a
    discourse sentence (personal, asking nothing of substance)."""
    content = framing = 0
    for piece in _segments(rest):
        units, frame = _units(piece)
        content += units
        framing += units if _discourse(piece) else frame
    return content, framing


def _texts(doc: _Doc, spans: Sequence[Span]) -> list[str]:
    return [doc.text[start:end] for start, end in spans]


# ----------------------------------------------------------- (a) fields ---


@dataclass(frozen=True)
class _Mark:
    """A labelled field: its casefolded label, role, value spans and, for a
    dropped field, the section it takes with it."""

    label: str
    role: str
    value: tuple[Span, ...]
    section: Span


def _label_words(label: str) -> list[str]:
    return _WORDS_RE.findall(label.casefold())


def _role(label: str, value: str) -> str:
    words = _label_words(label)
    whole = " ".join(words)
    keys = {whole, words[-1]} if words else {whole}
    if keys & _DROP_SET:
        return _DROP
    if keys & _SLOT_SET and _is_placeholder(value):
        return _SLOT
    return _SOFT if keys & _SOFT_SET else _PLAIN


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    label: str = ""
    style: str = ""  # "" (no mark) | label | colon (alone) | heading (markdown)
    value_at: int = 0


def _short_label(label: str) -> bool:
    return bool(label) and len(_label_words(label)) <= 4


def _label_line(line: str, start: int, end: int) -> _Line | None:
    """``Label: value`` (or ``Label:`` alone: a heading; markdown or bold when
    the line opens with ``#``, ``**`` or ``__``)."""
    match = _LABEL_RE.match(line)
    if not match or not _short_label(match["label"]):
        return None
    style = "label" if match["value"].strip() else "colon"
    if style == "colon" and line.lstrip()[:1] in ("#", "*", "_"):
        style = "heading"
    return _Line(start, end, match["label"], style, start + match.start("value"))


def _line_mark(text: str, start: int, end: int) -> _Line:
    line = text[start:end]
    labelled = _label_line(line, start, end)
    if labelled is not None:
        return labelled
    bare = line.strip()
    heading = _HEADING_RE.match(bare) if len(bare) <= HEADING_MAX_CHARS else None
    label = (heading["md"] or heading["bold"] or "") if heading else ""
    return _Line(start, end, label, "heading", end) if _short_label(label) else _Line(start, end)


def _lines(text: str) -> list[_Line]:
    """Every line, with its mark; at most MAX_MARKS lines are marks."""
    out: list[_Line] = []
    start = marks = 0
    for raw in text.split("\n"):
        end = start + len(raw)
        line = _line_mark(text, start, end) if marks < MAX_MARKS else _Line(start, end)
        marks += bool(line.style)
        out.append(line)
        start = end + 1
    return out


def _blank_line(text: str, line: _Line) -> bool:
    return not text[line.start : line.end].strip()


def _indent(text: str, line: _Line) -> int:
    raw = text[line.start : line.end]
    return len(raw) - len(raw.lstrip())


def _value_lines(text: str, lines: list[_Line], i: int) -> list[Span]:
    """A field's value: the rest of its line, or (a heading) the next
    non-blank lines; continued over non-blank, unmarked lines."""
    line = lines[i]
    spans = [(line.value_at, line.end)] if line.style == "label" else []
    j = i + 1
    if line.style != "label":
        while j < len(lines) and _blank_line(text, lines[j]):
            j += 1
    while j < len(lines) and not lines[j].style and not _blank_line(text, lines[j]):
        spans.append(_unquoted(text, lines[j].start, lines[j].end))
        j += 1
    return spans


def _unquoted(text: str, start: int, end: int) -> Span:
    """A value line less its block-quote marker (``> X`` under a heading)."""
    marker = _QUOTE_MARK_RE.match(text, start, end)
    return (marker.end(), end) if marker else (start, end)


def _section_end(text: str, lines: list[_Line], i: int) -> int:
    """Where a dropped field's section ends: a markdown heading's at the next
    heading; a label's at the next blank line, heading or label of no deeper
    indent."""
    line, j = lines[i], i + 1
    if line.style == "colon":
        while j < len(lines) and _blank_line(text, lines[j]):
            j += 1
    depth = _indent(text, line)
    while j < len(lines):
        nxt = lines[j]
        if nxt.style in ("heading", "colon") or (
            line.style != "heading" and _ends_section(text, nxt, depth)
        ):
            break
        j += 1
    return lines[j - 1].end if j > i else line.end


def _ends_section(text: str, nxt: _Line, depth: int) -> bool:
    if _blank_line(text, nxt):
        return True
    return nxt.style == "label" and _indent(text, nxt) <= depth


def _line_marks(text: str) -> list[_Mark]:
    lines = _lines(text)
    marks: list[_Mark] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.style:
            i += 1
            continue
        value = _value_lines(text, lines, i)
        role = _role(line.label, " ".join(text[s:e] for s, e in value))
        end = _section_end(text, lines, i) if role in (_DROP, _SLOT) else line.end
        marks.append(_Mark(line.label.casefold().strip(), role, tuple(value), (line.start, end)))
        i = _next_line(lines, i, end, role)
    return marks


def _next_line(lines: list[_Line], i: int, end: int, role: str) -> int:
    """After a dropped section, the first line past it; else the next line."""
    j = i + 1
    while role in (_DROP, _SLOT) and j < len(lines) and lines[j].start <= end:
        j += 1
    return j


def _tag_marks(text: str) -> list[_Mark]:
    """``<tag>…</tag>`` fields, labelled by the tag name (at most MAX_MARKS)."""
    marks: list[_Mark] = []
    position = 0
    for _ in range(MAX_MARKS):
        opening = _TAG_OPEN_RE.search(text, position)
        if opening is None:
            break
        closing = text.find(f"</{opening['label']}>", opening.end())
        if closing < 0:
            position = opening.end()
            continue
        value = (opening.end(), closing)
        role = _role(opening["label"], text[opening.end() : closing])
        end = closing + len(opening["label"]) + 3
        marks.append(_Mark(opening["label"].casefold(), role, (value,), (opening.start(), end)))
        position = end
    return marks


def _all_marks(text: str) -> list[_Mark]:
    """Tag fields and the line fields of the text outside the tags, in
    message order."""
    tags = _tag_marks(text)
    lines = _line_marks(_blank(text, [mark.section for mark in tags]))
    merged: list[_Mark] = []
    while tags and lines:
        first = tags if tags[0].section[0] < lines[0].section[0] else lines
        merged.append(first.pop(0))
    return merged + tags + lines


def _last_by_label(marks: list[_Mark]) -> list[_Mark]:
    """Value-bearing fields; a repeated label is a field only at its last
    occurrence (few-shot examples come first)."""
    last = {mark.label: index for index, mark in enumerate(marks)}
    return [
        mark
        for index, mark in enumerate(marks)
        if last[mark.label] == index and mark.role in (_PLAIN, _SOFT)
    ]


def _span_text(text: str, spans: Iterable[Span]) -> str:
    return " ".join(text[s:e] for s, e in spans)


def _chosen_fields(text: str, marks: list[_Mark]) -> list[tuple[_Mark, str, bool]]:
    """ACCEPTABLE field values in message order, at most MAX_FIELDS; a soft
    (context) field only beside a plain one."""
    chosen = []
    for mark in _last_by_label(marks):
        found = _acceptable(_span_text(text, mark.value))
        if found is not None:
            chosen.append((mark, *found))
    if not any(mark.role == _PLAIN for mark, _, _ in chosen):
        return []
    return chosen[:MAX_FIELDS]


def _field(doc: _Doc) -> tuple[_Found | None, list[Span]]:
    """(a) FIELD: labelled values, gated; and the sections of dropped fields
    (formats, rules, examples), which later finders do not see."""
    marks = _all_marks(doc.text)
    drops = [mark.section for mark in marks if mark.role in (_DROP, _SLOT)]
    chosen = _chosen_fields(doc.text, marks)
    if not chosen:
        return None, drops
    found = _joined(chosen)
    spans = [span for mark, _, _ in chosen for span in mark.value]
    return (found if _gate(doc, spans, found.query) else None), drops


def _joined(chosen: list[tuple[_Mark, str, bool]]) -> _Found:
    query = " ".join(cleaned for _, cleaned, _ in chosen)
    return _Found(query, FIELD, any(trimmed for _, _, trimmed in chosen))


def _without(doc: _Doc, sections: list[Span]) -> _Doc:
    if not sections:
        return doc
    removed = doc.removed + len(sections)
    return _Doc(_blank(doc.text, sections), removed, doc.had_format, doc.quoted)


# ------------------------------------------------------ (b)-(d) marks ---


def _most_content(text: str, candidates: Iterable[tuple[Span, ...]]) -> tuple | None:
    """The ACCEPTABLE candidate with the most content units (first on a tie):
    (spans, cleaned, trimmed, raw text)."""
    best: tuple | None = None
    best_units = -1
    for spans in candidates:
        raw = _span_text(text, spans)
        found = _acceptable(raw)
        units = _units(raw)[0] if found is not None else -1
        if units > best_units:
            best, best_units = (spans, *found, raw), units
    return best


def _gated(
    doc: _Doc,
    picked: tuple | None,
    basis: str,
    *,
    discourse: bool = False,
    presented: bool = False,
) -> _Found | None:
    if picked is None:
        return None
    spans, cleaned, trimmed, raw = picked
    if basis in (QUOTE, SCRIPT) and not _set_off(doc.text, spans):
        return None
    if not _gate(doc, spans, cleaned, discourse=discourse, presented=presented):
        return None
    return _Found(cleaned, basis, trimmed)


def _set_off(text: str, spans: Sequence[Span]) -> bool:
    """A quotation or Latin run is the thing to check when it stands apart
    from its sentence, or when its sentence reports it as said (focus-v2)."""
    return _stands_apart(text, spans) or _reported(text, spans)


def _holding(text: str, spans: Sequence[Span]) -> Span:
    """The sentences that hold ``spans``, as one span."""
    first, last = spans[0][0], spans[-1][1]
    start, end = first, last
    for s_start, s_end in _segment_spans(text):
        if s_start < last + 1 and first - 1 < s_end:
            start, end = min(start, s_start), max(end, s_end)
    return start, end


def _weight_ja(text: str) -> int:
    """Kanji and katakana that are not framing (META_JA): a Japanese
    phrase's substance, by character."""
    mask = _frame_mask(text)
    return sum(1 for match in _KANJI_KANA_RE.finditer(text) if match.start() not in mask)


def _light(text: str, latin_max: int, ja_max: int) -> bool:
    latin = _CJK_RE.sub(" ", text)
    return _substantive(latin) <= latin_max and _weight_ja(text) <= ja_max


def _reported(text: str, spans: Sequence[Span]) -> bool:
    """The quotation is reported speech: the sentence says who said it and
    what it is (``My PI says "X"``, ``"X" is what my PI claims``,
    ``共同研究者から「X」という指摘``) and nothing else of substance, so a
    quoted claim under discussion is set off and a quoted term inside a
    question (``Is the claim "X" valid for helium?``) is not."""
    if len(spans) != 1:
        return False
    start, end = spans[0]
    sentence = _holding(text, spans)
    open_at, close_at = _quote_marks(text, start, end)
    report = REPORT_JA_RE.match(text, end, sentence[1])
    if report is not None:
        reporter = text[sentence[0] : open_at]
        after = text[report.end() : sentence[1]]
        return _light(reporter, REPORTER_MAX, REPORTER_MAX_JA) and _light_comment(after)
    if open_at == start or close_at == end:
        return False
    return _reported_en(text, sentence, open_at, close_at)


def _quote_marks(text: str, start: int, end: int) -> Span:
    """Where the quotation's marks open and close (its own bounds if none)."""
    open_at = start - 1 if start and text[start - 1] in _QUOTE_OPENERS else start
    close_at = end + 1 if end < len(text) and text[end] in _QUOTE_CLOSERS else end
    return open_at, close_at


def _light_comment(text: str) -> bool:
    return _light(text, COMMENT_MAX, COMMENT_MAX_JA)


def _reported_en(text: str, sentence: Span, open_at: int, close_at: int) -> bool:
    """``<reporter> says "X" <comment>`` or ``"X" is what <reporter> claims``."""
    seg_start, seg_end = sentence
    window = max(seg_start, open_at - REPORT_WINDOW)
    before = REPORT_BEFORE_RE.search(text[window:open_at])
    if before is not None:
        reporter = text[seg_start : window + before.start()]
        after = text[close_at:seg_end]
        return _light(reporter, REPORTER_MAX, REPORTER_MAX_JA) and _light_comment(after)
    after_report = REPORT_AFTER_RE.match(text, close_at, seg_end)
    if after_report is None:
        return False
    lead, tail = text[seg_start:open_at], text[after_report.end() : seg_end]
    return _light_comment(lead) and _light(tail, REPORTER_MAX, REPORTER_MAX_JA)


def _stands_apart(text: str, spans: Sequence[Span]) -> bool:
    """A quotation or Latin run is the question only when the sentences that
    hold it say nothing else of substance (at most one unit): a quoted claim
    is set apart from its frame, a quoted term sits inside a question that
    keeps content of its own (2026-10-11, wave H review)."""
    around = [
        (start, end)
        for start, end in _segment_spans(text)
        if any(start < s_end and s_start < end for s_start, s_end in spans)
    ]
    rest = " ".join(_blank(text[start:end], _within(spans, start, end)) for start, end in around)
    return _substantive(rest) <= 1


def _within(spans: Sequence[Span], start: int, end: int) -> list[Span]:
    """``spans`` clipped to [start, end), as offsets into that slice."""
    clipped = ((max(s, start), min(e, end)) for s, e in spans)
    return [(s - start, e - start) for s, e in clipped if s < e]


def _quote_candidates(text: str) -> list[tuple[Span, ...]]:
    """Quotations in message order, then block quotes (each run of ``>``
    lines), at most MAX_MARKS in all."""
    found: list[tuple[Span, ...]] = []
    for match in _QUOTE_RE.finditer(text):
        if len(found) >= MAX_MARKS:
            return found
        found.append((match.span(match.lastgroup or 0),))
    for block in _BLOCKQUOTE_RE.finditer(text):
        if len(found) >= MAX_MARKS:
            break
        lines = _QUOTED_LINE_RE.finditer(text, block.start(), block.end())
        found.append(tuple(line.span(1) for line in lines))
    return found


def _quote(doc: _Doc) -> _Found | None:
    """(b) QUOTE: the quotation with the most content, gated; else a
    paragraph set apart from a personal framing, gated."""
    candidates = [(span,) for span in doc.quoted] + _quote_candidates(doc.text)
    found = _gated(doc, _most_content(doc.text, candidates[:MAX_MARKS]), QUOTE)
    if found is not None:
        return found
    set_apart = _most_content(doc.text, _set_apart(doc.text))
    return _gated(doc, set_apart, QUOTE, discourse=True)


def _paragraph_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    start = 0
    for match in _PARAGRAPH_END_RE.finditer(text):
        _push(spans, text, start, match.start())
        start = match.end()
    _push(spans, text, start, len(text))
    return spans


def _personal(text: str, words: frozenset[str] = _PRONOUNS) -> bool:
    return not words.isdisjoint(_WORDS_RE.findall(text.casefold()))


def _asks(text: str) -> bool:
    """A question with substance of its own (three or more units)."""
    return any(is_question_like(s) and _substantive(s) >= MIN_WORDS for s in _segments(text))


def _discourse(paragraph: str) -> bool:
    """A paragraph that addresses the reader or speaks for the writer (or is
    framing), and asks no question of substance."""
    personal = _personal(paragraph) or _kind(paragraph) in _FRAMING
    return personal and not _asks(paragraph)


def _set_apart(text: str) -> list[tuple[Span, ...]]:
    """Impersonal paragraphs of a message whose every other paragraph is
    discourse ("Hi! I'm a postdoc ... Could you look at this one?" / the
    claim / "I'd appreciate it if you could tell me ..."): the paragraph is
    marked off as a quotation would be."""
    spans = _paragraph_spans(text)
    if not 2 <= len(spans) <= MAX_MARKS:
        return []
    discourse = [_discourse(text[s:e]) for s, e in spans]
    return [
        (span,)
        for i, span in enumerate(spans)
        if not _personal(text[span[0] : span[1]], _ADDRESS)
        and all(discourse[:i] + discourse[i + 1 :])
    ]


def _script(doc: _Doc) -> _Found | None:
    """(c) SCRIPT: a Latin run inside a Japanese framing, gated."""
    if not _CJK_RE.search(doc.text) or not _META_JA_RE.search(doc.text):
        return None
    runs = [(match.span(),) for match in _LATIN_RUN_RE.finditer(doc.text)]
    return _gated(doc, _most_content(doc.text, runs), SCRIPT)


def _colon(doc: _Doc) -> _Found | None:
    """(d) COLON: the tail after a colon whose lead is framing, or a claim a
    personal lead presents (focus-v2), gated."""
    spans = _segment_spans(doc.text)
    for count, match in enumerate(_COLON_RE.finditer(doc.text)):
        if count >= MAX_MARKS:
            break
        found = _colon_at(doc, spans, match.start(), match.end())
        if found is not None:
            return found
    return None


def _colon_at(doc: _Doc, spans: list[Span], colon: int, after: int) -> _Found | None:
    text = doc.text
    index = _segment_index(spans, colon)
    lead = text[spans[index][0] if index >= 0 else 0 : colon]
    if len(lead) > LEAD_MAX_CHARS:
        return None
    end = _PARAGRAPH_END_RE.search(text, after)
    tail = (after, end.start() if end else len(text))
    if _units(lead)[0] >= 2 and _kind(lead) in _FRAMING:
        return _gated(doc, _most_content(text, [(tail,)]), COLON)
    if not _presents(text, spans, index, colon):
        return None
    return _presented(doc, tail)


def _presented(doc: _Doc, tail: Span) -> _Found | None:
    """The tail a personal lead presents, gated. It is a quotation in all but
    its marks: one short paragraph, bounded as a fenced quotation is
    (PROSE_MAX_CHARS, which also keeps sixteen colons linear), that stands on
    its own and is the message's one item."""
    text = doc.text
    if tail[1] - tail[0] > PROSE_MAX_CHARS or not _self_contained(text[tail[0] : tail[1]]):
        return None
    picked = _most_content(text, [(tail,)])  # bounded by the tail; the rest is O(n)
    if picked is None or not _presented_alone(text, tail):
        return None
    return _gated(doc, picked, COLON, presented=True)


def _presented_alone(text: str, tail: Span) -> bool:
    """What a lead presents is the message's one item: nothing of substance
    follows it but discourse (``Thanks!``), and nothing else in the message
    asks a question of substance. ``Some background on it: X`` followed by
    ``Which catalyst deactivates slowest?`` presents X as the context of a
    question; X is not the thing to search."""
    after = _segments(text[tail[1] :])
    if any(_kind(piece) == _CONTENT and not _discourse(piece) for piece in after):
        return False
    return not _asks(_blank(text, [tail]))


def _segment_index(spans: list[Span], index: int) -> int:
    """The segment holding ``index`` (the last one starting at or before it)."""
    found = -1
    for i, (start, _) in enumerate(spans):
        if start > index:
            break
        found = i
    return found


def _presents(text: str, spans: list[Span], index: int, colon: int) -> bool:
    """The text before the colon presents what follows (focus-v2): the lead
    sentence, with the one before it in the same paragraph, is discourse (it
    speaks for the writer or to the reader and asks nothing of substance, or
    is a bare ``The point was``), asks something of the reader (``you``,
    ``me``, ``please``) and points ahead (a cataphoric ``it``/``this``, or
    ``the claim``): ``Before I put this in my proposal I want to know
    whether our collection supports it, so give me the evidence: X``. This
    is the colon of presentation, whatever the lead's length or wording; a
    lead that only describes the writer's work (``We grow GaN on sapphire
    and our collection has papers on it: ...``) asks nothing and presents
    nothing."""
    if index < 0:
        return False
    pieces = _lead_pieces(text, spans, index, colon)
    if not _lead_is_discourse(pieces):
        return False
    lead = " ".join(pieces)
    asks = not _REQUEST_SET.isdisjoint(_WORDS_RE.findall(lead.casefold()))
    return asks and (_refers_ahead(lead) or _names_an_object(lead))


def _lead_is_discourse(pieces: list[str]) -> bool:
    """Every lead sentence is discourse (or a bare ``The point was``) and
    every clause of it is about the request (:func:`_about_the_request`)."""
    if not all(_discourse(piece) or _substantive(piece) <= 1 for piece in pieces):
        return False
    return all(_about_the_request(clause) for p in pieces for clause in _LEAD_CLAUSE_RE.split(p))


def _lead_pieces(text: str, spans: list[Span], index: int, colon: int) -> list[str]:
    """The lead sentence up to the colon, after the sentence before it when
    that one is in the same paragraph."""
    pieces = []
    if index and _same_paragraph(text, spans[index - 1], spans[index]):
        pieces.append(text[spans[index - 1][0] : spans[index - 1][1]])
    pieces.append(text[spans[index][0] : colon])
    return pieces


def _about_the_request(clause: str) -> bool:
    """A clause of a presenting lead holds a request, a framing word or a
    pronoun for the thing presented (``before I put this in my proposal``),
    or no more than one word of substance: a clause that only describes a
    topic (``I'm studying zeolite deactivation``) is what the thing presented
    leans on, and the lead is then no frame to drop."""
    if _substantive(clause) <= 1 or _refers_ahead(clause):
        return True
    words = _TOKEN_RE.findall(clause.casefold())
    if not _REQUEST_SET.isdisjoint(words):
        return True
    return any(stem(_POSSESSIVE_RE.sub("", word)) in _REQUEST_FRAME_STEMS for word in words)


def _refers_ahead(text: str) -> bool:
    """A FORWARD_REFS word used as a pronoun: ``it``, or ``this``/``these``/
    ``following``/``below`` followed by a function word or nothing (``put
    this in``, ``check the following:``), not by the noun it determines
    (``this alloy``, ``the following protocol``)."""
    folded = text.casefold()
    for match in _TOKEN_RE.finditer(folded):
        word = match.group()
        if word in _FORWARD_SET and (word == "it" or _heads_its_phrase(folded, match.end())):
            return True
    return False


def _same_paragraph(text: str, before: Span, after: Span) -> bool:
    return _PARAGRAPH_END_RE.search(text, before[1], after[0] + 1) is None


def _self_contained(tail: str) -> bool:
    """The tail opens with a sentence that speaks to no one and does not lean
    on what came before (``it reduces ...``, ``they show ...``): the thing a
    lead presents stands on its own."""
    pieces = _segments(tail)
    first = pieces[0] if pieces else ""
    if not first or _personal(first, _ADDRESS):
        return False
    words = _WORDS_RE.findall(first.casefold())[:2]
    if not _BACK_SET.isdisjoint(words):
        return False
    return words[:1] != ["it"] or words[1:2] in (["is"], ["was"])


# -------------------------------------------------- (e)-(f) subtraction ---


def _effective_kind(piece: str) -> str:
    """A segment's kind, read through its cleaning: a framing segment whose
    cleaned form is CONTENT (``Based on my papers, would you say X?``), or a
    framing question with substance of its own (``Could you tell me what my
    papers say reduces threading dislocations?``), counts as CONTENT."""
    kind = _kind(piece)
    if kind not in _FRAMING or _acceptable(piece) is not None or _asks(piece):
        return _CONTENT if kind in _FRAMING else kind
    return kind


def _effective(pieces: list[str]) -> tuple[list[str], list[tuple[str, bool]]]:
    """Each segment's effective kind and cleaned text."""
    return [_effective_kind(piece) for piece in pieces], [_clean(piece) for piece in pieces]


def _most_substance(texts: list[str], kinds: list[str]) -> int:
    top = 0
    for text, kind in zip(texts, kinds, strict=True):
        value = _substantive(text) if kind == _CONTENT else 0
        top = value if value > top else top
    return top


def _kept_segments(texts: list[str], kinds: list[str]) -> list[int]:
    """CONTENT segments that ask, are impersonal, or carry at least half the
    most substance;
    one that holds an anaphor keeps the CONTENT or THIN segment before it."""
    top = _most_substance(texts, kinds)
    keep: set[int] = set()
    for i, (text, kind) in enumerate(zip(texts, kinds, strict=True)):
        if kind != _CONTENT or not _worth_keeping(text, top):
            continue
        keep.add(i)
        if i and has_anaphor(text) and kinds[i - 1] in (_CONTENT, _THIN):
            keep.add(i - 1)
    return [i for i in range(len(texts)) if i in keep]


def _worth_keeping(text: str, top: int) -> bool:
    """A CONTENT segment that asks, that speaks to no one (a statement: the
    claim is never dropped for being shorter than a personal preamble,
    focus-v2), or that carries at least half the most substance."""
    if is_question_like(text) or not _personal(text, _ADDRESS):
        return True
    return 2 * _substantive(text) >= top


def _mixed(kinds: list[str], *, need_content: bool) -> bool:
    """A framing segment, and (``need_content``) a CONTENT one."""
    framing = any(kind in _FRAMING for kind in kinds)
    return framing and (not need_content or _CONTENT in kinds)


def _segments_query(doc: _Doc) -> _Found | None:
    """(e) SEGMENTS: the message less its framing segments (not gated)."""
    pieces = _segments(doc.text)
    if not _mixed([_kind(piece) for piece in pieces], need_content=False):
        return None
    kinds, cleaned = _effective(pieces)
    if not _mixed(kinds, need_content=True):
        return None
    kept = [cleaned[i] for i in _kept_segments([text for text, _ in cleaned], kinds)]
    query = " ".join(text for text, _ in kept)
    return _Found(query, SEGMENTS, any(trimmed for _, trimmed in kept))


def _clause(doc: _Doc) -> _Found | None:
    """(f) CLAUSE: a message that is one framing unit, less its framing
    clauses and lead."""
    if _kind(doc.text) not in _FRAMING:
        return None
    query, changed = _clean(doc.text)
    return _Found(query, CLAUSE, True) if changed and _units(query)[0] >= MIN_WORDS else None


def _find(doc: _Doc) -> _Found | None:
    """Step 5: the first finder whose span passes."""
    found, drops = _field(doc)
    if found is not None:
        return found
    doc = _without(doc, drops)
    for finder in (_quote, _script, _colon, _segments_query, _clause):
        found = finder(doc)
        if found is not None:
            return found
    return None


__all__ = [
    "FOCUS_ENV",
    "FOCUS_RULES_VERSION",
    "MARKED_BASES",
    "META_EN",
    "META_JA",
    "MIN_WORDS",
    "Focus",
    "content_tokens",
    "focus_enabled",
    "focus_query",
    "has_anaphor",
    "is_question_like",
    "rules_digest",
    "without_template_fences",
]
