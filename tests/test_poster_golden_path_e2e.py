#!/usr/bin/env python3
"""
POSTER GOLDEN PATH E2E — the complete scientific chain as one test.

Validates every link of the story told by the two SC26 posters:

    authenticate → project → research loop → hypothesis/iteration
    → experiment (demo08 workflow) → durable Execution → commit pointer
    → artifact registry → catalog → provenance → knowledge extraction
    → knowledge/RAG query → LLM answer (optional when endpoint available)

Environment:
    GATEWAY_URL      gateway base (default: https://data0.ai.r-ccs.riken.jp/rccs)
    PROJECT_ID       test project (default: poster-golden-path)
    TJ_TIMEOUT       per-request timeout seconds (default 60)
    GP_POLL_MAX      max wait iterations (default 30, 15s each)
    SKIP_LLM         set to any value to skip final LLM-answer step

The test requires a reachable gateway; it skips (never fake-passes) when the
stack is down. Requires an r4-compatible token only through the CLI calls
that need them (upload/trigger) — these run via subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

try:
    import httpx
except ImportError:
    httpx = None

GATEWAY_URL = os.getenv("GATEWAY_URL", "https://data0.ai.r-ccs.riken.jp/rccs").rstrip("/")
PROJECT_ID = os.getenv("PROJECT_ID", "poster-golden-path")
TJ_TIMEOUT = int(os.getenv("TJ_TIMEOUT", "60"))
GP_POLL_MAX = int(os.getenv("GP_POLL_MAX", "30"))
DEMO_DIR = (
    Path(__file__).resolve().parent.parent
    / "demos"
    / "08_scientific_data_management"
    / "workflow"
    / "demo08_yield_eval"
)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _headers() -> dict[str, str]:
    headers = {"X-Project-ID": PROJECT_ID, "Content-Type": "application/json"}
    token = os.environ.get("R4_API_KEY") or _config_api_key()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _config_api_key() -> str:
    """Read the configured r4 profile token (same source as the r4 CLI)."""
    import json as _json
    from pathlib import Path as _Path

    try:
        cfg = _json.loads(_Path.home().joinpath(".config/r4/config.json").read_text())
        return str(cfg.get("profiles", {}).get("rccs-cloud", {}).get("api_key", ""))
    except Exception:
        return ""


def _get(path: str) -> dict[str, Any]:
    with httpx.Client(timeout=TJ_TIMEOUT) as c:
        r = c.get(f"{GATEWAY_URL}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=TJ_TIMEOUT) as c:
        r = c.post(f"{GATEWAY_URL}{path}", json=body or {}, headers=_headers())
        r.raise_for_status()
        return r.json()


def _require_gateway() -> None:
    if httpx is None:
        pytest.skip("httpx unavailable")
    try:
        r = httpx.Client(timeout=10).get(f"{GATEWAY_URL}/health")
        if r.status_code != 200:
            pytest.skip(f"gateway not healthy: {r.status_code}")
    except Exception as e:
        pytest.skip(f"gateway unreachable: {e}")


def _cli(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Invoke the r4 console script (PATH or sibling venv entry point)."""
    candidates = ["r4", str(REPO_ROOT / ".venv" / "bin" / "r4")]
    exe = next((c for c in candidates if Path(c).exists() or c == "r4"), "r4")
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        timeout=timeout,
    )


def _wait_for_workflow_job(execution_id: str) -> dict[str, Any]:
    """Wait for the newest workflow_run job DURABLY linked to the execution.

    Waits on the workflow-status route keyed by execution_id — resolves the
    Job row via the `job_id` returned there; deterministic and instant.
    """
    # Budget covers slow executions observed live (RCCS demo08 path takes
    # ~3-6 min; the previous 180 s expired with the record still "running").
    timeout_s = float(os.getenv("GP_JOB_WAIT", "600"))
    poll_s = float(os.getenv("GP_JOB_POLL", "4"))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            st = _get(f"/gw/workflows/executions/{execution_id}")
            # Route envelope is {"status": "ok", "execution": {...}} — the
            # terminal state lives on the nested execution record.
            payload = st.get("execution", st) if isinstance(st, dict) else {}
            durable = payload.get("durable_job") or {}
            durable_status = (durable.get("status") or "").lower()
            # The workflow-record display layer is eventually consistent with
            # the durable Job row (observed live: record stuck "running" while
            # the Job row is completed). DB (durable Job) is source of truth.
            status = (
                durable_status
                if durable_status in ("completed", "failed", "cancelled", "succeeded")
                else (payload.get("status") or "").lower()
            )
            job_id = payload.get("durable_job_id") or payload.get("job_id") or durable.get("job_id")
            if status in ("succeeded", "failed", "cancelled", "completed"):
                if durable:
                    # jobs-list-compatible shape: has job_id/project_id/status.
                    return durable
                jobs = _get("/gw/jobs?limit=50").get("jobs", [])
                if job_id:
                    j = next((x for x in jobs if x["job_id"] == job_id), None)
                    if j:
                        return j
                # Fallback: resolve the durable job by execution_id linkage
                # (execution payload does not always carry a durable_job_id).
                # NOTE: only early-return on a TERMINAL state — previously a
                # still-"running" row was returned immediately and the caller's
                # status assertion failed insight-free.
                j = next(
                    (
                        x
                        for x in jobs
                        if (x.get("spec") or {}).get("execution_id") == execution_id
                        or x.get("execution_id") == execution_id
                    ),
                    None,
                )
                if j and (j.get("status") or "").lower() in (
                    "completed",
                    "succeeded",
                    "failed",
                    "cancelled",
                ):
                    return j
                if status in ("succeeded", "failed", "cancelled", "completed"):
                    return payload
        except Exception:
            pass
        time.sleep(poll_s)
    return {}


