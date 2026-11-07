"""Capability-Based Tool Controls (T-234 to T-239).

Phase 5 of Security Hardening: Capability-Based Tool Access Control.

This module provides:
- ToolCapability enum for fine-grained permissions
- Per-MCP-server whitelist enforcement
- Tool invocation audit logging
- Rate limiting per tool class
- Dangerous operation confirmation flow
- Context-based capability scoping

Attack Model:
Adversaries can manipulate LLM to:
1. Invoke tools beyond the user's authorization level
2. Abuse powerful tools (shell, file system, network)
3. Exfiltrate data through tool side channels
4. Escalate privileges via chained tool invocations

This module enforces capability checks before tool execution.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, Flag, auto
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

try:
    from enum import KEEP as _FLAG_KEEP
except ImportError:  # Python 3.10 does not expose FlagBoundary constants.
    _TOOL_CAPABILITY_BOUNDARY = {}
else:
    _TOOL_CAPABILITY_BOUNDARY = {"boundary": _FLAG_KEEP}


# =============================================================================
# Capabilities
# =============================================================================


class ToolCapability(Flag, **_TOOL_CAPABILITY_BOUNDARY):
    """Fine-grained tool capabilities.

    Capabilities follow the principle of least privilege.
    Users are granted minimal capabilities needed for their task.
    """

    NONE = 0

    # Read operations
    READ_LOCAL_FILE = auto()  # Read files from local filesystem
    READ_REMOTE_FILE = auto()  # Read files from remote URLs
    READ_DATABASE = auto()  # Read from databases
    READ_ENVIRONMENT = auto()  # Read environment variables

    # Write operations
    WRITE_LOCAL_FILE = auto()  # Write to local filesystem
    WRITE_DATABASE = auto()  # Write to databases
    WRITE_REMOTE = auto()  # Make external network requests

    # Execute operations
    EXECUTE_SHELL = auto()  # Execute shell commands
    EXECUTE_CODE = auto()  # Execute code (Python, etc.)
    EXECUTE_SQL = auto()  # Execute SQL queries

    # Administrative operations
    MODIFY_SETTINGS = auto()  # Modify configuration/settings
    MANAGE_USERS = auto()  # User management operations
    MANAGE_SECRETS = auto()  # Secret/credential management

    # AI/ML operations
    INVOKE_LLM = auto()  # Make LLM API calls
    EMBED_CONTENT = auto()  # Generate embeddings
    TRAIN_MODEL = auto()  # Train ML models

    # Job operations
    SUBMIT_JOB = auto()  # Submit compute jobs
    CANCEL_JOB = auto()  # Cancel running jobs
    VIEW_JOB = auto()  # View job status

    # Knowledge verification
    KNOWLEDGE_VERIFY = auto()  # Run knowledge verification checks

    # Combination presets
    READ_ONLY = READ_LOCAL_FILE | READ_REMOTE_FILE | READ_DATABASE
    STANDARD_USER = (
        READ_ONLY | WRITE_LOCAL_FILE | INVOKE_LLM | EMBED_CONTENT | VIEW_JOB | KNOWLEDGE_VERIFY
    )
    POWER_USER = STANDARD_USER | EXECUTE_CODE | SUBMIT_JOB | CANCEL_JOB
    ADMIN = ~NONE  # All capabilities


class DangerLevel(str, Enum):
    """Danger level of operations."""

    SAFE = "safe"  # Safe, no confirmation needed
    CAUTION = "caution"  # Warn user but allow
    DANGEROUS = "dangerous"  # Require explicit confirmation
    FORBIDDEN = "forbidden"  # Never allow


# =============================================================================
# Tool Registration
# =============================================================================


@dataclass
class ToolMetadata:
    """Metadata about a registered tool."""

    name: str
    server_name: str
    required_capabilities: ToolCapability
    danger_level: DangerLevel = DangerLevel.SAFE
    description: str = ""
    rate_limit_per_minute: int = 60
    requires_confirmation: bool = False

    # Audit settings
    log_inputs: bool = False  # Be careful with sensitive data
    log_outputs: bool = False

    # Additional metadata
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolInvocationRecord:
    """Record of a tool invocation for audit."""

    tool_name: str
    server_name: str
    user_id: str
    session_id: str
    capabilities_required: ToolCapability
    capabilities_granted: ToolCapability
    allowed: bool
    denied_reason: Optional[str]
    timestamp: datetime
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "server_name": self.server_name,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "capabilities_required": str(self.capabilities_required),
            "capabilities_granted": str(self.capabilities_granted),
            "allowed": self.allowed,
            "denied_reason": self.denied_reason,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
        }


# =============================================================================
# Security Context
# =============================================================================


@dataclass
class SecurityContext:
    """Security context for tool execution.

    Tracks user identity, granted capabilities, and session state.
    """

    user_id: str
    session_id: str
    granted_capabilities: ToolCapability

    # Rate limiting state
    invocation_counts: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    # Confirmation state
    pending_confirmations: dict[str, datetime] = field(default_factory=dict)

    # Audit trail
    invocations: list[ToolInvocationRecord] = field(default_factory=list)

    def has_capability(self, required: ToolCapability) -> bool:
        """Check if context has required capabilities."""
        return (self.granted_capabilities & required) == required

    def check_rate_limit(self, tool_name: str, limit_per_minute: int) -> bool:
        """Check if tool invocation is within rate limit."""
        now = time.time()
        minute_ago = now - 60

        # Clean old entries
        self.invocation_counts[tool_name] = [
            t for t in self.invocation_counts[tool_name] if t > minute_ago
        ]

        # Check limit
        if len(self.invocation_counts[tool_name]) >= limit_per_minute:
            return False

        # Record invocation
        self.invocation_counts[tool_name].append(now)
        return True

    def request_confirmation(self, tool_name: str) -> str:
        """Generate confirmation token for dangerous operation."""
        import secrets

        token = secrets.token_urlsafe(16)
        self.pending_confirmations[f"{tool_name}:{token}"] = datetime.now(timezone.utc)
        return token

    def confirm_operation(self, tool_name: str, token: str) -> bool:
        """Verify confirmation token."""
        key = f"{tool_name}:{token}"
        if key not in self.pending_confirmations:
            return False

        # Check expiry (5 minutes)
        issued = self.pending_confirmations[key]
        if (datetime.now(timezone.utc) - issued).total_seconds() > 300:
            del self.pending_confirmations[key]
            return False

        del self.pending_confirmations[key]
        return True


# =============================================================================
# Capability Enforcer
# =============================================================================


class CapabilityEnforcer:
    """Enforces capability-based access control for tools.

    Example:
        enforcer = CapabilityEnforcer()

        # Register tools
        enforcer.register_tool(ToolMetadata(
            name="read_file",
            server_name="filesystem",
            required_capabilities=ToolCapability.READ_LOCAL_FILE,
        ))

        # Check access
        result = enforcer.check_access(ctx, "read_file")
        if not result.allowed:
            raise PermissionError(result.denied_reason)
    """

    def __init__(self):
        """Initialize enforcer."""
        self._tools: dict[str, ToolMetadata] = {}
        self._server_tools: dict[str, list[str]] = defaultdict(list)
        self._audit_callback: Optional[Callable[[ToolInvocationRecord], None]] = None

    def register_tool(self, metadata: ToolMetadata) -> None:
        """Register a tool with its metadata.

        Args:
            metadata: Tool metadata including capabilities
        """
        key = f"{metadata.server_name}:{metadata.name}"
        self._tools[key] = metadata
        self._server_tools[metadata.server_name].append(metadata.name)

        logger.debug(f"Registered tool {key} requiring {metadata.required_capabilities}")

    def register_audit_callback(
        self,
        callback: Callable[[ToolInvocationRecord], None],
    ) -> None:
        """Register callback for audit logging.

        Args:
            callback: Function to call for each invocation
        """
        self._audit_callback = callback

    def get_tool(self, server_name: str, tool_name: str) -> Optional[ToolMetadata]:
        """Get tool metadata."""
        key = f"{server_name}:{tool_name}"
        return self._tools.get(key)

    def list_tools_for_server(self, server_name: str) -> list[ToolMetadata]:
        """Get all tools for a server."""
        return [
            self._tools[f"{server_name}:{name}"] for name in self._server_tools.get(server_name, [])
        ]

    def list_allowed_tools(self, ctx: SecurityContext) -> list[ToolMetadata]:
        """Get tools the context is allowed to use.

        Args:
            ctx: Security context

        Returns:
            List of allowed tools
        """
        allowed = []
        for metadata in self._tools.values():
            if ctx.has_capability(metadata.required_capabilities):
                allowed.append(metadata)
        return allowed

    def check_access(
        self,
        ctx: SecurityContext,
        server_name: str,
        tool_name: str,
        confirmation_token: Optional[str] = None,
    ) -> ToolInvocationRecord:
        """Check if context can invoke a tool.

        Args:
            ctx: Security context
            server_name: MCP server name
            tool_name: Tool name
            confirmation_token: Token for dangerous operations

        Returns:
            Invocation record (check allowed field)
        """
        metadata = self.get_tool(server_name, tool_name)
        if metadata is None:
            return self._record_access(
                ctx,
                server_name,
                tool_name,
                ToolCapability.NONE,
                False,
                f"Unknown tool: {server_name}:{tool_name}",
            )

        if metadata.danger_level == DangerLevel.FORBIDDEN:
            return self._record_metadata_access(ctx, metadata, False, "Tool is forbidden")

        if not ctx.has_capability(metadata.required_capabilities):
            return self._missing_capability_record(ctx, metadata)

        if not ctx.check_rate_limit(f"{server_name}:{tool_name}", metadata.rate_limit_per_minute):
            return self._record_metadata_access(
                ctx,
                metadata,
                False,
                f"Rate limit exceeded ({metadata.rate_limit_per_minute}/min)",
            )

        confirmation = self._check_confirmation(ctx, metadata, confirmation_token)
        if confirmation is not None:
            return confirmation

        return self._record_metadata_access(ctx, metadata, True, None)

    def _missing_capability_record(
        self,
        ctx: SecurityContext,
        metadata: ToolMetadata,
    ) -> ToolInvocationRecord:
        missing = _missing_capabilities(
            metadata.required_capabilities,
            ctx.granted_capabilities,
        )
        return self._record_metadata_access(
            ctx,
            metadata,
            False,
            f"Missing capabilities: {missing}",
        )

    def _check_confirmation(
        self,
        ctx: SecurityContext,
        metadata: ToolMetadata,
        confirmation_token: Optional[str],
    ) -> Optional[ToolInvocationRecord]:
        if metadata.danger_level != DangerLevel.DANGEROUS:
            return None
        key = f"{metadata.server_name}:{metadata.name}"
        if confirmation_token is None:
            token = ctx.request_confirmation(key)
            return self._record_metadata_access(
                ctx,
                metadata,
                False,
                f"Confirmation required. Token: {token}",
            )
        if not ctx.confirm_operation(key, confirmation_token):
            return self._record_metadata_access(
                ctx,
                metadata,
                False,
                "Invalid or expired confirmation token",
            )
        return None

    def _record_metadata_access(
        self,
        ctx: SecurityContext,
        metadata: ToolMetadata,
        allowed: bool,
        denied_reason: Optional[str],
    ) -> ToolInvocationRecord:
        return self._record_access(
            ctx,
            metadata.server_name,
            metadata.name,
            metadata.required_capabilities,
            allowed,
            denied_reason,
        )

    def _record_access(
        self,
        ctx: SecurityContext,
        server_name: str,
        tool_name: str,
        required_capabilities: ToolCapability,
        allowed: bool,
        denied_reason: Optional[str],
    ) -> ToolInvocationRecord:
        record = ToolInvocationRecord(
            tool_name=tool_name,
            server_name=server_name,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            capabilities_required=required_capabilities,
            capabilities_granted=ctx.granted_capabilities,
            allowed=allowed,
            denied_reason=denied_reason,
            timestamp=datetime.now(timezone.utc),
        )
        self._audit(ctx, record)
        return record

    def _audit(self, ctx: SecurityContext, record: ToolInvocationRecord) -> None:
        """Record invocation for audit."""
        ctx.invocations.append(record)

        if self._audit_callback:
            try:
                self._audit_callback(record)
            except Exception as e:
                logger.error(f"Audit callback failed: {e}")

        # Log significant events
        if not record.allowed:
            logger.warning(
                f"Tool access denied: {record.server_name}:{record.tool_name}",
                extra={
                    "user_id": record.user_id,
                    "reason": record.denied_reason,
                },
            )


# =============================================================================
# Decorator for Tool Functions
# =============================================================================


F = TypeVar("F", bound=Callable[..., Any])


def require_capability(
    capability: ToolCapability,
    server_name: str = "default",
    danger_level: DangerLevel = DangerLevel.SAFE,
) -> Callable[[F], F]:
    """Decorator to enforce capability checks on tool functions.

    Example:
        @require_capability(ToolCapability.READ_LOCAL_FILE, server_name="filesystem")
        async def read_file(ctx: SecurityContext, path: str) -> str:
            ...

    Args:
        capability: Required capability
        server_name: Server name for the tool
        danger_level: Danger level of the operation

    Returns:
        Decorated function
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(ctx: SecurityContext, *args, **kwargs):
            enforcer = get_capability_enforcer()

            # Check access
            result = enforcer.check_access(
                ctx,
                server_name,
                func.__name__,
                kwargs.pop("confirmation_token", None),
            )

            if not result.allowed:
                raise CapabilityError(result.denied_reason)

            # Execute
            start_time = time.time()
            try:
                return await func(ctx, *args, **kwargs)
            finally:
                result.duration_ms = int((time.time() - start_time) * 1000)

        return wrapper  # type: ignore

    return decorator


