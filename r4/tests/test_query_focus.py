"""ADR-093 D-8 wave H (``focus-v2``): what the pre-retrieval router searches.

A researcher's message wraps the thing to look up in framing (instructions,
output formats, schemas, few-shot examples, polite preambles, Japanese around
an English claim); the router searches the content, and a message with no
framing is searched exactly as written.
"""

from __future__ import annotations

import re
import statistics
import time

import pytest

from r4.companion import query_focus as qf

C = "IL-6 signalling drives insulin resistance in adipose tissue of obese mice."
A = "aspirin lowers adenoma recurrence in older adults"
#: The Phase-8 agent prompt (benchmarks/retrieval/backends.py AGENT_PROMPT).
PHASE8 = (
    "Using the literature in my project's knowledge graph 'scifact', is this "
    "scientific claim supported, contradicted, or is there not enough "
    "information to say? Reply with one JSON object and nothing else: "
    '{"verdict": "SUPPORT" | "CONTRADICT" | "NOT_ENOUGH_INFO", '
    '"evidence": [the source names you used]}\n\nCLAIM: '
)

# Fresh claims for the focus-v2 cases (not SciFact, not any template set).
L = "Lithium plating dominates capacity fade in graphite anodes below 0 C."
S = "Sirtuin 1 deacetylates PGC-1alpha in skeletal muscle during fasting."
G = "Graphene oxide membranes reject sodium ions with 97% efficiency."

#: focus-v2: the four structures focus-v1 missed on its held-out set, each in
#: wording of its own; every case here returns something else on focus-v1.
FOCUS_V2 = {
    # (1) A personal lead that asks the reader for something and points
    # ahead to what follows its colon, with no framing vocabulary needed.
    "v2_instr_before_no_lexicon": (
        "Since my student wants to quote it in her thesis defence next week, could you "
        f"find out whether anything we have backs it up and send me what you find: {L}",
        L,
        "colon",
    ),
    "v2_instr_before_subordinate_clause": (
        "Before this goes into the methods section of our paper, I'd like to be sure the "
        f"group's library does not contradict it, so please list whatever turns up: {S}",
        S,
        "colon",
    ),
    # (2) One paragraph of prose in a plain fence that the message names.
    "v2_format_named_prose_fence": (
        "Please assess the statement inside the fenced block using my papers.\n\n"
        f"```\n{S}\n```\n\nAnswer as YAML only:\nverdict: <SUPPORT|REFUTE|NEI>",
        S,
        "quote",
    ),
    "v2_format_text_fence": (
        "Check the hypothesis in this block against the literature in my project:\n"
        f'```text\n{L}\n```\nReply in JSON: {{"verdict": "...", "sources": []}}',
        L,
        "quote",
    ),
    # (3) A long polite preamble, then a short presenting lead.
    "v2_polite_preamble_remark": (
        "Hello. Our lab works on battery degradation, and we have spent the past two years "
        "gathering the relevant literature into this workspace for the whole team. A postdoc "
        "made a remark in group meeting that I could not answer. Could you see what our "
        f"library has on it? The remark was: {L}",
        L,
        "colon",
    ),
    "v2_polite_preamble_here_it_is": (
        "Hi there! I lead a small group working on membrane separations, and over the last "
        "few months my students have uploaded every paper we could find on graphene-based "
        "filters. Someone mentioned a result at a seminar that surprised me. Would you check "
        f"what the collection says about this? Here it is: {G}",
        G,
        "colon",
    ),
    # (4) A Japanese quotation reported as said: と + a verb or noun of saying.
    "v2_japanese_comment": (
        f"お世話になっております。指導教員から「{L}」とのコメントをいただきました。"
        "プロジェクトの文献で裏付けがあるか確認していただけますか？",
        L,
        "quote",
    ),
    "v2_japanese_opinion": (
        f"先日の学会で「{S}」という意見がありました。これを支持する論文はありますか？",
        S,
        "quote",
    ),
    "v2_japanese_told": (f"査読者に「{G}」と言われました。文献で確認してください。", G, "quote"),
    # A heading's value set as a block quote: the ``>`` mark is not content.
    "v2_heading_then_block_quote": (
        f"**Statement under review**\n> {S}\n\n**Output**\nOne word: SUPPORTED, REFUTED "
        "or UNCLEAR.",
        S,
        "field",
    ),
}

