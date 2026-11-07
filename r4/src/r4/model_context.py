"""Best-effort known context-length lookup and resolution for models.

The ``CTX MAX`` column means the model's *maximum supported context length*.
That value can come from three places, in order of trust:

1. ``capabilities.max_position_embeddings`` — detected/declared, authoritative.
2. A curated known-context table (this module), matched by name / HF id.
3. ``serving_profile.max_model_len`` — how the endpoint is *currently served*
   (often a memory-limited choice <= the model's true max, or the 4096 default).

:func:`resolve_max_context` combines these so every recognizable model shows a
real value instead of ``-`` or the misleading 4096 serving placeholder.

The curated table is intentionally conservative and pattern-based: entries are
matched as case-insensitive substrings against the model name and/or
HuggingFace id, and the **first** matching pattern wins. Values reflect the
documented usable context window, not rope-scaled maximums.
"""

from __future__ import annotations

from typing import Optional

# ADR-093 D-8/D-14: the model-size rule lives in r4_contracts.models, shared
# with UMRS (which sizes its retrieval profile from the answer model). These
# names are re-exported unchanged so every existing import keeps working.
from r4_contracts.models import _LARGE_UNSIZED_FAMILIES as _LARGE_UNSIZED_FAMILIES
from r4_contracts.models import _PARAM_COUNT_RE as _PARAM_COUNT_RE
from r4_contracts.models import SIZE_LARGE_MIN_B as SIZE_LARGE_MIN_B
from r4_contracts.models import SIZE_SMALL_MAX_B as SIZE_SMALL_MAX_B
from r4_contracts.models import model_param_billions as model_param_billions
from r4_contracts.models import model_size_class as model_size_class

# The hardcoded serving default (see ServingProfile.max_model_len). When a
# serving profile still carries this value it is treated as "unset" for the
# purpose of the max-context column, so a real known/declared value can win.
_SERVING_DEFAULT_MAX_LEN = 4096

