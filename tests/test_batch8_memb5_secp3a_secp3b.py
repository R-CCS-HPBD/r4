"""Batch 8 tests: MEM-B5 (Step Replay), SEC-P3a (Workload Identity),
SEC-P3b (Secret Broker).

Plain imports by ADR-053 (the spec-load machinery predates the jobs↔api
cycle fix; umrs resolves as a namespace package from the repo root).
"""

from __future__ import annotations

import os
import time
import unittest

from umrs.jobs.checkpoint_store import CheckpointStore
from umrs.jobs.execution_trace_store import ExecutionTraceStore
from umrs.jobs.secret_broker import SecretBroker
from umrs.jobs.step_replay import ReplayRequest, StepReplayEngine
from umrs.jobs.workload_identity import WorkloadIdentityIssuer

from r4_contracts.provenance import (
    ReplayResultRef,
    SecretLeaseRef,
    WorkloadIdentityRef,
)

UMRS_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "umrs", "src"))

# ╔═════════════════════════════════════════════════════════════╗
# ║ MEM-B5: Step Replay Engine                                ║
# ╚═════════════════════════════════════════════════════════════╝


class TestStepReplayBasic(unittest.TestCase):
    """Test basic replay creation."""

    def setUp(self):
        self.trace_store = ExecutionTraceStore()
        self.engine = StepReplayEngine(self.trace_store)
        # Create a source trace
        self.source = self.trace_store.create_trace(
            job_id="orig-job",
            step_name="generate",
            step_type="llm",
            inputs={"prompt": "hello", "temp": 0.7},
            model_ref={"model": "gpt-4"},
        )

    def test_replay_creates_new_trace(self):
        result = self.engine.replay(
            ReplayRequest(
                source_trace_id=self.source.trace_id,
                user_id="alice",
                reason="debugging",
            )
        )
        self.assertTrue(result.replay_id.startswith("rpl-"))
        self.assertEqual(result.source_trace_id, self.source.trace_id)
        self.assertEqual(result.step_name, "generate")
        self.assertIsNotNone(result.inputs_hash)  # auto-computed
        self.assertIsNotNone(result.inputs_snapshot)
        self.assertEqual(result.inputs_snapshot["prompt"], "hello")

    def test_replay_preserves_inputs(self):
        result = self.engine.replay(
            ReplayRequest(
                source_trace_id=self.source.trace_id,
                user_id="bob",
            )
        )
        # New trace should have same inputs
        new_trace = self.trace_store.get_trace(result.new_trace_id)
        self.assertIsNotNone(new_trace)
        self.assertEqual(new_trace.inputs_snapshot["prompt"], "hello")
        # Parent trace should be the source
        self.assertEqual(new_trace.parent_trace_id, self.source.trace_id)

    def test_replay_with_param_overrides(self):
        result = self.engine.replay(
            ReplayRequest(
                source_trace_id=self.source.trace_id,
                user_id="carol",
                param_overrides={"temp": 0.3},
            )
        )
        self.assertEqual(result.inputs_snapshot["prompt"], "hello")
        self.assertEqual(result.inputs_snapshot["temp"], 0.3)
        # Hash should differ from original when overrides applied
        self.assertIsNotNone(result.inputs_snapshot)

    def test_replay_source_not_found_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.replay(
                ReplayRequest(
                    source_trace_id="nonexistent",
                    user_id="u",
                )
            )
        self.assertIn("not found", str(ctx.exception))

    def test_replay_no_inputs_raises(self):
        empty = self.trace_store.create_trace(
            job_id="empty-job",
            step_name="step",
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.replay(
                ReplayRequest(
                    source_trace_id=empty.trace_id,
                    user_id="u",
                )
            )
        self.assertIn("no input data", str(ctx.exception))


class TestStepReplayQuery(unittest.TestCase):
    """Test replay listing and lookup."""

    def setUp(self):
        self.trace_store = ExecutionTraceStore()
        self.engine = StepReplayEngine(self.trace_store)
        src = self.trace_store.create_trace(
            job_id="j",
            step_name="s",
            inputs={"x": 1},
        )
        self.r1 = self.engine.replay(
            ReplayRequest(
                source_trace_id=src.trace_id,
                user_id="alice",
            )
        )
        self.r2 = self.engine.replay(
            ReplayRequest(
                source_trace_id=src.trace_id,
                user_id="bob",
            )
        )

    def test_get_replay(self):
        r = self.engine.get_replay(self.r1.replay_id)
        self.assertIsNotNone(r)
        self.assertEqual(r.user_id, "alice")

    def test_get_nonexistent(self):
        self.assertIsNone(self.engine.get_replay("nonexistent"))

    def test_list_all(self):
        results = self.engine.list_replays()
        self.assertEqual(len(results), 2)

    def test_list_by_user(self):
        results = self.engine.list_replays(user_id="alice")
        self.assertEqual(len(results), 1)

    def test_find_by_inputs_hash(self):
        # Use the hash from the first replay
        results = self.engine.find_by_inputs_hash(self.r1.inputs_hash)
        self.assertGreaterEqual(len(results), 1)

    def test_replay_serialization(self):
        d = self.r1.to_dict()
        self.assertIn("replay_id", d)
        self.assertIn("source_trace_id", d)
        self.assertIn("created_at", d)


# ╔═════════════════════════════════════════════════════════════╗
# ║ SEC-P3a: Workload Identity Issuer                         ║
# ╚═════════════════════════════════════════════════════════════╝


class TestWorkloadIdentityIssue(unittest.TestCase):
    """Test identity token issuance."""

    def setUp(self):
        self.issuer = WorkloadIdentityIssuer()

    def test_issue_basic(self):
        identity, token = self.issuer.issue(
            job_id="j1",
            step_name="inference",
            scope="inference",
        )
        self.assertTrue(identity.identity_id.startswith("wid-"))
        self.assertTrue(len(token) > 20)
        self.assertTrue(identity.is_valid)

    def test_issue_with_options(self):
        identity, token = self.issuer.issue(
            job_id="j2",
            step_name="download",
            scope="download",
            ttl_seconds=300,
            tier="project",
            owner_id="alice",
            metadata={"model": "gpt-4"},
        )
        self.assertEqual(identity.tier, "project")
        self.assertEqual(identity.owner_id, "alice")
        self.assertEqual(identity.metadata["model"], "gpt-4")

    def test_invalid_scope_rejects(self):
        with self.assertRaises(ValueError):
            self.issuer.issue(
                job_id="j",
                step_name="s",
                scope="invalid_scope",
            )

    def test_invalid_ttl_rejects(self):
        with self.assertRaises(ValueError):
            self.issuer.issue(
                job_id="j",
                step_name="s",
                scope="inference",
                ttl_seconds=99999,
            )
        with self.assertRaises(ValueError):
            self.issuer.issue(
                job_id="j",
                step_name="s",
                scope="inference",
                ttl_seconds=0,
            )

    def test_token_not_stored_plaintext(self):
        identity, token = self.issuer.issue(
            job_id="j",
            step_name="s",
            scope="inference",
        )
        # The token_hash should be a SHA-256 hex, not the token itself
        self.assertNotEqual(identity.token_hash, token)
        self.assertEqual(len(identity.token_hash), 64)  # SHA-256 hex


class TestWorkloadIdentityValidation(unittest.TestCase):
    """Test token validation."""

    def setUp(self):
        self.issuer = WorkloadIdentityIssuer()

    def test_validate_valid_token(self):
        identity, token = self.issuer.issue(
            job_id="j1",
            step_name="s",
            scope="inference",
        )
        result = self.issuer.validate(token)
        self.assertIsNotNone(result)
        self.assertEqual(result.identity_id, identity.identity_id)

    def test_validate_invalid_token(self):
        self.assertIsNone(self.issuer.validate("bogus-token"))

    def test_validate_revoked_token(self):
        identity, token = self.issuer.issue(
            job_id="j",
            step_name="s",
            scope="inference",
        )
        self.issuer.revoke(identity.identity_id)
        self.assertIsNone(self.issuer.validate(token))

    def test_validate_expired_token(self):
        identity, token = self.issuer.issue(
            job_id="j",
            step_name="s",
            scope="inference",
            ttl_seconds=1,
        )
        time.sleep(1.1)
        self.assertIsNone(self.issuer.validate(token))


class TestWorkloadIdentityRevoke(unittest.TestCase):
    """Test identity revocation."""

    def setUp(self):
        self.issuer = WorkloadIdentityIssuer()

    def test_revoke_existing(self):
        identity, _ = self.issuer.issue(
            job_id="j",
            step_name="s",
            scope="inference",
        )
        ok = self.issuer.revoke(identity.identity_id)
        self.assertTrue(ok)
        self.assertTrue(identity.is_revoked)

    def test_revoke_nonexistent(self):
        ok = self.issuer.revoke("nonexistent")
        self.assertFalse(ok)


class TestWorkloadIdentityList(unittest.TestCase):
    """Test identity listing."""

    def setUp(self):
        self.issuer = WorkloadIdentityIssuer()
        self.issuer.issue(job_id="j1", step_name="s1", scope="inference")
        self.issuer.issue(job_id="j1", step_name="s2", scope="download")
        self.issuer.issue(job_id="j2", step_name="s3", scope="inference")

    def test_list_all(self):
        results = self.issuer.list_identities()
        self.assertEqual(len(results), 3)

    def test_list_by_job(self):
        results = self.issuer.list_identities(job_id="j1")
        self.assertEqual(len(results), 2)

    def test_list_by_scope(self):
        results = self.issuer.list_identities(scope="inference")
        self.assertEqual(len(results), 2)

    def test_list_active_only(self):
        results = self.issuer.list_identities(active_only=True)
        self.assertEqual(len(results), 3)


class TestWorkloadIdentityCleanup(unittest.TestCase):
    """Test expired identity cleanup."""

    def test_cleanup_removes_expired(self):
        issuer = WorkloadIdentityIssuer()
        issuer.issue(job_id="j", step_name="s", scope="inference", ttl_seconds=1)
        time.sleep(1.1)
        count = issuer.cleanup_expired()
        self.assertEqual(count, 1)
        self.assertEqual(len(issuer.list_identities()), 0)


class TestWorkloadIdentitySerialization(unittest.TestCase):
    """Test serialization."""

    def test_to_dict(self):
        issuer = WorkloadIdentityIssuer()
        identity, _ = issuer.issue(
            job_id="j",
            step_name="s",
            scope="inference",
        )
        d = identity.to_dict()
        self.assertIn("identity_id", d)
        self.assertIn("is_valid", d)
        self.assertIn("issued_at", d)
        self.assertIn("expires_at", d)


# ╔═════════════════════════════════════════════════════════════╗
# ║ SEC-P3b: Secret Broker                                    ║
# ╚═════════════════════════════════════════════════════════════╝


class TestSecretBrokerIssue(unittest.TestCase):
    """Test secret lease issuance."""

    def setUp(self):
        self.broker = SecretBroker()

    def test_issue_basic(self):
        lease, secret = self.broker.issue_lease(
            job_id="j1",
            secret_name="s3_creds",
            scope="download",
        )
        self.assertTrue(lease.lease_id.startswith("lease-"))
        self.assertTrue(len(secret) > 30)
        self.assertTrue(lease.is_valid)

    def test_issue_with_options(self):
        lease, _ = self.broker.issue_lease(
            job_id="j2",
            secret_name="litellm_key",
            scope="inference",
            lease_seconds=600,
            bound_to_step="generate",
            tier="system",
            owner_id="admin",
        )
        self.assertEqual(lease.bound_to_step, "generate")
        self.assertEqual(lease.tier, "system")

    def test_invalid_scope_rejects(self):
        with self.assertRaises(ValueError):
            self.broker.issue_lease(
                job_id="j",
                secret_name="s",
                scope="bad_scope",
            )

    def test_invalid_lease_seconds_rejects(self):
        with self.assertRaises(ValueError):
            self.broker.issue_lease(
                job_id="j",
                secret_name="s",
                scope="inference",
                lease_seconds=99999,
            )

    def test_secret_not_stored_plaintext(self):
        lease, secret = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        self.assertNotEqual(lease.secret_hash, secret)
        self.assertEqual(len(lease.secret_hash), 64)

    def test_audit_on_issue(self):
        lease, _ = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        trail = self.broker.get_audit_trail(lease_id=lease.lease_id)
        self.assertTrue(len(trail) >= 1)
        self.assertEqual(trail[0].operation, "issue")


class TestSecretBrokerValidation(unittest.TestCase):
    """Test secret validation."""

    def setUp(self):
        self.broker = SecretBroker()

    def test_validate_valid_secret(self):
        lease, secret = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        result = self.broker.validate_secret(secret)
        self.assertIsNotNone(result)
        self.assertEqual(result.lease_id, lease.lease_id)
        self.assertEqual(result.access_count, 1)

    def test_validate_increments_access_count(self):
        lease, secret = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        self.broker.validate_secret(secret)
        self.broker.validate_secret(secret)
        result = self.broker.validate_secret(secret)
        self.assertEqual(result.access_count, 3)

    def test_validate_invalid_secret(self):
        self.assertIsNone(self.broker.validate_secret("bogus"))

    def test_validate_revoked_secret(self):
        lease, secret = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        self.broker.revoke_lease(lease.lease_id)
        self.assertIsNone(self.broker.validate_secret(secret))

    def test_validate_expired_secret(self):
        lease, secret = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
            lease_seconds=1,
        )
        time.sleep(1.1)
        self.assertIsNone(self.broker.validate_secret(secret))


