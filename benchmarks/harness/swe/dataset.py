"""SWE-bench Verified dataset access.

Fetches the verified instance list from the HuggingFace datasets-server rows
API with plain urllib — no `datasets`/pyarrow dependency, no huggingface
token (dataset is public). Rows are cached on disk so repeated runs are
offline and version-frozen.

Dataset: princeton-nlp/SWE-bench_Verified (500 instances, human-verified).
"""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_DATASET = "princeton-nlp/SWE-bench_Verified"
_ROWS_API = (
    "https://datasets-server.huggingface.co/rows"
    f"?dataset={urllib.parse.quote(_DATASET, safe='')}&config=default&split=test"
)
_PAGE_SIZE = 100  # datasets-server max per request
DEFAULT_CACHE = Path("benchmarks/harness/swe/.cache/verified.json")


@dataclass(frozen=True)
class SweInstance:
    """One SWE-bench Verified instance (the fields the lane needs)."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    environment_setup_commit: str | None = None
    hints_text: str = ""
    raw: dict = field(default_factory=dict, compare=False)


def _fetch_all_rows() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        url = f"{_ROWS_API}&offset={offset}&length={_PAGE_SIZE}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            payload = json.loads(resp.read())
        batch = [entry["row"] for entry in payload.get("rows", [])]
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < _PAGE_SIZE:
            return rows


def _parse_instance(row: dict) -> SweInstance:
    def _list(value) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = json.loads(value)
        return tuple(str(v) for v in value)

    return SweInstance(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        fail_to_pass=_list(row.get("FAIL_TO_PASS")),
        pass_to_pass=_list(row.get("PASS_TO_PASS")),
        environment_setup_commit=row.get("environment_setup_commit"),
        hints_text=row.get("hints_text") or "",
        raw=row,
    )


def load_verified(cache: Path = DEFAULT_CACHE, *, refresh: bool = False) -> list[SweInstance]:
    """Load all 500 verified instances, cached on disk."""
    if refresh or not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_fetch_all_rows()))
    rows = json.loads(cache.read_text())
    return [_parse_instance(row) for row in rows]


def select(
    instances: list[SweInstance],
    *,
    ids: list[str] | None = None,
    count: int | None = None,
    seed: int = 0,
) -> list[SweInstance]:
    """Pick instances deterministically: explicit ids, else random-sample with
    a fixed seed so suite contents are reproducible across runs."""
    if ids:
        wanted = set(ids)
        picked = [i for i in instances if i.instance_id in wanted]
        missing = wanted - {i.instance_id for i in picked}
        if missing:
            raise KeyError(f"unknown instance ids: {sorted(missing)}")
        return picked
    if count is not None:
        rng = random.Random(seed)
        chosen = rng.sample(instances, min(count, len(instances)))
        return sorted(chosen, key=lambda i: i.instance_id)
    return instances
