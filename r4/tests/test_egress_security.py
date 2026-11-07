"""Security tests for egress control and content scanning (O-O1).

Tests the r4 egress guard invariants:
- Sensitive content detection (PII, secrets, research data)
- Domain policy enforcement (allowlist, blocklist)
- Trust session lifecycle (creation, expiry, domain scoping)
- Auto-deny for secrets
- Audit trail integrity
"""

from __future__ import annotations

from datetime import datetime, timedelta

# ── SensitivityFlags ────────────────────────────────────────────────


class TestSensitivityFlags:
    """Sensitivity flag model tests."""

    def test_default_flags_not_sensitive(self):
        from r4.agent.egress import SensitivityFlags

        flags = SensitivityFlags()
        assert not flags.is_sensitive

    def test_pii_flag_is_sensitive(self):
        from r4.agent.egress import SensitivityFlags

        flags = SensitivityFlags(has_pii=True)
        assert flags.is_sensitive

    def test_secrets_flag_is_sensitive(self):
        from r4.agent.egress import SensitivityFlags

        flags = SensitivityFlags(has_secrets=True)
        assert flags.is_sensitive

    def test_research_data_flag_is_sensitive(self):
        from r4.agent.egress import SensitivityFlags

        flags = SensitivityFlags(has_research_data=True)
        assert flags.is_sensitive

    def test_file_data_alone_not_sensitive(self):
        from r4.agent.egress import SensitivityFlags

        flags = SensitivityFlags(has_file_data=True)
        assert not flags.is_sensitive

    def test_to_dict(self):
        from r4.agent.egress import SensitivityFlags

        flags = SensitivityFlags(has_pii=True, user_tags=["confidential"])
        d = flags.to_dict()
        assert d["pii"] is True
        assert d["secrets"] is False
        assert d["user_tags"] == ["confidential"]


# ── EgressRequest ───────────────────────────────────────────────────


class TestEgressRequest:
    """Egress request creation and lifecycle."""

    def test_create_extracts_domain(self):
        from r4.agent.egress import EgressRequest, SensitivityFlags

        req = EgressRequest.create(
            tool_name="web_search",
            destination_url="https://api.example.com/data",
            payload="search query",
            sensitivity_flags=SensitivityFlags(),
            user_id="alice",
            project_id="proj-1",
        )
        assert req.destination_domain == "api.example.com"
        assert req.tool_name == "web_search"
        assert req.decision.value == "pending"

    def test_create_hashes_payload(self):
        from r4.agent.egress import EgressRequest, SensitivityFlags

        req = EgressRequest.create(
            tool_name="upload",
            destination_url="https://storage.example.com",
            payload="my data",
            sensitivity_flags=SensitivityFlags(),
            user_id="alice",
            project_id="proj-1",
        )
        assert len(req.payload_hash) == 16
        assert req.payload_size_bytes == len("my data".encode())

    def test_approve_sets_decision(self):
        from r4.agent.egress import EgressDecision, EgressRequest, SensitivityFlags

        req = EgressRequest.create(
            tool_name="t",
            destination_url="https://a.com",
            payload="x",
            sensitivity_flags=SensitivityFlags(),
            user_id="u",
            project_id="p",
        )
        req.approve()
        assert req.decision == EgressDecision.APPROVED
        assert req.decision_at is not None

    def test_approve_trusted_sets_trusted(self):
        from r4.agent.egress import EgressDecision, EgressRequest, SensitivityFlags

        req = EgressRequest.create(
            tool_name="t",
            destination_url="https://a.com",
            payload="x",
            sensitivity_flags=SensitivityFlags(),
            user_id="u",
            project_id="p",
        )
        req.approve(trusted=True)
        assert req.decision == EgressDecision.TRUSTED

    def test_deny_sets_decision(self):
        from r4.agent.egress import (
            EgressDecision,
            EgressRejectionReason,
            EgressRequest,
            SensitivityFlags,
        )

        req = EgressRequest.create(
            tool_name="t",
            destination_url="https://a.com",
            payload="x",
            sensitivity_flags=SensitivityFlags(),
            user_id="u",
            project_id="p",
        )
        req.deny(EgressRejectionReason.USER_DENIED)
        assert req.decision == EgressDecision.DENIED
        assert req.rejection_reason == EgressRejectionReason.USER_DENIED

    def test_to_dict_includes_all_fields(self):
        from r4.agent.egress import EgressRequest, SensitivityFlags

        req = EgressRequest.create(
            tool_name="fetch",
            destination_url="https://data.example.com/results",
            payload="query",
            sensitivity_flags=SensitivityFlags(has_pii=True),
            user_id="alice",
            project_id="proj-1",
        )
        d = req.to_dict()
        assert d["tool_name"] == "fetch"
        assert d["destination_domain"] == "data.example.com"
        assert d["sensitivity_flags"]["pii"] is True
        assert d["decision"] == "pending"
        assert d["rejection_reason"] is None

    def test_get_preview_truncates(self):
        from r4.agent.egress import EgressRequest, SensitivityFlags

        req = EgressRequest.create(
            tool_name="t",
            destination_url="https://a.com",
            payload="x" * 500,
            sensitivity_flags=SensitivityFlags(),
            user_id="u",
            project_id="p",
        )
        preview = req.get_preview(max_length=50)
        assert len(preview) < 60
        assert preview.endswith("...")


