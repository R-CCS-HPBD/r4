"""Tests for r4.model_context known-context fallback lookup."""

from dataclasses import dataclass
from typing import Optional

import pytest
from r4_contracts import models as contract_models

from r4 import model_context
from r4.model_context import (
    REASONING_OUTPUT_TOKENS,
    SIZE_LARGE_MIN_B,
    SIZE_SMALL_MAX_B,
    default_max_output_tokens,
    format_context_length,
    known_context_length,
    model_param_billions,
    model_size_class,
    resolve_max_context,
)


@dataclass
class _Caps:
    max_position_embeddings: Optional[int] = None


@dataclass
class _Serving:
    max_model_len: Optional[int] = None


@dataclass
class _Model:
    name: Optional[str] = None
    huggingface_id: Optional[str] = None
    capabilities: Optional[_Caps] = None
    serving_profile: Optional[_Serving] = None


class TestKnownContextLength:
    def test_no_names(self):
        assert known_context_length() is None
        assert known_context_length(None) is None
        assert known_context_length(None, "") is None

    def test_unknown_model(self):
        assert known_context_length("totally-made-up-model-xyz") is None

    def test_matches_by_name(self):
        assert known_context_length("gpt-4o") == 128_000
        assert known_context_length("Qwen2.5-7B-Instruct") == 32_768
        assert known_context_length("Meta-Llama-3.1-8B-Instruct") == 131_072

    def test_matches_by_hf_id(self):
        # name unknown, but HF id matches
        assert known_context_length("my-custom-name", "facebook/bart-base") == 1_024
        assert known_context_length("flan", "google/flan-t5-xl") == 512

    def test_case_insensitive(self):
        assert known_context_length("MISTRAL-7B-INSTRUCT-V0.3") == 32_768

    def test_first_match_wins_specific_before_general(self):
        # llama-3.1 must match before the generic llama-3 fallback
        assert known_context_length("llama-3.1-70b") == 131_072
        # plain llama-3 (8k) should not be promoted to 128k
        assert known_context_length("llama-3-8b") == 8_192

    def test_seq2seq_small_contexts(self):
        assert known_context_length("flan-t5-xl") == 512
        assert known_context_length("bart-large-cnn") == 1_024

    def test_site_models(self):
        assert known_context_length("Bonsai-8B") == 65_536
        assert known_context_length("DeepSeek-V4-Pro") == 1_048_576
        assert known_context_length("deepseek-v4-pro-128k") == 1_048_576

    def test_kimi_family(self):
        # kimi-k3 is served on the rikyu public source (no endpoint
        # max_model_len) — the table is the only context resolver.
        assert known_context_length("kimi-k3") == 1_048_576
        assert known_context_length("kimi-k3@rikyu") == 1_048_576
        assert known_context_length("Kimi-K2.6-Instruct") == 131_072
        assert known_context_length("kimi-k2.6@rikyu") == 131_072

    def test_deepseek_before_qwen_for_distills(self):
        # A DeepSeek distill carrying "Qwen" in its name must resolve to the
        # DeepSeek context, not the generic Qwen 32k.
        assert known_context_length("DeepSeek-R1-Distill-Qwen-32B") == 131_072

    def test_glm5_family_known(self):
        # GLM-5.x family (GLM-5, GLM-5.1, GLM-5.2, GLM-5.2-FP8) has 1M context.
        assert known_context_length("GLM-5.1") == 1_048_576
        assert known_context_length("GLM-5.2") == 1_048_576
        assert known_context_length("GLM-5.2-FP8") == 1_048_576
        # GLM-4 stays known.
        assert known_context_length("glm-4-9b-chat") == 131_072


class TestFormatContextLength:
    def test_none_and_zero(self):
        assert format_context_length(None) == "-"
        assert format_context_length(0) == "-"

    def test_small_values(self):
        assert format_context_length(128) == "128"
        assert format_context_length(512) == "512"

    def test_k_values(self):
        assert format_context_length(65_536) == "64k"
        assert format_context_length(131_072) == "128k"
        assert format_context_length(1_048_576) == "1024k"