class TestSecretBrokerRevoke(unittest.TestCase):
    """Test lease revocation."""

    def setUp(self):
        self.broker = SecretBroker()

    def test_revoke_single(self):
        lease, _ = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        ok = self.broker.revoke_lease(lease.lease_id, actor="admin")
        self.assertTrue(ok)
        self.assertTrue(lease.is_revoked)
        self.assertIsNotNone(lease.revoked_at)

    def test_revoke_nonexistent(self):
        ok = self.broker.revoke_lease("nonexistent")
        self.assertFalse(ok)

    def test_revoke_job_leases(self):
        self.broker.issue_lease(job_id="j1", secret_name="s1", scope="inference")
        self.broker.issue_lease(job_id="j1", secret_name="s2", scope="download")
        self.broker.issue_lease(job_id="j2", secret_name="s3", scope="inference")
        count = self.broker.revoke_job_leases("j1", actor="system")
        self.assertEqual(count, 2)
        # j2 should still be valid
        j2_leases = self.broker.list_leases(job_id="j2", active_only=True)
        self.assertEqual(len(j2_leases), 1)

    def test_revoke_audit_trail(self):
        lease, _ = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        self.broker.revoke_lease(lease.lease_id, actor="admin")
        trail = self.broker.get_audit_trail(
            lease_id=lease.lease_id,
            operation="revoke",
        )
        self.assertTrue(len(trail) >= 1)


