"""ADR-093 D-14 (C17): one size rule and the retrieval profile table.

The stand-alone reader (llama3.2:3b) reads with the small profile; the remote
deployment's rikyu readers (kimi-k3, glm-5.3: frontier MoE models with
1M-token windows) read with the large one.

Lead decision for the maintainer (2026-10-10), which re-pins the C17 answers
below: ONE size rule everywhere, the EFFECTIVE size (sqrt(total x active) for
a mixture-of-experts model), so ``qwen3-30b-a3b`` and ``gpt-oss-20b`` are
medium; and a model of unknown scale reads with MEDIUM everywhere (C22), not
the small profile C17 chose.
"""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

from r4_contracts.models import (
    DEFAULT_PROFILE,
    KNOWN_MODEL_FAMILIES,
    PROFILE_OVERRIDES,
    RETRIEVAL_PROFILES,
    SIZE_LARGE_MIN_B,
    SIZE_SMALL_MAX_B,
    UNKNOWN_SCALE_PROFILE,
    NameScale,
    RetrievalProfile,
    active_param_billions,
    effective_params_b,
    known_model_family,
    model_param_billions,
    model_size_class,
    profile_for,
    profile_for_class,
    scale_from_name,
    size_class_for,
)

MODELS_SRC = Path(__file__).parents[1] / "src" / "r4_contracts" / "models.py"


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("kimi-k3", "large"),
        ("kimi-k3@rikyu", "large"),
        ("glm-5.3", "large"),
        ("GLM-5.3", "large"),
        ("glm-5.3-fp8", "large"),
        ("GLM-5.3-FP8@rikyu", "large"),
        ("llama3.2:3b", "small"),
        ("qwen3.8-27b", "large"),
        # Lead decision 2026-10-10 (the effective size): C17 pinned these large.
        ("qwen3-30b-a3b", "medium"),
        ("gpt-oss-20b", "medium"),
        ("gpt-oss-120b", "large"),
        ("llama-3.1-8b-instruct", "medium"),
        ("test-model", "unknown"),
        ("gemma-3n-e4b", "unknown"),
    ],
)
def test_the_size_rule(name, size):
    assert model_size_class(name) == size


def test_the_size_thresholds():
    assert (SIZE_SMALL_MAX_B, SIZE_LARGE_MIN_B) == (4.0, 14.0)


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("qwen3-30b-a3b", 30.0),  # the active-expert tag is not the size
        ("Qwen3-30B-A3B", 30.0),
        ("llama3.2:3b", 3.0),
        ("glm-5.3-fp8", None),  # a version and a precision tag state no size
        ("kimi-k3", None),
    ],
)
def test_the_parameter_count_a_name_states(name, params):
    assert model_param_billions(name) == params


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("kimi-k3", "large"),
        ("kimi-k3@rikyu", "large"),
        ("glm-5.3", "large"),
        ("glm-5.3-fp8", "large"),
        ("llama3.2:3b", "small"),
        ("qwen3.8-27b", "large"),
        # Lead decision 2026-10-10: the effective size (C17 read these large)
        # and unknown -> medium (C17 read these small).
        ("qwen3-30b-a3b", "medium"),
        ("gpt-oss-20b", "medium"),
        ("llama-3.1-8b-instruct", "medium"),
        ("test-model", "medium"),
        ("gemma-3n-e4b", "medium"),
        ("", "medium"),
        (None, "medium"),
    ],
)
def test_the_reading_model_picks_the_profile(model, expected):
    name, profile = profile_for(model)
    assert name == expected
    assert profile is RETRIEVAL_PROFILES[expected]


def test_an_unknown_model_reads_with_the_medium_profile():
    """Lead decision 2026-10-10: unknown -> medium everywhere (C17 said small)."""
    assert DEFAULT_PROFILE == UNKNOWN_SCALE_PROFILE == "medium"
    assert profile_for("totally-made-up-model-xyz") == ("medium", RETRIEVAL_PROFILES["medium"])


def test_the_d14_table():
    assert {
        # every profile reads the site's lexical default (ADR-093 Phase 9)
        "small": RetrievalProfile(4, 2, 50, "gate", 400, 3, 1_200, "off", "gate"),
        "medium": RetrievalProfile(6, 3, 50, "on", 600, 4, 2_000, "off", "gate"),
        "large": RetrievalProfile(6, 3, 50, "on", None, 4, 2_000, "multi", "gate"),
    } == RETRIEVAL_PROFILES


def test_the_field_order_is_the_contract():
    assert [field.name for field in dataclasses.fields(RetrievalProfile)] == [
        "k_max",
        "partial_k",
        "pool",
        "merge",
        "answer_max_tokens",
        "pack_passages",
        "pack_tokens",
        "expand",
        "lexical",
    ]


@pytest.mark.parametrize("name", ["small", "medium", "large"])
def test_each_profile_is_self_consistent(name):
    profile = RETRIEVAL_PROFILES[name]
    assert 0 < profile.partial_k < profile.k_max <= profile.pool
    assert profile.pack_passages <= profile.k_max


