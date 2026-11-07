"""Focused regressions for ``r4 agent-exec`` command handling."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest


def _agent_exec_args(**overrides) -> Namespace:
    values = {
        "exec_command": "list",
        "exec_id": "exec-1",
        "agent": None,
        "version": "",
        "model": "",
        "prompt_hash": "",
        "params": "{}",
        "inputs": [],
        "status": None,
        "outputs": [],
        "error": None,
        "trace": None,
        "project": "demo",
        "user": None,
        "limit": 50,
        "offset": 0,
        "as_json": True,
    }
    values.update(overrides)
    return Namespace(**values)


def test_cmd_agent_exec_list_json(monkeypatch: pytest.MonkeyPatch, capsys):
    import r4.client as client_module
    from r4._cli.agent_exec import cmd_agent_exec

    class FakeHTTP:
        def __init__(self) -> None:
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/gw/a2l/health"):
                return {"ok": True}
            return {"items": [{"id": "exec-1", "agent_name": "demo"}], "total": 1}

    fake_http = FakeHTTP()

    class FakeClient:
        def __init__(self) -> None:
            self.http = fake_http
            self.config = SimpleNamespace(gateway_url="http://gateway.test", project_id=None)

    monkeypatch.setattr(client_module, "Client", FakeClient)

    cmd_agent_exec(_agent_exec_args())

    assert json.loads(capsys.readouterr().out)["total"] == 1
    _, list_kwargs = fake_http.calls[1]
    assert list_kwargs["params"]["project_id"] == "demo"


def test_cmd_agent_exec_create_rejects_bad_params(monkeypatch: pytest.MonkeyPatch):
    import r4.client as client_module
    from r4._cli.agent_exec import cmd_agent_exec

    class FakeHTTP:
        def get(self, _url, **_kwargs):
            return {"ok": True}

    class FakeClient:
        def __init__(self) -> None:
            self.http = FakeHTTP()
            self.config = SimpleNamespace(gateway_url="http://gateway.test", project_id=None)

    monkeypatch.setattr(client_module, "Client", FakeClient)

    with pytest.raises(SystemExit) as exc_info:
        cmd_agent_exec(_agent_exec_args(exec_command="create", agent="demo", params="{"))

    assert exc_info.value.code == 2


# -- audit #18: `r4 run` parses into the dests cmd_agent_exec reads ----------


def _run_parser():
    import argparse

    from r4._cli.run import RunCommand

    parser = argparse.ArgumentParser()
    RunCommand().register(parser.add_subparsers(dest="command"))
    return parser


class _RunFakeHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, url, **_kwargs):
        self.calls.append(("GET", url))
        return {"executions": [], "total": 0}

    def post(self, url, **_kwargs):
        self.calls.append(("POST", url))
        return {"id": "exec-1"}

    def request(self, method, url, **_kwargs):
        self.calls.append((method, url))
        return {"id": "exec-1"}


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["run", "list", "--status", "failed", "--project", "p2"], ("GET", "/agent-executions")),
        (["run", "show", "exec-1"], ("GET", "/agent-executions/exec-1")),
        (["run", "create", "--agent", "qc", "--model", "m"], ("POST", "/agent-executions")),
        (["run", "update", "exec-1", "--status", "completed"], ("PATCH", "/exec-1")),
        (["run", "ledger", "job-1"], ("GET", "/gw/a2l/jobs/job-1/view")),
    ],
)
def test_r4_run_subcommands_reach_cmd_agent_exec(monkeypatch, capsys, argv, expected):
    import r4._cli.agent_exec as agent_exec_module
    import r4.client as client_module
    from r4._cli.agent_exec import cmd_agent_exec

    fake_http = _RunFakeHTTP()

    class FakeClient:
        def __init__(self) -> None:
            self.http = fake_http
            self.config = SimpleNamespace(gateway_url="http://gateway.test", project_id=None)

    monkeypatch.setattr(client_module, "Client", FakeClient)
    monkeypatch.setattr(agent_exec_module, "_current_project_id", lambda: None)

    cmd_agent_exec(_run_parser().parse_args(argv))

    method, suffix = expected
    assert any(m == method and url.endswith(suffix) for m, url in fake_http.calls)


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "create", "--agent", "qc", "--input", "art-1"],
        ["run", "update", "exec-1", "--output", "art-9"],
    ],
)
def test_r4_run_has_no_artifact_link_flags(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_parser().parse_args(argv)
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
