#!/usr/bin/env python3
"""
T-004: RAG End-to-End Test.

Tests the complete RAG user journey:
  Upload → Embed → Search → LLM Response

This is the "make it real for users" milestone.

Test Scenarios:
1. Upload a test document
2. Wait for embedding completion
3. Query with semantic search
4. Verify retrieved context
5. Get LLM response with RAG context

Prerequisites:
- Gateway running on GATEWAY_URL (default: http://localhost:8000)
- UMRS running with embedding service
- pgvector database available
- Mini-RiVault or mock LLM available

Usage:
    # Run with pytest
    pytest tests/test_rag_e2e.py -v

    # Run standalone
    python tests/test_rag_e2e.py [--gateway-url URL] [--project-id ID]

Environment Variables:
    GATEWAY_URL: Gateway endpoint (default: http://localhost:8000)
    TEST_PROJECT_ID: Test project (default: test-rag-e2e)
    TEST_JWT_SECRET: JWT signing secret (default: test-secret)
    RAG_E2E_TIMEOUT: Test timeout in seconds (default: 120)
"""

import argparse
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

# Tier marker (D-5/ADR-054): opt-in only via R4_RUN_E2E=1.
pytestmark = pytest.mark.e2e

try:
    import httpx
except ImportError:
    httpx = None

try:
    from jose import jwt as jose_jwt
except ImportError:
    jose_jwt = None


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class RAGTestConfig:
    """RAG E2E test configuration."""

    gateway_url: str = "http://localhost:8000"
    project_id: str = "test-rag-e2e"
    jwt_secret: str = "test-secret"
    model: str = "rivault-gpt-oss-static"
    timeout: int = 120
    poll_interval: int = 2
    verbose: bool = False

    @classmethod
    def from_env(cls) -> "RAGTestConfig":
        """Load configuration from environment."""
        return cls(
            gateway_url=os.getenv("GATEWAY_URL", "http://localhost:8000"),
            project_id=os.getenv("TEST_PROJECT_ID", "test-rag-e2e"),
            jwt_secret=os.getenv("TEST_JWT_SECRET", "test-secret"),
            model=os.getenv("TEST_MODEL", "rivault-gpt-oss-static"),
            timeout=int(os.getenv("RAG_E2E_TIMEOUT", "120")),
            verbose=os.getenv("RAG_E2E_VERBOSE", "").lower() in ("1", "true"),
        )


# =============================================================================
# Test Documents
# =============================================================================

# Sample documents for testing RAG retrieval
TEST_DOCUMENTS = {
    "fugaku_mpi_errors.txt": """
# Fugaku MPI Error Analysis Report

## Common MPI_Init Failures on Fugaku

The most frequent cause of MPI_Init failures on Fugaku is memory allocation
issues during the UCX transport layer initialization.

### Error Pattern 1: UCX shmget failure
When you see "UCX shmget failed" errors, this typically indicates:
1. Insufficient shared memory allocation (/dev/shm)
2. Too many concurrent MPI processes exhausting SysV IPC resources
3. Incorrect TOFU network configuration

Recommended fix: Set UCX_SHM_DEVICES=posix or increase /dev/shm size.

### Error Pattern 2: TOFU initialization timeout
TOFU network errors during MPI_Init usually mean:
1. Node allocation mismatch
2. Network partition issues
3. Hardware failure on specific nodes

Check with `fjstat -A` for node health status.

### Error Pattern 3: Memory allocation failure
Large MPI communicators may fail with ENOMEM if:
1. Per-process memory limits are too restrictive
2. The job requests more ranks than available memory supports

Recommendation: Use `--mem-per-cpu` Slurm flag appropriately.
""",
    "fugaku_performance_guide.txt": """
# Fugaku Performance Tuning Guide

## CPU Affinity Settings

For optimal performance on Fugaku's A64FX processors:

1. **Thread pinning**: Use `OMP_PLACES=cores` and `OMP_PROC_BIND=close`
2. **NUMA awareness**: Keep threads within the same CMG (Core Memory Group)
3. **SMT usage**: Disable SMT for memory-bound codes

## Memory Bandwidth Optimization

The A64FX has HBM2 memory with 1TB/s bandwidth per node.
To maximize bandwidth utilization:

- Use large pages (2MB or 512MB)
- Align data structures to cache line boundaries
- Minimize false sharing between threads

## Vectorization Tips

The SVE instruction set supports 512-bit vectors.
Compiler flags for optimal vectorization:
- `-Kfast,openmp,SVE=512`
- `-Nlst=t` (for vectorization report)

## File I/O Best Practices

Fugaku uses Lustre parallel filesystem.
For best I/O performance:
- Set stripe count: `lfs setstripe -c 8 directory/`
- Use collective MPI-IO operations
- Avoid small, frequent I/O operations
""",
}


