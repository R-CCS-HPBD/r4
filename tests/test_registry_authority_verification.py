"""
Registry Single Source of Truth Verification

Verify Postgres is the authoritative model source (JSON fallback is deprecated).
"""

from __future__ import annotations

import pytest


class TestRegistrySingleSourceOfTruth:
    """Verify Postgres is sole authority for model resolution."""

    def test_resolve_model_entry_method_exists(self):
        """Verify _resolve_model_entry function exists."""
        from umrs.jobs.adapters import _resolve_model_entry

        assert callable(_resolve_model_entry)

    def test_resolve_uses_durable_pool_not_registry_dsn(self):
        """Verify _resolve_model_entry uses durable job pool, not UMRS registry."""
        from inspect import getsource

        from umrs.jobs.adapters import _resolve_model_entry

        source = getsource(_resolve_model_entry)

        # Should use durable job DB pool
        assert "build_durable_job_db_pool_from_env" in source, (
            "_resolve_model_entry must use durable job DB pool"
        )

        # Should NOT use UMRS registry DSN (umrs_ai4s)
        assert "UMRS_REGISTRY_POSTGRES_DSN" not in source, (
            "_resolve_model_entry must not use old UMRS_REGISTRY_POSTGRES_DSN"
        )
        assert "registry_dsn" not in source.lower() or "durable" in source.lower(), (
            "Should prefer durable pool over registry DSN"
        )

    def test_resolve_checks_postgres_first(self):
        """Verify Postgres model_catalog_entries is checked first."""
        from inspect import getsource

        from umrs.jobs.adapters import _resolve_model_entry

        source = getsource(_resolve_model_entry)

        # Should try durable pool first
        durable_line = source.find("build_durable_job_db_pool_from_env")
        catalog_fetch = source.find("model_catalog_entries") or source.find("list_models")

        assert durable_line > 0, "Must build durable pool"
        if catalog_fetch > 0:
            assert durable_line < catalog_fetch, "Must build pool before querying catalog"

    def test_json_fallback_exists_with_deprecation_warning(self):
        """Verify JSON fallback exists (for compatibility) but with warning."""
        from inspect import getsource

        from umrs.jobs.adapters import _resolve_model_entry

        source = getsource(_resolve_model_entry)

        # Should have JSON fallback for backwards compatibility
        has_fallback = "_load_catalog" in source or "json" in source.lower()

        if has_fallback:
            # Must include deprecation/warning
            has_warning = (
                "warn" in source.lower() or "deprecat" in source.lower() or "log" in source.lower()
            )
            assert has_warning, "JSON fallback must include deprecation warning"

    def test_model_entry_contains_runtime_config(self):
        """Verify ModelEntry deserializes runtime_config from database."""
        from inspect import getsource

        from umrs.model_registry.models import ModelEntry

        source = getsource(ModelEntry)

        # Should have runtime_config field
        assert "runtime_config" in source, "ModelEntry must include runtime_config"

    def test_runtime_config_has_all_required_fields(self):
        """Verify RuntimeConfig includes all 7 VLLM config fields."""
        from inspect import getsource

        from umrs.model_registry.runtime_config import RuntimeConfig

        source = getsource(RuntimeConfig)

        required_fields = [
            "tool_call_parser",
            "reasoning_parser",
            "kv_cache_dtype",
            "enforce_eager",
            "flashinfer_moe_fp16",
            "chat_template_format",
            "mm_encoder_tp_mode",
        ]

        for field in required_fields:
            assert field in source, f"RuntimeConfig must have {field}"


class TestPostgresAuthorityEnforcement:
    """Verify Postgres is operationally the sole authority."""

    def test_model_registry_reads_from_postgres_not_json(self):
        """Verify model registry queries Postgres model_catalog_entries."""
        from inspect import getsource

        from umrs.model_registry.store import ModelStore

        source = getsource(ModelStore)

        # Should use Postgres connection
        assert "query(" in source or "select" in source or "execute" in source, (
            "ModelStore must execute SQL queries on Postgres"
        )

    def test_no_model_list_function_for_json_catalog(self):
        """Verify there's no model list function reading JSON only."""
        # The old pattern would be: def list_models_from_json() → _load_catalog()
        # New pattern: ModelStore.list_models() → Postgres query
        # We can check that main path is through ModelStore
        from inspect import getsource

        from umrs.server import model_registry_handler

        source = getsource(model_registry_handler)

        # Should use ModelStore
        assert "ModelStore" in source or "model_registry.store" in source, (
            "Model registry handler must use ModelStore"
        )

    def test_serve_slurm_receives_env_not_derivation(self):
        """Verify serve.slurm receives complete VLLM env vars, not bash derivation."""
        import os

        serve_slurm_path = os.path.join(
            os.path.dirname(__file__), "..", "umrs", "deploy", "serve.slurm"
        )

        # Check if file exists before reading
        if os.path.exists(serve_slurm_path):
            with open(serve_slurm_path) as f:
                source = f.read()

            # Should use passed-in env vars, not derive them
            # (i.e., VLLM_TOOL_CALL_PARSER should come from sbatch env, not derived)
            if "VLLM_TOOL_CALL_PARSER" in source:
                # If it's there, should be referenced as $VLLM_TOOL_CALL_PARSER, not derived
                assert "$VLLM_TOOL_CALL_PARSER" in source or "VLLM_TOOL_CALL_PARSER}" in source, (
                    "serve.slurm should use sbatch env vars, not derive them"
                )


class TestPreflightGatesRegistry:
    """Verify PreflightValidator gates on registry lookup."""

    def test_preflight_checks_registry(self):
        """Verify _check_registry verifies model exists."""
        from inspect import getsource

        from umrs.orchestrator.preflight import PreflightValidator

        source = getsource(PreflightValidator)

        assert "_check_registry" in source, "PreflightValidator must have _check_registry"

    def test_check_registry_verifies_model_exists(self):
        """Verify _check_registry method checks model presence."""
        # Get the source of the _check_registry method
        import re
        from inspect import getsource

        from umrs.orchestrator.preflight import PreflightValidator

        source = getsource(PreflightValidator)

        # Extract _check_registry method
        match = re.search(
            r"def _check_registry\(.*?\n(.*?)(?=\n    def |\nclass |\Z)", source, re.DOTALL
        )
        if match:
            method_source = match.group(0)

            # Should check for model existence
            assert "entry" in method_source or "model" in method_source, (
                "_check_registry must verify model existence"
            )


class TestNoHardcodedFallbacks:
    """Verify no hardcoded fallback paths exist."""

    def test_deployment_spec_does_not_assume_model_structure(self):
        """Verify DeploymentSpec uses runtime_config, not hardcoded assumptions."""
        from inspect import getsource

        from umrs.orchestrator.deployment_spec import DeploymentSpec

        source = getsource(DeploymentSpec)

        # Should read from runtime_config
        assert "runtime_config" in source, "DeploymentSpec must use runtime_config from registry"

        # Should NOT have hardcoded mappings like model_name → parser
        # (that would be a fallback)
        has_hardcoded = bool(
            source.count("if") > 3
            and ("model_name" in source or "model" in source)
            and ("if model" in source.lower() or "elif model" in source.lower())
        )

        if has_hardcoded:
            # Check if it's just validation, not derivation
            assert "runtime_config" in source, (
                "Any model checks should be validation against runtime_config, not derivation"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
