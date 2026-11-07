"""Drift gate for the companion bridge RPC result contract (plan H-4.3/H-4.4).

Offline mirror of ``tools/rpc_schema_export.py --check`` and
``tools/rpc_types_gen.py --check``: the JSON Schemas in
``contracts/bridge/v1/`` are what ``r4.companion.ui.rpc_schema`` produces,
and ``packages/r4-tui-v3/src/bridge/rpc-types.gen.ts`` is what those schemas
generate. Handler-vs-schema conformance lives in
``r4/tests/test_bridge_rpc_contract.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import rpc_schema_export, rpc_types_gen

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = REPO_ROOT / "contracts" / "bridge" / "v1"
TS_OUT = REPO_ROOT / "packages" / "r4-tui-v3" / "src" / "bridge" / "rpc-types.gen.ts"


def test_schema_artifacts_are_in_sync_with_code() -> None:
    assert rpc_schema_export.drifted(CONTRACT_DIR) == [], (
        "run: python tools/rpc_schema_export.py --write"
    )


def test_generated_ts_types_are_in_sync_with_schemas() -> None:
    expected = rpc_types_gen.render(CONTRACT_DIR)
    assert TS_OUT.read_text(encoding="utf-8") == expected, (
        "run: python tools/rpc_types_gen.py --write"
    )


def test_every_artifact_is_a_ref_document_named_after_its_file() -> None:
    def _root_refs(document: dict) -> list[str]:
        """$defs names the document's root points at: the dataclass-root $ref,
        or every anyOf branch ref of a union root (inline-root form, H-4)."""
        if "$ref" in document:
            return [str(document["$ref"]).removeprefix("#/$defs/")]
        return [
            str(branch["$ref"]).removeprefix("#/$defs/")
            for branch in document.get("anyOf", [])
            if isinstance(branch, dict) and "$ref" in branch
        ]

    for artifact in sorted(CONTRACT_DIR.glob("*.schema.json")):
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data["title"] == artifact.name.removesuffix(".schema.json")
        refs = _root_refs(data)
        assert refs, f"{artifact.name}: root has no $ref or anyOf branch refs"
        for name in refs:
            assert name in data["$defs"], f"{artifact.name}: $defs/{name} missing"


def test_check_modes_fail_on_a_stale_artifact(tmp_path: Path) -> None:
    for name, content in rpc_schema_export.export_all().items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    assert rpc_schema_export.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "retired.verb.schema.json").write_text("{}\n", encoding="utf-8")
    assert rpc_schema_export.main(["--check", "--out", str(tmp_path)]) == 1


def test_ts_check_fails_on_a_hand_edit(tmp_path: Path) -> None:
    out = tmp_path / "rpc-types.gen.ts"
    out.write_text(rpc_types_gen.render(CONTRACT_DIR) + "// edited\n", encoding="utf-8")
    assert rpc_types_gen.main(["--check", "--out", str(out)]) == 1
