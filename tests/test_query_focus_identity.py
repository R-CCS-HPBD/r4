"""ADR-093 wave H: the router's query focus leaves the eval's own text alone.

A bare SciFact claim, a nonsense probe and a null-calibration probe carry no
framing: they are searched exactly as written (I1), so the focus cannot move
the bare arm, the out-of-KB probes or the calibrated null. The Phase-8 agent
prompt around a claim gives back the claim itself.

The local SciFact data is read from ``SCIFACT_DATA`` (else
``~/data/scifact/data``, the harness README's location); without it those
tests skip.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from umrs.knowledge.retrieval.calibrate import NULL_PROBES_V1

from benchmarks.retrieval import scifact
from r4.companion.query_focus import FOCUS_ENV, content_tokens, focus_query

ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ROOT / "benchmarks" / "retrieval" / "backends.py"
DATA = Path(os.environ.get("SCIFACT_DATA") or Path.home() / "data" / "scifact" / "data")
CLAIM_FILES = ("claims_dev.jsonl", "claims_train.jsonl", "claims_test.jsonl")
GRAPHS = ("scifact-eval", "scifact")
#: Claims that need no data: the judge panel's five (2026-10-11).
JUDGE_CLAIMS = (
    "0-dimensional biomaterials show inductive properties.",
    "Aspirin use is associated with a reduced risk of colorectal adenoma recurrence.",
    "IL-6 signalling drives insulin resistance in adipose tissue of obese mice.",
    "Zeolite ZSM-5 with a higher Si/Al ratio shows lower coke formation during "
    "methanol-to-olefins conversion.",
    "Knowledge of the patient's genotype does not improve warfarin dosing outcomes.",
)


@pytest.fixture(autouse=True)
def _focus_on(monkeypatch):
    monkeypatch.setenv(FOCUS_ENV, "on")


def _agent_prompt() -> str:
    """``backends.AGENT_PROMPT``, read from the source: the harness module
    imports its siblings by bare name and is only importable as a script."""
    tree = ast.parse(BACKENDS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = getattr(node, "targets", [])
        if isinstance(node, ast.Assign) and [getattr(t, "id", "") for t in targets] == [
            "AGENT_PROMPT"
        ]:
            return ast.literal_eval(node.value)
    raise AssertionError("AGENT_PROMPT not found in backends.py")


def _local_claims() -> list[str]:
    files = [DATA / name for name in CLAIM_FILES if (DATA / name).is_file()]
    if not files:
        pytest.skip(f"no SciFact claims under {DATA} (set SCIFACT_DATA)")
    claims: list[str] = []
    for path in files:
        with path.open(encoding="utf-8") as fh:
            claims.extend(json.loads(line)["claim"] for line in fh if line.strip())
    return claims


def _normal(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(
    "probe", [*scifact.NONSENSE_EVAL_V1, *NULL_PROBES_V1], ids=lambda probe: probe[:32]
)
def test_a_probe_is_searched_as_written(probe):
    focus = focus_query(probe)
    assert focus.basis == "whole" and focus.query is probe


def test_every_local_scifact_claim_is_searched_as_written():
    moved = [claim for claim in _local_claims() if focus_query(claim).query is not claim]
    assert moved == []


def _recovered(claims: list[str]) -> tuple[int, list[str], int]:
    """(exact recoveries, claims that lost a content token, messages)."""
    prompt = _agent_prompt()
    exact, lost, total = 0, [], 0
    for graph in GRAPHS:
        for claim in claims:
            query = focus_query(prompt.format(graph=graph, claim=claim)).query
            exact += query == _normal(claim)
            if set(content_tokens(claim)) - set(content_tokens(query)):
                lost.append(claim)
            total += 1
    return exact, lost, total


def test_the_agent_prompt_gives_back_the_judge_claims():
    exact, lost, total = _recovered(list(JUDGE_CLAIMS))
    assert (exact, lost) == (total, [])


def test_the_agent_prompt_gives_back_every_local_claim():
    """The claim exactly (whitespace aside: the retrieve call normalises it)
    for at least 99.5% of local claims, and every content token for all."""
    exact, lost, total = _recovered(_local_claims())
    assert lost == []
    assert exact / total >= 0.995
