"""ADR-093 S6 probe (benchmarks/retrieval/consumer_probe.py), without a stack.

The consumers are the real ones (the companion's ``search_agentic`` tool and
the local loop's ``LocalAgentIterationExecutor``); only the transport is a
stub: a client whose ``rag.retrieve`` returns contract ``RetrieveResult``s and
whose ``chat`` returns a fixed reply.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.retrieval import consumer_probe as cp
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

LABELS = ("SUPPORT", "CONTRADICT", "NOT_ENOUGH_INFO")


@pytest.fixture(autouse=True)
def _isolated_r4(tmp_path, monkeypatch):
    """No user r4 config, a pinned model scale (no probe), retries that do
    not sleep: a failure is recorded at once."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("R4_MODEL_SCALE", "small")
    monkeypatch.setattr(cp.infer, "with_retries", lambda call, **_kw: call())


def _passage(rank: int, doc: str) -> RetrievedPassage:
    return RetrievedPassage(
        rank=rank,
        source_id=f"node-{doc}",
        artifact_id=f"art-{doc}",
        source_name=f"scifact-{doc}.md",
        text=f"abstract {doc}",
    )


class FakeRag:
    """``rag.retrieve`` as the verb answers: the docs ``docs_for(query)``
    names, or ``insufficient`` with none; ``fail`` raises instead."""

    def __init__(self, docs=(), *, docs_for=None, status="sufficient", fail=None):
        self.docs_for = docs_for or (lambda _query: list(docs))
        self.status, self.fail = status, fail
        self.calls: list[dict] = []

    def retrieve(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if self.fail is not None:
            raise self.fail
        docs = self.docs_for(query)
        status = self.status if docs else "insufficient"
        return RetrieveResult(
            query=query,
            passages=tuple(_passage(n, d) for n, d in enumerate(docs, start=1)),
            sufficiency=RetrievalSufficiency(status=status),
            searched=100,
        )


class FakeClient:
    """The transport the consumers reach: config, ``rag`` and ``chat``."""

    def __init__(self, rag: FakeRag, answer: str = "Supported [1].") -> None:
        self._config = SimpleNamespace(project_id="default", model="llama3.2:3b")
        self.config = self._config
        self.rag = rag
        self.answer = answer

    def chat(self, **_kwargs):
        return SimpleNamespace(content=self.answer)


def _stack(rag: FakeRag, answer: str = "Supported [1].") -> SimpleNamespace:
    return SimpleNamespace(
        client=FakeClient(rag, answer),
        project="default",
        doc_of=lambda *refs: cp.backends.doc_of_refs({}, *refs),
    )


def _claim(cid: int, label: str, evidence=()) -> cp.scifact.Claim:
    return cp.scifact.Claim(
        id=cid,
        text=f"claim number {cid} holds.",
        label=label,
        evidence=frozenset(evidence),
        cited=frozenset(evidence) or frozenset({str(9000 + cid)}),
    )


def _claims(per_label: int = 13) -> list[cp.scifact.Claim]:
    out = []
    for k, label in enumerate(LABELS):
        for i in range(per_label):
            cid = 100 * (k + 1) + i
            evidence = () if label == "NOT_ENOUGH_INFO" else (str(cid),)
            out.append(_claim(cid, label, evidence))
    return out


def _tool(stack, scope="rag_only"):
    tool = cp.companion_tool(stack.client, "search_agentic", "llama3.2:3b")
    return lambda **kw: tool(**{**kw, "scope": scope})


def _agentic(stack, claim, scope="rag_only"):
    return cp.agentic_row(stack, claim, _tool(stack), scope=scope, depth="shallow")


# ── the selection ────────────────────────────────────────────────────────────


def test_the_agentic_draw_is_ten_per_label_seeded_and_stable():
    claims = _claims()
    first, again = cp.select_agentic(claims), cp.select_agentic(list(reversed(claims)))
    assert [c.id for c in first] == [c.id for c in again]
    assert {label: sum(c.label == label for c in first) for label in LABELS} == dict.fromkeys(
        LABELS, 10
    )
    assert cp.select_agentic(claims, seed=cp.SEED + 7) != first


def test_the_loop_goals_have_gold_docs_and_avoid_the_agentic_claims():
    claims = _claims()
    agentic = {c.id for c in cp.select_agentic(claims)}
    goals = cp.select_goals(claims, exclude=cp.select_agentic(claims))
    with_gold = sum(bool(g.evidence) for g in goals)
    assert (len(goals), with_gold, {g.id for g in goals} & agentic) == (5, 5, set())
    plan = cp.selection(claims)
    assert (plan["loop"], len(plan["agentic"]["NOT_ENOUGH_INFO"])) == ([g.id for g in goals], 10)
    goal = cp.goal_for(goals[0])
    assert goal == cp.GOAL_TEMPLATE.format(claim=f"claim number {goals[0].id} holds")


# ── search_agentic: grounded ─────────────────────────────────────────────────


def test_an_answer_citing_a_retrieved_passage_is_grounded():
    stack = _stack(FakeRag(["111", "222"]), answer="It holds [2].")
    row = _agentic(stack, _claim(1, "SUPPORT", ["222"]))
    assert row["grounded"] is True and row["phantoms"] == []
    assert row["cited"][0] | {"n": 2, "doc": "222", "retrieved_passage": True} == row["cited"][0]
    assert (row["cites_gold"], row["gold_retrieved"]) == (True, True)
    assert row["retrieved_docs"] == ["111", "222"]
    call = row["retrieve_calls"][0]
    assert (call["k_max"], call["profile"], call["reader_model"], call["n"]) == (
        5,
        "small",
        "llama3.2:3b",
        2,
    )


def test_a_grouped_citation_counts_each_number():
    stack = _stack(FakeRag(["111", "222"]), answer="Both say so [1, 2].")
    row = _agentic(stack, _claim(1, "SUPPORT", ["999"]))
    assert [c["n"] for c in row["cited"]] == [1, 2] and row["grounded"] is True
    assert (row["cited_docs"], row["cites_gold"], row["gold_retrieved"]) == (
        ["111", "222"],
        False,
        False,
    )


def test_a_phantom_number_is_not_grounded_even_beside_a_real_one():
    stack = _stack(FakeRag(["111"]), answer="Yes [1], and also [7].")
    row = _agentic(stack, _claim(1, "SUPPORT", ["111"]))
    assert row["phantoms"] == [7] and row["grounded"] is False
    assert row["cites_gold"] is True  # reported apart from the gate


def test_an_answer_that_cites_nothing_is_not_grounded():
    stack = _stack(FakeRag(["111"]), answer="The literature supports it.")
    row = _agentic(stack, _claim(1, "SUPPORT", ["111"]))
    assert row["cited"] == [] and row["grounded"] is False and row["gold_retrieved"] is True


def test_an_insufficient_retrieval_leaves_nothing_to_ground_on():
    """The verb says no passage answers; the search falls back to the model's
    own knowledge, which cites no retrieved passage."""
    stack = _stack(FakeRag([]), answer="From what I know [1].")
    row = _agentic(stack, _claim(1, "NOT_ENOUGH_INFO"))
    assert row["sources_returned"] == 0 and row["grounded"] is False
    assert row["phantoms"] == [1] and row["cites_gold"] is None
    assert row["retrieve_calls"][0]["status"] == "insufficient"


def test_a_cited_source_that_is_no_retrieved_passage_does_not_ground():
    rag = FakeRag(["111"])
    stack = _stack(rag)
    payload = {
        "answer": "See the catalog [1].",
        "sources": [{"type": "catalog_entry", "title": "plate-reader.csv", "metadata": {}}],
        "citations": [{"index": 1, "source_title": "plate-reader.csv"}],
    }
    with cp.RetrieveRecorder(stack.client, stack.doc_of) as seen:
        stack.client.rag.retrieve("q", project_id="default")
    got = cp.grounding(payload, seen, {"111"})
    assert got["phantoms"] == [] and got["grounded"] is False
    assert got["cited"][0]["retrieved_passage"] is False and got["gold_retrieved"] is True


def test_a_number_past_the_listed_sources_resolves_through_the_tool_citations():
    """The tool lists 10 sources; the synthesizer may have numbered more."""
    rag = FakeRag([str(n) for n in range(100, 112)])
    stack = _stack(rag)
    with cp.RetrieveRecorder(stack.client, stack.doc_of) as seen:
        stack.client.rag.retrieve("q", project_id="default")
    payload = {
        "answer": "Passage eleven [11].",
        "sources": [{"type": "rag_document", "title": f"scifact-{n}.md"} for n in range(100, 110)],
        "citations": [{"index": 11, "source_title": "scifact-110.md"}],
    }
    got = cp.grounding(payload, seen, {"110"})
    assert got["grounded"] is True and got["cited_docs"] == ["110"]


def test_a_retrieve_outage_inside_the_search_is_an_error_not_a_result():
    stack = _stack(FakeRag(fail=RuntimeError("encoder unavailable")))
    with pytest.raises(cp.infer.TransientError, match="rag.retrieve failed"):
        _agentic(stack, _claim(1, "SUPPORT", ["111"]))


@pytest.mark.parametrize(
    ("output", "error"),
    [
        ("Error running agentic search: spine query failed", "TransientError"),
        ("Error running agentic search: bad scope", "AgenticError"),
        ("[1, 2]", "AgenticError"),
    ],
)
def test_the_tool_error_string_is_never_a_result(output, error):
    with pytest.raises(Exception) as caught:
        cp.agentic_payload(output)
    assert type(caught.value).__name__ == error


def test_the_recorder_puts_the_client_method_back():
    rag = FakeRag(["1"])
    client = FakeClient(rag)
    with cp.RetrieveRecorder(client, lambda *_r: None):
        assert "retrieve" in vars(rag)
    assert "retrieve" not in vars(rag) and rag.retrieve.__func__ is FakeRag.retrieve
    namespace = SimpleNamespace(retrieve=lambda _q, **_k: None)
    original = namespace.retrieve
    with cp.RetrieveRecorder(SimpleNamespace(rag=namespace), lambda *_r: None):
        assert namespace.retrieve is not original
    assert namespace.retrieve is original


# ── the loop digest ──────────────────────────────────────────────────────────


def _executor(stack):
    return cp.loop_executor(stack, "llama3.2:3b")


def test_the_loop_digest_finds_the_gold_doc_in_its_pack():
    stack = _stack(FakeRag(["555", "666"]))
    row = cp.loop_row(stack, _claim(7, "SUPPORT", ["666"]), _executor(stack))
    assert (row["basis"], row["gold_in_digest"], row["gold_rank_in_digest"]) == (
        "sufficient",
        True,
        2,
    )
    assert row["digest_docs"] == ["555", "666"] and row["digest_chars"] > 0
    query = stack.client.rag.calls[0]
    assert query["query"] == row["goal"] and query["k_max"] == 4 and query["profile"] == "small"


def test_an_insufficient_goal_finds_no_gold_and_says_why():
    stack = _stack(FakeRag([]))
    row = cp.loop_row(stack, _claim(7, "SUPPORT", ["666"]), _executor(stack))
    assert (row["basis"], row["gold_in_digest"], row["digest_docs"]) == ("insufficient", False, [])


def test_a_retrieve_outage_in_the_loop_is_an_error_not_the_graph_digest():
    """The executor swallows the failure and falls back to the graph digest;
    the probe must not score that as the digest's answer."""
    stack = _stack(FakeRag(fail=RuntimeError("ReadTimeout")))
    with pytest.raises(cp.infer.TransientError, match="loop digest"):
        cp.loop_row(stack, _claim(7, "SUPPORT", ["666"]), _executor(stack))


# ── gates ────────────────────────────────────────────────────────────────────


def _grounded_rows(grounded: int, total: int = 30, errors: int = 0) -> list[dict]:
    rows = []
    for i in range(total):
        label = LABELS[i % 3]
        if i < errors:
            rows.append({"claim": i, "label": label, "error": "ReadTimeout"})
            continue
        ok = i < errors + grounded
        rows.append(
            {
                "claim": i,
                "label": label,
                "grounded": ok,
                "phantoms": [],
                "cites_gold": None if label == "NOT_ENOUGH_INFO" else ok,
                "gold_retrieved": None if label == "NOT_ENOUGH_INFO" else True,
                "seconds": 1.0,
            }
        )
    return rows


@pytest.mark.parametrize(
    ("grounded", "errors", "passed"),
    # (26, 3): 26 of the 27 judged answers are grounded (0.96), yet only 26 of
    # the 30 claims (0.87): an errored claim counts as not grounded.
    [(27, 0, True), (26, 0, False), (27, 3, True), (26, 3, False)],
)
def test_the_agentic_gate_is_ninety_percent_of_thirty_with_errors_not_grounded(
    grounded, errors, passed
):
    summary = cp.agentic_summary(_grounded_rows(grounded, errors=errors))
    gate = cp.agentic_gate(summary)
    assert gate["pass"] is passed and gate["n"] == 30
    assert gate["value"] == pytest.approx(grounded / 30)
    assert summary["grounded"] == pytest.approx(grounded / (30 - errors))
    assert summary["errors"] == errors and summary["nei"]["n"] == 10
    assert summary["nei"]["cites_gold_n"] == 0  # NEI has no gold: reported apart


def test_the_agentic_gate_needs_all_thirty_claims():
    assert cp.agentic_gate(cp.agentic_summary(_grounded_rows(29, total=29)))["pass"] is False


@pytest.mark.parametrize(("found", "errors", "passed"), [(3, 0, True), (2, 0, False), (3, 2, True)])
def test_the_loop_gate_is_three_of_five(found, errors, passed):
    rows = [{"claim": i, "label": "SUPPORT", "error": "x"} for i in range(errors)]
    rows += [
        {
            "claim": 10 + i,
            "label": "SUPPORT",
            "gold_in_digest": i < found,
            "basis": "partial",
            "seconds": 0.1,
        }
        for i in range(5 - errors)
    ]
    gate = cp.loop_gate(cp.loop_summary(rows))
    assert (gate["found"], gate["pass"]) == (min(found, 5 - errors), passed)


def test_a_leg_not_run_has_no_verdict():
    summary = cp.summarize({"loop": []})
    assert summary["gates"]["agentic"]["pass"] is None and summary["pass"] is None


# ── the command, end to end on the stub transport ────────────────────────────


def _scifact_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    lines = []
    for c in _claims():
        evidence = {doc: [{"label": c.label, "sentences": [0]}] for doc in c.evidence}
        lines.append(
            json.dumps(
                {
                    "id": c.id,
                    "claim": c.text,
                    "evidence": evidence,
                    "cited_doc_ids": sorted(c.cited),
                }
            )
        )
    (data / "claims_dev.jsonl").write_text("\n".join(lines) + "\n")
    return data


def _claim_doc(query: str) -> list[str]:
    """The doc named by the claim id in a query: every query retrieves its
    own claim's doc (gold for an evidence claim)."""
    return [next(word for word in query.split() if word.isdigit())]


def test_run_writes_one_file_with_both_legs_and_their_gates(tmp_path, monkeypatch, capsys):
    data = _scifact_dir(tmp_path)
    monkeypatch.setattr(cp.run, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cp.backends, "provenance", lambda **kw: {"stub": True, **kw})
    stack = _stack(FakeRag(docs_for=_claim_doc), answer="It is so [1].")
    args = cp.parser().parse_args(["run", "--data", str(data), "--scope", "rag_only"])
    assert cp.cmd_run(args, stack=stack) == 0
    record = json.loads(Path(json.loads(capsys.readouterr().out)["file"]).read_text())
    summary, gates = record["summary"], record["summary"]["gates"]
    observed = {
        "kind": (record["kind"], record["slice"], record["settings"]["agentic_scope"]),
        "rows": (len(record["rows"]["agentic"]), len(record["rows"]["loop"])),
        "tool_reader": record["provenance"]["tool_reader"],
        "agentic": (gates["agentic"]["value"], gates["agentic"]["pass"]),
        "loop": (gates["loop"]["found"], gates["loop"]["pass"], summary["pass"]),
        "cites_gold": summary["agentic"]["evidence"]["cites_gold"],
    }
    assert observed == {
        "kind": ("consumers", "S6", "rag_only"),
        "rows": (30, 5),
        "tool_reader": "llama3.2:3b",
        "agentic": (1.0, True),
        "loop": (5, True, True),
        "cites_gold": 1.0,
    }
    assert record["definitions"]["grounded"].startswith("the tool answered")


def test_a_failing_claim_is_an_error_row_counted_against_the_gate(tmp_path, monkeypatch):
    claims = _claims()
    rag = FakeRag(fail=RuntimeError("encoder unavailable"))
    stack = _stack(rag)
    args = argparse.Namespace(legs=("agentic",), scope="rag_only", depth="shallow", model="m")
    rows = cp.measure(stack, claims, args, tool=_tool(stack))
    assert len(rows["agentic"]) == 30 and all("error" in r for r in rows["agentic"])
    gate = cp.summarize(rows)["gates"]["agentic"]
    assert (gate["value"], gate["pass"]) == (0.0, False)


def test_plan_prints_the_selection_without_a_stack(tmp_path, capsys):
    assert cp.main(["plan", "--data", str(_scifact_dir(tmp_path))]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["seed"] == cp.SEED and len(plan["loop"]) == 5
    assert sum(len(ids) for ids in plan["agentic"].values()) == 30


def test_an_unknown_leg_is_refused():
    with pytest.raises(SystemExit):
        cp.parser().parse_args(["run", "--data", "d", "--legs", "agentic,web"])
