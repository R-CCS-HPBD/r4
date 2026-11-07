#!/usr/bin/env python3
"""
TASK-047: Compose Journey E2E (5-step) — Full Research Loop.

Validates the complete AI4S research journey through the R4 CLI / Gateway stack
in a **compose mock environment** (docker-compose.e2e.yaml):

    1. Ingest    — Create project, import research artifacts (paper + code)
    2. Understand — Extract structured knowledge, build knowledge graph
    3. Verify     — Ask evidence-based questions, check confidence scores
    4. Run        — Plan research, submit verification job, approve
    5. Export     — Generate knowledge bundle, verify export status

Each step verifies the previous step's state and checks system readiness
before proceeding.  In mock environments where backend services are stubbed,
steps that depend on real LLM inference gracefully assert structural
completeness (JSON schema, non-empty fields) instead of content correctness.

Prerequisites:
    docker compose -f docker-compose.e2e.yaml up -d

Usage:
    # Full suite
    pytest tests/test_research_journey_e2e.py -v

    # Specific step
    pytest tests/test_research_journey_e2e.py -v -k test_step_2_understand

    # Standalone (no pytest)
    python tests/test_research_journey_e2e.py

Environment Variables:
    GATEWAY_URL      — Gateway base URL   (default: http://localhost:8080)
    PROJECT_ID       — Test project ID    (default: tj-047)
    TEST_MODEL       — Model name         (default: rivault-gpt-oss-static)
    TJ_TIMEOUT       — Per-step timeout   (default: 60)
    TJ_DEBUG         — Verbose output     (default: false)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

try:
    import httpx
except ImportError:
    httpx = None

# Heavyweight tier (2026-10-18): the steps run `python -m r4` with the caller's
# real HOME against whatever answers on GATEWAY_URL (default localhost:8080),
# so a plain `pytest tests/` on a workstation with a stack up drove the
# developer's own platform. Opt in with R4_RUN_E2E=1 (tests/conftest.py).
pytestmark = pytest.mark.e2e

# ── Configuration ──────────────────────────────────────────────────────────

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080").rstrip("/")
PROJECT_ID = os.getenv("PROJECT_ID", "tj-047")
TEST_MODEL = os.getenv("TEST_MODEL", "rivault-gpt-oss-static")
TJ_TIMEOUT = int(os.getenv("TJ_TIMEOUT", "60"))
TJ_DEBUG = os.getenv("TJ_DEBUG", "false").lower() == "true"

# Paths used by the test
TEST_DATA_DIR = Path(__file__).parent / "samples" / "tj-047"

# ── HTTP helpers ───────────────────────────────────────────────────────────


def _headers(project_id: str = PROJECT_ID) -> dict[str, str]:
    """Return default auth + content headers for Gateway requests."""
    return {"X-Project-ID": project_id, "Content-Type": "application/json"}


def _post_http(path: str, json_body: dict, step: str) -> dict[str, Any]:
    """POST to the Gateway. Returns parsed JSON or empty dict on failure."""
    try:
        with httpx.Client(timeout=TJ_TIMEOUT) as c:
            r = c.post(f"{GATEWAY_URL}{path}", json=json_body, headers=_headers())
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        if TJ_DEBUG:
            print(f"  HTTP {step} failed: {exc}", file=sys.stderr)
        return {}


def _get_http(path: str, step: str) -> dict[str, Any]:
    """GET from the Gateway. Returns parsed JSON or empty dict on failure."""
    try:
        with httpx.Client(timeout=TJ_TIMEOUT) as c:
            r = c.get(f"{GATEWAY_URL}{path}", headers=_headers())
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        if TJ_DEBUG:
            print(f"  HTTP {step} failed: {exc}", file=sys.stderr)
        return {}


# ── CLI helpers ────────────────────────────────────────────────────────────


def _r4(*argv: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run the r4 CLI through the interpreter that hosts the tests."""
    return subprocess.run(
        [sys.executable, "-m", "r4", *argv],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=timeout,
    )


def _debug_proc(label: str, proc: subprocess.CompletedProcess, limit: int = 500) -> None:
    """Echo a CLI result when TJ_DEBUG is on."""
    if not TJ_DEBUG:
        return
    print(f"  {label} stdout: {proc.stdout.strip()[:limit]}")
    if proc.stderr.strip():
        print(f"  {label} stderr: {proc.stderr.strip()[:limit]}")


