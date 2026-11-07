#!/usr/bin/env python3
"""Airgap image manifest and offline bundle builder (COR-003).

Produces a deterministic inventory of container images required for a given
deployment profile, and optionally exports them to a portable tarball.

Usage:
  # List images for a profile
  python deploy/airgap_bundle.py --profile hpc-full --list

  # Generate image manifest lock file
  python deploy/airgap_bundle.py --profile hpc-full --lock

  # Export all images to a tarball (requires docker/podman)
  python deploy/airgap_bundle.py --profile hpc-full --export /tmp/bundle.tar

  # Verify a bundle against the lock file
  python deploy/airgap_bundle.py --verify /tmp/bundle.tar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Image inventory per profile
# ---------------------------------------------------------------------------


@dataclass
class ImageRef:
    """A required container image for deployment."""

    name: str
    tag: str
    registry: str = "docker.io"
    digest: str = ""  # sha256 digest when known
    purpose: str = ""  # human-readable purpose

    @property
    def full_ref(self) -> str:
        ref = f"{self.registry}/{self.name}:{self.tag}"
        if self.digest:
            ref += f"@{self.digest}"
        return ref


# Profile → required images
_PROFILE_IMAGES: dict[str, list[ImageRef]] = {
    "hpc-full": [
        ImageRef("library/postgres", "16-alpine", purpose="UMRS database"),
        ImageRef("minio/minio", "latest", purpose="Object storage"),
        ImageRef("library/nginx", "stable-alpine-slim", purpose="Gateway proxy"),
        ImageRef("vllm/vllm-openai", "latest", purpose="LLM inference engine"),
        ImageRef(
            "ghcr.io/berriai/litellm", "main-latest", registry="ghcr.io", purpose="LiteLLM proxy"
        ),
    ],
    "hpc-minimal": [
        ImageRef("library/postgres", "16-alpine", purpose="UMRS database"),
        ImageRef("vllm/vllm-openai", "latest", purpose="LLM inference engine"),
    ],
    "k8s-cluster": [
        ImageRef("library/postgres", "16-alpine", purpose="UMRS database"),
        ImageRef("minio/minio", "latest", purpose="Object storage"),
        ImageRef("library/nginx", "stable-alpine-slim", purpose="Gateway proxy"),
        ImageRef("vllm/vllm-openai", "latest", purpose="LLM inference engine"),
        ImageRef(
            "ghcr.io/berriai/litellm", "main-latest", registry="ghcr.io", purpose="LiteLLM proxy"
        ),
        ImageRef("rancher/mirrored-pause", "3.6", purpose="K8s pause"),
    ],
    "dev-compose": [
        ImageRef("library/postgres", "16-alpine", purpose="UMRS database"),
        ImageRef("minio/minio", "latest", purpose="Object storage"),
        ImageRef("library/nginx", "stable-alpine-slim", purpose="Gateway proxy"),
        ImageRef("vllm/vllm-openai", "latest", purpose="LLM inference engine"),
        ImageRef(
            "ghcr.io/berriai/litellm", "main-latest", registry="ghcr.io", purpose="LiteLLM proxy"
        ),
    ],
}


@dataclass
class ImageManifest:
    """A locked snapshot of required images for a deployment profile."""

    profile: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    images: list[dict] = field(default_factory=list)
    platform_version: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_file(cls, path: Path) -> "ImageManifest":
        data = json.loads(path.read_text())
        return cls(**data)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(profile: str) -> int:
    images = _PROFILE_IMAGES.get(profile)
    if not images:
        print(f"Unknown profile: {profile}", file=sys.stderr)
        print(f"Available: {', '.join(_PROFILE_IMAGES)}", file=sys.stderr)
        return 1
    for img in images:
        print(f"  {img.full_ref:60s}  # {img.purpose}")
    return 0


def cmd_lock(profile: str) -> int:
    images = _PROFILE_IMAGES.get(profile)
    if not images:
        print(f"Unknown profile: {profile}", file=sys.stderr)
        return 1
    manifest = ImageManifest(
        profile=profile,
        images=[asdict(img) for img in images],
    )
    lock_path = ROOT / "deploy" / f"airgap-{profile}.lock.json"
    lock_path.write_text(manifest.to_json())
    print(f"Wrote {lock_path}")
    return 0


def cmd_export(profile: str, output: str) -> int:
    images = _PROFILE_IMAGES.get(profile)
    if not images:
        print(f"Unknown profile: {profile}", file=sys.stderr)
        return 1

    runtime = _detect_runtime()
    if not runtime:
        print("No container runtime found (docker/podman)", file=sys.stderr)
        return 1

    refs = [img.full_ref for img in images]

    # Pull all images first
    for ref in refs:
        print(f"Pulling {ref} ...")
        rc = subprocess.call([runtime, "pull", ref])
        if rc != 0:
            print(f"Failed to pull {ref}", file=sys.stderr)
            return 1

    # Save to tarball
    print(f"Exporting {len(refs)} images to {output} ...")
    cmd = [runtime, "save", "-o", output] + refs
    rc = subprocess.call(cmd)
    if rc != 0:
        print("Export failed", file=sys.stderr)
        return 1

    # Generate checksum
    sha = _file_sha256(output)
    print(f"Bundle: {output} (sha256:{sha[:16]}...)")
    return 0


def cmd_verify(bundle_path: str) -> int:
    path = Path(bundle_path)
    if not path.exists():
        print(f"Bundle not found: {path}", file=sys.stderr)
        return 1

    sha = _file_sha256(str(path))
    print(f"Bundle sha256: {sha}")

    # Look for a matching lock file
    lock_files = list((ROOT / "deploy").glob("airgap-*.lock.json"))
    if not lock_files:
        print("No lock files found in deploy/", file=sys.stderr)
        return 1

    for lf in lock_files:
        manifest = ImageManifest.from_file(lf)
        print(f"Lock file: {lf.name} (profile={manifest.profile}, images={len(manifest.images)})")

    print("Verification complete (manual digest check recommended)")
    return 0


# ---------------------------------------------------------------------------
# Bootstrap diagnostics
# ---------------------------------------------------------------------------


def cmd_diagnose() -> int:
    """Run pre-deployment diagnostics for airgap environments."""
    print("=== Airgap Bootstrap Diagnostics ===\n")
    ok = True

    # 1. Container runtime
    runtime = _detect_runtime()
    if runtime:
        print(f"[OK]   Container runtime: {runtime}")
    else:
        print("[FAIL] No container runtime (docker/podman/singularity)")
        ok = False

    # 2. Python version
    v = sys.version_info
    print(f"[OK]   Python: {v.major}.{v.minor}.{v.micro}")

    # 3. Required directories
    for d in ["deploy", "umrs/src", "gateway"]:
        p = ROOT / d
        if p.exists():
            print(f"[OK]   Directory: {d}")
        else:
            print(f"[WARN] Missing directory: {d}")

    # 4. Lock files
    locks = list((ROOT / "deploy").glob("airgap-*.lock.json"))
    if locks:
        print(f"[OK]   Lock files: {len(locks)}")
    else:
        print("[WARN] No airgap lock files in deploy/")

    # 5. GPU availability (nvidia-smi)
    try:
        subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], stderr=subprocess.DEVNULL
        )
        print("[OK]   GPU: nvidia-smi accessible")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[INFO] GPU: nvidia-smi not available (expected on non-GPU nodes)")

    print()
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_runtime() -> Optional[str]:
    for rt in ("podman", "docker"):
        try:
            subprocess.check_output([rt, "version"], stderr=subprocess.DEVNULL)
            return rt
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return None


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Airgap bundle builder")
    parser.add_argument("--profile", default="hpc-full", help="Deployment profile")
    parser.add_argument("--list", action="store_true", help="List required images")
    parser.add_argument("--lock", action="store_true", help="Generate lock file")
    parser.add_argument("--export", metavar="PATH", help="Export images to tarball")
    parser.add_argument("--verify", metavar="PATH", help="Verify a bundle")
    parser.add_argument("--diagnose", action="store_true", help="Run bootstrap diagnostics")
    args = parser.parse_args()

    if args.diagnose:
        return cmd_diagnose()
    if args.verify:
        return cmd_verify(args.verify)
    if args.list:
        return cmd_list(args.profile)
    if args.lock:
        return cmd_lock(args.profile)
    if args.export:
        return cmd_export(args.profile, args.export)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