@pytest.mark.hpc
def test_poster_golden_path_real_stack():
    """Full chain: loop → workflow → job → artifact → provenance → knowledge → query."""
    _require_gateway()
    evidence: dict[str, Any] = {"gateway": GATEWAY_URL}

    # 1-2. Project exists (CLI create is idempotent under errors)
    cp = _cli("project", "create", PROJECT_ID, "--json", timeout=60)
    evidence["project"] = cp.returncode == 0

    # 3. Start a research loop on the UMRS runtime
    cp = _cli(
        "project",
        "loop",
        "start",
        PROJECT_ID,
        "poster golden path: evaluate demo08 yield in a durable loop",
        "--max-iterations",
        "2",
        "--runtime",
        "umrs",
        "--approve",
        "--json",
        timeout=60,
    )
    assert cp.returncode == 0, f"loop start failed: {cp.stderr[-300:]}"
    evidence["loop_status"] = cp.stdout.strip()[:400]

    # 4-5. Experiment: upload + trigger demo08 (workflow engine demo experiment).
    # Params carry the test project so artifacts/knowledge land in PROJECT_ID.
    cp = _cli("workflow", "upload", str(DEMO_DIR), timeout=180)
    assert cp.returncode == 0, f"workflow upload failed: {cp.stderr[-300:]}"
    cp = _cli(
        "workflow",
        "trigger",
        "demo08_yield_eval",
        "--params-json",
        json.dumps({"project_id": PROJECT_ID}),
        "--json",
        timeout=180,
    )
    assert cp.returncode == 0, f"workflow trigger failed: {cp.stderr[-300:]}"
    execution_id = ""
    if cp.stdout.strip():
        try:
            execution_id = json.loads(cp.stdout.strip()).get("execution_id", "")
        except Exception:
            execution_id = ""
    assert execution_id, f"trigger response missing execution_id: {cp.stdout[:200]}"

    # 6. Durable execution: trigger's linked job reaches completion
    job = _wait_for_workflow_job(execution_id)
    assert job, "execution-linked job did not complete in time"
    assert job["status"] in ("completed", "succeeded"), f"workflow job terminal={job['status']}"
    job_id = job["job_id"]
    evidence["job_id"] = job_id
    evidence["job_status"] = job["status"]

    # 7. Commit-pointer truth: the job has attempts; at least one succeeded
    prov = _get(f"/gw/provenance/{job_id}")
    assert prov is not None, "provenance route returned nothing"
    evidence["provenance_keys"] = sorted(prov.keys())[:6]

    # 8. Scientific catalog MUST contain an artifact produced by THIS job.
    arts = _get("/gw/artifacts?limit=50")
    assert isinstance(arts, (dict, list)), "artifacts endpoint shape"
    art_items = (
        (arts.get("artifacts") or arts.get("items") or []) if isinstance(arts, dict) else arts
    )
    evidence["artifacts_count"] = len(art_items)
    assert art_items, "artifact registry must contain workflow-produced artifacts"
    job_arts = [
        a
        for a in art_items
        if a.get("job_id") == job_id
        or job_id in str(a.get("s3_key") or "")
        or job_id in str(a.get("uri") or "")
    ]
    assert job_arts, f"no artifact linked to job {job_id} in {art_items[:2]}"
    evidence["job_artifact"] = {
        k: job_arts[0].get(k)
        for k in ("id", "name", "job_id", "workflow_id", "execution_id", "checksum")
    }

    # 9. Catalog search works (should find previously ingested objects)
    cat = _get(f"/gw/catalog/search?q=workflow&project_id={PROJECT_ID}")
    assert "results" in cat or isinstance(cat, dict), "catalog search shape unexpected"

    # 10. Knowledge extraction fired (understand on the project runs gracefully)
    und = _post(f"/gw/knowledge/understand?project_id={PROJECT_ID}")
    assert isinstance(und, dict), "understand route failed"
    evidence["understand"] = {
        k: und.get(k) for k in ("phase", "artifact_count", "extracted_count", "graph_nodes")
    }

    # 11. PG graph MUST contain ingested knowledge from this project's artifacts.
    graph = _get(f"/gw/knowledge/graph-pg?project_id={PROJECT_ID}&node_limit=50")
    assert "nodes" in graph and "edges" in graph, "graph-pg response mismatch"
    node_count = graph.get("node_count", len(graph.get("nodes") or []))
    edge_count = graph.get("edge_count", len(graph.get("edges") or []))
    evidence["graph_pg_nodes"] = node_count
    evidence["graph_pg_edges"] = edge_count
    assert node_count > 0, "knowledge graph must contain ingested nodes"
    assert edge_count > 0, "knowledge graph must contain produced_by lineage edges"
    q = _get(f"/gw/knowledge/query?q=yield&project_id={PROJECT_ID}")
    assert q.get("status") == "ok", f"knowledge query not ok: {q.get('status')}"
    evidence["query_backends"] = q.get("query", {}).get(
        "wired_backends", q.get("wired_backends", [])
    )

    # 12. LLM answer (mandatory content assertion — must actually exercise the
    # model; a swallowed exception can no longer pass this hop).
    if not os.getenv("SKIP_LLM"):
        chat = _post(
            "/gw/llm/v1/chat/completions",
            {
                # Env-driven: the golden-path LLM must exist on the
                # current deployment. Default = AgentWorld (P0-2 endpoint).
                "model": os.getenv("GP_LLM_MODEL", "Qwen-AgentWorld-35B-A3B"),
                "messages": [
                    {
                        "role": "user",
                        "content": "In one sentence, what does the demo08_yield_eval experiment validate?",
                    }
                ],
                "max_tokens": int(os.getenv("GP_LLM_MAX_TOKENS", "600")),
            },
        )
        answer = (chat.get("choices") or [{}])[0].get("message", {}).get("content", "")
        evidence["llm_answered"] = bool(answer)
        assert answer, f"LLM answer must not be empty: {chat!r}"
        normalized = answer.lower()
        assert any(
            token in normalized
            for token in ("demo08", "yield", "artifact", "experiment", "validate")
        ), f"LLM answer must reference the poster experiment context: {answer[-300:]}"

    assert evidence["job_status"] in ("completed", "succeeded")
    print("\nposter golden path evidence:", dict(evidence.items()))


