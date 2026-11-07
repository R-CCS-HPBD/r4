"""Run this workflow's steps on your laptop, before uploading (Lab 2.1).

There is no server-side dry run (``r4 workflow lint`` checks the bundle's
shape only), so this script plays the runner: it reads ``workflow.json``,
builds each step's ``StepContext`` the way UMRS does (input bucket,
``<bucket>-output``, parent output keys as the next step's input keys) and
backs the "buckets" with plain directories.

    python run_local.py --data ../../tasks/eikonal/data/synthetic-kii    # offline fallback
    python run_local.py --data <folder with event.json + p_picks.csv> --step search_low

Needs the ``umrs`` package from the repository's ``.venv`` (``uv sync``).
Outputs land in ``./local-run/<bucket>-output/``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from pathlib import Path

from umrs.pipeline_sdk import StepContext

HERE = Path(__file__).resolve().parent
BUCKET = "local"


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class DirS3:
    """The two S3 calls a step makes, on top of a directory tree."""

    def __init__(self, root: Path):
        self.root = root

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 (boto3 names)
        return {"Body": _Body((self.root / Bucket / Key).read_bytes())}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_extra) -> dict:  # noqa: N803
        path = self.root / Bucket / Key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Body)
        return {}


def load_step(entrypoint: str):
    module_name, class_name = entrypoint.split(":")
    return getattr(importlib.import_module(module_name), class_name)()


def run_step(spec: dict, s3: DirS3, parents: dict, params: dict):
    config = {**params, **(spec.get("config") or {}), "step_name": spec["name"]}
    upstream = [k for p in spec.get("depends_on", []) for k in parents[p].output_keys]
    ctx = StepContext(
        s3=s3,
        input_bucket=BUCKET,
        input_keys=upstream,
        output_bucket=BUCKET + "-output",
        parent_output_bucket=BUCKET + "-output" if spec.get("depends_on") else "",
        config=config,
        run_id="local",
        parent_results={p: parents[p] for p in spec.get("depends_on", [])},
    )
    step = load_step(spec["entrypoint"])
    step.setup(config)
    try:
        return step.run(ctx)
    finally:
        step.teardown()


def stage_inputs(data_dir: Path, root: Path, keys: set[str]) -> None:
    bucket = root / BUCKET
    bucket.mkdir(parents=True, exist_ok=True)
    for key in keys:
        shutil.copyfile(data_dir / key, bucket / key)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the workflow's steps locally")
    parser.add_argument("--data", type=Path, required=True, help="directory with the input CSVs")
    parser.add_argument("--workflow", type=Path, default=HERE / "workflow.json")
    parser.add_argument("--step", help="stop after this step")
    args = parser.parse_args()
    spec = json.loads(args.workflow.read_text(encoding="utf-8"))
    sys.path.insert(0, str(args.workflow.resolve().parent))
    root = Path("local-run")
    keys = {
        v for s in spec["steps"] for k, v in (s.get("config") or {}).items() if k.endswith("_key")
    }
    stage_inputs(args.data, root, keys)
    s3, results = DirS3(root), {}
    for step_spec in spec["steps"]:
        results[step_spec["name"]] = run_step(step_spec, s3, results, {})
        print(f"[{step_spec['name']}] ok → {results[step_spec['name']].output_keys}")
        if step_spec["name"] == args.step:
            break
    print(f"outputs in {root / (BUCKET + '-output')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