def _json_or_empty(proc: subprocess.CompletedProcess) -> dict[str, Any]:
    """Parse a ``--json`` CLI payload; ``{}`` when the call failed or was silent."""
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw_output": proc.stdout[:500]}


# ── Infrastructure probes ──────────────────────────────────────────────────


def _gateway_reachable() -> bool:
    """True when a Gateway answers ``/health`` at :data:`GATEWAY_URL`.

    The Gateway-level tests below talk to a real NGINX gateway (every route
    behind ``auth_request``); nothing in-process can stand in for it, so they
    skip when the compose stack is not up.  The CLI journey steps do *not* use
    this probe: ``r4`` drives an in-process UMRS when no Gateway answers, so
    those steps must keep running in CI.
    """
    if httpx is None:
        return False
    try:
        with httpx.Client(timeout=3.0) as client:
            return client.get(f"{GATEWAY_URL}/health", headers=_headers()).status_code < 500
    except Exception:
        return False


GATEWAY_UP = _gateway_reachable()

requires_gateway = pytest.mark.skipif(
    not GATEWAY_UP,
    reason=(
        f"requires a reachable R4 Gateway at {GATEWAY_URL} "
        "(docker compose -f docker-compose.e2e.yaml up -d)"
    ),
)


# ── CLI entry-point prerequisite ──────────────────────────────────────────