def create_test_documents(tmpdir: Path) -> list[Path]:
    """Create test documents in temporary directory."""
    created = []
    for filename, content in TEST_DOCUMENTS.items():
        filepath = tmpdir / filename
        filepath.write_text(content)
        created.append(filepath)
    return created


# =============================================================================
# Test Helpers
# =============================================================================


def create_jwt(project_id: str, secret: str, user_id: str = "test-user") -> str | None:
    """Create JWT token for authentication."""
    if jose_jwt is None:
        return None
    payload = {
        "project_id": project_id,
        "sub": user_id,
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def get_auth_headers(config: RAGTestConfig) -> dict[str, str]:
    """Build authentication headers."""
    headers = {"Content-Type": "application/json"}
    token = create_jwt(config.project_id, config.jwt_secret)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        # Fallback for testing without jose
        headers["X-Project-ID"] = config.project_id
        headers["X-User-ID"] = "test-user"
    return headers


class RAGTestResult:
    """Test result tracker."""

    def __init__(self):
        self.steps = []
        self.passed = 0
        self.failed = 0

    def step_ok(self, name: str, detail: str = ""):
        """Record successful step."""
        self.steps.append(("ok", name, detail))
        self.passed += 1
        print(f"  ✅ {name}" + (f": {detail}" if detail else ""))

    def step_fail(self, name: str, detail: str):
        """Record failed step."""
        self.steps.append(("fail", name, detail))
        self.failed += 1
        print(f"  ❌ {name}: {detail}")

    def step_skip(self, name: str, reason: str):
        """Record skipped step."""
        self.steps.append(("skip", name, reason))
        print(f"  ⏭️  {name}: {reason}")

    @property
    def success(self) -> bool:
        return self.failed == 0


# =============================================================================
# RAG E2E Test Implementation
# =============================================================================


class RAGEndToEndTest:
    """
    RAG End-to-End Test Runner.

    Tests the complete flow: Upload → Embed → Search → LLM Response
    """

    def __init__(self, config: RAGTestConfig):
        self.config = config
        self.result = RAGTestResult()
        self.client = httpx.Client(timeout=config.timeout)
        self.headers = get_auth_headers(config)
        self.uploaded_doc_ids: list[str] = []
        self.index_name: str = "test-rag-index"

    def run(self) -> bool:
        """Run full E2E test suite."""
        print("\n" + "=" * 60)
        print("T-004: RAG End-to-End Test")
        print("=" * 60)
        print(f"Gateway: {self.config.gateway_url}")
        print(f"Project: {self.config.project_id}")
        print()

        try:
            # Step 1: Check prerequisites
            print("📋 Step 1: Check Prerequisites")
            if not self._check_prerequisites():
                return False

            # Step 2: Upload documents
            print("\n📤 Step 2: Upload Documents")
            if not self._upload_documents():
                return False

            # Step 3: Wait for embedding
            print("\n⏳ Step 3: Wait for Embedding")
            if not self._wait_for_embedding():
                return False

            # Step 4: Test semantic search
            print("\n🔍 Step 4: Semantic Search")
            if not self._test_semantic_search():
                return False

            # Step 5: Test RAG query (search + LLM)
            print("\n🤖 Step 5: RAG Query (Search + LLM)")
            if not self._test_rag_query():
                return False

            # Step 6: Cleanup
            print("\n🧹 Step 6: Cleanup")
            self._cleanup()

        except Exception as e:
            self.result.step_fail("Unexpected error", str(e))
            return False

        finally:
            self.client.close()

        # Summary
        print("\n" + "=" * 60)
        print(f"RESULTS: {self.result.passed} passed, {self.result.failed} failed")
        print("=" * 60)

        return self.result.success

    def _check_prerequisites(self) -> bool:
        """Check that required services are available."""
        # Check Gateway health
        try:
            resp = self.client.get(f"{self.config.gateway_url}/health")
            if resp.status_code == 200:
                self.result.step_ok("Gateway health", "UP")
            else:
                self.result.step_fail("Gateway health", f"Status {resp.status_code}")
                return False
        except httpx.RequestError as e:
            self.result.step_fail("Gateway connection", str(e))
            return False

        # Check UMRS RAG endpoint
        try:
            resp = self.client.get(
                f"{self.config.gateway_url}/gw/rag/health",
                headers=self.headers,
            )
            if resp.status_code in (200, 404):  # 404 means endpoint exists but no index
                self.result.step_ok("RAG service", "Available")
            else:
                self.result.step_fail("RAG service", f"Status {resp.status_code}")
                return False
        except httpx.RequestError as e:
            self.result.step_fail("RAG service connection", str(e))
            return False

        return True

    def _upload_documents(self) -> bool:
        """Upload test documents for RAG indexing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            doc_files = create_test_documents(tmppath)

            for doc_file in doc_files:
                try:
                    with open(doc_file, "rb") as f:
                        files = {"file": (doc_file.name, f, "text/plain")}
                        data = {
                            "project_id": self.config.project_id,
                            "index_name": self.index_name,
                        }
                        resp = self.client.post(
                            f"{self.config.gateway_url}/gw/rag/upload",
                            files=files,
                            data=data,
                            headers={
                                k: v
                                for k, v in self.headers.items()
                                if k != "Content-Type"  # Let httpx set multipart
                            },
                        )

                    if resp.status_code in (200, 201, 202):
                        result = resp.json()
                        doc_id = result.get("document_id", "unknown")
                        self.uploaded_doc_ids.append(doc_id)
                        self.result.step_ok(f"Upload {doc_file.name}", f"doc_id={doc_id}")
                    else:
                        self.result.step_fail(
                            f"Upload {doc_file.name}",
                            f"Status {resp.status_code}: {resp.text[:100]}",
                        )
                        return False

                except httpx.RequestError as e:
                    self.result.step_fail(f"Upload {doc_file.name}", str(e))
                    return False

        return True

    def _wait_for_embedding(self) -> bool:
        """Wait for documents to be embedded."""
        deadline = time.time() + self.config.timeout

        while time.time() < deadline:
            try:
                # Check embedding status
                resp = self.client.get(
                    f"{self.config.gateway_url}/gw/rag/index/{self.index_name}/status",
                    headers=self.headers,
                    params={"project_id": self.config.project_id},
                )

                if resp.status_code == 200:
                    status = resp.json()
                    state = status.get("status", "unknown")

                    if state == "ready":
                        doc_count = status.get("document_count", 0)
                        self.result.step_ok("Embedding complete", f"{doc_count} documents indexed")
                        return True
                    elif state == "processing":
                        if self.config.verbose:
                            print("    ... embedding in progress")
                        time.sleep(self.config.poll_interval)
                    elif state == "error":
                        self.result.step_fail(
                            "Embedding failed", status.get("error", "Unknown error")
                        )
                        return False
                    else:
                        time.sleep(self.config.poll_interval)

                elif resp.status_code == 404:
                    # Index not found yet, may still be creating
                    time.sleep(self.config.poll_interval)
                else:
                    self.result.step_fail("Check embedding status", f"Status {resp.status_code}")
                    return False

            except httpx.RequestError as e:
                self.result.step_fail("Check embedding status", str(e))
                return False

        self.result.step_fail("Embedding timeout", f"Exceeded {self.config.timeout}s")
        return False

    def _test_semantic_search(self) -> bool:
        """Test semantic search retrieval."""
        test_queries = [
            ("Why does UCX shmget fail?", "UCX", "shmget"),
            ("How to tune MPI performance on Fugaku?", "performance", "thread"),
            ("What causes MPI_Init failures?", "MPI_Init", "memory"),
        ]

        for query, expected_term1, expected_term2 in test_queries:
            try:
                resp = self.client.post(
                    f"{self.config.gateway_url}/gw/rag/search",
                    headers=self.headers,
                    json={
                        "query": query,
                        "project_id": self.config.project_id,
                        "index_name": self.index_name,
                        "top_k": 3,
                    },
                )

                if resp.status_code == 200:
                    results = resp.json()
                    hits = results.get("results", [])

                    if len(hits) > 0:
                        top_content = hits[0].get("content", "").lower()
                        # Check if relevant terms appear in results
                        has_term1 = expected_term1.lower() in top_content
                        has_term2 = expected_term2.lower() in top_content

                        if has_term1 or has_term2:
                            self.result.step_ok(
                                f"Search: '{query[:30]}...'",
                                f"{len(hits)} hits, relevant content found",
                            )
                        else:
                            self.result.step_fail(
                                f"Search: '{query[:30]}...'",
                                "Results don't contain expected terms",
                            )
                            return False
                    else:
                        self.result.step_fail(
                            f"Search: '{query[:30]}...'",
                            "No results returned",
                        )
                        return False
                else:
                    self.result.step_fail(
                        f"Search: '{query[:30]}...'",
                        f"Status {resp.status_code}",
                    )
                    return False

            except httpx.RequestError as e:
                self.result.step_fail(f"Search: '{query[:30]}...'", str(e))
                return False

        return True

    def _test_rag_query(self) -> bool:
        """Test full RAG query with LLM response."""
        rag_queries = [
            {
                "query": "What causes UCX shmget failures on Fugaku and how do I fix them?",
                "expected_in_answer": ["ucx", "shmget", "posix"],
                "expected_sources": 1,
            },
            {
                "query": "How do I optimize memory bandwidth on Fugaku?",
                "expected_in_answer": ["hbm", "bandwidth", "memory"],
                "expected_sources": 1,
            },
        ]

        for test_case in rag_queries:
            try:
                resp = self.client.post(
                    f"{self.config.gateway_url}/gw/rag/query",
                    headers=self.headers,
                    json={
                        "query": test_case["query"],
                        "project_id": self.config.project_id,
                        "index_name": self.index_name,
                        "top_k": 5,
                        "include_sources": True,
                    },
                )
            except httpx.RequestError as e:
                self.result.step_fail(f"RAG: '{test_case['query'][:35]}...'", str(e))
                return False
            if not self._evaluate_rag_response(test_case, resp):
                return False

        return True

    def _evaluate_rag_response(self, test_case: dict, resp) -> bool:
        """Classify one /gw/rag/query response and record the step outcome."""
        query = test_case["query"]
        if resp.status_code == 503:
            # LLM not available - partial success
            self.result.step_skip(f"RAG: '{query[:35]}...'", "LLM unavailable (503)")
            return True
        if resp.status_code != 200:
            self.result.step_fail(
                f"RAG: '{query[:35]}...'",
                f"Status {resp.status_code}: {resp.text[:100]}",
            )
            return False

        result = resp.json()
        answer = result.get("answer", "").lower()
        sources = result.get("sources", [])

        # Check answer contains expected terms
        expected_terms = test_case["expected_in_answer"]
        found_terms = [t for t in expected_terms if t in answer]
        has_sources = len(sources) >= test_case["expected_sources"]

        if found_terms and has_sources:
            self.result.step_ok(
                f"RAG: '{query[:35]}...'",
                f"Answer has {len(found_terms)}/{len(expected_terms)} terms, {len(sources)} sources",
            )
            return True

        detail = []
        if not found_terms:
            detail.append(f"missing terms: {expected_terms}")
        if not has_sources:
            detail.append(f"only {len(sources)} sources")
        self.result.step_fail(f"RAG: '{query[:35]}...'", "; ".join(detail))
        return False

    def _cleanup(self) -> bool:
        """Clean up test data."""
        # Delete test index
        try:
            resp = self.client.delete(
                f"{self.config.gateway_url}/gw/rag/index/{self.index_name}",
                headers=self.headers,
                params={"project_id": self.config.project_id},
            )
            if resp.status_code in (200, 204, 404):
                self.result.step_ok("Delete test index", f"{self.index_name}")
            else:
                self.result.step_skip("Delete test index", f"Status {resp.status_code}")
        except httpx.RequestError:
            self.result.step_skip("Delete test index", "Request failed")

        return True


# =============================================================================
# Pytest Integration
# =============================================================================


@pytest.fixture
def rag_config():
    """Pytest fixture for RAG test configuration."""
    return RAGTestConfig.from_env()


@pytest.mark.skipif(httpx is None, reason="httpx not installed")
class TestRAGEndToEnd:
    """Pytest wrapper for RAG E2E tests."""

    def test_gateway_reachable(self, rag_config):
        """Test that gateway is reachable."""
        with httpx.Client(timeout=10) as client:
            try:
                resp = client.get(f"{rag_config.gateway_url}/health")
                assert resp.status_code == 200, f"Gateway health check failed: {resp.status_code}"
            except httpx.RequestError as e:
                pytest.skip(f"Gateway not reachable: {e}")

    def test_rag_upload(self, rag_config):
        """Test document upload for RAG."""
        with httpx.Client(timeout=30) as client:
            headers = get_auth_headers(rag_config)

            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write("Test document for RAG upload.\n")
                f.write("Contains information about testing.\n")
                temp_path = f.name

            try:
                with open(temp_path, "rb") as f:
                    files = {"file": ("test_upload.txt", f, "text/plain")}
                    data = {
                        "project_id": rag_config.project_id,
                        "index_name": "pytest-test-index",
                    }

                    resp = client.post(
                        f"{rag_config.gateway_url}/gw/rag/upload",
                        files=files,
                        data=data,
                        headers={k: v for k, v in headers.items() if k != "Content-Type"},
                    )

                # Accept various success codes or skip if service unavailable
                if resp.status_code == 503:
                    pytest.skip("RAG service unavailable")
                elif resp.status_code == 404:
                    pytest.skip("RAG upload endpoint not found")
                else:
                    assert resp.status_code in (200, 201, 202), f"Upload failed: {resp.status_code}"

            finally:
                Path(temp_path).unlink(missing_ok=True)

    def test_rag_search(self, rag_config):
        """Test RAG semantic search."""
        with httpx.Client(timeout=30) as client:
            headers = get_auth_headers(rag_config)

            resp = client.post(
                f"{rag_config.gateway_url}/gw/rag/search",
                headers=headers,
                json={
                    "query": "test query",
                    "project_id": rag_config.project_id,
                    "top_k": 3,
                },
            )

            if resp.status_code == 503:
                pytest.skip("RAG service unavailable")
            elif resp.status_code == 404:
                pytest.skip("RAG search endpoint not found")
            else:
                assert resp.status_code in (200, 404), f"Search failed: {resp.status_code}"

    def test_full_rag_e2e(self, rag_config):
        """Run full RAG E2E test suite."""
        test = RAGEndToEndTest(rag_config)
        success = test.run()

        if not success and test.result.failed == 0:
            pytest.skip("Test prerequisites not met")
        else:
            assert success, f"RAG E2E test failed: {test.result.failed} failures"


# =============================================================================
# Standalone Execution
# =============================================================================


def main():
    """Run RAG E2E test standalone."""
    parser = argparse.ArgumentParser(description="RAG End-to-End Test")
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("GATEWAY_URL", "http://localhost:8000"),
        help="Gateway URL",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("TEST_PROJECT_ID", "test-rag-e2e"),
        help="Test project ID",
    )
    parser.add_argument(
        "--jwt-secret",
        default=os.getenv("TEST_JWT_SECRET", "test-secret"),
        help="JWT signing secret",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Test timeout in seconds",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if httpx is None:
        print("Error: httpx not installed. Run: pip install httpx")
        sys.exit(1)

    config = RAGTestConfig(
        gateway_url=args.gateway_url,
        project_id=args.project_id,
        jwt_secret=args.jwt_secret,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    test = RAGEndToEndTest(config)
    success = test.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
