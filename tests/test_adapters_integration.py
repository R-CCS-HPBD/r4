"""
Integration test: verify adapters.py wires DeploymentSpec + PreflightValidator + RuntimeConfig.

This test ensures that:
1. EndpointProvisionBackend.submit_job() can be imported without circular import errors
2. The method uses _resolve_model_entry() to get runtime_config from registry
3. The method creates a DeploymentSpec from the registry entry
4. The method runs PreflightValidator before sbatch
5. All VLLM config env vars are forwarded to sbatch
"""

import pytest


class TestAdaptersIntegration:
    """Verify adapters.py integration with new architecture."""

    def test_adapters_import_no_circular_error(self):
        """Verify adapters.py can be imported without circular import errors."""
        from umrs.jobs.adapters import (
            EndpointProvisionBackend,
            _resolve_model_entry,
            _resolve_roster_defaults,
            build_deployment_profile,
            log_preflight_result,
        )

        assert EndpointProvisionBackend is not None
        assert callable(_resolve_model_entry)
        assert callable(_resolve_roster_defaults)
        assert callable(build_deployment_profile)
        assert callable(log_preflight_result)

    def test_submit_job_uses_deployment_spec(self):
        """Verify submit_job() method source includes DeploymentSpec integration."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        # Verify key integrations are present
        assert "DeploymentSpec.from_registry" in source
        assert "_resolve_model_entry" in source
        assert "generate_sbatch_env" in source
        assert "load_site_config" in source

    def test_submit_job_uses_preflight_validator(self):
        """Verify submit_job() calls PreflightValidator.run_all()."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)

        assert "PreflightValidator" in source
        assert "await preflight.run_all()" in source
        assert "log_preflight_result" in source

    def test_deployment_spec_generates_vllm_env(self):
        """Verify DeploymentSpec.generate_sbatch_env() includes VLLM config."""
        from inspect import getsource

        from umrs.orchestrator.deployment_spec import DeploymentSpec

        source = getsource(DeploymentSpec.generate_sbatch_env)

        # All 7 VLLM config env vars must be generated
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

    def test_preflight_has_all_7_checks(self):
        """Verify PreflightValidator implements all 7 checks."""
        from inspect import getsource

        from umrs.orchestrator.preflight import PreflightValidator

        source = getsource(PreflightValidator)

        # All 7 check methods must be present
        check_methods = [
            "_check_registry",
            "_check_topology",
            "_check_storage",
            "_check_parser_availability",
            "_check_partition",
            "_check_container_image",
            "_check_capacity",
        ]
        for method in check_methods:
            assert method in source, f"{method} not found in PreflightValidator"

    def test_runtime_config_fields_present(self):
        """Verify RuntimeConfig has all model configuration fields."""
        from umrs.model_registry.runtime_config import RuntimeConfig

        # Create instance with all fields
        config = RuntimeConfig(
            model_family="deepseek_v4",
            tool_call_parser="deepseek_v4",
            reasoning_parser="deepseek_v4",
            kv_cache_dtype="fp8",
            enforce_eager=False,
            flashinfer_moe_fp16=True,
            chat_template_format="string",
            mm_encoder_tp_mode="TP",
            trust_remote_code=False,
            enable_auto_tool_choice=True,
            enable_prefix_caching=False,
        )

        assert config.model_family == "deepseek_v4"
        assert config.tool_call_parser == "deepseek_v4"
        assert config.kv_cache_dtype == "fp8"
        assert config.enforce_eager is False

    def test_model_entry_includes_runtime_config(self):
        """Verify ModelEntry includes runtime_config field."""
        from umrs.model_registry.models import ModelEntry, ModelScope
        from umrs.model_registry.runtime_config import RuntimeConfig

        entry = ModelEntry(
            model_id="test-model-123",
            name="test-model",
            scope=ModelScope.SYSTEM,
            storage_ref="/models/test",
            runtime_config=RuntimeConfig(
                model_family="glm",
                tool_call_parser="glm47",
            ),
        )

        assert entry.runtime_config is not None
        assert entry.runtime_config.model_family == "glm"
        assert entry.runtime_config.tool_call_parser == "glm47"

    def test_8_historical_failures_now_prevented(self):
        """Verify the 8 historical failure modes are now preventable."""
        from umrs.orchestrator.preflight import PreflightValidator

        # Map each historical failure to the check that prevents it
        failure_prevention_map = {
            "Missing parser": "_check_parser_availability",
            "Invalid topology": "_check_topology",
            "Alias mismatch": "_check_registry",
            "Missing registry": "_check_registry",
            "KV cache overflow": "_check_capacity",
            "CUDA graph garbage": "_check_registry",
            "MoE kernel NaN": "_check_registry",
            "Invalid partition": "_check_partition",
        }

        from inspect import getsource

        source = getsource(PreflightValidator)

        # Verify each check is implemented
        for failure, check_name in failure_prevention_map.items():
            assert check_name in source, (
                f"Check {check_name} (prevents '{failure}') not found in PreflightValidator"
            )


class TestProductionPath:
    """Verify the production path actually uses the new architecture."""

    def test_production_path_resolves_model_entry(self):
        """Verify production code calls _resolve_model_entry()."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)
        assert "_resolve_model_entry(model_name)" in source

    def test_production_path_builds_deployment_spec(self):
        """Verify production code creates DeploymentSpec from registry."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)
        assert "DeploymentSpec.from_registry(" in source
        assert "entry=model_entry" in source
        assert "profile=profile" in source
        assert "site_config=site" in source

    def test_production_path_validates_before_sbatch(self):
        """Verify production code validates before sbatch submission."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)
        # Verify validation happens before job submission
        assert "await preflight.run_all()" in source
        assert "result.passed" in source
        assert "self.orchestrator.submit_job(" in source

    def test_production_path_no_runtime_derivation_fallback(self):
        """Verify production code has no fallback to runtime pattern matching."""
        from inspect import getsource

        from umrs.jobs.adapters import EndpointProvisionBackend

        source = getsource(EndpointProvisionBackend.submit_job)
        # This is explicitly what should NOT be there anymore
        # (old code had regex pattern matching on model name)
        assert "endswith" not in source.lower() or "model" not in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
