"""ADR-093 C0 eval rail — the metrics, the prompts, the arms and the offline
replica stay honest without a stack or a model call."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks" / "retrieval"))

import arms  # noqa: E402
import backends  # noqa: E402
import comparisons  # noqa: E402
import infer  # noqa: E402
import metrics  # noqa: E402
import offline_lanes  # noqa: E402
import records  # noqa: E402
import run  # noqa: E402
import scifact  # noqa: E402
import server_probe  # noqa: E402

from r4.errors import (  # noqa: E402
    APIError,
    AuthenticationError,
    R4ConnectionError,
    R4TimeoutError,
    RateLimitError,
)
from r4_contracts.context_pack import (  # noqa: E402
    NOT_COVERED,
    estimate_tokens,
    instruction_text,
)
from r4_contracts.models import RETRIEVAL_PROFILES  # noqa: E402
from r4_contracts.retrieval import RetrievedPassage, RetrieveResult  # noqa: E402


def test_ranking_metrics_on_a_known_ranking():
    ranked = ["a", "x", "b", "y"]
    scores = metrics.retrieval_scores(ranked, {"a", "b"})
    assert scores["recall@1"] == 0.5
    assert scores["recall@3"] == 1.0
    assert scores["mrr@10"] == 1.0
    assert 0.9 < scores["ndcg@10"] < 1.0


def test_chunks_of_one_document_count_once():
    scores = metrics.retrieval_scores(["x", "x", "x", "a"], {"a"})
    assert scores["recall@1"] == 0.0 and scores["mrr@10"] == 0.5


def test_paired_delta_sees_a_real_improvement_and_no_change():
    better = metrics.paired_delta([0.0] * 50 + [1.0] * 50, [1.0] * 100)
    assert better["delta"] == 0.5 and better["p_not_better"] == 0.0
    same = metrics.paired_delta([1.0, 0.0] * 50, [1.0, 0.0] * 50)
    assert same["delta"] == 0.0 and same["p_not_better"] == 1.0


def test_macro_f1_is_zero_for_a_constant_wrong_answer():
    gold = ["SUPPORT", "CONTRADICT", "NOT_ENOUGH_INFO"]
    assert metrics.macro_f1(gold, ["SUPPORT"] * 3, scifact.LABELS) == pytest.approx(1 / 6)


@pytest.mark.parametrize(
    ("reply", "verdict", "ok"),
    [
        ('{"verdict": "SUPPORT", "evidence": [1]}', "SUPPORT", True),
        ('Sure! {"verdict": "refutes", "evidence": ["scifact-12.md"]} done', "CONTRADICT", True),
        ('{"verdict": "Not enough info"}', "NOT_ENOUGH_INFO", True),
        ("The passages support the claim.", "SUPPORT", False),
        ("There is not enough information to support this.", "NOT_ENOUGH_INFO", False),
        ("", "NOT_ENOUGH_INFO", False),
    ],
)
def test_verdict_parsing_is_lenient_but_never_invents_a_verdict(reply, verdict, ok):
    got, _evidence, parsed = infer.parse(reply)
    assert (got, parsed) == (verdict, ok)


def test_the_claim_goes_after_the_context():
    message = infer.user_message("CO2 is a gas.", "[1] source scifact-1.md\ntext")
    assert message.index("CONTEXT:") < message.index("CLAIM: CO2 is a gas.")


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_corpus_holds_every_cited_doc_plus_seeded_distractors(tmp_path):
    _write(
        tmp_path / "claims_dev.jsonl",
        [
            {
                "id": 1,
                "claim": "c1",
                "evidence": {"10": [{"label": "SUPPORT"}]},
                "cited_doc_ids": [10],
            },
            {"id": 2, "claim": "c2", "evidence": {}, "cited_doc_ids": [11]},
        ],
    )
    _write(
        tmp_path / "corpus.jsonl",
        [{"doc_id": d, "title": f"T{d}", "abstract": ["s1", "s2"]} for d in range(10, 30)],
    )
    corpus = scifact.load(tmp_path, distractors=5)
    assert {"10", "11"} <= set(corpus.docs) and len(corpus.docs) == 7
    assert [c.label for c in corpus.claims] == ["SUPPORT", "NOT_ENOUGH_INFO"]
    again = scifact.load(tmp_path, distractors=5)
    assert set(again.docs) == set(corpus.docs)  # seeded
    paths = scifact.write_docs(corpus, tmp_path / "docs")
    assert scifact.doc_id_of(paths[0].name) in corpus.docs
    assert paths[0].read_text().startswith("# T")


def test_doc_ids_are_read_back_from_names_only_when_they_are_scifact():
    assert scifact.doc_id_of("s3://b/projects/default/scifact-4983.md") == "4983"
    assert scifact.doc_id_of("notes-4983.md") is None
    assert scifact.doc_id_of("scifact-abc.md") is None


def test_context_scores_measure_what_reached_the_model():
    assert metrics.context_scores(["a", "x", "y"], {"a"}) == {
        "ctx_precision": 1 / 3,
        "ctx_recall": 1.0,
        "ctx_recall_all": 1.0,
        "passages": 3.0,
    }
    assert metrics.context_scores([], {"a"})["ctx_recall"] == 0.0


def test_mcnemar_is_exact_and_symmetric():
    assert metrics.mcnemar([(True, True)] * 5) == {"b": 0, "c": 0, "p": 1.0}
    one_way = metrics.mcnemar([(False, True)] * 10)
    assert one_way["c"] == 10 and one_way["p"] < 0.01
    balanced = metrics.mcnemar([(True, False), (False, True)] * 5)
    assert balanced["p"] == 1.0


def _tiny_scifact(tmp_path: Path) -> Path:
    _write(
        tmp_path / "claims_dev.jsonl",
        [{"id": 1, "claim": "c1", "evidence": {}, "cited_doc_ids": [10]}],
    )
    _write(
        tmp_path / "claims_train.jsonl",
        [
            {
                "id": 7,
                "claim": "t",
                "evidence": {"11": [{"label": "CONTRADICT"}]},
                "cited_doc_ids": [11],
            }
        ],
    )
    _write(
        tmp_path / "corpus.jsonl",
        [{"doc_id": d, "title": f"T{d}", "abstract": ["s"]} for d in range(10, 20)],
    )
    return tmp_path


def test_the_tune_set_over_the_full_corpus(tmp_path):
    full = scifact.load(_tiny_scifact(tmp_path), distractors=None, claims="tune")
    assert len(full.docs) == 10
    assert [c.label for c in full.claims] == ["CONTRADICT"]


def test_nonsense_probes_are_out_of_kb(tmp_path):
    nonsense = scifact.claims_for(_tiny_scifact(tmp_path), "nonsense")
    assert len(nonsense) == 30 and {c.label for c in nonsense} == {"NOT_ENOUGH_INFO"}
    assert all(c.id > 900000 and not c.evidence for c in nonsense)


def test_the_legacy_prompt_asks_the_question_first():
    system, build = infer.PROMPTS["legacy"]
    message = build("CO2 is a gas.", "[1] scifact-1.md\ntext")
    assert message.index("Question:") < message.index("Context:")
    assert "knowledge base does not answer it" in system


# ── sufficiency metrics ──────────────────────────────────────────────────────


def test_auroc_is_the_mann_whitney_probability_with_ties_half():
    assert metrics.auroc([3.0, 4.0], [1.0, 2.0]) == 1.0
    assert metrics.auroc([1.0, 2.0], [3.0, 4.0]) == 0.0
    assert metrics.auroc([1.0, 1.0], [1.0]) == 0.5
    # 1 > 0 and 1 == 1 (half) out of 2 pairs, 3 beats both: (1.5 + 2) / 4
    assert metrics.auroc([1.0, 3.0], [0.0, 1.0]) == pytest.approx(0.875)
    assert metrics.auroc([], [1.0]) is None and metrics.auroc([1.0], []) is None


def test_sufficiency_gates_read_nonsense_and_in_kb_evidence():
    report = metrics.sufficiency_report(
        {
            "nonsense": ["insufficient"] * 9 + ["partial"],
            "evidence": ["sufficient"] * 19 + ["insufficient"],
            "nei": ["insufficient", "sufficient"],
        }
    )
    assert report["insufficient_nonsense"]["rate"] == 0.9
    assert report["insufficient_nonsense"]["pass"] is True
    assert report["false_insufficient_evidence"]["rate"] == 0.05
    assert report["false_insufficient_evidence"]["pass"] is True
    assert report["insufficient_nei"] == {"rate": 0.5, "n": 2, "errors": 0}


def test_a_sufficiency_gate_with_no_rows_or_a_miss_does_not_pass():
    report = metrics.sufficiency_report({"evidence": ["insufficient", "sufficient"]})
    assert report["insufficient_nonsense"]["pass"] is False
    assert report["false_insufficient_evidence"]["pass"] is False


def test_feature_aurocs_leave_out_missing_features():
    feats = {
        "evidence": [{"z_null": 5.0, "rerank_p": None}, {"z_null": 4.0}],
        "nonsense": [{"z_null": 1.0}, {}],
        "nei": [{"z_null": 4.5}],
    }
    out = metrics.feature_aurocs(feats)
    assert out["z_null"]["evidence_vs_nonsense"] == {"auroc": 1.0, "n_pos": 2, "n_neg": 1}
    assert out["z_null"]["evidence_vs_nei"]["auroc"] == 0.5
    assert out["rerank_p"]["evidence_vs_nonsense"]["auroc"] is None


def _result(**over) -> dict:
    base = {
        "query": "q",
        "passages": [
            {
                "rank": 1,
                "source_id": "n1",
                "artifact_id": "art-000000000001",
                "source_name": "scifact-11.md",
                "text": "alpha",
                "scores": {"dense": 0.90},
            },
        ],
        "sufficiency": {"status": "sufficient", "basis": "z_null", "value": 3.5},
        "lanes": {"null": {"status": "ok", "mu": 0.80, "sigma": 0.02}, "lex": {"status": "off"}},
        "trace": {
            "mode": "dense",
            "candidates": [
                {"node_id": "n1", "artifact_id": "art-000000000001", "scores": {"dense": 0.90}},
                {"node_id": "n2", "artifact_id": "art-000000000002", "scores": {"dense": 0.86}},
            ],
        },
    }
    base.update(over)
    return base


def test_sufficiency_features_come_from_the_null_the_trace_and_the_lanes():
    feats = metrics.sufficiency_features(_result())
    assert feats["z_null"] == pytest.approx(5.0)
    assert feats["gap12"] == pytest.approx(0.04)
    assert feats["lexical"] is None  # the lexical lane did not run
    assert feats["rerank_p"] is None


def test_sufficiency_features_fall_back_to_the_verdict_and_the_passages():
    result = _result(lanes={"lex": {"status": "ok"}}, trace={})
    result["passages"][0]["scores"]["rerank_p"] = 0.7
    feats = metrics.sufficiency_features(result)
    assert feats["z_null"] == 3.5  # the verdict's own value, no null reported
    assert feats["gap12"] is None  # one kept passage, no trace
    assert feats["lexical"] == 0.0  # the lane ran and matched nothing
    assert feats["rerank_p"] == 0.7


def test_rationale_coverage_needs_the_whole_sentence_in_one_passage():
    sentences = ["Aspirin lowers  risk.", "It was a cohort study."]
    assert metrics.rationale_coverage(["x aspirin lowers risk. y"], sentences) == 0.5
    split = ["... It was a", "cohort study. ..."]
    assert metrics.rationale_coverage(split, sentences[1:]) == 0.0
    assert metrics.rationale_coverage(["It was a cohort study."], sentences[1:]) == 1.0
    assert metrics.rationale_coverage(["anything"], []) is None


# ── claims and corpus ────────────────────────────────────────────────────────


def _scifact_with_rationale(tmp_path: Path) -> Path:
    evidence = {
        "10": [{"sentences": [1, 0], "label": "SUPPORT"}, {"sentences": [1], "label": "SUPPORT"}]
    }
    _write(
        tmp_path / "claims_dev.jsonl",
        [{"id": 1, "claim": "c", "evidence": evidence, "cited_doc_ids": [10]}],
    )
    _write(
        tmp_path / "corpus.jsonl", [{"doc_id": 10, "title": "T", "abstract": [" s0 ", "s1", "s2"]}]
    )
    return tmp_path


def test_claims_carry_their_rationale_sentences(tmp_path):
    corpus = scifact.load(_scifact_with_rationale(tmp_path), distractors=None)
    claim = corpus.claims[0]
    assert claim.rationale == (("10", (0, 1)),)
    assert scifact.rationale_sentences(corpus, claim) == ["s0", "s1"]
    assert corpus.docs["10"]["abstract"] == "s0 s1 s2"


def test_nonsense_ids_and_the_seeded_subset_are_shared_helpers():
    assert scifact.is_nonsense(900001) and not scifact.is_nonsense(1400)
    every = [str(d) for d in range(100)]
    picked = scifact.subset_ids(every, [["3"], ["7", "999"]], distractors=5)
    assert {"3", "7"} <= picked and "999" not in picked and len(picked) == 7
    assert picked == scifact.subset_ids(every, [["7", "3"]], distractors=5)


# ── prompts ──────────────────────────────────────────────────────────────────


def _passage(rank: int, doc: str, text: str = "text") -> RetrievedPassage:
    return RetrievedPassage(
        rank=rank,
        source_id=f"node-{doc}",
        artifact_id="",
        source_name=f"scifact-{doc}.md",
        text=text,
    )


@pytest.mark.parametrize(
    ("prompt", "instruction"),
    [("pack-soft", "soft"), ("pack-strict", "strict"), ("quote", "quote")],
)
def test_pack_prompts_put_passages_first_and_the_verdict_format_last(prompt, instruction):
    text, citations = infer.pack_context("CO2 is a gas.", [_passage(1, "4")], prompt=prompt)
    system, build = infer.PROMPTS[prompt]
    message = build("CO2 is a gas.", text)
    assert system == infer.PACK_SYSTEM
    assert message.index("<passages") < message.index(instruction_text(instruction))
    assert message.index(instruction_text(instruction)) < message.index("Question:")
    assert message.index("CO2 is a gas.") < message.index('{"verdict"')
    assert message.rstrip().endswith(infer._VERDICT_FORMAT)
    assert citations == {"[1]": "node-4"}


def test_a_partial_verdict_marks_the_pack_and_no_passages_still_ask():
    text, _ = infer.pack_context("c", [_passage(1, "4")], prompt="pack-soft", status="partial")
    assert 'match="partial"' in text
    empty, citations = infer.pack_context("CO2 is a gas.", [], prompt="pack-soft")
    assert "Question:" in empty and "CO2 is a gas." in empty and citations == {}


def test_every_prompt_is_selectable_and_the_old_ones_are_unchanged():
    assert set(infer.PROMPT_NAMES) == {"json", "legacy", "pack-soft", "pack-strict", "quote"}
    assert infer.PROMPTS["json"] == (infer.SYSTEM, infer.user_message)
    assert infer.PROMPTS["legacy"] == (infer.LEGACY_SYSTEM, infer.legacy_message)


def test_the_pack_abstention_phrase_parses_as_not_enough_info():
    assert infer.parse(f"{NOT_COVERED}.") == ("NOT_ENOUGH_INFO", [], True)
    verdict, _, ok = infer.parse(f'{{"verdict": "SUPPORT", "evidence": [1]}} {NOT_COVERED}')
    assert (verdict, ok) == ("SUPPORT", True)


# ── retrieve arms ────────────────────────────────────────────────────────────


_SIZES = {"k_max": 4, "pool": 50, "per_artifact": 2}


@pytest.mark.parametrize(
    ("preset", "knobs"),
    [
        (
            "legacy",
            {"threshold": 0.7, "k_max": 5, "lexical": "off", "rerank": "off", "merge": "off"},
        ),
        (
            "dense",
            {"cutoff": "adaptive", **_SIZES, "lexical": "off", "rerank": "off", "merge": "off"},
        ),
        (
            "fused",
            {"cutoff": "adaptive", **_SIZES, "lexical": "on", "rerank": "off", "merge": "off"},
        ),
        (
            "rerank",
            {"cutoff": "adaptive", **_SIZES, "lexical": "on", "rerank": "on", "merge": "off"},
        ),
        (
            "merge",
            {"cutoff": "adaptive", **_SIZES, "lexical": "on", "rerank": "off", "merge": "on"},
        ),
        ("default", {"lexical": "auto", "rerank": "auto", "merge": "auto"}),
    ],
)
def test_retrieve_presets_set_every_knob_explicitly(preset, knobs):
    assert arms.PRESETS[preset] == knobs
    assert f"retrieve:{preset}" in arms.ARMS
    assert arms.reports_sufficiency(f"retrieve:{preset}")


def test_only_retrieve_arms_report_sufficiency():
    assert set(arms.PRESETS) == {"legacy", "dense", "fused", "rerank", "merge", "default", "large"}
    assert not arms.reports_sufficiency("legacy")
    assert not arms.reports_sufficiency("k3-embeddings")


def test_the_raw_get_sends_the_client_parameter_names():
    params = backends.retrieve_params("q1", "default", "scifact-eval", {"k_max": 5, "merge": None})
    assert params == {
        "q": ["q1"],
        "project_id": "default",
        "graphs": "scifact-eval",
        "trace": 1,
        "k_max": 5,
    }


def _doc_of(*refs):
    for ref in refs:
        doc = scifact.doc_id_of(str(ref or "")) or {"art-000000000002": "22"}.get(str(ref))
        if doc:
            return doc
    return None


def test_a_retrieve_result_maps_to_pre_cut_hits_and_kept_passages():
    got = backends.retrieved_from_result(_result(), _doc_of)
    assert [h.doc_id for h in got.hits] == [None, "22"]  # trace: art-...1 is unknown here
    assert (got.shown, got.status, got.prompt) == (["11"], "sufficient", "pack-soft")
    assert [p.source_id for p in got.passages] == ["n1"]
    assert got.context.startswith("[1] scifact-11.md")


def test_a_retrieve_result_keeps_its_verdict_and_features_in_meta():
    meta = backends.retrieved_from_result(_result(), _doc_of).meta
    assert meta["features"]["z_null"] == pytest.approx(5.0)
    assert (meta["passages"][0]["doc"], meta["mode"]) == ("11", "dense")
    assert meta["sufficiency"]["basis"] == "z_null"


def _lanes_for(params: dict) -> dict:
    """The lane report a healthy server gives for these knobs."""
    lanes = {"dense": {"status": "ok"}, "null": {"status": "ok", "mu": 0.80, "sigma": 0.02}}
    on = {knob for knob in ("lexical", "rerank", "merge") if params.get(knob) == "on"}
    for knob, required in arms.REQUIRED_LANES.items():
        lanes.update({name: {"status": "ok" if knob in on else "off"} for name, _ran in required})
    if params.get("profile"):
        lanes["profile"] = _profile_report(params["profile"], params.get("reader_model"))
    return lanes


def _profile_report(name: str, reader_model: str | None) -> dict:
    """``lanes.profile`` exactly as umrs ``profiles.report`` builds it."""
    return {"status": "ok", "name": name, "reader_model": reader_model or ""}


class _FakeRag:
    def __init__(self, result: dict, *, has_retrieve: bool) -> None:
        self.result, self.calls = result, []
        if has_retrieve:
            self.retrieve = self._retrieve

    def _retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return RetrieveResult.from_dict(self.result)


def _stack(rag, http=None):
    stack = object.__new__(backends.Stack)
    config = SimpleNamespace(gateway_url="http://gw/")
    stack.client = SimpleNamespace(rag=rag, http=http, config=config)
    stack.project, stack.graph, stack.art_to_doc, stack.corpus = "default", "g", {}, None
    return stack


def test_the_retrieve_arm_uses_the_client_method_with_explicit_knobs():
    rag = _FakeRag(_result(), has_retrieve=True)
    got = arms.ARMS["retrieve:dense"](
        _stack(rag), scifact.Claim(1, "c", "SUPPORT", frozenset(), frozenset())
    )
    query, kwargs = rag.calls[0]
    assert query == "c" and kwargs["graphs"] == ["g"] and kwargs["trace"] is True
    assert kwargs["lexical"] == "off" and kwargs["cutoff"] == "adaptive"
    assert got.meta["preset"] == "dense" and got.meta["sufficiency"]["status"] == "sufficient"


@pytest.mark.parametrize("preset", sorted(arms.PRESETS))
def test_every_preset_runs_through_the_real_r4_client(preset, monkeypatch):
    """The arm -> RAGClient.retrieve seam with the real client: a knob name the
    client does not take, or a wire name the route does not read, fails here."""
    import httpx

    from r4._http import HTTPCore
    from r4.client import Client
    from r4.config import Config

    sent = []

    class Gateway:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, params=None, **_kwargs):
            sent.append((url, params))
            body = _result(lanes=_lanes_for(params))
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(HTTPCore, "_client", lambda _self, _timeout=None: Gateway())
    client = Client(config=Config(gateway_url="http://gw", api_key="k", project_id="default"))
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset(), frozenset())
    got = arms.ARMS[f"retrieve:{preset}"](_stack(client.rag), claim)
    url, params = sent[0]
    expected = {"q": ["c"], "project_id": "default", "trace": 1, **arms.PRESETS[preset]}
    if preset not in arms.PROJECT_WIDE:
        expected["graphs"] = "g"
    assert url == "http://gw/gw/rag/retrieve" and params == expected
    assert got.meta["sufficiency"]["status"] == "sufficient" and got.shown == ["11"]


def test_the_legacy_retrieve_arm_searches_the_whole_project():
    """Receipt 4: only a project-wide threshold request runs the core's frozen
    pre-ADR-093 statement, the one /gw/rag/query used in B0."""
    rag = _FakeRag(_result(), has_retrieve=True)
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset(), frozenset())
    got = arms.ARMS["retrieve:legacy"](_stack(rag), claim)
    _query, kwargs = rag.calls[0]
    assert kwargs["graphs"] is None and kwargs["threshold"] == 0.7
    assert got.meta["project_wide"] is True
    seen = []

    class Http:
        def get(self, _url, **kwargs):
            seen.append(kwargs["params"])
            return _result()

    _stack(_FakeRag({}, has_retrieve=False), Http()).retrieve("c", project_wide=True)
    assert "graphs" not in seen[0]


def test_the_retrieve_arm_falls_back_to_a_raw_get_and_refuses_a_query_fallback():
    seen = []

    class Http:
        def get(self, url, **kwargs):
            seen.append((url, kwargs["params"]))
            return _result()

    got = _stack(_FakeRag({}, has_retrieve=False), Http()).retrieve("c", k_max=3)
    assert seen[0][0] == "http://gw/gw/rag/retrieve" and seen[0][1]["k_max"] == 3
    assert got.shown == ["11"]
    fallback = _result(lanes={"fallback": {"status": "ok"}})
    with pytest.raises(RuntimeError, match="no /rag/retrieve"):
        _stack(_FakeRag(fallback, has_retrieve=True)).retrieve("c")


# ── the scorer gate and the reader options ───────────────────────────────────


class _Client:
    def __init__(self) -> None:
        self.calls = 0

    def chat_completion(self, _messages, **_kwargs):
        self.calls += 1
        return {
            "choices": [{"message": {"content": '{"verdict": "SUPPORT", "evidence": ["[1]"]}'}}]
        }


def _gate_case(status: str, gate: bool):
    client = _Client()
    stack = _stack(None)
    stack.client = client
    stack.doc_of = _doc_of
    got = backends.Retrieved(
        passages=[_passage(1, "11")], shown=["11"], status=status, prompt="pack-soft"
    )
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset({"11"}), frozenset({"11"}))
    opts = SimpleNamespace(prompt=None, order="rank", scorer_gate=gate, model="m")
    row = run._claim_row(stack, lambda _s, _c: got, claim, do_infer=True, opts=opts)
    return row, client.calls


def test_the_scorer_gate_answers_insufficient_without_a_model_call():
    row, calls = _gate_case("insufficient", gate=True)
    assert calls == 0 and row["gated"] is True and row["verdict"] == "NOT_ENOUGH_INFO"
    assert row["sufficiency"] == "insufficient"


def test_without_the_gate_or_when_sufficient_the_model_reads_the_pack():
    for status, gate in (("insufficient", False), ("sufficient", True)):
        row, calls = _gate_case(status, gate)
        assert calls == 1 and row["gated"] is False and row["verdict"] == "SUPPORT"
        assert row["cited"] == ["11"] and row["prompt"] == "pack-soft"
        assert row["context_tokens"] > 0 and row["passages_shown"] == 1


def test_a_pack_context_numbers_passages_in_display_order():
    got = backends.Retrieved(passages=[_passage(i, str(10 + i)) for i in range(1, 5)])
    stack = SimpleNamespace(doc_of=_doc_of)
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset(), frozenset())
    _, docs = run._context_for(stack, claim, got, "pack-soft", "edge")
    assert docs == ["11", "13", "14", "12"]
    _, docs = run._context_for(stack, claim, got, "json", "edge")
    assert docs == ["11", "12", "13", "14"]


def test_sufficiency_rows_are_grouped_by_claim_kind():
    rows = [
        {
            "claim": 900001,
            "label": "NOT_ENOUGH_INFO",
            "sufficiency": "insufficient",
            "features": {"z_null": 0.5},
        },
        {"claim": 2, "label": "SUPPORT", "sufficiency": "sufficient", "features": {"z_null": 4.0}},
        {
            "claim": 3,
            "label": "NOT_ENOUGH_INFO",
            "sufficiency": "partial",
            "features": {"z_null": 2.5},
        },
    ]
    block = run.sufficiency_block(rows)
    assert block["insufficient_nonsense"]["rate"] == 1.0
    assert block["false_insufficient_evidence"]["rate"] == 0.0
    assert block["insufficient_nei"]["rate"] == 0.0
    assert block["features"]["z_null"]["evidence_vs_nonsense"]["auroc"] == 1.0
    assert block["status_counts"] == {"insufficient": 1, "sufficient": 1, "partial": 1}


def test_the_sufficiency_command_pools_dev_and_nonsense_files(tmp_path, capsys):
    dev = {
        "arm": "retrieve:dense",
        "claims_set": "dev",
        "rows": [{"claim": 2, "label": "SUPPORT", "sufficiency": "sufficient", "features": {}}],
    }
    junk = {
        "arm": "retrieve:dense",
        "claims_set": "nonsense",
        "rows": [
            {"claim": 900001, "label": "NOT_ENOUGH_INFO", "sufficiency": "insufficient"},
            {"claim": 900002, "label": "NOT_ENOUGH_INFO", "error": "boom"},
        ],
    }
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    for path, record in zip(paths, (dev, junk), strict=True):
        path.write_text(json.dumps(record))
    assert run.main(["sufficiency", *map(str, paths)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["arms"] == ["retrieve:dense"]
    # the errored probe counts against the gate (review 2026-10-10, eval-c21-8)
    assert report["insufficient_nonsense"] == {
        "rate": 1.0,
        "n": 1,
        "errors": 1,
        "rate_itt": 0.5,
        "gate": ">= 0.9",
        "pass": False,
    }


def test_old_commands_keep_their_flags_and_file_names():
    args = run._parser().parse_args(["infer", "--data", "d", "--arms", "closed,legacy"])
    assert (args.prompt, args.order, args.scorer_gate, args.project) == (
        None,
        "rank",
        False,
        "default",
    )
    assert run._variant(args) == ""
    gated = run._parser().parse_args(
        ["infer", "--data", "d", "--arms", "retrieve:dense", "--prompt", "quote", "--scorer-gate"]
    )
    assert run._variant(gated, "retrieve:dense") == "-p-quote-gate"
    assert run._variant(gated, "oracle") == "-p-quote"  # the gate cannot fire there


def test_retrieval_alone_runs_every_claim_only_for_arms_with_a_verdict():
    claims = [
        scifact.Claim(1, "a", "SUPPORT", frozenset({"1"}), frozenset({"1"})),
        scifact.Claim(2, "b", "NOT_ENOUGH_INFO", frozenset(), frozenset({"2"})),
    ]
    corpus = scifact.Corpus(claims=claims)
    args = SimpleNamespace(limit=None)
    assert len(run._arm_claims(corpus, "k3-embeddings", args, do_infer=False)) == 1
    assert len(run._arm_claims(corpus, "retrieve:dense", args, do_infer=False)) == 2


# ── the offline replica ──────────────────────────────────────────────────────


def test_the_vendored_stop_words_are_scikit_learns():
    text = pytest.importorskip("sklearn.feature_extraction.text")
    assert offline_lanes.STOP_WORDS == text.ENGLISH_STOP_WORDS


def test_lexical_lanes_rank_the_chunk_that_holds_the_rare_term():
    index = offline_lanes.LexicalIndex(
        ["the kinase binds insulin", "insulin insulin levels", "weather report", "kinase kinase"]
    )
    assert offline_lanes.tokens("The kinase AND insulin") == ["kinase", "insulin"]
    bm25 = index.bm25("kinase insulin")
    assert int(np.argmax(bm25)) == 0 and bm25[2] == 0.0
    idf = index.idf_lexical("kinase weather")
    assert idf[2] == pytest.approx(np.log(4 / 1)) and idf[0] == pytest.approx(np.log(4 / 2))


def test_doc_ranking_max_pools_chunks_and_rrf_fuses_lanes():
    docs = np.array(["a", "a", "b", "c"])
    scores = np.array([0.9, 0.8, 0.0, 0.5], dtype=np.float32)
    assert offline_lanes.doc_ranking(scores, docs) == ["a", "c", "b"]
    assert offline_lanes.doc_ranking(scores, docs, positive_only=True) == ["a", "c"]
    assert offline_lanes.rrf([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]
    assert offline_lanes.rrf([["a"], ["b"]]) == ["a", "b"]  # a tie keeps lane order


def test_platform_chunks_are_token_windows_without_blank_tails():
    text = "aa bb cc dd  "
    offsets = [(0, 2), (3, 5), (6, 8), (9, 11), (12, 13)]
    assert offline_lanes.platform_chunks(text, offsets, window=2) == [(0, "aa bb"), (1, "cc dd")]


def _tiny_cache(path: Path) -> Path:
    chunks = [[d, 0, f"doc {d} about topic{d}"] for d in range(10, 20)]
    claims = [
        {"id": 1, "claim": "topic12", "evidence": {"12": []}, "cited_doc_ids": [12], "split": "dev"}
    ]
    vecs = np.eye(10, dtype=np.float32)
    (path / "chunks.json").write_text(json.dumps(chunks))
    (path / "claims.json").write_text(json.dumps(claims))
    np.save(path / "chunk_vecs.npy", vecs)
    np.save(path / "claim_vecs.npy", vecs[[2]])
    return path


def test_the_replica_evaluates_every_lane_and_cuts_the_1k_tier(tmp_path):
    cache = offline_lanes.load_cache(_tiny_cache(tmp_path))
    report = offline_lanes.evaluate(cache)
    assert report["claims"] == 1 and set(report["lanes"]) == set(offline_lanes.LANES)
    assert (
        report["lanes"]["dense"]["recall@1"] == 1.0 and report["lanes"]["bm25"]["recall@1"] == 1.0
    )
    small = offline_lanes.load_cache(tmp_path, tier="1k")
    assert len(small.texts) == 10  # 1 cited + all 9 others (fewer than 717)
    assert not offline_lanes.cache_present(tmp_path / "missing")


REPLICA = Path(os.environ.get("ADR093_REPLICA_DIR") or offline_lanes.DEFAULT_CACHE)


@pytest.mark.skipif(
    not offline_lanes.cache_present(REPLICA),
    reason=f"no offline replica cache at {REPLICA}: set ADR093_REPLICA_DIR or run "
    "offline_lanes.py build with a staged e5-base-v2 (never downloaded)",
)
def test_the_replica_reproduces_the_full_corpus_record():
    cache = offline_lanes.load_cache(REPLICA)
    reference = offline_lanes.REFERENCE.get(cache.scheme, {}).get("full")
    if not reference:
        pytest.skip(f"no recorded reference for text scheme {cache.scheme!r}")
    report = offline_lanes.evaluate(cache)
    assert report["claims"] == 188
    for lane, expected in reference.items():
        assert report["lanes"][lane]["ndcg@10"] == pytest.approx(expected, abs=0.005), lane


def _judged_row(claim: int, label: str, status: str, verdict: str, *, gated: bool) -> dict:
    return {
        "claim": claim,
        "label": label,
        "retrieve_seconds": 0.1,
        "context_tokens": 0 if gated else 120,
        "passages_shown": 1,
        "sufficiency": status,
        "features": {},
        "verdict": verdict,
        "parsed": True,
        "cited": [],
        "gold_in_context": False,
        "infer_seconds": 0.0,
        "usage": {},
        "gated": gated,
    }


def test_the_summary_reports_the_gate_the_reader_cost_and_sufficiency():
    rows = [
        _judged_row(900001, "NOT_ENOUGH_INFO", "insufficient", "NOT_ENOUGH_INFO", gated=True),
        _judged_row(2, "SUPPORT", "sufficient", "SUPPORT", gated=False),
    ]
    summary = run.summarize(rows, {900001: set(), 2: {"9"}})
    assert summary["gated_rate"] == 0.5
    assert summary["context_tokens"]["mean"] == 60.0
    assert summary["sufficiency"]["insufficient_nonsense"]["rate"] == 1.0
    headline = run._headline(summary)
    assert headline["insufficient_nonsense"] == 1.0
    assert headline["false_insufficient_evidence"] == 0.0


# ── review fixes (2026-10-10): outages, fallbacks and honest denominators ────

_CLAIM = scifact.Claim(1, "c", "SUPPORT", frozenset({"11"}), frozenset({"11"}))


def _outage() -> dict:
    """What the server answers (HTTP 200) when the query encoder times out."""
    reason = "query encoder unavailable (ReadTimeout)"
    return _result(
        passages=[],
        sufficiency={"status": "insufficient", "basis": "unavailable", "detail": reason},
        lanes={"dense": {"status": "unavailable", "reason": reason}},
        trace={},
    )


def test_a_retrieve_outage_is_an_error_not_insufficient(monkeypatch):
    monkeypatch.setattr(infer.time, "sleep", lambda _seconds: None)
    stack = _stack(_FakeRag(_outage(), has_retrieve=True))
    with pytest.raises(infer.TransientError, match="unavailable"):
        stack.retrieve("c")
    junk = scifact.Claim(900001, "junk", "NOT_ENOUGH_INFO", frozenset(), frozenset())
    opts = SimpleNamespace(prompt=None, order="rank", scorer_gate=True, model="m")
    rows = run._run_arm(stack, [junk], "retrieve:dense", do_infer=True, opts=opts)
    assert "TransientError" in rows[0]["error"]
    assert "sufficiency" not in run.summarize(rows, {900001: set()})


@pytest.mark.parametrize(
    "lane",
    [
        {"null": {"status": "unavailable", "reason": "probes not encoded"}},
        {"dense": {"status": "unavailable", "reason": "dense lane failed (OperationalError)"}},
    ],
)
def test_a_failed_dense_or_null_lane_is_an_outage_on_both_paths(lane):
    result = _result(lanes={**_lanes_for({}), **lane})
    with pytest.raises(infer.TransientError):
        _stack(_FakeRag(result, has_retrieve=True)).retrieve("c")

    class Http:
        def get(self, _url, **_kwargs):
            return result

    with pytest.raises(infer.TransientError):
        _stack(_FakeRag({}, has_retrieve=False), Http()).retrieve("c")


@pytest.mark.parametrize(
    ("preset", "broken"),
    [
        ("rerank", "rerank"),
        ("fused", "lex"),
        ("fused", "ident"),
        ("fused", "fusion"),
        ("merge", "merge"),
    ],
)
def test_a_preset_whose_stage_did_not_run_is_an_error(preset, broken):
    lanes = _lanes_for(arms.PRESETS[preset])
    lanes[broken] = {"status": "unavailable", "reason": "migration 0120 is missing"}
    rag = _FakeRag(_result(lanes=lanes), has_retrieve=True)
    with pytest.raises(arms.PresetNotMeasured, match=broken):
        arms.ARMS[f"retrieve:{preset}"](_stack(rag), _CLAIM)


def test_a_fused_preset_with_one_ranking_is_still_measured():
    lanes = {
        **_lanes_for(arms.PRESETS["fused"]),
        "fusion": {"status": "off", "reason": "one ranking"},
    }
    got = arms.ARMS["retrieve:fused"](
        _stack(_FakeRag(_result(lanes=lanes), has_retrieve=True)), _CLAIM
    )
    assert got.meta["preset"] == "fused"


def test_the_summary_counts_lane_statuses():
    rows = [
        {"claim": n, "label": "SUPPORT", "retrieve_seconds": 0.1, "meta": {"lanes": lanes}}
        for n, lanes in enumerate(({"lex": {"status": "ok"}}, {"lex": {"status": "off"}}))
    ]
    assert run.summarize(rows, {})["lane_status"] == {"lex": {"ok": 1, "off": 1}}


def test_the_agent_recorder_sees_the_retrieve_verb():
    rag = _FakeRag(_result(), has_retrieve=True)
    stack = _stack(rag)
    with backends._Recorder(stack) as seen:
        stack.client.rag.retrieve("c")
    assert (seen.calls, seen.docs) == (["rag.retrieve"], ["11"])
    assert stack.client.rag.retrieve == rag._retrieve  # restored


def test_the_recorder_marks_the_routers_retrieves_and_keeps_its_decision(monkeypatch):
    from r4.companion import retrieval_router

    stack = _stack(_FakeRag(_result(), has_retrieve=True))

    def route(_session, message, **_kwargs):
        stack.client.rag.retrieve(message)
        return retrieval_router.RouteDecision(message, "pack", status="sufficient", sids=("n1",))

    monkeypatch.setattr(retrieval_router, "route_turn_decision", route)
    with backends._Recorder(stack) as seen:
        retrieval_router.route_turn_decision(None, "c")
        stack.client.rag.retrieve("c")
    assert seen.calls == ["router:rag.retrieve", "rag.retrieve"]
    assert (seen.routes[0]["action"], seen.routes[0]["sids"]) == ("pack", ["n1"])
    assert retrieval_router.route_turn_decision is route


def test_an_unmapped_passage_keeps_its_rank_and_dilutes_the_context():
    hits = [backends.Hit(None, 0.86, "perovskite note", "node-x"), backends.Hit("11", 0.84, "a")]
    stack = SimpleNamespace(k3=lambda *_a, **_k: backends.Retrieved(hits=list(hits)))
    got = arms.ARMS["k3-embeddings"](stack, _CLAIM)
    row = run._row(_CLAIM, got, got.context, None)
    assert (row["retrieval"]["mrr@10"], row["retrieval"]["recall@1"]) == (0.5, 0.0)
    assert (row["context"]["ctx_precision"], row["intruders"]) == (0.5, 1)


def test_a_retrieve_passage_of_another_artifact_is_shown_as_other():
    result = _result()
    other = {"rank": 2, "source_id": "n9", "artifact_id": "", "source_name": "zeolite.md"}
    result["passages"].append({**other, "text": "x", "scores": {"dense": 0.85}})
    got = backends.retrieved_from_result(result, _doc_of)
    row = run._row(_CLAIM, got, got.context, None)
    assert got.shown == ["11", "other:n9"] and row["context"]["ctx_precision"] == 0.5
    assert row["shown_refs"] == ["n1", "n9"]


def _tool_stack(output: str, seen: list | None = None):
    stack = _stack(None)

    def tool(**kwargs):
        (seen if seen is not None else []).append(kwargs)
        return output

    stack._tools = {"knowledge_query": tool, "rag_query": tool}
    return stack


@pytest.mark.parametrize(
    "output",
    [
        json.dumps({"type": "knowledge_error", "code": "knowledge_query_failed", "message": "503"}),
        json.dumps({"error": {"code": "knowledge_query_failed"}}),
        "[embedding_backend_unavailable] RAG querying RAG failed with gateway status 503.",
        "Error querying RAG for RAG: boom",
        json.dumps({"type": "knowledge_query", "unavailable_backends": {"embeddings": "no url"}}),
        json.dumps({"answer": "No knowledge in this project answers this (spine query failed: X)"}),
    ],
)
def test_a_tool_error_is_never_a_result(output):
    for arm in ("tool:knowledge_query", "tool:rag_query"):
        with pytest.raises(infer.TransientError):
            arms.ARMS[arm](_tool_stack(output), _CLAIM)


def test_a_tool_result_with_no_hits_is_still_a_result():
    output = json.dumps({"type": "knowledge_query", "results": [], "unavailable_backends": {}})
    assert arms.ARMS["tool:knowledge_query"](_tool_stack(output), _CLAIM).hits == []


def test_the_tool_arm_follows_the_graph_flag():
    seen: list = []
    stack = _tool_stack(json.dumps({"type": "knowledge_query", "results": []}), seen)
    stack.graph = "scifact-1k"
    arms.ARMS["tool:knowledge_query"](stack, _CLAIM)
    assert seen[0]["graphs"] == ["scifact-1k"]


class _Answering:
    """``client.rag`` whose /rag/query answers one fixed response."""

    def __init__(self, answer: str, sources=(), metadata=None) -> None:
        self.response = SimpleNamespace(
            answer=answer, sources=list(sources), raw_response={"metadata": metadata or {}}
        )

    def query(self, *_args, **_kwargs):
        return self.response


def _source(node: str, doc: str):
    meta = {"source_name": f"scifact-{doc}.md"}
    return SimpleNamespace(document_id=node, metadata=meta, score=0.9, content="text")


@pytest.mark.parametrize(
    ("answer", "sources", "metadata"),
    [
        (
            "No knowledge in this project answers this (spine query failed: OperationalError).",
            [],
            {},
        ),
        ("No knowledge in this project answers this (knowledge database unavailable).", [], {}),
        ("Could not generate an answer: the chat model timed out", [_source("n1", "11")], {}),
        ("No knowledge in this project answers this (x).", [], {"lanes": _outage()["lanes"]}),
        (
            "No knowledge in this project answers this (x).",
            [],
            {"sufficiency": {"basis": "unavailable"}},
        ),
    ],
)
def test_an_answer_path_outage_is_an_error(answer, sources, metadata):
    with pytest.raises(infer.TransientError):
        _stack(_Answering(answer, sources, metadata)).rag_sources("q", top_k=5, threshold=None)


def test_an_answer_path_abstention_is_a_result():
    verdict = {"status": "insufficient", "basis": "z_null", "value": 1.2, "threshold": 3.0}
    answer = "No knowledge in this project answers this (z_null 1.20 < 3)."
    rag = _Answering(answer, [], {"sufficiency": verdict})
    got = _stack(rag).rag_sources("q", top_k=5, threshold=None)
    assert got.hits == [] and got.answer == answer


def test_a_gated_row_shows_the_reader_nothing():
    row, calls = _gate_case("insufficient", gate=True)
    assert calls == 0
    assert (row["context_tokens"], row["context_chars"], row["passages_shown"]) == (0, 0, 0)
    assert row["context"] == {
        "ctx_precision": 0.0,
        "ctx_recall": 0.0,
        "ctx_recall_all": 0.0,
        "passages": 0.0,
    }
    retrieved = row["retrieved"]
    assert retrieved["context_tokens"] > 0 and retrieved["passages_shown"] == 1
    assert retrieved["context"]["ctx_precision"] == 1.0
    assert row["gold_in_context"] is True  # the cut kept it; the gate withheld it


def test_the_summary_of_real_rows_reports_the_gated_reader_cost():
    gated, _ = _gate_case("insufficient", gate=True)
    read, _ = _gate_case("sufficient", gate=True)
    summary = run.summarize([gated, read], {1: {"11"}})
    assert summary["context_tokens"]["mean"] == read["context_tokens"] / 2
    assert summary["passages_shown"]["mean"] == 0.5 and summary["gated_rate"] == 0.5


@pytest.mark.parametrize(
    ("reply", "verdict", "ok"),
    [
        ("The passages do not support the claim.", "NOT_ENOUGH_INFO", False),
        ("No passage contradicts or supports this claim.", "NOT_ENOUGH_INFO", False),
        ("The claim isn't supported by [1].", "NOT_ENOUGH_INFO", False),
        ("[1] supports it (SUPPORT). The dose is NOT IN KNOWLEDGE BASE.", "SUPPORT", False),
        ("The passages contradict it. There is no doubt.", "CONTRADICT", False),
        ("not in knowledge base", "NOT_ENOUGH_INFO", True),
    ],
)
def test_a_negated_or_mixed_reply_never_becomes_a_verdict(reply, verdict, ok):
    got, _evidence, parsed = infer.parse(reply)
    assert (got, parsed) == (verdict, ok)


@pytest.mark.parametrize("preset", ["dense", "fused", "rerank", "merge"])
def test_adaptive_presets_pin_the_request_sizes(preset):
    assert {"k_max": 4, "pool": 50, "per_artifact": 2}.items() <= arms.PRESETS[preset].items()


def _vrow(claim: int, label: str, verdict: str, *, shown: bool = True, cited=()) -> dict:
    return {
        "claim": claim,
        "label": label,
        "verdict": verdict,
        "parsed": True,
        "cited": list(cited),
        "gold_in_context": shown,
        "infer_seconds": 0.5,
        "retrieve_seconds": 0.1,
        "usage": {"prompt_tokens": 100},
    }


def _record(tmp_path: Path, name: str, rows: list[dict]) -> str:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"arm": name, "rows": rows}))
    return str(path)


def _compare(tmp_path, capsys, rows_a: list[dict], rows_b: list[dict], cmd: str = "compare"):
    paths = [_record(tmp_path, name, rows) for name, rows in (("a", rows_a), ("b", rows_b))]
    assert run.main([cmd, *paths]) == 0
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def _abstaining_and_fixed() -> tuple[list[dict], list[dict]]:
    """A abstains on 20 shown-evidence claims and asserts on 10 NEI claims;
    B gets all 30 right."""
    evidence = [_vrow(i, "SUPPORT", "NOT_ENOUGH_INFO") for i in range(20)]
    nei = [_vrow(100 + i, "NOT_ENOUGH_INFO", "SUPPORT") for i in range(10)]
    fixed = [{**row, "verdict": row["label"]} for row in evidence + nei]
    return evidence + nei, fixed


def test_compare_reports_the_receipts_with_intervals(tmp_path, capsys):
    report, _err = _compare(tmp_path, capsys, *_abstaining_and_fixed())
    abstain = report["wrong_abstain_when_shown"]
    assert (abstain["delta"], abstain["n"], abstain["ci_excludes_zero"]) == (-1.0, 20, True)
    assert (report["unsupported_verdict"]["delta"], report["unsupported_verdict"]["n"]) == (
        -1.0,
        10,
    )
    assert report["macro_f1"]["delta"] > 0 and report["macro_f1"]["non_inferior"] is True
    assert report["accuracy"]["non_inferior"] is True and report["complete"] is True


def test_a_paired_rate_with_no_claims_is_undefined(tmp_path, capsys):
    closed = [_vrow(i, "SUPPORT", "NOT_ENOUGH_INFO", shown=False) for i in range(5)]
    report, _err = _compare(tmp_path, capsys, closed, closed)
    abstain = report["wrong_abstain_when_shown"]
    assert (abstain["delta"], abstain["n"], abstain["ci_excludes_zero"]) == (None, 0, False)


def test_compare_reports_errors_and_scores_them_wrong(tmp_path, capsys):
    rows_a = [_vrow(i, "SUPPORT", "SUPPORT") for i in range(10)]
    rows_b = rows_a[:7] + [
        {"claim": i, "label": "SUPPORT", "error": "APIError 500"} for i in (7, 8, 9)
    ]
    report, err = _compare(tmp_path, capsys, rows_a, rows_b)
    assert report["errors"] == {"a": 0, "b": 3} and report["complete"] is False
    assert (report["claims"], report["accuracy"]["delta"]) == (7, 0.0)
    assert report["accuracy_itt"]["delta"] == pytest.approx(-0.3)
    assert report["accuracy_itt"]["claims"] == 10 and "incomplete" in err


def test_parity_compares_node_ids_in_order(tmp_path, capsys):
    def row(claim, refs=None, shown=("11",)):
        out = {"claim": claim, "label": "SUPPORT", "shown": list(shown)}
        return out if refs is None else {**out, "shown_refs": refs}

    rows_a = [row(1, ["n1", "n2"]), row(2, ["n3", "n4"]), row(3)]
    rows_b = [row(1, ["n1", "n2"]), row(2, ["n4", "n3"]), row(3)]
    report, _err = _compare(tmp_path, capsys, rows_a, rows_b, cmd="parity")
    assert (report["claims"], report["identical"]) == (3, 2)
    assert report["basis"] == {"refs": 2, "docs": 1} and report["mismatches"][0]["claim"] == 2


def test_repro_says_a_run_reproduces_itself_claim_by_claim(tmp_path, capsys):
    rows = [{**_vrow(i, "SUPPORT", "SUPPORT"), "reply": f"r{i}"} for i in range(6)]
    report, _err = _compare(tmp_path, capsys, rows, [dict(r) for r in rows], cmd="repro")
    assert (report["claims"], report["same_verdict"], report["verdict_agreement"]) == (6, 6, 1.0)
    assert (report["same_reply"], report["reproduces"], report["complete"]) == (6, True, True)
    # a field neither run recorded is not compared (None, n 0), never "the same"
    assert (report["same_shown_refs"], report["same_shown_refs_n"]) == (None, 0)


def test_repro_names_the_claims_whose_verdict_moved(tmp_path, capsys):
    rows_a = [{**_vrow(i, "SUPPORT", "SUPPORT"), "reply": "same"} for i in range(5)]
    rows_b = [dict(r) for r in rows_a]
    rows_b[3] = {**rows_b[3], "verdict": "CONTRADICT", "reply": "moved"}
    rows_b[1] = {**rows_b[1], "reply": "reworded"}  # same verdict, other words
    report, _err = _compare(tmp_path, capsys, rows_a, rows_b, cmd="repro")
    assert (report["same_verdict"], report["same_reply"]) == (4, 3)
    assert report["verdict_differs"] == [3] and report["reproduces"] is False
    assert report["verdict_agreement"] == pytest.approx(0.8)


def test_repro_does_not_reproduce_when_a_run_left_a_claim_out(tmp_path, capsys):
    rows_a = [_vrow(i, "SUPPORT", "SUPPORT") for i in range(4)]
    rows_b = rows_a[:3] + [{"claim": 3, "label": "SUPPORT", "error": "ReadTimeout"}]
    report, err = _compare(tmp_path, capsys, rows_a, rows_b, cmd="repro")
    assert (report["claims"], report["same_verdict"]) == (3, 3)
    assert report["complete"] is False and report["reproduces"] is False
    assert "incomplete" in err


def test_headroom_used_places_an_arm_between_the_brackets(tmp_path, capsys):
    claims = range(10)
    files = [
        _record(
            tmp_path,
            "arm",
            [_vrow(c, "SUPPORT", "SUPPORT" if c < 5 else "CONTRADICT") for c in claims],
        ),
        _record(tmp_path, "closed", [_vrow(c, "SUPPORT", "CONTRADICT") for c in claims]),
        _record(tmp_path, "oracle", [_vrow(c, "SUPPORT", "SUPPORT") for c in claims]),
    ]
    assert run.main(["headroom", *files]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["claims"], report["accuracy"]["headroom_used"]) == (10, 0.5)
    assert run.headroom_used(0.5, 0.5, 0.5) is None


def test_the_summary_reports_p50_and_p95():
    rows = [
        {
            "claim": i,
            "label": "SUPPORT",
            "retrieve_seconds": float(i),
            "meta": {"timing_ms": {"dense": 10.0 * i}},
        }
        for i in range(1, 21)
    ]
    latency = run.summarize(rows, {})["latency"]
    assert latency["retrieve_seconds"] == {"p50": 10.0, "p95": 19.0, "n": 20}
    assert latency["timing_ms.dense"]["p95"] == 190.0


def test_an_undefined_rate_is_none_with_its_denominator():
    rows = [_vrow(i, "SUPPORT", "NOT_ENOUGH_INFO", shown=False) for i in range(5)]
    summary = run.summarize(rows, {i: {"9"} for i in range(5)})
    assert (summary["wrong_abstain_when_shown"], summary["wrong_abstain_when_shown_n"]) == (None, 0)
    assert (summary["unsupported_verdict"], summary["citation_precision"]) == (None, None)
    assert (summary["gold_shown_rate"], summary["gold_shown_rate_n"]) == (0.0, 5)
    headline = run._headline(summary)
    assert (headline["wrong_abstain_when_shown"], headline["wrong_abstain_when_shown_n"]) == (
        None,
        0,
    )


class _Citing:
    def __init__(self, evidence: list[str]) -> None:
        self.reply = json.dumps({"verdict": "SUPPORT", "evidence": evidence})

    def chat_completion(self, _messages, **_kwargs):
        return {"choices": [{"message": {"content": self.reply}}]}


@pytest.mark.parametrize(
    ("evidence", "cited"),
    [(["1", "9"], ["11", "phantom:9"]), (["[1]", "scifact-12.md"], ["11", "phantom:12"])],
)
def test_a_phantom_citation_lowers_citation_precision(evidence, cited):
    stack = _stack(None)
    stack.client, stack.doc_of = _Citing(evidence), _doc_of
    got = backends.Retrieved(
        passages=[_passage(1, "11")], shown=["11"], context="[1] scifact-11.md\nt"
    )
    opts = SimpleNamespace(prompt="json", order="rank", scorer_gate=False, model="m")
    row = run._claim_row(stack, lambda _s, _c: got, _CLAIM, do_infer=True, opts=opts)
    summary = run.summarize([row], {1: {"11"}})
    assert row["cited"] == cited
    assert (summary["citation_precision"], summary["phantom_rate"]) == (0.5, 0.5)


def test_answer_defaults_to_the_product_path_and_names_the_bracket():
    args = run._parser().parse_args(["answer", "--data", "d"])
    assert args.threshold is None and run._variant(args, "rag:query") == ""
    bracket = run._parser().parse_args(["answer", "--data", "d", "--threshold", "0.7"])
    assert run._variant(bracket, "rag:query") == "-t0.7"


def test_grouped_citations_count_every_label():
    assert run.cite_numbers("as shown [1, 2]. and [3]") == {1, 2, 3}
    assert run.cite_numbers("in [2020] and [a]") == set()


def test_an_answer_row_maps_grouped_citations_to_docs():
    sources = [_source("n1", "11"), _source("n2", "12")]
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset({"12"}), frozenset({"12"}))
    row = run._answer_row(_stack(_Answering("Both agree [1, 2].", sources)), claim, None)
    assert row["cited"] == ["11", "12"] and row["cited_evidence"] is True


def _oracle_row(prompt: str) -> dict:
    sentences = ["x" * 4100, "The rationale sentence is here."]
    doc = {"title": "T", "abstract": " ".join(sentences), "sentences": sentences}
    stack = _stack(None)
    stack.corpus, stack.doc_of = scifact.Corpus(docs={"11": doc}), _doc_of
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset({"11"}), frozenset({"11"}), (("11", (1,)),))
    opts = SimpleNamespace(prompt=prompt, order="rank", scorer_gate=False, model="m")
    return run._claim_row(stack, arms.ARMS["oracle"], claim, do_infer=False, opts=opts)


def test_rationale_coverage_reads_what_the_prompt_rendered():
    assert _oracle_row("json")["rationale_coverage"] == 0.0  # cut at 4,000 characters
    assert _oracle_row("pack-soft")["rationale_coverage"] == 1.0  # the pack shows it whole


def test_order_is_named_only_where_a_pack_renders_it():
    parse = run._parser().parse_args
    edge_json = parse(
        ["infer", "--data", "d", "--arms", "oracle", "--prompt", "json", "--order", "edge"]
    )
    assert run._variant(edge_json, "oracle") == "-p-json"
    edge = parse(["infer", "--data", "d", "--arms", "retrieve:dense", "--order", "edge"])
    assert run._variant(edge, "retrieve:dense") == "-o-edge" and run._variant(edge, "oracle") == ""


def _scifact_for_ingest(tmp_path: Path) -> Path:
    _write(tmp_path / "claims_dev.jsonl", [])
    abstract = ["  First sentence.  ", "Second one. "]
    _write(
        tmp_path / "corpus.jsonl",
        [{"doc_id": d, "title": f" Title {d} ", "abstract": abstract} for d in (7, 8)],
    )
    return tmp_path


def test_the_replica_chunks_the_ingested_text(tmp_path):
    data = _scifact_for_ingest(tmp_path)
    paths = scifact.write_docs(scifact.load(data, distractors=None), tmp_path / "docs")
    written = {scifact.doc_id_of(p.name): p.read_text(encoding="utf-8") for p in paths}
    assert offline_lanes.source_texts(data) == written
    assert written["7"] == "# Title 7\n\nFirst sentence. Second one.\n"


def _space_tokenizer(text: str, **_kwargs) -> dict:
    return {"offset_mapping": [m.span() for m in re.finditer(r"\S+", text)]}


def test_the_replica_keeps_the_heading_the_graph_holds(tmp_path):
    chunks = offline_lanes.chunk_corpus({"7": "# T\n\na b c\n"}, _space_tokenizer)
    assert chunks == [["7", 0, "# T\n\na b c"]]
    cache_dir = _tiny_cache(tmp_path)
    assert offline_lanes.load_cache(cache_dir).scheme == offline_lanes.PROTOTYPE_SCHEME
    (cache_dir / offline_lanes.META_FILE).write_text(json.dumps({"text_scheme": "ingested-md-v1"}))
    assert offline_lanes.evaluate(offline_lanes.load_cache(cache_dir))["scheme"] == "ingested-md-v1"


def test_the_tune_split_scores_only_claims_whose_evidence_is_in_the_corpus(tmp_path):
    path = _tiny_cache(tmp_path)
    claims = json.loads((path / "claims.json").read_text())
    for n, evidence in enumerate(({"13": []}, {"99": []}, {"12": [], "99": []}), start=2):
        claims.append({"id": n, "claim": "topic13", "evidence": evidence, "split": "train"})
    (path / "claims.json").write_text(json.dumps(claims))
    np.save(path / "claim_vecs.npy", np.eye(10, dtype=np.float32)[[2, 3, 3, 3]])
    report = offline_lanes.evaluate(offline_lanes.load_cache(path, tier="1k"), split="tune")
    assert (report["claims"], report["dropped_evidence_outside_corpus"]) == (1, 2)


# ── provenance ───────────────────────────────────────────────────────────────

_PROBE = {
    "code_dir": "/app/umrs-src/umrs",
    "code": {"sha256": "s" * 64, "files": 3, "mtime_max": 200.0},
    "started_at": 100.0,
    "rag_stats": {
        "embedding_model": "/opt/umrs/models/e5-base-v2",
        "embedding_dimensions": 768,
        "reranker": "unavailable (not staged)",
        "reranker_reason": "UMRS_RAG_RERANKER_MODEL is empty",
    },
    "ollama_tags": {"models": [{"name": "llama3.2:3b", "digest": "a80c4f17"}]},
}


def _fake_stack_provenance(monkeypatch) -> tuple[dict, list]:
    git_calls: list = []

    def git(*args):
        git_calls.append(args)
        return {"rev-parse": "abc123", "status": "?? benchmarks/retrieval/x.py"}.get(args[-2], "")

    def run_in(_service, *argv, timeout=60):
        if argv[0] == "printenv":
            return "UMRS_KNOWLEDGE_CHUNK_SCHEME=fixed-v0\nOLLAMA_NUM_PARALLEL=1\nPATH=/bin\n"
        return "\x1b[0m\n" + json.dumps(_PROBE) + "\n"

    def sql(_query, **params):
        if "graph" in params:
            return [["g-1", "5183", "9377", "9377", "2026-10-09", "e5", "md5x"]]
        return [["9395", "9392"]] if params else [["postgres", "15.19"]]

    monkeypatch.setattr(backends, "_git", git)
    monkeypatch.setattr(backends, "_exec", run_in)
    monkeypatch.setattr(backends, "sql", sql)
    return backends.provenance(model="llama3.2:3b"), git_calls


def test_provenance_hashes_the_code_untracked_files_included(monkeypatch):
    prov, git_calls = _fake_stack_provenance(monkeypatch)
    assert prov["parent_dirty"] is True and ("status", "--porcelain") in git_calls
    harness = prov["code"]["harness"]
    assert harness["files"] >= 8 and len(harness["sha256"]) == 64
    assert prov["server"]["edited_after_start"] is True
    assert prov["server"]["matches_checkout"] is False


def test_provenance_names_the_models_and_the_corpus(monkeypatch):
    prov, _calls = _fake_stack_provenance(monkeypatch)
    models = prov["models"]
    assert models["chat_model_digest"] == "a80c4f17"
    assert models["embedder"]["model"].endswith("e5-base-v2")
    assert models["reranker"]["model"] == "unavailable (not staged)"
    corpus = prov["corpus"]
    assert (corpus["graphs"][0]["chunks"], corpus["project_chunks"]) == ("9377", "9395")
    assert corpus["chunk_scheme"] == "fixed-v0"


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ({"query_prefix": "b0", "query_encoding": "passage: query: {q}"}, "b0"),
        ({"query_prefix": "fixed", "query_encoding": "query: {q}"}, "fixed"),
        ({}, "unavailable"),
    ],
)
def test_provenance_records_the_query_encoding_the_rag_service_runs(reported, expected):
    """UMRS_RAG_QUERY_PREFIX lives in the rag container, which stack_settings
    does not read: a run records it from /rag/stats, so receipt-4 parity (b0)
    and the B0' arms (fixed) are told apart."""
    probe = {"ollama_tags": {"models": []}, "rag_stats": {"embedding_model": "e5", **reported}}
    embedder = backends.model_provenance(probe, "llama3.2:3b")["embedder"]
    assert str(embedder["query_prefix"]).startswith(expected)
    assert embedder["query_encoding"] == reported.get("query_encoding", embedder["query_prefix"])


