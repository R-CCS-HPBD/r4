"""``r4 --version`` surfaces the package version (R-09, 2026-09-13).

The flag used to carry a hard-coded ``0.1.0`` string in ``r4/cli.py``; it now
reads ``r4.__version__`` so a release bump has exactly one source of truth.
"""

from __future__ import annotations

import re
import subprocess
import sys

import r4


def test_version_flag_prints_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "r4.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    assert result.returncode == 0, output
    assert output == f"r4 {r4.__version__}"


def test_version_is_pep440_release_or_rc():
    # PEP 440 public version: N(.N)* with an optional rcN pre-release segment.
    assert re.fullmatch(r"\d+(\.\d+)*(rc\d+)?", r4.__version__), r4.__version__
    assert r4.__version__ != "0.1.0"  # the pre-release baseline was bumped for rc1
