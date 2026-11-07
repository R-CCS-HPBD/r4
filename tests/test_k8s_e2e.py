#!/usr/bin/env python3
"""
T-003: Kubernetes End-to-End Integration Test for UMRS + Mini-RiVault

Tests the full Kubernetes-based deployment flow using Kind or minikube:
1. Check cluster availability and prerequisites
2. Deploy UMRS stack via kustomize
3. Wait for all pods to be ready
4. Verify service endpoints
5. Run API tests (health, RAG endpoints)
6. Clean up resources

Requirements:
- Kind or minikube installed and running
- kubectl configured for the cluster
- Docker for building images
- kustomize (or kubectl with -k)

Usage:
    # Run with pytest
    pytest tests/test_k8s_e2e.py -v

    # Run standalone
    python tests/test_k8s_e2e.py

    # Check prerequisites only
    python tests/test_k8s_e2e.py --check-only

    # Skip cleanup for debugging
    python tests/test_k8s_e2e.py --no-cleanup

Environment Variables:
    K8S_NAMESPACE        - Kubernetes namespace (default: umrs-system)
    K8S_CONTEXT          - Kubernetes context to use
    K8S_TIMEOUT          - Wait timeout in seconds (default: 300)
    REGISTRY_HOST        - Local registry host (default: localhost:5001)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen

try:
    pytest = __import__("pytest")
except ImportError:
    pytest = None

# Infrastructure guard (2026-09-14). This module is a heavyweight E2E: it
# builds images, applies manifests and waits on rollouts against a real
# cluster. Reachability is deliberately NOT the gate — a developer box with a
# stray kind/k3s cluster would silently spend the whole job timeout inside the
# repo-root belt (measured: still running after 90s on a host where
# `kubectl cluster-info` answers). Opt-in only, matching how the compose E2E
# suites are label-gated in CI rather than run by default.
if pytest is not None and os.environ.get("R4_E2E_K8S", "") not in ("1", "true", "yes"):
    pytest.skip(
        "heavyweight Kubernetes E2E: set R4_E2E_K8S=1 to run it against a "
        "prepared cluster (needs kubectl, kustomize and a writable registry)",
        allow_module_level=True,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Project root paths
PROJECT_ROOT = Path(__file__).parent.parent
K8S_MANIFESTS = PROJECT_ROOT / "umrs" / "docker" / "k8s"
DOCKER_DIR = PROJECT_ROOT / "umrs" / "docker"


@dataclass
class K8sTestConfig:
    """Configuration for K8s E2E tests."""

    namespace: str = field(
        default_factory=lambda: subprocess.getoutput("echo ${K8S_NAMESPACE:-umrs-system}").strip()
    )
    context: str | None = field(
        default_factory=lambda: subprocess.getoutput("echo ${K8S_CONTEXT:-}").strip() or None
    )
    timeout: int = field(
        default_factory=lambda: int(subprocess.getoutput("echo ${K8S_TIMEOUT:-300}").strip())
    )
    registry_host: str = field(
        default_factory=lambda: subprocess.getoutput(
            "echo ${REGISTRY_HOST:-localhost:5001}"
        ).strip()
    )
    container_runtime: str | None = field(
        default_factory=lambda: os.getenv("R4_CONTAINER_RUNTIME") or os.getenv("CONTAINER_RUNTIME")
    )
    build_images: bool = field(
        default_factory=lambda: os.getenv("K8S_BUILD_IMAGES", "false").lower() == "true"
    )
    no_cleanup: bool = False


def run_cmd(
    cmd: list[str],
    capture_output: bool = True,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    logger.debug(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=check,
            timeout=timeout,
        )
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e.stderr}")
        raise


def check_kubectl_available() -> tuple[bool, str]:
    """Check if kubectl is available and configured."""
    try:
        result = run_cmd(["kubectl", "version", "--client", "-o", "json"], check=False)
        if result.returncode == 0:
            version_info = json.loads(result.stdout)
            version = version_info.get("clientVersion", {}).get("gitVersion", "unknown")
            return True, f"kubectl {version}"
        return False, "kubectl not configured properly"
    except FileNotFoundError:
        return False, "kubectl not found in PATH"
    except Exception as e:
        return False, str(e)


def check_cluster_available(context: str | None = None) -> tuple[bool, str]:
    """Check if a Kubernetes cluster is reachable."""
    try:
        cmd = ["kubectl", "cluster-info"]
        if context:
            cmd.extend(["--context", context])
        result = run_cmd(cmd, check=False, timeout=10)
        if result.returncode == 0:
            return True, "Cluster is reachable"
        return False, "Cluster not reachable"
    except subprocess.TimeoutExpired:
        return False, "Cluster connection timed out"
    except Exception as e:
        return False, str(e)


def check_kind_cluster() -> tuple[bool, str]:
    """Check if Kind cluster exists."""
    try:
        result = run_cmd(["kind", "get", "clusters"], check=False)
        if result.returncode == 0:
            clusters = result.stdout.strip().split("\n")
            if clusters and clusters[0]:
                return True, f"Kind clusters: {', '.join(clusters)}"
        return False, "No Kind clusters found"
    except FileNotFoundError:
        return False, "Kind not installed"
    except Exception as e:
        return False, str(e)


def detect_container_runtime(requested: str | None = None) -> str | None:
    """Return the first healthy supported container runtime."""
    candidates = [requested] if requested else ["podman", "docker"]
    for runtime in candidates:
        if not runtime:
            continue
        try:
            result = run_cmd([runtime, "info"], check=False)
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            return runtime
    return None


def check_container_runtime_available(requested: str | None = None) -> tuple[bool, str]:
    """Check if Podman or Docker is available for optional image builds."""
    runtime = detect_container_runtime(requested)
    if runtime is None:
        target = requested or "podman/docker"
        return False, f"{target} not available or not running"
    try:
        version = run_cmd([runtime, "--version"], check=False).stdout.strip()
    except Exception:
        version = runtime
    return True, version or runtime


def check_kustomize_available() -> tuple[bool, str]:
    """Check if kustomize (standalone or kubectl -k) is available."""
    # Try standalone kustomize first
    try:
        result = run_cmd(["kustomize", "version"], check=False)
        if result.returncode == 0:
            return True, f"kustomize {result.stdout.strip()}"
    except FileNotFoundError:
        pass

    # Fallback to kubectl -k
    try:
        result = run_cmd(
            ["kubectl", "kustomize", "--help"],
            check=False,
        )
        if result.returncode == 0:
            return True, "kubectl kustomize (built-in)"
    except FileNotFoundError:
        pass

    return False, "Neither kustomize nor kubectl kustomize available"


def check_registry_available(registry_host: str) -> tuple[bool, str]:
    """Check if local registry is reachable."""
    try:
        url = f"http://{registry_host}/v2/"
        req = Request(url, method="GET")
        with urlopen(req, timeout=5) as response:
            if response.status == 200:
                return True, f"Registry at {registry_host} is reachable"
    except Exception as e:
        return False, f"Registry not reachable: {e}"
    return False, "Registry returned non-200 status"


def check_manifests_exist() -> tuple[bool, str]:
    """Check if K8s manifests exist."""
    if not K8S_MANIFESTS.exists():
        return False, f"K8s manifests not found at {K8S_MANIFESTS}"
    kustomization = K8S_MANIFESTS / "kustomization.yaml"
    if not kustomization.exists():
        return False, "kustomization.yaml not found"
    return True, f"Manifests found at {K8S_MANIFESTS}"


def check_prerequisites(config: K8sTestConfig) -> dict[str, tuple[bool, str]]:
    """Check all prerequisites and return status dict."""
    checks = {}

    checks["kubectl"] = check_kubectl_available()
    checks["cluster"] = check_cluster_available(config.context)
    checks["kind"] = check_kind_cluster()
    checks["container_runtime"] = check_container_runtime_available(config.container_runtime)
    checks["kustomize"] = check_kustomize_available()
    checks["registry"] = check_registry_available(config.registry_host)
    checks["manifests"] = check_manifests_exist()

    return checks


def report_prerequisites(config: K8sTestConfig) -> bool:
    """Log every prerequisite check and report whether the required ones passed."""
    checks = check_prerequisites(config)
    all_ok = True

    for name, (ok, msg) in checks.items():
        status = "✓" if ok else "✗"
        level = logger.info if ok else logger.error
        level(f"  {status} {name}: {msg}")
        if not ok and prerequisite_required(name, config):
            all_ok = False

    return all_ok


def prerequisite_required(name: str, config: K8sTestConfig) -> bool:
    """Return whether a prerequisite is required for this run mode."""
    if name in {"kubectl", "cluster", "kustomize", "manifests"}:
        return True
    return bool(config.build_images and name in {"container_runtime", "registry"})


class K8sDeployManager:
    """Manages K8s deployments for testing."""

    def __init__(self, config: K8sTestConfig):
        self.config = config
        self._kubectl_base = ["kubectl"]
        if config.context:
            self._kubectl_base.extend(["--context", config.context])

    def _kubectl(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        """Run kubectl with configured context."""
        cmd = self._kubectl_base + args
        return run_cmd(cmd, **kwargs)

    def build_and_push_images(self) -> bool:
        """Build and push images to local registry."""
        logger.info("Building and pushing images to local registry...")
        runtime = detect_container_runtime(self.config.container_runtime)
        if runtime is None:
            logger.error("No healthy Podman or Docker runtime available for image builds")
            return False

        # Build UMRS server image
        try:
            run_cmd(
                [
                    runtime,
                    "build",
                    "-t",
                    f"{self.config.registry_host}/umrs:latest",
                    "-f",
                    str(DOCKER_DIR / "Dockerfile"),
                    str(DOCKER_DIR.parent),
                ],
                timeout=600,
            )
            run_cmd(
                [runtime, "push", f"{self.config.registry_host}/umrs:latest"],
                timeout=120,
            )
            logger.info("UMRS image built and pushed")
            return True
        except Exception as e:
            logger.error(f"Failed to build/push images: {e}")
            return False

    def deploy_stack(self) -> bool:
        """Deploy the full UMRS stack using kustomize."""
        logger.info(f"Deploying UMRS stack to namespace {self.config.namespace}...")

        try:
            # Apply kustomize manifests
            self._kubectl(
                [
                    "apply",
                    "-k",
                    str(K8S_MANIFESTS),
                ],
                timeout=60,
            )
            logger.info("Stack deployed successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to deploy stack: {e}")
            return False

    def wait_for_pods_ready(self, timeout: int | None = None) -> bool:
        """Wait for all pods in namespace to be ready."""
        timeout = timeout or self.config.timeout
        logger.info(f"Waiting for pods to be ready (timeout: {timeout}s)...")

        deployments = ["postgres", "minio", "umrs-server", "umrs-embed-worker"]
        start_time = time.time()

        for deployment in deployments:
            while time.time() - start_time < timeout:
                try:
                    result = self._kubectl(
                        [
                            "rollout",
                            "status",
                            f"deployment/{deployment}",
                            "-n",
                            self.config.namespace,
                            "--timeout=30s",
                        ],
                        check=False,
                        timeout=35,
                    )
                    if result.returncode == 0:
                        logger.info(f"  ✓ {deployment} is ready")
                        break
                except subprocess.TimeoutExpired:
                    pass

                logger.debug(f"  Waiting for {deployment}...")
                time.sleep(5)
            else:
                logger.error(f"  ✗ Timeout waiting for {deployment}")
                return False

        return True

    def get_service_endpoint(self, service_name: str) -> str | None:
        """Get the endpoint URL for a service via port-forward."""
        # For Kind, we'll use port-forward to access services
        try:
            result = self._kubectl(
                [
                    "get",
                    "svc",
                    service_name,
                    "-n",
                    self.config.namespace,
                    "-o",
                    "jsonpath={.spec.ports[0].port}",
                ],
                check=False,
            )
            if result.returncode == 0:
                port = result.stdout.strip()
                return f"http://localhost:{port}"
        except Exception as e:
            logger.error(f"Failed to get service endpoint: {e}")
        return None

    def port_forward(
        self, service_name: str, local_port: int, service_port: int
    ) -> subprocess.Popen:
        """Start port forwarding to a service."""
        cmd = self._kubectl_base + [
            "port-forward",
            f"svc/{service_name}",
            f"{local_port}:{service_port}",
            "-n",
            self.config.namespace,
        ]
        logger.info(
            f"Starting port-forward: localhost:{local_port} -> {service_name}:{service_port}"
        )
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(2)  # Give port-forward time to establish
        return proc

    def get_pod_logs(self, selector: str, tail: int = 50) -> str:
        """Get logs from pods matching selector."""
        try:
            result = self._kubectl(
                [
                    "logs",
                    "-l",
                    selector,
                    "-n",
                    self.config.namespace,
                    f"--tail={tail}",
                    "--all-containers=true",
                ],
                check=False,
            )
            return result.stdout
        except Exception:
            return ""

    def delete_stack(self) -> bool:
        """Delete the UMRS stack."""
        logger.info("Deleting UMRS stack...")
        try:
            self._kubectl(
                [
                    "delete",
                    "-k",
                    str(K8S_MANIFESTS),
                    "--ignore-not-found=true",
                ],
                timeout=120,
                check=False,
            )
            logger.info("Stack deleted")
            return True
        except Exception as e:
            logger.error(f"Failed to delete stack: {e}")
            return False


class APITestClient:
    """Simple HTTP client for testing API endpoints."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def check_health(self) -> bool:
        """Check if the health endpoint returns OK."""
        try:
            url = f"{self.base_url}/healthz"
            req = Request(url, method="GET")
            with urlopen(req, timeout=10) as response:
                return response.status == 200
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    def list_models(self) -> dict | None:
        """List available models (for RiVault mock)."""
        try:
            url = f"{self.base_url}/v1/models"
            req = Request(url, method="GET")
            with urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.debug(f"List models failed: {e}")
            return None


