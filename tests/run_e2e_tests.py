#!/usr/bin/env python3
"""
E2E Test Runner for R4 Platform

This script provides a unified interface for running E2E tests in different modes:

1. Local mode (default): Uses mock services via Docker Compose
2. HPC mode: Connects to real Slurm infrastructure
3. K8s mode: Connects to real Kubernetes cluster (Kind/minikube)

Usage:
    # Start mock environment and run tests
    python tests/run_e2e_tests.py

    # Run tests against existing environment
    python tests/run_e2e_tests.py --no-setup

    # Run only Slurm tests
    python tests/run_e2e_tests.py --suite slurm

    # Run demo contract validation without Docker setup
    python tests/run_e2e_tests.py --suite demos

    # Run in HPC mode (requires actual Slurm)
    python tests/run_e2e_tests.py --mode hpc

    # Cleanup only
    python tests/run_e2e_tests.py --cleanup-only
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.e2e.yaml"


def run_cmd(
    cmd: list[str], check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  → {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        cwd=PROJECT_ROOT,
    )


def check_docker_available() -> bool:
    """Check if Docker is available."""
    try:
        result = run_cmd(["docker", "info"], check=False, capture=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def check_compose_available() -> bool:
    """Check if Docker Compose is available."""
    try:
        result = run_cmd(["docker", "compose", "version"], check=False, capture=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _container_is_ready(container: str) -> bool:
    result = run_cmd(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
        check=False,
        capture=True,
    )
    status = result.stdout.strip() if result.returncode == 0 else "unknown"
    if status in ("healthy", ""):
        return True
    if status != "unknown" and result.returncode == 0:
        return False
    running_result = run_cmd(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        check=False,
        capture=True,
    )
    return running_result.stdout.strip() == "true"


def _wait_for_healthy_containers(container_names: list[str], max_wait: int = 120) -> bool:
    print("\n  Waiting for services to be healthy...")
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if all(_container_is_ready(container) for container in container_names):
            print("  ✓ All services healthy")
            return True
        time.sleep(5)
        print(f"    Still waiting... ({int(time.time() - start_time)}s)")
    print(f"  ✗ Services not healthy after {max_wait}s")
    show_logs()
    return False


def start_mock_environment() -> bool:
    """Start the mock E2E environment."""
    print("\n📦 Starting mock E2E environment...")

    if not COMPOSE_FILE.exists():
        print(f"  ✗ Compose file not found: {COMPOSE_FILE}")
        return False

    print("\n  Building images...")
    result = run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "build"], check=False)
    if result.returncode != 0:
        print("  ✗ Failed to build images")
        return False

    print("\n  Starting services...")
    result = run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], check=False)
    if result.returncode != 0:
        print("  ✗ Failed to start services")
        return False

    # Container names as defined in docker-compose.e2e.yaml
    return _wait_for_healthy_containers(
        [
            "slurm-mock",
            "compute-mock",
            "e2e-postgres",
            "e2e-minio",
            "e2e-umrs",
            "e2e-gateway-nginx",
        ]
    )


def stop_mock_environment():
    """Stop the mock E2E environment."""
    print("\n🛑 Stopping mock E2E environment...")
    run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"], check=False)


def show_logs():
    """Show logs from all services."""
    print("\n📋 Service logs:")
    run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "logs", "--tail=50"], check=False)


def run_tests(suite: str = "all", verbose: bool = False) -> bool:
    """Run the E2E test suite."""
    print(f"\n🧪 Running E2E tests (suite: {suite})...")

    # Build pytest command
    cmd = [sys.executable, "-m", "pytest"]

    if suite == "slurm":
        cmd.append("tests/test_slurm_e2e.py")
    elif suite == "k8s":
        cmd.append("tests/test_k8s_e2e.py")
    elif suite == "rag":
        cmd.append("tests/test_rag_e2e.py")
    elif suite == "demos":
        cmd.append("tests/test_demo_mounts_e2e.py")
    elif suite == "workflows":
        cmd.append("tests/test_workflow_strict_e2e.py")
    elif suite == "smoke":
        cmd.extend(["-m", "e2e"])
    else:
        # Run all E2E tests
        cmd.extend(["tests/", "-m", "e2e or integration"])

    if verbose:
        cmd.append("-v")

    cmd.extend(["--tb=short"])

    result = run_cmd(cmd, check=False)
    return result.returncode == 0


def run_local_tests() -> bool:
    """Run tests against locally running mock services."""
    print("\n🧪 Running local E2E tests...")

    # Set environment variables for local testing
    env = os.environ.copy()
    env.update(
        {
            "GATEWAY_URL": "http://localhost:8080",
            "UMRS_URL": "http://localhost:8001",
            "LITELLM_URL": "http://localhost:11434",
            "MINIO_URL": "http://localhost:9000",
            "E2E_TEST_MODE": "true",
        }
    )

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_mock_e2e.py",
        "tests/test_workflow_strict_e2e.py",
        "-v",
        "--tb=short",
        "-m",
        "e2e",
    ]

    result = subprocess.run(cmd, env=env, cwd=PROJECT_ROOT)
    return result.returncode == 0


def register_mock_endpoint() -> bool:
    """Register mock endpoint with UMRS for full E2E flow."""
    print("\n📝 Registering mock endpoint with UMRS...")

    script_path = PROJECT_ROOT / "tests" / "docker" / "register_mock_endpoint.py"
    if not script_path.exists():
        print(f"  ⚠ Registration script not found: {script_path}")
        return True  # Non-fatal, tests can still run with 503 fallback

    env = os.environ.copy()
    env.update(
        {
            "UMRS_URL": "http://localhost:8001",
            "LITELLM_HOST": "compute-mock",
            "LITELLM_PORT": "11434",
            "MODEL_NAME": "mock-model",
            "PROJECT_ID": "test-project",
            "JOB_ID": "mock-job-001",
        }
    )

    result = subprocess.run(
        ["python3", str(script_path)],
        env=env,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print("  ✓ Mock endpoint registered")
        return True
    else:
        print(f"  ⚠ Registration returned {result.returncode}")
        if result.stderr:
            print(f"    stderr: {result.stderr[:200]}")
        return True  # Non-fatal


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E2E Test Runner for R4 Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["local", "hpc", "k8s"],
        default="local",
        help="Test mode: local (mock), hpc (real Slurm), k8s (real K8s)",
    )
    parser.add_argument(
        "--suite",
        choices=["all", "slurm", "k8s", "rag", "demos", "smoke", "workflows"],
        default="all",
        help="Test suite to run",
    )
    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="Don't start mock environment (assumes it's already running)",
    )
    parser.add_argument(
        "--no-cleanup", action="store_true", help="Don't stop mock environment after tests"
    )
    parser.add_argument("--cleanup-only", action="store_true", help="Only cleanup, don't run tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    return parser


def _needs_local_setup(args: argparse.Namespace) -> bool:
    return args.mode == "local" and not args.no_setup and args.suite != "demos"


def _check_local_prerequisites() -> int | None:
    if not check_docker_available():
        print("✗ Docker is not available")
        return 1
    if not check_compose_available():
        print("✗ Docker Compose is not available")
        return 1
    return None


def _run_selected_suite(args: argparse.Namespace) -> bool:
    if args.mode == "local" and args.suite == "demos":
        return run_tests(args.suite, args.verbose)
    if args.mode == "local":
        return run_local_tests()
    if args.mode == "hpc":
        print("\n🖥️  Running in HPC mode (requires Slurm access)...")
        return run_tests(args.suite, args.verbose)
    print("\n☸️  Running in K8s mode (requires cluster access)...")
    return run_tests(args.suite, args.verbose)


def _should_cleanup(args: argparse.Namespace) -> bool:
    return (
        args.mode == "local" and not args.no_cleanup and not args.no_setup and args.suite != "demos"
    )


def _run_with_optional_setup(args: argparse.Namespace) -> tuple[bool, int | None]:
    if _needs_local_setup(args):
        if not start_mock_environment():
            print("\n✗ Failed to start mock environment")
            return False, 1
        register_mock_endpoint()
    return _run_selected_suite(args), None


def main():
    args = _build_arg_parser().parse_args()

    print("=" * 60)
    print("R4 Platform - E2E Test Runner")
    print("=" * 60)

    if args.cleanup_only:
        stop_mock_environment()
        return 0

    if _needs_local_setup(args):
        prerequisite_error = _check_local_prerequisites()
        if prerequisite_error is not None:
            return prerequisite_error

    success = True
    early_exit: int | None = None
    try:
        success, early_exit = _run_with_optional_setup(args)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        success = False
    finally:
        if _should_cleanup(args):
            stop_mock_environment()

    if early_exit is not None:
        return early_exit

    return _report_result(success)


def _report_result(success: bool) -> int:
    print("\n" + "=" * 60)
    if success:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