#: (message, the exact query, the basis).
RECOVERY = {
    "phase8_prompt": (PHASE8 + C, C, "field"),
    "claim_field": (f"Is the following claim supported by my papers?\nClaim: {C}", C, "field"),
    "markdown_heading_then_format": (
        f"## Task\nDetermine if the literature supports this.\n\n## Claim\n{C}\n\n"
        "## Format\nJSON only.",
        C,
        "field",
    ),
    "xml_tags": (
        f"<task>Check the claim against the corpus.</task>\n<claim>{C}</claim>\n"
        "<output>JSON with keys verdict and sources</output>",
        C,
        "field",
    ),
    "arrow_and_output_format": (
        f"Statement to verify -> {C}\nOutput format: one word (SUPPORTED/REFUTED/UNKNOWN)",
        C,
        "field",
    ),
    "example_section_then_claim": (
        "Example:\nClaim: Smoking causes lung cancer.\nAnswer: SUPPORT\n\n"
        f"Now do this one.\nClaim: {C}\nAnswer:",
        C,
        "field",
    ),
    "repeated_label": (
        f"Claim: Smoking causes lung cancer.\nVerdict: SUPPORT\n\nClaim: {C}\nVerdict:",
        C,
        "field",
    ),
    "block_quote": (f"Is this supported by my papers?\n> {C}\nAnswer briefly.", C, "quote"),
    "curly_quote": (
        f"As a careful reviewer, assess: “{C}” Respond with SUPPORT, CONTRADICT or NEI only.",
        C,
        "quote",
    ),
    "japanese_label": (
        f"次の主張は私のプロジェクトの文献で裏付けられますか？JSONで答えてください。\n主張：{C}",
        C,
        "field",
    ),
    "japanese_quote": (
        f"「{C}」という主張について、ナレッジグラフの論文に基づいて判定してください。",
        C,
        "quote",
    ),
    "latin_claim_in_unmarked_japanese": (
        f"{C} これは正しいですか？根拠となる論文も教えてください。",
        C,
        "script",
    ),
    "check_against_my_kb": (f"Check this against my KB: {C}", C, "colon"),
    "claim_then_framing_question": (
        f"{C}\n\nIs this backed up by anything in the KB? Answer in one sentence.",
        C,
        "quote",  # a paragraph set apart from a framing one
    ),
    "q_and_a": (f"Q: Is it true that {A}?\nA (cite sources):", f"{A}?", "field"),
    "reported_speech": (
        f"My supervisor insists that {A}. I'm not convinced. "
        "What does the evidence in our knowledge base actually say?",
        f"{A}.",
        "segments",
    ),
    "would_you_say": (
        "Pretend you are an expert reviewer. Based solely on the indexed abstracts, "
        f"would you say {A}? Reply in Japanese.",
        f"{A}?",
        "segments",
    ),
    "fenced_template_then_claim": (
        "Evaluate the claim below using only my project's documents.\n"
        '```json\n{"verdict": "...", "evidence": []}\n```\n'
        f"Claim: {C}",
        C,
        "field",
    ),
    "polite_preamble_paragraphs": (
        "Hi! I'm a postdoc writing a review chapter, and I've loaded two hundred papers "
        "into this project. Could you take a look at this one for me?\n\n"
        f"{C}\n\n"
        "I'd really appreciate it if you could tell me whether the literature we have "
        "agrees. Thanks so much in advance!",
        C,
        "quote",
    ),
    **FOCUS_V2,
}

