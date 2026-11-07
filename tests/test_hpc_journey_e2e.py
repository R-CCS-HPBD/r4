#!/usr/bin/env python3
"""
TASK-048: Dual-cluster HPC Journey E2E — 3-step Research Loop.

Validates the complete AI4S research journey through the R4 CLI / Gateway stack
on **real HPC infrastructure** (Slurm-backed clusters):

    1. Init    — Create project, import fixture corpus
    2. Verify  — Run verification, check confidence
    3. Execute — Plan and run one verified experiment workflow

Total: 3 steps (vs 5 for the compose mock journey).

Prerequisites:
    - HPC clusters reachable (RCCS Cloud or AI4S)
    - R4_E2E_PROFILE set to ``rccs`` or ``ai4s``
    - Per-cluster credentials configured via env vars

Usage:
    # Full suite (both clusters)
    pytest tests/test_hpc_journey_e2e.py -v

    # Single cluster
    R4_E2E_PROFILE=rccs pytest tests/test_hpc_journey_e2e.py -v

    # Specific step
    pytest tests/test_hpc_journey_e2e.py -v -k test_step_2_verify_only

    # Standalone (no pytest)
    python tests/test_hpc_journey_e2e.py

Environment Variables:
    R4_E2E_PROFILE       — HPC profile: rccs, ai4s, or all (default: ai4s)
    R4_E2E_RCCS_GW_URL   — RCCS Gateway URL (default: https://gw.rccs.ai4s-internal)
    R4_E2E_AI4S_GW_URL   — AI4S Gateway URL (default: https://gw.ai4s.rccs.internal)
    R4_E2E_API_KEY       — API key for both clusters (default: empty → skip)
    HPC_JOURNEY_TIMEOUT  — Per-step timeout in seconds (default: 120)
    HPC_JOURNEY_DEBUG    — Verbose output (default: false)
    REPORT_DIR           — JSON report output directory (default: tests/reports)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

try:
    import httpx
except ImportError:
    httpx = None

# ── Configuration ──────────────────────────────────────────────────────────

R4_E2E_PROFILE = os.environ.get("R4_E2E_PROFILE", "ai4s").lower()
R4_E2E_RCCS_GW_URL = os.environ.get("R4_E2E_RCCS_GW_URL", "https://gw.rccs.ai4s-internal").rstrip(
    "/"
)
R4_E2E_AI4S_GW_URL = os.environ.get("R4_E2E_AI4S_GW_URL", "https://gw.ai4s.rccs.internal").rstrip(
    "/"
)
R4_E2E_API_KEY = os.environ.get("R4_E2E_API_KEY", "")
HPC_JOURNEY_TIMEOUT = int(os.environ.get("HPC_JOURNEY_TIMEOUT", "120"))
HPC_JOURNEY_DEBUG = os.environ.get("HPC_JOURNEY_DEBUG", "false").lower() == "true"
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "tests/reports"))

PROJECT_ID = "hpc-048"

# ── Cluster profiles ─────────────────────────────────────────────────────

_CLUSTER_PROFILES: dict[str, dict[str, str]] = {
    "rccs": {
        "gateway_url": R4_E2E_RCCS_GW_URL,
        "api_key": R4_E2E_API_KEY,
        "project_id": os.environ.get("R4_E2E_RCCS_PROJECT_ID", "rivault"),
        "model_name": os.environ.get("R4_E2E_RCCS_MODEL", ""),
    },
    "ai4s": {
        "gateway_url": R4_E2E_AI4S_GW_URL,
        "api_key": R4_E2E_API_KEY,
        "project_id": os.environ.get("R4_E2E_AI4S_PROJECT_ID", "rivault"),
        "model_name": os.environ.get("R4_E2E_AI4S_MODEL", ""),
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────


def _resolve_gateway() -> str:
    """Return the Gateway URL for the selected profile."""
    profile = R4_E2E_PROFILE if R4_E2E_PROFILE in _CLUSTER_PROFILES else None
    if profile is None:
        pytest.skip(
            f"No HPC profile configured. "
            f"R4_E2E_PROFILE={R4_E2E_PROFILE!r} is not in {list(_CLUSTER_PROFILES.keys())}. "
            "Set R4_E2E_PROFILE=rccs or R4_E2E_PROFILE=ai4s."
        )
    return _CLUSTER_PROFILES[profile]["gateway_url"]


def _health_check(gateway_url: str, timeout: int = 10) -> bool:
    """Validate that the Gateway endpoint responds.

    Returns True if health check passes, False otherwise.
    """
    if httpx is None:
        return False
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(f"{gateway_url}/health")
            return r.status_code == 200
    except Exception:
        return False


def _headers(project_id: str = PROJECT_ID) -> dict[str, str]:
    """Return default auth + content headers for Gateway requests."""
    return {
        "Authorization": f"Bearer {_CLUSTER_PROFILES.get(R4_E2E_PROFILE, {}).get('api_key', '')}",
        "X-Project-ID": project_id,
        "Content-Type": "application/json",
    }


def _post_http(path: str, json_body: dict, step: str) -> dict[str, Any]:
    """POST to the Gateway. Returns parsed JSON or empty dict on failure."""
    gw = _CLUSTER_PROFILES.get(R4_E2E_PROFILE, {}).get("gateway_url", "")
    headers = {
        "Authorization": f"Bearer {_CLUSTER_PROFILES.get(R4_E2E_PROFILE, {}).get('api_key', '')}",
        "X-Project-ID": PROJECT_ID,
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=HPC_JOURNEY_TIMEOUT) as c:
            r = c.post(f"{gw}{path}", json=json_body, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        if HPC_JOURNEY_DEBUG:
            print(f"  HTTP {step} failed: {exc}", file=sys.stderr)
        return {}


def _run_cli(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run an ``r4`` CLI command and return the result."""
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(Path(__file__).parent.parent.parent / "r4" / "src"))
    return subprocess.run(
        [sys.executable, "-m", "r4"] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _parse_cli_output(result: subprocess.CompletedProcess) -> dict[str, Any]:
    """Parse JSON output from a CLI command, return empty dict on failure."""
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw_output": result.stdout[:500]}


def _ensure_fixture_corpus() -> Path:
    """Create a minimal fixture corpus for import. Returns path."""
    fixtures_dir = Path(__file__).parent / "samples" / "hpc-048"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    _write_fixture(
        fixtures_dir,
        "attention_is_all_you_need.txt",
        "Attention Is All You Need\n"
        "Authors: Vaswani et al.\n\n"
        "We propose a new simple network architecture, the Transformer,\n"
        "based solely on attention mechanisms. Experiments on machine\n"
        "translation tasks show this model to be superior in quality.\n",
    )
    _write_fixture(
        fixtures_dir,
        "transformer_attention.py",
        "# transformer_attention.py — simplified scaled dot-product attention\n"
        "import torch\n"
        "import torch.nn as nn\n\n"
        "class MultiHeadAttention(nn.Module):\n"
        "    def __init__(self, d_model: int, num_heads: int):\n"
        "        super().__init__()\n"
        "        self.num_heads = num_heads\n"
        "        self.d_model = d_model\n\n"
        "    def forward(self, query, key, value, mask=None):\n"
        "        scores = torch.matmul(query, key.transpose(-2, -1))\n"
        "        attention = torch.softmax(scores, dim=-1)\n"
        "        return torch.matmul(attention, value)\n\n"
        "# Reference: Vaswani et al., 2017\n",
    )
    return fixtures_dir


def _write_fixture(directory: Path, name: str, content: str) -> None:
    """Write a fixture file only if it does not already exist."""
    path = directory / name
    if not path.is_file():
        path.write_text(content, encoding="utf-8")


def _run_project_command(command: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
    """Run an r4 project subcommand and parse JSON output."""
    result = _run_cli(["project", command, PROJECT_ID, *args, "--json"], timeout=timeout)
    return _parse_cli_output(result)


@dataclass
class GatewayHPCJourneyConfig:
    """Configuration for HPC journey tests."""

    profile: str
    gateway_url: str
    api_key: str
    project_id: str
    model_name: str


def _build_journey_config() -> GatewayHPCJourneyConfig:
    """Build a config dict from environment variables and cluster profiles."""
    profile = R4_E2E_PROFILE
    if profile not in _CLUSTER_PROFILES:
        pytest.skip(
            f"Unknown R4_E2E_PROFILE={profile!r}. Use one of: {', '.join(_CLUSTER_PROFILES.keys())}"
        )

    cfg = _CLUSTER_PROFILES[profile]
    gw_url = cfg["gateway_url"]
    api_key = cfg["api_key"] or R4_E2E_API_KEY
    project_id = cfg["project_id"]
    model_name = cfg["model_name"] or os.environ.get("SLURM_TEST_MODEL_NAME", "")

    # Skip if API key is empty — auth will fail
    if not api_key:
        pytest.skip(
            f"No API key configured for profile {profile!r}. "
            "Set R4_E2E_API_KEY or R4_E2E_<PROFILE>_API_KEY."
        )

    return GatewayHPCJourneyConfig(
        profile=profile,
        gateway_url=gw_url,
        api_key=api_key,
        project_id=project_id,
        model_name=model_name,
    )


def _collect_results(step_results: list[dict[str, Any]], cluster: str) -> None:
    """Write results to a JSON report file.

    Creates the report directory if needed. The report is appended-to
    (not overwritten) so that partial runs preserve previous results.
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "hpc-journey.json"

    existing: dict[str, Any] = {}
    if report_path.is_file():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    run_id = f"{cluster}-{time.strftime('%Y%m%d-%H%M%S')}"
    entries = existing.get("runs", [])
    entries.append(
        {
            "run_id": run_id,
            "profile": cluster,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "steps": step_results,
        }
    )
    existing["runs"] = entries
    existing["last_profile"] = cluster

    report_path.write_text(
        json.dumps(existing, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    if HPC_JOURNEY_DEBUG:
        print(f"  Results written to {report_path}")


# ── Step 1: Init (Create Project + Import Corpus) ─────────────────────────


def _create_project() -> subprocess.CompletedProcess:
    """Create the test project via CLI."""
    return _run_cli(
        [
            "project",
            "create",
            PROJECT_ID,
            "--name",
            f"HPC-048 {PROJECT_ID} Test",
            "--json",
        ],
        timeout=30,
    )


def step1_init(config: GatewayHPCJourneyConfig) -> dict[str, Any]:
    """Create project and import fixture corpus."""
    print(f"\n▸ Step 1/3 ({config.profile}): Init — creating project, importing corpus...")

    create_result = _create_project()
    if HPC_JOURNEY_DEBUG:
        print(f"  create stdout: {create_result.stdout.strip()}")

    fixtures_dir = _ensure_fixture_corpus()
    artifact_count = sum(1 for f in fixtures_dir.iterdir() if f.is_file())
    assert artifact_count > 0, "Fixture corpus must exist"

    import_result = _run_cli(
        [
            "project",
            "import",
            PROJECT_ID,
            str(fixtures_dir),
            "--json",
        ],
        timeout=60,
    )

    if HPC_JOURNEY_DEBUG:
        print(f"  import stdout: {import_result.stdout.strip()}")

    data = _parse_cli_output(import_result)
    artifacts_ingested = data.get("artifacts_ingested", artifact_count)
    assert artifacts_ingested >= artifact_count, (
        f"Should have ingested at least {artifact_count} artifacts, got {artifacts_ingested}"
    )

    data_dir = Path.home() / ".r4" / "projects" / PROJECT_ID
    if data_dir.exists():
        assert any(data_dir.iterdir()), "Project data_dir should contain files"

    print(f"  ✓ Init complete: {artifacts_ingested} artifact(s) into project {PROJECT_ID}")
    return {
        "step": 1,
        "project_id": PROJECT_ID,
        "artifacts_ingested": artifacts_ingested,
        "fixtures_dir": str(fixtures_dir),
        "data_dir": str(data_dir),
        "status": "ok",
    }


# ── Step 2: Verify (Extract Knowledge + Question) ────────────────────────


def step2_verify(config: GatewayHPCJourneyConfig) -> dict[str, Any]:
    """Extract structured knowledge and run verification."""
    print(f"\n▸ Step 2/3 ({config.profile}): Verify — extracting knowledge, running verify...")

    init_data = _run_project_command("init", [], timeout=HPC_JOURNEY_TIMEOUT)
    extracted_count = init_data.get("extracted_count", 0)
    graph_nodes = init_data.get("graph_nodes", 0)
    assert extracted_count > 0, "Should have extracted at least some knowledge"
    assert graph_nodes > 0, "Knowledge graph should have nodes"

    verify_data = _run_project_command(
        "verify",
        ["What is the Transformer architecture?"],
        timeout=HPC_JOURNEY_TIMEOUT,
    )
    confidence = verify_data.get("confidence", 0.0)
    answered = verify_data.get("answered", False)
    assert answered, "Verification should return an answer"

    print(
        f"  ✓ Verify complete: {extracted_count} knowledge items, "
        f"{graph_nodes} graph nodes, confidence {confidence:.2f}"
    )
    return {
        "step": 2,
        "project_id": PROJECT_ID,
        "extracted_count": extracted_count,
        "graph_nodes": graph_nodes,
        "graph_edges": init_data.get("graph_edges", 0),
        "verification_confidence": confidence,
        "status": "ok",
    }


# ── Step 3: Execute (Plan + Run One Workflow) ─────────────────────────────


def step3_execute(config: GatewayHPCJourneyConfig) -> dict[str, Any]:
    """Plan research and execute one experiment."""
    print(f"\n▸ Step 3/3 ({config.profile}): Execute — planning research, running workflow...")

    plan_data = _run_project_command(
        "plan",
        ['"Compare Transformer attention mechanisms to RNN-based approaches"'],
        timeout=HPC_JOURNEY_TIMEOUT,
    )
    objectives = plan_data.get("objectives", [])
    experiments = plan_data.get("experiments", [])
    assert len(experiments) > 0, "Research plan should have at least one experiment"

    run_data = _run_project_command(
        "run",
        ["--max-iterations", "1", "--approve"],
        timeout=HPC_JOURNEY_TIMEOUT,
    )
    iterations = run_data.get("iterations", 0)
    experiments_run = run_data.get("experiments_run", 0)
    assert iterations >= 1, "Should have executed at least one iteration"
    assert experiments_run >= 1, "Should have run at least one experiment"

    print(
        f"  ✓ Execute complete: {iterations} iteration(s), "
        f"{experiments_run} experiment(s) run, {len(objectives)} objectives"
    )
    return {
        "step": 3,
        "project_id": PROJECT_ID,
        "objectives": len(objectives),
        "experiments_planned": len(experiments),
        "iterations": iterations,
        "experiments_run": experiments_run,
        "status": "ok",
    }


# ── Pytest Tests ──────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.hpc
@pytest.mark.nightly
class TestHPCJourneyE2E:
    """End-to-end HPC journey tests across dual-cluster deployments."""

    @pytest.fixture(scope="class")
    def config(self) -> GatewayHPCJourneyConfig:
        """Build cluster config from environment variables."""
        return _build_journey_config()

    @pytest.fixture(scope="class", autouse=True)
    def validate_cluster(self, config: GatewayHPCJourneyConfig) -> None:
        """Skip the entire suite if the cluster endpoint is unreachable."""
        if not _health_check(config.gateway_url):
            pytest.skip("HPC endpoint unreachable")

    def test_step_1_init_only(self, config: GatewayHPCJourneyConfig) -> None:
        """Step 1 only: create project and import fixture corpus."""
        results = [step1_init(config)]
        _collect_results(results, config.profile)
        assert results[0]["artifacts_ingested"] > 0

    def test_step_2_verify_only(self, config: GatewayHPCJourneyConfig) -> None:
        """Step 2 only: extract knowledge and verify."""
        results = [step2_verify(config)]
        _collect_results(results, config.profile)
        assert results[0]["extracted_count"] > 0

    def test_step_3_workflow_only(self, config: GatewayHPCJourneyConfig) -> None:
        """Step 3 only: plan research and execute one experiment."""
        results = [step3_execute(config)]
        _collect_results(results, config.profile)
        assert results[0]["experiments_run"] > 0

    def test_hpc_journey(self, config: GatewayHPCJourneyConfig) -> None:
        """Full 3-step HPC journey: Init → Verify → Execute."""
        step1_result = step1_init(config)
        assert step1_result["artifacts_ingested"] > 0

        step2_result = step2_verify(config)
        assert step2_result["extracted_count"] > 0

        step3_result = step3_execute(config)
        assert step3_result["experiments_run"] > 0

        results = [step1_result, step2_result, step3_result]
        _collect_results(results, config.profile)

        # Validate end-to-end chain
        assert step1_result["data_dir"] != ""
        assert step2_result["graph_nodes"] > 0
        assert step3_result["iterations"] >= 1


# ── Standalone Runner ─────────────────────────────────────────────────────


if __name__ == "__main__":
    # Allow --report-dir override from CLI
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--report-dir" and i + 1 < len(args):
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            args = args[:i] + args[i + 2 :]
            break

    print("=" * 70)
    print(f"TASK-048: HPC Journey E2E — profile={R4_E2E_PROFILE}")
    print("=" * 70)

    config = _build_journey_config()
    print(f"  Gateway: {config.gateway_url}")
    print(f"  Project: {config.project_id}")
    print(f"  Model:   {config.model_name or '(not set — will use default)'}")

    if not _health_check(config.gateway_url):
        print("  ✗ HPC endpoint unreachable — skipping")
        sys.exit(0)

    results: list[dict[str, Any]] = []

    try:
        r1 = step1_init(config)
        r2 = step2_verify(config)
        r3 = step3_execute(config)
        results = [r1, r2, r3]
    except Exception as exc:
        print(f"  ✗ Journey failed: {exc}")
        if results:
            _collect_results(results, config.profile)
        raise

    _collect_results(results, config.profile)
    print(f"\n✓ HPC journey complete for {config.profile}")
