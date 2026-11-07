"""ADR-093 D-9 (C24): a shown passage's source name is its citation.

Measured 2026-10-10 (SciFact dev, companion with the router on,
llama3.2:3b): the small reader cites by the source name the pack header
shows (``"evidence": ["scifact-18617259.md"]``), and the footer said the
answer cites no passage. It also gave one label to several names
(``[S1] scifact-19058822.md`` where that name was S2), which the footer
reported only as numbers missing from S1.

A filename-like name of a passage shown THIS turn, matched exactly and
word-bounded, cites that passage. A label written against another shown
passage's name cites the name's passage and is reported as a mislabel. The
digits inside a matched name are never checked as numbers. Prose words and
names the turn did not show are never citations.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from r4_contracts.retrieval import RetrievedPassage

from r4.companion import citation_check as cc
from r4.companion.evidence_ledger import EvidenceLedger

QUESTION = "Does vitamin D reduce hip fractures in older women?"
PASSAGES = (
    (
        "scifact-14920021.md",
        "Vitamin D supplementation reduced hip fracture risk by 20% in women over 65.",
    ),
    (
        "scifact-19058822.md",
        "Hip fracture incidence did not change with vitamin D in a cohort of older women.",
    ),
    (
        "scifact-27768226.md",
        "Calcium and vitamin D together lowered fracture rates among older women.",
    ),
)
SHOWN = ("S1", "S2", "S3")


def _ledger(passages=PASSAGES, turn_id: str = "t1", ledger: EvidenceLedger | None = None):
    ledger = ledger or EvidenceLedger()
    start = len(ledger) + 1
    shown = [
        RetrievedPassage(
            rank=i,
            source_id=f"node-{start + i}",
            artifact_id=f"art-{start + i}",
            source_name=name,
            text=text,
        )
        for i, (name, text) in enumerate(passages)
    ]
    citations = {f"[S{start + i}]": p.source_id for i, p in enumerate(shown)}
    ledger.record(citations, shown, turn_id=turn_id, status="sufficient")
    return ledger


def _report(answer: str, ledger: EvidenceLedger | None = None, shown=SHOWN):
    return cc.check_answer(answer, ledger or _ledger(), question=QUESTION, shown=shown)


def test_a_bare_source_name_cites_its_passage():
    report = _report('{"verdict": "SUPPORT", "evidence": ["scifact-14920021.md"]}')

    assert report.cited_sids == ("S1",)
    footer = cc.footer_suffix(report, _ledger())
    assert "cites no passage" not in footer
    assert "Sources: [S1] scifact-14920021.md" in footer


def test_a_label_written_against_another_passages_name_is_a_mislabel():
    report = _report("Incidence did not change [S1] scifact-19058822.md.")

    assert report.cited_sids == ("S2",)
    assert report.mislabels == (("S1", "S2"),)
    assert report.missing_numbers == ()
    assert "[S1] for [S2]" in cc.footer_suffix(report, _ledger())


def test_the_measured_reply_cites_all_three_and_names_both_mislabels():
    reply = (
        '{"verdict": "CONTRADICT", "evidence": ["[S1] scifact-14920021.md", '
        '"[S1] scifact-19058822.md", "[S1] scifact-27768226.md"]}'
    )
    report = _report(reply)

    assert set(report.cited_sids) == {"S1", "S2", "S3"}
    assert report.mislabels == (("S1", "S2"), ("S1", "S3"))
    assert report.missing_numbers == ()
    assert report.summary()["mislabels"] == [["S1", "S2"], ["S1", "S3"]]


def test_a_label_that_agrees_with_its_name_is_no_mislabel():
    report = _report("Incidence did not change [S2] scifact-19058822.md.")

    assert report.cited_sids == ("S2",)
    assert report.mislabels == ()


def test_digits_inside_a_matched_name_are_not_checked_as_numbers():
    report = _report("Risk fell by 20% in scifact-14920021.md.")

    assert report.cited_sids == ("S1",)
    assert report.missing_numbers == ()


def test_a_number_outside_the_name_is_still_checked():
    report = _report("Risk fell by 45% in scifact-14920021.md.")

    assert report.missing_numbers == ("45",)


@pytest.mark.parametrize(
    "sentence",
    [
        "See scifact-1492002 for details.",  # a prefix of a shown name
        "See xscifact-14920021.md for details.",  # inside a longer token
        "See scifact-14920021.mdx for details.",
        "See SCIFACT-14920021.MD for details.",  # names match exactly
    ],
)
def test_partial_or_altered_names_are_not_citations(sentence):
    assert _report(sentence).sentences == ()


def test_a_name_the_turn_did_not_show_is_not_a_citation():
    ledger = _ledger((("earlier-notes.md", "An earlier turn's passage."),), turn_id="t0")
    ledger = _ledger(turn_id="t1", ledger=ledger)  # S2-S4 this turn

    report = cc.check_answer(
        "As in earlier-notes.md, risk fell.", ledger, question=QUESTION, shown=("S2", "S3", "S4")
    )

    assert report.sentences == ()


def test_a_plain_word_name_is_never_matched_in_prose():
    passages = (("Introduction", "Vitamin D reduced hip fracture risk in older women."),)

    report = _report("The Introduction says risk fell.", _ledger(passages), shown=("S1",))

    assert report.sentences == ()


def test_names_are_read_even_where_bare_numbers_are_not():
    report = cc.check_answer(
        "Risk fell in scifact-14920021.md.",
        _ledger(),
        question=QUESTION,
        shown=SHOWN,
        bare_numbers=False,
    )

    assert report.cited_sids == ("S1",)


def test_the_check_line_names_each_mislabel():
    reply = (
        '{"verdict": "CONTRADICT", "evidence": ["[S1] scifact-14920021.md", '
        '"[S1] scifact-19058822.md", "[S1] scifact-27768226.md"]}'
    )

    footer = cc.footer_suffix(_report(reply), _ledger())

    assert "label mismatches: [S1] for [S2], [S1] for [S3]." in footer
    assert "cites no passage" not in footer


def test_a_mislabel_alone_flags_the_answer():
    report = _report(
        "Hip fracture incidence did not change with vitamin D [S1] scifact-19058822.md."
    )

    assert report.count("supported") == 1
    assert (report.phantom, report.missing_numbers, report.quote_mismatches) == ((), (), 0)
    assert report.flagged


def test_a_mislabel_is_reported_once_per_answer():
    answer = "Incidence did not change [S1] scifact-19058822.md. Again [S1] scifact-19058822.md."

    assert _report(answer).mislabels == (("S1", "S2"),)


def test_the_footer_names_at_most_four_mislabels():
    passages = tuple((f"doc-{i}.md", f"Vitamin D finding number {i}.") for i in range(1, 7))
    ledger = _ledger(passages)
    labelled = ", ".join(f'"[S1] doc-{i}.md"' for i in range(1, 7))
    shown = tuple(f"S{i}" for i in range(1, 7))

    report = _report(f'{{"evidence": [{labelled}]}}', ledger, shown=shown)
    footer = cc.footer_suffix(report, ledger)

    assert len(report.mislabels) == 5
    assert "[S1] for [S5]" in footer and "[S1] for [S6]" not in footer
    assert len(footer) <= cc.FOOTER_MAX_CHARS


def test_a_phantom_label_before_a_shown_name_is_a_mislabel():
    report = _report("Incidence did not change [S9] scifact-19058822.md.")

    assert report.cited_sids == ("S2",)
    assert report.phantom == ()
    assert report.mislabels == (("S9", "S2"),)


def test_a_label_opening_a_line_before_a_name_stays_with_the_name():
    """The pack header's form, copied into a list: the label is the name's,
    not the line before's."""
    answer = (
        "Vitamin D lowered hip fracture risk [S1].\n"
        "- [S1] scifact-19058822.md: incidence did not change.\n"
        "- [S3] scifact-27768226.md: calcium and vitamin D lowered fracture rates."
    )

    report = _report(answer)

    assert [check.cited for check in report.sentences] == [("S1",), ("S2",), ("S3",)]
    assert report.mislabels == (("S1", "S2"),)