class TestResolveMaxContext:
    def test_declared_wins(self):
        m = _Model(
            name="Qwen2.5-7B",
            capabilities=_Caps(max_position_embeddings=8_192),
            serving_profile=_Serving(max_model_len=131_072),
        )
        assert resolve_max_context(m) == 8_192

    def test_bonsai_from_fallback(self):
        m = _Model(name="Bonsai-8B", capabilities=_Caps(), serving_profile=_Serving())
        assert resolve_max_context(m) == 65_536

    def test_deepseek_v4_fallback_beats_serving(self):
        # Served at 128k but the model's true max is 1M.
        m = _Model(
            name="DeepSeek-V4-Pro",
            huggingface_id="deepseek-ai/DeepSeek-V4-Pro",
            capabilities=_Caps(),
            serving_profile=_Serving(max_model_len=131_072),
        )
        assert resolve_max_context(m) == 1_048_576

    def test_default_serving_treated_as_unset(self):
        # serving 4096 is the placeholder default; the known value should win.
        m = _Model(
            name="Qwen2.5-72B-Instruct",
            capabilities=_Caps(),
            serving_profile=_Serving(max_model_len=4096),
        )
        assert resolve_max_context(m) == 32_768

    def test_distill_resolves_to_deepseek(self):
        m = _Model(
            name="DeepSeek-R1-Distill-Qwen-32B",
            capabilities=_Caps(),
            serving_profile=_Serving(max_model_len=4096),
        )
        assert resolve_max_context(m) == 131_072

    def test_non_default_serving_wins_when_no_fallback(self):
        m = _Model(
            name="ai4s-smoke",
            capabilities=_Caps(),
            serving_profile=_Serving(max_model_len=128),
        )
        assert resolve_max_context(m) == 128

    def test_serving_known_no_fallback_override(self):
        # gemma-3 known is 131072; served at 131072 → stays 128k.
        m = _Model(
            name="gemma-4-31B-it",
            capabilities=_Caps(),
            serving_profile=_Serving(max_model_len=131_072),
        )
        assert resolve_max_context(m) == 131_072

    def test_nothing_known_returns_none(self):
        m = _Model(name="mystery-model", capabilities=_Caps(), serving_profile=_Serving())
        assert resolve_max_context(m) is None

    def test_default_serving_only_returned_as_last_resort(self):
        # Unknown model served at the 4096 default and no known value → show 4096.
        m = _Model(
            name="mystery-model",
            capabilities=_Caps(),
            serving_profile=_Serving(max_model_len=4096),
        )
        assert resolve_max_context(m) == 4096


class TestSizeRuleReExport:
    """ADR-093 D-14 (C17): the size rule moved to r4_contracts.models.

    ``r4.model_context`` re-exports the same objects, so every existing
    import keeps working and R4 and UMRS can never drift apart.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "SIZE_SMALL_MAX_B",
            "SIZE_LARGE_MIN_B",
            "model_param_billions",
            "model_size_class",
            "_PARAM_COUNT_RE",
            "_LARGE_UNSIZED_FAMILIES",
        ],
    )
    def test_the_same_object_as_the_contract(self, name):
        assert getattr(model_context, name) is getattr(contract_models, name)

    @pytest.mark.parametrize(
        ("name", "size"),
        [
            ("llama3.2:3b", "small"),
            ("smollm2-360m", "small"),
            ("llama-3.1-8b-instruct", "medium"),
            ("qwen2.5-7b-instruct-1m", "medium"),
            ("qwen3.8-27b", "large"),
            # Lead decision 2026-10-10: one size rule, the EFFECTIVE size
            # (sqrt(30 x 3) ~ 9.5B), so the C17 answer "large" is re-pinned.
            ("Qwen3-30B-A3B", "medium"),
            ("mixtral-8x7b", "large"),
            ("kimi-k3", "large"),
            ("glm-5.3", "large"),
            ("glm-5.3-fp8", "large"),
            ("claude-sonnet-4", "large"),
            ("test-model", "unknown"),
            (None, "unknown"),
        ],
    )
    def test_the_size_class_is_unchanged(self, name, size):
        assert model_size_class(name) == size

    def test_the_parameter_count_is_unchanged(self):
        assert model_param_billions("Qwen3-30B-A3B") == 30.0
        assert model_param_billions("mixtral-8x7b") == 56.0
        assert model_param_billions("smollm2-360m") == pytest.approx(0.36)
        assert model_param_billions("kimi-k3") is None
        assert (SIZE_SMALL_MAX_B, SIZE_LARGE_MIN_B) == (4.0, 14.0)


class TestRikyuReaders:
    """The rikyu readers (kimi-k3, glm-5.3) keep their 1M window and the
    ADR-087 output default the large retrieval profile defers to."""

    @pytest.mark.parametrize("name", ["kimi-k3", "kimi-k3@rikyu", "glm-5.3", "GLM-5.3-FP8"])
    def test_window_and_output_default(self, name):
        assert known_context_length(name) == 1_048_576
        assert default_max_output_tokens(name) == REASONING_OUTPUT_TOKENS
