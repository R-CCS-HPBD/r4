"""Cycle-3 client-server-compat#2 (2026-10-22): the r4 command that the UMRS
keyless-export refusal names is one the real r4 parser accepts.

The refusal said "--allow-unsigned" while no r4 export command had that
flag. This pairs the server's message with the client's parser, so either
side drifting turns this red. Needs PYTHONPATH=umrs/src:r4/src.
"""

from __future__ import annotations

import re
import shlex

import pytest

pytest.importorskip("umrs")
from fastapi import HTTPException  # noqa: E402
from umrs.api.knowledge import ExportRequest, _refuse_unsignable_export  # noqa: E402

from r4 import cli  # noqa: E402


def _refusal_message(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("UMRS_SECRET_KEY", raising=False)
    with pytest.raises(HTTPException) as info:
        _refuse_unsignable_export(ExportRequest(project_id="pa"))
    return str(info.value.detail)


def test_named_command_parses_and_opts_out(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = re.findall(r"`r4 ([^`]+)`", _refusal_message(monkeypatch))
    assert commands, "the refusal names no r4 command"
    for command in commands:
        args = cli._build_parser().parse_args([*shlex.split(command), "pa"])
        assert args.allow_unsigned is True
