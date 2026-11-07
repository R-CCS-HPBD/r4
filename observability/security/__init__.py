"""Security module for Observability.

This module provides secure logging and audit capabilities:
- LogScrubber: PII and secrets scrubbing from logs
- SecurityAuditLogger: Structured security event logging
- Access controls and retention policies

Phase 8 of Security Hardening.
"""

from observability.security.log_scrubber import (
    LogScrubber,
    ScrubConfig,
    ScrubFilter,
    ScrubPattern,
    ScrubResult,
    ScrubType,
    StructuredLogScrubber,
    get_log_scrubber,
    install_scrub_filter,
    scrub_text,
)

from observability.security.audit_logger import (
    AuditAccessControl,
    AuditAccessRule,
    AuditEvent,
    AuditEventType,
    AuditSeverity,
    LogSigner,
    RetentionPolicy,
    SecurityAuditLogger,
    audit_log,
    get_audit_logger,
)

__all__ = [
    # Log scrubbing
    "LogScrubber",
    "ScrubConfig",
    "ScrubFilter",
    "ScrubPattern",
    "ScrubResult",
    "ScrubType",
    "StructuredLogScrubber",
    "get_log_scrubber",
    "install_scrub_filter",
    "scrub_text",
    # Audit logging
    "AuditAccessControl",
    "AuditAccessRule",
    "AuditEvent",
    "AuditEventType",
    "AuditSeverity",
    "LogSigner",
    "RetentionPolicy",
    "SecurityAuditLogger",
    "audit_log",
    "get_audit_logger",
]