class K8sE2ETest:
    """Main E2E test runner."""

    def __init__(self, config: K8sTestConfig):
        self.config = config
        self.deploy_manager = K8sDeployManager(config)
        self.port_forwards: list[subprocess.Popen] = []

    def cleanup(self):
        """Clean up all resources."""
        # Stop port forwards
        for proc in self.port_forwards:
            proc.terminate()
            proc.wait(timeout=5)
        self.port_forwards.clear()

        # Delete stack if requested
        if not self.config.no_cleanup:
            self.deploy_manager.delete_stack()

    def _build_images_if_requested(self) -> bool:
        """Build and push images when configured, otherwise note that it was skipped."""
        if self.config.build_images:
            return self.deploy_manager.build_and_push_images()
        logger.info("  (Skipping image build - using pre-built images)")
        return True

    def _wait_for_pods_or_dump_logs(self) -> bool:
        """Wait for the deployment rollouts, dumping pod logs when they never settle."""
        if self.deploy_manager.wait_for_pods_ready():
            return True
        # Collect logs for debugging
        logger.info("\nCollecting pod logs for debugging...")
        logs = self.deploy_manager.get_pod_logs("app.kubernetes.io/name=umrs")
        if logs:
            logger.info(f"Pod logs:\n{logs}")
        return False

    def _check_api_endpoints(self) -> bool:
        """Port-forward to the UMRS server and verify its health endpoint answers."""
        # Port-forward to UMRS server
        pf = self.deploy_manager.port_forward("umrs-server", 8001, 8000)
        self.port_forwards.append(pf)
        time.sleep(3)  # Allow connection to establish

        client = APITestClient("http://localhost:8001")

        # Health check
        if client.check_health():
            logger.info("  ✓ Health endpoint OK")
            return True
        logger.error("  ✗ Health endpoint failed")
        return False

    def run(self) -> bool:
        """Run the full E2E test."""
        logger.info("=" * 60)
        logger.info("T-003: Kubernetes End-to-End Integration Test")
        logger.info("=" * 60)

        try:
            # Step 1: Check prerequisites
            logger.info("\n[Step 1/5] Checking prerequisites...")
            if not report_prerequisites(self.config):
                logger.error("Critical prerequisites not met. Aborting.")
                return False

            # Step 2: Build and push images (optional, may be pre-built)
            logger.info("\n[Step 2/5] Building and pushing images...")
            if not self._build_images_if_requested():
                return False

            # Step 3: Deploy stack
            logger.info("\n[Step 3/5] Deploying UMRS stack...")
            if not self.deploy_manager.deploy_stack():
                logger.error("Failed to deploy stack")
                return False

            # Step 4: Wait for pods
            logger.info("\n[Step 4/5] Waiting for pods to be ready...")
            if not self._wait_for_pods_or_dump_logs():
                return False

            # Step 5: Test endpoints
            logger.info("\n[Step 5/5] Testing API endpoints...")
            if not self._check_api_endpoints():
                return False

            logger.info("\n" + "=" * 60)
            logger.info("All tests passed!")
            logger.info("=" * 60)
            return True

        except Exception as e:
            logger.error(f"Test failed with exception: {e}")
            return False
        finally:
            self.cleanup()


