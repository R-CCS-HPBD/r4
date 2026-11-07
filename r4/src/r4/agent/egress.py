"""Egress Control for the Agentic Workflow Builder.

This module provides egress surveillance to protect sensitive research data.
All outbound data requests from MCP tools or agent actions must pass through
the EgressGuard, which:

1. Scans content for sensitive data (via ContentScanner)
2. Prompts user for approval before sending
3. Logs all egress decisions to audit trail
4. Supports trust sessions for batch approvals

Key Principle: Ask always, trust explicitly.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse


class EgressPolicy(str, Enum):
    """Egress policy modes."""

    ALWAYS_ASK = "always_ask"  # Always prompt user (default)
    ALLOWLISTED = "allowlisted"  # Domain in allowlist, skip prompt
    BLOCKED = "blocked"  # Domain in blocklist, deny immediately


class EgressDecision(str, Enum):
    """User decision on egress request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    TRUSTED = "trusted"  # Approved via trust session


class EgressRejectionReason(str, Enum):
    """Reason why an egress request was blocked or denied."""

    DOMAIN_BLOCKED = "domain_blocked"
    SENSITIVE_SECRETS = "sensitive_secrets"
    SENSITIVE_PII = "sensitive_pii"
    SENSITIVE_POLICY = "sensitive_policy"
    USER_DENIED = "user_denied"


@dataclass
class SensitivityFlags:
    """Flags indicating detected sensitive content."""

    has_pii: bool = False
    has_secrets: bool = False
    has_research_data: bool = False
    has_file_data: bool = False
    user_tags: List[str] = field(default_factory=list)
    details: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def is_sensitive(self) -> bool:
        """Check if any sensitivity flag is set."""
        return self.has_pii or self.has_secrets or self.has_research_data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pii": self.has_pii,
            "secrets": self.has_secrets,
            "research_data": self.has_research_data,
            "file_data": self.has_file_data,
            "user_tags": self.user_tags,
            "details": self.details,
        }


@dataclass
class EgressRequest:
    """A request to send data to an external destination."""

    request_id: str
    tool_name: str
    destination_url: str
    destination_domain: str
    payload: Any
    payload_hash: str
    payload_size_bytes: int
    sensitivity_flags: SensitivityFlags
    user_id: str
    project_id: str
    session_id: Optional[str] = None
    decision: EgressDecision = EgressDecision.PENDING
    rejection_reason: Optional[EgressRejectionReason] = None
    decision_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def create(
        cls,
        tool_name: str,
        destination_url: str,
        payload: Any,
        sensitivity_flags: SensitivityFlags,
        user_id: str,
        project_id: str,
        session_id: Optional[str] = None,
    ) -> "EgressRequest":
        """Create a new egress request."""
        # Extract domain from URL
        parsed = urlparse(destination_url)
        domain = parsed.netloc or parsed.path.split("/")[0]

        # Serialize and hash payload
        payload_str = str(payload) if not isinstance(payload, str) else payload
        payload_bytes = payload_str.encode("utf-8")
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()[:16]

        return cls(
            request_id=str(uuid.uuid4()),
            tool_name=tool_name,
            destination_url=destination_url,
            destination_domain=domain,
            payload=payload,
            payload_hash=payload_hash,
            payload_size_bytes=len(payload_bytes),
            sensitivity_flags=sensitivity_flags,
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
        )

    def approve(self, trusted: bool = False) -> None:
        """Mark request as approved."""
        self.decision = EgressDecision.TRUSTED if trusted else EgressDecision.APPROVED
        self.rejection_reason = None
        self.decision_at = datetime.utcnow()

    def deny(self, reason: Optional[EgressRejectionReason] = None) -> None:
        """Mark request as denied."""
        self.decision = EgressDecision.DENIED
        self.rejection_reason = reason
        self.decision_at = datetime.utcnow()

    def timeout(self) -> None:
        """Mark request as timed out."""
        self.decision = EgressDecision.TIMED_OUT
        self.rejection_reason = None
        self.decision_at = datetime.utcnow()

    def get_preview(self, max_length: int = 200) -> str:
        """Get a truncated preview of the payload."""
        payload_str = str(self.payload)
        if len(payload_str) > max_length:
            return payload_str[:max_length] + "..."
        return payload_str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "destination_url": self.destination_url,
            "destination_domain": self.destination_domain,
            "payload_hash": self.payload_hash,
            "payload_size_bytes": self.payload_size_bytes,
            "sensitivity_flags": self.sensitivity_flags.to_dict(),
            "user_id": self.user_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "decision": self.decision.value,
            "rejection_reason": (self.rejection_reason.value if self.rejection_reason else None),
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class TrustSession:
    """A time-limited trust session for batch egress approvals."""

    session_id: str
    user_id: str
    trusted_domains: Set[str]
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def create(
        cls,
        user_id: str,
        domains: List[str],
        duration_minutes: int = 30,
    ) -> "TrustSession":
        """Create a new trust session."""
        return cls(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            trusted_domains=set(domains),
            expires_at=datetime.utcnow() + timedelta(minutes=duration_minutes),
        )

    @property
    def is_expired(self) -> bool:
        """Check if trust session has expired."""
        return datetime.utcnow() > self.expires_at

    @property
    def remaining_minutes(self) -> int:
        """Get remaining minutes in trust session."""
        if self.is_expired:
            return 0
        delta = self.expires_at - datetime.utcnow()
        return int(delta.total_seconds() / 60)

    def is_trusted(self, domain: str) -> bool:
        """Check if domain is trusted in this session."""
        if self.is_expired:
            return False
        return domain in self.trusted_domains

    def add_domain(self, domain: str) -> None:
        """Add a domain to trusted list."""
        self.trusted_domains.add(domain)


