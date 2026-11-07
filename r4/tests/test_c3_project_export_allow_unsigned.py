"""Cycle-3 client-server-compat#2 (2026-10-22): `r4 project export` has an
explicit, loud ``--allow-unsigned``.

A server without a bundle signing key (UMRS_SECRET_KEY) refuses a signed
export with a 503 that names this flag. No r4 export command had it, so a
user who followed the remedy got "unrecognized arguments" and could not
export their own project. The SDK already sent ``allow_unsigned``; only the
CLI never set it. Unsigned is never the default, and every use is announced
on stderr.
"""

from __future__ import annotations

import json
from argparse import Namespace
from typing import Any

import pytest

from r4 import cli
from r4._cli.project_knowledge import project_export


def _parse(argv: list[str]) -> Namespace:
    return cli._build_parser().parse_args(["project", "export", *argv])


def test_parser_accepts_allow_unsigned() -> None:
    assert _parse(["--allow-unsigned", "pa"]).allow_unsigned is True


def test_unsigned_is_never_the_default() -> None:
    assert _parse(["pa"]).allow_unsigned is False


class _Recorder:
    calls: list[dict[str, Any]] = []

    def __init__(self, *a: object, **kw: object) -> None:
        recorder = self

        class _Knowledge:
            def export(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
                type(recorder).calls.append({"project_id": project_id, **kwargs})
                return {"bundle_id": "b1", "status": "processing"}

        self.knowledge = _Knowledge()


class _PM:
    def get(self, project_id: str) -> object:
        return object()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    _Recorder.calls = []
    monkeypatch.setattr("r4.client.Client", _Recorder)
    return _Recorder.calls


def _run(argv: list[str]) -> int:
    return project_export(_parse(argv), _PM())  # type: ignore[arg-type]


def test_flag_reaches_the_server_request_and_warns(recorded, capsys) -> None:
    assert _run(["--allow-unsigned", "--json", "pa"]) == 0
    assert recorded == [
        {
            "project_id": "pa",
            "include_raw": False,
            "acknowledge_sensitive": False,
            "chunk_text": False,
            "allow_unsigned": True,
        }
    ]
    out, err = capsys.readouterr()
    assert json.loads(out)["bundle_id"] == "b1"  # stdout stays JSON
    assert "UNSIGNED" in err and "--allow-unsigned" in err


def test_without_the_flag_nothing_unsigned_is_asked_or_said(recorded, capsys) -> None:
    assert _run(["pa"]) == 0
    assert recorded[0]["allow_unsigned"] is False
    assert "UNSIGNED" not in capsys.readouterr().err
