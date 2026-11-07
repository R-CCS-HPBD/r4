"""``r4.redaction``: one redactor for every surface, plus the argv-only rule.

c2-transport (2026-10-21). ``cli._redact_argv_secrets`` (c2-cli lane) kept its
own pattern list, whose Slack rule ``xox[abpr]_`` never matched a real Slack
token (``xoxb-…``, with a dash); the companion's ``redact_tokens`` missed
``xoxe-1-`` refresh tokens, ``xapp-1-`` app tokens and webhook URLs.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from r4.redaction import ARGV_REDACTED, redact_argv, redact_tokens

# Fakes built from parts so the repo's own secret scanner stays quiet.
_JWT = ".".join(("eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJhbGljZSJ9", "SIGNATUREsecretPART"))
_SLACK_BOT = "xox" + "b-2415890412-5312890412345-AbCdEfGhIjKlMnOpQrStUvWx"


@pytest.mark.parametrize(
    "secret",
    [
        _JWT,
        "sk-live0123456789abcdef",
        "rk-live0123456789",
        "Zx9" + "q" * 40,
        _SLACK_BOT,
        "hf_" + "F4ke" * 6,
    ],
)
def test_argv_echo_never_carries_a_token(secret):
    out = redact_argv(f"unrecognized arguments: {secret}")
    assert secret not in out
    assert out == f"unrecognized arguments: {ARGV_REDACTED}"


def test_argv_keeps_hex_ids_and_paths():
    text = "invalid choice: '2c0624cc-c807-4054-8921-a8a49624ef89' /data/" + "a" * 40
    assert redact_argv(text) == text


def test_argv_keeps_the_option_argparse_names():
    for text in ("argument --api-key: expected one argument", "argument -t/--token: invalid int"):
        assert redact_argv(text) == text


def test_argv_redacts_a_short_credential_named_value():
    assert redact_argv("unrecognized arguments: --api-key=hunter2") == (
        f"unrecognized arguments: --api-key={ARGV_REDACTED}"
    )


def test_argv_is_idempotent():
    once = redact_argv(f"unrecognized arguments: {_JWT} {_SLACK_BOT}")
    assert redact_argv(once) == once


def test_redact_tokens_is_the_companion_redactor():
    from r4.companion import tool_summaries

    text = f"token={_JWT} Bearer abc {_SLACK_BOT}"
    assert redact_tokens(text) == tool_summaries.redact_tokens(text)


def test_importing_the_module_stays_cheap():
    code = "import sys, r4.redaction; print('r4.companion' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