def test_a_name_two_shown_passages_share_cites_both():
    passages = (
        ("report-1.pdf", "Vitamin D reduced hip fracture risk in older women."),
        ("report-1.pdf", "Hip fracture incidence did not change in a second cohort."),
        ("notes_2.md", "Calcium lowered fracture rates."),
    )
    ledger = _ledger(passages)

    agrees = _report("Risk fell [S2] report-1.pdf.", ledger)
    disagrees = _report("Risk fell [S3] report-1.pdf.", ledger)

    assert set(agrees.cited_sids) == {"S1", "S2"} and agrees.mislabels == ()
    assert disagrees.mislabels == (("S3", "S1"), ("S3", "S2"))


def test_the_longer_of_two_overlapping_names_wins():
    passages = (
        ("Report 2020.pdf", "Vitamin D reduced hip fracture risk in older women."),
        ("My Report 2020.pdf", "Vitamin D reduced hip fracture risk in older women."),
    )

    report = _report("Risk fell in My Report 2020.pdf.", _ledger(passages), shown=("S1", "S2"))

    assert report.cited_sids == ("S2",)


def test_a_label_inside_a_matched_name_is_not_read_as_a_label():
    passages = (
        ("S2-protocol.md", "Vitamin D was dosed at 800 IU daily in older women."),
        ("scifact-19058822.md", "Hip fracture incidence did not change."),
    )

    report = _report(
        "The dose is set out in S2-protocol.md.", _ledger(passages), shown=("S1", "S2")
    )

    assert report.cited_sids == ("S1",)


