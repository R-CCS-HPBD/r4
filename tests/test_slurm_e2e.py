#!/usr/bin/env python3
"""
T-002: Slurm Diagnostic End-to-End Integration Test

This file is the lower-level direct Slurm diagnostic harness.
It validates that a Mini-RiVault job can be launched on the cluster via
raw Slurm and Singularity primitives, but it is not the canonical
Gateway -> UMRS platform boundary test.

Diagnostic flow:
1. Check Slurm availability and prerequisites
2. Submit Mini-RiVault job via raw `sbatch`
3. Wait for job to be ready
4. Verify LLM endpoint is accessible
5. Run chat completion
6. Clean up job

Requirements:
- Slurm cluster access (sinfo, sbatch, squeue, scancel)
- Singularity/Apptainer installed
- Pre-staged Mini-RiVault Singularity image
- Pre-staged model weights

Usage:
    # Run with pytest
    pytest tests/test_slurm_e2e.py -v

    # Run standalone
    python tests/test_slurm_e2e.py --model-path /models/qwen-0.5b

    # Skip cleanup for debugging
    python tests/test_slurm_e2e.py --no-cleanup

Environment Variables:
    SLURM_TEST_MODEL_PATH   - Path to model weights
    SLURM_TEST_IMAGE        - Singularity image path
    SLURM_TEST_PARTITION    - Slurm partition name
    SLURM_TEST_TIME         - Job time limit
    SLURM_TEST_GPUS         - Number of GPUs
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Add parent to path for imports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class SlurmTestConfig:
    """Configuration for Slurm E2E tests."""

    # Model configuration
    model_path: str = field(
        default_factory=lambda: os.environ.get("SLURM_TEST_MODEL_PATH", "/models/rivault-gpt-oss")
    )

    # Singularity image
    singularity_image: str = field(
        default_factory=lambda: os.environ.get(
            "SLURM_TEST_IMAGE", "/opt/singularity/mini-rivault.sif"
        )
    )

    # Slurm configuration
    partition: str = field(default_factory=lambda: os.environ.get("SLURM_TEST_PARTITION", "gpu"))
    time_limit: str = field(default_factory=lambda: os.environ.get("SLURM_TEST_TIME", "00:30:00"))
    gpus: int = field(default_factory=lambda: int(os.environ.get("SLURM_TEST_GPUS", "1")))
    cpus: int = field(default_factory=lambda: int(os.environ.get("SLURM_TEST_CPUS", "4")))
    mem: str = field(default_factory=lambda: os.environ.get("SLURM_TEST_MEM", "32G"))

    # Job info directory
    job_info_dir: str = field(
        default_factory=lambda: os.environ.get("SLURM_JOB_INFO_DIR", "/var/run/rivault")
    )

    # Timeouts (seconds)
    submit_timeout: int = 30
    ready_timeout: int = 300  # 5 minutes for job to become ready
    request_timeout: int = 60

    # LiteLLM configuration
    litellm_port: int = field(default_factory=lambda: int(os.environ.get("LITELLM_PORT", "11434")))
    litellm_master_key: str = field(
        default_factory=lambda: os.environ.get("LITELLM_MASTER_KEY", "sk-ll-slurm-test")
    )

    # Test options
    cleanup: bool = True


# =============================================================================
# Prerequisites Check
# =============================================================================


def check_slurm_available() -> tuple[bool, str]:
    """Check if Slurm commands are available."""
    try:
        result = subprocess.run(
            ["sinfo", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            return True, f"Slurm available: {version}"
        return False, f"sinfo failed: {result.stderr}"
    except FileNotFoundError:
        return False, "Slurm commands not found (sinfo not in PATH)"
    except subprocess.TimeoutExpired:
        return False, "Slurm command timed out"
    except Exception as e:
        return False, f"Slurm check failed: {e}"


def check_singularity_available() -> tuple[bool, str]:
    """Check if Singularity/Apptainer is available."""
    for cmd in ["singularity", "apptainer"]:
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return True, f"Container runtime available: {version}"
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return False, "Neither singularity nor apptainer found in PATH"


def _probe(issues: list[str], ok: bool, ok_msg: str, fail_msg: str) -> None:
    """Record one prerequisite probe result."""
    if ok:
        logger.info(ok_msg)
    else:
        issues.append(fail_msg)


def check_prerequisites(config: SlurmTestConfig) -> tuple[bool, list[str]]:
    """Check all prerequisites for Slurm E2E test."""
    issues = []

    slurm_ok, slurm_msg = check_slurm_available()
    _probe(issues, slurm_ok, slurm_msg, f"Slurm: {slurm_msg}")

    sing_ok, sing_msg = check_singularity_available()
    _probe(issues, sing_ok, sing_msg, f"Singularity: {sing_msg}")

    image_ok = Path(config.singularity_image).exists()
    _probe(
        issues,
        image_ok,
        f"Singularity image found: {config.singularity_image}",
        f"Singularity image not found: {config.singularity_image}",
    )

    model_ok = Path(config.model_path).exists()
    _probe(
        issues,
        model_ok,
        f"Model path found: {config.model_path}",
        f"Model path not found: {config.model_path}",
    )

    # Check partition exists
    if slurm_ok:
        try:
            result = subprocess.run(
                ["sinfo", "-p", config.partition, "-h"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip():
                issues.append(f"Slurm partition not found: {config.partition}")
            else:
                logger.info(f"Slurm partition available: {config.partition}")
        except Exception as e:
            issues.append(f"Failed to check partition: {e}")

    # Check job info directory writable
    try:
        job_info_path = Path(config.job_info_dir)
        job_info_path.mkdir(parents=True, exist_ok=True)
        test_file = job_info_path / ".test_write"
        test_file.touch()
        test_file.unlink()
        logger.info(f"Job info directory writable: {config.job_info_dir}")
    except Exception as e:
        issues.append(f"Job info directory not writable: {e}")

    return len(issues) == 0, issues


# =============================================================================
# Slurm Job Management
# =============================================================================


class SlurmJobManager:
    """Manages Slurm job lifecycle for testing."""

    def __init__(self, config: SlurmTestConfig):
        self.config = config
        self.job_id: str | None = None

    def submit_job(self) -> str:
        """Submit Mini-RiVault Slurm job."""
        script = self._generate_job_script()

        logger.info("Submitting Slurm job...")
        result = subprocess.run(
            ["sbatch", "--parsable"],
            input=script,
            capture_output=True,
            text=True,
            timeout=self.config.submit_timeout,
        )

        if result.returncode != 0:
            raise RuntimeError(f"sbatch failed: {result.stderr}")

        self.job_id = result.stdout.strip().split(";")[0]
        logger.info(f"Job submitted: {self.job_id}")
        return self.job_id

    def get_job_state(self) -> str:
        """Get current job state from squeue/sacct."""
        if not self.job_id:
            return "UNKNOWN"

        # Try squeue first (for running jobs)
        result = subprocess.run(
            ["squeue", "-j", self.job_id, "-h", "-o", "%T"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        # Fall back to sacct (for completed jobs)
        result = subprocess.run(
            ["sacct", "-j", self.job_id, "-n", "-o", "State", "-P"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Return first non-empty state
            states = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
            return states[0] if states else "UNKNOWN"

        return "UNKNOWN"

    def get_job_info(self) -> dict:
        """Read job info file written by the job."""
        if not self.job_id:
            return {}

        info_file = Path(self.config.job_info_dir) / f"rivault-job-{self.job_id}.info"
        if not info_file.exists():
            return {}

        info = {}
        with open(info_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    key, value = line.split("=", 1)
                    info[key] = value
        return info

    def wait_for_ready(self) -> dict:
        """Wait for job to be ready, return job info."""
        logger.info(f"Waiting for job {self.job_id} to be ready...")
        start = time.time()

        while time.time() - start < self.config.ready_timeout:
            state = self.get_job_state()
            info = self.get_job_info()
            rivault_status = info.get("RIVAULT_STATUS", "unknown")

            logger.info(f"  Job state: {state}, RiVault status: {rivault_status}")

            if rivault_status == "ready":
                logger.info("Job is ready!")
                return info

            if rivault_status == "failed" or state in ("FAILED", "CANCELLED", "TIMEOUT"):
                raise RuntimeError(f"Job failed: state={state}, status={rivault_status}")

            time.sleep(10)

        raise TimeoutError(f"Job did not become ready within {self.config.ready_timeout}s")

    def cancel_job(self) -> None:
        """Cancel the job."""
        if not self.job_id:
            return

        logger.info(f"Cancelling job {self.job_id}...")
        try:
            subprocess.run(
                ["scancel", self.job_id],
                capture_output=True,
                timeout=10,
            )
            logger.info("Job cancelled")
        except Exception as e:
            logger.warning(f"Failed to cancel job: {e}")

    def _generate_job_script(self) -> str:
        """Generate sbatch script."""
        cfg = self.config
        return f"""#!/bin/bash