class TestSecretBrokerRenew(unittest.TestCase):
    """Test lease renewal."""

    def setUp(self):
        self.broker = SecretBroker()

    def test_renew_extends_expiry(self):
        lease, _ = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
            lease_seconds=600,
        )
        original_expiry = lease.expires_at
        ok = self.broker.renew_lease(lease.lease_id, 300)
        self.assertTrue(ok)
        self.assertGreater(lease.expires_at, original_expiry)

    def test_renew_beyond_max_fails(self):
        lease, _ = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
            lease_seconds=1800,
        )
        # Try to extend way beyond max
        ok = self.broker.renew_lease(lease.lease_id, 99999)
        self.assertFalse(ok)

    def test_renew_revoked_fails(self):
        lease, _ = self.broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        self.broker.revoke_lease(lease.lease_id)
        ok = self.broker.renew_lease(lease.lease_id, 300)
        self.assertFalse(ok)


class TestSecretBrokerList(unittest.TestCase):
    """Test lease listing."""

    def setUp(self):
        self.broker = SecretBroker()
        self.broker.issue_lease(job_id="j1", secret_name="s1", scope="inference")
        self.broker.issue_lease(job_id="j1", secret_name="s2", scope="download")
        self.broker.issue_lease(job_id="j2", secret_name="s3", scope="inference")

    def test_list_all(self):
        results = self.broker.list_leases()
        self.assertEqual(len(results), 3)

    def test_list_by_job(self):
        results = self.broker.list_leases(job_id="j1")
        self.assertEqual(len(results), 2)

    def test_list_by_scope(self):
        results = self.broker.list_leases(scope="download")
        self.assertEqual(len(results), 1)


