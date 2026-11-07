"""Phase 8b probe (benchmarks/retrieval/framing_probe.py), offline legs.

The probe sends each claim through the router's own query (focus off: the
whole message as before wave H; focus on: ``retrieval_router.focus_query``)
and scores gold-in-pack, sufficiency and the nonsense probes per template.
These tests drive it with a fake retrieve; the server legs are the
maintainer's (they need the stand-alone stack).
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from benchmarks.retrieval import framing_probe as fp
from r4.companion.query_focus import FOCUS_ENV

CLAIM = "IL-6 signalling drives insulin resistance in adipose tissue of obese mice."
TEMPLATES = [
    {"id": "label", "kind": "instruction_before", "text": "Check my papers.\nClaim: {claim}"},
    {
        "id": "ja",
        "kind": "japanese",
        "text": "以下の主張を文献で確認してください。\n\n主張: {claim}",
    },
]


@pytest.fixture(autouse=True)
def _focus_on(monkeypatch):
    monkeypatch.setenv(FOCUS_ENV, "on")


def _data(tmp_path):
    rows = [
        {"id": 1, "claim": CLAIM, "evidence": {"11": [{"sentences": [0], "label": "SUPPORT"}]}},
        {"id": 2, "claim": "Aspirin lowers adenoma recurrence in older adults.", "evidence": {}},
    ]
    for row in rows:
        row["cited_doc_ids"] = [11]
    (tmp_path / "claims_dev.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    templates = tmp_path / "templates.json"
    templates.write_text(json.dumps({"templates": TEMPLATES}, ensure_ascii=False))
    return tmp_path, templates


def _args(tmp_path, **extra):
    data, templates = _data(tmp_path)
    argv = ["--data", str(data), "--templates", str(templates), "--no-eval-template"]
    argv += ["--out-dir", str(tmp_path / "results"), *extra.pop("argv", [])]
    return fp.parse_args(argv)


def test_the_framed_arm_is_the_router_query_with_focus_off():
    message = fp.render(TEMPLATES[0], CLAIM)
    assert fp.router_query(message, focus=False) == (" ".join(message.split()), "whole")
    assert fp.router_query(message, focus=True) == (CLAIM, "field")
    assert os.environ[FOCUS_ENV] == "on"  # the switch is restored


def test_the_eval_template_is_the_agent_prompt():
    template = fp.eval_template("Graph '{graph}', reply {{\"v\": 1}}\n\nCLAIM: {claim}", "g")
    assert fp.render(template, CLAIM) == f"Graph 'g', reply {{\"v\": 1}}\n\nCLAIM: {CLAIM}"


def test_a_template_without_a_claim_slot_is_refused(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"templates": [{"id": "x", "text": "no slot"}]}))
    with pytest.raises(ValueError, match="no {claim}"):
        fp.load_templates(path)


def test_offline_scores_recall_leakage_and_identity(tmp_path):
    args = _args(tmp_path)
    items = fp.load_items(args.data)
    scores = fp.offline_scores(TEMPLATES, items)
    label, ja = scores["templates"]["label"], scores["templates"]["ja"]
    assert (label["recall"], label["leakage"], label["bases"]) == (1.0, 0.0, {"field": len(items)})
    assert (ja["language"], ja["recall"]) == ("ja", 1.0)
    assert scores["identity"]["moved"] == []
    assert fp.offline_gate(scores)["passed"]


class FakeServer:
    """The gold abstract for the bare claim; a framed query drowns it."""

    def __init__(self):
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        if "Check my papers" in query or "確認" in query:
            return {"status": "partial", "value": 2.0, "docs": ["99"]}
        if query.startswith("IL-6"):
            return {"status": "sufficient", "value": 9.0, "docs": ["11", "12"]}
        return {"status": "insufficient", "value": -1.0, "docs": ["50"]}


def test_the_live_arms_score_per_template_and_cache_repeats(tmp_path):
    args = _args(tmp_path, argv=["--limit", "1"])
    server = FakeServer()
    result = fp.run_live(args, retrieve=server)
    label = result["summary"]["label"]
    assert result["summary"]["bare"]["gold"] == 1
    assert (label["framed"]["gold"], label["focused"]["gold"]) == (0, 1)
    assert label["focused"]["nonsense_insufficient"] == 1
    assert label["bases"] == {"field": 2}
    # bare 2 + framed 2 per template; every focused query repeats a bare one
    assert result["calls"] == len(set(server.queries)) == 2 + 2 * len(TEMPLATES)


def test_the_gate_reads_the_focused_arm_against_the_spec_thresholds():
    def arm(gold, sufficient, nonsense):
        return {"gold": gold, "sufficient": sufficient, "n_dev": 188}, nonsense

    def summary(*arms):
        rows = {}
        for i, ((focused, nonsense), lang) in enumerate(arms):
            focused |= {"nonsense_insufficient": nonsense, "n_nonsense": 30}
            rows[f"t{i}"] = {"language": lang, "focused": focused, "framed": focused}
        return {"bare": {}, **rows}

    passing = summary((arm(160, 175, 28), "en"), (arm(152, 171, 27), "ja"))
    assert fp.gate(passing, True)["passed"]
    one_low = summary((arm(170, 180, 29), "en"), (arm(144, 175, 29), "ja"))
    assert fp.gate(one_low, True)["checks"]["gold_min"] is False
    assert fp.gate(passing, False)["checks"]["identity"] is False
    assert set(fp.by_language(passing)) == {"en", "ja"}


def test_the_result_is_written_under_results(tmp_path):
    args = _args(tmp_path, argv=["--offline", "--label", "heldout"])
    assert (
        fp.main(
            [
                "--data",
                str(args.data),
                "--templates",
                str(args.templates),
                "--no-eval-template",
                "--offline",
                "--label",
                "heldout",
                "--out-dir",
                str(args.out_dir),
            ]
        )
        == 0
    )
    (written,) = args.out_dir.glob("*-framing-offline-heldout.json")
    result = json.loads(written.read_text())
    assert result["provenance"]["focus_rules"] == "focus-v2"
    assert len(result["provenance"]["query_focus_sha256"]) == 64


def test_parse_args_keeps_the_small_profile_defaults(tmp_path):
    args = _args(tmp_path)
    assert isinstance(args, argparse.Namespace)
    assert (args.k_max, args.profile, args.reader_model) == (4, "small", "llama3.2:3b")