#SBATCH --job-name=rivault-test
#SBATCH --partition={cfg.partition}
#SBATCH --time={cfg.time_limit}
#SBATCH --gres=gpu:{cfg.gpus}
#SBATCH --cpus-per-task={cfg.cpus}
#SBATCH --mem={cfg.mem}
#SBATCH --output={cfg.job_info_dir}/job-%j.out
#SBATCH --error={cfg.job_info_dir}/job-%j.err

set -e

VLLM_MODEL_PATH="{cfg.model_path}"
VLLM_PORT=8000
LITELLM_PORT={cfg.litellm_port}
LITELLM_MASTER_KEY="{cfg.litellm_master_key}"
SINGULARITY_IMAGE="{cfg.singularity_image}"
JOB_INFO_DIR="{cfg.job_info_dir}"
TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1
VLLM_NO_USAGE_STATS=1

echo "============================================="
echo "Mini-RiVault Slurm Test Job"
echo "============================================="
echo "Job ID:      $SLURM_JOB_ID"
echo "Node:        $(hostname)"
echo "Model:       $VLLM_MODEL_PATH"
echo "============================================="

mkdir -p "$JOB_INFO_DIR"

# Write job info
JOB_INFO_FILE="$JOB_INFO_DIR/rivault-job-${{SLURM_JOB_ID}}.info"
cat > "$JOB_INFO_FILE" << EOF
RIVAULT_JOB_ID=${{SLURM_JOB_ID}}
RIVAULT_NODE=$(hostname)
RIVAULT_LITELLM_PORT=${{LITELLM_PORT}}
RIVAULT_VLLM_PORT=${{VLLM_PORT}}
RIVAULT_MODEL_PATH=${{VLLM_MODEL_PATH}}
RIVAULT_TRANSFORMERS_OFFLINE=${{TRANSFORMERS_OFFLINE}}
RIVAULT_HF_HUB_OFFLINE=${{HF_HUB_OFFLINE}}
RIVAULT_VLLM_NO_USAGE_STATS=${{VLLM_NO_USAGE_STATS}}
RIVAULT_START_TIME=$(date -Iseconds)
RIVAULT_STATUS=starting
EOF