def test_provenance_says_unavailable_instead_of_empty(monkeypatch):
    def fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "standalone.sh")

    monkeypatch.setattr(backends, "_git", lambda *_a: "")
    monkeypatch.setattr(backends, "_exec", fail)
    monkeypatch.setattr(backends, "sql", fail)
    prov = backends.provenance()
    assert prov["settings"].startswith("unavailable") and prov["store"].startswith("unavailable")
    assert prov["corpus"].startswith("unavailable")
    assert prov["models"]["chat_model_digest"].startswith("unavailable")


def test_the_server_probe_hashes_like_the_host_and_runs_standalone(tmp_path):
    pkg = tmp_path / "umrs"
    (pkg / "knowledge" / "retrieval").mkdir(parents=True)
    (pkg / "api").mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "knowledge" / "retrieval" / "dense.py").write_text("x = 1\n")
    (pkg / "api" / "rag_retrieve.py").write_text("y = 2\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "UMRS_RAG_DIRECT_URL": "http://127.0.0.1:9"}
    source = Path(server_probe.__file__).read_text()
    argv = [sys.executable, "-c", source, "http://127.0.0.1:9"]
    out = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60, check=True)
    probe = json.loads(out.stdout)
    assert probe["code"] == server_probe.files_sha256(str(pkg)) and probe["code"]["files"] == 2
    assert str(probe["ollama_tags"]).startswith("unavailable")


