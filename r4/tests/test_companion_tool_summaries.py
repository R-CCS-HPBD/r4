"""tool_summaries sanitization tests (wave T3-ui S2)."""

from __future__ import annotations

import pytest

from r4.companion.tool_summaries import (
    args_summary_for,
    redact_tokens,
    result_preview_for,
)


class TestCategories:
    def test_read_tools_yield_path_only(self):
        summary = args_summary_for("lab_file_read", {"s3_key": "data/catalog/x.csv", "secret": "x"})
        assert summary == "data/catalog/x.csv"
        assert "secret" not in summary

    def test_write_tools_yield_path(self):
        summary = args_summary_for("file_write", {"path": "src/main.py", "content": "big"})
        assert summary == "src/main.py"
        assert "content" not in summary and "big" not in summary

    def test_search_suffix_yields_query(self):
        summary = args_summary_for("lab_catalog_search", {"query": "FWI convergence"})
        assert summary == "FWI convergence"

    def test_unknown_tool_gets_default_empty(self):
        assert args_summary_for("mcp_custom_anything", {"query": "leak?"}) == ""
        assert args_summary_for("knowledge_graph_decide", {"approved": True}) == ""

    def test_exec_first_line_only(self):
        summary = args_summary_for("shell_exec", {"command": "make -j4 && ./deploy.sh && rm -rf /"})
        assert summary == "make -j4 && ./deploy.sh && rm -rf /"
        summary2 = args_summary_for("shell_exec", {"command": "echo one\necho two\necho three"})
        assert summary2 == "echo one"


class TestTruncation:
    def test_long_path_truncated_with_ellipsis(self):
        long_path = "d/" * 60 + "file.json"
        summary = args_summary_for("file_read", {"path": long_path})
        assert len(summary) <= 96
        assert summary.endswith("…")

    def test_long_shell_summary_truncated(self):
        long_cmd = "python " + "x " * 100
        summary = args_summary_for("shell_exec", {"command": long_cmd})
        assert len(summary) <= 120
        assert summary.endswith("…")


class TestRedaction:
    def test_sk_token_redacted(self):
        assert "⟨redacted⟩" in args_summary_for(
            "lab_file_read", {"s3_key": "sk-AbCdEf123456/projects/x"}
        )

    def test_bearer_redacted(self):
        out = redact_tokens("call --head 'Bearer eyJtoken.longtoken.jwtother'")
        assert "Bearer" not in out
        assert "sk-" not in out

    def test_jwtish_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiI.eyJzdWIiOiJ1c2VyMTIzNDU2Nzlh.SigabcDEFGHI"
        assert redact_tokens(f"tok={jwt}") == "tok=⟨redacted⟩"

    def test_api_key_assignment_redacted(self):
        out = redact_tokens("export umrs_api_key=r4secretkey123")
        assert "r4secretkey123" not in out


class TestResultPreview:
    def test_preview_capped_and_sanitized(self):
        raw = "ok " + "x" * 500 + " sk-XXXXXXXXXX"
        preview = result_preview_for("shell_exec", raw)
        assert len(preview) <= 200
        assert preview.endswith("…")


class TestRedactionShapes:
    """Cycle-2 security lane (2026-10-21): the last-pass redactor let these
    shapes through to tool cards, result previews and transcripts. Repro:
    $C2/security/redact/fuzz_py_redact.py (12 leaks on the base)."""

    PW = "Hunter2Passw0rd"
    S3 = "wJalrXUtnFEMIK7MDENGbPxRfiCYFAKEKEY"
    # Fakes built from parts so the repo's own secret scanner stays quiet.
    JWT = ".".join(("eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJ0ZXN0In0", "FAKESIGFAKESIGFAKESIG"))
    AKID = "AKIA" + "FAKEFAKEFAKE1234"

    def test_json_credential_values(self):
        out = redact_tokens(f'{{"api_key": "{self.PW}", "access_token": "x{self.PW}"}}')
        assert self.PW not in out
        assert '"api_key": ⟨redacted⟩' in out

    def test_s3cfg_and_env_secrets(self):
        text = f"[default]\naccess_key = {self.AKID}\nsecret_key = {self.S3}\n"
        out = redact_tokens(text + f"AWS_SECRET_ACCESS_KEY={self.S3}")
        assert self.S3 not in out and self.AKID not in out
        assert "secret_key = ⟨redacted⟩" in out

    def test_url_userinfo_and_basic_auth(self):
        out = redact_tokens(f"https://alice:{self.PW}@gw/x Authorization: Basic YWxpY2U6aHVudGVy")
        assert out == "https://alice:⟨redacted⟩@gw/x Authorization: ⟨redacted⟩"

    def test_provider_tokens_and_private_keys(self):
        hf = "hf_FAKEfakeFAKEfakeFAKEfake123"
        pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAFAKE\n-----END OPENSSH PRIVATE KEY-----"
        assert redact_tokens(f"using {hf}\n{pem}\nok") == "using ⟨redacted⟩\n⟨redacted⟩\nok"

    @pytest.mark.parametrize(
        "token",
        [
            "xox" + "b-2415890412-5312890412345-AbCdEfGhIjKlMnOpQrStUvWx",
            "xox" + "p-2415890412-2415890413-5312890412345-0123456789abcdef",
            "xox" + "e-1-My0xLTI0MTU4OTA0MTItNTMxMjg5MDQxMjM0NS1BYkNkRWZH",
            "xox" + "e.xoxp-1-Mi0yLTI0MTU4OTA0MTItMjQxNTg5MDQxMw",
            "xap" + "p-1-A0123456789-5312890412345-0123456789abcdef",
        ],
    )
    def test_slack_tokens(self, token):
        """c2-transport: `xoxe-1-` refresh and `xapp-1-` app-level tokens leaked."""
        assert token[-12:] not in redact_tokens(f"slack said {token} ok")

    def test_slack_webhook_url(self):
        hook = "https://hooks.slack.com/services/T0123ABCD/B0123ABCD/" + "AbCdEfGhIjKl" * 2
        out = redact_tokens(f"curl -X POST {hook}")
        assert out == "curl -X POST https://hooks.slack.com/services/⟨redacted⟩"

    def test_jwt_wrapped_over_lines(self):
        wrapped = f"{self.JWT[:30]}\n{self.JWT[30:]}\nnext line"
        assert redact_tokens(wrapped) == "⟨redacted⟩\nnext line"

    def test_shell_card_summary(self):
        cmd = f'curl -s https://alice:{self.PW}@gw/x -d \'{{"password": "{self.PW}"}}\''
        assert self.PW not in args_summary_for("shell_exec", {"command": cmd})

    def test_ordinary_text_untouched(self):
        text = "max_tokens: 512, s3_key: data/x.csv, https://example.com/a@b, git@github.com:x/y"
        assert redact_tokens(text) == text

    def test_linear_on_hostile_input(self):
        import time

        start = time.monotonic()
        redact_tokens("a-" * 8192 + "a." * 8192)
        assert time.monotonic() - start < 0.5