def test_only_the_large_profile_expands_and_lifts_the_answer_limit():
    expanding = {name for name, p in RETRIEVAL_PROFILES.items() if p.expand != "off"}
    unlimited = {name for name, p in RETRIEVAL_PROFILES.items() if p.answer_max_tokens is None}
    assert expanding == unlimited == {"large"}


def test_profiles_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        RETRIEVAL_PROFILES["small"].k_max = 12  # type: ignore[misc]


@pytest.mark.parametrize(
    ("model", "override", "expected"),
    [
        ("kimi-k3", "small", "small"),
        ("llama3.2:3b", "large", "large"),
        ("llama3.2:3b", " LARGE ", "large"),
        (None, "medium", "medium"),
        ("kimi-k3", "auto", "large"),
        ("llama3.2:3b", "auto", "small"),
        ("kimi-k3", "", "large"),
        ("kimi-k3", "huge", "large"),  # an unrecognised override falls through
    ],
)
def test_an_override_names_the_profile_outright(model, override, expected):
    assert profile_for(model, override)[0] == expected


def test_the_override_values():
    assert ("auto", *RETRIEVAL_PROFILES) == PROFILE_OVERRIDES


def _imported_roots(tree: ast.AST) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
    return roots


def test_the_module_is_stdlib_only():
    roots = _imported_roots(ast.parse(MODELS_SRC.read_text()))
    assert roots and roots <= set(sys.stdlib_module_names) | {"__future__"}


# --- C22: the shared half of model-scale detection ---------------------------


@pytest.mark.parametrize(
    ("total", "active", "expected"),
    [
        (30.0, 3.0, 9.486832980505138),  # a 30B-A3B reads like ~9.5B
        (1_000.0, 32.0, 178.88543819998318),  # kimi-k2: 1T total, 32B active
        (8.0, None, 8.0),  # dense: the total
        (8.0, 8.0, 8.0),  # an active size that is not below the total is ignored
        (8.0, 0.0, 8.0),
    ],
)
def test_the_effective_size(total, active, expected):
    assert effective_params_b(total, active) == pytest.approx(expected)


@pytest.mark.parametrize(("total", "active"), [(None, 3.0), (0.0, None), (-1.0, None)])
def test_no_total_no_effective_size(total, active):
    assert effective_params_b(total, active) is None


@pytest.mark.parametrize(
    ("params", "size"),
    [(None, "unknown"), (3.2, "small"), (4.0, "small"), (9.49, "medium"), (14.0, "medium")]
    + [(14.1, "large"), (671.0, "large")],
)
def test_the_class_of_a_size(params, size):
    assert size_class_for(params) == size


@pytest.mark.parametrize(
    ("name", "active"),
    [("qwen3-30b-a3b", 3.0), ("Qwen3-235B-A22B", 22.0), ("qwen3:30b_a3b", 3.0)]
    + [("glm-4.5-air", None), ("llama3.2:3b", None), ("30B-A3B", 3.0)],
)
def test_the_active_size_a_name_states(name, active):
    assert active_param_billions(name) == active


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kimi-k3", NameScale("large", basis="family")),
        ("glm-5.3", NameScale("large", basis="family")),
        ("GLM-5.3-FP8", NameScale("large", basis="family")),
        ("claude-sonnet-4", NameScale("large", basis="family")),
        ("llama3.2:3b", NameScale("small", 3.0, None, 3.0, "name")),
        ("llama-3.1-8b-instruct", NameScale("medium", 8.0, None, 8.0, "name")),
        ("test-model", NameScale("unknown")),
    ],
)
def test_what_a_name_says_about_scale(name, expected):
    assert scale_from_name(name) == expected


@pytest.mark.parametrize(
    ("name", "size", "total", "active", "basis"),
    [
        ("kimi-k2-instruct", "large", 1_000.0, 32.0, "family"),
        ("deepseek-v3.1", "large", 671.0, 37.0, "family"),
        ("DeepSeek-R1", "large", 671.0, 37.0, "family"),
        ("qwen3-235b-a22b", "large", 235.0, 22.0, "name"),  # the name states both
        ("qwen3:235b", "large", 235.0, 22.0, "family"),
        ("Llama-4-Maverick-17B-128E-Instruct", "large", 400.0, 17.0, "family"),
        ("llama4:scout", "large", 109.0, 17.0, "family"),
        ("gpt-oss-120b", "large", 117.0, 5.1, "family"),
        ("gpt-oss:20b", "medium", 21.0, 3.6, "family"),  # sqrt(21 x 3.6) ~ 8.7B
        ("mixtral-8x7b-instruct", "large", 46.7, 12.9, "family"),
        ("glm-4.5-air", "large", 106.0, 12.0, "family"),
        ("Qwen3-30B-A3B", "medium", 30.0, 3.0, "name"),  # the active tag is read
        ("deepseek-r1-distill-qwen-7b", "medium", 7.0, None, "name"),  # a distill's tag wins
        ("deepseek-r1:7b", "medium", 7.0, None, "name"),
    ],
)
def test_published_sizes_and_the_effective_class(name, size, total, active, basis):
    scale = scale_from_name(name)
    assert (scale.size_class, scale.total_b, scale.active_b, scale.basis) == (
        size,
        total,
        active,
        basis,
    )
    assert scale.effective_b == pytest.approx(effective_params_b(total, active))