def test_psql_variables_bind_values_and_refuse_bad_names(monkeypatch):
    seen = []

    def fake_run(argv, **_kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout="\x1b[0mg-1\t5\n")

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    assert backends.sql("SELECT :'graph'", graph="it's") == [["g-1", "5"]]
    assert seen[0][-1] == "--set=graph=it's" and "it's" not in seen[0][-2]
    assert "printf" in seen[0][5] and "ON_ERROR_STOP=1" in seen[0][5]
    with pytest.raises(ValueError):
        backends.sql("SELECT 1", **{"bad name": "x"})


# ── ADR-093 D-14: the large profile and the remote reader (C21) ──────────────

READER_GW = "https://rikyu.example"
STACK_GW = "http://localhost:8080"
#: The large profile the `retrieve:large` preset and a large tool reader are
#: sized by (ADR-093 D-14): k_max 10, pool 100, pack 8,000 tokens until
#: MD-14; the medium sizes since ADR-093 MD-14, 2026-10-13 (6, 50, 2,000).
_LARGE = RETRIEVAL_PROFILES["large"]
#: The tokens a large reader's knowledge_query shows of each hit (C23b): the
#: large pack shared over its k_max hits (800 until MD-14; 333 since).
_LARGE_SHARE = _LARGE.pack_tokens // _LARGE.k_max