class CapabilityError(Exception):
    """Raised when capability check fails."""


def _missing_capabilities(
    required: ToolCapability,
    granted: ToolCapability,
) -> ToolCapability:
    return ToolCapability(required.value & ~granted.value)


# =============================================================================
# Server Whitelist
# =============================================================================


@dataclass
class ServerWhitelist:
    """Per-MCP-server whitelist configuration."""

    server_name: str
    enabled: bool = True
    allowed_tools: Optional[set[str]] = None  # None = all tools allowed
    blocked_tools: set[str] = field(default_factory=set)
    max_capabilities: ToolCapability = ToolCapability.STANDARD_USER

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is allowed on this server."""
        if not self.enabled:
            return False

        if tool_name in self.blocked_tools:
            return False

        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return False

        return True


class ServerWhitelistManager:
    """Manages server whitelists."""

    def __init__(self):
        self._whitelists: dict[str, ServerWhitelist] = {}

    def set_whitelist(self, whitelist: ServerWhitelist) -> None:
        """Set whitelist for a server."""
        self._whitelists[whitelist.server_name] = whitelist

    def get_whitelist(self, server_name: str) -> Optional[ServerWhitelist]:
        """Get whitelist for a server."""
        return self._whitelists.get(server_name)

    def is_server_enabled(self, server_name: str) -> bool:
        """Check if server is enabled."""
        whitelist = self._whitelists.get(server_name)
        return whitelist.enabled if whitelist else True  # Default: enabled

    def is_tool_allowed(self, server_name: str, tool_name: str) -> bool:
        """Check if tool is allowed on server."""
        whitelist = self._whitelists.get(server_name)
        if whitelist is None:
            return True  # No whitelist = all allowed
        return whitelist.is_tool_allowed(tool_name)


# =============================================================================
# Singleton Instances
# =============================================================================


_capability_enforcer: Optional[CapabilityEnforcer] = None
_whitelist_manager: Optional[ServerWhitelistManager] = None


def get_capability_enforcer() -> CapabilityEnforcer:
    """Get global capability enforcer instance."""
    global _capability_enforcer
    if _capability_enforcer is None:
        _capability_enforcer = CapabilityEnforcer()
    return _capability_enforcer


def get_whitelist_manager() -> ServerWhitelistManager:
    """Get global whitelist manager instance."""
    global _whitelist_manager
    if _whitelist_manager is None:
        _whitelist_manager = ServerWhitelistManager()
    return _whitelist_manager


# =============================================================================
# Convenience Functions
# =============================================================================


def create_context(
    user_id: str,
    session_id: str,
    capabilities: ToolCapability = ToolCapability.STANDARD_USER,
) -> SecurityContext:
    """Create a new security context.

    Args:
        user_id: User identifier
        session_id: Session identifier
        capabilities: Granted capabilities

    Returns:
        New security context
    """
    return SecurityContext(
        user_id=user_id,
        session_id=session_id,
        granted_capabilities=capabilities,
    )


def register_standard_tools(enforcer: Optional[CapabilityEnforcer] = None) -> None:
    """Register standard MCP tools with their capabilities.

    Call this at server startup to register built-in tools.
    """
    if enforcer is None:
        enforcer = get_capability_enforcer()

    for name, server, capability, danger, confirm, description, rate_limit in _STANDARD_TOOL_SPECS:
        enforcer.register_tool(
            ToolMetadata(
                name=name,
                server_name=server,
                required_capabilities=capability,
                danger_level=danger,
                requires_confirmation=confirm,
                description=description,
                rate_limit_per_minute=rate_limit,
            )
        )


_STANDARD_TOOL_SPECS = (
    (
        "read_file",
        "filesystem",
        ToolCapability.READ_LOCAL_FILE,
        DangerLevel.SAFE,
        False,
        "Read a file from local filesystem",
        60,
    ),
    (
        "write_file",
        "filesystem",
        ToolCapability.WRITE_LOCAL_FILE,
        DangerLevel.CAUTION,
        False,
        "Write to a file on local filesystem",
        60,
    ),
    (
        "run_command",
        "shell",
        ToolCapability.EXECUTE_SHELL,
        DangerLevel.DANGEROUS,
        True,
        "Execute a shell command",
        10,
    ),
    (
        "query",
        "database",
        ToolCapability.EXECUTE_SQL,
        DangerLevel.SAFE,
        False,
        "Execute a database query",
        60,
    ),
    (
        "chat_completion",
        "llm",
        ToolCapability.INVOKE_LLM,
        DangerLevel.SAFE,
        False,
        "Generate chat completion",
        60,
    ),
    (
        "embed",
        "embedding",
        ToolCapability.EMBED_CONTENT,
        DangerLevel.SAFE,
        False,
        "Generate embeddings",
        60,
    ),
    (
        "submit_job",
        "umrs",
        ToolCapability.SUBMIT_JOB,
        DangerLevel.SAFE,
        False,
        "Submit a compute job",
        60,
    ),
    (
        "cancel_job",
        "umrs",
        ToolCapability.CANCEL_JOB,
        DangerLevel.CAUTION,
        False,
        "Cancel a running job",
        60,
    ),
    (
        "get_job_status",
        "umrs",
        ToolCapability.VIEW_JOB,
        DangerLevel.SAFE,
        False,
        "Get job status",
        60,
    ),
)
