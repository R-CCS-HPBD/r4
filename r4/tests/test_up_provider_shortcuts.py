"""`r4 up --public` provider shortcuts name public providers only.

Site endpoints (an institute's own LiteLLM or Ollama) are passed as a full
URL; a shortcut would point every user at a host most of them cannot reach.
"""

from __future__ import annotations

from unittest.mock import patch

from r4._cli import up


def test_shortcuts_point_at_public_https_hosts():
    for name, url in up._PROVIDER_SHORTCUTS.items():
        assert url.startswith("https://"), (name, url)
    assert "rccs" not in up._PROVIDER_SHORTCUTS


def test_site_name_is_not_expanded():
    assert up._provider_url("rccs") == "rccs"
    assert up._provider_url("openai") == "https://api.openai.com"


def test_full_url_passes_through():
    url = "https://llm.example.ac.jp/v1"
    assert up._provider_url(url) == url


def test_prompt_lists_only_public_shortcuts(capsys):
    with patch.object(up, "_prompt_required_input", return_value="groq"):
        assert up._prompt_public_provider_url() == "https://api.groq.com/openai"
    out = capsys.readouterr().out
    assert "rccs" not in out
    assert "http://" not in out