# ============================================================================
# pytest fixtures and tests
# ============================================================================

if pytest is not None:

    @pytest.fixture
    def k8s_available():
        """Skip tests if K8s cluster is not available."""
        ok, _ = check_cluster_available()
        if not ok:
            pytest.skip("Kubernetes cluster not available")

    @pytest.fixture
    def kind_available():
        """Skip tests if Kind is not available."""
        ok, _ = check_kind_cluster()
        if not ok:
            pytest.skip("Kind cluster not available")

    @pytest.fixture
    def k8s_config():
        """Provide K8s test configuration."""
        return K8sTestConfig()

    class TestK8sPrerequisites:
        """Test prerequisite checks."""

        def test_kubectl_check(self):
            """Test kubectl availability check."""
            ok, msg = check_kubectl_available()
            # This test always runs - just verifies the check function works
            assert isinstance(ok, bool)
            assert isinstance(msg, str)

        def test_cluster_check(self, k8s_available):
            """Test cluster connectivity (skipped if no cluster)."""
            ok, msg = check_cluster_available()
            assert ok
            assert "reachable" in msg.lower()

        def test_manifests_check(self):
            """Test that K8s manifests exist."""
            ok, msg = check_manifests_exist()
            assert ok, f"Manifests not found: {msg}"

    class TestK8sDeployment:
        """Test K8s deployment (requires Kind cluster)."""

        @pytest.fixture(autouse=True)
        def setup(self, kind_available, k8s_config):
            """Set up test with cleanup on failure."""
            self.config = k8s_config
            self.config.no_cleanup = False
            self.test = K8sE2ETest(self.config)
            yield
            self.test.cleanup()

        @pytest.mark.slow
        def test_full_e2e(self):
            """Run the full E2E test."""
            assert self.test.run()


# ============================================================================
# CLI entry point
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the standalone runner."""
    parser = argparse.ArgumentParser(
        description="Kubernetes E2E test for UMRS",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Kubernetes namespace (default: umrs-system)",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Kubernetes context to use",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Wait timeout in seconds",
    )
    parser.add_argument(
        "--registry",
        default="localhost:5001",
        help="Local registry host:port",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't delete resources after test (for debugging)",
    )
    parser.add_argument(
        "--build-images",
        action="store_true",
        help="Build and push images with Podman/Docker before deploying",
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
    return parser


def main():
    """CLI entry point."""
    args = build_arg_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Build config
    config = K8sTestConfig(
        namespace=args.namespace or "umrs-system",
        context=args.context,
        timeout=args.timeout,
        registry_host=args.registry,
        build_images=args.build_images,
        no_cleanup=args.no_cleanup,
    )

    if args.check_only:
        logger.info("Checking prerequisites only...")
        sys.exit(0 if report_prerequisites(config) else 1)

    # Run full test
    test = K8sE2ETest(config)
    success = test.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
