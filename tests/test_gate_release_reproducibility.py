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


def fake_wheel(path: Path, gate: bytes = GATE) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("r4_contracts/hpc_gate.py", gate)


def fake_runner(calls: list[tuple], differences: dict[str, bytes] | None = None):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        out = Path(argv[argv.index("--out-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        project = Path(argv[-1]).name
        artifact = "r4" if project == "r4" else "gate"
        key = f"{artifact}:{kwargs['env']['BUILD_LABEL']}"
        if project == "r4":
            content = (differences or {}).get(key, b"r4-wheel")
            (out / "r4-0.2.0rc4-py3-none-any.whl").write_bytes(content)
        else:
            content = (differences or {}).get(key, GATE)
            fake_wheel(out / "r4_contracts-0.2.0rc4-py3-none-any.whl", content)
        return subprocess.CompletedProcess(argv, 0)

    return run


def test_double_build_compares_real_artifact_bytes(tmp_path):
    calls: list[tuple] = []
    result = repro.build_reproducibly(ROOT, tmp_path, 1_767_225_600, fake_runner(calls))
    assert result["r4_sha256"] == result["builds"][0]["r4_sha256"]
    assert result["gate_sha256"] == result["builds"][1]["gate_sha256"]
    assert len(calls) == 4


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


def test_manifest_writer_uses_the_release_schema_and_gate_bytes(tmp_path):
    from r4 import hpc_release, hpc_signing

    with zipfile.ZipFile(tmp_path / "contracts.whl", "w") as wheel:
        wheel.writestr("r4_contracts/hpc_gate.py", hpc_signing.gate_source())
    payload = repro.manifest_from_wheel(tmp_path / "contracts.whl", "0.2.0rc4", 2_000_000_000)
    body = hpc_release.parse_manifest(
        payload, hpc_signing.gate_source(), "0.2.0rc4", now=1_900_000_000
    )
    assert body.sha256 == hpc_signing.gate_sha256()
    assert set(yaml.safe_load(payload)) == {"gate_version", "sha256", "min_client", "expires"}