cleanup() {{
    echo "Cleaning up..."
    sed -i 's/RIVAULT_STATUS=.*/RIVAULT_STATUS=stopped/' "$JOB_INFO_FILE" 2>/dev/null || true
    singularity instance stop rivault-${{SLURM_JOB_ID}} 2>/dev/null || true
}}
trap cleanup EXIT INT TERM

# Validate
if [[ ! -f "$SINGULARITY_IMAGE" ]]; then
    echo "Error: Singularity image not found: $SINGULARITY_IMAGE"
    sed -i 's/RIVAULT_STATUS=.*/RIVAULT_STATUS=failed/' "$JOB_INFO_FILE"
    exit 1
fi

if [[ ! -d "$VLLM_MODEL_PATH" ]]; then
    echo "Error: Model path not found: $VLLM_MODEL_PATH"
    sed -i 's/RIVAULT_STATUS=.*/RIVAULT_STATUS=failed/' "$JOB_INFO_FILE"
    exit 1
fi

# Load modules
module load cuda/12.1 2>/dev/null || true
module load singularity 2>/dev/null || true

# Start container
echo "Starting Mini-RiVault..."
singularity run \\
    --nv \\
    --bind "${{VLLM_MODEL_PATH}}:/models:ro" \\
    --env VLLM_MODEL_PATH=/models \\
    --env VLLM_PORT=${{VLLM_PORT}} \\
    --env LITELLM_PORT=${{LITELLM_PORT}} \\
    --env LITELLM_MASTER_KEY=${{LITELLM_MASTER_KEY}} \\
    --env TRANSFORMERS_OFFLINE=${{TRANSFORMERS_OFFLINE}} \
    --env HF_HUB_OFFLINE=${{HF_HUB_OFFLINE}} \
    --env VLLM_NO_USAGE_STATS=${{VLLM_NO_USAGE_STATS}} \
    "$SINGULARITY_IMAGE" &

