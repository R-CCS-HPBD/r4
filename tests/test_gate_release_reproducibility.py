"""The wave-E wheel/gate double-build check and its non-publishing CI lane."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts import check_gate_release_reproducible as repro

ROOT = Path(__file__).resolve().parents[1]
GATE = b"#!/usr/bin/env python3\n# synthetic gate fixture\n"
# The real U2.0b shim bytes (ADR-117 D-2): r4-contracts ships only this alias.
SHIM = (ROOT / "r4-contracts" / "src" / "r4_contracts" / "hpc_gate.py").read_bytes()


def fake_wheel(path: Path, member: str, content: bytes) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(member, content)


def fake_runner(
    calls: list[tuple], differences: dict[str, bytes] | None = None, shim: bytes = SHIM
):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        out = Path(argv[argv.index("--out-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        project = Path(argv[-1]).name
        label = kwargs["env"]["BUILD_LABEL"]
        if project == "r4":
            content = (differences or {}).get(f"r4:{label}", b"r4-wheel")
            (out / "r4-0.2.0rc4-py3-none-any.whl").write_bytes(content)
        elif project == "r4-contracts":
            wheel = out / "r4_contracts-0.2.0rc4-py3-none-any.whl"
            fake_wheel(wheel, "r4_contracts/hpc_gate.py", shim)
        else:
            content = (differences or {}).get(f"gate:{label}", GATE)
            wheel = out / "umrs_protocol-0.2.0rc4-py3-none-any.whl"
            fake_wheel(wheel, "umrs_protocol/hpc_gate.py", content)
        return subprocess.CompletedProcess(argv, 0)

    return run


def test_double_build_compares_real_artifact_bytes(tmp_path):
    calls: list[tuple] = []
    result = repro.build_reproducibly(ROOT, tmp_path, 1_767_225_600, fake_runner(calls))
    assert result["r4_sha256"] == result["builds"][0]["r4_sha256"]
    assert result["gate_sha256"] == result["builds"][1]["gate_sha256"]
    assert len(calls) == 6
    built = [Path(call[0][-1]).relative_to(ROOT).as_posix() for call in calls]
    assert built == ["r4", "r4-contracts", "umrs/protocol"] * 2


def test_both_builds_receive_identical_deterministic_inputs(tmp_path):
    calls: list[tuple] = []
    repro.build_reproducibly(ROOT, tmp_path, 1_767_225_600, fake_runner(calls))
    assert all(call[1]["env"]["SOURCE_DATE_EPOCH"] == "1767225600" for call in calls)
    assert all(call[1]["env"]["PYTHONHASHSEED"] == "0" for call in calls)
    assert all(call[1]["check"] for call in calls)


@pytest.mark.parametrize(
    ("artifact", "digest"),
    [
        ("r4:a", "r4_sha256"),
        ("r4:b", "r4_sha256"),
        ("gate:a", "gate_sha256"),
        ("gate:b", "gate_sha256"),
    ],
)
def test_each_artifacts_double_build_difference_fails(tmp_path, artifact, digest):
    different = {artifact: b"intentionally different bytes"}
    with pytest.raises(repro.ReproducibilityError, match=digest):
        repro.build_reproducibly(ROOT, tmp_path, 1_767_225_600, fake_runner([], different))


def test_ci_double_build_is_read_only_and_has_no_release_capability(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/gate-release-integrity.yaml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    runs = "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )
    assert "scripts/check_gate_release_reproducible.py" in runs
    for forbidden in ("gh release", "attest", "ssh-keygen -Y sign", "git push", "upload-artifact"):
        assert forbidden not in runs


def test_ci_double_build_checks_out_umrs_protocol_before_building():
    """U2.0b: the gate wheel is built from umrs/protocol, which actions/checkout
    leaves empty (a submodule); the pin composite must fill it first."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/gate-release-integrity.yaml").read_text())
    steps = workflow["jobs"]["double-build"]["steps"]
    uses = [step.get("uses", "") for step in steps]
    runs = [step.get("run", "") for step in steps]
    pin = uses.index("./.github/actions/umrs-pin-checkout")
    build = next(i for i, run in enumerate(runs) if "check_gate_release_reproducible.py" in run)
    assert uses[0].startswith("actions/checkout@") and pin < build
    assert steps[pin].get("with", {}).get("tree", "auto") in ("auto", "sparse", "full")
    assert "umrs/protocol" in repro.BUILD_DIRS


def test_manifest_writer_uses_the_release_schema_and_gate_bytes(tmp_path):
    from r4 import hpc_release, hpc_signing

    with zipfile.ZipFile(tmp_path / "protocol.whl", "w") as wheel:
        wheel.writestr("umrs_protocol/hpc_gate.py", hpc_signing.gate_source())
    payload = repro.manifest_from_wheel(tmp_path / "protocol.whl", "0.2.0rc4", 2_000_000_000)
    body = hpc_release.parse_manifest(
        payload, hpc_signing.gate_source(), "0.2.0rc4", now=1_900_000_000
    )
    assert body.sha256 == hpc_signing.gate_sha256()
    assert set(yaml.safe_load(payload)) == {"gate_version", "sha256", "min_client", "expires"}


def test_the_gate_is_read_from_the_umrs_protocol_wheel(tmp_path):
    """U2.0b: the r4-contracts wheel holds the shim; the gate digest is the
    umrs-protocol wheel's ``umrs_protocol/hpc_gate.py``."""
    result = repro.build_reproducibly(ROOT, tmp_path, 0, fake_runner([]))
    assert repro.GATE_MEMBER == "umrs_protocol/hpc_gate.py"
    assert result["gate_sha256"] == repro._sha256(GATE)


@pytest.mark.parametrize(
    "shim",
    [
        GATE,  # a second full gate in the r4-contracts wheel
        SHIM + b"\ndef main():\n    pass\n",
        b"from umrs_protocol import hpc_gate\n",  # no sys.modules alias
        SHIM + b"#" * 1024,
    ],
)
def test_a_contracts_wheel_without_the_alias_shim_is_refused(tmp_path, shim):
    with pytest.raises(repro.ReproducibilityError, match="alias"):
        repro.build_reproducibly(ROOT, tmp_path, 0, fake_runner([], shim=shim))


def test_the_real_shim_passes_the_check(tmp_path):
    wheel = tmp_path / "r4_contracts-0.2.0rc4-py3-none-any.whl"
    fake_wheel(wheel, repro.SHIM_MEMBER, SHIM)
    repro.check_contracts_shim(wheel)