class TestSecretBrokerAudit(unittest.TestCase):
    """Test audit trail."""

    def test_full_lifecycle_audit(self):
        broker = SecretBroker()
        lease, secret = broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
            lease_seconds=600,
        )
        broker.validate_secret(secret)
        broker.renew_lease(lease.lease_id, 300)
        broker.revoke_lease(lease.lease_id)

        trail = broker.get_audit_trail(lease_id=lease.lease_id)
        ops = [e.operation for e in trail]
        self.assertIn("issue", ops)
        self.assertIn("access", ops)
        self.assertIn("renew", ops)
        self.assertIn("revoke", ops)


class TestSecretBrokerCleanup(unittest.TestCase):
    """Test expired lease cleanup."""

    def test_cleanup_removes_expired(self):
        broker = SecretBroker()
        broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
            lease_seconds=1,
        )
        time.sleep(1.1)
        count = broker.cleanup_expired()
        self.assertEqual(count, 1)


class TestSecretBrokerSerialization(unittest.TestCase):
    """Test serialization."""

    def test_lease_to_dict(self):
        broker = SecretBroker()
        lease, _ = broker.issue_lease(
            job_id="j",
            secret_name="s",
            scope="inference",
        )
        d = lease.to_dict()
        self.assertIn("lease_id", d)
        self.assertIn("is_valid", d)
        self.assertIn("secret_hash", d)

    def test_audit_entry_to_dict(self):
        broker = SecretBroker()
        broker.issue_lease(job_id="j", secret_name="s", scope="inference")
        trail = broker.get_audit_trail()
        self.assertTrue(len(trail) > 0)
        d = trail[0].to_dict()
        self.assertIn("operation", d)
        self.assertIn("timestamp", d)