# ── TrustSession ────────────────────────────────────────────────────


class TestTrustSession:
    """Trust session lifecycle."""

    def test_create_session(self):
        from r4.agent.egress import TrustSession

        session = TrustSession.create(
            user_id="alice",
            domains=["api.arxiv.org", "api.example.com"],
            duration_minutes=30,
        )
        assert not session.is_expired
        assert session.remaining_minutes > 0
        assert session.is_trusted("api.arxiv.org")
        assert session.is_trusted("api.example.com")
        assert not session.is_trusted("evil.com")

    def test_expired_session_not_trusted(self):
        from r4.agent.egress import TrustSession

        session = TrustSession.create(
            user_id="alice",
            domains=["api.example.com"],
            duration_minutes=0,
        )
        # Force expiry
        session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        assert session.is_expired
        assert session.remaining_minutes == 0
        assert not session.is_trusted("api.example.com")

    def test_add_domain_to_session(self):
        from r4.agent.egress import TrustSession

        session = TrustSession.create(
            user_id="alice",
            domains=["api.arxiv.org"],
        )
        assert not session.is_trusted("api.example.com")
        session.add_domain("api.example.com")
        assert session.is_trusted("api.example.com")


# ── ProjectEgressPolicy ────────────────────────────────────────────


class TestProjectEgressPolicy:
    """Per-project policy enforcement."""

    def test_default_policy_always_asks(self):
        from r4.agent.egress import EgressPolicy, ProjectEgressPolicy

        policy = ProjectEgressPolicy(project_id="test")
        assert policy.get_policy_for_domain("any.com") == EgressPolicy.ALWAYS_ASK

    def test_blocked_domain_denied(self):
        from r4.agent.egress import EgressPolicy, ProjectEgressPolicy

        policy = ProjectEgressPolicy(
            project_id="test",
            blocked_domains={"evil.com"},
        )
        assert policy.get_policy_for_domain("evil.com") == EgressPolicy.BLOCKED

    def test_allowlisted_domain(self):
        from r4.agent.egress import EgressPolicy, ProjectEgressPolicy

        policy = ProjectEgressPolicy(
            project_id="test",
            allowlisted_domains={"api.arxiv.org"},
        )
        assert policy.get_policy_for_domain("api.arxiv.org") == EgressPolicy.ALLOWLISTED
        assert policy.get_policy_for_domain("other.com") == EgressPolicy.ALWAYS_ASK

    def test_auto_deny_on_secrets(self):
        from r4.agent.egress import ProjectEgressPolicy, SensitivityFlags

        policy = ProjectEgressPolicy(project_id="test", auto_deny_on_secrets=True)
        flags = SensitivityFlags(has_secrets=True)
        assert policy.should_auto_deny(flags)

    def test_no_auto_deny_on_pii_by_default(self):
        from r4.agent.egress import ProjectEgressPolicy, SensitivityFlags

        policy = ProjectEgressPolicy(project_id="test")
        flags = SensitivityFlags(has_pii=True)
        assert not policy.should_auto_deny(flags)

    def test_auto_deny_on_pii_when_enabled(self):
        from r4.agent.egress import ProjectEgressPolicy, SensitivityFlags

        policy = ProjectEgressPolicy(project_id="test", auto_deny_on_pii=True)
        flags = SensitivityFlags(has_pii=True)
        assert policy.should_auto_deny(flags)


