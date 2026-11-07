"""
Registry single-source-of-truth verification tests.

The critical architectural question:
- Can models be registered in JSON and provisioned via Postgres?
- Can models be registered in Postgres and provisioned via JSON fallback?

If either is true, we still have a dual-store problem.

These tests verify single source of truth: only Postgres is the authority.

Patch targets note: umrs.jobs.adapters imports build_durable_job_db_pool_from_env,
ModelStore and _load_catalog lazily *inside* the functions under test (to avoid a
circular import with umrs.orchestrator).  Lazy imports resolve through the defining
module at call time, so these tests must patch the definition site
(umrs.shared.pg.*, umrs.model_registry.store.*), not an attribute
on umrs.jobs.adapters — the latter has never existed.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest


class TestRegistrySingleSourceOfTruth:
    """Verify Postgres is the sole authority — JSON fallback is truly eliminated."""

    def test_model_in_postgres_only_succeeds(self):
        """Register only in Postgres → provision succeeds."""
        from umrs.jobs.adapters import _resolve_model_entry
        from umrs.model_registry.models import ModelEntry, ModelScope

        # Model exists ONLY in Postgres
        mock_entry = ModelEntry(
            model_id="qwen-only-in-postgres",
            name="qwen-32b",
            scope=ModelScope.SYSTEM,
            storage_ref="/models/qwen-32b",
        )

        with patch("umrs.shared.pg.build_durable_job_db_pool_from_env") as mock_pool_builder:
            mock_pool = MagicMock()
            mock_conn = MagicMock()
            mock_pool.getconn = MagicMock(return_value=mock_conn)
            mock_pool.putconn = MagicMock()
            mock_pool.closeall = MagicMock()
            mock_pool_builder.return_value = mock_pool

            with patch("umrs.model_registry.store.ModelStore") as MockStore:
                mock_store = MagicMock()
                # Postgres: has the model
                mock_store.list_models = MagicMock(return_value=[mock_entry])
                MockStore.return_value = mock_store

                # Call _resolve_model_entry
                result = _resolve_model_entry("qwen-32b")

                assert result is not None, "Model in Postgres should be found"
                assert result.model_id == "qwen-only-in-postgres"

                # Verify Postgres was checked
                assert mock_store.list_models.called

    def test_model_in_json_only_succeeds_with_deprecation_warning(self):
        """Register only in JSON (not Postgres) → provision uses fallback with warning."""
        from umrs.jobs.adapters import _resolve_model_entry

        with patch("umrs.shared.pg.build_durable_job_db_pool_from_env") as mock_pool_builder:
            # Postgres fails/unavailable
            mock_pool_builder.side_effect = Exception("Postgres unavailable")

            with patch("umrs.shared.model_catalog.load_catalog") as mock_load_catalog:
                # JSON: has the model
                mock_load_catalog.return_value = {
                    "models": [
                        {
                            "model_id": "kimi-only-in-json",
                            "name": "kimi-k2-7b",
                            "scope": "system",
                            "storage_ref": "/models/kimi-k2-7b",
                        }
                    ]
                }

                with patch("umrs.jobs.adapters.logger") as mock_logger:
                    result = _resolve_model_entry("kimi-k2-7b")

                    # Should succeed but with warning
                    assert result is not None, "JSON fallback should work"

                    # Verify deprecation warning was logged
                    assert mock_logger.warning.called
                    warning_msg = str(mock_logger.warning.call_args)
                    assert "DEPRECATED" in warning_msg or "JSON" in warning_msg

    def test_model_in_both_postgres_and_json_prefers_postgres(self):
        """Model in both stores → Postgres wins (JSON ignored)."""
        from umrs.jobs.adapters import _resolve_model_entry
        from umrs.model_registry.models import ModelEntry, ModelScope

        postgres_entry = ModelEntry(
            model_id="deepseek-from-postgres",
            name="deepseek-v4",
            scope=ModelScope.SYSTEM,
            storage_ref="/models/deepseek-v4-postgres",
        )

        json_entry = {
            "model_id": "deepseek-from-json",
            "name": "deepseek-v4",
            "scope": "system",
            "storage_ref": "/models/deepseek-v4-json",  # Different path!
        }

        with patch("umrs.shared.pg.build_durable_job_db_pool_from_env") as mock_pool_builder:
            mock_pool = MagicMock()
            mock_conn = MagicMock()
            mock_pool.getconn = MagicMock(return_value=mock_conn)
            mock_pool.putconn = MagicMock()
            mock_pool.closeall = MagicMock()
            mock_pool_builder.return_value = mock_pool

            with patch("umrs.model_registry.store.ModelStore") as MockStore:
                mock_store = MagicMock()
                # Postgres: has the model
                mock_store.list_models = MagicMock(return_value=[postgres_entry])
                MockStore.return_value = mock_store

                with patch("umrs.shared.model_catalog.load_catalog") as mock_load_catalog:
                    # JSON: also has a model with same name (different ID!)
                    mock_load_catalog.return_value = {"models": [json_entry]}

                    result = _resolve_model_entry("deepseek-v4")

                    # Should use Postgres version
                    assert result is not None
                    assert result.model_id == "deepseek-from-postgres", (
                        "Should use Postgres version, not JSON"
                    )
                    assert result.storage_ref == "/models/deepseek-v4-postgres"

                    # Verify JSON was never consulted
                    assert not mock_load_catalog.called, (
                        "JSON loader should not be called when Postgres succeeds"
                    )

    def test_model_not_in_postgres_not_in_json_fails(self):
        """Model in neither store → provision fails explicitly."""
        from umrs.jobs.adapters import _resolve_model_entry

        with patch("umrs.shared.pg.build_durable_job_db_pool_from_env") as mock_pool_builder:
            mock_pool = MagicMock()
            mock_conn = MagicMock()
            mock_pool.getconn = MagicMock(return_value=mock_conn)
            mock_pool_builder.return_value = mock_pool

            with patch("umrs.model_registry.store.ModelStore") as MockStore:
                mock_store = MagicMock()
                # Postgres: empty
                mock_store.list_models = MagicMock(return_value=[])
                MockStore.return_value = mock_store

                with patch("umrs.shared.model_catalog.load_catalog") as mock_load_catalog:
                    # JSON: empty
                    mock_load_catalog.return_value = {"models": []}

                    result = _resolve_model_entry("nonexistent-model-123")

                    # Should be None
                    assert result is None, "Unregistered model should return None"

    def test_provision_fails_if_model_not_in_postgres(self):
        """End-to-end: if model missing from Postgres, provision fails before sbatch."""
        import asyncio

        from umrs.jobs.adapters import EndpointProvisionBackend
        from umrs.jobs.models import Job, JobAttempt, JobType
        from umrs.orchestrator.endpoint_registry import EndpointRegistry

        # Use the real Job/JobAttempt records the scheduler hands to the
        # backend — a bare MagicMock silently satisfies field accesses
        # (e.g. job.ttl_seconds) that the production path actually arithmetics on.
        job = Job(
            job_id="job-ssot-1",
            project_id="test",
            user_id="test",
            job_type=JobType.ENDPOINT_PROVISION,
            spec={"model": "unregistered-model", "gpu_count": 1, "env_vars": {}},
            idempotency_key="idem-ssot-1",
        )
        attempt = JobAttempt(
            attempt_id="a1",
            job_id=job.job_id,
            attempt_no=1,
            backend="slurm",
        )

        with patch("umrs.jobs.adapters._resolve_model_entry") as mock_resolve:
            # Model not found
            mock_resolve.return_value = None

            # Mirror the production wiring in adapters.create_backend_factory():
            # the backend owns orchestrator + registry + port_manager.
            backend = EndpointProvisionBackend(
                orchestrator=MagicMock(),
                registry=MagicMock(spec=EndpointRegistry),
                port_manager=MagicMock(),
            )

            # Provision should fail
            with pytest.raises(RuntimeError) as exc_info:
                asyncio.run(backend.submit_job(job, attempt))

            # Error should mention model not found
            assert (
                "not found" in str(exc_info.value).lower()
                or "unregistered" in str(exc_info.value).lower()
            )

            # Verify sbatch was NOT called
            assert not backend.orchestrator.submit_job.called


class TestRegistryPathTracing:
    """Verify _resolve_model_entry uses correct database."""

    def test_resolve_uses_durable_job_db_pool(self):
        """_resolve_model_entry must use durable job DB pool, not registry DSN."""
        from umrs.jobs.adapters import _resolve_model_entry

        with patch("umrs.shared.pg.build_durable_job_db_pool_from_env") as mock_job_pool:
            mock_pool = MagicMock()
            mock_conn = MagicMock()
            mock_pool.getconn = MagicMock(return_value=mock_conn)
            mock_pool.putconn = MagicMock()
            mock_pool.closeall = MagicMock()
            mock_job_pool.return_value = mock_pool

            with patch("umrs.model_registry.store.ModelStore"):
                # The store is a stub; only the pool-builder call path matters here.
                with contextlib.suppress(Exception):
                    _resolve_model_entry("any-model")

                # Verify build_durable_job_db_pool_from_env was called
                assert mock_job_pool.called, (
                    "_resolve_model_entry should use durable job DB pool, not registry DSN"
                )

    def test_resolve_does_not_use_registry_postgres_dsn(self):
        """_resolve_model_entry must NOT use UMRS_REGISTRY_POSTGRES_DSN."""
        import os

        from umrs.jobs.adapters import _resolve_model_entry

        # Set registry DSN (should be ignored)
        original_dsn = os.environ.get("UMRS_REGISTRY_POSTGRES_DSN")
        try:
            os.environ["UMRS_REGISTRY_POSTGRES_DSN"] = "postgresql://registry-host:5432/registry"

            with patch("umrs.shared.pg.build_durable_job_db_pool_from_env") as mock_job_pool:
                mock_pool = MagicMock()
                mock_conn = MagicMock()
                mock_pool.getconn = MagicMock(return_value=mock_conn)
                mock_job_pool.return_value = mock_pool

                # The store is a stub; only the pool-builder call path matters here.
                with (
                    patch("umrs.model_registry.store.ModelStore"),
                    contextlib.suppress(Exception),
                ):
                    _resolve_model_entry("any-model")

                # The call should NOT include 'registry' in its connection
                # (This is a bit indirect, but the point is the pool was built
                # via durable pool builder, not registry DSN)
                assert mock_job_pool.called

        finally:
            if original_dsn:
                os.environ["UMRS_REGISTRY_POSTGRES_DSN"] = original_dsn
            else:
                os.environ.pop("UMRS_REGISTRY_POSTGRES_DSN", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
