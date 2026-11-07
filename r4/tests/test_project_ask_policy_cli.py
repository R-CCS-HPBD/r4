"""`r4 project ask_policy` — show / set the persisted ask-the-scientist
default (D-33, ADR-066 amendment 3: maturity-stage presets)."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest


def _args(**over):
    base = {
        "project_id": "proj-a",
        "stage": None,
        "mode": None,
        "clear": False,
        "triggers": "",
        "json": False,
    }
    base.update(over)
    return Namespace(**base)


def _run(args, reply):
    from r4._cli import project_knowledge as pk

    client = MagicMock()
    client.get_platform_project.return_value = reply
    client.set_project_ask_policy.return_value = reply
    with (
        patch("r4.client.Client", return_value=client),
        patch.object(pk, "_resolve_project_id", lambda a, pm: "proj-a"),
    ):
        pk.project_ask_policy(args, MagicMock())
    return client


REPLY = {
    "project": {
        "ask_mode": "profile",
        "pause_triggers": ["physical_action"],
        "maturity_stage": "autonomous",
    }
}


def test_no_flags_shows_the_policy(capsys):
    client = _run(_args(), REPLY)
    client.get_platform_project.assert_called_once_with("proj-a")
    client.set_project_ask_policy.assert_not_called()
    out = capsys.readouterr().out
    assert "stage=autonomous" in out and "physical_action" in out


def test_stage_sets_the_preset():
    client = _run(_args(stage="competent"), REPLY)
    client.set_project_ask_policy.assert_called_once_with("proj-a", maturity_stage="competent")


def test_explicit_profile_with_triggers():
    client = _run(_args(mode="profile", triggers="cost, direction_change"), REPLY)
    client.set_project_ask_policy.assert_called_once_with(
        "proj-a", ask_mode="profile", pause_triggers=["cost", "direction_change"]
    )


def test_clear_sends_an_empty_policy():
    client = _run(_args(clear=True), REPLY)
    client.set_project_ask_policy.assert_called_once_with("proj-a")


def test_client_body_shapes():
    from r4.client import Client

    c = Client.__new__(Client)
    c._is_umrs_direct = False  # body-shape pin; skip the mode probe
    c.patch = MagicMock(return_value={})
    c.set_project_ask_policy("p", maturity_stage="learning")
    assert c.patch.call_args.kwargs["json"] == {
        "action": "set_ask_policy",
        "maturity_stage": "learning",
    }
    c.set_project_ask_policy("p", ask_mode="ask")
    assert c.patch.call_args.kwargs["json"] == {
        "action": "set_ask_policy",
        "ask_mode": "ask",
        "pause_triggers": [],
    }
    assert c.patch.call_args.args[0] == "/gw/projects/p"


@pytest.mark.parametrize("name", ["ask_policy", "ask-policy"])
def test_subcommand_and_alias_dispatch(name):
    from r4._cli.project import _project_handlers
    from r4._cli.project_knowledge import project_ask_policy

    assert _project_handlers()[name] is project_ask_policy