#: Plain questions built to trip the rules: framing words, scare quotes,
#: "In mice: ...", "Note: ...", "Context:/Question:", polite openers.
PLAIN = (
    "What evidence supports the claim that IL-6 drives insulin resistance?",
    "Does the knowledge graph embedding method TransE outperform RotatE on link prediction?",
    "Is JSON-LD used for metadata in the Materials Project database?",
    'Why is "dark matter halo bias" scale-dependent?',
    "In mice: does IL-6 knockout improve insulin sensitivity?",
    "Summarize the evidence on statin use and dementia risk.",
    "ゼオライトの細孔径はコーク生成に影響しますか？",
    "What do the papers say about coke formation in ZSM-5?",
    "Compare the support vector machine and random forest results for phase classification.",
    "Note: the samples were annealed at 500 C. How does annealing temperature affect grain size?",
    "Can you tell me how annealing affects grain size in copper thin films?",
    "Is the claim that statins reduce dementia risk supported by randomized trials?",
    "Context: we grow GaN on sapphire by MOCVD. Question: what buffer layer reduces "
    "threading dislocations?",
    "Please summarize what my papers say about perovskite stability under humidity.",
    "Does the citation network of the corpus show that review articles are cited more?",
    # 2026-10-11 wave H review: a quoted term or a Latin run inside a question
    # that keeps content of its own is not the question.
    'Do the papers support the "lattice oxygen mechanism" for NiFe LDH under alkaline '
    "OER conditions?",
    'Is the claim "graphene is impermeable to all gases" still valid for helium at high pressure?',
    'What does the literature say about "solid electrolyte interphase growth" in silicon anodes?',
    'Is the hypothesis "microglia drive synapse loss" supported in APP/PS1 mice '
    "older than 6 months?",
    "Is the statement 'entropy always increases' valid for open biological systems?",
    "「量子ドット」の発光効率は粒径にどう依存しますか？",
    "『ペロブスカイト太陽電池の効率』は、湿度85%の条件でどれくらい低下しますか？",
    "論文によると、perovskite solar cellsの湿度安定性はどう改善されますか？",
    "この論文の主張では、solid oxide fuel cellsの劣化は何が原因ですか？",
    # 2026-10-11 focus-v2 near misses: each looks like one of the four new
    # structures and is not.
    # A colon after a long personal clause that presents nothing (no request,
    # no pronoun pointing ahead; the tail is the question).
    "Before we buy a new MOCVD reactor for our lab, I want to know what limits the GaN "
    "growth rate on sapphire: is it the ammonia flow or the substrate temperature?",
    "We grow GaN on sapphire by MOCVD and our reference collection has many papers about "
    "it: which buffer layer reduces threading dislocations?",
    "In our lab we anneal copper films with the following protocol: how does the annealing "
    "temperature affect grain size?",
    "She said the furnace was held at 900 C for six hours: does that explain the cracking "
    "in our YSZ pellets?",
    # Real code, a log line and an error in a fence; "claim" as a modifier.
    "Why does this function return None for a claim?\n```python\ndef f(claim):\n"
    "    print(claim)\n```",
    "Is this statement correct?\n```\nx = np.mean(a, axis=0)\n```",
    "Why does this fail when I load the claim file?\n```\nConnection refused while "
    "reading the configuration file from disk\n```",
    "Can you explain this error from the claim parser?\n```\nERROR: the server returned "
    "status 503 while reading the config file.\n```",
    # 「term」 and という/といった naming, defining or listing, not reporting.
    "「量子ドット」という用語は何ですか？",
    "「量子ドット」という材料の発光効率は粒径にどう依存しますか？",
    "「量子ドット」という言葉は誰が最初に使いましたか？",
    "「ペロブスカイトや量子ドット」といった材料の発光効率は粒径にどう依存しますか？",
    "「エントロピーは常に増大する」という法則は開放系でも成り立ちますか？",
    "「ペロブスカイトは湿度に弱い」という説がありますが、封止すれば安定性は改善しますか？",
    "共同研究者から「量子ドット」について質問されましたが、発光効率は粒径にどう依存しますか？",
    # Reported speech that keeps a question of its own, quoted or not.
    "My supervisor says statins reduce dementia risk in older adults — is that right?",
    "My supervisor says statins reduce dementia risk in older adults. Is that right?",
    'My supervisor says "graphene is impermeable to all gases" — is that still valid for '
    "helium at high pressure?",
    'My colleague told me "zeolite pore size controls coke formation" — does that hold for '
    "SAPO-34 at 450 C?",
    # "code block" and "JSON" as the subject matter.
    "How do I convert a markdown code block into a JSON schema for crystallographic data?",
    "Which papers compare JSON and HDF5 for storing molecular dynamics trajectories?",
    "Why does the code block in our notebook render the claim text in monospace?",
    "What is the JSON schema used by the Materials Project API for band structures?",
    "Is a code block or a JSON file better for sharing simulation input decks?",
    "According to Smith et al., what limits the cycle life of silicon anodes?",
)

