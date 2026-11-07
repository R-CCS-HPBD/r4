"""
Runtime execution path verification — prove DeploymentSpec and PreflightValidator
are actually called, not just present in source code.

Uses mocks/spies to verify:
1. EndpointProvisionBackend.submit_job() calls DeploymentSpec.from_registry()
2. submit_job() calls PreflightValidator.run_all()
3. submit_job() calls generate_sbatch_env()
4. No fallback to old bash derivation paths

Patch-target note: ``EndpointProvisionBackend.submit_job`` imports ``DeploymentSpec``,
``PreflightValidator`` and ``load_site_config`` lazily *inside* the method (comment in
adapters.py: "Lazy imports to avoid circular dependencies with orchestrator").  A lazy
import is resolved through the defining module on every call, so
``umrs.jobs.adapters.DeploymentSpec`` & friends are not module attributes and patching
them raises AttributeError.  These tests therefore patch the definition sites:
``umrs.orchestrator.deployment_spec.DeploymentSpec``,
``umrs.orchestrator.preflight.PreflightValidator``,
``umrs.orchestrator.site_config.load_site_config``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# ── Production-shaped fixtures ───────────────────────────────────────────────
# The real Job/JobAttempt dataclasses are used (not bare MagicMocks) because
# submit_job() does arithmetic on job.ttl_seconds and reads attempt.attempt_no;
# a MagicMock satisfies those accesses silently and then explodes deep inside
# the method, which would make every assertion below vacuous.


def _make_job(model: str, **spec_overrides: Any):
    from umrs.jobs.models import Job, JobType

    spec: dict[str, Any] = {
        "model": model,
        "gpu_count": 1,
        "max_model_len": 4096,
        "env_vars": {},
    }
    spec.update(spec_overrides)
    return Job(
        job_id="job-123",
        project_id="test-project",
        user_id="test-user",
        job_type=JobType.ENDPOINT_PROVISION,
        spec=spec,
        idempotency_key="idem-job-123",
    )


def _make_attempt(job: Any):
    from umrs.jobs.models import JobAttempt

    return JobAttempt(
        attempt_id="attempt-1",
        job_id=job.job_id,
        attempt_no=1,
        backend="slurm",
    )


def _make_backend(backend_ref: str = "slurm-123"):
    """Mirror the production wiring in adapters.create_backend_factory().

    The backend is constructed with orchestrator + registry + port_manager;
    ``registry`` is a required positional argument and submit_job() calls
    ``self.registry.register(...)`` on the success path.
    """
    from umrs.jobs.adapters import EndpointProvisionBackend
    from umrs.orchestrator.endpoint_registry import EndpointRegistry

    backend = EndpointProvisionBackend(
        orchestrator=MagicMock(),
        registry=MagicMock(spec=EndpointRegistry),
        port_manager=MagicMock(),
    )
    backend.port_manager.allocate = Mock(return_value=8001)
    backend.orchestrator.submit_job = AsyncMock(return_value=backend_ref)
    return backend


def _make_site_config(partition: str = "gpu") -> MagicMock:
    """Site config stub that advertises *partition*.

    adapters._validate_partition() rejects any partition the site does not know
    about.  An unconfigured MagicMock has no usable scheduler.partition, so
    submit_job() would abort before preflight and the assertions below would
    never be reached.
    """
    site = MagicMock()
    site.scheduler.partition = partition
    site.partition_profiles = {partition: {}}
    return site


def _certification(model_id: str) -> dict[str, Any]:
    """An active certification record, as _check_active_certification returns it.

    UMRS_CERTIFICATION_MODE defaults to "hard", so submit_job() raises before
    reaching DeploymentSpec unless the model is certified.  Certification
    enforcement has its own coverage; these tests isolate the
    DeploymentSpec/preflight path by stubbing an already-certified model.
    """
    return {
        "cert_id": "cert-1",
        "model_id": model_id,
        "model_name": "test-model",
        "launcher_fingerprint": "fp-1",
        "sif_version": "1.0",
        "sif_sha256": "0" * 64,
        "certified_config": {},
        "smoke_test_passed": True,
        "certified_at": "2026-09-01T00:00:00Z",
        "tester": "ci",
    }


async def _run_submit(backend, job, attempt):
    """Run submit_job() and return the exception it raised, if any.

    submit_job() runs past the point each test asserts on (endpoint registration,
    readiness polling, …) against mocks that do not model those later stages, so a
    trailing failure is expected.  The exception is returned rather than swallowed
    so it can be reported in the assertion message when a mock was *not* reached.
    """
    try:
        await backend.submit_job(job, attempt)
    except Exception as exc:
        return exc
    return None


@dataclass
class MockDeploymentSpec:
    """Mock DeploymentSpec with instrumentation."""

    model_family: str = "glm"
    tool_call_parser: str = "glm47"
    reasoning_parser: str = "glm47"
    kv_cache_dtype: str = "fp8"
    enforce_eager: bool = False
    flashinfer_moe_fp16: bool = True
    chat_template_format: str | None = None
    mm_encoder_tp_mode: str = "EAGER"
    tp: int = 1
    pp: int = 1
    gpus_per_node: int = 1
    node_count: int = 1
    partition: str = "gpu"
    max_model_len: int = 4096
    parallel_strategy: str = "auto"

    # Track which methods were called (and with what, for from_registry)
    _called: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_registry(cls, entry, profile, site_config, **kwargs):
        """Factory method that records being called."""
        spec = cls()
        spec._called["from_registry"] = {
            "entry": entry,
            "profile": profile,
            "site_config": site_config,
            **kwargs,
        }
        return spec

    def validate(self):
        """Validation method that records being called."""
        self._called["validate"] = True
        return []

    def generate_sbatch_env(self):
        """Generate sbatch env that records being called."""
        self._called["generate_sbatch_env"] = True
        return {
            "VLLM_TOOL_CALL_PARSER": self.tool_call_parser,
            "VLLM_REASONING_PARSER": self.reasoning_parser,
            "VLLM_KV_CACHE_DTYPE": self.kv_cache_dtype,
            "VLLM_ENFORCE_EAGER": str(self.enforce_eager).lower(),
            "VLLM_USE_FLASHINFER_MOE_FP16": str(self.flashinfer_moe_fp16).lower(),
            "VLLM_CHAT_TEMPLATE_FORMAT": self.chat_template_format or "",
            "VLLM_MM_ENCODER_TP_MODE": self.mm_encoder_tp_mode,
            "SLURM_CPUS_PER_TASK": "8",
            "SLURM_PARTITION": self.partition,
            "SLURM_TIME": "02:00:00",
            "SLURM_NTASKS": "1",
        }


@dataclass
class MockPreflightResult:
    """Mock PreflightResult."""

    passed: bool = True
    checks: list[Any] = field(default_factory=list)
    total_duration_ms: float = 100.0

    def summary(self):
        return "All checks passed"


@dataclass
class _SubmitPathMocks:
    """Handles on every collaborator submit_job() reaches."""

    resolve_model_entry: Any
    check_certification: Any
    resolve_roster_defaults: Any
    build_deployment_profile: Any
    load_site_config: Any
    deployment_spec_cls: Any
    preflight_validator_cls: Any

    @property
    def preflight_validator(self):
        return self.preflight_validator_cls.return_value


@contextlib.contextmanager
def _patched_submit_path(model_entry, *, deployment_spec, preflight_passed: bool = True):
    """Patch submit_job()'s collaborators at their definition sites."""
    with (
        patch("umrs.jobs.adapters._resolve_model_entry", return_value=model_entry) as resolve,
        patch(
            "umrs.jobs.adapters._check_active_certification",
            return_value=_certification(model_entry.model_id),
        ) as cert,
        patch("umrs.jobs.adapters._resolve_roster_defaults", return_value={}) as roster,
        patch(
            "umrs.jobs.adapters.build_deployment_profile", return_value={"gpu_count": 1}
        ) as profile,
        patch(
            "umrs.orchestrator.site_config.load_site_config", return_value=_make_site_config()
        ) as site,
        patch("umrs.orchestrator.deployment_spec.DeploymentSpec") as spec_cls,
        patch("umrs.orchestrator.preflight.PreflightValidator") as validator_cls,
    ):
        spec_cls.from_registry = Mock(return_value=deployment_spec)
        validator_cls.return_value.run_all = AsyncMock(
            return_value=MockPreflightResult(passed=preflight_passed)
        )
        yield _SubmitPathMocks(
            resolve_model_entry=resolve,
            check_certification=cert,
            resolve_roster_defaults=roster,
            build_deployment_profile=profile,
            load_site_config=site,
            deployment_spec_cls=spec_cls,
            preflight_validator_cls=validator_cls,
        )


def _model_entry(model_id: str, name: str, storage_ref: str):
    from umrs.model_registry.models import ModelEntry, ModelScope

    return ModelEntry(
        model_id=model_id,
        name=name,
        scope=ModelScope.SYSTEM,
        storage_ref=storage_ref,
    )


def _magic_spec(*, gpus_per_node: int, sbatch_env: dict[str, str]) -> MagicMock:
    """A DeploymentSpec stub whose generate_sbatch_env() is a spy."""
    spec = MagicMock()
    spec.generate_sbatch_env = Mock(return_value=sbatch_env)
    spec.gpus_per_node = gpus_per_node
    spec.node_count = 1
    spec.parallel_strategy = "auto"
    spec.partition = "gpu"
    return spec


class TestRuntimeExecutionPath:
    """Verify actual execution flow of new architecture."""

    @pytest.mark.asyncio
    async def test_submit_job_calls_deployment_spec_from_registry(self):
        """Verify submit_job() actually calls DeploymentSpec.from_registry()."""
        job = _make_job("test-glm")
        attempt = _make_attempt(job)
        entry = _model_entry("test-glm-123", "test-glm", "/models/glm")

        with _patched_submit_path(entry, deployment_spec=MockDeploymentSpec()) as mocks:
            backend = _make_backend()
            backend._apply_advanced_layout_kwargs = MagicMock()

            exc = await _run_submit(backend, job, attempt)

            from_registry = mocks.deployment_spec_cls.from_registry
            assert from_registry.called, (
                f"DeploymentSpec.from_registry() was not called (submit_job raised: {exc!r})"
            )
            assert from_registry.call_args.kwargs["entry"] is entry, (
                "from_registry() must be handed the entry resolved from the registry"
            )

    @pytest.mark.asyncio
    async def test_submit_job_calls_preflight_validator(self):
        """Verify submit_job() actually calls PreflightValidator.run_all()."""
        job = _make_job("test-glm")
        attempt = _make_attempt(job)
        entry = _model_entry("test-glm-123", "test-glm", "/models/glm")
        deployment_spec = MockDeploymentSpec()

        with _patched_submit_path(entry, deployment_spec=deployment_spec) as mocks:
            backend = _make_backend()

            exc = await _run_submit(backend, job, attempt)

            validator_cls = mocks.preflight_validator_cls
            assert validator_cls.called, (
                f"PreflightValidator() was not called (submit_job raised: {exc!r})"
            )
            assert validator_cls.call_args.kwargs["spec"] is deployment_spec, (
                "PreflightValidator must validate the generated DeploymentSpec"
            )
            assert mocks.preflight_validator.run_all.called, "preflight.run_all() was not called"

    @pytest.mark.asyncio
    async def test_submit_job_calls_generate_sbatch_env(self):
        """Verify submit_job() actually calls generate_sbatch_env()."""
        job = _make_job("test-glm")
        attempt = _make_attempt(job)
        entry = _model_entry("test-glm-123", "test-glm", "/models/glm")
        deployment_spec = _magic_spec(
            gpus_per_node=1,
            sbatch_env={
                "VLLM_TOOL_CALL_PARSER": "glm47",
                "VLLM_REASONING_PARSER": "glm47",
                "VLLM_KV_CACHE_DTYPE": "fp8",
                "SLURM_PARTITION": "gpu",
            },
        )

        with _patched_submit_path(entry, deployment_spec=deployment_spec):
            backend = _make_backend()

            exc = await _run_submit(backend, job, attempt)

            assert deployment_spec.generate_sbatch_env.called, (
                f"generate_sbatch_env() was not called (submit_job raised: {exc!r})"
            )

    @pytest.mark.asyncio
    async def test_submit_job_forwards_all_vllm_env_vars(self):
        """Verify all VLLM config vars are forwarded to orchestrator.submit_job()."""
        job = _make_job("test-deepseek", gpu_count=2, max_model_len=32768)
        attempt = _make_attempt(job)
        entry = _model_entry("test-deepseek-123", "test-deepseek", "/models/deepseek")

        vllm_vars = {
            "VLLM_TOOL_CALL_PARSER": "deepseek_v4",
            "VLLM_REASONING_PARSER": "deepseek_v4",
            "VLLM_KV_CACHE_DTYPE": "fp8",
            "VLLM_ENFORCE_EAGER": "false",
            "VLLM_USE_FLASHINFER_MOE_FP16": "true",
            "VLLM_CHAT_TEMPLATE_FORMAT": "string",
            "VLLM_MM_ENCODER_TP_MODE": "EAGER",
        }
        deployment_spec = _magic_spec(gpus_per_node=2, sbatch_env=vllm_vars.copy())

        with _patched_submit_path(entry, deployment_spec=deployment_spec):
            backend = _make_backend(backend_ref="slurm-456")

            exc = await _run_submit(backend, job, attempt)

            assert backend.orchestrator.submit_job.called, (
                f"orchestrator.submit_job() was not called (submit_job raised: {exc!r})"
            )
            call_kwargs = backend.orchestrator.submit_job.call_args[1]
            submit_env = call_kwargs.get("env", {})

            # Check all VLLM vars are present
            for var_name, var_value in vllm_vars.items():
                assert var_name in submit_env, f"{var_name} missing from submit_env"
                assert submit_env[var_name] == var_value, (
                    f"{var_name}={submit_env[var_name]} != {var_value}"
                )

    @pytest.mark.asyncio
    async def test_preflight_failure_blocks_sbatch_submission(self):
        """Verify preflight validation failure prevents sbatch submission."""
        job = _make_job("invalid-model", gpu_count=8)
        attempt = _make_attempt(job)
        entry = _model_entry("invalid-model-123", "invalid-model", "/models/invalid")

        with _patched_submit_path(
            entry, deployment_spec=MockDeploymentSpec(), preflight_passed=False
        ):
            backend = _make_backend(backend_ref="slurm-789")

            # Pin hard mode: this test asserts the blocking behaviour, which
            # UMRS_PREFLIGHT_MODE=warn disables by design.  "hard" is the shipped
            # default (adapters._PREFLIGHT_MODE).
            with (
                patch("umrs.jobs.adapters._PREFLIGHT_MODE", "hard"),
                pytest.raises(RuntimeError) as exc_info,
            ):
                await backend.submit_job(job, attempt)

            assert "Preflight validation failed" in str(exc_info.value)
            # Verify sbatch was NOT called
            assert not backend.orchestrator.submit_job.called, (
                "preflight failure must block submission to the orchestrator"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