def _large_lanes() -> dict:
    return _lanes_for(arms.PRESETS["large"])


def test_the_large_preset_is_the_d14_large_profile():
    from r4_contracts.models import RETRIEVAL_PROFILES, model_size_class

    knobs, large = arms.PRESETS["large"], RETRIEVAL_PROFILES["large"]
    assert knobs == {
        "profile": "large",
        "reader_model": "kimi-k3",
        "cutoff": "adaptive",
        "k_max": large.k_max,
        "pool": large.pool,
        "per_artifact": 2,
        "lexical": "on",
        "rerank": "off",
        "merge": "on",
    }
    assert (knobs["k_max"], knobs["pool"], knobs["merge"]) == (large.k_max, large.pool, large.merge)
    assert model_size_class(knobs["reader_model"]) == "large"
    assert arms.default_prompt("retrieve:large") == "pack-soft" and "retrieve:large" in arms.ARMS


def test_the_large_preset_is_the_merge_preset_at_the_large_sizes():
    """One factor against ``retrieve:merge``: the sizes and the profile."""
    merge, large = arms.PRESETS["merge"], arms.PRESETS["large"]
    changed = {knob for knob in large if large[knob] != merge.get(knob)}
    # Since MD-14 (2026-10-13) the large pool equals the merge preset's 50.
    assert changed == {"profile", "reader_model", "k_max"}


def test_the_large_preset_sends_the_profile_and_the_reader_through_the_client():
    rag = _FakeRag(_result(lanes=_large_lanes()), has_retrieve=True)
    got = arms.ARMS["retrieve:large"](_stack(rag), _CLAIM)
    _query, kwargs = rag.calls[0]
    assert (kwargs["profile"], kwargs["reader_model"], kwargs["k_max"]) == (
        "large",
        "kimi-k3",
        _LARGE.k_max,
    )
    assert got.meta["knobs"]["profile"] == "large" and got.meta["preset"] == "large"


class _PreProfileRag:
    """``RAGClient.retrieve`` before ADR-093 C19: no ``profile``, no ``reader_model``."""

    # the signature is the fixture: these names and no others
    def retrieve(self, query, *, project_id=None, graphs=None, trace=False, k_max=None):  # noqa: ARG002
        raise AssertionError("called with knobs it does not take")


def test_a_client_without_the_profile_knobs_is_bypassed_by_a_raw_get():
    seen = []

    class Http:
        def get(self, _url, **kwargs):
            seen.append(kwargs["params"])
            return _result(lanes=_lanes_for(kwargs["params"]))

    got = arms.ARMS["retrieve:large"](_stack(_PreProfileRag(), Http()), _CLAIM)
    assert (seen[0]["profile"], seen[0]["reader_model"], seen[0]["pool"]) == (
        "large",
        "kimi-k3",
        _LARGE.pool,
    )
    assert got.meta["preset"] == "large"


def test_accepts_reads_the_signature():
    assert backends.accepts(_PreProfileRag().retrieve, {"k_max": 4})
    assert not backends.accepts(_PreProfileRag().retrieve, {"profile": "large"})
    assert backends.accepts(_FakeRag({}, has_retrieve=True).retrieve, {"profile": "large"})


@pytest.mark.parametrize("report", [None, {"name": "small", "reader_model": "kimi-k3"}])
def test_a_profile_the_server_did_not_apply_is_an_error(report):
    """An older server ignores ``profile`` and cuts with the small partial cap:
    that row is never filed under the large preset's name."""
    lanes = {key: value for key, value in _large_lanes().items() if key != "profile"}
    if report:
        lanes["profile"] = report
    rag = _FakeRag(_result(lanes=lanes), has_retrieve=True)
    with pytest.raises(arms.PresetNotMeasured, match="profile="):
        arms.ARMS["retrieve:large"](_stack(rag), _CLAIM)


def test_a_preset_without_a_profile_needs_no_profile_report():
    assert arms.unmet_profile(arms.PRESETS["merge"], {}) == []
    assert arms.unmet_profile(arms.PRESETS["large"], {"profile": {"name": "large"}}) == []


def test_the_summary_counts_the_profile_by_name():
    """The server's report carries ``status: ok`` beside the name (umrs
    ``profiles.report``): every profile would count as ``ok`` if read by status."""
    rows = [
        {
            "claim": n,
            "label": "SUPPORT",
            "retrieve_seconds": 0.1,
            "meta": {"lanes": {"profile": _profile_report(name, reader), "lex": {"status": "ok"}}},
        }
        for n, (name, reader) in enumerate((("large", "kimi-k3"), ("small", None)))
    ]
    assert run.summarize(rows, {})["lane_status"] == {
        "lex": {"ok": 2},
        "profile": {"large": 1, "small": 1},
    }


class _Chat:
    """A chat endpoint that records the model each verdict asked for."""

    def __init__(self, model: str = "") -> None:
        self.model, self.asked = model, []

    def chat_completion(self, _messages, **kwargs):
        self.asked.append(kwargs["model"])
        return {
            "choices": [{"message": {"content": '{"verdict": "SUPPORT", "evidence": ["[1]"]}'}}]
        }


_OPTS = SimpleNamespace(prompt=None, order="rank", scorer_gate=False, model="llama3.2:3b")


def _large_stack(reader=None):
    rag = _FakeRag(_result(lanes=_large_lanes()), has_retrieve=True)
    stack, own = _stack(rag), _Chat()
    stack.client.chat_completion = own.chat_completion
    stack.reader = reader
    return stack, rag, own


def test_the_remote_reader_is_used_only_for_the_verdict_call():
    reader = _Chat(model="kimi-k3")
    stack, rag, own = _large_stack(reader)
    row = run._claim_row(stack, arms.ARMS["retrieve:large"], _CLAIM, do_infer=True, opts=_OPTS)
    assert len(rag.calls) == 1 and own.asked == []  # retrieval on the stack, no stack chat
    assert reader.asked == ["kimi-k3"] and row["verdict"] == "SUPPORT"


def test_without_a_reader_the_stack_reads_with_its_own_model():
    stack, rag, own = _large_stack()
    run._claim_row(stack, arms.ARMS["retrieve:large"], _CLAIM, do_infer=True, opts=_OPTS)
    assert len(rag.calls) == 1 and own.asked == ["llama3.2:3b"]
    assert run.verdict_reader(stack, "m") == (stack.client, "m")


def _r4_dir(path: Path, gateway: str, key: str | None = None) -> Path:
    profile = {"gateway_url": gateway, **({"api_key": key} if key else {})}
    (path / "r4").mkdir(parents=True)
    (path / "r4" / "config.json").write_text(json.dumps({"profiles": {"default": profile}}))
    return path


@pytest.fixture
def stack_env(tmp_path, monkeypatch):
    """The stand-alone run's r4 identity (an XDG dir, R4_* variables, r4's
    process singletons): the reader must neither read nor change any of it."""
    import r4.auth
    import r4.config
    import r4.project

    stack_dir = _r4_dir(tmp_path / "stack", STACK_GW, "sk-stack-file")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(stack_dir))
    monkeypatch.setenv("R4_GATEWAY_URL", STACK_GW)
    monkeypatch.setenv("R4_API_KEY", "sk-stack-env")
    auth = SimpleNamespace(profile="default", get_auth_header=lambda: {"Authorization": "x"})
    singletons = (auth, object(), object())
    for module, attr, value in zip(
        (r4.auth, r4.project, r4.config),
        ("_auth_manager", "_project_manager", "_config"),
        singletons,
        strict=True,
    ):
        monkeypatch.setattr(module, attr, value)
    return SimpleNamespace(dir=stack_dir, singletons=singletons)


def _stack_untouched(env) -> bool:
    import r4.auth
    import r4.config
    import r4.project

    now = (r4.auth._auth_manager, r4.project._project_manager, r4.config._config)
    same = all(a is b for a, b in zip(now, env.singletons, strict=True))
    environ = (os.environ.get(k) for k in ("XDG_CONFIG_HOME", "R4_GATEWAY_URL", "R4_API_KEY"))
    return same and tuple(environ) == (str(env.dir), STACK_GW, "sk-stack-env")


def test_the_reader_client_is_built_from_its_own_directory(tmp_path, stack_env):
    reader_dir = _r4_dir(tmp_path / "rikyu", READER_GW, "sk-reader")
    reader = backends.ReaderClient(reader_dir, "kimi-k3", stack_gateway=STACK_GW)
    config = reader._client.config
    assert (config.gateway_url, config.api_key) == (READER_GW, "sk-reader")
    assert _stack_untouched(stack_env)
    assert not hasattr(reader, "rag") and not hasattr(reader, "knowledge")


