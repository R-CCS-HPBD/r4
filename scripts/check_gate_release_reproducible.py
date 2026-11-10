#!/usr/bin/env python3
"""Build the r4 wheel and packaged gate twice; compare their exact hashes.

This is a read-only source check with outputs in a caller-selected temporary
workspace. It never signs, uploads, tags or publishes anything. The same
``SOURCE_DATE_EPOCH`` and deterministic interpreter settings cover both builds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GATE_MEMBER = "r4_contracts/hpc_gate.py"


class ReproducibilityError(RuntimeError):
    """The two builds differ or did not produce exactly one expected wheel."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gate_from_wheel(wheel: Path) -> bytes:
    with zipfile.ZipFile(wheel) as archive:
        return archive.read(GATE_MEMBER)


def manifest_from_wheel(wheel: Path, min_client: str, expires: int) -> bytes:
    """Write release bytes from a built gate; the maintainer signs them separately."""
    from r4 import hpc_release
    from r4_contracts import hpc_gate

    gate = gate_from_wheel(wheel)
    return hpc_release.manifest_bytes(
        gate, gate_version=hpc_gate.GATE_VERSION, min_client=min_client, expires=expires
    )


def _one_wheel(out: Path, project: str) -> Path:
    dist = project.replace("-", "_")
    wheels = list(out.glob(f"{dist}-*.whl"))
    if len(wheels) != 1:
        raise ReproducibilityError(f"expected one {dist} wheel, found {len(wheels)}")
    return wheels[0]


def _build_once(root: Path, out: Path, epoch: int, label: str, runner: Callable[..., Any]) -> dict:
    env = {
        **os.environ,
        "SOURCE_DATE_EPOCH": str(epoch),
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
        "BUILD_LABEL": label,
    }
    for project in ("r4", "r4-contracts"):
        runner(
            ["uv", "build", "--wheel", "--no-sources", "--out-dir", str(out), str(root / project)],
            cwd=root,
            env=env,
            check=True,
        )
    r4 = _one_wheel(out, "r4").read_bytes()
    gate = gate_from_wheel(_one_wheel(out, "r4-contracts"))
    return {"r4_sha256": _sha256(r4), "gate_sha256": _sha256(gate)}


def build_reproducibly(
    root: Path, work_dir: Path, epoch: int, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any]:
    """Build twice below ``work_dir`` and refuse any digest difference."""
    work_dir.mkdir(parents=True, exist_ok=True)
    builds = [_build_once(root, work_dir / label, epoch, label, runner) for label in ("a", "b")]
    mismatches = [key for key in builds[0] if builds[0][key] != builds[1][key]]
    if mismatches:
        raise ReproducibilityError(f"reproducibility_mismatch: {', '.join(mismatches)}")
    return {**builds[0], "source_date_epoch": epoch, "builds": builds}


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--epoch", type=int, default=0)
    parser.add_argument("--manifest", type=Path, help="also write unsigned canonical release JSON")
    parser.add_argument("--min-client", help="required with --manifest")
    parser.add_argument("--expires", type=int, help="required with --manifest: Unix timestamp")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.manifest and (args.min_client is None or args.expires is None):
        raise ReproducibilityError("--manifest needs --min-client and --expires")
    if args.work_dir:
        result = build_reproducibly(args.root, args.work_dir, args.epoch)
        if args.manifest:
            wheel = _one_wheel(args.work_dir / "a", "r4-contracts")
            args.manifest.write_bytes(manifest_from_wheel(wheel, args.min_client, args.expires))
    else:
        with tempfile.TemporaryDirectory(prefix="r4-gate-repro-") as tmp:
            result = build_reproducibly(args.root, Path(tmp), args.epoch)
            if args.manifest:
                wheel = _one_wheel(Path(tmp) / "a", "r4-contracts")
                args.manifest.write_bytes(manifest_from_wheel(wheel, args.min_client, args.expires))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ReproducibilityError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"gate-release reproducibility check failed: {exc}", file=sys.stderr)
        sys.exit(1)
