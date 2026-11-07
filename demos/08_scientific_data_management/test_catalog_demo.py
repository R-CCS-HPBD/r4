"""Unit tests for catalog_demo.py (run: pytest demos/08_scientific_data_management/)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_DEMO_DIR = Path(__file__).resolve().parent
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

import catalog_demo  # noqa: E402


GOOD_CSV_PREVIEW = """\
batch_id,yield_pct,co2_uptake_mmol_g,notes
batch-1,72.1,3.42,baseline MOF-5 run
batch-3,85.0,4.21,highest yield in series
"""


class SequenceClient:
    def __init__(self, pages: list[dict]):
        self._pages = pages
        self._index = 0

    def get(self, path: str, *, params: dict | None = None):
        page = self._pages[min(self._index, len(self._pages) - 1)]
        self._index += 1
        return page


def test_assert_csv_preview_accepts_fixture_content() -> None:
    catalog_demo.assert_csv_preview(GOOD_CSV_PREVIEW)


def test_assert_csv_preview_rejects_missing_signals() -> None:
    with pytest.raises(ValueError, match="missing expected content"):
        catalog_demo.assert_csv_preview("batch_id,yield_pct\nbatch-1,72.1\n")


def test_upload_matchers_includes_full_and_relative_keys() -> None:
    matchers = catalog_demo.upload_matchers(
        [{"key": "demo-project/uploads/demo-08/batch-3-yields.csv"}]
    )
    assert "demo-project/uploads/demo-08/batch-3-yields.csv" in matchers
    assert "uploads/demo-08/batch-3-yields.csv" in matchers


def test_wait_catalog_ready_ignores_unrelated_ready_hits() -> None:
    matchers = catalog_demo.upload_matchers(
        [{"key": "demo-project/uploads/demo-08-run-1/batch-3-yields.csv"}]
    )
    client = SequenceClient(
        [
            {
                "results": [
                    {
                        "s3_key": "demo-project/uploads/demo-08/batch-3-yields.csv",
                        "state": "ready",
                    }
                ]
            },
            {
                "results": [
                    {
                        "s3_key": "demo-project/uploads/demo-08-run-1/batch-3-yields.csv",
                        "state": "ready",
                    }
                ]
            },
        ]
    )
    hits = catalog_demo.wait_catalog_ready(
        client,
        "batch-3",
        upload_matchers=matchers,
        timeout_sec=1,
        poll_sec=0.01,
    )
    assert len(hits) == 1
    assert hits[0]["s3_key"].endswith("demo-08-run-1/batch-3-yields.csv")


def test_wait_catalog_ready_times_out_when_no_matching_hits() -> None:
    matchers = catalog_demo.upload_matchers(
        [{"key": "demo-project/uploads/demo-08-new/batch-3-yields.csv"}]
    )

    class StaleClient:
        def get(self, path: str, *, params: dict | None = None):
            return {
                "results": [
                    {
                        "s3_key": "demo-project/uploads/demo-08/batch-3-yields.csv",
                        "state": "ready",
                    }
                ]
            }

    hits = catalog_demo.wait_catalog_ready(
        StaleClient(),
        "batch-3",
        upload_matchers=matchers,
        timeout_sec=0.05,
        poll_sec=0.01,
    )
    assert hits == []


def test_dry_run_exits_zero_and_creates_png(tmp_path: Path) -> None:
    csv_path = tmp_path / "batch-3-yields.csv"
    csv_path.write_text(GOOD_CSV_PREVIEW, encoding="utf-8")
    png_path = tmp_path / "sample-chart.png"

    exit_code = catalog_demo.main(
        ["--mode", "legacy", "--csv", str(csv_path), "--png", str(png_path), "--dry-run"]
    )

    assert exit_code == 0
    assert png_path.is_file()


def test_blank_project_fixture_collects_expected_modalities(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    manifest = json.loads(
        (_DEMO_DIR / "fixture" / "MANIFEST.json").read_text(encoding="utf-8")
    )
    (fixture_dir / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel in (
        "README.md",
        "code/model.py",
        "data/batch-3-yields.csv",
        "notebooks/analysis.ipynb",
        "notebooks/preprocessing.ipynb",
    ):
        src = _DEMO_DIR / "fixture" / rel
        dest = fixture_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())

    catalog_demo.ensure_blank_project_fixture(fixture_dir)
    files = catalog_demo.collect_blank_project_files(fixture_dir)

    assert sum(1 for item in files if item.expected_modality == "document") == 15
    assert sum(1 for item in files if item.expected_modality == "notebook") == 2
    assert any(item.upload_name == "code/model.py" for item in files)
    assert any(item.upload_name == "data/batch-3-yields.csv" for item in files)
    modalities = {item.expected_modality for item in files}
    assert modalities >= {"document", "notebook", "code", "structured", "text"}


def test_dry_run_blank_project_validates_fixture(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    manifest = json.loads(
        (_DEMO_DIR / "fixture" / "MANIFEST.json").read_text(encoding="utf-8")
    )
    (fixture_dir / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel in (
        "README.md",
        "code/model.py",
        "data/batch-3-yields.csv",
        "notebooks/analysis.ipynb",
        "notebooks/preprocessing.ipynb",
    ):
        src = _DEMO_DIR / "fixture" / rel
        dest = fixture_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())

    exit_code = catalog_demo.main(
        ["--mode", "blank", "--fixture-dir", str(fixture_dir), "--dry-run"]
    )
    assert exit_code == 0


def test_assert_modality_coverage_requires_all_expected() -> None:
    hits = [
        {"detected_modality": "document"},
        {"detected_modality": "notebook"},
        {"detected_modality": "code"},
        {"detected_modality": "structured"},
        {"detected_modality": "text"},
    ]
    catalog_demo.assert_modality_coverage(
        hits,
        {"document", "notebook", "code", "structured", "text"},
    )

    with pytest.raises(ValueError, match="missing modalities"):
        catalog_demo.assert_modality_coverage(hits, {"document", "image"})


def test_wait_blank_project_ready_waits_for_all_uploads() -> None:
    uploads = [
        {"key": "proj/uploads/p/demo-08-blank/data/batch-3-yields.csv", "_upload_name": "data/batch-3-yields.csv"},
        {"key": "proj/uploads/p/demo-08-blank/code/model.py", "_upload_name": "code/model.py"},
    ]
    manifest = {
        "search_queries": {
            "csv": "batch-3",
            "code": "torch",
        }
    }

    class MultiClient:
        def __init__(self) -> None:
            self._calls = 0

        def get(self, path: str, *, params: dict | None = None):
            self._calls += 1
            query = (params or {}).get("q", "")
            if self._calls < 2:
                if query == "batch-3":
                    return {
                        "results": [
                            {
                                "s3_key": uploads[0]["key"],
                                "state": "ready",
                                "detected_modality": "structured",
                            }
                        ]
                    }
                return {"results": []}
            return {
                "results": [
                    {
                        "s3_key": uploads[0]["key"],
                        "state": "ready",
                        "detected_modality": "structured",
                    },
                    {
                        "s3_key": uploads[1]["key"],
                        "state": "ready",
                        "detected_modality": "code",
                    },
                ]
            }

    hits = catalog_demo.wait_blank_project_ready(
        MultiClient(),
        uploads,
        manifest,
        timeout_sec=1,
        poll_sec=0.01,
    )
    assert len(hits) == 2