ADVERSARIAL = (
    "a: " * 1333,
    '"q" ' * 999,
    "Claim: x y z\n" * 300,
    "Please reply in JSON. " * 180,
    # A heading line with a long space run (the old heading pattern was cubic).
    "## Format" + " " * 3000 + "x\n" + C,
    "## Task <claim>" + (C + " ") * 4 + "</claim> please verify against my papers\nReply in JSON.",
    # focus-v2: one per new pattern, each reached on every run.
    # _NOT_PROSE_RE: sixteen named prose fences (the fence bound).
    "Check the claim in the code block.\n"
    + ("```\n" + "Lithium plating dominates " * 9 + "\n```\n") * 16,
    # _PROSE_WORD_RE: one hyphen run of a word that never closes.
    "Check the claim in the code block.\n```\nword " + "a-" * 290 + "\n```\n" + " x" * 1600,
    # _QUOTE_MARK_RE: a heading whose value is one long run of quote marks.
    "## Claim\n" + "> " * 1995,
    # _LEAD_CLAUSE_RE: sixteen presenting leads of joined clauses.
    ("Could you check it for me" + " and so" * 9 + ": a b c\n\n") * 40,
    # REPORT_BEFORE_RE: reporting verbs before every quotation.
    ('my PI says "' + C + '" ') * 50,
    # REPORT_AFTER_RE: "is what my" and filler after every quotation.
    ('"' + C + '" is what my ' + "a " * 30) * 30,
    # REPORT_JA_RE: a quotative と run before the verb.
    "共同研究者から「" + C + "」" + "と" * 3800 + "言われました。",
    # _KANJI_KANA_RE: a reporter of 3,800 kanji.
    "共同研究者" * 760 + "から「" + C + "」という指摘を受けました。",
    # _NEXT_WORD_RE: a long space run, then "claim" as a modifier 180 times.
    "Check the claim" + " " * 1900 + "file claim " * 180 + "\n```\nLithium plating dominates\n```",
)
ADVERSARIAL_IDS = (
    "colons",
    "quotes",
    "labels",
    "formats",
    "heading-space-run",
    "heading-task-tag",
    "v2-not-prose",
    "v2-prose-word",
    "v2-quote-mark",
    "v2-lead-clauses",
    "v2-report-before",
    "v2-report-after",
    "v2-report-ja",
    "v2-kanji-kana",
    "v2-next-word",
)


def _tokens(text: str) -> set[str]:
    return set(qf.content_tokens(text))


@pytest.fixture(autouse=True)
def _focus_on(monkeypatch):
    monkeypatch.setenv(qf.FOCUS_ENV, "on")


# ------------------------------------------------------------- recovery ---


@pytest.mark.parametrize(("message", "query", "basis"), RECOVERY.values(), ids=RECOVERY)
def test_the_marked_content_is_recovered_exactly(message, query, basis):
    focus = qf.focus_query(message)
    assert (focus.query, focus.basis) == (query, basis)


@pytest.mark.parametrize(("message", "query", "_basis"), RECOVERY.values(), ids=RECOVERY)
def test_every_content_token_of_the_claim_is_kept(message, query, _basis):
    assert _tokens(query) <= _tokens(qf.focus_query(message).query)


@pytest.mark.parametrize(
    ("message", "claim", "absent"),
    [
        (
            f"Hey! Quick sanity check — {C} Is that right according to my papers? "
            "Just give me yes/no plus the sources.",
            C,
            ("right", "papers", "sources", "give"),
        ),
        (
            "Using my KG 'perovskite humidity stability studies', is IL-6 signalling "
            "linked to insulin resistance in obese mice supported? Reply in JSON.",
            "IL-6 signalling linked to insulin resistance in obese mice",
            ("using", "kg", "reply", "json", "perovskite", "humidity", "studies"),
        ),
    ],
    ids=["dash_preamble", "named_kg_and_json"],
)
def test_free_prose_framing_is_cut_and_the_claim_kept(message, claim, absent):
    focus = qf.focus_query(message)
    assert focus.basis != "whole"
    assert _tokens(claim) <= _tokens(focus.query)
    assert not _tokens(focus.query) & set(absent)
    assert "perovskite humidity stability studies" not in focus.query


# ---------------------------------------------------------------- whole ---


