"""Where the same-model harness benchmark keeps its code and its data.

Code lives here, in git. Data never does. The question set, the corpus and
every answer, grade and log come from project research data that is not
distributed with the repository, so they stay under one bench root outside
the checkout, named by a required environment variable:

    $R4_SAME_MODEL_BENCH (required; no default)
      gold/gold.json, corpus/, results/source_kinds.json   inputs (not distributed)
      proto/, current/                                     arm B/C KG tools
      r4glm/                                               outputs: grades, reports
      r4glm/harness/                                       lane outputs: results_<lane>/,
                                                           logs/, run/, work/, smoke/,
                                                           r4_variants/

``BENCH``, ``OUT``, ``LANE`` and ``SMOKE_OUT`` resolve on first use, so a
module that needs only ``REPO`` or ``CODE`` imports without the variable; a
script that touches the data stops with a clear message when it is unset.

``smoke/`` next to this file is a synthetic fixture (one invented project),
safe to keep in git; smoke-run outputs still go to ``r4glm/harness/smoke``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

ENV = "R4_SAME_MODEL_BENCH"
CODE = Path(__file__).resolve().parent
REPO = CODE.parents[2]
SMOKE = CODE / "smoke"

# Data locations relative to the bench root, resolved lazily by __getattr__.
_UNDER_BENCH = {
    "BENCH": "",
    "OUT": "r4glm",
    "LANE": "r4glm/harness",
    "SMOKE_OUT": "r4glm/harness/smoke",
}


def assert_outside_repo(bench: Path) -> None:
    """Refuse to run with the bench root inside the checkout (data would land in git)."""
    if bench == REPO or REPO in bench.parents:
        raise SystemExit(f"{ENV}={bench} is inside the repository {REPO}; use a path outside it")


def bench_root(environ: Mapping[str, str] | None = None) -> Path:
    """The bench root from $R4_SAME_MODEL_BENCH; SystemExit when unset or inside the repo."""
    raw = (os.environ if environ is None else environ).get(ENV, "").strip()
    if not raw:
        raise SystemExit(
            f"{ENV} is not set. Point it at the bench root outside this checkout "
            "(gold/, corpus/, r4glm/). The question set and corpus are not "
            "distributed with the repository; see benchmarks/harness/same_model/README.md."
        )
    bench = Path(raw).expanduser().resolve()
    assert_outside_repo(bench)
    return bench


def __getattr__(name: str) -> Path:
    sub = _UNDER_BENCH.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return bench_root() / sub