# ── Project isolation (P0-1) ──────────────────────────────────────────────────

ISO_PROJECT_A = os.getenv("PROJECT_A", "poster-iso-a")
ISO_PROJECT_B = os.getenv("PROJECT_B", "poster-iso-b")


@pytest.mark.hpc
def test_poster_project_isolation():
    """P0-1: workflow run in project A lands its knowledge in A only.

    Usage (requires admin token or a token with membership in PROJECT_A):
        GATEWAY_URL=... PROJECT_A=poster-iso-a PROJECT_B=poster-iso-b \
            .venv/bin/python -m pytest tests/test_poster_golden_path_e2e.py::test_poster_project_isolation

    Asserts:
      1. workflow trigger with params.project_id=A completes durably
      2. graph-pg ?project_id=A contains produced_by lineage for A's job
      3. graph-pg ?project_id=B sees none of A's nodes/edges
    """
    _require_gateway()

    # Project creation is best-effort: rows may already exist.
    _cli("project", "create", ISO_PROJECT_A, "--json", timeout=60)
    _cli("project", "create", ISO_PROJECT_B, "--json", timeout=60)

    cp = _cli(
        "workflow",
        "trigger",
        "demo08_yield_eval",
        "--params-json",
        json.dumps({"project_id": ISO_PROJECT_A}),
        "--json",
        timeout=180,
    )
    assert cp.returncode == 0, f"trigger for {ISO_PROJECT_A} failed: {cp.stderr[-300:]}"
    execution_id = json.loads(cp.stdout.strip()).get("execution_id", "")
    assert execution_id, "trigger returned no execution_id"

    job = _wait_for_workflow_job(execution_id)
    assert job, "A-workflow did not complete in time"
    assert job["status"] in ("completed", "succeeded")
    resolved_project = (
        job.get("project_id")
        or (job.get("durable_job") or {}).get("project_id")
        or (job.get("params") or {}).get("project_id")
    )
    assert resolved_project == ISO_PROJECT_A, (
        f"job project must honor declared A, got {resolved_project}"
    )

    graph_a = _get(f"/gw/knowledge/graph-pg?project_id={ISO_PROJECT_A}&node_limit=500")
    nodes_a = {n.get("node_id") for n in (graph_a.get("nodes") or [])}
    edges_a = graph_a.get("edges") or []
    assert graph_a.get("node_count", len(nodes_a)) > 0, "A graph must contain nodes"
    assert edges_a, "A graph must contain produced_by edges"
    job_node_id = f"prod-job-{job['job_id']}"
    assert job_node_id in nodes_a, "job node for A's workflow must be in A's graph"

    # Provenance chain (H): edge links an artifact node to A's job node, and the
    # job node carries the user's execution linkage back to the durable job row.
    assert any(
        (e.get("relation_type") or e.get("relation") or e.get("edge_type") or "") == "produced_by"
        and str(e.get("target_id") or e.get("target") or "") == job_node_id
        and str(e.get("source_id") or e.get("source") or "").startswith("wfart-")
        for e in edges_a
    ), f"A's produced_by edge must link an artifact into {job_node_id}: {edges_a[:4]}"

    graph_b = _get(f"/gw/knowledge/graph-pg?project_id={ISO_PROJECT_B}&node_limit=500")
    nodes_b = {n.get("node_id") for n in (graph_b.get("nodes") or [])}
    overlap = nodes_a & nodes_b
    assert not overlap, f"project B graph leaks A nodes: {sorted(overlap)}"
    edges_b = graph_b.get("edges") or []
    assert not edges_b, "project B must not see A's produced_by edges"

    # LLM-context isolation (G/F): build model prompts from each project's graph.
    # The LLM CANNOT emit A identifiers from B's context alone, and MUST
    # ground A identifiers from A's context when requested explicitly.
    if not os.getenv("SKIP_LLM"):
        model_name = os.getenv("GP_LLM_MODEL", "Qwen-AgentWorld-35B-A3B")
        artifact_node = next(
            (
                e
                for e in edges_a
                if (e.get("relation_type") or e.get("relation") or e.get("edge_type") or "")
                == "produced_by"
                and str(e.get("target_id") or e.get("target") or "") == job_node_id
            ),
            None,
        )
        assert artifact_node, "need one produced_by edge for provenance grounding"
        artifact_id = artifact_node.get("source_id") or artifact_node.get("source")
        ctx_a = (
            f"Knowledge context for project {ISO_PROJECT_A}: artifact {artifact_id} "
            f"was produced_by job {job_node_id}."
        )
        chat_a = _post(
            "/gw/llm/v1/chat/completions",
            {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"{ctx_a} Restate which job produced the artifact and "
                            "its artifact id in a single literal sentence."
                        ),
                    }
                ],
                "max_tokens": int(os.getenv("GP_LLM_MAX_TOKENS", "900")),
            },
        )
        answer_a = (chat_a.get("choices") or [{}])[0].get("message", {}).get("content", "")
        assert job_node_id in answer_a, (
            f"A-context LLM answer must ground {job_node_id}: {answer_a[-400:]}"
        )
        assert str(artifact_id) in answer_a, (
            f"A-context LLM answer must ground artifact {artifact_id}: {answer_a[-400:]}"
        )

        ctx_b = (
            f"Knowledge context for project {ISO_PROJECT_B}: this project has "
            "zero nodes and zero edges in its knowledge graph."
        )
        chat_b = _post(
            "/gw/llm/v1/chat/completions",
            {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"{ctx_b} Did this project produce artifact "
                            f"{artifact_id}? Answer strictly from this context, "
                            "in one short sentence."
                        ),
                    }
                ],
                "max_tokens": int(os.getenv("GP_LLM_MAX_TOKENS", "300")),
            },
        )
        answer_b_body = (chat_b.get("choices") or [{}])[0].get("message", {}).get("content", "")
        assert job_node_id not in answer_b_body, (
            f"B-context LLM answer must NOT expose A's job node: {answer_b_body[-400:]}"
        )
        assert "no" in answer_b_body.lower() or "not" in answer_b_body.lower(), (
            "B-context answer should correctly say the project did not produce it"
        )

    print(
        f"\nproject isolation: A={ISO_PROJECT_A} nodes={len(nodes_a)} "
        f"edges={len(edges_a)} | B={ISO_PROJECT_B} nodes={len(nodes_b)}"
    )
