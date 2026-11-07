"""Unit tests for shared terminal branding helpers."""

from __future__ import annotations

from r4.ui_brand import brand_banner_text, brand_html


def test_chat_banner_text_contains_wordmark_and_copy():
    text = brand_banner_text("chat")
    assert "R4 Chat" in text
    assert "Ask R4 about models, data, and workflows." in text
    assert "████" in text


def test_companion_banner_text_contains_wordmark_and_copy():
    text = brand_banner_text("companion")
    assert "R4 Companion" in text
    assert "Research with R4. Type /help for commands." in text
    assert "████" in text


def test_chat_brand_html_contains_wordmark_and_logo():
    text = brand_html("chat")
    assert "R4 Chat" in text
    assert "Ask R4 about models, data, and workflows." in text
    assert "&lt;" not in text
    assert "████" in text