# Ordered (pattern, context_length) pairs. More specific patterns MUST come
# before more general ones (first match wins). DeepSeek is listed before Qwen so
# that distill names like "DeepSeek-R1-Distill-Qwen-32B" resolve to the DeepSeek
# context, not the Qwen one.
_KNOWN_CONTEXT_PATTERNS: list[tuple[str, int]] = [
    # --- Site / platform models (operator-asserted real values) ---
    ("deepseek-v4", 1_048_576),  # DeepSeek-V4-Pro family — 1M context
    ("bonsai", 65_536),  # Bonsai-8B — 64k context
    # --- OpenAI ---
    ("gpt-4o", 128_000),
    ("gpt-4.1", 1_047_576),
    ("gpt-4-turbo", 128_000),
    ("gpt-4-32k", 32_768),
    ("gpt-4", 8_192),
    ("gpt-3.5-turbo-16k", 16_385),
    ("gpt-3.5-turbo", 16_385),
    ("o1-mini", 128_000),
    ("o1", 200_000),
    ("o3-mini", 200_000),
    ("o3", 200_000),
    # --- Anthropic Claude ---
    ("claude-3-5", 200_000),
    ("claude-3.5", 200_000),
    ("claude-3-7", 200_000),
    ("claude-3", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-opus-4", 200_000),
    ("claude-2.1", 200_000),
    ("claude-2", 100_000),
    # --- Meta Llama ---
    ("llama-3.3", 131_072),
    ("llama-3.2", 131_072),
    ("llama-3.1", 131_072),
    ("llama3.3", 131_072),
    ("llama3.2", 131_072),
    ("llama3.1", 131_072),
    ("llama-3-", 8_192),
    ("llama3-", 8_192),
    ("llama-3", 8_192),
    ("codellama", 16_384),
    ("llama-2", 4_096),
    ("llama2", 4_096),
    # --- DeepSeek (before Qwen so distills resolve here) ---
    ("deepseek-v3", 163_840),
    ("deepseek-v2.5", 131_072),
    ("deepseek-v2", 131_072),
    ("deepseek-r1", 131_072),
    ("deepseek-coder-v2", 131_072),
    ("deepseek-coder", 16_384),
    ("deepseek", 131_072),
    # --- Moonshot Kimi (site-served via rikyu; public source => no endpoint
    # max_model_len, so this table is the only resolver) ---
    ("kimi-k3", 1_048_576),  # Kimi K3 — 1M context (operator-asserted)
    ("kimi-k2", 131_072),  # Kimi K2.x family — 128k context
    ("kimi", 131_072),
    # --- Qwen ---
    ("qwen3", 131_072),
    ("qwen2.5", 32_768),
    ("qwen2", 32_768),
    ("qwq", 131_072),
    ("qwen", 32_768),
    # --- Mistral / Mixtral ---
    ("mixtral", 32_768),
    ("mistral-large", 131_072),
    ("mistral-nemo", 131_072),
    ("mistral-small", 32_768),
    ("mistral-7b-instruct-v0.1", 8_192),
    ("mistral-7b-instruct-v0.2", 32_768),
    ("mistral-7b-instruct-v0.3", 32_768),
    ("mistral-7b", 32_768),
    ("mistral", 32_768),
    # --- GLM / ChatGLM ---
    ("glm-5.2", 1_048_576),  # GLM-5.2 (BF16 + FP8) — 1M context
    ("glm-5", 1_048_576),  # GLM-5 / GLM-5.1 — 1M context
    ("glm-4", 131_072),
    ("glm4", 131_072),
    ("chatglm3", 8_192),
    ("chatglm", 8_192),
    # --- Google Gemma / Gemini ---
    ("gemini-1.5", 1_048_576),
    ("gemini", 1_048_576),
    ("gemma-3", 131_072),
    ("gemma-2", 8_192),
    ("gemma", 8_192),
    # --- Microsoft Phi ---
    ("phi-3.5", 131_072),
    ("phi-3-medium-128k", 131_072),
    ("phi-3-mini-128k", 131_072),
    ("phi-3-small-128k", 131_072),
    ("phi-4", 16_384),
    ("phi-3", 4_096),
    ("phi-2", 2_048),
    # --- gpt-oss (OpenAI open-weight) ---
    ("gpt-oss", 131_072),
    # --- Encoder/seq2seq (small, fixed) ---
    ("flan-t5", 512),
    ("flan-ul2", 2_048),
    ("t5-", 512),
    ("bart-large", 1_024),
    ("bart-base", 1_024),
    ("bart", 1_024),
    # --- Embedding models (informational) ---
    ("bge-m3", 8_192),
    ("e5-mistral", 32_768),
    ("nomic-embed", 8_192),
]


def known_context_length(*names: Optional[str]) -> Optional[int]:
    """Return a best-effort context length for a model by name / HF id.

    Each argument (model name, HuggingFace id, etc.) is matched
    case-insensitively against the known-context patterns; the first matching
    pattern across all provided identifiers wins.

    Returns ``None`` when no pattern matches.
    """
    haystacks = [n.lower() for n in names if n]
    if not haystacks:
        return None
    for pattern, ctx in _KNOWN_CONTEXT_PATTERNS:
        if any(pattern in haystack for haystack in haystacks):
            return ctx
    return None


# ADR-087: default output limit for reasoning-model families whose servers
# otherwise apply a small default (rikyu: 32,768) that one long reasoning turn
# can spend with no answer. Matched like _KNOWN_CONTEXT_PATTERNS; clamped by the
# caller to the context window left after the prompt.
REASONING_OUTPUT_TOKENS = 131_072
_REASONING_FAMILIES = (
    "glm-5",
    "glm-4.5",
    "glm-4.6",
    "kimi-k2",
    "kimi-k3",
    "deepseek-r1",
    "deepseek-v3.1",
    "deepseek-v3.2",
    "qwen3",
    "qwq",
    "gpt-oss",
)


def default_max_output_tokens(*names: Optional[str]) -> Optional[int]:
    """Output limit to send for known reasoning families; ``None`` = server default."""
    haystacks = [n.lower() for n in names if n]
    if any(fam in h for h in haystacks for fam in _REASONING_FAMILIES):
        return REASONING_OUTPUT_TOKENS
    return None


def resolve_max_context(model) -> Optional[int]:
    """Resolve a model's maximum context length for display.

    Precedence:

    1. ``capabilities.max_position_embeddings`` (authoritative).
    2. The larger of the curated known value and a non-default serving length
       (a model's true max is never smaller than how it is served).
    3. The serving length even if it is the 4096 default (last resort).

    Returns ``None`` when nothing is known.
    """
    caps = getattr(model, "capabilities", None)
    declared = getattr(caps, "max_position_embeddings", None) if caps else None
    if declared:
        return declared

    profile = getattr(model, "serving_profile", None)
    serving = getattr(profile, "max_model_len", None) if profile else None

    # Treat the hardcoded serving default as "unset" so a real known value wins.
    effective_serving = serving if serving and serving != _SERVING_DEFAULT_MAX_LEN else None

    fallback = known_context_length(
        getattr(model, "name", None), getattr(model, "huggingface_id", None)
    )

    candidates = [c for c in (effective_serving, fallback) if c]
    if candidates:
        return max(candidates)
    return serving


def format_context_length(value: Optional[int]) -> str:
    """Format a context length as ``'64k'`` / ``'512'`` / ``'-'``."""
    if not value:
        return "-"
    return f"{value // 1024}k" if value >= 1024 else str(value)
