#!/usr/bin/env python3
"""Model manifest locks for airgap and reproducible deployments (COR-003).

Produces a deterministic inventory of pre-staged model files required for
a given deployment profile, with sha256 checksums for integrity verification.

Usage:
  # List models for a profile
  python deploy/model_manifest.py --profile hpc-full --list

  # Generate model manifest lock file
  python deploy/model_manifest.py --profile hpc-full --lock

  # Verify pre-staged models against lock file
  python deploy/model_manifest.py --verify --profile hpc-full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Model inventory per profile
# ---------------------------------------------------------------------------


@dataclass
class ModelManifestRef:
    """A required model for deployment."""

    name: str
    path: str  # Filesystem path or HuggingFace repo id
    version: str = ""
    source: str = "filesystem_path"  # filesystem_path | hf_repo_ref
    sha256: str = ""  # File-level or directory-level hash
    purpose: str = ""  # human-readable purpose
    model_type: str = "llm"  # llm | embedding | vision | reranker

    @property
    def display(self) -> str:
        src = f"[{self.source}]"
        ver = f" v{self.version}" if self.version else ""
        return f"{self.name}{ver} {src} @ {self.path}"


# Profile → required models
# Paths use env-var placeholders; resolve at lock/verify time.
_PROFILE_MODELS: dict[str, list[ModelManifestRef]] = {
    "hpc-full": [
        ModelManifestRef(
            "Qwen2.5-7B-Instruct",
            path="${VLLM_MODEL_PATH}",
            source="filesystem_path",
            purpose="Primary LLM for inference",
            model_type="llm",
        ),
        ModelManifestRef(
            "all-MiniLM-L6-v2",
            path="${EMBEDDING_MODEL_PATH}",
            source="filesystem_path",
            purpose="Embedding model for RAG",
            model_type="embedding",
        ),
    ],
    "hpc-minimal": [
        ModelManifestRef(
            "Qwen2.5-7B-Instruct",
            path="${VLLM_MODEL_PATH}",
            source="filesystem_path",
            purpose="Primary LLM for inference",
            model_type="llm",
        ),
    ],
    "k8s-cluster": [
        ModelManifestRef(
            "Qwen2.5-7B-Instruct",
            path="${VLLM_MODEL_PATH}",
            source="filesystem_path",
            purpose="Primary LLM for inference",
            model_type="llm",
        ),
        ModelManifestRef(
            "all-MiniLM-L6-v2",
            path="${EMBEDDING_MODEL_PATH}",
            source="filesystem_path",
            purpose="Embedding model for RAG",
            model_type="embedding",
        ),
    ],
    "dev-compose": [
        ModelManifestRef(
            "Qwen2.5-7B-Instruct",
            path="${VLLM_MODEL_PATH}",
            source="filesystem_path",
            purpose="Primary LLM for inference",
            model_type="llm",
        ),
    ],
    # ADR-093 MD-3 (decided 2026-10-11): rccs stages the GPU/multilingual
    # cross-encoder for POST /rag/rerank. The entry records WHAT is staged;
    # nothing downloads here — staging is a MODEL_DOWNLOAD job / Ansible step,
    # and the rag service loads only a staged directory (a hub id is refused
    # under HF_HUB_OFFLINE=1). Point RERANKER_MODEL_PATH (and the rag
    # service's UMRS_RAG_RERANKER_MODEL) at that directory. Staged is not on:
    # `rerank=auto` stays off (UMRS_RAG_RERANK_DEFAULT=off; S4 failed narrowly).
    # TODO(ADR-093 MD-3): the spec names the site "rccs" but the manifest's
    # profiles are deployment kinds; this profile lists only what MD-3 stages
    # for rccs (its chat model and embedder are staged elsewhere). Fold it
    # into another profile if the maintainer prefers. The hub revision is not
    # pinned yet (version=""), and the lock's sha256 is filled by --lock
    # after staging.
    "rccs": [
        ModelManifestRef(
            "bge-reranker-v2-m3",
            path="${RERANKER_MODEL_PATH}",
            source="filesystem_path",
            purpose=(
                "Cross-encoder for POST /rag/rerank, GPU (ADR-093 D-7, MD-3); "
                "staged from hub BAAI/bge-reranker-v2-m3; off by default "
                "(UMRS_RAG_RERANK_DEFAULT=off)"
            ),
            model_type="reranker",
        ),
    ],
}


@dataclass
class ModelManifest:
    """A locked snapshot of required models for a deployment profile."""

    profile: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    models: list[dict] = field(default_factory=list)
    platform_version: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_file(cls, path: Path) -> ModelManifest:
        data = json.loads(path.read_text())
        return cls(**data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_path(raw_path: str) -> str:
    """Resolve ${ENV_VAR} placeholders in model paths."""
    import re

    def _sub(match: re.Match) -> str:
        var = match.group(1)
        val = os.environ.get(var, "")
        return val

    return re.sub(r"\$\{(\w+)\}", _sub, raw_path)


def _compute_dir_hash(path: Path) -> str:
    """Compute sha256 over all files in a model directory (sorted, deterministic)."""
    h = hashlib.sha256()
    if path.is_file():
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    if not path.is_dir():
        return ""

    # Sort files for determinism
    files = sorted(path.rglob("*"))
    for fp in files:
        if fp.is_file():
            # Include relative path in hash for structure integrity
            rel = fp.relative_to(path)
            h.update(str(rel).encode())
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(profile: str) -> int:
    models = _PROFILE_MODELS.get(profile)
    if not models:
        print(f"Unknown profile: {profile}", file=sys.stderr)
        print(f"Available: {', '.join(_PROFILE_MODELS)}", file=sys.stderr)
        return 1
    for m in models:
        resolved = _resolve_path(m.path)
        # An unset placeholder resolves to "", and Path("") is the cwd, which
        # always exists: check the resolution first, or "?" never shows.
        exists = "?" if not resolved else "✓" if Path(resolved).exists() else "✗"
        print(f"  [{exists}] {m.display}")
        if resolved and resolved != m.path:
            print(f"       → {resolved}")
    return 0


def cmd_lock(profile: str) -> int:
    models = _PROFILE_MODELS.get(profile)
    if not models:
        print(f"Unknown profile: {profile}", file=sys.stderr)
        return 1

    locked: list[dict] = []
    for m in models:
        resolved = _resolve_path(m.path)
        entry = asdict(m)
        entry["resolved_path"] = resolved
        if resolved and Path(resolved).exists():
            entry["sha256"] = _compute_dir_hash(Path(resolved))
            print(f"  Hashed {m.name}: {entry['sha256'][:16]}...")
        else:
            print(f"  [WARN] {m.name}: path not found ({resolved})")
        locked.append(entry)

    manifest = ModelManifest(profile=profile, models=locked)
    lock_path = ROOT / "deploy" / f"model-{profile}.lock.json"
    lock_path.write_text(manifest.to_json())
    print(f"Wrote {lock_path}")
    return 0


def cmd_verify(profile: str) -> int:
    lock_path = ROOT / "deploy" / f"model-{profile}.lock.json"
    if not lock_path.exists():
        print(f"Lock file not found: {lock_path}", file=sys.stderr)
        print("Run with --lock first to generate it.", file=sys.stderr)
        return 1

    manifest = ModelManifest.from_file(lock_path)
    print(f"Verifying {len(manifest.models)} models for profile '{manifest.profile}'")
    print(f"Lock created: {manifest.created_at}\n")

    ok = True
    for entry in manifest.models:
        name = entry.get("name", "?")
        resolved = _resolve_path(entry.get("path", ""))
        expected_hash = entry.get("sha256", "")

        if not resolved or not Path(resolved).exists():
            print(f"  [FAIL] {name}: path not found ({resolved})")
            ok = False
            continue

        if not expected_hash:
            print(f"  [WARN] {name}: no hash in lock file, skipping integrity check")
            continue

        actual_hash = _compute_dir_hash(Path(resolved))
        if actual_hash == expected_hash:
            print(f"  [OK]   {name}: hash matches ({actual_hash[:16]}...)")
        else:
            print(f"  [FAIL] {name}: hash mismatch")
            print(f"         expected: {expected_hash[:32]}...")
            print(f"         actual:   {actual_hash[:32]}...")
            ok = False

    print()
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model manifest lock manager (COR-003)")
    parser.add_argument(
        "--profile",
        choices=list(_PROFILE_MODELS.keys()),
        default="hpc-full",
        help="Deployment profile",
    )
    parser.add_argument("--list", action="store_true", help="List models for profile")
    parser.add_argument("--lock", action="store_true", help="Generate model lock file")
    parser.add_argument("--verify", action="store_true", help="Verify models against lock")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args(argv)

    if args.list:
        return cmd_list(args.profile)
    if args.lock:
        return cmd_lock(args.profile)
    if args.verify:
        return cmd_verify(args.profile)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
