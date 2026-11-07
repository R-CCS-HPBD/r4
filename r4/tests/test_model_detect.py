"""Tests for r4.model_detect capability auto-detection."""

import json
import os

import pytest

from r4.model_detect import detect_capabilities


@pytest.fixture
def model_dir(tmp_path):
    """Create a temporary model directory."""
    return tmp_path / "test-model"


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


class TestDetectCapabilities:
    def test_nonexistent_dir(self, tmp_path):
        result = detect_capabilities(str(tmp_path / "nope"))
        assert result.model_family is None
        assert result.supports_tool_calling is None

    def test_empty_dir(self, model_dir):
        model_dir.mkdir()
        result = detect_capabilities(str(model_dir))
        assert result.model_family is None
        assert result.supports_vision is None
        assert result.supports_tool_calling is False

    def test_qwen_family_detection(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "qwen2",
                "architectures": ["Qwen2ForCausalLM"],
                "max_position_embeddings": 32768,
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.model_family == "qwen"
        assert result.max_position_embeddings == 32768
        assert result.supports_vision is False

    def test_llama_family_detection(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "llama",
                "architectures": ["LlamaForCausalLM"],
                "max_position_embeddings": 8192,
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.model_family == "llama"
        assert result.max_position_embeddings == 8192

    def test_mistral_family_detection(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "mixtral",
                "architectures": ["MixtralForCausalLM"],
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.model_family == "mistral"

    def test_vision_detection_from_architecture(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "qwen2_vl",
                "architectures": ["Qwen2VLForConditionalGeneration"],
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_vision is True

    def test_vision_detection_from_config_keys(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "custom",
                "architectures": ["CustomModel"],
                "vision_config": {"hidden_size": 768},
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_vision is True

    def test_vision_detection_from_media_placeholder(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "kimi_k25",
                "architectures": ["KimiK25ForConditionalGeneration"],
                "media_placeholder_token_id": 3605,
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_vision is True

    def test_vision_detection_from_kimi_k27_architecture(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "kimi_k25",
                "architectures": ["KimiK25ForConditionalGeneration"],
                "vision_config": {"mm_hidden_size": 1152},
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_vision is True

    def test_tool_calling_detection_string_template(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "qwen2",
                "architectures": ["Qwen2ForCausalLM"],
            },
        )
        _write_json(
            str(model_dir / "tokenizer_config.json"),
            {
                "chat_template": "{% if tool_calls %}use tools{% endif %}",
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_tool_calling is True
        assert result.supports_structured_output is True

    def test_tool_calling_detection_list_template(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "qwen2",
                "architectures": ["Qwen2ForCausalLM"],
            },
        )
        _write_json(
            str(model_dir / "tokenizer_config.json"),
            {
                "chat_template": [
                    {"name": "default", "template": "Hello"},
                    {"name": "tool_use", "template": "{% if tool_calls %}yes{% endif %}"},
                ],
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_tool_calling is True

    def test_no_tool_calling(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "llama",
                "architectures": ["LlamaForCausalLM"],
            },
        )
        _write_json(
            str(model_dir / "tokenizer_config.json"),
            {
                "chat_template": "{% for msg in messages %}{{ msg.content }}{% endfor %}",
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.supports_tool_calling is False
        assert result.supports_structured_output is None

    def test_max_output_tokens(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "qwen2",
                "architectures": ["Qwen2ForCausalLM"],
            },
        )
        _write_json(
            str(model_dir / "generation_config.json"),
            {
                "max_new_tokens": 2048,
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.max_output_tokens == 2048

    def test_full_detection(self, model_dir):
        """End-to-end test with all config files present."""
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "qwen2",
                "architectures": ["Qwen2ForCausalLM"],
                "max_position_embeddings": 131072,
            },
        )
        _write_json(
            str(model_dir / "tokenizer_config.json"),
            {
                "chat_template": "{% if '<tool_call>' in message %}...{% endif %}",
            },
        )
        _write_json(
            str(model_dir / "generation_config.json"),
            {
                "max_new_tokens": 8192,
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.model_family == "qwen"
        assert result.supports_tool_calling is True
        assert result.supports_vision is False
        assert result.supports_structured_output is True
        assert result.max_position_embeddings == 131072
        assert result.max_output_tokens == 8192

    def test_deepseek_family(self, model_dir):
        model_dir.mkdir()
        _write_json(
            str(model_dir / "config.json"),
            {
                "model_type": "deepseek_v3",
                "architectures": ["DeepseekV3ForCausalLM"],
            },
        )
        result = detect_capabilities(str(model_dir))
        assert result.model_family == "deepseek"

    def test_invalid_json(self, model_dir):
        """Malformed JSON should not crash."""
        model_dir.mkdir()
        with open(model_dir / "config.json", "w") as f:
            f.write("{invalid json")
        result = detect_capabilities(str(model_dir))
        assert result.model_family is None
