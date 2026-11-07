"""Application profiles for the video-feed pipeline.

A profile declares: how a client-side clip directory is scanned, what metadata
is carried per clip, which workflow to trigger, and how results are shaped.

Profiles are Data — a YAML per application. ``example.yaml`` ships beside
the module; real application profiles live outside this repository
(2026-11-03) and load from ``$VIDEO_FEED_PROFILE_DIR`` (searched first) or
``--profile-file``. Authors add new ones without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # host may be stdlib-only (e.g. a cluster login node) — lazy
    yaml = None

try:
    from typing_extensions import TypedDict
except ImportError:  # py3.8 fallback no typing_extensions? fine — plain dict
    TypedDict = dict  # type: ignore


PROFILES_DIR = Path(__file__).parent / "profiles"
PROFILE_DIR_ENV = "VIDEO_FEED_PROFILE_DIR"


def _profile_dirs() -> list[Path]:
    extra = os.environ.get(PROFILE_DIR_ENV)
    return ([Path(extra).expanduser()] if extra else []) + [PROFILES_DIR]


def _profile_path(name: str) -> Path:
    for directory in _profile_dirs():
        candidate = directory / f"{name}.yaml"
        if candidate.exists():
            return candidate
    return PROFILES_DIR / f"{name}.yaml"


@dataclass
class FeedProfile:
    """Per-app ingest profile.

    Fields:
      name:            profile id ("example", ...)
      site:            site hint ("local", "analyst-laptop", ...) — metadata tag
      pattern:         file glob for clips relative to source root
      metadata_suffix: sidecar file suffix with capture metadata
      meta_keys:       ordered list of metadata json keys to carry (camel or snake)
      bucket_purpose:  right bucket tag used in gateway upload `purpose` field
      workflow:        registered workflow name on the platform
      workflow_params: base params merged into every trigger payload
      dedupe_fields:   which capture fields form the idempotency tuple
      embed_kinds:     embedding kinds this app produces ("pgvector" kinds)
      kg:              knowledge-graph node/edge hints for the results adapter
      assets:          optional asset knobs (plan_mode='per-clip', model_key,
                       inventory/plan column templates)
    """

    name: str
    site: str
    pattern: str
    metadata_suffix: str
    meta_keys: list[str]
    bucket_purpose: str
    workflow: str
    workflow_params: dict[str, Any] = field(default_factory=dict)
    dedupe_fields: tuple[str, ...] = ()
    embed_kinds: list[str] = field(default_factory=list)
    kg: dict[str, Any] = field(default_factory=dict)
    assets: dict[str, Any] = field(default_factory=dict)


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # stdlib fallback: min-yaml via json-like eval is unsafe; raise with guidance
    raise RuntimeError(f"yaml module unavailable and required to load profile {path}")


def load_profile(name: str, file: str | Path | None = None) -> FeedProfile:
    path = Path(file) if file else _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {path}")
    data = _load_yaml(path)
    wf = dict(data.get("workflow", {}))
    return FeedProfile(
        name=data["name"],
        site=data.get("site", ""),
        pattern=data.get("pattern", "*.mkv"),
        metadata_suffix=data.get("metadata_suffix", "_metadata.json"),
        meta_keys=list(data.get("meta_keys", [])),
        bucket_purpose=data.get("bucket_purpose", f"video-feed-{data['name']}"),
        workflow=wf.get("name", data["name"]),
        workflow_params=dict(wf.get("params", {})),
        dedupe_fields=tuple(data.get("dedupe_fields", ()) or ()),
        embed_kinds=list(data.get("embed_kinds", [])),
        kg=dict(data.get("kg", {})),
        assets=dict(data.get("assets", {})),
    )


def list_profiles() -> list[str]:
    return sorted({p.stem for d in _profile_dirs() for p in d.glob("*.yaml")})