def test_a_name_inside_a_path_is_not_a_citation():
    assert _report("See corpus/scifact-14920021.md for details.").sentences == ()


def test_the_source_form_still_finds_an_earlier_turns_name():
    ledger = _ledger((("earlier-notes.md", "An earlier turn's passage."),), turn_id="t0")
    ledger = _ledger(turn_id="t1", ledger=ledger)

    report = cc.check_answer(
        "Risk fell (Source: earlier-notes.md).", ledger, question=QUESTION, shown=("S2", "S3", "S4")
    )

    assert report.cited_sids == ("S1",)


def test_a_matched_name_lends_the_sentence_no_support():
    """The name is a marker: its words are stripped before support is scored,
    as its digits are before numbers are."""
    passages = (
        ("vitamin-d-hip-fracture-trial.pdf", "Vitamin D reduced hip fracture risk in women."),
    )

    report = _report(
        "Statins lowered cholesterol in vitamin-d-hip-fracture-trial.pdf.",
        _ledger(passages),
        shown=("S1",),
    )

    assert [check.verdict for check in report.sentences] == ["unsupported"]


# ------------------------------------------------ review fixes (2026-10-11) ---


@pytest.mark.parametrize(
    ("answer", "cited"),
    [
        ("Statins cured cancer. [S1] scifact-14920021.md.", ("S1",)),
        ("Statins cured cancer.\n[S1] scifact-14920021.md", ("S1",)),
        ("Statins cured cancer.\n- [S1] scifact-14920021.md", ("S1",)),
        ("Statins lowered cholesterol in older men. [S2] scifact-19058822.md", ("S2",)),
        ("Statins cured cancer. scifact-14920021.md", ("S1",)),
        ("Statins cured cancer.\nSource: scifact-14920021.md", ("S1",)),
        ("Statins cured cancer.\nSources: [S1]", ("S1",)),
    ],
)
def test_a_trailing_citation_is_checked_with_the_claim_before_it(answer, cited):
    """c24-1, c24-2: a piece of nothing but citations joins the sentence before
    it. Alone it has no terms, takes the question's, and reads 'supported'
    while the claim it cites goes unchecked."""
    report = _report(answer)

    assert [(check.cited, check.verdict) for check in report.sentences] == [(cited, "unsupported")]
    assert report.flagged
    assert "0 of 1 cited sentences supported" in cc.footer_suffix(report, _ledger())


def test_strict_downgrades_a_claim_whose_only_citation_trails_it():
    answer = "The evidence supports this: statins cured cancer.\n- [S1] scifact-14920021.md"

    report = _report(answer)

    assert not report.answer_supported
    assert cc.downgrade(answer, report).startswith(cc.STRICT_NOTE)


def test_a_reference_line_joined_to_its_claim_still_reports_its_mislabel():
    answer = "Vitamin D reduced hip fracture risk by 20% [S1].\n- [S1] scifact-19058822.md"

    report = _report(answer)

    assert [check.cited for check in report.sentences] == [("S1", "S2")]
    assert report.mislabels == (("S1", "S2"),)


def test_a_label_after_an_inline_full_stop_cites_the_sentence_before_a_name():
    """c24-split: in-line, a label after a full stop cites the sentence before
    it (``X rose. [S1] Y fell.``), even when the next sentence opens with a
    shown name; only a label opening a line belongs to the name after it."""
    answer = (
        "Vitamin D supplementation reduced hip fracture risk by 20%. "
        "[S1] scifact-19058822.md, by contrast, found no change [S2]."
    )

    report = _report(answer)

    assert [check.cited for check in report.sentences] == [("S1",), ("S2",)]
    assert report.sentences[0].verdict == "supported"
    assert report.mislabels == ()