# ── EgressGuard ─────────────────────────────────────────────────────


class _StubScanner:
    """Stub scanner returning pre-set SensitivityFlags for guard tests.

    EgressGuard.check() calls scanner.scan(payload) and expects a
    SensitivityFlags-compatible object.  The real ContentScanner returns
    ScanResult (a different type) — this is a known integration gap.
    These tests isolate the guard logic.
    """

    def __init__(self, flags=None):
        from r4.agent.egress import SensitivityFlags

        self._flags = flags or SensitivityFlags()

    def scan(self, _payload):
        return self._flags


def _make_guard(**kwargs):
    """Create an EgressGuard with a stubbed scanner."""
    from r4.agent.egress import EgressGuard

    guard = EgressGuard(**kwargs)
    guard._scanner = _StubScanner(kwargs.pop("sensitivity", None))
    return guard


class TestEgressGuard:
    """Core egress guard logic."""

    def test_blocked_domain_denied(self):
        from r4.agent.egress import (
            EgressDecision,
            EgressGuard,
            EgressRejectionReason,
            ProjectEgressPolicy,
        )

        policy = ProjectEgressPolicy(
            project_id="test",
            blocked_domains={"evil.com"},
        )
        guard = EgressGuard(user_id="alice", project_id="test", policy=policy)
        guard._scanner = _StubScanner()
        decision = guard.check(
            tool_name="fetch",
            destination_url="https://evil.com/data",
            payload="harmless",
        )
        assert decision == EgressDecision.DENIED
        assert guard.get_last_request() is not None
        assert guard.get_last_request().rejection_reason == EgressRejectionReason.DOMAIN_BLOCKED

    def test_auto_approve_mode(self):
        from r4.agent.egress import EgressDecision, EgressGuard

        guard = EgressGuard(
            user_id="alice",
            project_id="test",
            auto_approve=True,
        )
        guard._scanner = _StubScanner()
        decision = guard.check(
            tool_name="search",
            destination_url="https://api.example.com",
            payload="query",
        )
        assert decision == EgressDecision.APPROVED

    def test_callback_approval(self):
        from r4.agent.egress import EgressDecision, EgressGuard

        def always_approve(_req):
            return EgressDecision.APPROVED

        guard = EgressGuard(
            user_id="alice",
            project_id="test",
            approval_callback=always_approve,
        )
        guard._scanner = _StubScanner()
        decision = guard.check(
            tool_name="fetch",
            destination_url="https://api.example.com",
            payload="data",
        )
        assert decision == EgressDecision.APPROVED

    def test_callback_denial(self):
        from r4.agent.egress import EgressDecision, EgressGuard, EgressRejectionReason

        def always_deny(_req):
            return EgressDecision.DENIED

        guard = EgressGuard(
            user_id="alice",
            project_id="test",
            approval_callback=always_deny,
        )
        guard._scanner = _StubScanner()
        decision = guard.check(
            tool_name="fetch",
            destination_url="https://api.example.com",
            payload="data",
        )
        assert decision == EgressDecision.DENIED
        assert guard.get_last_request() is not None
        assert guard.get_last_request().rejection_reason == EgressRejectionReason.USER_DENIED

    def test_no_callback_returns_pending(self):
        from r4.agent.egress import EgressDecision, EgressGuard

        guard = EgressGuard(user_id="alice", project_id="test")
        guard._scanner = _StubScanner()
        decision = guard.check(
            tool_name="fetch",
            destination_url="https://api.example.com",
            payload="data",
        )
        assert decision == EgressDecision.PENDING

    def test_trust_session_approves_trusted_domain(self):
        from r4.agent.egress import EgressDecision, EgressGuard

        guard = EgressGuard(user_id="alice", project_id="test")
        guard._scanner = _StubScanner()
        guard.start_trust_session(["api.example.com"], duration_minutes=5)

        decision = guard.check(
            tool_name="fetch",
            destination_url="https://api.example.com/data",
            payload="safe query",
        )
        assert decision == EgressDecision.TRUSTED

    def test_trust_session_does_not_approve_untrusted_domain(self):
        from r4.agent.egress import EgressDecision, EgressGuard

        guard = EgressGuard(user_id="alice", project_id="test")
        guard._scanner = _StubScanner()
        guard.start_trust_session(["api.example.com"], duration_minutes=5)

        decision = guard.check(
            tool_name="fetch",
            destination_url="https://other.com/data",
            payload="data",
        )
        # Not in trusted list, falls through to PENDING (no callback)
        assert decision == EgressDecision.PENDING

    def test_audit_log_records_all_requests(self):
        from r4.agent.egress import EgressGuard

        guard = EgressGuard(
            user_id="alice",
            project_id="test",
            auto_approve=True,
        )
        guard._scanner = _StubScanner()
        guard.check("t1", "https://a.com", "data1")
        guard.check("t2", "https://b.com", "data2")
        guard.check("t3", "https://c.com", "data3")

        log = guard.get_audit_log()
        assert len(log) == 3
        assert log[0].tool_name == "t1"
        assert log[2].tool_name == "t3"

    def test_end_trust_session_clears_all(self):
        from r4.agent.egress import EgressGuard

        guard = EgressGuard(user_id="alice", project_id="test")
        guard.start_trust_session(["api.example.com"])
        guard.end_trust_session()

        assert guard.get_active_trust_session() is None

    def test_allowlisted_non_sensitive_auto_approved(self):
        from r4.agent.egress import EgressDecision, EgressGuard, ProjectEgressPolicy

        policy = ProjectEgressPolicy(
            project_id="test",
            allowlisted_domains={"api.arxiv.org"},
        )
        guard = EgressGuard(user_id="alice", project_id="test", policy=policy)
        guard._scanner = _StubScanner()
        decision = guard.check(
            tool_name="search",
            destination_url="https://api.arxiv.org/search",
            payload="quantum computing papers",
        )
        assert decision == EgressDecision.APPROVED

    def test_auto_deny_secrets_in_payload(self):
        from r4.agent.egress import (
            EgressDecision,
            EgressGuard,
            EgressRejectionReason,
            ProjectEgressPolicy,
            SensitivityFlags,
        )

        policy = ProjectEgressPolicy(project_id="test", auto_deny_on_secrets=True)
        guard = EgressGuard(user_id="alice", project_id="test", policy=policy)
        guard._scanner = _StubScanner(SensitivityFlags(has_secrets=True))

        decision = guard.check(
            tool_name="upload",
            destination_url="https://api.example.com/upload",
            payload="password: secret123",
        )
        assert decision == EgressDecision.DENIED
        assert guard.get_last_request() is not None
        assert guard.get_last_request().rejection_reason == EgressRejectionReason.SENSITIVE_SECRETS

    def test_sensitive_content_still_prompts_on_trusted_domain(self):
        """Even with a trust session, sensitive content needs individual approval."""
        from r4.agent.egress import (
            EgressDecision,
            EgressGuard,
            SensitivityFlags,
        )

        guard = EgressGuard(user_id="alice", project_id="test")
        guard._scanner = _StubScanner(SensitivityFlags(has_pii=True))
        guard.start_trust_session(["api.example.com"], duration_minutes=5)

        decision = guard.check(
            tool_name="fetch",
            destination_url="https://api.example.com/data",
            payload="data with PII",
        )
        # Sensitive content requires explicit approval even on trusted domains
        assert decision == EgressDecision.PENDING