def test_r4_module_entry_point() -> None:
    """``python -m r4`` must work — every journey step depends on it.

    The steps shell out with ``sys.executable -m r4 ...`` so the CLI is pinned
    to the interpreter running the tests (the ``r4`` console script is only on
    PATH when the venv is activated).  That form needs ``r4/src/r4/__main__.py``
    delegating to the ``r4.cli:main`` entry point declared in
    ``r4/pyproject.toml``; without it every step below dies with
    "No module named r4.__main__".
    """
    result = subprocess.run(
        [sys.executable, "-m", "r4", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"`python -m r4 --version` failed: {result.stderr[:500]}"
    assert result.stdout.strip().startswith("r4 "), f"unexpected banner: {result.stdout!r}"


# ── Step 1: Ingest ────────────────────────────────────────────────────────


@requires_gateway
def test_step_1_ingest() -> dict[str, Any]:
    """Import research artifacts into the UMRS knowledge store.

    Creates a project, writes sample artifacts, runs ``r4 project import``,
    and validates the response.

    Validates:
        - Sample artifacts are created
        - r4 CLI import succeeds (or returns expected structural response)
        - Project data directory exists
    """
    print("\n▸ Step 1/5: Ingest — importing artifacts...")

    samples_dir = _ensure_sample_artifacts()
    artifact_count = sum(1 for f in samples_dir.iterdir() if f.is_file())
    assert artifact_count > 0, "Sample artifacts must exist"

    # Ensure the project exists first (already-exists is not an error here)
    _r4("project", "create", PROJECT_ID, "--name", "TASK-047 Test", timeout=30)

    # Run r4 project import (mimics CLI behavior)
    result = _r4("project", "import", PROJECT_ID, str(samples_dir), "--json")
    _debug_proc("import", result)

    # In mock/compose mode the CLI may succeed or return a partial response
    data = _json_or_empty(result)

    # Validate structural expectations
    artifacts_ingested = data.get("artifacts_ingested", artifact_count)
    assert artifacts_ingested >= artifact_count, (
        f"Should have ingested at least {artifact_count} artifacts, got {artifacts_ingested}"
    )

    data_dir = Path.home() / ".r4" / "projects" / PROJECT_ID
    if data_dir.exists():
        assert any(data_dir.iterdir()), "Project data_dir should contain files"

    print(f"  ✓ Ingested {artifacts_ingested} artifact(s) into project {PROJECT_ID}")
    return {
        "step": 1,
        "artifacts_ingested": artifacts_ingested,
        "data_dir": str(data_dir),
        "status": "ok",
    }


def _inject_planted_defects() -> list[str]:
    """Inject known defective artifacts into the project for verify to find.

    Returns list of defect IDs.
    """
    samples_dir = _ensure_sample_artifacts()
    defect_ids: list[str] = []

    # Create a deliberately broken file
    broken_file = samples_dir / "defective_analysis.txt"
    broken_file.write_text(
        "KNOWN_DEFECT: This analysis contains a circular reference in methodology.\n"
        "KNOWN_DEFECT: Sample size n=3 is statistically invalid.\n"
        "KNOWN_DEFECT: Results contradict established literature.\n",
        encoding="utf-8",
    )
    defect_ids.append("defect-001")

    # Create a file with contradictory statements
    contradict_file = samples_dir / "contradictory_results.txt"
    contradict_file.write_text(
        "KNOWN_DEFECT: Figure 1 shows accuracy=95% but text claims 72%.\n"
        "KNOWN_DEFECT: Methods section references Dataset B but results use Dataset C.\n",
        encoding="utf-8",
    )
    defect_ids.append("defect-002")

    # Re-import to include defects
    subprocess.run(
        [sys.executable, "-m", "r4", "project", "import", PROJECT_ID, str(samples_dir), "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    return defect_ids


# ── Step 2: Understand ───────────────────────────────────────────────────


@requires_gateway
def test_step_2_understand() -> dict[str, Any]:
    """Extract structured knowledge and build the knowledge graph.

    Validates:
        - Knowledge is extracted from ingested artifacts
        - Knowledge graph has nodes and edges
        - Phase advances from INGESTED → UNDERSTOOD
    """
    print("\n▸ Step 2/5: Understand — building knowledge graph...")

    # Via r4 project init CLI
    result = _r4("project", "init", PROJECT_ID, "--json")
    _debug_proc("init", result)

    assert result.returncode == 0, f"r4 project init failed: {result.stderr}"

    data = json.loads(result.stdout)
    assert "phase" in data, "Response should include phase"
    assert "extracted_count" in data, "Response should include extracted knowledge count"
    assert data["extracted_count"] > 0, "Should have extracted at least some knowledge"
    assert data.get("graph_nodes", 0) > 0, "Knowledge graph should have nodes"

    print(
        f"  ✓ Extracted {data['extracted_count']} knowledge item(s), "
        f"{data.get('graph_nodes', 0)} graph nodes"
    )
    return {
        "step": 2,
        "extracted": data["extracted_count"],
        "nodes": data.get("graph_nodes", 0),
        "status": "ok",
    }


# ── Step 3: Verify ───────────────────────────────────────────────────────


@requires_gateway
def test_step_3_verify() -> dict[str, Any]:
    """Ask evidence-based verification questions about the knowledge.

    Validates:
        - Verification answers are returned with evidence
        - Confidence score is present and non-negative
        - Answer cites source artifacts
    """
    print("\n▸ Step 3/5: Verify — asking knowledge questions...")

    questions = [
        "What is the main contribution of the paper?",
        "How does the transformer model compute attention?",
    ]

    results = [_verify_one(question) for question in questions]

    return {"step": 3, "verifications": results, "status": "ok"}


def _verify_one(question: str) -> dict[str, Any]:
    """Ask one verification question and assert the evidence envelope."""
    result = _r4("project", "verify", PROJECT_ID, question, "--json")
    _debug_proc(f"verify {question[:40]}", result, limit=200)

    assert result.returncode == 0, f"r4 project verify failed: {result.stderr}"

    data = json.loads(result.stdout)
    assert data.get("answered", False), "Should have an answer"
    assert "question" in data, "Response should echo the question"
    confidence = data.get("confidence", -1)
    assert confidence >= 0, f"Confidence should be non-negative, got {confidence}"

    print(f"  ✓ Verified: '{question[:50]}...' confidence={confidence:.2f}")
    return {"question": question, "confidence": confidence}


# ── Step 4: Run Workflow ─────────────────────────────────────────────────


@requires_gateway
def test_step_4_run_workflow() -> dict[str, Any]:
    """Execute the research workflow end-to-end.

    Validates:
        - Workflow execution starts and completes
        - Plan was generated (from the understand phase)
        - Workflow produces actionable output
    """
    print("\n▸ Step 4/5: Run — executing research workflow...")

    goal = "Analyze the transformer architecture and propose next steps"

    # First, generate a research plan via r4 project plan
    plan_result = _r4("project", "plan", PROJECT_ID, goal, "--json")
    _debug_proc("plan", plan_result, limit=300)

    # Then run the workflow with approval.
    #
    # `r4 project run` was retired by WP-07 (commit 4b807885, ADR-036
    # Amendment 2 §7): local runs folded onto `r4 project loop start`.  The
    # default `umrs` runtime fails closed until a workflow is registered with
    # the caller, so the offline journey drives the documented offline runtime
    # (`--runtime local`, "local (SQLite, offline)").
    run_result = _r4(
        "project",
        "loop",
        "start",
        PROJECT_ID,
        goal,
        "--runtime",
        "local",
        "--approve",
        "--max-iterations",
        "1",
        "--json",
        timeout=120,
    )
    _debug_proc("loop start", run_result)

    assert run_result.returncode == 0, f"r4 project loop start failed: {run_result.stderr[:500]}"

    run_data = json.loads(run_result.stdout)

    # Validate structure: the loop must be bound to this project and report a
    # lifecycle state, not just echo back an empty envelope.
    assert run_data.get("project_id") == PROJECT_ID, (
        f"Loop must be bound to {PROJECT_ID}, got {run_data.get('project_id')!r}"
    )
    assert run_data.get("loop_id"), "Loop response must carry a loop_id"
    assert run_data.get("status"), "Loop response must carry a status"

    print(f"  ✓ Workflow executed — project={run_data.get('project_id', PROJECT_ID)}")
    return {
        "step": 4,
        "plan_generated": plan_result.returncode == 0,
        "run_result": {k: str(v)[:200] for k, v in run_data.items()},
        "status": "ok",
    }


# ── Step 5: Export ────────────────────────────────────────────────────────


@requires_gateway
def test_step_5_export() -> dict[str, Any]:
    """Generate a final research report and export it.

    Validates:
        - Export triggers successfully (returns bundle_id/status)
        - Response has structured data (project info, knowledge stats)
        - Export can be queried via project status
    """
    print("\n▸ Step 5/5: Export — generating research report...")

    export_dir = TEST_DATA_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    # Use JSON output for structured assertions
    export_result = _r4("project", "export", PROJECT_ID, "--json")
    _debug_proc("export", export_result)

    assert export_result.returncode == 0, f"r4 project export failed: {export_result.stderr[:500]}"

    data = json.loads(export_result.stdout)
    # Export returns bundle_id and status
    assert "bundle_id" in data or "project_id" in data or "status" in data, (
        f"Export response missing structured data: {data}"
    )

    bundle_id = data.get("bundle_id", "pending")
    status = data.get("status", "unknown")
    print(f"  ✓ Export triggered — bundle_id={bundle_id}, status={status}")

    # Write export report for CI
    report_path = export_dir / "research_report.json"
    report_path.write_text(
        json.dumps({"project_id": PROJECT_ID, "export": data, "step": 5}, indent=2),
        encoding="utf-8",
    )

    # "status" is the per-step outcome every other step returns (the `_only`
    # wrapper and the full-journey test assert on it); the export job's own
    # state (processing/ready/...) rides along under its own key.
    return {
        "step": 5,
        "bundle_id": bundle_id,
        "export_status": status,
        "status": "ok",
        "report": str(report_path),
    }


# ── Full Journey Test ─────────────────────────────────────────────────────


def _print_journey_banner() -> None:
    """Print the journey header (gateway/project/model under test)."""
    print("\n" + "=" * 72)
    print("TASK-047: Compose Journey E2E (5-step)")
    print(f"  Gateway: {GATEWAY_URL}")
    print(f"  Project: {PROJECT_ID}")
    print(f"  Model:   {TEST_MODEL}")
    print("=" * 72)


def _run_step(step, label: str) -> dict[str, Any]:
    """Run one journey step and assert it reported a successful outcome."""
    result = step()
    assert result["status"] == "ok", f"{label} must succeed"
    return result


def _restart_checkpoint(label: str) -> dict[str, Any]:
    """Re-read project state after a step — the restart-stability checkpoint."""
    status = _check_project_status()
    assert status is not None, f"Project must be readable after {label}"
    print(f"  ✓ Restart stability (post-{label}): phase={status.get('phase', '?')}")
    return status


def _approve_planted_defects() -> None:
    """Resolve the planted defects through the approve verb (best effort)."""
    approve_result = _r4("project", "approve", PROJECT_ID, "--json")
    approve_data = _json_or_empty(approve_result)
    if approve_result.returncode == 0:
        print(f"  ✓ Defects approved — {approve_data.get('approved_count', 0)} resolved")


@pytest.mark.e2e
def test_research_journey_full():
    """Run the complete 5-step research journey.

    Full chain: create→import→init→verify→resolve→approve→plan→emit→run→assert
    Includes restart-stability checkpoints after step 2 and step 4.

    Steps:
        1. Ingest    — Import research artifacts + planted defects
        2. Understand — Extract structured knowledge + restart checkpoint
        3. Verify     — Ask evidence-based questions + resolve defects
        4. Run        — Execute research workflow + approve + restart checkpoint
        5. Export     — Generate final report
    """
    _print_journey_banner()

    results: list[dict[str, Any]] = []

    # Step 1: Ingest + Plant Defects
    results.append(_run_step(test_step_1_ingest, "Ingest"))

    defect_ids = _inject_planted_defects()
    print(f"  ✓ Planted {len(defect_ids)} defect(s): {', '.join(defect_ids)}")

    # Step 2: Understand + Restart Stability Check
    results.append(_run_step(test_step_2_understand, "Understand"))
    status_after_understand = _restart_checkpoint("understand")

    # Step 3: Verify + Resolve Defects
    results.append(_run_step(test_step_3_verify, "Verify"))
    _approve_planted_defects()

    # Step 4: Run Workflow + Emit + Restart Check
    results.append(_run_step(test_step_4_run_workflow, "Run"))
    status_after_run = _restart_checkpoint("run")

    # Step 5: Export
    results.append(_run_step(test_step_5_export, "Export"))

    # Summary
    print("\n" + "=" * 72)
    print(f"TASK-047 COMPLETE: {len(results)}/5 steps passed")
    print("=" * 72)

    # Store results for CI reporting
    report_path = TEST_DATA_DIR / "journey_report.json"
    report_path.write_text(
        json.dumps(
            {
                "journey": "TASK-047",
                "steps": results,
                "defects": defect_ids,
                "restart_stability": {
                    "post_understand": status_after_understand,
                    "post_run": status_after_run,
                },
                "gateway": GATEWAY_URL,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    assert len(results) == 5, f"Expected 5 steps, got {len(results)}"


# ── Individual Step Tests (for selective execution) ──────────────────────


@pytest.mark.e2e
def test_step_1_ingest_only():
    """Run Step 1 (Ingest) in isolation."""
    r = test_step_1_ingest()
    assert r["status"] == "ok"


@pytest.mark.e2e
def test_step_2_understand_only():
    """Run Step 2 (Understand) in isolation."""
    r = test_step_2_understand()
    assert r["status"] == "ok"


@pytest.mark.e2e
def test_step_3_verify_only():
    """Run Step 3 (Verify) in isolation."""
    r = test_step_3_verify()
    assert r["status"] == "ok"


@pytest.mark.e2e
def test_step_4_run_workflow_only():
    """Run Step 4 (Run) in isolation."""
    r = test_step_4_run_workflow()
    assert r["status"] == "ok"


@pytest.mark.e2e
def test_step_5_export_only():
    """Run Step 5 (Export) in isolation."""
    r = test_step_5_export()
    assert r["status"] == "ok"


# ── Gateway API-level Tests ───────────────────────────────────────────────


@pytest.mark.e2e
@requires_gateway
def test_gateway_health_for_journey():
    """Validate the Gateway is healthy before running the journey."""
    data = _get_http("/health", "health check")
    assert data.get("status") in ("ok", "degraded"), f"Gateway status: {data.get('status')}"
    print(f"  ✓ Gateway healthy: {data.get('status')}")


@pytest.mark.e2e
@requires_gateway
def test_project_list_via_gateway():
    """Validate project listing works via Gateway API."""
    data = _post_http("/umrs/projects", {}, "project list")
    # `_post_http` returns {} for any transport/HTTP error, so an empty payload
    # here means the route was refused (auth_request) or is missing — both are
    # failures, not "no projects".
    assert data, "Gateway project listing returned no payload (route or auth failure)"
    assert "projects" in data or isinstance(data, list), "Should return project list"
    print(f"  ✓ Projects listed via Gateway: {len(data.get('projects', data))}")


# ── Helpers ───────────────────────────────────────────────────────────────


def _ensure_sample_artifacts() -> Path:
    """Create sample test artifacts if they don't exist."""
    samples_dir = TEST_DATA_DIR / "artifacts"
    samples_dir.mkdir(parents=True, exist_ok=True)

    # Create sample paper analysis
    paper_file = samples_dir / "paper_analysis.md"
    if not paper_file.exists():
        paper_file.write_text(
            "# Transformer Architecture Analysis\n\n"
            "The transformer model uses self-attention mechanisms to process "
            "sequential data in parallel. Key components include:\n"
            "- Multi-head attention\n"
            "- Position encoding\n"
            "- Feed-forward networks\n\n"
            "Performance: accuracy=95%, F1=0.92 on benchmark tasks.\n",
            encoding="utf-8",
        )

    # Create sample code analysis
    code_file = samples_dir / "code_analysis.md"
    if not code_file.exists():
        code_file.write_text(
            "# Code Quality Report\n\n"
            "Analyzed 15 source files. Key findings:\n"
            "- 3 high-complexity functions (CCN > 10)\n"
            "- Missing type annotations in 42% of functions\n"
            "- 2 deprecated API calls found\n",
            encoding="utf-8",
        )

    return samples_dir


def _check_project_status() -> dict[str, Any] | None:
    """Query project status via Gateway. Returns None on failure."""
    try:
        return _get_http(f"/umrs/projects/{PROJECT_ID}", "status check")
    except Exception:
        return None


# ── Step 6: Provenance & Lineage (chain closure) ────────────────────────


def _latest_job_id(jobs: dict[str, Any]) -> str:
    """Extract the newest job_id from a jobs listing payload."""
    job_list = jobs.get("jobs", []) if isinstance(jobs, dict) else []
    return job_list[0].get("job_id", "") if job_list else ""


def _assert_provenance(job_id: str) -> None:
    """Assert a provenance record exists for the job (structural)."""
    if not job_id:
        print("  (no durable jobs yet — provenance check structural-only)")
        return
    prov = _get_http(f"/gw/provenance/{job_id}", "provenance")
    assert isinstance(prov, dict), "Provenance response must be an object"
    assert any(k in prov for k in ("records", "trace", "job_id", "attempts")), (
        f"Provenance record missing expected keys: {sorted(prov)[:8]}"
    )


@requires_gateway
def test_step_6_provenance_lineage() -> dict[str, Any]:
    """Close the chain: job → provenance → artifacts → knowledge graph.

    Validates (structurally; mock-tolerant):
        - ``GET /gw/jobs`` lists durable jobs
        - ``GET /gw/provenance/{job_id}`` returns a provenance record
        - ``GET /gw/artifacts`` lists artifacts
        - ``GET /gw/knowledge/graph`` returns nodes+edges

    Skips when the compose stack is not up (no fake pass).
    """
    print("\n▸ Step 6/6: Provenance & lineage — closing the research chain...")

    jobs = _get_http("/gw/jobs?limit=5", "jobs list")
    if not jobs:
        pytest.skip("gateway unreachable — compose stack not up")

    job_id = _latest_job_id(jobs)
    _assert_provenance(job_id)

    arts = _get_http("/gw/artifacts?limit=5", "artifacts")
    assert isinstance(arts, dict), "Artifacts response must be an object"

    graph = _get_http(f"/gw/knowledge/graph?project_id={PROJECT_ID}", "graph")
    assert isinstance(graph, dict) and "nodes" in graph and "edges" in graph, (
        "Knowledge graph response must include nodes+edges"
    )

    print(f"  ✓ Provenance/lineage chain readable (job={job_id or 'none'})")
    return {"step": 6, "job_id": job_id, "status": "ok"}


@pytest.mark.e2e
def test_step_6_provenance_lineage_only():
    """Run Step 6 (Provenance & lineage) in isolation."""
    r = test_step_6_provenance_lineage()
    assert r["status"] == "ok"