CONTAINER_PID=$!

# Wait for readiness
echo "Waiting for service to be ready..."
READY=false
for i in $(seq 1 60); do
    if curl -sf -H "Authorization: Bearer ${{LITELLM_MASTER_KEY}}" \\
       "http://localhost:${{LITELLM_PORT}}/v1/models" > /dev/null 2>&1; then
        READY=true
        break
    fi
    sleep 5
done

if $READY; then
    echo "Mini-RiVault is ready!"
    sed -i 's/RIVAULT_STATUS=.*/RIVAULT_STATUS=ready/' "$JOB_INFO_FILE"
    echo "RIVAULT_READY_TIME=$(date -Iseconds)" >> "$JOB_INFO_FILE"
    wait $CONTAINER_PID
else
    echo "Error: Failed to start within timeout"
    sed -i 's/RIVAULT_STATUS=.*/RIVAULT_STATUS=failed/' "$JOB_INFO_FILE"
    exit 1
fi
"""


# =============================================================================
# LLM Client for Testing
# =============================================================================


class LLMTestClient:
    """Simple LLM client for testing the deployed endpoint."""

    def __init__(self, host: str, port: int, api_key: str):
        self.base_url = f"http://{host}:{port}"
        self.api_key = api_key

    def list_models(self, timeout: int = 30) -> list[str]:
        """List available models."""
        import json
        import urllib.request

        url = f"{self.base_url}/v1/models"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return [m["id"] for m in data.get("data", [])]

    def chat_completion(
        self,
        messages: list[dict],
        model: str = "rivault-gpt-oss-static",
        timeout: int = 60,
    ) -> str:
        """Send chat completion request."""
        import json
        import urllib.request

        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 100,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]


# =============================================================================
# Test Runner
# =============================================================================


class SlurmE2ETest:
    """End-to-end test for Slurm-based Mini-RiVault deployment."""

    def __init__(self, config: SlurmTestConfig):
        self.config = config
        self.job_manager = SlurmJobManager(config)
        self.results: dict[str, bool] = {}

    def run_all_tests(self) -> bool:
        """Run all E2E tests."""
        try:
            # Step 1: Prerequisites
            self._test_prerequisites()

            # Step 2: Submit job
            self._test_submit_job()

            # Step 3: Wait for ready
            job_info = self._test_wait_for_ready()

            # Step 4: Verify offline inference env
            self._test_verify_offline_env(job_info)

            # Step 5: Verify endpoint
            self._test_verify_endpoint(job_info)

            # Step 6: Chat completion
            self._test_chat_completion(job_info)

            logger.info("=" * 60)
            logger.info("All Slurm E2E tests passed!")
            logger.info("=" * 60)
            return True

        except Exception as e:
            logger.error(f"Test failed: {e}")
            return False

        finally:
            if self.config.cleanup:
                self._cleanup()

    def _test_prerequisites(self) -> None:
        """Test 1: Check prerequisites."""
        logger.info("=" * 60)
        logger.info("Step 1: Checking prerequisites")
        logger.info("=" * 60)

        ok, issues = check_prerequisites(self.config)
        if not ok:
            for issue in issues:
                logger.error(f"  - {issue}")
            raise RuntimeError("Prerequisites not met")

        self.results["prerequisites"] = True
        logger.info("Prerequisites check passed")

    def _test_submit_job(self) -> str:
        """Test 2: Submit Slurm job."""
        logger.info("=" * 60)
        logger.info("Step 2: Submitting Slurm job")
        logger.info("=" * 60)

        job_id = self.job_manager.submit_job()
        self.results["submit_job"] = True
        logger.info(f"Job submitted successfully: {job_id}")
        return job_id

    def _test_wait_for_ready(self) -> dict:
        """Test 3: Wait for job to be ready."""
        logger.info("=" * 60)
        logger.info("Step 3: Waiting for job to be ready")
        logger.info("=" * 60)

        job_info = self.job_manager.wait_for_ready()
        self.results["wait_for_ready"] = True
        logger.info(f"Job ready on node: {job_info.get('RIVAULT_NODE')}")
        return job_info

    def _test_verify_endpoint(self, job_info: dict) -> None:
        """Test 5: Verify LLM endpoint is accessible."""
        logger.info("=" * 60)
        logger.info("Step 5: Verifying LLM endpoint")
        logger.info("=" * 60)

        host = job_info.get("RIVAULT_NODE", "localhost")
        port = int(job_info.get("RIVAULT_LITELLM_PORT", self.config.litellm_port))

        client = LLMTestClient(host, port, self.config.litellm_master_key)

        models = client.list_models(timeout=self.config.request_timeout)
        logger.info(f"Available models: {models}")

        if not models:
            raise RuntimeError("No models available on endpoint")

        self.results["verify_endpoint"] = True
        logger.info("Endpoint verification passed")

    def _test_verify_offline_env(self, job_info: dict) -> None:
        """Test 4: Verify inference is configured for offline execution."""
        logger.info("=" * 60)
        logger.info("Step 4: Verifying offline inference environment")
        logger.info("=" * 60)

        expected_flags = {
            "RIVAULT_TRANSFORMERS_OFFLINE": "1",
            "RIVAULT_HF_HUB_OFFLINE": "1",
            "RIVAULT_VLLM_NO_USAGE_STATS": "1",
        }
        actual_flags = {key: job_info.get(key) for key in expected_flags}

        for key, expected_value in expected_flags.items():
            if actual_flags[key] != expected_value:
                raise RuntimeError(
                    f"Offline inference invariant failed: {key}={actual_flags[key]!r}, expected {expected_value!r}"
                )

        self.results["verify_offline_env"] = True
        logger.info("Offline inference environment verified: %s", actual_flags)

    def _test_chat_completion(self, job_info: dict) -> None:
        """Test 6: Run chat completion."""
        logger.info("=" * 60)
        logger.info("Step 6: Testing chat completion")
        logger.info("=" * 60)

        host = job_info.get("RIVAULT_NODE", "localhost")
        port = int(job_info.get("RIVAULT_LITELLM_PORT", self.config.litellm_port))

        client = LLMTestClient(host, port, self.config.litellm_master_key)

        messages = [{"role": "user", "content": "Say 'Slurm test passed' in exactly 3 words."}]

        response = client.chat_completion(
            messages=messages,
            timeout=self.config.request_timeout,
        )

        logger.info(f"LLM response: {response}")

        if not response:
            raise RuntimeError("Empty response from LLM")

        self.results["chat_completion"] = True
        logger.info("Chat completion test passed")

    def _cleanup(self) -> None:
        """Clean up: Cancel job."""
        logger.info("=" * 60)
        logger.info("Cleanup: Cancelling job")
        logger.info("=" * 60)

        self.job_manager.cancel_job()


# =============================================================================
# Pytest Integration
# =============================================================================

try:
    import pytest

    @pytest.fixture
    def slurm_config():
        """Pytest fixture for Slurm test config."""
        return SlurmTestConfig()

    @pytest.fixture
    def slurm_available():
        """Check if Slurm is available."""
        ok, msg = check_slurm_available()
        if not ok:
            pytest.skip(f"Slurm not available: {msg}")
        return True

    @pytest.fixture
    def singularity_available():
        """Check if Singularity is available."""
        ok, msg = check_singularity_available()
        if not ok:
            pytest.skip(f"Singularity not available: {msg}")
        return True

    class TestSlurmE2E:
        """Pytest test class for Slurm E2E tests."""

        def test_generated_job_script_enforces_offline_inference(self, slurm_config):
            """The diagnostic Slurm script should keep inference offline."""
            script = SlurmJobManager(slurm_config)._generate_job_script()

            markers = (
                "TRANSFORMERS_OFFLINE=1",
                "HF_HUB_OFFLINE=1",
                "VLLM_NO_USAGE_STATS=1",
                "RIVAULT_TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE}",
                "RIVAULT_HF_HUB_OFFLINE=${HF_HUB_OFFLINE}",
                "RIVAULT_VLLM_NO_USAGE_STATS=${VLLM_NO_USAGE_STATS}",
                "--env TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE}",
                "--env HF_HUB_OFFLINE=${HF_HUB_OFFLINE}",
                "--env VLLM_NO_USAGE_STATS=${VLLM_NO_USAGE_STATS}",
            )
            missing = [m for m in markers if m not in script]
            assert not missing, f"offline markers missing from script: {missing}"

        @pytest.mark.slow
        @pytest.mark.hpc
        def test_full_e2e(self, slurm_config, slurm_available, singularity_available):
            """Full E2E test - submit job, wait, verify, chat."""
            ok, issues = check_prerequisites(slurm_config)
            if not ok:
                pytest.skip(f"Prerequisites not met: {issues}")

            test = SlurmE2ETest(slurm_config)
            assert test.run_all_tests()

        @pytest.mark.hpc
        def test_prerequisites_only(self, slurm_config, slurm_available):
            """Test only prerequisites check."""
            ok, issues = check_prerequisites(slurm_config)
            # Just log, don't fail - we want to know what's missing
            if not ok:
                for issue in issues:
                    logger.warning(f"Prerequisite issue: {issue}")
            # This test always passes - it's informational
            assert True

except ImportError:
    pass  # pytest not installed


# =============================================================================
# CLI Entry Point
# =============================================================================


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Slurm E2E test for Mini-RiVault",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--model-path",
        default=os.environ.get("SLURM_TEST_MODEL_PATH", "/models/rivault-gpt-oss"),
        help="Path to model weights",
    )
    parser.add_argument(
        "--image",
        default=os.environ.get("SLURM_TEST_IMAGE", "/opt/singularity/mini-rivault.sif"),
        help="Singularity image path",
    )
    parser.add_argument(
        "--partition",
        default=os.environ.get("SLURM_TEST_PARTITION", "gpu"),
        help="Slurm partition",
    )
    parser.add_argument(
        "--time",
        default="00:30:00",
        help="Job time limit",
    )
    parser.add_argument(
        "--gpus",
        type=int,
        default=1,
        help="Number of GPUs",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't cancel job after test (for debugging)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check prerequisites, don't run tests",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = SlurmTestConfig(
        model_path=args.model_path,
        singularity_image=args.image,
        partition=args.partition,
        time_limit=args.time,
        gpus=args.gpus,
        cleanup=not args.no_cleanup,
    )

    if args.check_only:
        logger.info("Checking prerequisites only...")
        ok, issues = check_prerequisites(config)
        if ok:
            logger.info("All prerequisites met!")
            return 0
        else:
            for issue in issues:
                logger.error(f"  - {issue}")
            return 1

    test = SlurmE2ETest(config)
    success = test.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