class _ChatGateway:
    """httpx as r4's HTTPCore sees it: records each POST, answers a verdict."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, url, **kwargs):
        import httpx

        headers = kwargs.get("headers") or {}
        self.sent.append(
            {
                "url": url,
                "auth": headers.get("Authorization"),
                "model": (kwargs.get("json") or {}).get("model"),
                "xdg": os.environ.get("XDG_CONFIG_HOME"),
            }
        )
        reply = {"role": "assistant", "content": '{"verdict": "SUPPORT", "evidence": ["[1]"]}'}
        body = {
            "choices": [{"message": reply}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))


def test_the_verdict_goes_to_the_reader_gateway_with_the_reader_key(
    tmp_path, stack_env, monkeypatch
):
    """End to end through the real r4 client: retrieval on the stack, the one
    verdict call to the reader's gateway with the reader's own key."""
    from r4._http import HTTPCore

    gateway = _ChatGateway()
    monkeypatch.setattr(HTTPCore, "_client", lambda _self, _timeout=None: gateway)
    reader_dir = _r4_dir(tmp_path / "rikyu", READER_GW, "sk-reader")
    reader = backends.ReaderClient(reader_dir, "glm-5.3", stack_gateway=STACK_GW)
    stack, rag, own = _large_stack(reader)
    row = run._claim_row(stack, arms.ARMS["retrieve:large"], _CLAIM, do_infer=True, opts=_OPTS)
    assert gateway.sent == [
        {
            "url": f"{READER_GW}/gw/llm/v1/chat/completions",
            "auth": "Bearer sk-reader",
            "model": "glm-5.3",
            "xdg": str(reader_dir.resolve()),
        }
    ]
    assert len(rag.calls) == 1 and own.asked == [] and row["usage"]["prompt_tokens"] == 9
    assert _stack_untouched(stack_env)


def test_a_reader_directory_that_cannot_read_is_refused(tmp_path, stack_env):
    with pytest.raises(backends.ReaderConfigError, match="no r4 config"):
        backends.ReaderClient(tmp_path / "missing", "kimi-k3")
    same = _r4_dir(tmp_path / "same", STACK_GW + "/", "sk-reader")
    with pytest.raises(backends.ReaderConfigError, match="stack's own gateway"):
        backends.ReaderClient(same, "kimi-k3", stack_gateway=STACK_GW)
    keyless = _r4_dir(tmp_path / "keyless", READER_GW)
    with pytest.raises(backends.ReaderConfigError, match="no credential"):
        backends.ReaderClient(keyless, "kimi-k3", stack_gateway=STACK_GW)
    assert _stack_untouched(stack_env)


@pytest.mark.parametrize(
    ("url", "public"),
    [
        ("https://rikyu.example", "https://rikyu.example"),
        ("https://user:pw@rikyu.example:8443/api?token=t0k#frag", "https://rikyu.example:8443/api"),
        ("", ""),
        (None, ""),
    ],
)
def test_a_public_url_carries_no_credential(url, public):
    assert backends.public_url(url) == public


def test_the_reader_provenance_names_the_gateway_and_model_never_a_key(tmp_path, stack_env):
    leaky = "https://user:pw@rikyu.example/api?token=t0k"
    reader = backends.ReaderClient(
        _r4_dir(tmp_path / "rikyu", leaky, "sk-reader"), "kimi-k3", stack_gateway=STACK_GW
    )
    prov = reader.provenance()
    assert (prov["kind"], prov["gateway_url"], prov["model"]) == (
        "remote",
        "https://rikyu.example/api",
        "kimi-k3",
    )
    assert prov["retrieval_profile"] == "large" and prov["r4_profile"] == "default"
    text = json.dumps(prov)
    assert not any(secret in text for secret in ("sk-reader", "pw@", "t0k", "sk-stack"))


def test_the_result_provenance_records_the_reader(monkeypatch):
    prov, _calls = _fake_stack_provenance(monkeypatch)
    assert prov["reader"] == {"kind": "stack", "model": "llama3.2:3b", "retrieval_profile": "small"}
    remote = SimpleNamespace(provenance=lambda: {"kind": "remote", "model": "kimi-k3"})
    assert backends.provenance(reader=remote)["reader"] == {"kind": "remote", "model": "kimi-k3"}


def _infer_args(*extra: str):
    return run._parser().parse_args(["infer", "--data", "d", "--arms", "retrieve:large", *extra])


def test_no_remote_reader_is_built_without_reader_config(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a remote reader was built")

    monkeypatch.setattr(backends, "ReaderClient", refuse)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/somewhere/rikyu")
    args = _infer_args()
    assert (args.reader_config, args.reader_model) == (None, None)
    assert run.remote_reader(args, _stack(None)) is None
    answer = run._parser().parse_args(["answer", "--data", "d"])
    assert run.remote_reader(answer, _stack(None)) is None


def test_setup_wires_the_reader_into_the_stack_and_the_provenance(monkeypatch):
    built, recorded = [], []

    def reader(config_dir, model, *, stack_gateway):
        built.append((config_dir, model, stack_gateway))
        return SimpleNamespace(model=model, gateway_url=READER_GW, config_dir=config_dir)

    monkeypatch.setattr(run.scifact, "load", lambda *_a, **_k: scifact.Corpus())
    stacks = iter([_stack(None), _stack(None)])
    monkeypatch.setattr(run.backends, "Stack", lambda **_k: next(stacks))
    monkeypatch.setattr(run.backends, "provenance", lambda **k: recorded.append(k) or {})
    monkeypatch.setattr(run.backends, "ReaderClient", reader)
    _corpus, plain, _prov = run._setup(_infer_args())
    _corpus, remote, _prov = run._setup(
        _infer_args("--reader-config", "/x", "--reader-model", "kimi-k3")
    )
    assert plain.reader is None and recorded[0]["reader"] is None
    assert built == [("/x", "kimi-k3", "http://gw/")] and recorded[1]["reader"] is remote.reader


@pytest.mark.parametrize(
    ("argv", "why"),
    [
        (["infer", "--arms", "closed", "--reader-model", "kimi-k3"], "go together"),
        (["infer", "--arms", "closed", "--reader-config", "/x"], "go together"),
        (
            ["infer", "--arms", "agent", "--reader-config", "/x", "--reader-model", "kimi-k3"],
            "the agent arm reads through its session",
        ),
        (
            [
                "retrieve",
                "--arms",
                "retrieve:large",
                "--reader-config",
                "/x",
                "--reader-model",
                "m",
            ],
            "unrecognized arguments: --reader-config",
        ),
    ],
)
def test_the_reader_flags_go_together_on_verdict_arms_only(argv, why, capsys):
    with pytest.raises(SystemExit):
        run.main([argv[0], "--data", "d", *argv[1:]])
    assert why in capsys.readouterr().err


@pytest.mark.parametrize(
    ("model", "suffix"), [("kimi-k3", "-r-kimi-k3"), ("GLM-5.3-FP8@rikyu", "-r-GLM-5.3-FP8-rikyu")]
)
def test_a_remote_reader_names_its_result_file(model, suffix):
    args = _infer_args("--reader-config", "/x", "--reader-model", model)
    assert run._variant(args, "retrieve:large") == suffix
    assert run._variant(_infer_args(), "retrieve:large") == ""


def test_a_result_file_records_the_remote_reader_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    args = _infer_args("--reader-config", "/x", "--reader-model", "kimi-k3")
    rows = [{"claim": 1, "label": "SUPPORT", "error": "boom"}]
    record = run._write("infer", "retrieve:large", args, rows, {}, {"reader": {}}, "T")
    assert (record["reader_model"], record["model"]) == ("kimi-k3", "llama3.2:3b")
    assert record["preset"] == arms.PRESETS["large"]
    assert [p.name for p in tmp_path.iterdir()] == ["T-infer-dev-retrieve-large-r-kimi-k3.json"]
    local = run._write("infer", "closed", _infer_args(), rows, {}, {}, "U")
    assert local["reader_model"] is None
    capsys.readouterr()


# ── ADR-093 C21 review, second pass (2026-10-10) ─────────────────────────────
# The remote reader's replies, retries, the retrieve bound and an expired login.

_NEI = "NOT_ENOUGH_INFO"
_VERDICT_JSON = '{"verdict": "SUPPORT", "evidence": ["[1]"]}'


def _chat_reply(content, *, finish: str | None = "stop", tokens: int = 12, reasoning: str = ""):
    """A chat reply as r4's client returns it (``ChatResponse``)."""
    from r4.chat_types import ChatResponse

    message = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return ChatResponse.from_api_response(
        {
            "model": "glm-5.3",
            "choices": [{"finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 9, "completion_tokens": tokens},
        }
    )


def _cut_reasoning():
    """glm-5.3 at a 200-token limit: reasoning only, cut, no content."""
    reasoning = "[1] clearly supports the claim"
    return _chat_reply(None, finish="length", tokens=200, reasoning=reasoning)


class _Reader:
    """A verdict reader that answers one fixed reply and records each budget."""

    def __init__(self, reply, model: str = "glm-5.3") -> None:
        self.reply, self.model, self.budgets = reply, model, []

    def chat_completion(self, _messages, **kwargs):
        self.budgets.append(kwargs.get("max_tokens"))
        return self.reply


def test_a_reply_cut_at_its_output_limit_is_never_a_verdict():
    """eval-c21-1: the verdict was read from the response's repr (its reasoning)."""
    with pytest.raises(infer.ReplyNotScored) as cut:
        infer.ask(_Reader(_cut_reasoning()), "glm-5.3", "c", "[1] scifact-11.md\nt", "pack-soft")
    assert cut.value.fields == {
        "finish_reason": "length",
        "completion_tokens": 200,
        "max_tokens": None,
    }


def test_a_cut_reply_is_an_error_row_that_says_why():
    stack, _rag, _own = _large_stack(_Reader(_cut_reasoning()))
    rows = run._run_arm(stack, [_CLAIM], "retrieve:large", do_infer=True, opts=_OPTS)
    assert "ReplyNotScored" in rows[0]["error"] and "verdict" not in rows[0]
    assert (rows[0]["finish_reason"], rows[0]["completion_tokens"]) == ("length", 200)


@pytest.mark.parametrize(
    "reply",
    [
        _chat_reply(None, reasoning="[1] supports the claim"),
        _chat_reply(""),
        _chat_reply("<think>[1] supports it, so SUPPORT"),
        _chat_reply("<think>SUPPORT</think>  "),
    ],
    ids=["reasoning-only", "empty", "unclosed-think", "think-then-nothing"],
)
def test_reasoning_or_an_empty_reply_is_never_read_as_a_verdict(reply):
    with pytest.raises(infer.ReplyNotScored):
        infer.ask(_Reader(reply), "glm-5.3", "c", "ctx", "json")


def test_the_answer_after_a_think_block_is_the_verdict():
    content = '<think>it might SUPPORT</think>{"verdict": "CONTRADICT", "evidence": ["[1]"]}'
    out = infer.ask(_Reader(_chat_reply(content), model="m"), "m", "c", "ctx", "json")
    assert (out["verdict"], out["reply"][:1], out["finish_reason"]) == ("CONTRADICT", "{", "stop")


@pytest.mark.parametrize(
    ("model", "budget"),
    [("glm-5.3", None), ("kimi-k3", None), ("qwen3-235b-a22b", None), ("llama3.2:3b", 200)],
)
def test_a_reasoning_reader_gets_room_to_finish_and_the_3b_keeps_b0s_budget(model, budget):
    """A reasoning or large reader sends no limit (the provider's default, the
    D-14 large profile's); the stack's 3B keeps B0's 200, so it pairs with B0."""
    reader = _Reader(_chat_reply(_VERDICT_JSON), model=model)
    out = infer.ask(reader, model, "c", "ctx", "json")
    assert reader.budgets == [budget] and out["max_tokens"] == budget


def test_a_verdict_row_records_how_the_reply_ended():
    stack, _rag, _own = _large_stack(_Reader(_chat_reply(_VERDICT_JSON, tokens=30)))
    row = run._claim_row(stack, arms.ARMS["retrieve:large"], _CLAIM, do_infer=True, opts=_OPTS)
    assert (row["verdict"], row["finish_reason"], row["completion_tokens"], row["max_tokens"]) == (
        "SUPPORT",
        "stop",
        30,
        None,
    )


def test_a_result_file_records_its_schema_and_verdict_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    rows = [{"claim": 1, "label": "SUPPORT", "error": "boom"}]
    remote_args = _infer_args("--reader-config", "/x", "--reader-model", "kimi-k3")
    remote = run._write("infer", "retrieve:large", remote_args, rows, {}, {}, "T")
    local = run._write("infer", "closed", _infer_args(), rows, {}, {}, "U")
    assert (remote["verdict_max_tokens"], local["verdict_max_tokens"]) == (None, 200)
    assert remote["schema_version"] == local["schema_version"] == comparisons.SCHEMA_VERSION == 2
    capsys.readouterr()


def _fails_once(exc: Exception):
    calls: list[int] = []

    def call():
        calls.append(1)
        if len(calls) == 1:
            raise exc
        return "ok"

    return call, calls


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitError(retry_after=1),
        R4TimeoutError(),
        R4ConnectionError(),
        APIError("bad gateway", status_code=502),
    ],
    ids=["429", "timeout", "connection", "502"],
)
def test_a_rate_limit_timeout_or_dropped_connection_is_retried(exc, monkeypatch):
    """eval-c21-5: only a 5xx was retried; r4 raises these as siblings of APIError."""
    slept: list[float] = []
    monkeypatch.setattr(infer.time, "sleep", slept.append)
    call, calls = _fails_once(exc)
    assert infer.with_retries(call) == "ok" and (len(calls), len(slept)) == (2, 1)


def test_a_rate_limit_waits_its_retry_after_and_a_refused_login_is_not_retried():
    assert infer.retry_pause(RateLimitError(retry_after=45), 10.0) == 45.0
    assert infer.retry_pause(RateLimitError(retry_after=10**6), 10.0) == infer.MAX_RETRY_AFTER
    assert infer.retry_pause(AuthenticationError(status_code=401), 10.0) is None
    assert infer.retry_pause(APIError("bad request", status_code=400), 10.0) is None


def test_a_stage_down_for_an_outage_that_heals_is_retried():
    """``reranker unreachable (ReadTimeout)`` under host load heals; a missing
    migration does not (``test_a_preset_whose_stage_did_not_run_is_an_error``)."""
    lanes = _lanes_for(arms.PRESETS["rerank"])
    lanes["rerank"] = {"status": "unavailable", "reason": "reranker unreachable (ReadTimeout)"}
    rag = _FakeRag(_result(lanes=lanes), has_retrieve=True)
    with pytest.raises(infer.TransientError, match="rerank"):
        arms.ARMS["retrieve:rerank"](_stack(rag), _CLAIM)


def test_a_retrieve_on_the_client_path_waits_as_long_as_the_raw_get():
    """eval-c21-6: the client method fell back to its own 10 s default."""
    rag = _FakeRag(_result(lanes=_large_lanes()), has_retrieve=True)
    got = arms.ARMS["retrieve:large"](_stack(rag), _CLAIM)
    assert rag.calls[0][1]["timeout"] == backends.RETRIEVE_TIMEOUT_S == 120.0
    assert got.meta["retrieve_timeout_s"] == 120.0


def test_the_real_client_retrieve_is_not_bounded_by_its_10_s_default(monkeypatch):
    import httpx

    from r4._http import HTTPCore
    from r4.client import Client
    from r4.config import Config

    bounds: list = []

    class Gateway:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, params=None, **_kwargs):
            body = _result(lanes=_lanes_for(params))
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    def client_at(_self, timeout=None):
        bounds.append(timeout)
        return Gateway()

    monkeypatch.setattr(HTTPCore, "_client", client_at)
    monkeypatch.delenv("R4_RAG_RETRIEVE_TIMEOUT", raising=False)
    config = Config(gateway_url="http://gw", api_key="k", project_id="default", timeout=300)
    got = arms.ARMS["retrieve:large"](_stack(Client(config=config).rag), _CLAIM)
    assert bounds == [120.0] and got.meta["retrieve_timeout_s"] == 120.0


class _ExpiringReader(_Reader):
    """Answers once, then refuses the login (an OIDC token r4 never refreshes)."""

    def chat_completion(self, messages, **kwargs):
        if self.budgets:
            raise AuthenticationError(status_code=401)
        return super().chat_completion(messages, **kwargs)


def test_an_expired_reader_login_stops_the_run_instead_of_filing_error_rows():
    """eval-c21-7: every later claim became an error row, the summary covered
    only the claims before the expiry."""
    stack, rag, _own = _large_stack(_ExpiringReader(_chat_reply(_VERDICT_JSON)))
    claims = [scifact.Claim(i, "c", "SUPPORT", frozenset({"11"}), frozenset()) for i in range(1, 6)]
    with pytest.raises(AuthenticationError):
        run._run_arm(stack, claims, "retrieve:large", do_infer=True, opts=_OPTS)
    assert len(rag.calls) == 2  # stopped at the refused call


def test_a_refused_reader_names_the_login_that_renews_it(tmp_path, stack_env, monkeypatch):
    reader_dir = _r4_dir(tmp_path / "rikyu", READER_GW, "sk-reader")
    reader = backends.ReaderClient(reader_dir, "kimi-k3", stack_gateway=STACK_GW)

    def refuse(*_args, **_kwargs):
        raise AuthenticationError(status_code=401)

    monkeypatch.setattr(reader._client, "chat_completion", refuse)
    with pytest.raises(backends.ReaderConfigError, match="r4 login") as refused:
        reader.chat_completion([], model="kimi-k3")
    assert run.is_fatal(refused.value) and _stack_untouched(stack_env)


def test_a_reader_whose_login_expired_is_stopped_before_an_arm(tmp_path, stack_env, monkeypatch):
    reader_dir = _r4_dir(tmp_path / "rikyu", READER_GW, "sk-reader")
    reader = backends.ReaderClient(reader_dir, "kimi-k3", stack_gateway=STACK_GW)
    monkeypatch.setattr(backends, "_has_credential", lambda _config: False)
    with pytest.raises(backends.ReaderConfigError, match="expired"):
        reader.check()


def test_each_arm_checks_the_reader_login_before_it_runs(monkeypatch):
    class Expired:
        model = "kimi-k3"

        def check(self):
            raise backends.ReaderConfigError("the reader's login has expired")

    stack = _stack(None)
    stack.reader = Expired()
    monkeypatch.setattr(run, "_setup", lambda _args: (scifact.Corpus(), stack, {}))
    monkeypatch.setattr(run, "_run_arm", lambda *_a, **_k: pytest.fail("an arm ran"))
    args = _infer_args("--reader-config", "/x", "--reader-model", "kimi-k3")
    with pytest.raises(backends.ReaderConfigError):
        run.cmd_run(args, do_infer=True)


# Citations, passage numbers, lanes and the E-B abstention.


def _name_stack(art_to_doc: dict | None = None):
    """A stack that maps refs to SciFact docs by name (and artifact id)."""
    return SimpleNamespace(doc_of=lambda *refs: backends.doc_of_refs(art_to_doc or {}, *refs))


@pytest.mark.parametrize("ref", ["14717500", "[14717500]", "14717500.md", "scifact-14717500.md"])
def test_citing_a_shown_doc_by_its_bare_scifact_id_is_not_a_phantom(ref):
    """eval-c21-2: the context labels a passage ``scifact-14717500.md``; a 3B
    cites ``14717500`` (134 of 338 refs in the B0 oracle file)."""
    assert run.cited_set(_name_stack(), [ref], ["14717500"], {"14717500"}) == ["14717500"]


def test_a_bare_id_of_a_doc_the_reader_was_not_shown_is_a_phantom():
    cited = run.cited_set(_name_stack(), ["99999999"], ["14717500"], {"14717500"})
    assert cited == ["phantom:99999999"]


def test_the_agent_arm_maps_a_bare_id_of_a_doc_it_retrieved(monkeypatch):
    turn = {
        "reply": json.dumps({"verdict": "SUPPORT", "evidence": ["14717500", "GNB2"]}),
        "docs_seen": ["14717500"],
        "seconds": 1.0,
        "usage": {},
        "retrieval_calls": ["rag.retrieve"],
    }
    monkeypatch.setattr(backends, "agent_turn", lambda *_args: turn)
    row = run._agent_row(_name_stack(), _CLAIM, "m", 1)
    assert row["cited"] == ["14717500", "phantom:GNB2"]


