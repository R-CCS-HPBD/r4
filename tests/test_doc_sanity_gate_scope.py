"""doc_sanity_gate: default scope and the tracked baseline (hardening plan H-11.8; 2026-10-18).

Before 2026-10-18 ``doc_sanity_gate.py`` without arguments scanned nothing and
passed, and its baseline path was cwd-relative and the file did not exist. CI
now runs it with no arguments, so no arguments must mean the gate's scope.
"""

from __future__ import annotations

import json

import tools.doc_sanity_gate as gate


def _plant(root, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_no_arguments_scan_the_default_scope(tmp_path, monkeypatch):
    _plant(tmp_path, "docs/adr/001-x.md", "The catalog gets 出力saturated with tokens.\n")
    _plant(tmp_path, "docs/other/y.md", "outside the scope 出力saturated\n")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    assert gate.default_paths() == ["docs/adr/001-x.md"]
    assert gate.main([]) == 1


def test_clean_default_scope_passes(tmp_path, monkeypatch):
    _plant(tmp_path, "docs/development/plan.md", "# Plan\nAll good.\n")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    assert gate.main([]) == 0


def test_baseline_is_tracked_and_repo_rooted():
    assert gate.BASELINE_PATH.is_absolute()
    data = json.loads(gate.BASELINE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data["known_findings"], list)


def test_live_default_scope_is_clean_against_the_baseline(monkeypatch):
    monkeypatch.chdir(gate.REPO_ROOT)
    assert gate.main([]) == 0
