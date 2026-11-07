"""Regression test: JWT/JWT-profile name must never seed invisible unicode
(U+2028 LINE SEPARATOR from Slack-wrapped tokens) into connection filenames.

Bug: `r4 auth login --api-key <jwt-from-slack>` produced
`'ai4s'$'\\342\\200\\250''.json` / `.token` in ~/.config/umrs/connections.
"""

from __future__ import annotations

from r4._cli.login import _sanitize_profile_name


class TestSanitizeProfileName:
    def test_clean_name_unchanged(self):
        assert _sanitize_profile_name("ai4s") == "ai4s"

    def test_none_passthrough(self):
        assert _sanitize_profile_name(None) is None

    def test_empty_passthrough(self):
        assert _sanitize_profile_name("") == ""

    def test_unicode_line_separator_stripped(self):
        assert _sanitize_profile_name("ai4s ") == "ai4s"

    def test_mixed_separators_and_spaces(self):
        assert _sanitize_profile_name(" my-profile  x ") == "my-profilex"

    def test_only_garbage_falls_back_to_default(self):
        assert _sanitize_profile_name("  ") == "default"

    def test_dotted_and_dashed_names_kept(self):
        assert _sanitize_profile_name("rccs-ds4.m10") == "rccs-ds4.m10"
