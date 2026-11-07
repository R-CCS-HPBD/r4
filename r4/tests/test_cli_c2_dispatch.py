"""CLI dispatch boundary (production-quality cycle 2, cli lane, 2026-10-21).

Found by driving ``python -m r4`` against fake gateways:

* a lazily-registered command (quota, ops, provenance, run, trace, teamup …)
  that failed at runtime printed the ROOT help on stdout and exited 2 — the
  error itself was never shown, and ``--json`` stdout became help text;
* a root flag in front of a lazy command (``r4 -v quota``,
  ``r4 --source b ops health``) became a parse error, and ``--source`` was
  never applied to lazy commands at all;
* ``r4 -p …`` printed a raw traceback for an ordinary R4Error;
* a token typed as an argument (``r4 auth login <JWT>``) was echoed back by
  the argparse error.
"""

from __future__ import annotations

import logging
import sys

import pytest

import r4._cli.ops as ops_cli
from r4 import cli
from r4.errors import R4Error

# Built at runtime so secret scanners do not flag a fixture as a leaked token.
_JWT = ".".join(["eyJhbGciOiAiSFMyNTYifQ", "eyJzdWIiOiAiYWxpY2UifQ", "SIGNATUREsecretPART"])


def test_ops_is_still_a_lazily_dispatched_command():
    """These tests exercise the lazy path only while ``ops`` is not pre-registered."""
    assert "ops" not in cli._PRE_REGISTER_COMMANDS


def _run_main(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["r4", *argv])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    code = exc_info.value.code
    return code if isinstance(code, int) else 0


@pytest.fixture
def ops_calls(monkeypatch):
    calls: list = []

    def _handle(self, args):
        calls.append(args)
        return 0

    monkeypatch.setattr(ops_cli.OpsCommand, "handle", _handle)
    return calls


def test_lazy_command_failure_is_reported_not_answered_with_root_help(monkeypatch, capsys):
    def _boom(self, args):
        raise R4Error("gateway answered 500", {"hint": "run `r4 health`"})

    monkeypatch.setattr(ops_cli.OpsCommand, "handle", _boom)

    code = _run_main(monkeypatch, ["ops", "health", "--json"])
    out, err = capsys.readouterr()

    assert code == 1
    assert "Error: gateway answered 500" in err
    assert "hint: run `r4 health`" in err
    assert "usage: r4" not in out, "the root help replaced the error"
    assert out == ""


def test_root_flag_before_lazy_command_still_parses(monkeypatch, ops_calls):
    # -v would set the root logger to DEBUG for the rest of the test session.
    monkeypatch.setattr(logging, "basicConfig", lambda **_kw: None)
    code = _run_main(monkeypatch, ["-v", "ops", "health"])

    assert code == 0
    assert len(ops_calls) == 1
    assert ops_calls[0].command == "ops"


def test_source_override_applies_to_lazy_command(monkeypatch, ops_calls):
    applied: list[str] = []
    monkeypatch.setattr(cli, "_apply_tui_source_override", applied.append)

    code = _run_main(monkeypatch, ["--source", "b", "ops", "health"])

    assert code == 0
    assert applied == ["b"]
    assert len(ops_calls) == 1


def test_one_shot_prompt_error_has_no_traceback(monkeypatch, capsys):
    def _fail(args):
        raise R4Error("No model selected for this session.")

    monkeypatch.setattr(cli, "_launch_tui", _fail)

    code = _run_main(monkeypatch, ["-p", "hi"])
    _, err = capsys.readouterr()

    assert code == 1
    assert "Error: No model selected for this session." in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "argv, secret",
    [
        (["auth", "login", _JWT], "SIGNATUREsecretPART"),
        (["source", "set", "pub", "sk-live0123456789abcdef"], "sk-live0123456789abcdef"),
        (["source", "list", "Zx9" + "q" * 40], "Zx9" + "q" * 40),
    ],
)
def test_parser_error_never_echoes_a_token(monkeypatch, capsys, argv, secret):
    code = _run_main(monkeypatch, argv)
    out, err = capsys.readouterr()

    assert code == 2
    assert secret not in out + err
    assert "<redacted>" in err


def test_parser_error_keeps_hex_ids(monkeypatch, capsys):
    run_id = "2c0624cc-c807-4054-8921-a8a49624ef89"

    _run_main(monkeypatch, ["source", "list", run_id])

    assert run_id in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["sessions"], ["sessions", "list"]])
def test_a_removed_alias_whose_handler_registers_another_name_does_not_loop(
    monkeypatch, capsys, argv
):
    """Stab cycle 3 (python-runtime#3): `r4 sessions` (renamed `r4 session`).

    Its registry handler registers the parser ``session``; the lazy re-parse
    met the same invalid choice and dispatched again until RecursionError
    (Python 3.10), or argparse's "conflicting subparser" (3.11+).
    """
    code = _run_main(monkeypatch, argv)
    err = capsys.readouterr().err

    assert code == 2
    assert "Did you mean 'session'?" in err
    assert "Traceback" not in err