def test_strict_keeps_a_supported_claim_whose_label_precedes_a_name():
    answer = (
        "SUPPORT. Vitamin D supplementation reduced hip fracture risk by 20% in women "
        "over 65. [S1] scifact-19058822.md is about something else entirely."
    )

    assert cc.downgrade(answer, _report(answer)) == answer


SMITH = (
    (
        "Smith J. 2019 vitamin D trial.pdf",
        "Vitamin D reduced hip fracture risk by 20% in older women.",
    ),
)


@pytest.mark.parametrize(
    "answer",
    [
        "Risk fell by 20% in Smith J. 2019 vitamin D trial.pdf.",
        '{"verdict": "SUPPORT", "evidence": ["Smith J. 2019 vitamin D trial.pdf"]}',
    ],
)
def test_a_sentence_end_inside_a_shown_name_does_not_split_it(answer):
    """c24-3: an author/title file name with ``. `` in it is one citation."""
    report = _report(answer, _ledger(SMITH), shown=("S1",))

    assert report.cited_sids == ("S1",)
    assert report.missing_numbers == ()


@pytest.mark.parametrize(
    "answer",
    [
        "リスクはscifact-14920021.mdで低下した。",
        "資料scifact-14920021.mdは、リスク低下を示す。",
        "scifact-14920021.mdによると、ビタミンDで骨折リスクが20%低下した。",
        "骨折リスクは低下した[S1]。",
    ],
)
def test_a_citation_written_against_japanese_text_is_read(answer):
    """c24-4: Japanese puts no space before or after a file name or a label;
    a CJK character bounds a Latin token."""
    assert _report(answer).cited_sids == ("S1",)


def test_a_cjk_edged_name_still_needs_a_token_boundary():
    ledger = _ledger((("論文_2024.md", "ビタミンDは骨折リスクを下げた。"),))

    assert _report("大論文_2024.mdを参照。", ledger, shown=("S1",)).sentences == ()
    assert _report("「論文_2024.md」を参照。", ledger, shown=("S1",)).cited_sids == ("S1",)


@pytest.mark.parametrize(
    "answer",
    [
        "Incidence did not change [S1] `scifact-19058822.md`.",
        "Incidence did not change [S1] **scifact-19058822.md**.",
        "Incidence did not change [S1](scifact-19058822.md).",
        "Incidence did not change [S1]：scifact-19058822.md。",
        "Incidence did not change [S1]-scifact-19058822.md.",
    ],
)
def test_a_label_against_a_wrapped_or_joined_name_is_a_mislabel(answer):
    """c24-5: markdown around the name, a full-width colon or an unspaced
    hyphen between label and name still bind them."""
    report = _report(answer)

    assert report.cited_sids == ("S2",)
    assert report.mislabels == (("S1", "S2"),)
    assert report.missing_numbers == ()


@pytest.mark.parametrize("join", [" — ", " - ", ": "])
def test_a_spaced_dash_or_colon_after_a_clause_citation_is_no_mislabel(join):
    """c24-mislabel-dash-colon: mid-clause, a dash or colon after a label
    starts a new clause; the label is the first clause's citation."""
    answer = (
        f"Vitamin D supplementation reduced hip fracture risk by 20% [S1]{join}"
        "scifact-19058822.md, by contrast, found no change [S2]."
    )

    report = _report(answer)

    assert report.cited_sids == ("S1", "S2")
    assert report.mislabels == ()
    assert report.missing_numbers == ()


@pytest.mark.parametrize(
    "answer",
    [
        '{"verdict": "CONTRADICT", "evidence": ["[S1] — scifact-19058822.md"]}',
        "Incidence did not change.\n[S1]: scifact-19058822.md",
    ],
)
def test_a_dash_or_colon_binds_a_label_that_opens_its_segment(answer):
    """A JSON string or a reference line: there the label is the name's."""
    report = _report(answer)

    assert report.cited_sids == ("S2",)
    assert report.mislabels == (("S1", "S2"),)