# ── ContentScanner ──────────────────────────────────────────────────


class TestContentScanner:
    """Content scanner pattern detection."""

    def test_clean_content_no_matches(self):
        from r4.agent.scanner import ContentScanner

        scanner = ContentScanner()
        result = scanner.scan("Hello, how are you today?")
        assert not result.is_sensitive

    def test_detect_email_pii(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("Contact me at user@example.com for details")
        assert result.is_sensitive
        assert SensitivityLevel.PII in result.sensitivity

    def test_detect_api_key_secret(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("api_key: sk_live_abcdefghijklmnopqrstuvwxyz1234567890")
        assert result.is_sensitive
        assert SensitivityLevel.SECRETS in result.sensitivity

    def test_detect_bearer_token(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan(
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIx"
        )
        assert result.is_sensitive
        assert SensitivityLevel.SECRETS in result.sensitivity

    def test_detect_private_key(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK...")
        assert result.is_sensitive
        assert SensitivityLevel.SECRETS in result.sensitivity

    def test_detect_password(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("password: mysecretpassword123")
        assert result.is_sensitive
        assert SensitivityLevel.SECRETS in result.sensitivity

    def test_detect_connection_string(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("postgres://admin:secret@db.example.com:5432/mydb")
        assert result.is_sensitive
        assert SensitivityLevel.SECRETS in result.sensitivity

    def test_detect_doi_research_data(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("See paper 10.1038/nature12373 for methodology")
        assert SensitivityLevel.RESEARCH_DATA in result.sensitivity

    def test_detect_arxiv_id(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("Reference arXiv:2301.12345v2 discusses this")
        assert SensitivityLevel.RESEARCH_DATA in result.sensitivity

    def test_user_tags_add_sensitivity(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner()
        result = scanner.scan("Normal text", user_tags=["confidential"])
        assert result.is_sensitive
        assert SensitivityLevel.USER_TAGGED in result.sensitivity
        assert "confidential" in result.user_tags

    def test_scan_match_redaction(self):
        from r4.agent.scanner import ContentScanner

        scanner = ContentScanner()
        result = scanner.scan("Contact user@example.com")
        pii_matches = [m for m in result.matches if m.pattern_name == "email"]
        if pii_matches:
            redacted = pii_matches[0].redacted()
            assert "..." in redacted or "****" in redacted

    def test_scan_result_summary(self):
        from r4.agent.scanner import ContentScanner

        scanner = ContentScanner()
        result = scanner.scan("api_key: sk_live_abcdefghijklmnopqrstuvwxyz1234567890")
        summary = result.summary()
        assert "Secrets" in summary

    def test_disabled_category_not_scanned(self):
        from r4.agent.scanner import ContentScanner, SensitivityLevel

        scanner = ContentScanner(enable_pii=False)
        result = scanner.scan("Contact user@example.com")
        pii_matches = [m for m in result.matches if m.category == SensitivityLevel.PII]
        assert len(pii_matches) == 0


# ── Integration: Scanner → EgressGuard ──────────────────────────────


class TestScannerGuardIntegration:
    """Test real ContentScanner ↔ EgressGuard integration (no stub)."""

    def test_real_scanner_denies_secrets(self):
        from r4.agent.egress import EgressDecision, EgressGuard, ProjectEgressPolicy

        policy = ProjectEgressPolicy(project_id="test", auto_deny_on_secrets=True)
        guard = EgressGuard(user_id="alice", project_id="test", policy=policy)
        # Uses real ContentScanner — no stub
        decision = guard.check(
            tool_name="upload",
            destination_url="https://api.example.com/data",
            payload="password: supersecret123",
        )
        assert decision == EgressDecision.DENIED

    def test_real_scanner_approves_clean_on_allowlist(self):
        from r4.agent.egress import EgressDecision, EgressGuard, ProjectEgressPolicy

        policy = ProjectEgressPolicy(
            project_id="test",
            allowlisted_domains={"api.arxiv.org"},
        )
        guard = EgressGuard(user_id="alice", project_id="test", policy=policy)
        decision = guard.check(
            tool_name="search",
            destination_url="https://api.arxiv.org/search",
            payload="quantum computing review",
        )
        assert decision == EgressDecision.APPROVED

    def test_real_scanner_flags_pii_on_trusted_domain(self):
        from r4.agent.egress import EgressDecision, EgressGuard

        guard = EgressGuard(user_id="alice", project_id="test")
        guard.start_trust_session(["api.example.com"], duration_minutes=5)
        decision = guard.check(
            tool_name="fetch",
            destination_url="https://api.example.com/data",
            payload="Contact user@example.com for SSN 123-45-6789",
        )
        # PII detected → sensitive → trust session skipped → falls to pending
        assert decision == EgressDecision.PENDING

    def test_scan_result_to_flags_conversion(self):
        from r4.agent.egress import EgressGuard
        from r4.agent.scanner import ContentScanner

        scanner = ContentScanner()
        result = scanner.scan("api_key: sk_live_abcdefghijklmnopqrstuvwxyz1234567890")
        flags = EgressGuard._scan_result_to_flags(result)
        assert flags.has_secrets
        assert flags.is_sensitive
        assert len(flags.details) > 0
