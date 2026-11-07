"""
Security tests for Extensibility features (X-301 through X-304).

Tests:
- X-301: Plugin sandbox escape prevention
- X-302: MCP SSRF prevention
- X-303: Project scoping for custom tools
- X-304: Rate limiting for user MCP servers

Note: These tests validate security contracts using mock implementations.
The actual UMRS implementation must satisfy these same contracts.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pytest

# =============================================================================
# Mock Implementations (representing the contract)
# =============================================================================


class PluginSourceType(Enum):
    """Types of plugin sources."""

    GIT_REPO = "git_repo"
    S3_WHEEL = "s3_wheel"
    LOCAL_PATH = "local_path"


class PluginStatus(Enum):
    """Status of a plugin execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class MockPluginSpec:
    """Mock plugin specification for testing contracts."""

    source_type: PluginSourceType
    source_uri: str
    entrypoint: str
    source_ref: str | None = None
    requirements: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    gpu_count: int = 0
    output_destinations: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        """Validate plugin specification."""
        if self.source_type == PluginSourceType.GIT_REPO:
            if not self.source_ref:
                raise ValueError("Git source requires source_ref (commit hash)")
            if not (self.source_uri.startswith("https://") or self.source_uri.startswith("git@")):
                raise ValueError("Git URI must start with https:// or git@")

        elif self.source_type == PluginSourceType.S3_WHEEL:
            if not self.source_uri.startswith("s3://"):
                raise ValueError("S3 source must start with s3://")
            if not self.source_uri.endswith(".whl"):
                raise ValueError("S3 source must be a wheel file (.whl)")

        elif self.source_type == PluginSourceType.LOCAL_PATH:
            if not self.source_uri.startswith("/"):
                raise ValueError("Local path must be absolute")

        if ":" not in self.entrypoint:
            raise ValueError("Entrypoint must be in module:function format")


APPROVED_OUTPUT_PREFIXES = ["s3://", "pgvector://", "umrs://"]


class MockPluginRunner:
    """Mock plugin runner for testing contracts."""

    def __init__(self):
        self.audit_log = None
        self.base_image = "plugin-runner:latest"

    async def validate_spec(self, spec: MockPluginSpec) -> None:
        """Validate plugin specification."""
        for dest in spec.output_destinations:
            if not any(dest.startswith(prefix) for prefix in APPROVED_OUTPUT_PREFIXES):
                raise ValueError(f"Output destination not approved: {dest}")

        if spec.gpu_count > 8:
            raise ValueError("Maximum 8 GPUs allowed per plugin")

    def _generate_container_env(self, spec: MockPluginSpec) -> dict[str, str]:
        """Generate environment variables for container."""
        env = {
            "PLUGIN_ENTRYPOINT": spec.entrypoint,
            "PLUGIN_SOURCE_TYPE": spec.source_type.value,
            "PLUGIN_SOURCE_URI": spec.source_uri,
        }

        if spec.source_ref:
            env["PLUGIN_SOURCE_REF"] = spec.source_ref

        for key, value in spec.env_vars.items():
            if not key.startswith("PLUGIN_"):
                env[f"USER_{key}"] = value

        return env


@dataclass
class MockToolDefinition:
    """MCP tool definition."""

    name: str
    description: str = ""
    category: str = "compute"


@dataclass
class MockMCPServerInfo:
    """MCP server info."""

    id: str
    name: str
    url: str
    tools: list[MockToolDefinition] = field(default_factory=list)
    owner_project_id: str | None = None
    status: str = "unknown"
    health_check_passed: bool = False


