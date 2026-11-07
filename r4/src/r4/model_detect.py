"""
Auto-detect model capabilities from local model files.

Reads config.json, tokenizer_config.json, and generation_config.json
to infer capabilities like tool calling, vision, context length, etc.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Architecture classes that indicate vision support
_VISION_ARCHITECTURES = {
    "qwen2vlforconditionalgeneration",
    "qwen2_5_vlforconditionalgeneration",
    "qwenvlforconditionalgeneration",
    "llavaforconditionalgeneration",
    "llavanextforconditionalgeneration",
    "llavanextvideo",
    "internvlchatmodel",
    "phi3vforconditionalgeneration",
    "minicpmv",
    "cogvlmforcausallm",
    "deepseekvisuallanguagemodel",
    "mantaforconditionalgeneration",
    "pixtralforconditionalgeneration",
    "molmoforconditionalgeneration",
    "chameleonforconditionalgeneration",
    "fuyu",
    "paligemmaforconditionalgeneration",
    "glm4vforcausallm",
    "idefics2forconditionalgeneration",
    "idefics3forconditionalgeneration",
    # Kimi K2.x multimodal LLMs (MoonViT; K2.6 / K2.7-Code)
    "kimik25forconditionalgeneration",
}

# Patterns in chat_template that indicate tool calling support
_TOOL_CALLING_PATTERNS = [
    r"<tool_call>",
    r"<\|tool_call\|>",
    r'"tool_calls"',
    r"tool_call",
    r'"function"',
    r"<function",
    r"<\|plugin\|>",
    r"tools",
    r"<\|tool_start\|>",
    r"<\|tool_end\|>",
]

# Compiled regex for tool calling detection
_TOOL_CALLING_RE = re.compile("|".join(_TOOL_CALLING_PATTERNS), re.IGNORECASE)


@dataclass
class DetectedCapabilities:
    """Auto-detected model capabilities from local files."""

    model_family: Optional[str] = None
    supports_tool_calling: Optional[bool] = None
    supports_vision: Optional[bool] = None
    supports_structured_output: Optional[bool] = None
    max_output_tokens: Optional[int] = None
    max_position_embeddings: Optional[int] = None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON file, returning None on any error."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _normalize_model_family(model_type: Any) -> Optional[str]:
    if not model_type:
        return None

    family = str(model_type).lower()
    family_prefixes = {
        "qwen": "qwen",
        "llama": "llama",
        "mistral": "mistral",
        "mixtral": "mistral",
        "phi": "phi",
        "gemma": "gemma",
        "deepseek": "deepseek",
        "internlm": "internlm",
        "chatglm": "glm",
        "glm": "glm",
        "starcoder": "starcoder",
        "codellama": "codellama",
    }
    for prefix, normalized in family_prefixes.items():
        if family.startswith(prefix):
            return normalized
    return family


def _max_position_embeddings(config: Dict[str, Any]) -> Optional[int]:
    max_pos = config.get("max_position_embeddings")
    if isinstance(max_pos, int) and max_pos > 0:
        return max_pos

    text_cfg = config.get("text_config")
    if isinstance(text_cfg, dict):
        max_pos = text_cfg.get("max_position_embeddings")
        if isinstance(max_pos, int) and max_pos > 0:
            return max_pos
    return None


# HuggingFace tokenizers use very large sentinel values (e.g. 1e30) for
# ``model_max_length`` when no real limit is defined. Anything at/above this
# threshold is treated as "unset" rather than a real context length.
_MODEL_MAX_LENGTH_SENTINEL = 10**15


def _tokenizer_model_max_length(tok_config: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the tokenizer's ``model_max_length`` if it is a real value.

    The tokenizer's ``model_max_length`` is often the most honest usable
    context length (e.g. 65k for bonsai-8B), whereas ``config.json``'s
    ``max_position_embeddings`` can be rope-scaled/inflated (e.g. 512k).
    HuggingFace sentinel values (~1e30) are ignored.
    """
    if not tok_config:
        return None
    raw = tok_config.get("model_max_length")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and 0 < raw < _MODEL_MAX_LENGTH_SENTINEL:
        return int(raw)
    return None