# Review of ADR-093 (2026-10-10, profiles-graph-2): ollama names a
# mixture-of-experts model by its TOTAL size with no -a<n>b tag, and UMRS
# sizes a model by its registry name alone. qwen3:30b (Qwen3-30B-A3B) was
# classed large, so UMRS read with the large profile and turned the graph
# lane on over a graph an effective ~10B model extracted.
@pytest.mark.parametrize(
    ("name", "total", "active", "size"),
    [
        ("qwen3:30b", 30.5, 3.3, "medium"),
        ("qwen3-coder:30b", 30.5, 3.3, "medium"),
        ("qwen3-vl:30b", 30.0, 3.0, "medium"),
        ("deepseek-v2:16b", 15.7, 2.4, "medium"),
        ("deepseek-coder-v2:16b", 15.7, 2.4, "medium"),
        ("DeepSeek-V2-Lite-Chat", 15.7, 2.4, "medium"),
        ("mixtral", 46.7, 12.9, "large"),  # ollama's untagged mixtral is 8x7B
        ("mixtral:8x7b", 46.7, 12.9, "large"),
        ("mixtral:8x22b", 141.0, 39.0, "large"),
    ],
)
def test_ollama_moe_tags_without_an_active_tag_read_their_published_sizes(
    name, total, active, size
):
    scale = scale_from_name(name)
    assert (scale.total_b, scale.active_b, scale.size_class, scale.basis) == (
        total,
        active,
        size,
        "family",
    )
    assert model_size_class(name) == size and profile_for(name)[0] == size


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("qwen3:32b", NameScale("large", 32.0, None, 32.0, "name")),
        ("qwen3:14b", NameScale("medium", 14.0, None, 14.0, "name")),
        ("qwen3:4b", NameScale("small", 4.0, None, 4.0, "name")),
    ],
)
def test_dense_siblings_of_a_moe_tag_keep_their_stated_size(name, expected):
    assert scale_from_name(name) == expected


def test_specific_family_patterns_precede_general_ones():
    patterns = [p for family in KNOWN_MODEL_FAMILIES for p in family.patterns]
    shadowed = [(early, late) for i, early in enumerate(patterns) for late in patterns[i + 1 :]]
    assert [pair for pair in shadowed if pair[0] in pair[1]] == []


def test_the_family_table_states_sizes_or_none():
    for family in KNOWN_MODEL_FAMILIES:
        assert family.total_b is None or 0 < (family.active_b or family.total_b) <= family.total_b


def test_a_family_matches_across_names():
    family = known_model_family("rikyu-chat", "moonshotai/Kimi-K2-Instruct")
    assert family is not None and family.total_b == 1_000.0
    assert known_model_family("test-model", None) is None


@pytest.mark.parametrize(
    ("size_class", "override", "expected"),
    [
        ("small", None, "small"),
        ("medium", None, "medium"),
        ("large", None, "large"),
        ("unknown", None, "medium"),  # detection tried every source: balanced
        (None, None, "medium"),
        ("LARGE ", None, "large"),
        ("small", "large", "large"),  # an override names the profile outright
        ("unknown", "auto", "medium"),  # auto is no override
        ("large", "huge", "large"),
    ],
)
def test_the_profile_of_a_detected_class(size_class, override, expected):
    assert profile_for_class(size_class, override) == (expected, RETRIEVAL_PROFILES[expected])


def test_a_detected_unknown_and_a_bare_unknown_name_read_the_same():
    """Lead decision 2026-10-10: one answer for unknown, detected or named."""
    assert UNKNOWN_SCALE_PROFILE == "medium"
    assert profile_for_class("unknown")[0] == "medium"
    assert profile_for("test-model")[0] == "medium"


@pytest.mark.parametrize(
    "name",
    ["qwen3-30b-a3b", "Qwen3-30B-A3B", "qwen3:30b-a3b@ollama", "gpt-oss-20b", "gpt-oss:20b"]
    + ["kimi-k3", "glm-5.3", "llama3.2:3b", "mixtral-8x7b", "test-model", None],
)
def test_one_size_rule_the_class_the_scale_and_the_profile_agree(name):
    """ONE rule (lead decision 2026-10-10): the class, the named scale and the
    profile never disagree, so R4 and UMRS cannot size one reader twice."""
    size = scale_from_name(name).size_class
    assert model_size_class(name) == size
    assert profile_for(name) == profile_for_class(size)
