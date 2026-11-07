"""umrs-front start.sh creates a front database only after a successful check (2026-10-16).

start.sh ran ``SELECT 1 FROM pg_database ... | grep -q 1 || CREATE DATABASE``
for the ai4s, bdrt and octopus databases. When the check itself failed (Postgres
still in crash recovery after the 2026-10-16 unclean host reboot, or the
``podman exec`` path racing the container), the pipeline fell through to
``CREATE DATABASE`` on a database that existed, and Postgres logged
``ERROR: database "umrs_ai4s" already exists`` (ops-watch #468). The helper
``_ensure_front_db`` now retries the check and creates only when it succeeded
and found no row; a check that keeps failing leaves creation to the server's
own ``_ensure_umrs_db`` at start.

The tests run the real shell function under bash with ``_pg_cmd`` and
``sleep`` stubbed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
START_SH = REPO_ROOT / "umrs/deploy/umrs-front/start.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _function_source() -> str:
    text = START_SH.read_text()
    match = re.search(r"^(\s*)_ensure_front_db\(\) \{\n.*?^\1\}\n", text, re.S | re.M)
    assert match, "start.sh must define _ensure_front_db()"
    return match.group(0)


def _run(check_results: list[str], tmp_path: Path) -> tuple[list[str], str]:
    """Run _ensure_front_db umrs_ai4s; each SELECT takes the next scripted
    result ('fail' = the check command fails, otherwise its stdout). The
    function calls _pg_cmd inside $( ), a subshell, so the stub keeps its state
    in files."""
    (tmp_path / "results").write_text("".join((r or "EMPTY") + "\n" for r in check_results))
    (tmp_path / "n").write_text("0")
    (tmp_path / "calls").write_text("")
    script = (
        "set -u\n"
        f"STATE={str(tmp_path)!r}\n"
        "sleep() { :; }\n"
        "_pg_cmd() {\n"
        '  case "$*" in\n'
        '    *"SELECT 1 FROM pg_database"*)\n'
        '      local n r; n=$(cat "$STATE/n"); echo $((n + 1)) > "$STATE/n"\n'
        '      echo check >> "$STATE/calls"\n'
        '      r=$(sed -n "$((n + 1))p" "$STATE/results"); r="${r:-fail}"\n'
        '      [[ "$r" == fail ]] && return 1\n'
        '      [[ "$r" == EMPTY ]] && r=""\n'
        '      printf "%s\\n" "$r"; return 0 ;;\n'
        '    *"CREATE DATABASE"*) echo "create:$*" >> "$STATE/calls"; return 0 ;;\n'
        "  esac\n"
        "}\n" + _function_source() + "_ensure_front_db umrs_ai4s\n"
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return (tmp_path / "calls").read_text().splitlines(), out.stdout


def test_an_existing_database_is_never_created_again(tmp_path: Path) -> None:
    calls, _ = _run(["1"], tmp_path)
    assert calls == ["check"]


def test_a_missing_database_is_created_once(tmp_path: Path) -> None:
    calls, _ = _run([""], tmp_path)
    assert calls[0] == "check"
    assert [c for c in calls if c.startswith("create:")] == [
        "create:-d postgres -c CREATE DATABASE umrs_ai4s;"
    ]


def test_a_failed_check_is_retried_before_deciding(tmp_path: Path) -> None:
    calls, _ = _run(["fail", "fail", "1"], tmp_path)
    assert calls == ["check", "check", "check"]


def test_a_check_that_keeps_failing_never_creates(tmp_path: Path) -> None:
    """The 2026-10-16 case: the check could not run, the database existed."""
    calls, out = _run(["fail"] * 10, tmp_path)
    assert not [c for c in calls if c.startswith("create:")]
    assert "WARNING" in out


def test_the_three_front_databases_use_the_helper() -> None:
    text = START_SH.read_text()
    for var, default in (
        ("POSTGRES_DB_AI4S", "umrs_ai4s"),
        ("POSTGRES_DB_BDRT", "front_bdrt"),
        ("POSTGRES_DB_OCTOPUS", "front_octopus"),
    ):
        assert f'_ensure_front_db "${{{var}:-{default}}}"' in text
    assert "| grep -q 1 || \\" not in text
