"""``python -m r4`` entry point.

Mirrors the console script declared in ``r4/pyproject.toml``
(``[project.scripts] r4 = "r4.cli:main"``) so the CLI can also be reached
through the interpreter that hosts the package — the form test harnesses and
subprocess callers need when they must pin the CLI to a specific venv
(``sys.executable -m r4 ...``).  Same convention as the umrs CLI packages
(``umrs/src/umrs/cli/__main__.py``).
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