@dataclass
class ProjectEgressPolicy:
    """Per-project egress policy configuration."""

    project_id: str
    default_action: EgressPolicy = EgressPolicy.ALWAYS_ASK
    allowlisted_domains: Set[str] = field(default_factory=set)
    blocked_domains: Set[str] = field(default_factory=set)
    auto_deny_on_secrets: bool = True  # Auto-deny if secrets detected
    auto_deny_on_pii: bool = False  # Auto-deny if PII detected
    require_mfa_for_egress: bool = False  # Require MFA for any egress

    # Common research domains that are often allowed
    COMMON_RESEARCH_DOMAINS = {
        "arxiv.org",
        "api.semanticscholar.org",
        "pubmed.ncbi.nlm.nih.gov",
        "api.crossref.org",
        "doi.org",
        "orcid.org",
    }

    def get_policy_for_domain(self, domain: str) -> EgressPolicy:
        """Get the policy for a specific domain."""
        if domain in self.blocked_domains:
            return EgressPolicy.BLOCKED
        if domain in self.allowlisted_domains:
            return EgressPolicy.ALLOWLISTED
        return self.default_action

    def should_auto_deny(self, sensitivity: SensitivityFlags) -> bool:
        """Check if request should be auto-denied based on sensitivity."""
        if self.auto_deny_on_secrets and sensitivity.has_secrets:
            return True
        if self.auto_deny_on_pii and sensitivity.has_pii:
            return True
        return False


# Type for approval callback
ApprovalCallback = Callable[[EgressRequest], EgressDecision]


