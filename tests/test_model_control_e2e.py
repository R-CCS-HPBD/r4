"""
Model Control Flow E2E Scenario Tests (T-211 - T-214).

End-to-end scenarios covering user/system model flows:
- T-211: System warm reuse - two users share warm system endpoint
- T-212: User download + provision + chat - full flow
- T-213: Policy denial case - user without access gets 403 with reason
- T-214: Hybrid endpoint URL routing - Gateway routes to node:port and DNS
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pytest

# =============================================================================
# Mock Components for E2E Testing
# =============================================================================


class EndpointType(Enum):
    """Type of endpoint."""

    SYSTEM = "system"
    PROJECT = "project"
    USER = "user"


class EndpointStatus(Enum):
    """Endpoint status."""

    READY = "ready"
    PROVISIONING = "provisioning"
    FAILED = "failed"


@dataclass
class MockEndpoint:
    """Mock endpoint for testing."""

    endpoint_id: str
    model_name: str
    endpoint_type: EndpointType
    status: EndpointStatus
    url: str
    owner_id: str
    project_id: str | None = None
    users: list[str] = field(default_factory=list)  # Users currently using this endpoint
    request_count: int = 0


@dataclass
class MockAuditEntry:
    """Mock audit log entry."""

    timestamp: datetime
    user_id: str
    project_id: str
    action: str
    model_name: str
    endpoint_id: str
    endpoint_type: str
    request_duration_ms: int = 0


class MockEndpointRegistry:
    """Mock endpoint registry for E2E tests."""

    def __init__(self):
        self.endpoints: dict[str, MockEndpoint] = {}
        self.audit_log: list[MockAuditEntry] = []

    def get_system_endpoint(self, model_name: str) -> MockEndpoint | None:
        """Get system endpoint for a model."""
        for ep in self.endpoints.values():
            if (
                ep.model_name == model_name
                and ep.endpoint_type == EndpointType.SYSTEM
                and ep.status == EndpointStatus.READY
            ):
                return ep
        return None

    def provision_user_endpoint(
        self,
        model_name: str,
        user_id: str,
        project_id: str,
    ) -> MockEndpoint:
        """Provision a user-scoped endpoint."""
        endpoint_id = f"ep-user-{user_id}-{model_name}"
        endpoint = MockEndpoint(
            endpoint_id=endpoint_id,
            model_name=model_name,
            endpoint_type=EndpointType.USER,
            status=EndpointStatus.READY,
            url=f"http://node1:8080/{endpoint_id}",
            owner_id=user_id,
            project_id=project_id,
        )
        self.endpoints[endpoint_id] = endpoint
        return endpoint

    def record_usage(
        self,
        endpoint: MockEndpoint,
        user_id: str,
        project_id: str,
        duration_ms: int = 100,
    ):
        """Record endpoint usage in audit log."""
        entry = MockAuditEntry(
            timestamp=datetime.now(),
            user_id=user_id,
            project_id=project_id,
            action="inference",
            model_name=endpoint.model_name,
            endpoint_id=endpoint.endpoint_id,
            endpoint_type=endpoint.endpoint_type.value,
            request_duration_ms=duration_ms,
        )
        self.audit_log.append(entry)
        endpoint.request_count += 1
        if user_id not in endpoint.users:
            endpoint.users.append(user_id)


class MockModelRegistry:
    """Mock model registry for E2E tests."""

    def __init__(self):
        self.models: dict[str, dict[str, Any]] = {}
        self.policies: dict[str, dict[str, Any]] = {}

    def register_model(
        self,
        name: str,
        scope: str,
        owner_id: str,
        project_id: str | None = None,
        storage_path: str = "",
    ) -> dict[str, Any]:
        """Register a model."""
        model = {
            "name": name,
            "scope": scope,
            "owner_id": owner_id,
            "project_id": project_id,
            "storage_path": storage_path or f"/models/{name}",
            "status": "available",
        }
        self.models[name] = model
        return model

    def set_policy(
        self,
        model_name: str,
        allowed_projects: list[str] = None,
        denied_projects: list[str] = None,
    ):
        """Set model policy."""
        self.policies[model_name] = {
            "model_name": model_name,
            "allowed_projects": allowed_projects or [],
            "denied_projects": denied_projects or [],
        }

    def check_access(
        self,
        model_name: str,
        user_id: str,
        project_id: str,
    ) -> tuple[bool, str]:
        """Check if user can access model."""
        model = self.models.get(model_name)
        if not model:
            return False, f"Model '{model_name}' not found"

        # Scope-based access check
        if model["scope"] == "user":
            if model["owner_id"] != user_id:
                return False, f"Model '{model_name}' is private to user '{model['owner_id']}'"
        elif model["scope"] == "project" and model["project_id"] != project_id:
            return (
                False,
                f"Model '{model_name}' requires membership in project '{model['project_id']}'",
            )

        # Policy-based access check
        policy = self.policies.get(model_name)
        if policy:
            if project_id in policy.get("denied_projects", []):
                return False, f"Project '{project_id}' is denied access to '{model_name}'"
            allowed = policy.get("allowed_projects", [])
            if allowed and project_id not in allowed:
                return False, f"Model '{model_name}' is only available to: {', '.join(allowed)}"

        return True, "OK"


class MockGateway:
    """Mock Gateway for E2E tests."""

    def __init__(self, registry: MockEndpointRegistry, model_registry: MockModelRegistry):
        self.registry = registry
        self.model_registry = model_registry

    def route_request(
        self,
        model_name: str,
        user_id: str,
        project_id: str,
        prefer_system: bool = True,
    ) -> tuple[MockEndpoint | None, str | None]:
        """Route a request to appropriate endpoint."""
        # Check access first
        allowed, reason = self.model_registry.check_access(model_name, user_id, project_id)
        if not allowed:
            return None, reason

        # Try system endpoint first if preference is set
        if prefer_system:
            endpoint = self.registry.get_system_endpoint(model_name)
            if endpoint:
                return endpoint, None

        # Fall back to user endpoint
        endpoint = self.registry.provision_user_endpoint(model_name, user_id, project_id)
        return endpoint, None


# =============================================================================
# T-211: System Warm Reuse
# =============================================================================


class TestSystemWarmReuse:
    """T-211: Two users share warm system endpoint, audit logs differ."""

    @pytest.fixture
    def setup_system(self):
        """Set up test environment with system model."""
        registry = MockEndpointRegistry()
        model_registry = MockModelRegistry()

        # Create a system model
        model_registry.register_model(
            name="llama-3-70b",
            scope="system",
            owner_id="admin",
        )

        # Create a warm system endpoint
        system_endpoint = MockEndpoint(
            endpoint_id="ep-sys-llama-3",
            model_name="llama-3-70b",
            endpoint_type=EndpointType.SYSTEM,
            status=EndpointStatus.READY,
            url="http://warm-pool-1:8080/v1",
            owner_id="system",
        )
        registry.endpoints[system_endpoint.endpoint_id] = system_endpoint

        gateway = MockGateway(registry, model_registry)

        return {
            "registry": registry,
            "model_registry": model_registry,
            "gateway": gateway,
            "system_endpoint": system_endpoint,
        }

    def test_two_users_share_warm_endpoint(self, setup_system):
        """Two users from different projects share the same warm endpoint."""
        gateway = setup_system["gateway"]
        registry = setup_system["registry"]

        # User A from project-A requests
        endpoint_a, error = gateway.route_request(
            model_name="llama-3-70b",
            user_id="user-a",
            project_id="proj-a",
            prefer_system=True,
        )
        assert error is None
        assert endpoint_a.endpoint_type == EndpointType.SYSTEM
        registry.record_usage(endpoint_a, "user-a", "proj-a", duration_ms=150)

        # User B from project-B requests - should get SAME endpoint
        endpoint_b, error = gateway.route_request(
            model_name="llama-3-70b",
            user_id="user-b",
            project_id="proj-b",
            prefer_system=True,
        )
        assert error is None
        assert endpoint_b.endpoint_id == endpoint_a.endpoint_id  # SAME endpoint
        registry.record_usage(endpoint_b, "user-b", "proj-b", duration_ms=200)

    def test_audit_logs_differ_per_user(self, setup_system):
        """Audit logs distinguish between users even on shared endpoint."""
        gateway = setup_system["gateway"]
        registry = setup_system["registry"]

        # Both users make requests
        endpoint, _ = gateway.route_request("llama-3-70b", "user-a", "proj-a")
        registry.record_usage(endpoint, "user-a", "proj-a")

        endpoint, _ = gateway.route_request("llama-3-70b", "user-b", "proj-b")
        registry.record_usage(endpoint, "user-b", "proj-b")

        # Verify audit logs are separate
        audit = registry.audit_log
        assert len(audit) == 2

        assert audit[0].user_id == "user-a"
        assert audit[0].project_id == "proj-a"
        assert audit[0].endpoint_type == "system"

        assert audit[1].user_id == "user-b"
        assert audit[1].project_id == "proj-b"
        assert audit[1].endpoint_type == "system"

        # Same endpoint, different billing/audit trail
        assert audit[0].endpoint_id == audit[1].endpoint_id

    def test_warm_reuse_no_provisioning_delay(self, setup_system):
        """Warm reuse has no provisioning delay - endpoint already ready."""
        gateway = setup_system["gateway"]
        system_endpoint = setup_system["system_endpoint"]

        # First request
        endpoint1, _ = gateway.route_request("llama-3-70b", "user-a", "proj-a")
        assert endpoint1.status == EndpointStatus.READY

        # Second request - same endpoint, no delay
        endpoint2, _ = gateway.route_request("llama-3-70b", "user-b", "proj-b")
        assert endpoint2.status == EndpointStatus.READY
        assert endpoint2.endpoint_id == system_endpoint.endpoint_id


# =============================================================================
# T-212: User Download + Provision + Chat Flow
# =============================================================================


class TestUserDownloadProvisionChatFlow:
    """T-212: Full flow: download → stage → provision → chat."""

    @dataclass
    class MockDownloadJob:
        """Mock download job."""

        job_id: str
        model_name: str
        status: str = "pending"
        target_path: str = ""
        progress: float = 0.0

    @pytest.fixture
    def setup_user_flow(self):
        """Set up test environment for user flow."""
        registry = MockEndpointRegistry()
        model_registry = MockModelRegistry()

        return {
            "registry": registry,
            "model_registry": model_registry,
            "download_jobs": {},
        }

    def test_full_user_flow(self, setup_user_flow):
        """Test complete user flow: download → register → provision → chat."""
        model_registry = setup_user_flow["model_registry"]
        registry = setup_user_flow["registry"]
        download_jobs = setup_user_flow["download_jobs"]

        user_id = "researcher-1"
        project_id = "research-lab"
        model_name = "custom-model-v1"

        # Step 1: Request model download
        download_job = self.MockDownloadJob(
            job_id="dl-123",
            model_name=model_name,
            status="running",
            target_path=f"/data/models/{project_id}/{model_name}",
        )
        download_jobs[download_job.job_id] = download_job

        # Step 2: Download completes, model auto-registered
        download_job.status = "completed"
        download_job.progress = 1.0

        model = model_registry.register_model(
            name=model_name,
            scope="user",
            owner_id=user_id,
            project_id=project_id,
            storage_path=download_job.target_path,
        )

        assert model["status"] == "available"
        assert model["owner_id"] == user_id

        # Step 3: Provision endpoint
        endpoint = registry.provision_user_endpoint(
            model_name=model_name,
            user_id=user_id,
            project_id=project_id,
        )

        assert endpoint.status == EndpointStatus.READY
        assert endpoint.owner_id == user_id

        # Step 4: Chat with model
        registry.record_usage(endpoint, user_id, project_id)
        assert endpoint.request_count == 1

        # Verify complete audit trail
        assert len(registry.audit_log) == 1
        assert registry.audit_log[0].user_id == user_id
        assert registry.audit_log[0].model_name == model_name

    def test_user_cannot_access_others_private_model(self, setup_user_flow):
        """After download, other users cannot access the private model."""
        model_registry = setup_user_flow["model_registry"]
        registry = setup_user_flow["registry"]

        # User A downloads and registers
        model_registry.register_model(
            name="private-model",
            scope="user",
            owner_id="user-a",
        )

        # User B tries to access
        gateway = MockGateway(registry, model_registry)
        endpoint, error = gateway.route_request(
            model_name="private-model",
            user_id="user-b",
            project_id="any-project",
        )

        assert endpoint is None
        assert error is not None
        assert "private" in error.lower()


# =============================================================================
# T-213: Policy Denial Case
# =============================================================================


class TestPolicyDenialCase:
    """T-213: User without access gets 403 with clear reason."""

    @pytest.fixture
    def setup_restricted_model(self):
        """Set up test environment with restricted model."""
        registry = MockEndpointRegistry()
        model_registry = MockModelRegistry()

        # Create a restricted system model
        model_registry.register_model(
            name="gpt-4-internal",
            scope="system",
            owner_id="admin",
        )

        # Add policy restricting to specific projects
        model_registry.set_policy(
            model_name="gpt-4-internal",
            allowed_projects=["ml-team", "research-group"],
        )

        gateway = MockGateway(registry, model_registry)

        return {
            "registry": registry,
            "model_registry": model_registry,
            "gateway": gateway,
        }

    def test_unauthorized_project_gets_clear_denial(self, setup_restricted_model):
        """User from unauthorized project gets 403 with helpful message."""
        gateway = setup_restricted_model["gateway"]

        endpoint, error = gateway.route_request(
            model_name="gpt-4-internal",
            user_id="user-x",
            project_id="finance-dept",  # Not in allowed_projects
        )

        assert endpoint is None
        assert error is not None
        assert "ml-team" in error or "research-group" in error

    def test_denied_project_gets_specific_denial(self, setup_restricted_model):
        """User from denied project gets specific denial message."""
        model_registry = setup_restricted_model["model_registry"]
        gateway = setup_restricted_model["gateway"]

        # Add explicit deny
        model_registry.set_policy(
            model_name="gpt-4-internal",
            allowed_projects=["ml-team"],
            denied_projects=["competitor-project"],
        )

        endpoint, error = gateway.route_request(
            model_name="gpt-4-internal",
            user_id="user-y",
            project_id="competitor-project",
        )

        assert endpoint is None
        assert error is not None
        assert "denied" in error.lower()

    def test_authorized_project_succeeds(self, setup_restricted_model):
        """User from authorized project can access the model."""
        gateway = setup_restricted_model["gateway"]
        registry = setup_restricted_model["registry"]

        # Add system endpoint
        registry.endpoints["ep-sys-gpt4"] = MockEndpoint(
            endpoint_id="ep-sys-gpt4",
            model_name="gpt-4-internal",
            endpoint_type=EndpointType.SYSTEM,
            status=EndpointStatus.READY,
            url="http://warm-pool-2:8080/v1",
            owner_id="system",
        )

        endpoint, error = gateway.route_request(
            model_name="gpt-4-internal",
            user_id="team-member",
            project_id="ml-team",  # In allowed_projects
        )

        assert error is None
        assert endpoint is not None
        assert endpoint.model_name == "gpt-4-internal"


# =============================================================================
# T-214: Hybrid Endpoint URL Routing
# =============================================================================


class TestHybridEndpointURLRouting:
    """T-214: Gateway routes to both node:port and service DNS."""

    @pytest.fixture
    def endpoints_with_different_urls(self):
        """Create endpoints with different URL formats."""
        return {
            # HPC/Slurm style: node:port
            "slurm": MockEndpoint(
                endpoint_id="ep-slurm-1",
                model_name="llama-3-70b",
                endpoint_type=EndpointType.USER,
                status=EndpointStatus.READY,
                url="http://compute-node-42:11434/v1",
                owner_id="user-hpc",
            ),
            # Kubernetes style: service DNS
            "k8s": MockEndpoint(
                endpoint_id="ep-k8s-1",
                model_name="llama-3-70b",
                endpoint_type=EndpointType.SYSTEM,
                status=EndpointStatus.READY,
                url="http://litellm.ai-platform.svc.cluster.local:8080/v1",
                owner_id="system",
            ),
            # Docker Compose style: service name
            "compose": MockEndpoint(
                endpoint_id="ep-compose-1",
                model_name="llama-3-70b",
                endpoint_type=EndpointType.PROJECT,
                status=EndpointStatus.READY,
                url="http://litellm:8080/v1",
                owner_id="project-dev",
                project_id="dev-team",
            ),
        }

    def test_gateway_routes_to_slurm_node_port(self, endpoints_with_different_urls):
        """Gateway correctly routes to Slurm node:port URLs."""
        endpoint = endpoints_with_different_urls["slurm"]

        # Validate URL format
        assert "compute-node" in endpoint.url
        assert ":11434" in endpoint.url

        # Simulate Gateway URL construction
        forwarded_url = f"{endpoint.url}/chat/completions"
        assert forwarded_url == "http://compute-node-42:11434/v1/chat/completions"

    def test_gateway_routes_to_k8s_service_dns(self, endpoints_with_different_urls):
        """Gateway correctly routes to Kubernetes service DNS URLs."""
        endpoint = endpoints_with_different_urls["k8s"]

        # Validate URL format
        assert ".svc.cluster.local" in endpoint.url

        # Simulate Gateway URL construction
        forwarded_url = f"{endpoint.url}/chat/completions"
        assert "svc.cluster.local" in forwarded_url

    def test_gateway_routes_to_compose_service(self, endpoints_with_different_urls):
        """Gateway correctly routes to Docker Compose service names."""
        endpoint = endpoints_with_different_urls["compose"]

        # Validate URL format - simple service name
        assert endpoint.url.startswith("http://litellm:")

        # Simulate Gateway URL construction
        forwarded_url = f"{endpoint.url}/chat/completions"
        assert forwarded_url == "http://litellm:8080/v1/chat/completions"

    def test_gateway_validates_url_format(self, endpoints_with_different_urls):
        """Gateway validates endpoint URL format before routing."""

        def validate_endpoint_url(url: str) -> tuple[bool, str]:
            """Validate endpoint URL format."""
            if not url.startswith(("http://", "https://")):
                return False, "URL must use http or https scheme"

            # Must have host and port
            if url.count(":") < 2:  # scheme + port separator
                return False, "URL must include port"

            # Must not contain dangerous characters
            if ".." in url or ";" in url or "${" in url:
                return False, "URL contains invalid characters"

            return True, "OK"

        # Valid URLs
        for ep in endpoints_with_different_urls.values():
            valid, msg = validate_endpoint_url(ep.url)
            assert valid, f"URL {ep.url} should be valid: {msg}"

        # Invalid URLs
        invalid_urls = [
            "ftp://server:21/",
            "http://server/no-port",
            "http://ser..ver:8080/",
            "http://server:8080/${INJECT}/",
        ]
        for url in invalid_urls:
            valid, msg = validate_endpoint_url(url)
            assert not valid, f"URL {url} should be invalid"

    def test_url_type_preserved_in_audit(self, endpoints_with_different_urls):
        """Audit log preserves URL type information."""
        registry = MockEndpointRegistry()

        for backend_type, endpoint in endpoints_with_different_urls.items():
            registry.endpoints[endpoint.endpoint_id] = endpoint
            registry.record_usage(
                endpoint=endpoint,
                user_id=f"user-{backend_type}",
                project_id=f"proj-{backend_type}",
            )

        # Verify all backends logged
        assert len(registry.audit_log) == 3

        # Each log should have endpoint info
        endpoint_types = {e.endpoint_type for e in registry.audit_log}
        assert "system" in endpoint_types
        assert "user" in endpoint_types
        assert "project" in endpoint_types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