class MockMCPServerRegistry:
    """Mock MCP server registry for contract testing."""

    def __init__(self):
        self._servers: dict[str, MockMCPServerInfo] = {}

    async def list_servers(
        self,
        project_id: str | None = None,
        healthy_only: bool = False,
    ) -> list[MockMCPServerInfo]:
        """List servers visible to a project."""
        servers = list(self._servers.values())

        if project_id:
            servers = [
                s for s in servers if s.owner_project_id == project_id or s.owner_project_id is None
            ]

        if healthy_only:
            servers = [s for s in servers if s.health_check_passed]

        return servers

    async def get_server(self, server_id: str) -> MockMCPServerInfo | None:
        return self._servers.get(server_id)

    async def get_available_tools(
        self,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get tools available to a project."""
        result = []
        for server in await self.list_servers(project_id=project_id, healthy_only=True):
            for tool in server.tools:
                result.append(
                    {
                        "server_id": server.id,
                        "server_name": server.name,
                        "tool_name": tool.name,
                    }
                )
        return result


# =============================================================================
# X-301: Plugin Sandbox Escape Prevention
# =============================================================================


class TestPluginSandboxEscapePrevention:
    """Test plugin sandbox isolation."""

    def test_plugin_spec_rejects_host_path_in_production(self):
        """Plugin local paths should be rejected in production mode."""
        with pytest.raises(ValueError, match="Local path must be absolute"):
            MockPluginSpec(
                source_type=PluginSourceType.LOCAL_PATH,
                source_uri="relative/path",  # Not absolute
                entrypoint="module:func",
            )

    def test_plugin_spec_validates_entrypoint_format(self):
        """Entrypoint must be in module:function format."""
        with pytest.raises(ValueError, match="module:function format"):
            MockPluginSpec(
                source_type=PluginSourceType.S3_WHEEL,
                source_uri="s3://bucket/plugin.whl",
                entrypoint="invalid_entrypoint",  # Missing colon
            )

    def test_plugin_spec_rejects_invalid_s3_uri(self):
        """S3 wheel source must be valid S3 URI."""
        with pytest.raises(ValueError, match="start with s3://"):
            MockPluginSpec(
                source_type=PluginSourceType.S3_WHEEL,
                source_uri="http://evil.com/plugin.whl",
                entrypoint="module:func",
            )

    def test_plugin_spec_rejects_non_wheel_s3(self):
        """S3 source must be a wheel file."""
        with pytest.raises(ValueError, match="wheel file"):
            MockPluginSpec(
                source_type=PluginSourceType.S3_WHEEL,
                source_uri="s3://bucket/plugin.tar.gz",
                entrypoint="module:func",
            )

    def test_plugin_spec_requires_git_commit(self):
        """Git source must specify commit hash."""
        with pytest.raises(ValueError, match="requires source_ref"):
            MockPluginSpec(
                source_type=PluginSourceType.GIT_REPO,
                source_uri="https://github.com/org/repo.git",
                entrypoint="module:func",
                # Missing source_ref
            )

    @pytest.mark.asyncio
    async def test_plugin_runner_rejects_unapproved_output_destinations(self):
        """Output destinations must be from approved list."""
        spec = MockPluginSpec(
            source_type=PluginSourceType.S3_WHEEL,
            source_uri="s3://bucket/plugin.whl",
            entrypoint="module:func",
            output_destinations=["http://evil.com/exfiltrate"],
        )

        runner = MockPluginRunner()

        with pytest.raises(ValueError, match="not approved"):
            await runner.validate_spec(spec)

    def test_plugin_env_vars_cannot_override_system(self):
        """User env vars cannot override PLUGIN_ prefixed vars."""
        spec = MockPluginSpec(
            source_type=PluginSourceType.S3_WHEEL,
            source_uri="s3://bucket/plugin.whl",
            entrypoint="module:func",
            env_vars={
                "PLUGIN_ENTRYPOINT": "malicious:code",  # Attempt override
                "MY_VAR": "safe_value",
            },
        )

        runner = MockPluginRunner()
        env = runner._generate_container_env(spec)

        # System var should NOT be overridden
        assert env["PLUGIN_ENTRYPOINT"] == "module:func"
        # User var should be prefixed
        assert env["USER_MY_VAR"] == "safe_value"
        assert "MY_VAR" not in env

    @pytest.mark.asyncio
    async def test_plugin_gpu_limit_enforced(self):
        """Plugin cannot request more than 8 GPUs."""
        spec = MockPluginSpec(
            source_type=PluginSourceType.S3_WHEEL,
            source_uri="s3://bucket/plugin.whl",
            entrypoint="module:func",
            gpu_count=16,  # Excessive
        )

        runner = MockPluginRunner()

        with pytest.raises(ValueError, match="Maximum 8 GPUs"):
            await runner.validate_spec(spec)


# =============================================================================
# X-302: MCP SSRF Prevention
# =============================================================================


class TestMCPSSRFPrevention:
    """Test MCP server SSRF protection."""

    def test_mcp_registration_rejects_localhost_urls(self):
        """MCP servers cannot register localhost URLs."""
        # Simulated validation
        blocked_urls = [
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://0.0.0.0:8080",
            "http://[::1]:8080",
        ]

        def validate_url(url: str) -> bool:
            """URL validation that blocks local addresses."""
            from urllib.parse import urlparse

            parsed = urlparse(url)
            hostname = parsed.hostname or ""

            blocked_hosts = [
                "localhost",
                "127.0.0.1",
                "0.0.0.0",
                "::1",
            ]

            # Check for blocked hosts
            if hostname.lower() in blocked_hosts:
                return False

            # Check for link-local/private ranges
            if hostname.startswith("10.") or hostname.startswith("192.168."):
                return False
            if hostname.startswith("172."):
                parts = hostname.split(".")
                if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
                    return False

            return True

        for url in blocked_urls:
            assert not validate_url(url), f"Should block: {url}"

    def test_mcp_registration_rejects_internal_k8s_services(self):
        """MCP servers cannot target internal K8s services."""
        internal_urls = [
            "http://kubernetes.default.svc",
            "http://metadata.google.internal",
            "http://169.254.169.254",  # AWS/GCP metadata
            "http://umrs.rivault.svc.cluster.local",
        ]

        def validate_url(url: str) -> bool:
            """URL validation that blocks internal services."""
            from urllib.parse import urlparse

            parsed = urlparse(url)
            hostname = parsed.hostname or ""

            # Block metadata services
            if "169.254" in hostname:
                return False
            if "metadata" in hostname.lower():
                return False

            # Block K8s internal
            if ".svc" in hostname or ".cluster.local" in hostname:
                return False
            return "kubernetes" not in hostname.lower()

        for url in internal_urls:
            assert not validate_url(url), f"Should block: {url}"

    def test_mcp_tool_invocation_url_allowlist(self):
        """Tool invocations should only reach allowed domains."""
        allowed_domains = [
            "api.github.com",
            "api.example.com",
        ]

        def check_url_allowed(url: str, allowlist: list) -> bool:
            """Check if URL domain is in allowlist."""
            from urllib.parse import urlparse

            parsed = urlparse(url)
            hostname = parsed.hostname or ""

            return any(
                hostname == domain or hostname.endswith(f".{domain}") for domain in allowlist
            )

        # Allowed
        assert check_url_allowed("https://api.github.com/users", allowed_domains)
        assert check_url_allowed("https://api.example.com/data", allowed_domains)

        # Blocked
        assert not check_url_allowed("https://evil.com/steal", allowed_domains)
        assert not check_url_allowed("http://internal.corp/secrets", allowed_domains)

    def test_mcp_response_size_limit(self):
        """MCP tool responses should be size-limited."""
        MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB

        def truncate_response(data: bytes, max_size: int = MAX_RESPONSE_SIZE) -> bytes:
            """Truncate response to max size."""
            if len(data) > max_size:
                return data[:max_size]
            return data

        large_response = b"x" * (MAX_RESPONSE_SIZE + 1000)
        truncated = truncate_response(large_response)

        assert len(truncated) == MAX_RESPONSE_SIZE
        assert len(truncated) < len(large_response)


# =============================================================================
# X-303: Project Scoping for Custom Tools
# =============================================================================


class TestProjectScopingForCustomTools:
    """Test that custom tools are properly project-scoped."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock MCP registry."""
        registry = MockMCPServerRegistry()

        # Register servers for different projects
        server_a = MockMCPServerInfo(
            id="mcp-server-a",
            name="project-a-server",
            url="http://server-a:8080",
            tools=[
                MockToolDefinition(name="tool_a1", description="Tool for project A"),
                MockToolDefinition(name="tool_a2", description="Another tool for A"),
            ],
            owner_project_id="project-a",
            health_check_passed=True,
        )

        server_b = MockMCPServerInfo(
            id="mcp-server-b",
            name="project-b-server",
            url="http://server-b:8080",
            tools=[
                MockToolDefinition(name="tool_b1", description="Tool for project B"),
            ],
            owner_project_id="project-b",
            health_check_passed=True,
        )

        registry._servers["mcp-server-a"] = server_a
        registry._servers["mcp-server-b"] = server_b

        return registry

    @pytest.mark.asyncio
    async def test_project_can_only_see_own_servers(self, mock_registry):
        """Projects should only see their own servers."""
        # Project A sees only its server
        servers_a = await mock_registry.list_servers(project_id="project-a")
        server_names = [s.name for s in servers_a]

        assert "project-a-server" in server_names
        # project-b-server should not be visible to project-a
        assert all(s.owner_project_id in ("project-a", None) for s in servers_a)

    @pytest.mark.asyncio
    async def test_project_tools_are_scoped(self, mock_registry):
        """Tools available to a project should respect ownership."""
        # Get tools for project A
        tools = await mock_registry.get_available_tools(project_id="project-a")

        _tool_names = [t["tool_name"] for t in tools]

        # Should have access to project A's tools
        for tool in tools:
            # Each tool should come from an allowed server
            server = await mock_registry.get_server(tool["server_id"])
            assert server.owner_project_id in ("project-a", None)

    def test_cross_project_tool_access_denied(self):
        """User from project A cannot invoke project B's tools."""

        def check_tool_access(
            tool_owner_project: str,
            requester_project: str,
        ) -> bool:
            """Check if requester can access tool."""
            if tool_owner_project is None:
                # Platform tool, accessible to all
                return True
            return tool_owner_project == requester_project

        # Same project - allowed
        assert check_tool_access("project-a", "project-a")

        # Different project - denied
        assert not check_tool_access("project-a", "project-b")

        # Platform tool - allowed
        assert check_tool_access(None, "project-a")


# =============================================================================
# X-304: Rate Limiting for User MCP Servers
# =============================================================================


class TestMCPRateLimiting:
    """Test rate limiting for user MCP servers."""

    def test_rate_limit_configuration_per_server(self):
        """Each MCP server can have its own rate limit."""
        server_limits = {
            "platform-server": {"rps": 100, "burst": 200},
            "user-server-1": {"rps": 10, "burst": 20},
            "user-server-2": {"rps": 5, "burst": 10},
        }

        def get_rate_limit(server_id: str) -> dict:
            """Get rate limit for a server."""
            default = {"rps": 10, "burst": 20}
            return server_limits.get(server_id, default)

        # Platform servers get higher limits
        assert get_rate_limit("platform-server")["rps"] == 100

        # User servers get lower limits
        assert get_rate_limit("user-server-1")["rps"] == 10

    def test_rate_limiter_enforces_limits(self):
        """Rate limiter should reject requests over limit."""
        from time import time

        class SimpleRateLimiter:
            """Simple token bucket rate limiter."""

            def __init__(self, rps: int, burst: int):
                self.rps = rps
                self.burst = burst
                self.tokens = burst
                self.last_update = time()

            def allow(self) -> bool:
                """Check if request is allowed."""
                now = time()
                elapsed = now - self.last_update
                self.last_update = now

                # Add tokens based on elapsed time
                self.tokens = min(self.burst, self.tokens + elapsed * self.rps)

                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
                return False

        limiter = SimpleRateLimiter(rps=10, burst=5)

        # First 5 requests should succeed (burst)
        results = [limiter.allow() for _ in range(5)]
        assert all(results)

        # 6th request should fail (no tokens left)
        assert not limiter.allow()

    def test_rate_limit_by_project(self):
        """Rate limits should be tracked per project."""
        from collections import defaultdict

        class ProjectRateLimiter:
            """Rate limiter with per-project tracking."""

            def __init__(self, default_rps: int = 10):
                self.default_rps = default_rps
                self.project_counters = defaultdict(int)
                self.project_limits = {}

            def set_limit(self, project_id: str, rps: int):
                self.project_limits[project_id] = rps

            def check_limit(self, project_id: str) -> bool:
                """Check if project is within rate limit."""
                limit = self.project_limits.get(project_id, self.default_rps)
                current = self.project_counters[project_id]
                return current < limit

            def record_request(self, project_id: str):
                """Record a request for a project."""
                self.project_counters[project_id] += 1

            def reset(self):
                """Reset all counters (called periodically)."""
                self.project_counters.clear()

        limiter = ProjectRateLimiter(default_rps=5)
        limiter.set_limit("premium-project", 100)

        # Regular project gets default limit
        for _i in range(5):
            assert limiter.check_limit("regular-project")
            limiter.record_request("regular-project")

        # 6th request exceeds limit
        assert not limiter.check_limit("regular-project")

        # Premium project still has room
        assert limiter.check_limit("premium-project")

    def test_rate_limit_response_includes_headers(self):
        """Rate limit info should be in response headers."""

        def generate_rate_limit_headers(
            limit: int,
            remaining: int,
            reset_at: int,
        ) -> dict:
            """Generate rate limit headers."""
            return {
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(reset_at),
            }

        headers = generate_rate_limit_headers(
            limit=100,
            remaining=95,
            reset_at=1700000000,
        )

        assert headers["X-RateLimit-Limit"] == "100"
        assert headers["X-RateLimit-Remaining"] == "95"
        assert "X-RateLimit-Reset" in headers

    def test_rate_limit_429_response(self):
        """Exceeding rate limit should return 429."""
        from fastapi import HTTPException

        def check_rate_limit_or_reject(allowed: bool) -> None:
            """Check rate limit and raise 429 if exceeded."""
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Try again later.",
                    headers={
                        "Retry-After": "60",
                    },
                )

        # Should pass without exception
        check_rate_limit_or_reject(True)

        # Should raise 429
        with pytest.raises(HTTPException) as exc_info:
            check_rate_limit_or_reject(False)

        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers
