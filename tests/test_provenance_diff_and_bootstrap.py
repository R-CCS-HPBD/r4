"""Tests for MEM-B7 (provenance diff) and COR-003 (bootstrap validator)."""

from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest

# ===========================================================================
# MEM-B7: Provenance Diff Engine
# ===========================================================================
# Plain imports by ADR-053 (umrs resolves as a namespace package from the repo root).
from umrs.jobs.provenance_diff import (
    compute_provenance_diff,
)


def _prov(
    job_id: str = "job-a",
    job_type: str = "workflow_run",
    status: str = "completed",
    model: dict | None = None,
    artifacts: list | None = None,
    total_duration_seconds: float | None = None,
    execution_authority: str = "slurm",
    total_attempts: int = 1,
) -> dict:
    return {
        "job_id": job_id,
        "job_type": job_type,
        "status": status,
        "model": model,
        "artifacts": artifacts or [],
        "total_duration_seconds": total_duration_seconds,
        "execution_authority": execution_authority,
        "total_attempts": total_attempts,
        "restart_policy": "on_failure",
    }


class TestProvenanceDiffIdentical:
    """Diffing identical provenance records yields no diffs."""

    def test_no_diffs(self):
        prov = _prov(model={"name": "qwen", "path": "/m/q"})
        diff = compute_provenance_diff(prov, prov)
        assert len(diff.job_level_diffs) == 0
        assert len(diff.artifact_diffs) == 0
        assert diff.summary["job_field_changes"] == 0

    def test_to_dict(self):
        prov = _prov()
        diff = compute_provenance_diff(prov, prov)
        d = diff.to_dict()
        assert d["job_id_a"] == "job-a"
        assert d["job_id_b"] == "job-a"


class TestProvenanceDiffJobLevel:
    """Detect job-level field changes."""

    def test_status_change(self):
        a = _prov(status="completed")
        b = _prov(status="failed")
        diff = compute_provenance_diff(a, b)
        fields = {d.field: d for d in diff.job_level_diffs}
        assert "status" in fields
        assert fields["status"].old == "completed"
        assert fields["status"].new == "failed"

    def test_model_change(self):
        a = _prov(model={"name": "qwen-7b", "path": "/m/q7"})
        b = _prov(model={"name": "qwen-32b", "path": "/m/q32"})
        diff = compute_provenance_diff(a, b)
        fields = {d.field: d for d in diff.job_level_diffs}
        assert "model.name" in fields
        assert "model.path" in fields

    def test_duration_change(self):
        a = _prov(total_duration_seconds=42.0)
        b = _prov(total_duration_seconds=100.0)
        diff = compute_provenance_diff(a, b)
        fields = {d.field: d for d in diff.job_level_diffs}
        assert "total_duration_seconds" in fields

    def test_attempts_change(self):
        a = _prov(total_attempts=1)
        b = _prov(total_attempts=3)
        diff = compute_provenance_diff(a, b)
        fields = {d.field: d for d in diff.job_level_diffs}
        assert "total_attempts" in fields


class TestProvenanceDiffArtifacts:
    """Detect artifact changes between runs."""

    def test_added_artifact(self):
        a = _prov(artifacts=[])
        b = _prov(artifacts=[{"uri": "s3://out/result.tar"}])
        diff = compute_provenance_diff(a, b)
        assert len(diff.artifact_diffs) == 1
        assert diff.artifact_diffs[0].new == "present"

    def test_removed_artifact(self):
        a = _prov(artifacts=[{"uri": "s3://out/result.tar"}])
        b = _prov(artifacts=[])
        diff = compute_provenance_diff(a, b)
        assert len(diff.artifact_diffs) == 1
        assert diff.artifact_diffs[0].new == "absent"

    def test_changed_hash(self):
        a = _prov(artifacts=[{"uri": "s3://out/r.tar", "sha256": "aaa"}])
        b = _prov(artifacts=[{"uri": "s3://out/r.tar", "sha256": "bbb"}])
        diff = compute_provenance_diff(a, b)
        assert len(diff.artifact_diffs) == 1
        assert "sha256" in diff.artifact_diffs[0].field


class TestProvenanceDiffSteps:
    """Detect step-level changes using traces."""

    def test_added_step(self):
        traces_a = [{"step_name": "embed", "status": "succeeded"}]
        traces_b = [
            {"step_name": "embed", "status": "succeeded"},
            {"step_name": "index", "status": "succeeded"},
        ]
        diff = compute_provenance_diff(_prov(), _prov(), traces_a, traces_b)
        step_map = {sd.step_name: sd for sd in diff.step_diffs}
        assert step_map["index"].status == "added"

    def test_removed_step(self):
        traces_a = [
            {"step_name": "embed", "status": "succeeded"},
            {"step_name": "cleanup", "status": "succeeded"},
        ]
        traces_b = [{"step_name": "embed", "status": "succeeded"}]
        diff = compute_provenance_diff(_prov(), _prov(), traces_a, traces_b)
        step_map = {sd.step_name: sd for sd in diff.step_diffs}
        assert step_map["cleanup"].status == "removed"

    def test_changed_step(self):
        traces_a = [{"step_name": "train", "status": "succeeded", "inputs_hash": "h1"}]
        traces_b = [{"step_name": "train", "status": "succeeded", "inputs_hash": "h2"}]
        diff = compute_provenance_diff(_prov(), _prov(), traces_a, traces_b)
        step_map = {sd.step_name: sd for sd in diff.step_diffs}
        assert step_map["train"].status == "changed"
        assert any(fd.field == "inputs_hash" for fd in step_map["train"].field_diffs)

    def test_unchanged_step(self):
        trace = [{"step_name": "embed", "status": "succeeded", "inputs_hash": "h1"}]
        diff = compute_provenance_diff(_prov(), _prov(), trace, trace)
        assert diff.step_diffs[0].status == "unchanged"

    def test_no_traces(self):
        diff = compute_provenance_diff(_prov(), _prov())
        assert len(diff.step_diffs) == 0

    def test_summary_counts(self):
        traces_a = [
            {"step_name": "a", "status": "succeeded"},
            {"step_name": "b", "status": "succeeded"},
        ]
        traces_b = [
            {"step_name": "a", "status": "failed"},
            {"step_name": "c", "status": "succeeded"},
        ]
        diff = compute_provenance_diff(_prov(), _prov(), traces_a, traces_b)
        assert diff.summary["steps_changed"] == 1  # a changed
        assert diff.summary["steps_added"] == 1  # c added
        assert diff.summary["steps_removed"] == 1  # b removed


class TestProvenanceDiffSerialization:
    """Diff serializes correctly to dict."""

    def test_round_trip(self):
        a = _prov(status="completed", model={"name": "m1"})
        b = _prov(status="failed", model={"name": "m2"})
        diff = compute_provenance_diff(a, b)
        d = diff.to_dict()
        assert isinstance(d["job_level_diffs"], list)
        assert isinstance(d["summary"], dict)
        assert d["summary"]["job_field_changes"] > 0


# ===========================================================================
# COR-003: Bootstrap Validator
# ===========================================================================

# Import bootstrap_validator from tools/ (plain import — tools is a package dir)
from tools import bootstrap_validator as _bv_mod  # noqa: E402

validate = _bv_mod.validate
CheckResult = _bv_mod.CheckResult
ValidationReport = _bv_mod.ValidationReport
check_env_vars = _bv_mod.check_env_vars
check_disk_space = _bv_mod.check_disk_space
check_python_version = _bv_mod.check_python_version


class TestBootstrapValidatorProfiles:
    """All profiles should be validated without crashes."""

    @pytest.mark.parametrize("profile", ["hpc-full", "hpc-minimal", "k8s-cluster", "dev-compose"])
    def test_validate_runs(self, profile):
        report = validate(profile)
        assert isinstance(report, ValidationReport)
        assert report.profile == profile
        assert len(report.checks) > 0

    def test_report_serialization(self):
        report = validate("dev-compose")
        d = report.to_dict()
        assert "profile" in d
        assert "ok" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)


def _requires_python_floor(path: pathlib.Path) -> tuple[int, ...]:
    """Parse a pyproject's requires-python floor, e.g. '>=3.11' -> (3, 11)."""
    match = re.search(
        r'^requires-python\s*=\s*"[><=~^]*\s*([0-9]+(?:\.[0-9]+)+)',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"{path.name} declares no requires-python"
    return tuple(int(part) for part in match.group(1).split("."))


class TestBootstrapValidatorChecks:
    """Individual check functions work correctly."""

    def test_python_version_matches_the_declared_floor(self):
        """check_python_version() must agree with its own declared floor.

        The original form asserted a bare ``"pass"`` on the comment "We're
        running Python 3.11+". That is an assumption about the interpreter, not
        a contract. Pin the mapping from the running version to the reported
        status instead of pinning the environment.
        """
        results = check_python_version()
        assert len(results) == 1
        result = results[0]
        assert result.name == "python_version"
        expected = "pass" if sys.version_info >= _bv_mod.MIN_PYTHON else "fail"
        assert result.status == expected
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in result.detail

    def test_python_floor_equals_the_workspace_requires_python(self):
        """The validator's floor must equal what the packages declare.

        It demanded >= 3.11 while every pyproject declares >= 3.10 and
        CONSTITUTION §4.1 says "Python 3.10+ only", so a deployment on the
        documented-supported interpreter failed its own bootstrap check
        (2026-09-14). Read the declared floor rather than restating it, so the
        two cannot drift apart again.

        2026-10-21 (ADR-096 D-1): the root pins the dev workspace at >= 3.11
        (pillow 13.0.0 has no 3.10 build) while members keep the wider >= 3.10
        floor that the validator's MIN_PYTHON encodes for pip installs of the
        r4 client. The invariant is now: members agree on one floor, that
        floor equals MIN_PYTHON, and the root pins at or above it.
        """
        root = pathlib.Path(__file__).resolve().parents[1]
        floors = {
            rel: _requires_python_floor(root / rel)
            for rel in (
                "pyproject.toml",
                "r4/pyproject.toml",
                "r4-contracts/pyproject.toml",
                "mcp-servers/pyproject.toml",
                "packages/r4-teamengine-spi/pyproject.toml",
            )
            if (root / rel).exists()
        }
        member_floors = {floor for rel, floor in floors.items() if rel != "pyproject.toml"}
        assert len(member_floors) == 1, f"members disagree on their Python floor: {member_floors}"
        member_floor = member_floors.pop()
        assert member_floor == _bv_mod.MIN_PYTHON
        root_floor = floors["pyproject.toml"]
        assert root_floor >= member_floor, (
            f"root pins the dev workspace at {root_floor}, below the member floor {member_floor}"
        )

    def test_disk_space_runs(self):
        results = check_disk_space()
        assert len(results) == 1
        assert results[0].status in ("pass", "warn")

    def test_env_vars_missing(self):
        # With no env vars set, hpc-full should have failures
        old_env = {}
        for var in [
            "UMRS_DB_URL",
            "UMRS_S3_ENDPOINT",
            "LITELLM_MASTER_KEY",
            "VLLM_MODEL_PATH",
            "SLURM_PARTITION",
        ]:
            old_env[var] = os.environ.pop(var, None)

        try:
            results = check_env_vars("hpc-full")
            fail_count = sum(1 for r in results if r.status == "fail")
            assert fail_count >= 3  # At least some should fail
        finally:
            for var, val in old_env.items():
                if val is not None:
                    os.environ[var] = val

    def test_env_vars_pass_when_set(self):
        os.environ["UMRS_DB_URL"] = "postgres://test"
        try:
            results = check_env_vars("dev-compose")
            assert results[0].status == "pass"
        finally:
            del os.environ["UMRS_DB_URL"]


class TestProvenanceDiffCLI:
    """CLI diff subcommand registration."""

    def test_diff_subcommand_exists(self):
        from argparse import ArgumentParser

        from r4._cli.provenance import ProvenanceCommand

        root = ArgumentParser()
        sub = root.add_subparsers()
        cmd = ProvenanceCommand()
        cmd.register(sub)
        # Should parse diff args without error
        ns = root.parse_args(["provenance", "diff", "job-a", "job-b"])
        assert ns.job_id_a == "job-a"
        assert ns.job_id_b == "job-b"

    def test_diff_json_flag(self):
        from argparse import ArgumentParser

        from r4._cli.provenance import ProvenanceCommand

        root = ArgumentParser()
        sub = root.add_subparsers()
        cmd = ProvenanceCommand()
        cmd.register(sub)
        ns = root.parse_args(["provenance", "diff", "a", "b", "--json"])
        assert ns.as_json is True
