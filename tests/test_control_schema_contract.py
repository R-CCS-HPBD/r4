"""Contract-test rail for the companion control-event wire format (Review F5).

Replays recorded JSONL fixture envelopes through the code decoder/encoder:
- expect=ok  → from_dict must decode AND re-encode to `canonical` (or the
  input envelope identity when absent — unknown keys tolerated on input,
  dropped on canonical output by design).
- expect=error → from_dict must raise.

The fixture set is the version-matrix fixture for the v1 ↔ v2 handshake
question: future-version payloads are hard-rejected, unknown payload KEYS
are tolerated without error. When CONTROL_SCHEMA_VERSION bumps to 2, a
v2 fixture family lands next to these and this file grows a version-parametrized
replay — without this test changing shape.

Drift gate: tools/control_schema_export.py --check proves the artifacts
people consume are the artifacts the code produces. This test does NOT
require the jsonschema package — schema conformance is nominally asserted
against payload property types (minimal draft-thin subset); artifacts are
nevertheless loadable by any JSON Schema implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from r4.companion import control_schema as cs

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = REPO_ROOT / "contracts" / "control" / "v1"
FIXTURE_DIR = CONTRACT_DIR / "fixtures"


def _load_cases() -> list[tuple[str, dict]]:
    cases: list[tuple[str, dict]] = []
    for fixture in sorted(FIXTURE_DIR.glob("*.jsonl")):
        for line_no, raw in enumerate(fixture.read_text().splitlines(), 1):
            if raw.strip():
                cases.append((f"{fixture.name}:{line_no}", json.loads(raw)))
    return cases


CASES = _load_cases()


def _case_id(case: tuple[str, dict]) -> str:
    return f"{case[0]}[{case[1].get('case', '?')}]"


@pytest.mark.parametrize("label,case", CASES, ids=[_case_id(c) for c in CASES])
def test_fixture_replay(label: str, case: dict) -> None:
    expect = case["expect"]
    envelope = case["envelope"]
    if expect == "error":
        with pytest.raises(ValueError):
            cs.from_dict(envelope)
        return
    if expect == "skip":
        # unknown event type (newer-binary generation in a persisted log):
        # skipped with a warning, never a decode crash.
        assert cs.from_dict(envelope) is None
        return
    event = cs.from_dict(envelope)
    assert event is not None
    canonical = case.get("canonical") or envelope
    assert cs.to_dict(event) == canonical


def test_every_event_type_has_schema_artifact() -> None:
    for name in cs._EVENT_TYPES:
        assert (CONTRACT_DIR / f"{name}.schema.json").exists(), f"missing artifact for {name}"


def test_every_artifact_loads_as_json_schema() -> None:
    for artifact in CONTRACT_DIR.glob("*.schema.json"):
        data = json.loads(artifact.read_text())
        assert data["type"] == "object"
        assert data["required"] == ["type", "payload"]
        assert data["properties"]["type"]["const"] == artifact.stem.replace(".schema", "")


def test_schema_artifacts_are_in_sync_with_code() -> None:
    """Offline mirror of `tools/control_schema_export.py --check`."""
    from tools import control_schema_export

    drifted = []
    for name, content in control_schema_export.export_all(CONTRACT_DIR).items():
        target = CONTRACT_DIR / name
        if not target.exists() or target.read_text() != content:
            drifted.append(name)
    assert drifted == [], f"stale artifacts: {drifted} (run --write)"


def test_optional_nullable_fields_emitted_as_anyof() -> None:
    artifact = json.loads((CONTRACT_DIR / "tool_call_end.schema.json").read_text())
    duration = artifact["properties"]["payload"]["properties"]["duration_ms"]
    assert "anyOf" in duration
    assert {"type": "null"} in duration["anyOf"]


def test_collection_fields_have_item_types() -> None:
    artifact = json.loads((CONTRACT_DIR / "session_state.schema.json").read_text())
    graphs = artifact["properties"]["payload"]["properties"]["graphs"]
    assert graphs["items"] == {"type": "string"}


def test_enum_fields_pin_values() -> None:
    artifact = json.loads((CONTRACT_DIR / "approval_decision.schema.json").read_text())
    decision = artifact["properties"]["payload"]["properties"]["decision"]
    assert decision["enum"] == ["once", "always", "no"]


def test_fixtures_dir_has_version_matrix_coverage() -> None:
    """The handshake/version case family must stay present."""
    matrix = (FIXTURE_DIR / "handshake_matrix.jsonl").read_text()
    assert "unknown-type-skips-with-warning" in matrix
    assert "missing-version-decodes" in matrix
