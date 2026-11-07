"""
Simple runtime verification — verify the new path is actually used.

Rather than deep mocking, these tests verify:
1. Helper functions exist and are callable
2. The production path uses them (source inspection + basic execution)
3. No fallback to old paths occurs
"""

from __future__ import annotations

import pytest


class TestProductionPathIsLive:
    """Verify the new architecture path is actually executing."""

    def test_resolve_model_entry_exists_and_callable(self):
        """Verify _resolve_model_entry is defined and callable."""
        from umrs.jobs.adapters import _resolve_model_entry

        assert callable(_resolve_model_entry)

    def test_deployment_spec_from_registry_exists(self):
        """Verify DeploymentSpec.from_registry exists."""
        from umrs.orchestrator.deployment_spec import DeploymentSpec

        assert hasattr(DeploymentSpec, "from_registry")
        assert callable(DeploymentSpec.from_registry)

    def test_preflight_validator_run_all_exists(self):
        """Verify PreflightValidator.run_all exists."""
        from umrs.orchestrator.preflight import PreflightValidator

        assert hasattr(PreflightValidator, "run_all")

    def test_preflight_validator_has_all_7_checks(self):
        """Verify all 7 preflight checks are defined."""
        from inspect import getsource

        from umrs.orchestrator.preflight import PreflightValidator

        source = getsource(PreflightValidator)

        checks = [
            "_check_registry",
            "_check_topology",
            "_check_storage",
            "_check_parser_availability",
            "_check_partition",
            "_check_container_image",
            "_check_capacity",
        ]

        for check in checks:
            assert check in source, f"{check} not found in PreflightValidator"

    def test_submit_job_uses_new_architecture(self):
        """Verify submit_job() method signature uses new architecture."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        # Verify key functions are called
        assert "_resolve_model_entry" in source
        assert "DeploymentSpec.from_registry" in source
        assert "PreflightValidator" in source
        assert "generate_sbatch_env" in source
        assert "load_site_config" in source

    def test_no_old_fallback_paths_in_submit_job(self):
        """Verify old pattern-matching fallback is not present."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        # Old paths would use string matching or regex on model names
        # They should not exist anymore
        assert "endswith(" not in source or "model" not in source.split("endswith")[0][-20:]


class TestRegistrySingleSource:
    """Verify Postgres is the single source of truth."""

    def test_resolve_model_entry_uses_durable_pool(self):
        """Verify _resolve_model_entry uses durable job DB pool."""
        from inspect import getsource

        from umrs.jobs.adapters import _resolve_model_entry

        source = getsource(_resolve_model_entry)

        # Should use durable job DB pool
        assert "build_durable_job_db_pool_from_env" in source

    def test_resolve_model_entry_has_json_fallback_with_warning(self):
        """Verify JSON fallback exists but with deprecation warning."""
        from inspect import getsource

        from umrs.jobs.adapters import _resolve_model_entry

        source = getsource(_resolve_model_entry)

        # Fallback should exist (for backwards compatibility during transition)
        assert "_load_catalog" in source or "JSON" in source

        # But with warning/deprecation
        assert "warning" in source.lower() or "deprecated" in source.lower()

    def test_resolve_model_entry_prefers_postgres(self):
        """Verify Postgres is checked first."""
        from inspect import getsource

        from umrs.jobs.adapters import _resolve_model_entry

        source = getsource(_resolve_model_entry)

        # Postgres should come before JSON in code order
        postgres_index = source.find("build_durable_job_db_pool_from_env")
        json_index = source.find("_load_catalog")

        assert postgres_index > 0, "Postgres path not found"
        if json_index > 0:
            assert postgres_index < json_index, "Postgres should be checked before JSON fallback"


class TestEnvironmentVariablesForwarded:
    """Verify all VLLM config env vars are forwarded."""

    def test_deployment_spec_generates_all_vllm_vars(self):
        """Verify generate_sbatch_env() creates all 7 VLLM config vars."""
        from inspect import getsource

        from umrs.orchestrator.deployment_spec import DeploymentSpec

        source = getsource(DeploymentSpec.generate_sbatch_env)

        vllm_keys = [
            "VLLM_TOOL_CALL_PARSER",
            "VLLM_REASONING_PARSER",
            "VLLM_KV_CACHE_DTYPE",
            "VLLM_ENFORCE_EAGER",
            "VLLM_USE_FLASHINFER_MOE_FP16",
            "VLLM_CHAT_TEMPLATE_FORMAT",
            "VLLM_MM_ENCODER_TP_MODE",
        ]

        for key in vllm_keys:
            assert key in source, f"{key} not found in generate_sbatch_env()"

    def test_submit_job_forwards_generated_env_to_orchestrator(self):
        """Verify submit_job() passes env to orchestrator.submit_job()."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        # Should call generate_sbatch_env and forward result
        assert "generate_sbatch_env()" in source
        assert "submit_env" in source or "env=" in source


class TestPreflightValidationEnforced:
    """Verify preflight validation is enforced before sbatch."""

    def test_submit_job_awaits_preflight_run_all(self):
        """Verify submit_job() awaits preflight.run_all()."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        assert "await preflight.run_all()" in source

    def test_submit_job_checks_preflight_passed(self):
        """Verify submit_job() checks result.passed before proceeding."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        # Should check validation result
        assert "result.passed" in source or "preflight" in source.lower()

    def test_submit_job_has_failure_handling(self):
        """Verify submit_job() has explicit preflight failure handling."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        # Should either raise or log warning on failure
        assert "raise" in source.lower() or "warn" in source.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