class EgressGuard:
    """Guard that controls all outbound data access.

    The EgressGuard is the central point for egress surveillance. It:
    1. Intercepts all outbound requests
    2. Scans content for sensitive data
    3. Checks against project policies
    4. Prompts user for approval
    5. Logs all decisions

    Usage:
        guard = EgressGuard(user_id="...", project_id="...")

        # Check egress
        decision = await guard.check(
            tool_name="web_search",
            destination_url="https://api.example.com/search",
            payload={"query": "my research"},
        )

        if decision == EgressDecision.APPROVED:
            # Proceed with request
            ...
    """

    def __init__(
        self,
        user_id: str,
        project_id: str,
        policy: Optional[ProjectEgressPolicy] = None,
        approval_callback: Optional[ApprovalCallback] = None,
        auto_approve: bool = False,
    ):
        """Initialize EgressGuard.

        Args:
            user_id: ID of the user making requests
            project_id: ID of the project
            policy: Project egress policy (defaults to always-ask)
            approval_callback: Callback to get user approval (for CLI/UI)
            auto_approve: If True, auto-approve all (for testing only!)
        """
        self.user_id = user_id
        self.project_id = project_id
        self.policy = policy or ProjectEgressPolicy(project_id=project_id)
        self.approval_callback = approval_callback
        self.auto_approve = auto_approve

        # Trust sessions
        self._trust_sessions: Dict[str, TrustSession] = {}

        # Request history (for audit)
        self._request_history: List[EgressRequest] = []

        # Scanner will be imported to avoid circular dependency
        self._scanner: Optional[Any] = None

    def _get_scanner(self) -> Any:
        """Lazy load scanner to avoid circular import."""
        if self._scanner is None:
            from .scanner import ContentScanner

            self._scanner = ContentScanner()
        return self._scanner

    @staticmethod
    def _scan_result_to_flags(scan_result: Any) -> SensitivityFlags:
        """Convert a scanner ScanResult to SensitivityFlags.

        The ContentScanner returns a ScanResult with a SensitivityLevel
        flag enum; the EgressGuard works with SensitivityFlags dataclass.
        """
        from .scanner import SensitivityLevel

        sensitivity = scan_result.sensitivity
        details: Dict[str, List[str]] = {}
        for m in scan_result.matches:
            details.setdefault(m.pattern_name, []).append(m.redacted())

        return SensitivityFlags(
            has_pii=bool(SensitivityLevel.PII & sensitivity),
            has_secrets=bool(SensitivityLevel.SECRETS & sensitivity),
            has_research_data=bool(SensitivityLevel.RESEARCH_DATA & sensitivity),
            has_file_data=False,
            user_tags=list(scan_result.user_tags),
            details=details,
        )

    def start_trust_session(
        self,
        domains: List[str],
        duration_minutes: int = 30,
    ) -> TrustSession:
        """Start a trust session for batch approvals.

        Args:
            domains: List of domains to trust
            duration_minutes: How long to trust (default 30 min)

        Returns:
            TrustSession object
        """
        session = TrustSession.create(
            user_id=self.user_id,
            domains=domains,
            duration_minutes=duration_minutes,
        )
        self._trust_sessions[session.session_id] = session
        return session

    def get_active_trust_session(self) -> Optional[TrustSession]:
        """Get the active (non-expired) trust session, if any."""
        # Clean expired sessions
        expired = [sid for sid, s in self._trust_sessions.items() if s.is_expired]
        for sid in expired:
            del self._trust_sessions[sid]

        # Return first active session
        for session in self._trust_sessions.values():
            if not session.is_expired:
                return session
        return None

    def end_trust_session(self) -> None:
        """End all trust sessions."""
        self._trust_sessions.clear()

    def check(
        self,
        tool_name: str,
        destination_url: str,
        payload: Any,
        session_id: Optional[str] = None,
    ) -> EgressDecision:
        """Check if egress is allowed and get user approval.

        This is the main entry point. It:
        1. Scans payload for sensitive content
        2. Checks domain against policy
        3. Checks trust session
        4. Prompts user if needed
        5. Records decision

        Args:
            tool_name: Name of the MCP tool making the request
            destination_url: URL being accessed
            payload: Data being sent
            session_id: Optional agent session ID

        Returns:
            EgressDecision indicating whether egress is allowed
        """
        # Scan content
        scanner = self._get_scanner()
        scan_result = scanner.scan(payload)
        # Convert ScanResult → SensitivityFlags if needed
        if isinstance(scan_result, SensitivityFlags):
            sensitivity = scan_result
        else:
            sensitivity = self._scan_result_to_flags(scan_result)

        # Create request record
        request = EgressRequest.create(
            tool_name=tool_name,
            destination_url=destination_url,
            payload=payload,
            sensitivity_flags=sensitivity,
            user_id=self.user_id,
            project_id=self.project_id,
            session_id=session_id,
        )

        # Check domain policy
        domain_policy = self.policy.get_policy_for_domain(request.destination_domain)

        if domain_policy == EgressPolicy.BLOCKED:
            request.deny(EgressRejectionReason.DOMAIN_BLOCKED)
            self._record_request(request)
            return EgressDecision.DENIED

        # Check auto-deny based on sensitivity
        if self.policy.should_auto_deny(sensitivity):
            if sensitivity.has_secrets:
                request.deny(EgressRejectionReason.SENSITIVE_SECRETS)
            elif sensitivity.has_pii:
                request.deny(EgressRejectionReason.SENSITIVE_PII)
            else:
                request.deny(EgressRejectionReason.SENSITIVE_POLICY)
            self._record_request(request)
            return EgressDecision.DENIED

        # Check trust session
        trust_session = self.get_active_trust_session()
        if trust_session and trust_session.is_trusted(request.destination_domain):
            # Sensitive content still requires individual approval
            if not sensitivity.is_sensitive:
                request.approve(trusted=True)
                self._record_request(request)
                return EgressDecision.TRUSTED

        # Check allowlist (non-sensitive only)
        if domain_policy == EgressPolicy.ALLOWLISTED and not sensitivity.is_sensitive:
            request.approve(trusted=True)
            self._record_request(request)
            return EgressDecision.APPROVED

        # Auto-approve mode (for testing)
        if self.auto_approve:
            request.approve()
            self._record_request(request)
            return EgressDecision.APPROVED

        # Prompt user
        if self.approval_callback:
            decision = self.approval_callback(request)
            if decision == EgressDecision.APPROVED:
                request.approve()
            elif decision == EgressDecision.DENIED:
                request.deny(EgressRejectionReason.USER_DENIED)
            else:
                request.timeout()
            self._record_request(request)
            return decision

        # No callback, default to pending (caller must handle)
        self._record_request(request)
        return EgressDecision.PENDING

    def _record_request(self, request: EgressRequest) -> None:
        """Record request in history."""
        self._request_history.append(request)

    def get_audit_log(
        self,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[EgressRequest]:
        """Get egress request audit log.

        Args:
            since: Only return requests after this time
            limit: Maximum number of requests to return

        Returns:
            List of EgressRequest objects
        """
        requests = self._request_history

        if since:
            requests = [r for r in requests if r.created_at > since]

        return requests[-limit:]

    def get_last_request(self) -> Optional[EgressRequest]:
        """Return the latest egress request, if present."""
        if not self._request_history:
            return None
        return self._request_history[-1]

    def format_approval_prompt(self, request: EgressRequest) -> str:
        """Format an approval prompt for CLI display.

        Args:
            request: The egress request

        Returns:
            Formatted string for display
        """
        lines = [
            "┌─────────────────────────────────────────────────────────────┐",
            "│ ⚠️  OUTBOUND DATA REQUEST                                    │",
            "├─────────────────────────────────────────────────────────────┤",
            f"│ Tool:        {request.tool_name:<46} │",
            f"│ Destination: {request.destination_url[:46]:<46} │",
            f"│ Domain:      {request.destination_domain:<46} │",
            (
                f"│ Size:        {request.payload_size_bytes} bytes"
                f"{' ' * (40 - len(str(request.payload_size_bytes)))} │"
            ),
        ]

        # Add sensitivity warnings
        if request.sensitivity_flags.is_sensitive:
            lines.append("│                                                             │")
            if request.sensitivity_flags.has_pii:
                lines.append("│ ⚠️  Detected: PII (personal information)                    │")
            if request.sensitivity_flags.has_secrets:
                lines.append("│ 🔴 Detected: Secrets (API keys, tokens)                     │")
            if request.sensitivity_flags.has_research_data:
                lines.append("│ ⚠️  Detected: Research keywords                              │")

        # Add preview
        preview = request.get_preview(50)
        lines.extend(
            [
                "│                                                             │",
                "│ Preview:                                                    │",
                f"│   {preview:<57} │",
                "│                                                             │",
                "│ [A]pprove  [D]eny  [P]review full  [T]rust for session     │",
                "└─────────────────────────────────────────────────────────────┘",
            ]
        )

        return "\n".join(lines)


def cli_approval_callback(request: EgressRequest) -> EgressDecision:
    """Default CLI approval callback.

    Prompts user interactively for egress approval.
    """
    guard = EgressGuard(request.user_id, request.project_id)
    print(guard.format_approval_prompt(request))

    while True:
        choice = input("> ").strip().lower()

        if choice in ("a", "approve", "y", "yes"):
            return EgressDecision.APPROVED
        elif choice in ("d", "deny", "n", "no"):
            return EgressDecision.DENIED
        elif choice in ("p", "preview"):
            print(f"\nFull payload:\n{request.payload}\n")
        elif choice in ("t", "trust"):
            print("Trust session not implemented in this callback")
            return EgressDecision.APPROVED
        else:
            print("Please enter [A]pprove, [D]eny, [P]review, or [T]rust")