@pytest.mark.parametrize("message", PLAIN)
def test_a_plain_question_is_searched_as_written(message):
    """I1: no framing, no change: the very string, byte for byte."""
    focus = qf.focus_query(message)
    assert focus.basis == "whole" and focus.query is message


def test_a_message_over_the_cap_is_searched_whole():
    message = (PHASE8 + C) + " " + "x" * qf.FOCUS_MAX_CHARS
    assert qf.focus_query(message) == qf.Focus(message, "whole")


def test_an_open_or_code_fence_makes_the_message_whole():
    code = f"Is this right?\n```python\nprint(1)\n```\nClaim: {C}"
    unclosed = f'Is this right?\n```json\n{{"verdict": "..."}}\nClaim: {C}'
    assert qf.focus_query(code).basis == "whole"
    assert qf.focus_query(unclosed).basis == "whole"


def test_the_kill_switch_searches_the_whole_message(monkeypatch):
    monkeypatch.setenv(qf.FOCUS_ENV, "off")
    assert qf.focus_query(PHASE8 + C) == qf.Focus(PHASE8 + C, "whole")
    assert not qf.focus_enabled()


@pytest.mark.parametrize("value", [None, "", "yes", "1", "auto"])
def test_focus_is_off_unless_switched_on(monkeypatch, value):
    """focus-v1 failed its held-out gate (2026-10-11), so it is opt-in."""
    if value is None:
        monkeypatch.delenv(qf.FOCUS_ENV, raising=False)
    else:
        monkeypatch.setenv(qf.FOCUS_ENV, value)
    assert not qf.focus_enabled()
    assert qf.focus_query(PHASE8 + C) == qf.Focus(PHASE8 + C, "whole")


@pytest.mark.parametrize("message", ["", "   ", None])
def test_nothing_to_focus_is_returned_as_given(message):
    assert qf.focus_query(message) == qf.Focus(message, "whole")


# ------------------------------------------------------------ properties ---


@pytest.mark.parametrize(
    "message",
    [message for message, _, _ in RECOVERY.values()] + list(PLAIN),
)
def test_focus_is_idempotent(message):
    once = qf.focus_query(message).query
    assert qf.focus_query(once).query == once


@pytest.mark.parametrize(
    "message",
    ADVERSARIAL,
    ids=ADVERSARIAL_IDS,
)
def test_an_adversarial_message_is_bounded(message):
    """3,999 characters of marks: each finder examines at most MAX_MARKS
    marks and MAX_SEGMENTS segments (the prototype took 2 s on ``a: ``)."""
    message = message[:3999]
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        qf.focus_query(message)
        timings.append(time.perf_counter() - started)
    assert statistics.median(timings) < 0.05


def test_meta_ja_is_declared_longest_first():
    """An alternation takes the first entry that matches: a word must come
    before its own prefix."""
    lengths = [len(word) for word in qf.META_JA]
    assert lengths == sorted(lengths, reverse=True)


def test_the_rules_digest_names_the_rules():
    """A result records the rules it was measured with; a lexicon or
    threshold edit changes the digest and needs a new FOCUS_RULES_VERSION
    (and, after a held-out run, a new held-out set)."""
    digest = qf.rules_digest()
    assert re.fullmatch(r"[0-9a-f]{64}", digest) and digest == qf.rules_digest()
    assert (qf.FOCUS_RULES_VERSION, digest) == (
        "focus-v2",
        "90f6de2fe3d982f04bb45bc491b96cb5b8024f9739f70ccc2b5d5834ac244713",
    )


#: Every lexicon, threshold and pattern focus-v2 added.
FOCUS_V2_RULES = (
    "PROSE_INFOS",
    "QUOTED_OBJECTS",
    "PREPOSITIONS",
    "REQUEST_ROLES",
    "FORWARD_REFS",
    "REQUEST_WORDS",
    "BACK_REFS",
    "REPORT_VERBS_EN",
    "PROPOSITION_NOUNS_JA",
    "REPORT_VERBS_JA",
    "_QUOTE_OPENERS",
    "_QUOTE_CLOSERS",
    "PROSE_MAX_CHARS",
    "PROSE_MIN_WORDS",
    "PROSE_WORD_SHARE",
    "REPORT_WINDOW",
    "REPORTER_MAX",
    "REPORTER_MAX_JA",
    "COMMENT_MAX",
    "COMMENT_MAX_JA",
    "_NOT_PROSE_RE",
    "_PROSE_WORD_RE",
    "_QUOTE_MARK_RE",
    "_LEAD_CLAUSE_RE",
    "REPORT_BEFORE_RE",
    "REPORT_AFTER_RE",
    "REPORT_JA_RE",
    "_KANJI_KANA_RE",
    "_NEXT_WORD_RE",
)