def test_an_earlier_turns_label_for_the_same_document_is_no_mislabel():
    """c24-mislabel-cross-turn: labels are session-monotonic, so a follow-up
    turn shows the same document as S2 while the answer still writes S1."""
    earlier = (("report-2020.pdf", "Vitamin D reduced hip fracture risk by 20% in older women."),)
    ledger = _ledger(earlier, turn_id="t0")
    later = (
        ("report-2020.pdf", "Methods: women over 65 were randomised to vitamin D or placebo."),
        ("other-study.md", "Calcium lowered fracture rates."),
    )
    ledger = _ledger(later, turn_id="t1", ledger=ledger)  # S2, S3

    report = cc.check_answer(
        "Vitamin D reduced hip fracture risk by 20% in older women [S1] report-2020.pdf.",
        ledger,
        question=QUESTION,
        shown=("S2", "S3"),
    )

    assert report.mislabels == ()
    assert report.cited_sids == ("S1", "S2")
    assert report.missing_numbers == ()
    assert "label mismatches" not in cc.footer_suffix(report, ledger)


@pytest.mark.parametrize("name", ["COVID-19", "SARS-CoV-2", "Phase 3", "Table 1", "Smith 2021"])
def test_a_title_like_name_is_never_matched_in_prose(name):
    """c24-title-fallback: UMRS falls back to the artifact title, so a name
    may be a prose term; it needs an extension or an identifier shape."""
    passages = ((name, "Vaccination rates among older adults rose in 2021."), PASSAGES[1])
    answer = (
        "Vitamin D did not change hip fracture incidence [S2]. "
        f"During {name}, fracture admissions fell by 30%."
    )

    report = _report(answer, _ledger(passages), shown=("S1", "S2"))

    assert report.cited_sids == ("S2",)
    assert report.missing_numbers == ()


def test_an_uncited_answer_naming_a_title_like_source_cites_no_passage():
    ledger = _ledger((("COVID-19", "Vaccination rates among older adults rose in 2021."),))

    report = _report(
        "COVID-19 lockdowns reduced hip fracture admissions among older adults.",
        ledger,
        shown=("S1",),
    )

    assert "cites no passage" in cc.footer_suffix(report, ledger)


@pytest.mark.parametrize("name", ["scifact-14920021", "notes_v2", "NCT01234567"])
def test_an_identifier_without_an_extension_still_cites(name):
    passages = ((name, "Vitamin D reduced hip fracture risk in older women."),)

    report = _report(f"Risk fell in {name}.", _ledger(passages), shown=("S1",))

    assert report.cited_sids == ("S1",)


def test_a_long_one_line_answer_of_names_is_checked_in_linear_time():
    """c24-quadratic: a small reader looping on a JSON evidence list writes
    one 80k-character line; the check runs synchronously after the turn.
    Linear, it takes about 0.1 s; quadratic, several seconds."""
    passages = tuple((f"d-{i}.md", f"Vitamin D finding {i}.") for i in range(1, 51))
    ledger = _ledger(passages)
    shown = tuple(f"S{i}" for i in range(1, 51))
    block = ", ".join(f"[S{i % 50 + 1}] d-{i}.md" for i in range(1, 51))
    answer = ", ".join([block] * 100)

    started = time.perf_counter()
    report = cc.check_answer(answer, ledger, question=QUESTION, shown=shown)
    elapsed = time.perf_counter() - started

    assert len(answer) > 70_000
    assert len(report.mislabels) == 50
    assert elapsed < 1.5


# Wave D seam (2026-10-11): the session hook, the path a companion turn takes.
# knowledge_query numbers its hits, so a turn that called it reads no bare
# [n]; the names the turn was shown still cite, and the mislabels reach the
# footer and the session's record.


@pytest.mark.parametrize("turn_tools", [(), ("knowledge_query",)])
def test_a_companion_turn_reports_its_mislabels_through_the_session_hook(turn_tools):
    session = SimpleNamespace(
        _evidence_ledger=_ledger(),
        _current_turn_id="t1",
        _turn_tool_names=list(turn_tools),
        metadata={},
        agent=SimpleNamespace(_emit=lambda *_args: None),
    )
    reply = (
        '{"verdict": "CONTRADICT", "evidence": ["[S1] scifact-14920021.md", '
        '"[S1] scifact-19058822.md", "[S1] scifact-27768226.md"]}'
    )

    out = cc.check_session_turn(session, QUESTION, reply)

    record = session.metadata[cc.METADATA_KEY]
    assert record["bare_numbers"] is not bool(turn_tools)
    assert record["mislabels"] == [["S1", "S2"], ["S1", "S3"]]
    assert out.startswith(reply)
    assert "label mismatches: [S1] for [S2], [S1] for [S3]." in out
