"""Pins for tools/mypy_baseline.py (hardening plan H-0.5, 2026-10-18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import mypy_baseline as mb

ROOT = Path(__file__).resolve().parents[1]

OUT = """\
src/r4/a.py:10: error: Missing type arguments for generic type "dict"  [type-arg]
src/r4/a.py:12:5: error: Name "x" already defined on line 3  [no-redef]
src/r4/a.py:13: note: Use "-> None" if function does not return a value
src/r4/b.py:7: error: Function is missing a return type annotation  [no-untyped-def]
"""


def _run(tmp_path: Path, command: str, text: str, *extra: str) -> int:
    out = tmp_path / "out.txt"
    out.write_text(text)
    args = [command, str(out), "--baseline", str(tmp_path / "base.txt"), "--prefix", "r4/"]
    return mb.main([*args, *extra])


def test_error_key_drops_line_numbers_and_adds_prefix():
    key = mb.error_key(
        'src/r4/a.py:99:4: error: Name "x" already defined on line 3  [no-redef]', "r4/"
    )
    assert key == 'r4/src/r4/a.py:no-redef:Name "x" already defined on line N'


def test_notes_and_noise_are_not_errors():
    assert mb.error_key('src/r4/a.py:13: note: Use "-> None"') is None
    assert mb.error_key("Success: no issues found in 3 source files") is None


def test_parse_output_is_a_multiset():
    keys = mb.parse_output(OUT + OUT.splitlines()[0] + "\n")
    assert keys['src/r4/a.py:type-arg:Missing type arguments for generic type "dict"'] == 2
    assert sum(keys.values()) == 4


def test_check_passes_on_baselined_errors_after_a_line_shift(tmp_path):
    assert _run(tmp_path, "write", OUT) == 0
    shifted = OUT.replace(":10:", ":40:").replace(":7:", ":70:")
    assert _run(tmp_path, "check", shifted, "--status", "1") == 0


def test_check_fails_on_a_new_error(tmp_path, capsys):
    _run(tmp_path, "write", OUT)
    extra = "src/r4/c.py:1: error: Returning Any from function  [no-any-return]\n"
    assert _run(tmp_path, "check", OUT + extra, "--status", "1") == 1
    assert "r4/src/r4/c.py:no-any-return" in capsys.readouterr().out


def test_check_fails_on_a_second_copy_of_a_baselined_error(tmp_path):
    _run(tmp_path, "write", OUT)
    assert _run(tmp_path, "check", OUT + OUT.splitlines()[0] + "\n", "--status", "1") == 1


def test_check_passes_when_errors_were_fixed(tmp_path):
    _run(tmp_path, "write", OUT)
    assert _run(tmp_path, "check", "", "--status", "0") == 0


@pytest.mark.parametrize(("text", "status"), [("crash", 2), ("unparseable output", 1)])
def test_check_fails_when_mypy_did_not_finish(tmp_path, text, status):
    _run(tmp_path, "write", OUT)
    assert _run(tmp_path, "check", text, "--status", str(status)) == 2


def test_write_only_shrinks_without_allow_new(tmp_path):
    _run(tmp_path, "write", OUT)
    extra = "src/r4/c.py:1: error: Returning Any from function  [no-any-return]\n"
    assert _run(tmp_path, "write", OUT + extra) == 1
    assert _run(tmp_path, "write", OUT + extra, "--allow-new") == 0
    assert _run(tmp_path, "write", "") == 0
    assert not mb.load_baseline(tmp_path / "base.txt")


@pytest.mark.parametrize("name", ["mypy_baseline_gateway.txt", "mypy_baseline_r4.txt"])
def test_committed_baselines_hold_normalised_keys(name):
    path = ROOT / "tools" / name
    keys = mb.load_baseline(path)
    assert keys
    prefix = "gateway/sidecar/" if "gateway" in name else "r4/src/r4/"
    for key in keys:
        path_part, code, _message = key.split(":", 2)
        assert path_part.startswith(prefix) and code, key
        assert "/home/" not in key and "/tmp/" not in key