def _edited(value):
    if isinstance(value, re.Pattern):
        return re.compile(value.pattern + "(?:x)?")
    if isinstance(value, (tuple, str)):
        return value + (("x",) if isinstance(value, tuple) else "x")
    return value + 1


@pytest.mark.parametrize("name", FOCUS_V2_RULES)
def test_the_rules_digest_covers_every_focus_v2_rule(monkeypatch, name):
    before = qf.rules_digest()
    monkeypatch.setattr(qf, name, _edited(getattr(qf, name)))
    assert qf.rules_digest() != before


def test_without_template_fences_drops_only_an_output_template():
    template = 'Answer as:\n```json\n{"verdict": "SUPPORT | CONTRADICT", "evidence": []}\n```'
    code = "Why?\n```python\nprint(1)\n```"
    data = 'Is this sensible?\n```json\n{"lr": 0.001, "epochs": 20}\n```'
    assert "```" not in qf.without_template_fences(template)
    assert qf.without_template_fences(code) == code
    assert qf.without_template_fences(data) == data


def test_without_template_fences_drops_the_marks_of_a_named_prose_fence():
    """focus-v2: a plain fence holding one paragraph the message names (``the
    claim in the code block``) is a quotation, not code, so C12 does not skip
    the turn; a fence of prose the message does not name stays."""
    named = f"Evaluate the claim in the code block.\n```\n{L}\n```"
    unnamed = f"What does this output mean?\n```\n{L}\n```"
    modifier = "Why does the claim file fail?\n```\nConnection refused while reading the file\n```"
    assert "```" not in qf.without_template_fences(named)
    assert L in qf.without_template_fences(named)
    assert qf.without_template_fences(unnamed) == unnamed
    assert qf.without_template_fences(modifier) == modifier


# -------------------------------------------------------- focus-v2 edges ---


def test_subtraction_never_drops_an_impersonal_statement():
    """focus-v2 (3): a short claim after a long personal preamble, with no
    colon, is kept by subtraction however much more the preamble says
    (focus-v1 kept the preamble and dropped the claim: half the substance of
    the longest segment was its bar)."""
    claim = "Silicon anodes crack above 300 nm."
    message = (
        "Hello. Our lab works on battery degradation, and we have spent the past two years "
        "gathering the relevant literature into this workspace for the whole team. Could you "
        f"see what our library has on this? {claim}"
    )
    focus = qf.focus_query(message)
    assert focus.basis == "segments"
    assert _tokens(claim) <= _tokens(focus.query)


@pytest.mark.parametrize(
    "message",
    [
        # The presented text is the context of a question that follows it.
        f"Let me give you some background on it: {L}\n\nWhich anode binder delays plating "
        "the most under these conditions?",
        f"Which anode binder delays plating the most? Let me give you some background on it: {L}",
        # The lead names a topic the presented text leans on.
        f"I'm studying zeolite deactivation and I want you to check this against our papers "
        f"for me: {L}",
        # "this" determines a noun; it does not point ahead.
        f"I'm studying this alloy and I want you to look for me: {L}",
    ],
    ids=["question-after", "question-before", "topic-clause", "determiner-this"],
)
def test_a_lead_that_presents_context_or_a_topic_keeps_the_message_whole(message):
    """focus-v2 (1): a colon after a personal lead presents the thing to
    search only when the lead asks, points ahead with a pronoun, names no
    topic of its own, and nothing else in the message asks a question."""
    assert qf.focus_query(message) == qf.Focus(message, "whole")


def test_a_list_after_to_itta_is_no_reported_speech():
    """focus-v2 (4): 「A や B」といった (such as) introduces a list, not a
    report, so the quotation is not taken as the claim."""
    message = "「ペロブスカイトや量子ドット」といった材料について、文献を教えてください。"
    assert qf.focus_query(message).basis != "quote"
