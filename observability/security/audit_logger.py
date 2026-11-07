"""Security Audit Logger (T-254 to T-255).

Phase 8 of Security Hardening: Secure Observability.

This module provides:
- Security event logging with structured output
- Access controls for audit logs
- Retention policies for security events
- Tamper-evident log signing

Attack Model:
Attackers may try to:
1. Delete or modify audit logs to hide their tracks
2. Flood logs to cause denial of service
3. Access audit logs to learn about security measures

Defense:
- Append-only log storage
- Rate limiting on log writes
- Access controls on log reads
- Optional cryptographic signing
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional
from collections import deque

logger = logging.getLogger(__name__)


# =============================================================================
# Types
# =============================================================================


class AuditEventType(str, Enum):
    """Types of security audit events."""

    # Authentication
    AUTH_SUCCESS = "auth.success"
    AUTH_FAILURE = "auth.failure"
    AUTH_LOGOUT = "auth.logout"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"

    # Authorization
    AUTHZ_GRANTED = "authz.granted"
    AUTHZ_DENIED = "authz.denied"

    # Policy violations
    POLICY_VIOLATION = "policy.violation"
    POLICY_BLOCK = "policy.block"
    POLICY_WARN = "policy.warn"

    # Data access
    DATA_READ = "data.read"
    DATA_WRITE = "data.write"
    DATA_DELETE = "data.delete"
    DATA_EXPORT = "data.export"

    # Admin operations
    ADMIN_CONFIG_CHANGE = "admin.config_change"
    ADMIN_USER_CREATE = "admin.user_create"
    ADMIN_USER_DELETE = "admin.user_delete"
    ADMIN_ROLE_CHANGE = "admin.role_change"

    # Security events
    SEC_INJECTION_ATTEMPT = "sec.injection_attempt"
    SEC_RATE_LIMIT = "sec.rate_limit"
    SEC_SUSPICIOUS_ACTIVITY = "sec.suspicious_activity"

    # System events
    SYS_STARTUP = "sys.startup"
    SYS_SHUTDOWN = "sys.shutdown"
    SYS_ERROR = "sys.error"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""

    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """A security audit event.

    Immutable record of a security-relevant action.
    """

    event_id: str
    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: datetime

    # Actor
    actor_id: Optional[str] = None
    actor_type: str = "user"  # user, service, system

    # Resource
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None

    # Context
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None

    # Details
    action: Optional[str] = None
    outcome: str = "success"  # success, failure, blocked
    details: dict[str, Any] = field(default_factory=dict)

    # Integrity
    signature: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "timestamp": self.timestamp.isoformat(),
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "action": self.action,
            "outcome": self.outcome,
            "details": self.details,
            "signature": self.signature,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# =============================================================================
# Event ID Generator
# =============================================================================


class EventIdGenerator:
    """Thread-safe event ID generator."""

    def __init__(self, prefix: str = "evt"):
        self._prefix = prefix
        self._counter = 0
        self._lock = threading.Lock()

    def generate(self) -> str:
        """Generate unique event ID."""
        with self._lock:
            self._counter += 1
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            return f"{self._prefix}_{timestamp}_{self._counter:06d}"


# =============================================================================
# Log Signing
# =============================================================================


class LogSigner:
    """HMAC-based log signing for tamper detection.

    Signs log entries with HMAC-SHA256 to detect tampering.
    """

    def __init__(self, secret_key: Optional[bytes] = None):
        """Initialize signer.

        Args:
            secret_key: HMAC secret key (generated if not provided)
        """
        self._key = secret_key or os.urandom(32)

    def sign(self, event: AuditEvent) -> str:
        """Sign an audit event.

        Args:
            event: Event to sign

        Returns:
            HMAC signature (hex)
        """
        # Create canonical representation (excluding signature)
        data = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "actor_id": event.actor_id,
            "resource_id": event.resource_id,
            "action": event.action,
            "outcome": event.outcome,
        }

        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))

        signature = hmac.new(self._key, canonical.encode(), hashlib.sha256).hexdigest()

        return signature

    def verify(self, event: AuditEvent) -> bool:
        """Verify event signature.

        Args:
            event: Event to verify

        Returns:
            True if signature valid
        """
        if not event.signature:
            return False

        expected = self.sign(event)
        return hmac.compare_digest(event.signature, expected)


# =============================================================================
# Retention Policy
# =============================================================================


@dataclass
class RetentionPolicy:
    """Retention policy for audit logs."""

    # Default retention
    default_days: int = 90

    # Per-severity retention
    severity_retention: dict[AuditSeverity, int] = field(
        default_factory=lambda: {
            AuditSeverity.INFO: 30,
            AuditSeverity.NOTICE: 60,
            AuditSeverity.WARNING: 90,
            AuditSeverity.ERROR: 365,
            AuditSeverity.CRITICAL: 365 * 2,  # 2 years
        }
    )

    # Per-event-type retention overrides
    event_type_retention: dict[AuditEventType, int] = field(default_factory=dict)

    def get_retention_days(self, event: AuditEvent) -> int:
        """Get retention days for an event."""
        # Check event type override first
        if event.event_type in self.event_type_retention:
            return self.event_type_retention[event.event_type]

        # Check severity
        if event.severity in self.severity_retention:
            return self.severity_retention[event.severity]

        return self.default_days

    def is_expired(self, event: AuditEvent) -> bool:
        """Check if event has expired."""
        retention_days = self.get_retention_days(event)
        expiry = event.timestamp + timedelta(days=retention_days)
        return datetime.now(timezone.utc) > expiry


# =============================================================================
# Access Control
# =============================================================================


@dataclass
class AuditAccessRule:
    """Access control rule for audit logs."""

    role: str
    can_read: bool = True
    can_export: bool = False
    can_query_all_projects: bool = False
    can_query_all_users: bool = False

    # Event type restrictions
    allowed_event_types: Optional[set[AuditEventType]] = None
    denied_event_types: Optional[set[AuditEventType]] = None


class AuditAccessControl:
    """Access control for audit log queries.

    Enforces who can read which audit events.
    """

    DEFAULT_RULES = {
        "admin": AuditAccessRule(
            role="admin",
            can_read=True,
            can_export=True,
            can_query_all_projects=True,
            can_query_all_users=True,
        ),
        "project_admin": AuditAccessRule(
            role="project_admin",
            can_read=True,
            can_export=True,
            can_query_all_projects=False,
            can_query_all_users=True,  # Within their project
        ),
        "user": AuditAccessRule(
            role="user",
            can_read=True,
            can_export=False,
            can_query_all_projects=False,
            can_query_all_users=False,
        ),
    }

    def __init__(self, rules: Optional[dict[str, AuditAccessRule]] = None):
        """Initialize access control.

        Args:
            rules: Role-based access rules
        """
        self._rules = rules or self.DEFAULT_RULES.copy()

    def can_read(
        self,
        event: AuditEvent,
        reader_id: str,
        reader_role: str,
        reader_project_id: Optional[str] = None,
    ) -> bool:
        """Check if reader can access event.

        Args:
            event: Event to read
            reader_id: Reader's user ID
            reader_role: Reader's role
            reader_project_id: Reader's project (if applicable)

        Returns:
            True if access allowed
        """
        rule = self._rules.get(reader_role)
        if not rule or not rule.can_read:
            return False

        # Check event type restrictions
        if rule.allowed_event_types and event.event_type not in rule.allowed_event_types:
            return False
        if rule.denied_event_types and event.event_type in rule.denied_event_types:
            return False

        # Check if own event
        if event.actor_id == reader_id:
            return True

        # Check project scope
        if not rule.can_query_all_projects:
            if event.project_id and event.project_id != reader_project_id:
                return False

        # Check user scope
        if not rule.can_query_all_users:
            if event.actor_id != reader_id:
                return False

        return True

    def can_export(self, reader_role: str) -> bool:
        """Check if role can export audit logs."""
        rule = self._rules.get(reader_role)
        return rule.can_export if rule else False


# =============================================================================
# Audit Logger
# =============================================================================


class SecurityAuditLogger:
    """Security audit logger.

    Provides structured logging of security events with:
    - Event signing for tamper detection
    - Access controls on reads
    - Retention policy enforcement
    - Rate limiting

    Example:
        audit = SecurityAuditLogger()

        # Log authentication
        audit.log_auth_success(user_id="user123", session_id="sess456")

        # Log policy violation
        audit.log_policy_violation(
            user_id="user123",
            rule_name="jailbreak_attempt",
            content_sample="ignore previous...",
        )
    """

    def __init__(
        self,
        signer: Optional[LogSigner] = None,
        retention: Optional[RetentionPolicy] = None,
        access_control: Optional[AuditAccessControl] = None,
        max_events: int = 100000,
        output_callback: Optional[Callable[[AuditEvent], None]] = None,
    ):
        """Initialize audit logger.

        Args:
            signer: Log signer for integrity
            retention: Retention policy
            access_control: Access control rules
            max_events: Maximum events to keep in memory
            output_callback: Callback for event output (e.g., to external system)
        """
        self._signer = signer or LogSigner()
        self._retention = retention or RetentionPolicy()
        self._access = access_control or AuditAccessControl()
        self._max_events = max_events
        self._output_callback = output_callback

        self._events: deque[AuditEvent] = deque(maxlen=max_events)
        self._id_gen = EventIdGenerator()
        self._lock = threading.Lock()

        # Rate limiting
        self._rate_limit_window = 60  # seconds
        self._rate_limit_max = 1000  # events per window
        self._rate_counts: dict[str, list[datetime]] = {}

    def _check_rate_limit(self, actor_id: Optional[str]) -> bool:
        """Check rate limit for actor."""
        if not actor_id:
            return True

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=self._rate_limit_window)

        # Clean old entries
        if actor_id in self._rate_counts:
            self._rate_counts[actor_id] = [
                t for t in self._rate_counts[actor_id] if t > window_start
            ]
        else:
            self._rate_counts[actor_id] = []

        # Check limit
        if len(self._rate_counts[actor_id]) >= self._rate_limit_max:
            logger.warning(f"Rate limit exceeded for actor: {actor_id}")
            return False

        self._rate_counts[actor_id].append(now)
        return True

    def log(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        action: Optional[str] = None,
        outcome: str = "success",
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> Optional[AuditEvent]:
        """Log a security audit event.

        Args:
            event_type: Type of event
            severity: Event severity
            actor_id: Actor who triggered event
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            action: Action performed
            outcome: Outcome (success/failure/blocked)
            project_id: Project context
            session_id: Session context
            request_id: Request correlation ID
            details: Additional details

        Returns:
            Created event, or None if rate limited
        """
        # Rate limit check
        if not self._check_rate_limit(actor_id):
            return None

        event = AuditEvent(
            event_id=self._id_gen.generate(),
            event_type=event_type,
            severity=severity,
            timestamp=datetime.now(timezone.utc),
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            outcome=outcome,
            project_id=project_id,
            session_id=session_id,
            request_id=request_id,
            details=details or {},
        )

        # Sign event
        event.signature = self._signer.sign(event)

        # Store
        with self._lock:
            self._events.append(event)

        # Output callback
        if self._output_callback:
            try:
                self._output_callback(event)
            except Exception as e:
                logger.error(f"Audit output callback failed: {e}")

        # Log to standard logger as well
        logger.info(
            f"Audit: {event_type.value}",
            extra={
                "audit_event": event.to_dict(),
            },
        )

        return event

    # =========================================================================
    # Convenience methods
    # =========================================================================

    def log_auth_success(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> AuditEvent:
        """Log successful authentication."""
        return self.log(
            event_type=AuditEventType.AUTH_SUCCESS,
            severity=AuditSeverity.INFO,
            actor_id=user_id,
            session_id=session_id,
            action="login",
            details=details,
        )

    def log_auth_failure(
        self,
        user_id: Optional[str] = None,
        reason: str = "invalid_credentials",
        details: Optional[dict] = None,
    ) -> AuditEvent:
        """Log failed authentication."""
        d = {"reason": reason}
        if details:
            d.update(details)
        return self.log(
            event_type=AuditEventType.AUTH_FAILURE,
            severity=AuditSeverity.WARNING,
            actor_id=user_id,
            action="login",
            outcome="failure",
            details=d,
        )

    def log_policy_violation(
        self,
        user_id: Optional[str] = None,
        rule_name: str = "unknown",
        content_sample: str = "",
        project_id: Optional[str] = None,
        blocked: bool = False,
    ) -> AuditEvent:
        """Log policy violation."""
        return self.log(
            event_type=AuditEventType.POLICY_BLOCK if blocked else AuditEventType.POLICY_WARN,
            severity=AuditSeverity.WARNING if blocked else AuditSeverity.NOTICE,
            actor_id=user_id,
            project_id=project_id,
            action="policy_check",
            outcome="blocked" if blocked else "warned",
            details={
                "rule_name": rule_name,
                "content_sample": content_sample[:100],
            },
        )

    def log_injection_attempt(
        self,
        user_id: Optional[str] = None,
        injection_type: str = "unknown",
        content_sample: str = "",
        project_id: Optional[str] = None,
    ) -> AuditEvent:
        """Log injection attempt."""
        return self.log(
            event_type=AuditEventType.SEC_INJECTION_ATTEMPT,
            severity=AuditSeverity.ERROR,
            actor_id=user_id,
            project_id=project_id,
            action="injection_detected",
            outcome="blocked",
            details={
                "injection_type": injection_type,
                "content_sample": content_sample[:100],
            },
        )

    # =========================================================================
    # Query methods
    # =========================================================================

    def query(
        self,
        reader_id: str,
        reader_role: str,
        reader_project_id: Optional[str] = None,
        event_types: Optional[list[AuditEventType]] = None,
        actor_id: Optional[str] = None,
        project_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events with access control.

        Args:
            reader_id: ID of user making query
            reader_role: Role of user making query
            reader_project_id: Project of user making query
            event_types: Filter by event types
            actor_id: Filter by actor
            project_id: Filter by project
            since: Start time
            until: End time
            limit: Maximum results

        Returns:
            List of accessible events (most recent first)
        """
        results = []

        with self._lock:
            for event in reversed(self._events):
                # Access control check
                if not self._access.can_read(event, reader_id, reader_role, reader_project_id):
                    continue

                # Apply filters
                if event_types and event.event_type not in event_types:
                    continue
                if actor_id and event.actor_id != actor_id:
                    continue
                if project_id and event.project_id != project_id:
                    continue
                if since and event.timestamp < since:
                    continue
                if until and event.timestamp > until:
                    continue

                results.append(event)

                if len(results) >= limit:
                    break

        return results

    def cleanup_expired(self) -> int:
        """Remove expired events based on retention policy.

        Returns:
            Number of events removed
        """
        removed = 0

        with self._lock:
            non_expired = deque(maxlen=self._max_events)
            for event in self._events:
                if not self._retention.is_expired(event):
                    non_expired.append(event)
                else:
                    removed += 1

            self._events = non_expired

        if removed > 0:
            logger.info(f"Cleaned up {removed} expired audit events")

        return removed


# =============================================================================
# Global Instance
# =============================================================================


_audit_logger: Optional[SecurityAuditLogger] = None


def get_audit_logger() -> SecurityAuditLogger:
    """Get global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = SecurityAuditLogger()
    return _audit_logger


def audit_log(
    event_type: AuditEventType,
    severity: AuditSeverity = AuditSeverity.INFO,
    **kwargs,
) -> Optional[AuditEvent]:
    """Convenience function to log audit event.

    Args:
        event_type: Type of event
        severity: Event severity
        **kwargs: Additional event parameters

    Returns:
        Created event
    """
    return get_audit_logger().log(event_type, severity, **kwargs)