@pytest.mark.parametrize(
    ("ref", "numbers"),
    [
        ("2", [2]),
        ("[2]", [2]),
        ("passage 2", [2]),
        ("Source #3", [3]),
        ("1, 2", [1, 2]),
        ("[1][3]", [1, 3]),
        ("1 and 2", [1, 2]),
        ("GNB2", None),
        ("0.5", None),
        ("HR 1.2", None),
        ("ApoE4 allele", None),
        ("123", None),
    ],
)
def test_only_a_passage_reference_is_read_as_a_passage_number(ref, numbers):
    """eval-c21-19: every digit in a ref was joined into one number."""
    assert run.passage_numbers(ref) == numbers


def test_a_gene_name_is_not_credited_to_the_passage_its_digit_names():
    shown = ["11", "22", "33"]
    cited = run.cited_set(_name_stack(), ["GNB2", "[1][2]"], shown, set(shown))
    assert cited == ["11", "22", "phantom:GNB2"]


def test_two_chunks_of_another_artifact_take_one_rank_like_a_scifact_doc():
    """eval-c21-20: the sentinel was keyed on the node, so another artifact's
    two chunks took two ranks while a SciFact doc's two chunks take one."""
    candidates = [
        {"node_id": "x1", "artifact_id": "art-0000000000aa", "scores": {"dense": 0.90}},
        {"node_id": "x2", "artifact_id": "art-0000000000aa", "scores": {"dense": 0.88}},
        {"node_id": "n2", "artifact_id": "art-000000000002", "scores": {"dense": 0.86}},
    ]
    result = _result(trace={"mode": "dense", "candidates": candidates})
    got = backends.retrieved_from_result(result, _doc_of)
    claim = scifact.Claim(1, "c", "SUPPORT", frozenset({"22"}), frozenset({"22"}))
    row = run._row(claim, got, got.context, None)
    assert row["ranked"] == ["other:art-0000000000aa", "22"]
    assert row["retrieval"]["mrr@10"] == 0.5


def _k3_payload(rag_lane: dict) -> dict:
    hit = {"source_id": "n1", "title": "scifact-11.md", "content": "x", "score": 0.9}
    lanes = {"embeddings_lane": {"lane": "similarity", "model": "e5"}, "rag_lane": rag_lane}
    return {"query": {"results": [hit], "total_results": 1, **lanes}}


def _k3_stack(payload: dict):
    stack = _stack(None)
    stack.client = SimpleNamespace(knowledge=SimpleNamespace(query=lambda *_a, **_k: payload))
    return stack


@pytest.mark.parametrize(
    ("reason", "error"),
    [
        ("lexical lane failed (QueryCanceled)", infer.TransientError),
        ("knowledge_nodes.search_text is missing (migration 0120)", infer.NotMeasured),
    ],
)
def test_a_k3_answer_without_its_lexical_lane_is_not_a_k3_all_result(reason, error):
    """eval-c21-16: only the dense lane was checked."""
    payload = _k3_payload({"lane": "lexical", "status": "unavailable", "reason": reason})
    with pytest.raises(error, match="lexical lane unavailable"):
        arms.ARMS["k3-all"](_k3_stack(payload), _CLAIM)


def test_the_k3_lexical_lane_is_recorded_and_does_not_fail_the_embeddings_scope():
    lane = {"lane": "lexical", "status": "unavailable", "reason": "x"}
    got = arms.ARMS["k3-embeddings"](_k3_stack(_k3_payload(lane)), _CLAIM)
    assert got.meta["rag_lane"] == lane and got.shown == ["11"]


def _answer_claim_row(answer: str, metadata: dict):
    rag = _Answering(answer, [_source("n1", "11")], metadata)
    return run._answer_row(_stack(rag), _CLAIM, None)


def test_an_answer_that_names_a_gap_is_a_partial_answer_not_an_abstention():
    """fix-regressions-4: the phrase anywhere in the answer made it an abstention;
    the server's post-check (metadata.abstained) says it is a partial answer."""
    answer = "Aspirin lowers the risk of stroke [1]. The dose is not in the knowledge base."
    profile = _profile_report("small", "llama3.2:3b")
    metadata = {"abstained": False, "partial_answer": True, "lanes": {"profile": profile}}
    row = _answer_claim_row(answer, metadata)
    assert (row["abstained"], row["partial_answer"], row["abstained_basis"]) == (
        False,
        True,
        "server",
    )
    assert (row["abstained_phrase"], row["profile"]) == (True, "small")


def test_an_older_server_without_the_flag_is_read_by_the_phrase():
    row = _answer_claim_row("The knowledge base does not answer this.", {})
    assert (row["abstained"], row["abstained_basis"], row["abstained_server"]) == (
        True,
        "phrase",
        None,
    )


def test_the_answer_summary_counts_where_the_server_and_the_phrase_disagree():
    partial = _answer_claim_row(
        "X [1]. The dose is not in the knowledge base.", {"abstained": False}
    )
    refused = _answer_claim_row("The knowledge base does not answer this.", {"abstained": True})
    summary = run.answer_summary([partial, refused])
    assert (summary["abstain_rate_evidence"], summary["abstain_rate_evidence_phrase"]) == (0.5, 1.0)
    assert (summary["abstain_disagreement"], summary["abstained_basis"]) == (0.5, {"server": 2})


def test_a_run_that_names_no_reader_does_not_claim_the_medium_profile():
    """eval-c21-15: ``answer`` names no model, and UMRS sizes /rag/query from its
    own registry model; a ``retrieve`` run reads no verdicts at all."""
    prov = backends.reader_provenance(None, None)
    assert prov["retrieval_profile"] == backends.SERVER_RESOLVED and "medium" not in json.dumps(
        prov
    )
    assert backends.reader_provenance(None, "llama3.2:3b", reads_verdicts=False) is None


def test_setup_says_which_commands_read_verdicts(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(run.scifact, "load", lambda *_a, **_k: scifact.Corpus())
    stack = _stack(None)
    monkeypatch.setattr(run.backends, "Stack", lambda **_k: stack)
    monkeypatch.setattr(run.backends, "provenance", lambda **k: recorded.append(k) or {})
    run._setup(run._parser().parse_args(["retrieve", "--data", "d", "--arms", "legacy"]))
    run._setup(run._parser().parse_args(["answer", "--data", "d"]))
    assert [(k["reads_verdicts"], k["model"]) for k in recorded] == [
        (False, "llama3.2:3b"),
        (True, None),
    ]


# ADR-093 D-14 (C23): the tool arms read what a session of the reader reads;
# knowledge_query asks K3 for that reader's profile k_max.


def test_the_stack_builds_the_companion_tools_for_its_session_model(monkeypatch):
    built: list[dict] = []

    def get_builtin_tools(_client, **kwargs):
        built.append(kwargs)
        return [{"name": "knowledge_query", "execute": lambda **_k: "{}"}]

    monkeypatch.setattr("r4.companion.tools.get_builtin_tools", get_builtin_tools)
    plain, sized = _stack(None), _stack(None)
    sized.session_model = "llama3.2:3b"
    plain._tool("knowledge_query")
    sized._tool("knowledge_query")
    assert built == [{"session_model": None}, {"session_model": "llama3.2:3b"}]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["infer", "--arms", "tool:knowledge_query"], "llama3.2:3b"),
        (["retrieve", "--arms", "tool:knowledge_query", "--model", "qwen2.5:14b"], "qwen2.5:14b"),
        (
            [
                "infer",
                "--arms",
                "tool:knowledge_query",
                "--reader-config",
                "/x",
                "--reader-model",
                "kimi-k3",
            ],
            "kimi-k3",
        ),
        (["answer"], None),
    ],
)
def test_setup_sizes_the_tools_for_the_reader_and_records_it(monkeypatch, argv, expected):
    recorded: list[dict] = []

    def reader(config_dir, model, **_kwargs):
        return SimpleNamespace(model=model, gateway_url=READER_GW, config_dir=config_dir)

    monkeypatch.setattr(run.scifact, "load", lambda *_a, **_k: scifact.Corpus())
    stack = _stack(None)
    monkeypatch.setattr(run.backends, "Stack", lambda **_k: stack)
    monkeypatch.setattr(run.backends, "provenance", lambda **k: recorded.append(k) or {})
    monkeypatch.setattr(run.backends, "ReaderClient", reader)
    monkeypatch.setattr(run.backends, "tool_sizing", lambda _client, model: {"for": model})
    _corpus, got, _prov = run._setup(run._parser().parse_args([argv[0], "--data", "d", *argv[1:]]))
    assert got.session_model == expected
    assert recorded[0]["tool_reader"] == expected


def test_the_result_provenance_records_the_tool_reader(monkeypatch):
    prov, _calls = _fake_stack_provenance(monkeypatch)
    assert prov["tool_reader"] is None
    sized = backends.provenance(model="llama3.2:3b", reads_verdicts=False, tool_reader="kimi-k3")
    assert (sized["reader"], sized["tool_reader"]) == (None, "kimi-k3")


# c23-mf-2: the model name does not fix the tool arms' sizing (R4_MODEL_SCALE,
# an `r4 model scale set` pin), so the provenance records what it resolved to.


@pytest.mark.parametrize(
    ("arm_list", "sized"),
    [
        ("tool:knowledge_query", True),
        ("retrieve:dense,tool:rag_query", True),
        ("retrieve:dense,oracle", False),  # no tool arm: no scale probe either
    ],
)
def test_setup_records_how_the_tool_arms_were_sized(monkeypatch, arm_list, sized):
    recorded: list[dict] = []
    asked: list[tuple] = []

    def tool_sizing(client, model):
        asked.append((client, model))
        return {"profile": "large", "knowledge_query_limit": 10}

    monkeypatch.setattr(run.scifact, "load", lambda *_a, **_k: scifact.Corpus())
    stack = _stack(None)
    monkeypatch.setattr(run.backends, "Stack", lambda **_k: stack)
    monkeypatch.setattr(run.backends, "provenance", lambda **k: recorded.append(k) or {})
    monkeypatch.setattr(run.backends, "tool_sizing", tool_sizing, raising=False)
    run._setup(run._parser().parse_args(["retrieve", "--data", "d", "--arms", arm_list]))
    expected = {"profile": "large", "knowledge_query_limit": 10} if sized else None
    assert recorded[0].get("tool_sizing", "unrecorded") == expected
    assert asked == ([(stack.client, "llama3.2:3b")] if sized else [])


