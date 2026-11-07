"""`r4 knowledge search` prints what the K3 facade returned.

``GET /umrs/knowledge/query`` answers ``{"status": "ok", "query": {...}}``;
the human printer read ``results``/``wired_backends`` from the top level, so
every non-JSON search printed "0 result(s) [no backend]" while ``--json``
showed the hits (stand-alone stack, 2026-10-09).
"""

from __future__ import annotations

from r4._cli.knowledge import _print_query_results

_ENVELOPE = {
    "status": "ok",
    "query": {
        "query": "moisture damage",
        "scope": "all",
        "results": [
            {
                "result_type": "embedding_match",
                "title": "chunk 0 of art-a83a9490c9fb",
                "content": "Encapsulation slows moisture ingress.",
                "score": 0.79,
                "metadata": {"lane": "similarity"},
            },
            {
                "result_type": "knowledge_node",
                "title": "alumina",
                "content": "{'semantic_type': 'material'}",
                "score": 0.6,
                "metadata": {},
            },
        ],
        "duration_seconds": 0.042,
        "wired_backends": ["catalog", "knowledge_graph", "embeddings", "rag"],
        "unavailable_backends": {},
        "embeddings_lane": {"lane": "similarity", "model": "/models/e5-base-v2"},
    },
}


def test_envelope_results_are_printed(capsys):
    _print_query_results(_ENVELOPE, "moisture damage")
    out = capsys.readouterr().out
    assert "2 result(s) for 'moisture damage'" in out
    assert "[catalog, knowledge_graph, embeddings, rag; 42ms]" in out
    assert "chunk 0 of art-a83a9490c9fb" in out
    assert "Encapsulation slows moisture ingress." in out
    assert "no backend" not in out


def test_embeddings_lane_is_named(capsys):
    _print_query_results(_ENVELOPE, "moisture damage")
    assert "embeddings lane: similarity (/models/e5-base-v2)" in capsys.readouterr().out


def test_fallback_lane_says_why(capsys):
    body = dict(_ENVELOPE["query"])
    body["embeddings_lane"] = {"lane": "table-reader", "reason": "no query encoder"}
    _print_query_results({"status": "ok", "query": body}, "q")
    assert "embeddings lane: table-reader (no query encoder)" in capsys.readouterr().out


def test_empty_space_names_model_and_reason(capsys):
    body = dict(_ENVELOPE["query"])
    body["embeddings_lane"] = {"lane": "similarity", "model": "m", "reason": "no vectors"}
    _print_query_results({"status": "ok", "query": body}, "q")
    assert "embeddings lane: similarity (m; no vectors)" in capsys.readouterr().out


def test_bare_payload_still_prints(capsys):
    """Callers that already unwrapped the envelope keep working."""
    _print_query_results(_ENVELOPE["query"], "moisture damage")
    assert "2 result(s)" in capsys.readouterr().out


def test_multiline_snippet_is_one_line(capsys):
    body = dict(_ENVELOPE["query"])
    body["results"] = [dict(body["results"][0], content="# Note\n\nline one.\nline two.")]
    _print_query_results({"status": "ok", "query": body}, "q")
    assert "      # Note line one. line two." in capsys.readouterr().out
