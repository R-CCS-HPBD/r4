"""ADR-093 S9 script (benchmarks/retrieval/s9_confirm.sh), dry-run only.

The script pre-registers its gate before any S9 run; these tests hold it to
the ADR's table (verbatim) and check the run it would make, without a stack:
``S9_DRY_RUN=1`` prints every command and creates nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "retrieval" / "s9_confirm.sh"
ADR = ROOT / "docs" / "adr" / "093-retrieval-for-small-local-models.md"


def _adr_row(slice_name: str) -> list[str]:
    """The cells of one row of the ADR's gate table."""
    line = next(row for row in ADR.read_text().splitlines() if row.startswith(f"| {slice_name} "))
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _header() -> str:
    """The script's leading comment block, as one line of prose."""
    lines = []
    for line in SCRIPT.read_text().splitlines()[1:]:
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    return re.sub(r"\s+", " ", " ".join(lines))


@pytest.mark.parametrize("slice_name", ["S9 Confirm and document", "S5 Router, citations"])
def test_the_header_pre_registers_the_adr_gate_verbatim(slice_name):
    name, components, gate = _adr_row(slice_name)
    assert f"{name} | {components} | {gate}" in _header()


def test_the_header_says_how_the_two_seeds_are_read():
    header = _header()
    assert "takes no seed" in header and "run.py repro" in header and "<= 1.5 s" in header


def _dry(tmp_path, **env):
    full = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "S9_DRY_RUN": "1",
        "S9_DATA": "/data/scifact",
        "S9_B0_DIR": str(tmp_path / "b0"),
        "ADR093_RESULTS": str(tmp_path / "results"),
        "ADR093_PYTHON": "/py",
        **env,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], env=full, capture_output=True, text=True, timeout=60
    )


#: What the dry run must print: the measurements, then the reports.
EXPECTED_COMMANDS = (
    "retrieve --data /data/scifact --arms retrieve:dense --label S9",
    "retrieve --data /data/scifact --claims nonsense --arms retrieve:dense --label S9",
    "infer --data /data/scifact --arms retrieve:dense --prompt pack-soft --label S9-run1",
    "infer --data /data/scifact --arms retrieve:dense --prompt pack-soft --label S9-run2",
    "--claims tune --arms retrieve:dense --prompt pack-soft --label S9-run1",
    "--claims tune --arms retrieve:dense --prompt pack-soft --label S9-run2",
    "--claims nonsense --arms retrieve:dense --prompt pack-soft --label S9",
    "answer --data /data/scifact --label S9",
    "answer --data /data/scifact --claims nonsense --label S9",
    "env R4_RAG_ROUTER=on /py",
    "--arms agent --limit 60 --label S9-router",
    "--arms closed,oracle --prompt pack-soft",
    "run.py compare <B0 *-infer-dev-legacy-B0.json>",
    "-s9-repro-dev.json",
    "-s9-repro-tune.json",
    "-s9-headroom-run1-B0-brackets.json",
    "-s9-headroom-run2-fresh-brackets.json",
    "-s9-sufficiency.json",
    "-s9-latency.json",
)


def test_a_dry_run_prints_the_whole_run_and_creates_nothing(tmp_path):
    proc = _dry(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = "\n".join(line for line in proc.stdout.splitlines() if line.startswith("+ "))
    assert [needle for needle in EXPECTED_COMMANDS if needle not in text] == []
    assert not (tmp_path / "results").exists()


def test_the_run_pairs_with_the_rescored_b0_files_when_they_exist(tmp_path):
    b0 = tmp_path / "b0"
    b0.mkdir()
    (b0 / "20261010T002611Z-infer-dev-legacy-B0.json").write_text("{}")
    (b0 / "20261010T050707Z-infer-dev-agent-B0.json").write_text("{}")
    (b0 / "20261010T050707Z-infer-dev-agent-B0-rescored.json").write_text("{}")
    proc = _dry(tmp_path)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert f"rescore --data /data/scifact {b0}/20261010T002611Z-infer-dev-legacy-B0.json" in out
    assert not any("run.py rescore" in ln and "dev-agent-B0" in ln for ln in out.splitlines())
    assert f"compare {b0}/20261010T050707Z-infer-dev-agent-B0-rescored.json" in out
    assert "20261010T002611Z-infer-dev-legacy-B0.json" in proc.stderr  # the B0 listing


def test_fresh_brackets_can_be_skipped(tmp_path):
    out = _dry(tmp_path, S9_FRESH_BRACKETS="0").stdout
    assert "closed,oracle" not in out and "fresh-brackets" not in out


def test_the_run_needs_the_data_directory(tmp_path):
    proc = _dry(tmp_path, S9_DATA="")
    assert proc.returncode != 0 and "S9_DATA" in proc.stderr
