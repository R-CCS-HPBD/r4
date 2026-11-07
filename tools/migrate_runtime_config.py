#!/usr/bin/env python3
"""
One-time migration: populate ``runtime_config`` for all registered models.

Reads the current pool roster (pool-ai4s.yaml) and the serve.slurm derivation
logic, then writes authoritative ``runtime_config`` entries into the Postgres
``model_catalog_entries`` table for every model that has a matching entry.

This is additive and non-breaking:
- Models without entries keep empty runtime_config (serve.slurm fallback)
- Models with entries get explicit config (serve.slurm env vars override bash)
- The JSON catalog fallback is NOT removed (deprecated in a later phase)

Usage:
    cd umrs
    source deploy/ai4s/.env
    export UMRS_SITE_CONFIG=deploy/ai4s/site.yaml
    .venv/bin/python tools/migrate_runtime_config.py [--dry-run]

Requirements:
    - Postgres reachable (uses build_durable_job_db_pool_from_env)
    - pool-ai4s.yaml readable
    - Python 3.10+
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# ── Project root ────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "umrs" / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("migrate_runtime_config")

# ── Pool roster path ────────────────────────────────────────────────────────
_POOL_ROSTER = _PROJECT_ROOT / "umrs" / "deploy" / "ai4s" / "pool-ai4s.yaml"


# ── serve.slurm derivation logic (mirrored from bash) ───────────────────────


def derive_tool_parser(model_name: str) -> str | None:
    """Mirrors serve.slurm:derive_tool_parser()."""
    lc = model_name.lower()
    if "deepseek" in lc and "v4" in lc:
        return "deepseek_v4"
    if "deepseek" in lc:
        return "deepseek_v3"
    if any(p in lc for p in ("glm-4.5", "glm4.5", "glm45")):
        return "glm45"
    if "glm" in lc:
        return "glm47"
    if "kimi" in lc:
        return "kimi_k2"
    if "llm-jp" in lc or "llm_jp" in lc:
        return "hermes"
    if "gpt-oss" in lc and "swallow" in lc:
        return ""  # explicitly disabled
    if "gemma" in lc and "4" in lc:
        return "gemma4"
    return "hermes"


def derive_reasoning_parser(model_name: str) -> str:
    """Mirrors serve.slurm:derive_reasoning_parser()."""
    lc = model_name.lower()
    if "glm" in lc:
        return "glm45"
    if "gemma" in lc and "4" in lc:
        return "gemma4"
    if "kimi" in lc:
        return "kimi_k2"
    if "gpt-oss" in lc and "swallow" in lc:
        return "gpt_oss"
    if "llm-jp" in lc or "llm_jp" in lc:
        return ""  # disabled — not in SIF
    return ""


def derive_kv_cache_dtype(tool_parser: str | None) -> str | None:
    """Mirrors serve.slurm:derive_kv_cache_dtype()."""
    if tool_parser == "deepseek_v4":
        return "fp8"
    return None


def derive_enforce_eager(model_name: str) -> bool:
    """Mirrors serve.slurm: CUDA graph/eager logic."""
    lc = model_name.lower()
    if "glm" in lc:
        return True
    return "deepseek" in lc and "v4" in lc


def derive_flashinfer_moe_fp16(model_name: str) -> bool:
    """Mirrors serve.slurm: FlashInfer-TRTLLM MoE kernel."""
    lc = model_name.lower()
    # GLM: NaN on tools routing
    if "glm" in lc:
        return False
    # llm-jp: expert intermediate_size=64 not %128
    return not ("llm-jp" in lc or "llm_jp" in lc)


def derive_chat_template_format(model_name: str) -> str | None:
    """Mirrors serve.slurm: --chat-template-content-format."""
    lc = model_name.lower()
    if "glm" in lc:
        return "string"
    return None


def derive_mm_encoder_tp_mode(model_name: str) -> str | None:
    """Mirrors serve.slurm / pool-ai4s.yaml for mm_encoder_tp_mode."""
    lc = model_name.lower()
    if "kimi-k2.7" in lc or "kimi_k2.7" in lc:
        return "data"
    return None


def derive_model_family(model_name: str) -> str:
    """Derive model family tag from model name."""
    lc = model_name.lower()
    if "deepseek" in lc and "v4" in lc:
        return "deepseek_v4"
    if "deepseek" in lc:
        return "deepseek_v3"
    if "glm" in lc:
        return "glm"
    if "kimi" in lc:
        return "kimi_k2"
    if "llm-jp" in lc or "llm_jp" in lc:
        return "llm_jp"
    if "gpt-oss" in lc and "swallow" in lc:
        return "gpt_oss"
    if "gemma" in lc and "4" in lc:
        return "gemma4"
    if "diffusion" in lc:
        return "diffusion_gemma"
    return "unknown"


def build_runtime_config(model_name: str, pool_entry: dict | None = None) -> dict[str, Any]:
    """Build a runtime_config dict for a model from serve.slurm derivations.

    Pool entry env_vars override the derivation for VLLM_MAX_NUM_SEQS,
    VLLM_GPU_MEMORY_UTILIZATION, etc., but NOT for parser/kernel decisions.
    """
    tool_parser = derive_tool_parser(model_name)
    reasoning_parser = derive_reasoning_parser(model_name)
    kv_cache = derive_kv_cache_dtype(tool_parser)

    cfg: dict[str, Any] = {
        "model_family": derive_model_family(model_name),
        "tool_call_parser": tool_parser,
        "reasoning_parser": reasoning_parser,
        "kv_cache_dtype": kv_cache,
        "enforce_eager": derive_enforce_eager(model_name),
        "flashinfer_moe_fp16": derive_flashinfer_moe_fp16(model_name),
        "chat_template_format": derive_chat_template_format(model_name),
        "mm_encoder_tp_mode": derive_mm_encoder_tp_mode(model_name),
        "trust_remote_code": True,
        "enable_auto_tool_choice": tool_parser not in (None, ""),
        "enable_prefix_caching": True,
    }
    return cfg


# ── Main ─────────────────────────────────────────────────────────────────────


def load_pool_roster(path: Path) -> dict[str, dict[str, Any]]:
    """Load pool-ai4s.yaml and index by model name."""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; skipping pool roster")
        return {}
    if not path.exists():
        logger.warning("Pool roster not found: %s", path)
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    defaults = data.get("defaults") or {}
    entries = data.get("endpoints") or []
    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        model = entry.get("model")
        if model:
            merged = {**defaults, **entry}
            index[model] = merged
    return index


def _migrate_dry_run_log(model_name: str, rc) -> None:
    logger.info(
        "  [DRY-RUN] %s  family=%s parser=%s reasoning=%s kv=%s eager=%s moe_fp16=%s",
        model_name,
        rc.model_family,
        rc.tool_call_parser,
        rc.reasoning_parser,
        rc.kv_cache_dtype,
        rc.enforce_eager,
        rc.flashinfer_moe_fp16,
    )


def _migrate_capabilities_json(conn, model_name: str) -> str | None:
    from umrs.model_registry.capabilities import ModelCapabilities, resolve_capabilities

    cur = conn.cursor()
    cur.execute(
        "SELECT capabilities FROM model_catalog_entries WHERE name = %s AND scope = 'system'",
        (model_name,),
    )
    cap_row = cur.fetchone()
    existing_caps = ModelCapabilities.from_dict(cap_row[0] if cap_row and cap_row[0] else None)
    return json.dumps(resolve_capabilities(model_name, existing_caps).to_dict())


def _migrate_one_entry(conn, model_name: str, runtime_dict, caps_json: str | None) -> bool:
    cur = conn.cursor()
    cur.execute(
        "UPDATE model_catalog_entries SET runtime_config = %s WHERE name = %s AND scope = 'system'",
        (json.dumps(runtime_dict), model_name),
    )
    if caps_json is not None and cur.rowcount:
        cur.execute(
            "UPDATE model_catalog_entries SET capabilities = %s::jsonb "
            "WHERE name = %s AND scope = 'system'",
            (caps_json, model_name),
        )
    if not cur.rowcount:
        logger.debug("  Skipped %s: no system-scope entry", model_name)
        return False
    conn.commit()
    logger.info(
        "  Updated %s: family=%s parser=%s reasoning=%s",
        model_name,
        runtime_dict.get("model_family"),
        runtime_dict.get("tool_call_parser"),
        runtime_dict.get("reasoning_parser"),
    )
    return True


def migrate(dry_run: bool = False, backfill_effort: bool = True) -> None:
    from umrs.model_registry.runtime_config import RuntimeConfig
    from umrs.model_registry.store import AccessContext, ModelStore, PathValidator
    from umrs.server.job_tracking_handler import build_durable_job_db_pool_from_env

    pool_index = load_pool_roster(_POOL_ROSTER)

    db_pool = build_durable_job_db_pool_from_env()
    conn = db_pool.getconn()
    try:
        store = ModelStore(conn, path_validator=PathValidator())
        entries = store.list_models(
            AccessContext(user_id="", is_admin=True),
            limit=500,
        )

        updated = 0
        skipped = 0
        for entry in entries:
            model_name = entry.name
            runtime_dict = build_runtime_config(model_name, pool_index.get(model_name, None))

            if dry_run:
                _migrate_dry_run_log(model_name, RuntimeConfig.from_dict(runtime_dict))
                updated += 1
                continue

            caps_json = _migrate_capabilities_json(conn, model_name) if backfill_effort else None
            if _migrate_one_entry(conn, model_name, runtime_dict, caps_json):
                updated += 1
            else:
                skipped += 1

        logger.info("Migration complete: %d updated, %d skipped", updated, skipped)
        if dry_run:
            logger.info("DRY RUN -- no changes committed.")
    finally:
        db_pool.putconn(conn)
        db_pool.closeall()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate runtime_config for registered models")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without committing")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