@pytest.fixture
def scale_home(tmp_path, monkeypatch):
    """r4's model-scale detection with no config, no pin, no env override and
    no network (every probe answers 404): only the name rule and the env."""
    import httpx

    from r4 import config as r4_config
    from r4 import model_scale

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("R4_MODEL_SCALE", "R4_RAG_PROFILE", "R4_SOURCE", "R4_GATEWAY_URL", "R4_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(r4_config, "_config", None)
    monkeypatch.setattr(
        model_scale, "_TRANSPORT", httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    model_scale.clear_scale_memo()
    yield
    model_scale.clear_scale_memo()


def test_the_tool_sizing_is_what_the_model_resolved_to_not_its_name(scale_home, monkeypatch):
    client = _stack(None).client
    by_name = backends.tool_sizing(client, "llama3.2:3b")
    monkeypatch.setenv("R4_MODEL_SCALE", "large")
    forced = backends.tool_sizing(client, "llama3.2:3b")
    assert (by_name["profile"], by_name["knowledge_query_limit"]) == ("small", 4)
    assert forced == {
        "scale": "large",
        "scale_basis": "env",
        "provisional": False,
        "profile": "large",
        "knowledge_query_limit": _LARGE.k_max,
        # C23b: the large pack over its k_max hits (since ADR-093 MD-14,
        # 2026-10-13: 2,000 over 6, not 8,000 over 10)
        "knowledge_query_tokens": _LARGE_SHARE,
    }
    assert backends.tool_sizing(client, None) is None


def test_the_provenance_records_the_scale_override_and_the_tool_sizing(monkeypatch):
    monkeypatch.setenv("R4_MODEL_SCALE", "large")
    monkeypatch.setenv("R4_RAG_PROFILE", "small")
    prov, _calls = _fake_stack_provenance(monkeypatch)
    assert prov["r4_rag_env"]["R4_MODEL_SCALE"] == "large"
    assert prov["r4_rag_env"]["R4_RAG_PROFILE"] == "small"
    assert prov.get("tool_sizing", "unrecorded") is None
    sizing = {"profile": "large", "knowledge_query_limit": 10}
    assert backends.provenance(model="llama3.2:3b", tool_sizing=sizing)["tool_sizing"] == sizing


# c23-retrieve-model-factor: a retrieve comparison does not hold --model fixed,
# so a retrieve file names the model that sized (or searched for) its arm.


@pytest.mark.parametrize(
    ("argv", "name", "suffix"),
    [
        (["retrieve", "--model", "kimi-k3"], "tool:knowledge_query", "-m-kimi-k3"),
        (["retrieve", "--model", "qwen2.5:14b"], "tool:rag_query", "-m-qwen2.5-14b"),
        (["retrieve", "--model", "qwen2.5:14b"], "agent", "-m-qwen2.5-14b"),
        (["retrieve"], "tool:knowledge_query", ""),  # the default reader keeps its name
        (["retrieve", "--model", "kimi-k3"], "retrieve:dense", ""),  # --model sizes nothing
        (["infer", "--model", "kimi-k3"], "tool:knowledge_query", ""),  # infer compares it
    ],
)
def test_a_retrieve_file_names_the_model_its_tool_arm_was_sized_for(argv, name, suffix):
    args = run._parser().parse_args([argv[0], "--data", "d", "--arms", name, *argv[1:]])
    assert run._variant(args, name) == suffix


# Wave D seam (2026-10-11): the arm, the real companion tool and the
# provenance agree on the K3 limit, and a hit whose empty keys the tool left
# out still maps to its document.


class _SeamKnowledge:
    """K3 for the seam: one graph ``g`` in project ``default``; two hits, the
    second with no title (the tool leaves the key out)."""

    def __init__(self) -> None:
        self.limits: list[int] = []

    def graphs(self, **_kwargs):
        return [{"graph_id": "g", "name": "g", "project_id": "default", "status": "active"}]

    def query(self, _query, **kwargs):
        self.limits.append(kwargs.get("limit"))
        rows = [
            {"title": "scifact-11.md", "score": 0.9, "source_id": "n1", "content": "a"},
            {"title": None, "score": 0.8, "source_id": "art-0123456789ab", "content": "b"},
        ]
        return {"query": {"results": rows, "total_results": len(rows)}}


@pytest.mark.parametrize(("scale", "limit"), [(None, 4), ("large", _LARGE.k_max)])
def test_the_tool_arm_asks_k3_for_the_limit_its_provenance_records(
    scale_home, monkeypatch, scale, limit
):
    if scale:
        monkeypatch.setenv("R4_MODEL_SCALE", scale)
    stack = _stack(None)
    stack.client.knowledge = _SeamKnowledge()
    stack.client.config.project_id = "default"
    stack.art_to_doc = {"art-0123456789ab": "12"}
    stack.session_model = "llama3.2:3b"
    got = arms.ARMS["tool:knowledge_query"](stack, _CLAIM)
    assert stack.client.knowledge.limits == [limit]
    assert [hit.doc_id for hit in got.hits] == ["11", "12"]
    sizing = backends.tool_sizing(stack.client, stack.session_model)
    assert sizing["knowledge_query_limit"] == limit


# The comparisons: gates on every claim, what each pairing left out, the
# factors the files did not hold fixed, and B0 files (schema 1).


def _write_record(tmp_path: Path, name: str, record: dict) -> str:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(record))
    return str(path)


def test_a_sufficiency_gate_counts_errored_claims_against_itself(tmp_path, capsys):
    """eval-c21-8: 27 of 30 nonsense probes errored and the gate passed on the 3 left."""
    rows = [{"claim": 900001 + i, "label": _NEI, "error": "TransientError('x')"} for i in range(27)]
    rows += [
        {"claim": 900028 + i, "label": _NEI, "sufficiency": "insufficient", "features": {}}
        for i in range(3)
    ]
    record = {"arm": "retrieve:dense", "claims_set": "nonsense", "rows": rows}
    assert run.main(["sufficiency", _write_record(tmp_path, "n", record)]) == 0
    captured = capsys.readouterr()
    gate = json.loads(captured.out)["insufficient_nonsense"]
    assert (gate["rate"], gate["errors"], gate["rate_itt"], gate["pass"]) == (1.0, 27, 0.1, False)
    assert "errored claims counted against the gates" in captured.err


def test_errored_evidence_claims_count_as_false_insufficient():
    report = metrics.sufficiency_report({"evidence": ["sufficient"] * 18}, {"evidence": 2})
    gate = report["false_insufficient_evidence"]
    assert (gate["rate"], gate["errors"], gate["rate_itt"], gate["pass"]) == (0.0, 2, 0.1, False)


def test_a_summary_judges_its_sufficiency_gates_on_every_claim():
    rows = [
        _judged_row(900001, _NEI, "insufficient", _NEI, gated=True),
        {"claim": 900002, "label": _NEI, "error": "TransientError('x')"},
    ]
    gate = run.summarize(rows, {})["sufficiency"]["insufficient_nonsense"]
    assert (gate["rate"], gate["rate_itt"], gate["pass"]) == (1.0, 0.5, False)


def _bracket_files(tmp_path: Path, arm_rows: list[dict], **arm_fields) -> list[str]:
    closed = [_vrow(c, "SUPPORT", _NEI) for c in (1, 2)]
    oracle = [_vrow(c, "SUPPORT", "SUPPORT") for c in (1, 2)]
    records = {
        "arm": {"arm": "retrieve:large", "rows": arm_rows, **arm_fields},
        "closed": {"arm": "closed", "kind": "infer", "model": "llama3.2:3b", "rows": closed},
        "oracle": {"arm": "oracle", "kind": "infer", "model": "llama3.2:3b", "rows": oracle},
    }
    return [_write_record(tmp_path, name, record) for name, record in records.items()]


def test_headroom_reports_what_each_bracket_left_out(tmp_path, capsys):
    """eval-c21-9: an errored arm claim vanished; headroom read 1.0 on the rest."""
    arm = [_vrow(1, "SUPPORT", "SUPPORT"), {"claim": 2, "label": "SUPPORT", "error": "x"}]
    assert run.main(["headroom", *_bracket_files(tmp_path, arm)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["accuracy"]["headroom_used"] == 1.0
    assert report["completeness"]["files"]["arm"] == {"errors": 1, "missing": 0}
    assert (report["completeness"]["complete"], report["accuracy_itt"]["headroom_used"]) == (
        False,
        0.5,
    )
    assert "incomplete brackets" in captured.err


def test_headroom_names_brackets_read_by_another_reader(tmp_path, capsys):
    arm = [_vrow(c, "SUPPORT", "SUPPORT") for c in (1, 2)]
    fields = {
        "kind": "infer",
        "schema_version": 2,
        "model": "llama3.2:3b",
        "reader_model": "kimi-k3",
    }
    assert run.main(["headroom", *_bracket_files(tmp_path, arm, **fields)]) == 0
    captured = capsys.readouterr()
    differ = json.loads(captured.out)["factors"]["differ"]
    assert differ["reader_model"] == {"arm": "kimi-k3", "closed": None, "oracle": None}
    assert "factors differ" in captured.err and "reader_model" in captured.err


def _b0_record(rows: list[dict], kind: str = "infer", **fields) -> dict:
    """A record as B0's harness wrote it: no schema_version, reader, budget,
    threshold or corpus fingerprint."""
    base = {"arm": "legacy", "kind": kind, "claims_set": "dev", "model": "llama3.2:3b"}
    return {**base, "provenance": {"parent_commit": "2dba21bb"}, "rows": rows, **fields}


def _v2_record(rows: list[dict], kind: str = "infer", **fields) -> dict:
    provenance = {
        "models": {"embedder": {"query_prefix": "fixed"}},
        "corpus": {"graphs": [{"chunks_md5": "md5x"}]},
    }
    base = {"schema_version": 2, "arm": "retrieve:dense", "kind": kind, "claims_set": "dev"}
    infer_fields = {"model": "llama3.2:3b", "reader_model": None, "order": None}
    return {**base, **infer_fields, "provenance": provenance, "rows": rows, **fields}


def test_compare_reports_the_factors_the_files_did_not_hold_fixed(tmp_path, capsys):
    """eval-c21-10: a B0 file against a kimi-k3 file with the fixed query
    encoding paired with no warning."""
    rows = [_vrow(1, "SUPPORT", "SUPPORT")]
    paths = [
        _write_record(tmp_path, "a", _b0_record(rows)),
        _write_record(tmp_path, "b", _v2_record(rows, reader_model="kimi-k3")),
    ]
    assert run.main(["compare", *paths]) == 0
    captured = capsys.readouterr()
    factors = json.loads(captured.out)["factors"]
    assert factors["differ"]["reader_model"] == {"a": None, "b": "kimi-k3"}
    assert factors["differ"]["query_prefix"] == {"a": "b0", "b": "fixed"}
    assert factors["inferred"]["verdict_max_tokens"] == {"a": 200}
    assert "factors differ" in captured.err and "query_prefix" in captured.err


@pytest.mark.parametrize(("prefix", "differs"), [("b0", False), ("fixed", True)])
def test_a_b0_file_ran_the_double_prefix_encoding(prefix, differs):
    """Receipt 4 holds only under UMRS_RAG_QUERY_PREFIX=b0: B0's server sent
    ``passage: query: <q>`` and its files could not record it, so a parity
    run with the fixed encoding is named as differing, never 'unrecorded'
    (integrator, 2026-10-10)."""
    new = _v2_record([], kind="retrieve")
    new["provenance"]["models"]["embedder"]["query_prefix"] = prefix
    report = comparisons.factor_report({"b0": _b0_record([], kind="retrieve"), "new": new})
    assert report["inferred"]["query_prefix"] == {"b0": "b0"}
    assert ("query_prefix" in report.get("differ", {})) is differs
    assert "query_prefix" not in report.get("unrecorded", {})


def test_a_b0_answer_file_reads_as_the_07_bracket():
    """B0's ``answer`` sent 0.7 and recorded no threshold; a new file without
    one sent none (the product path)."""
    report = comparisons.factor_report(
        {"a": _b0_record([], kind="answer"), "b": _v2_record([], kind="answer", threshold=None)}
    )
    assert report["differ"]["threshold"] == {"a": 0.7, "b": None}
    assert report["inferred"]["threshold"] == {"a": 0.7}


def _b0_oracle_row(reply: str, *, tokens: int = 20) -> dict:
    row = {**_vrow(3, "SUPPORT", "SUPPORT"), "shown": ["14717500"], "ranked": ["14717500"]}
    return {**row, "reply": reply, "usage": {"prompt_tokens": 100, "completion_tokens": tokens}}


def test_compare_names_the_definitions_a_b0_file_reads_differently(tmp_path, capsys):
    """eval-c21-11: summary and row keys kept their B0 names under new definitions."""
    rows = [_b0_oracle_row(_VERDICT_JSON)]
    paths = [
        _write_record(tmp_path, "a", _b0_record(rows)),
        _write_record(tmp_path, "b", _v2_record(rows)),
    ]
    assert run.main(["compare", *paths]) == 0
    captured = capsys.readouterr()
    definitions = json.loads(captured.out)["definitions"]
    assert set(definitions["changed"]) == {"ranked", "shown", "verdict"}
    assert definitions["schema_versions"] == {"a": 1, "b": 2} and "rescore" in captured.err


def test_rescore_rewrites_a_b0_row_under_the_current_rules():
    reply = json.dumps({"verdict": "SUPPORT", "evidence": ["14717500"]})
    record = _b0_record([{**_b0_oracle_row(reply), "cited": []}], arm="oracle")
    out = run.rescore_record(record, _name_stack(), {3: {"14717500"}}, source="b0.json")
    assert out["rows"][0]["cited"] == ["14717500"] and out["summary"]["citation_precision"] == 1.0
    assert (out["schema_version"], out["rescore"]["v1_fields"]) == (2, ["ranked", "shown"])
    remaining = comparisons.definitions({"a": out, "b": _v2_record([])}, ("ranked", "verdict"))
    assert set(remaining["definitions"]["changed"]) == {"ranked"}


def test_a_b0_reply_cut_at_200_tokens_is_rescored_as_an_error():
    record = _b0_record([_b0_oracle_row(_VERDICT_JSON, tokens=200)], arm="oracle")
    out = run.rescore_record(record, _name_stack(), {3: {"14717500"}}, source="b0.json")
    assert "ReplyNotScored" in out["rows"][0]["error"] and out["summary"]["errors"] == 1


def test_rescore_keeps_the_v1_citation_where_the_numbering_was_lost():
    """B0 dropped another artifact's passage from a project-wide arm's shown
    docs, so ``[2]`` there no longer names the second shown doc."""
    reply = json.dumps({"verdict": "SUPPORT", "evidence": ["[2]"]})
    full = {**_b0_oracle_row(reply), "claim": 1, "shown": ["11", "22", "33"], "cited": ["22"]}
    short = {**full, "claim": 2, "shown": ["11", "33"], "cited": ["33"]}
    out = run.rescore_record(
        _b0_record([full, short]), _name_stack(), {1: {"22"}, 2: {"33"}}, source="x"
    )
    assert [row["cited"] for row in out["rows"]] == [["22"], ["33"]]
    assert "numbering is lost" in out["rows"][1]["rescore"] and "rescore" not in out["rows"][0]


def test_the_rescore_command_writes_beside_the_b0_file(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    data = _tiny_scifact(tmp_path / "data")
    reply = json.dumps({"verdict": "NOT_ENOUGH_INFO", "evidence": []})
    row = {**_vrow(1, _NEI, _NEI), "shown": [], "reply": reply, "usage": {"completion_tokens": 9}}
    path = _write_record(tmp_path, "b0", _b0_record([row]))
    assert run.main(["rescore", "--data", str(data), "--no-db", path]) == 0
    rescored = json.loads((tmp_path / "b0-rescored.json").read_text())
    assert (rescored["schema_version"], rescored["rescore"]["rows"]) == (2, {"rescored": 1})
    assert json.loads(Path(path).read_text()).get("schema_version") is None  # B0 untouched
    capsys.readouterr()


@pytest.mark.parametrize(
    ("b0_shown", "new_shown", "outcome"),
    [
        (["11", "22", "33", "44"], ["11", "other:art-9", "22", "33", "44"], "undecidable"),
        (["11", "22"], ["11", "22"], "identical"),
        (["11", "22"], ["22", "other:art-9", "11"], "mismatch"),
    ],
)
def test_parity_against_a_b0_file_cannot_decide_what_b0_did_not_record(
    b0_shown, new_shown, outcome
):
    """eval-c21-12: B0 dropped another artifact's passage; the same server
    answer read as a receipt-4 failure."""
    a = {"claim": 1, "label": "SUPPORT", "shown": b0_shown}
    b = {"claim": 1, "label": "SUPPORT", "shown": new_shown, "shown_refs": ["n"] * len(new_shown)}
    assert comparisons.parity_outcome(a, b) == ("docs", outcome)


def test_parity_reports_undecidable_claims_apart(tmp_path, capsys):
    a = [{"claim": 1, "label": "SUPPORT", "shown": ["11", "22"]}]
    b = [{"claim": 1, "label": "SUPPORT", "shown": ["11", "other:art-9", "22"], "shown_refs": []}]
    report, _err = _compare(tmp_path, capsys, a, b, cmd="parity")
    assert (report["identical"], report["undecidable"], report["basis"]) == (0, 1, {"docs": 1})
    assert report["mismatches"][0]["outcome"] == "undecidable"


def _answer_row(claim: int, label: str, *, abstained: bool, cites: bool) -> dict:
    return {
        "claim": claim,
        "label": label,
        "abstained": abstained,
        "cited_evidence": cites,
        "gold_in_context": True,
    }


def test_compare_pairs_two_answer_files(tmp_path, capsys):
    """eval-c21-18: compare printed only claims and completeness for E-B files."""
    a = [_answer_row(i, "SUPPORT", abstained=True, cites=False) for i in range(10)]
    b = [_answer_row(i, "SUPPORT", abstained=False, cites=True) for i in range(10)]
    report, _err = _compare(tmp_path, capsys, a, b)
    rate = report["abstain_rate_evidence"]
    assert (rate["delta"], rate["n"], rate["ci_excludes_zero"]) == (-1.0, 10, True)
    assert report["cites_evidence_rate"]["delta"] == 1.0 and report["abstained_rule"] == "phrase"


def test_an_answer_compare_reads_both_files_with_one_abstention_rule():
    """A B0 row has only the phrase; the new row's phrase is what pairs with it."""
    b0 = _answer_row(1, "SUPPORT", abstained=True, cites=False)
    new = {**b0, "abstained": False, "abstained_server": False, "abstained_phrase": True}
    out = comparisons.paired_answers([(b0, new)])
    assert (out["abstained_rule"], out["abstain_rate_evidence"]["delta"]) == ("phrase", 0.0)
    both = comparisons.paired_answers([(new, {**new, "abstained": True, "abstained_server": True})])
    assert (both["abstained_rule"], both["abstain_rate_evidence"]["delta"]) == ("server", 1.0)


def test_unpaired_claims_with_nothing_to_compare_keep_a_pairing_complete(tmp_path, capsys):
    """eval-c21-21: a retrieve:* arm runs all 300 claims in retrieve mode; its
    NEI rows carry no retrieval block, so B0's 188 rows pair completely."""
    block = metrics.retrieval_scores(["11"], {"11"})
    scored = [{"claim": c, "label": "SUPPORT", "retrieval": block} for c in (1, 2)]
    nei = {"claim": 3, "label": _NEI, "sufficiency": "insufficient"}
    report, err = _compare(tmp_path, capsys, scored, [*scored, nei])
    assert report["complete"] is True and "incomplete" not in err
    assert report["unpaired_unscored"] == {"only_in_a": 0, "only_in_b": 1}
    assert report["unpaired"] == {"only_in_a": 0, "only_in_b": 0}


# Provenance: untracked files, the imported r4 packages, the containers' code,
# the lexical corpus.


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README").write_text("x\n")
    subprocess.run([*git, "add", "README"], check=True)
    subprocess.run([*git, "-c", "commit.gpgsign=false", "commit", "-qm", "init"], check=True)
    return repo


def test_the_code_fingerprint_sees_an_edit_to_an_untracked_file(tmp_path, monkeypatch):
    """eval-c21-3: status names an untracked file and ``git diff`` leaves it
    out, so editing r4_contracts/models.py (untracked today) changed nothing."""
    repo = _git_repo(tmp_path)
    models = repo / "r4-contracts" / "src" / "r4_contracts" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text("LARGE_K_MAX = 10\n")
    monkeypatch.setattr(backends, "ROOT", repo)
    before = backends._git_state()
    models.write_text("LARGE_K_MAX = 12\n")
    after = backends._git_state()
    assert before["status_sha256"] == after["status_sha256"]
    assert before["untracked_sha256"] != after["untracked_sha256"]
    assert after["untracked_files"] == 1


def test_provenance_hashes_the_r4_packages_this_process_imported(monkeypatch):
    """A frozen copy runs through PYTHONPATH: ``parent`` describes the live
    checkout, not the r4 that was imported (the pack renderer, the profiles)."""
    import r4_contracts

    prov, _calls = _fake_stack_provenance(monkeypatch)
    imported = prov["code"]["imported"]
    assert imported["r4_contracts"]["dir"] == str(Path(r4_contracts.__file__).resolve().parent)
    assert imported["r4_contracts"]["files"] >= 3 and len(imported["r4"]["sha256"]) == 64


def test_the_server_code_hash_covers_the_k3_route(tmp_path):
    """eval-c21-4: api/knowledge_query.py serves the k3-* arms."""
    route = tmp_path / "umrs" / "api" / "knowledge_query.py"
    route.parent.mkdir(parents=True)
    route.write_text("A = 1\n")
    before = server_probe.files_sha256(str(tmp_path / "umrs"))
    route.write_text("A = 2\n")
    assert server_probe.files_sha256(str(tmp_path / "umrs")) != before


def _fake_packages(base: Path) -> None:
    for rel in (
        "umrs/__init__.py",
        "umrs/api/rag_service.py",
        "umrs/knowledge/retrieval/dense.py",
        "r4_contracts/__init__.py",
        "r4_contracts/models.py",
    ):
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")


def _probe_in(base: Path, *argv: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(base), "UMRS_RAG_DIRECT_URL": "http://127.0.0.1:9"}
    source = Path(server_probe.__file__).read_text()
    command = [sys.executable, "-c", source, "http://127.0.0.1:9", *argv]
    out = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def test_the_probe_hashes_r4_contracts_inside_the_container(tmp_path):
    """Both containers mount r4_contracts: the profile table and the pack renderer."""
    _fake_packages(tmp_path)
    probe = _probe_in(tmp_path)
    contracts = server_probe.files_sha256(str(tmp_path / "r4_contracts"), server_probe.PACKAGE_CODE)
    assert probe["contracts"] == contracts and contracts["files"] == 2
    assert probe["role"] == "umrs" and probe["code"]["files"] == 1


def test_the_rag_container_is_probed_for_its_own_code(tmp_path):
    """The query encoder, UMRS_RAG_QUERY_PREFIX and the reranker run there."""
    _fake_packages(tmp_path)
    probe = _probe_in(tmp_path, "rag")
    rag_code = server_probe.files_sha256(str(tmp_path / "umrs"), server_probe.RAG_CODE)
    assert probe["code"] == rag_code and rag_code["files"] == 1
    assert probe["role"] == "rag" and "rag_stats" not in probe


def test_the_server_check_covers_r4_contracts_and_the_rag_container():
    code = {
        "umrs_retrieval": {"sha256": "u"},
        "umrs_rag": {"sha256": "r"},
        "contracts_checkout": {"sha256": "c"},
    }
    umrs = {
        "code": {"sha256": "u", "mtime_max": 50.0},
        "contracts": {"sha256": "c", "mtime_max": 150.0},  # edited after the start
        "started_at": 100.0,
    }
    rag = {
        "code": {"sha256": "r", "mtime_max": 50.0},
        "contracts": {"sha256": "old", "mtime_max": 60.0},
        "started_at": 100.0,
    }
    out = backends.server_summary(umrs, code, rag)
    assert (out["matches"], out["edited_after_start"]) == ({"code": True, "contracts": True}, True)
    assert (out["rag"]["matches_checkout"], out["matches_checkout"]) == (False, False)


def _lexical_sql(column_count: str):
    seen: list[tuple[str, dict]] = []

    def sql(query, **params):
        seen.append((query, params))
        if "information_schema" in query:
            return [["search_text", column_count]]
        return [["g-1", "1800", "md5lex"]]

    return sql, seen


def test_the_corpus_fingerprint_covers_what_the_lexical_lanes_search(monkeypatch):
    """eval-c21-17: a partial and a full search_text backfill had one fingerprint."""
    sql, seen = _lexical_sql("1")
    monkeypatch.setattr(backends, "sql", sql)
    assert backends.lexical_fingerprint("default", "scifact-eval") == [
        {"graph_id": "g-1", "indexed_chunks": "1800", "search_text_md5": "md5lex"}
    ]
    assert seen[1][1] == {"project": "default", "graph": "scifact-eval"}  # bound, not formatted
    corpus = backends.corpus_fingerprint("default", "scifact-eval")
    assert corpus["search_text"][0]["indexed_chunks"] == "1800"


def test_a_store_without_the_lexical_column_says_so(monkeypatch):
    sql, seen = _lexical_sql("0")
    monkeypatch.setattr(backends, "sql", sql)
    assert "migration 0120" in backends.lexical_fingerprint("default", "g")
    assert len(seen) == 1  # the column is never read when it is missing


def test_the_multi_evidence_probe_takes_claims_spanning_two_abstracts(tmp_path):
    _write(
        tmp_path / "claims_dev.jsonl",
        [
            {
                "id": 1,
                "claim": "a",
                "evidence": {"10": [{"label": "SUPPORT"}]},
                "cited_doc_ids": [10],
            },
            {
                "id": 2,
                "claim": "b",
                "evidence": {"10": [{"label": "SUPPORT"}], "11": [{"label": "SUPPORT"}]},
                "cited_doc_ids": [10, 11],
            },
        ],
    )
    _write(
        tmp_path / "claims_train.jsonl",
        [
            {
                "id": 7,
                "claim": "c",
                "evidence": {"12": [{"label": "CONTRADICT"}], "13": [{"label": "CONTRADICT"}]},
                "cited_doc_ids": [12, 13],
            }
        ],
    )
    multi = scifact.claims_for(tmp_path, "multi")
    assert [c.id for c in multi] == [2, 7]


def test_all_evidence_shown_needs_every_evidence_abstract():
    assert metrics.context_scores(["a", "b", "x"], {"a", "b"})["ctx_recall_all"] == 1.0
    assert metrics.context_scores(["a", "x"], {"a", "b"})["ctx_recall_all"] == 0.0
    assert metrics.context_scores(["a"], set())["ctx_recall_all"] == 0.0


# Wave D (C23, 2026-10-11): compare holds the tool sizing fixed. The one tool
# arm its reader sizes is tool:knowledge_query, and all it sends that the
# reader decides is K3's limit (provenance.tool_sizing.knowledge_query_limit;
# the profile only picks it). A file written before C23 records none, and its
# tool asked K3 for 10. tool:rag_query sends top_k 5 and no profile whatever
# its reader, so nothing sizes it.

#: Unsized: 10 hits of 200 characters, the 50 tokens they come to in English.
_UNSIZED = {"knowledge_query_limit": 10, "knowledge_query_tokens": 50}
_SIZED_4 = {  # a C23b file sized small: 4 hits of up to 300 tokens
    "scale": "small",
    "scale_basis": "name",
    "provisional": False,
    "profile": "small",
    "knowledge_query_limit": 4,
    "knowledge_query_tokens": 300,
}
#: The same sizing as a wave D file (C23, before C23b) recorded it: no tokens.
_WAVE_D_4 = {k: v for k, v in _SIZED_4.items() if k != "knowledge_query_tokens"}


def _pre_c23_tool(arm: str = "tool:knowledge_query", kind: str = "retrieve") -> dict:
    """A tool arm's file as the harness wrote it before C23: no tool_sizing."""
    return _v2_record([], kind=kind, arm=arm)


def _sized_tool(sizing: dict | None, arm: str = "tool:knowledge_query") -> dict:
    record = _pre_c23_tool(arm)
    record["provenance"]["tool_sizing"] = sizing
    return record


def _compare_records(tmp_path: Path, capsys, a: dict, b: dict) -> tuple[dict, str]:
    paths = [_write_record(tmp_path, name, rec) for name, rec in (("a", a), ("b", b))]
    assert run.main(["compare", *paths]) == 0
    captured = capsys.readouterr()
    return json.loads(captured.out)["factors"], captured.err


def test_compare_warns_when_a_pre_c23_tool_file_meets_one_sized_4(tmp_path, capsys):
    factors, err = _compare_records(tmp_path, capsys, _pre_c23_tool(), _sized_tool(_SIZED_4))
    sized = {"knowledge_query_limit": 4, "knowledge_query_tokens": 300}
    assert factors["differ"] == {"tool_sizing": {"a": _UNSIZED, "b": sized}}
    assert factors["inferred"] == {"tool_sizing": {"a": _UNSIZED}}
    assert f"factors differ: {json.dumps(factors['differ'])}" in err
    assert 'factors differ: {"tool_sizing": ' in err


def test_compare_is_silent_for_tool_files_sized_alike(tmp_path, capsys):
    """The same profile and K3 limit, whatever the scale's basis; two pre-C23
    files ran alike unsized, which compare names as inferred, never warns."""
    by_env = {**_SIZED_4, "scale_basis": "env", "provisional": True}
    factors, err = _compare_records(tmp_path, capsys, _sized_tool(_SIZED_4), _sized_tool(by_env))
    assert (factors, err) == ({}, "")
    factors, err = _compare_records(tmp_path, capsys, _pre_c23_tool(), _pre_c23_tool())
    assert factors == {"inferred": {"tool_sizing": {"a": _UNSIZED, "b": _UNSIZED}}}
    assert err == ""


def test_the_tool_sizing_is_read_only_when_every_file_ran_a_tool_arm():
    """A tool arm against a retrieve:* arm, the agent or the closed/oracle
    brackets reads no tool sizing on the other side: nothing to hold fixed."""
    tools = comparisons.factor_report({"a": _pre_c23_tool(), "b": _sized_tool(_SIZED_4)})
    assert set(tools["differ"]) == {"tool_sizing"}
    for other in ("retrieve:dense", "retrieve:large", "agent", "tool:rag_query"):
        report = comparisons.factor_report(
            {"tool": _sized_tool(_SIZED_4), "other": _sized_tool(None, arm=other)}
        )
        assert "tool_sizing" not in json.dumps(report), other
    brackets = {
        "arm": _pre_c23_tool(kind="infer"),
        "closed": _v2_record([], arm="closed"),
        "oracle": _v2_record([], arm="oracle"),
    }
    assert "tool_sizing" not in json.dumps(comparisons.factor_report(brackets))


@pytest.mark.parametrize("sizing", [{"error": "HTTPStatusError"}, None])
def test_a_tool_sizing_the_run_could_not_resolve_is_unrecorded(tmp_path, capsys, sizing):
    """A detection that failed (``backends.tool_sizing`` records the error) or
    a wave D file with no sizing says nothing: unrecorded, never unsized."""
    factors, err = _compare_records(tmp_path, capsys, _sized_tool(sizing), _sized_tool(_SIZED_4))
    assert factors == {"unrecorded": {"tool_sizing": ["a"]}}
    assert 'factors not recorded: {"tool_sizing": ["a"]}' in err


def test_compare_holds_only_the_k3_limit_the_tool_sends(tmp_path, capsys):
    """knowledge_query sends K3 its limit, never the profile: a pre-C23 file
    (unsized, 10) and a wave D file sized large (10; its hits, before C23b,
    still cut to 200 characters, inferred as 50 tokens) asked K3 alike, so the pair compares
    silently; the profile is no factor (c23-compare-profile)."""
    large = {**_WAVE_D_4, "scale": "large", "profile": "large", "knowledge_query_limit": 10}
    factors, err = _compare_records(tmp_path, capsys, _pre_c23_tool(), _sized_tool(large))
    both = {"a": _UNSIZED, "b": _UNSIZED}  # the wave D file's tokens are inferred
    assert (factors, err) == ({"inferred": {"tool_sizing": both}}, "")
    unresolved = {**large, "profile": None}  # no profile resolved: 10 as well
    factors, err = _compare_records(tmp_path, capsys, _sized_tool(unresolved), _sized_tool(large))
    assert (factors, err) == ({"inferred": {"tool_sizing": both}}, "")


@pytest.mark.parametrize("arm_b", ["tool:rag_query", "tool:knowledge_query"])
def test_rag_query_files_hold_no_tool_sizing(tmp_path, capsys, arm_b):
    """rag_query sends top_k 5 and no profile whatever its reader: two of its
    runs, or one against knowledge_query, made no call the sizing moved
    (c23-compare-rag-query)."""
    a = _pre_c23_tool(arm="tool:rag_query")
    b = _sized_tool(_SIZED_4, arm=arm_b)
    factors, err = _compare_records(tmp_path, capsys, a, b)
    assert (factors, err) == ({}, "")


# C23b (2026-10-11): knowledge_query shows each hit within its reader's share
# of the profile's pack, counted in the pack estimator's tokens (small 300,
# large 800; large 333 since ADR-093 MD-14, 2026-10-13), and the provenance
# records it beside the K3 limit. An unsized
# query keeps 200 characters, recorded as the 50 tokens they come to in ASCII
# text (SciFact is English: exact there). compare holds both fixed; a wave D
# file (C23, before C23b) recorded the limit only, and its hits were cut to
# 200 characters, which compare reads as inferred at 50 tokens.


def test_the_tool_sizing_records_the_tokens_each_hit_shows(scale_home, monkeypatch):
    from r4.companion.knowledge_graph_tools import query_sizing

    client = _stack(None).client
    small = backends.tool_sizing(client, "llama3.2:3b")
    monkeypatch.setenv("R4_MODEL_SCALE", "large")
    large = backends.tool_sizing(client, "llama3.2:3b")
    assert (small["knowledge_query_limit"], small["knowledge_query_tokens"]) == (4, 300)
    # the large profile's k_max and pack share (since ADR-093 MD-14,
    # 2026-10-13: 6 hits of 333 tokens, not 10 of 800)
    assert (large["knowledge_query_limit"], large["knowledge_query_tokens"]) == (
        _LARGE.k_max,
        _LARGE_SHARE,
    )
    assert tuple(query_sizing(client, "llama3.2:3b")) == (_LARGE.k_max, _LARGE_SHARE, None)
    assert "knowledge_query_chars" not in large


def test_an_unsized_run_records_what_compare_infers_for_an_unsized_file(scale_home, monkeypatch):
    """No profile resolved: 10 hits of 200 characters, recorded as the 50
    tokens a file written before C23b is inferred at, so the two compare
    alike (comparisons.UNSIZED_TOOLS, SIZING_ADDED)."""
    import r4.companion.retrieval_router as router
    from r4.companion.knowledge_graph_tools import UNSIZED_TOKENS

    monkeypatch.setattr(router, "reader_fields", lambda *_a, **_k: {})
    sizing = backends.tool_sizing(_stack(None).client, "llama3.2:3b")
    sent = comparisons.SIZED_TOOL_ARMS["tool:knowledge_query"]
    assert {key: sizing[key] for key in sent} == _UNSIZED
    assert {key: comparisons.UNSIZED_TOOLS[key] for key in sent} == _UNSIZED
    assert comparisons.SIZING_ADDED == {"knowledge_query_tokens": UNSIZED_TOKENS}


class _LongSeamKnowledge(_SeamKnowledge):
    """The seam's two hits with passages longer than any reader's budget."""

    def __init__(self, text: str = "vitamin D and hip fracture " * 200) -> None:  # 5,400 chars
        super().__init__()
        self.text = text

    def query(self, _query, **kwargs):
        payload = super().query(_query, **kwargs)
        for row in payload["query"]["results"]:
            row["content"] = self.text
        return payload


def _long_seam_stack(text: str | None = None) -> backends.Stack:
    stack = _stack(None)
    stack.client.knowledge = _LongSeamKnowledge(*([text] if text else []))
    stack.client.config.project_id = "default"
    stack.art_to_doc = {"art-0123456789ab": "12"}
    stack.session_model = "llama3.2:3b"
    return stack


@pytest.mark.parametrize(("scale", "tokens"), [(None, 300), ("large", _LARGE_SHARE)])
def test_the_tool_arm_shows_each_hit_within_the_tokens_its_provenance_records(
    scale_home, monkeypatch, scale, tokens
):
    if scale:
        monkeypatch.setenv("R4_MODEL_SCALE", scale)
    stack = _long_seam_stack()
    got = arms.ARMS["tool:knowledge_query"](stack, _CLAIM)
    # English: 4 characters a token, ended at a word in the last fifth
    shown = [
        (hit.doc_id, tokens * 4 // 5 <= estimate_tokens(hit.text) <= tokens, len(hit.text))
        for hit in got.hits
    ]
    assert [(doc, fits, size <= tokens * 4) for doc, fits, size in shown] == [
        ("11", True, True),
        ("12", True, True),
    ]
    assert '"content_clipped": true' in got.context
    sizing = backends.tool_sizing(stack.client, stack.session_model)
    assert sizing["knowledge_query_tokens"] == tokens


def test_the_tool_arm_reads_a_japanese_hit_shown_as_text(scale_home):
    """The reader's context carries the passage itself, not six-character
    escapes; the harness still maps both hits to their documents, each cut
    to the small reader's 300 tokens (300 characters, not 1,200)."""
    stack = _long_seam_stack("大腿骨頸部骨折とビタミンDの摂取量の関連を検討した。" * 100)
    got = arms.ARMS["tool:knowledge_query"](stack, _CLAIM)
    assert [hit.doc_id for hit in got.hits] == ["11", "12"]
    assert [estimate_tokens(hit.text) for hit in got.hits] == [300, 300]
    assert got.hits[0].text in got.context
    assert "\\u" not in got.context
    assert len(got.context) < 2 * sum(len(hit.text) for hit in got.hits) + 1_000


def test_compare_warns_when_a_wave_d_tool_file_meets_one_sized_by_c23b(tmp_path, capsys):
    """The same K3 limit, but the wave D file's hits were cut to 200
    characters (inferred, 50 tokens) and the C23b file's to the small
    reader's 300 tokens."""
    factors, err = _compare_records(tmp_path, capsys, _sized_tool(_WAVE_D_4), _sized_tool(_SIZED_4))
    wave_d = {"knowledge_query_limit": 4, "knowledge_query_tokens": 50}
    sized = {"knowledge_query_limit": 4, "knowledge_query_tokens": 300}
    assert factors["differ"] == {"tool_sizing": {"a": wave_d, "b": sized}}
    assert factors["inferred"] == {"tool_sizing": {"a": wave_d}}
    assert 'factors differ: {"tool_sizing": ' in err


def test_compare_names_two_wave_d_tool_files_sized_alike_as_inferred(tmp_path, capsys):
    factors, err = _compare_records(
        tmp_path, capsys, _sized_tool(_WAVE_D_4), _sized_tool(_WAVE_D_4)
    )
    wave_d = {"knowledge_query_limit": 4, "knowledge_query_tokens": 50}
    assert (factors, err) == ({"inferred": {"tool_sizing": {"a": wave_d, "b": wave_d}}}, "")


def test_compare_holds_the_tokens_fixed_where_the_limits_agree(tmp_path, capsys):
    """A pre-C23 file and a C23b file sized large both asked K3 for 10, but
    the large reader saw up to 800 tokens of a hit, not 50; so did a large
    reader against an unresolved one (10 hits of 200 characters, 50 tokens)."""
    large = {**_SIZED_4, "profile": "large", "knowledge_query_limit": 10}
    large["knowledge_query_tokens"] = 800
    factors, _err = _compare_records(tmp_path, capsys, _pre_c23_tool(), _sized_tool(large))
    shown = {"knowledge_query_limit": 10, "knowledge_query_tokens": 800}
    assert factors["differ"] == {"tool_sizing": {"a": _UNSIZED, "b": shown}}
    unresolved = {**large, "profile": None, "knowledge_query_tokens": 50}
    factors, err = _compare_records(tmp_path, capsys, _sized_tool(unresolved), _sized_tool(large))
    assert factors == {"differ": {"tool_sizing": {"a": _UNSIZED, "b": shown}}}
    assert 'factors differ: {"tool_sizing": ' in err


def test_a_tool_sizing_with_tokens_but_no_limit_is_unrecorded(tmp_path, capsys):
    """Only a key added after C23 (the tokens) is inferred when absent: a
    sizing without the K3 limit says nothing."""
    limitless = {k: v for k, v in _SIZED_4.items() if k != "knowledge_query_limit"}
    factors, _err = _compare_records(
        tmp_path, capsys, _sized_tool(limitless), _sized_tool(_SIZED_4)
    )
    assert factors == {"unrecorded": {"tool_sizing": ["a"]}}


# C23b review (2026-10-11): the interim C23b build recorded each hit's share
# in characters (knowledge_query_chars, 1,200 for the small reader) and no
# tokens. Those characters came to 300 tokens of English and 1,200 of
# Japanese, so such a file's tokens are unrecorded and compare and parity
# warn; they are never the 50 a file written before C23b is inferred at.

_INTERIM_4 = {**_WAVE_D_4, "knowledge_query_chars": 1_200}


@pytest.mark.parametrize("other", [_SIZED_4, _WAVE_D_4], ids=["c23b", "wave-d"])
def test_compare_reads_an_interim_character_share_as_unrecorded(tmp_path, capsys, other):
    factors, err = _compare_records(tmp_path, capsys, _sized_tool(_INTERIM_4), _sized_tool(other))
    assert factors["unrecorded"] == {"tool_sizing": ["a"]}
    assert "differ" not in factors
    assert 'factors not recorded: {"tool_sizing": ["a"]}' in err


def test_parity_warns_about_an_interim_character_share(tmp_path, capsys):
    records = {"a": _sized_tool(_INTERIM_4), "b": _sized_tool(_WAVE_D_4)}
    for record in records.values():
        record["rows"] = [{"claim": 1, "label": "SUPPORT", "shown": ["11", "22"]}]
    paths = [_write_record(tmp_path, name, record) for name, record in records.items()]
    assert run.main(["parity", *paths]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["factors"]["unrecorded"] == {"tool_sizing": ["a"]}
    assert 'factors not recorded: {"tool_sizing": ["a"]}' in captured.err


def test_a_sizing_that_records_its_tokens_reads_them_beside_an_interim_key():
    both = {**_SIZED_4, "knowledge_query_chars": 1_200}
    sent = {"knowledge_query_limit": 4, "knowledge_query_tokens": 300}
    assert comparisons.FACTORS["tool_sizing"](_sized_tool(both)) == (sent, comparisons.RECORDED)
    assert comparisons.SIZING_INTERIM == {"knowledge_query_chars": "knowledge_query_tokens"}


# ── committable aggregate records (ADR-093 MD-10, 2026-10-11) ───────────────


_RECORD_TEXT = "Vitamin D supplementation lowers hip fracture risk in older adults."


def _record_fixture() -> dict:
    return {
        "schema_version": 2,
        "arm": "retrieve:dense",
        "kind": "infer",
        "claims_set": "dev",
        "label": "S7",
        "model": "llama3.2:3b",
        "prompt": "pack-soft",
        "preset": {"k_max": 4, "note": _RECORD_TEXT},
        "provenance": {
            "parent_commit": "abc123",
            "parent_dirty": False,
            "code": {
                "parent": {"commit": "abc123", "dirty": True, "diff_sha256": "d" * 64},
                "harness": {"dir": "/Users/someone/repo/benchmarks", "sha256": "e" * 64},
            },
            "data": "/Users/someone/data/scifact",
        },
        "summary": {
            "accuracy": {"mean": 0.6, "ci95": [0.55, 0.65], "n": 300},
            "examples": [_RECORD_TEXT],
            _RECORD_TEXT: 1.0,
            "lanes": {"dense": {"ok": 300}},
        },
        "rows": [{"claim": 1, "text": _RECORD_TEXT, "reply": "SUPPORT"}],
    }


def test_a_record_keeps_numbers_and_identifiers_and_drops_text():
    out = records.aggregate(_record_fixture(), source="/x/results/run.json")
    dumped = json.dumps(out)
    assert _RECORD_TEXT not in dumped and "/Users" not in dumped and "rows" not in out
    assert out["source"] == "run.json"
    assert out["summary"] == {
        "accuracy": {"mean": 0.6, "ci95": [0.55, 0.65], "n": 300},
        "examples": [],
        "lanes": {"dense": {"ok": 300}},
    }


def test_a_record_keeps_the_preset_numbers_digests_and_identifiers():
    out = records.aggregate(_record_fixture(), source="/x/results/run.json")
    assert out["preset"] == {"k_max": 4}
    assert out["provenance"]["code"]["parent"] == {
        "commit": "abc123",
        "dirty": True,
        "diff_sha256": "d" * 64,
    }
    assert out["provenance"]["code"]["harness"] == {"sha256": "e" * 64}
    assert (out["arm"], out["label"], out["prompt"]) == ("retrieve:dense", "S7", "pack-soft")


def test_a_long_identifier_is_dropped_not_kept():
    out = records.aggregate({**_record_fixture(), "label": _RECORD_TEXT * 2})
    assert "label" not in out


def test_build_writes_one_record_over_files(tmp_path):
    for name in ("b.json", "a.json"):
        (tmp_path / name).write_text(json.dumps(_record_fixture()))
    out = tmp_path / "records" / "r.json"
    assert (
        records.main([str(tmp_path / "b.json"), str(tmp_path / "a.json"), "--out", str(out)]) == 0
    )
    record = json.loads(out.read_text())
    assert record["record_version"] == records.RECORD_VERSION
    assert [r["source"] for r in record["runs"]] == ["a.json", "b.json"]