# ╔═════════════════════════════════════════════════════════════╗
# ║ MEM-B6 / MEM-C3 Existence Validation                      ║
# ╚═════════════════════════════════════════════════════════════╝


class TestMEMB6CheckpointStoreExists(unittest.TestCase):
    """Validate that MEM-B6 checkpoint infrastructure exists."""

    def test_checkpoint_store_importable(self):
        store = CheckpointStore()
        self.assertIsNotNone(store)

    def test_checkpoint_save_and_retrieve(self):
        store = CheckpointStore()
        cp = store.save_checkpoint(
            job_id="j1",
            attempt_id="a1",
            step_name="train",
            state_inline={"epoch": 5, "loss": 0.3},
        )
        self.assertIsNotNone(cp)
        latest = store.latest_checkpoint("j1", "a1", "train")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.state_inline["epoch"], 5)


class TestMEMC3ContextPackExists(unittest.TestCase):
    """Validate that MEM-C3 context pack API module exists."""

    def test_context_api_importable(self):
        context_api = os.path.join(UMRS_SRC, "umrs", "api", "context.py")
        self.assertTrue(os.path.exists(context_api))

    def test_context_pack_endpoint_defined(self):
        context_api = os.path.join(UMRS_SRC, "umrs", "api", "context.py")
        with open(context_api) as f:
            content = f.read()
        self.assertIn("context/pack", content)


# ╔═════════════════════════════════════════════════════════════╗
# ║ r4-contracts DTOs                                          ║
# ╚═════════════════════════════════════════════════════════════╝


class TestReplayResultRefDTO(unittest.TestCase):
    def test_to_dict(self):
        ref = ReplayResultRef(
            replay_id="rpl-001",
            source_trace_id="trc-src",
            new_trace_id="trc-new",
            new_job_id="j-new",
            step_name="generate",
        )
        d = ref.to_dict()
        self.assertEqual(d["replay_id"], "rpl-001")
        self.assertEqual(d["step_name"], "generate")


class TestWorkloadIdentityRefDTO(unittest.TestCase):
    def test_to_dict(self):
        ref = WorkloadIdentityRef(
            identity_id="wid-001",
            job_id="j1",
            scope="inference",
        )
        d = ref.to_dict()
        self.assertEqual(d["identity_id"], "wid-001")
        self.assertTrue(d["is_valid"])


class TestSecretLeaseRefDTO(unittest.TestCase):
    def test_to_dict(self):
        ref = SecretLeaseRef(
            lease_id="lease-001",
            job_id="j1",
            secret_name="s3_creds",
            scope="download",
        )
        d = ref.to_dict()
        self.assertEqual(d["lease_id"], "lease-001")
        self.assertEqual(d["secret_name"], "s3_creds")


if __name__ == "__main__":
    unittest.main()
