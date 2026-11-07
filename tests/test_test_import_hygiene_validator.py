"""Pins for tools/test_import_hygiene_validator.py (plan step 1.5)."""

from __future__ import annotations

from pathlib import Path

from tools import test_import_hygiene_validator as hygiene


def test_repo_is_clean_against_itself():
    findings = hygiene.scan_repo(Path(__file__).resolve().parent.parent)
    assert findings == [], "\n".join(f.format() for f in findings)


def test_flags_sys_path_insert(tmp_path):
    src = tmp_path / "t_bad.py"
    src.write_text("import sys\nsys.path.insert(0, '/x')\n")
    findings = hygiene._scan_file(src, "t_bad.py")
    assert len(findings) == 1
    assert findings[0].call == "sys.path.insert"
    assert findings[0].line == 2


def test_flags_spec_load(tmp_path):
    src = tmp_path / "t_spec.py"
    src.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('m', 'm.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
    )
    findings = hygiene._scan_file(src, "t_spec.py")
    calls = {f.call for f in findings}
    assert calls == {"importlib.util.spec_from_file_location", "importlib.util.module_from_spec"}


def test_prose_and_valid_imports_pass(tmp_path):
    src = tmp_path / "t_ok.py"
    src.write_text(
        '"""Mention of sys.path.insert in prose must not fire."""\n'
        "# Nor this: importlib.util.spec_from_file_location\n"
        "import sys\n"
        "from pathlib import Path\n"
        "x = sys.argv\n"
        "insert = list.insert\n"
    )
    assert hygiene._scan_file(src, "t_ok.py") == []


def test_allowlist_entries_skip_files(tmp_path, monkeypatch):
    allow = tmp_path / "tools" / "test_import_hygiene_allowlist.txt"
    allow.parent.mkdir(parents=True)
    allow.write_text("umrs/tests/test_x.py  # isolated by design\n")
    monkeypatch.setattr(hygiene, "ALLOWLIST_PATH", "tools/test_import_hygiene_allowlist.txt")
    loaded = hygiene._load_allowlist(tmp_path)
    assert loaded == frozenset({"umrs/tests/test_x.py"})


def test_unparseable_and_missing_files_are_skipped(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("If __name__ :\n")
    assert hygiene._scan_file(bad, "broken.py") == []


def test_sys_path_append_flagged(tmp_path):
    src = tmp_path / "t_append.py"
    src.write_text("import sys\nsys.path.append('/tmp/x')\n")
    findings = hygiene._scan_file(src, "t_append.py")
    assert [f.call for f in findings] == ["sys.path.append"]


def test_sys_path_benign_uses_pass(tmp_path):
    src = tmp_path / "t_benign.py"
    src.write_text(
        "import sys\nsys.path.remove('/x')\nprint(len(sys.path))\nother_path.insert(0, 'x')\n"
    )
    assert hygiene._scan_file(src, "t_benign.py") == []


def test_indirect_attribute_chain_not_confused(tmp_path):
    src = tmp_path / "t_chain.py"
    src.write_text("mypkg.sys.path.insert(0, '/x')\n")
    # Not literally sys.path.insert; keep scope tight (no false positives).
    assert hygiene._scan_file(src, "t_chain.py") == []


def test_call_inside_function_flagged(tmp_path):
    src = tmp_path / "t_func.py"
    src.write_text("import sys\ndef boot():\n    sys.path.insert(0, '/x')\n")
    findings = hygiene._scan_file(src, "t_func.py")
    assert len(findings) == 1 and findings[0].line == 3


def test_multiline_call_flagged(tmp_path):
    src = tmp_path / "t_multi.py"
    src.write_text("import sys\nsys.path.insert(\n    0,\n    '/x',\n)\n")
    assert len(hygiene._scan_file(src, "t_multi.py")) == 1


def test_scan_repo_honors_allowlist(tmp_path):
    root = tmp_path
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("import sys\nsys.path.insert(0, '/x')\n")
    (root / "tools").mkdir()
    (root / "tools" / "test_import_hygiene_allowlist.txt").write_text("tests/test_a.py\n")
    monkeyroot = hygiene.scan_repo(root)
    assert monkeyroot == []


def test_scan_repo_reports_relative_paths(tmp_path):
    root = tmp_path
    (root / "umrs" / "tests").mkdir(parents=True)
    (root / "umrs" / "tests" / "test_b.py").write_text("import sys\nsys.path.insert(0, '/x')\n")
    (root / "tools").mkdir()
    (root / "tools" / "test_import_hygiene_allowlist.txt").write_text("")
    findings = hygiene.scan_repo(root)
    assert [f.path for f in findings] == ["umrs/tests/test_b.py"]


def test_hidden_dirs_and_pycache_skipped(tmp_path):
    root = tmp_path
    (root / "tests" / ".git_work").mkdir(parents=True)
    (root / "tests" / ".git_work" / "x.py").write_text("import sys\nsys.path.insert(0, '/x')\n")
    (root / "tests" / "__pycache__").mkdir()
    (root / "tests" / "__pycache__" / "y.py").write_text("import sys\nsys.path.insert(0, '/x')\n")
    (root / "tools").mkdir()
    (root / "tools" / "test_import_hygiene_allowlist.txt").write_text("")
    assert hygiene.scan_repo(root) == []


def test_main_exit_codes_and_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "scan_repo", lambda _root: [])
    rc = hygiene.main([])
    out = capsys.readouterr().out
    assert rc == 0 and "no forbidden" in out


def test_main_json_mode(tmp_path, capsys, monkeypatch):
    import json

    finding = hygiene.HygieneFinding("tests/x.py", 3, "sys.path.insert", "why")
    monkeypatch.setattr(hygiene, "scan_repo", lambda _root: [finding])
    rc = hygiene.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload == [{"path": "tests/x.py", "line": 3, "call": "sys.path.insert"}]