def _resolve_context_length(
    config_max_pos: Optional[int], tokenizer_max_len: Optional[int]
) -> Optional[int]:
    """Pick the real usable context length from config and tokenizer values.

    Prefers the smaller of the two when both are present (the config value is
    frequently rope-scaled higher than the tokenizer's real limit). Falls back
    to whichever single value is available.
    """
    candidates = [v for v in (config_max_pos, tokenizer_max_len) if v]
    if not candidates:
        return None
    return min(candidates)


def _detect_vision_support(config: Dict[str, Any]) -> bool:
    architectures = config.get("architectures", [])
    if architectures:
        arch_lower = {str(architecture).lower() for architecture in architectures}
        if arch_lower & _VISION_ARCHITECTURES:
            return True

    vision_keys = {
        "vision_config",
        "visual",
        "image_size",
        "vision_tower",
        "media_placeholder_token_id",
        "mm_hidden_size",
        "mm_projector_type",
    }
    return bool(vision_keys & set(config.keys()))


def _chat_template_text(chat_template: Any) -> str:
    if isinstance(chat_template, list):
        return " ".join(
            item.get("template", "") if isinstance(item, dict) else str(item)
            for item in chat_template
        )
    return chat_template if isinstance(chat_template, str) else ""


def _detect_tool_calling(tok_config: Optional[Dict[str, Any]]) -> bool:
    if not tok_config:
        return False
    chat_template = _chat_template_text(tok_config.get("chat_template", ""))
    return bool(chat_template and _TOOL_CALLING_RE.search(chat_template))


def _max_output_tokens(gen_config: Optional[Dict[str, Any]]) -> Optional[int]:
    if not gen_config:
        return None
    max_new = gen_config.get("max_new_tokens")
    return max_new if isinstance(max_new, int) and max_new > 0 else None


def detect_capabilities_from_files(
    config: Optional[Dict[str, Any]],
    tok_config: Optional[Dict[str, Any]],
    gen_config: Optional[Dict[str, Any]],
) -> DetectedCapabilities:
    """
    Detect capabilities from pre-loaded config dicts.

    This is the core detection logic shared by the local filesystem path
    and the remote SSH path. Each argument is the parsed JSON contents of
    the corresponding model file (``config.json``, ``tokenizer_config.json``,
    ``generation_config.json``). ``None`` means the file was missing or
    unreadable.

    Returns:
        DetectedCapabilities with fields set where detection succeeded.
    """
    result = DetectedCapabilities()

    # --- config.json ---
    config_max_pos: Optional[int] = None
    if config:
        result.model_family = _normalize_model_family(config.get("model_type"))
        config_max_pos = _max_position_embeddings(config)
        result.supports_vision = _detect_vision_support(config)

    # --- tokenizer_config.json ---
    result.supports_tool_calling = _detect_tool_calling(tok_config)

    # Context length: prefer the real usable limit. config.json's
    # max_position_embeddings is often rope-scaled/inflated, while the
    # tokenizer's model_max_length reflects the actual usable context.
    result.max_position_embeddings = _resolve_context_length(
        config_max_pos, _tokenizer_model_max_length(tok_config)
    )

    # Structured output generally works if tool calling works
    if result.supports_tool_calling:
        result.supports_structured_output = True

    # --- generation_config.json ---
    result.max_output_tokens = _max_output_tokens(gen_config)

    logger.info(
        "Detected capabilities: family=%s tools=%s vision=%s ctx=%s max_out=%s",
        result.model_family,
        result.supports_tool_calling,
        result.supports_vision,
        result.max_position_embeddings,
        result.max_output_tokens,
    )

    return result


def detect_capabilities(model_path: str) -> DetectedCapabilities:
    """
    Detect model capabilities from local model files.

    Reads:
    - config.json → model_type (family), architectures (vision),
      max_position_embeddings (context length)
    - tokenizer_config.json → chat_template (tool calling)
    - generation_config.json → max_new_tokens (max output)

    Args:
        model_path: Path to model directory containing config files.

    Returns:
        DetectedCapabilities with fields set where detection succeeded.
    """
    base = Path(model_path)

    if not base.is_dir():
        logger.debug("Model path %s is not a directory, skipping detection", model_path)
        return DetectedCapabilities()

    return detect_capabilities_from_files(
        config=_read_json(base / "config.json"),
        tok_config=_read_json(base / "tokenizer_config.json"),
        gen_config=_read_json(base / "generation_config.json"),
    )
