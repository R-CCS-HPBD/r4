"""`r4 project storage` — bind a project's stores outside the front-end (ADR-077).

Pinned: locators come from flags > dedicated files > an env file (``-`` =
stdin) and are only ever sent, never printed or persisted; ``--unset-*``
sends "" (unbind); rendering shows the server's redacted form only; the
client calls the four /gw/projects/{id}/storage verbs.
"""

from __future__ import annotations

import io
import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

S3 = "http://AKIDEXAMPLE:s3cr3t-key@10.0.0.31:28900"
PG = "postgresql://example-project-b:pg-pass@10.0.0.31:25432/example-project-b?sslmode=require"

REDACTED = {
    "status": "ok",
    "storage": {
        "project_id": "example-project-b",
        "bound": True,
        "s3": {
            "endpoint_url": "http://10.0.0.31:28900",
            "bucket": "",
            "region": "us-east-1",
            "backend": "seaweedfs",
            "verify": True,
            "access_key_hint": "AKID…",
        },
        "postgres": {
            "host": "10.0.0.31",
            "port": 25432,
            "dbname": "example-project-b",
            "user": "example-project-b",
            "sslmode": "require",
        },
        "updated_by": "mnagaso",
        "updated_at": "2026-10-01T12:00:00+00:00",
    },
    "probe": {
        "ok": True,
        "s3": {"ok": True, "bucket": "r4-data", "buckets_created": ["r4-data"], "roundtrip_ms": 9},
        "postgres": {
            "ok": True,
            "server_version": "15.19",
            "tls": True,
            "pgvector": "0.8.1",
            "vector_roundtrip": True,
            "latency_ms": 4,
        },
    },
}


def _args(action: str, **over) -> Namespace:
    base = {
        "storage_action": action,
        "project_id": "example-project-b",
        "env_file": None,
        "s3_url_file": None,
        "postgres_uri_file": None,
        "s3_url": None,
        "postgres_uri": None,
        "unset_s3": False,
        "unset_postgres": False,
        "no_check": False,
        "json": False,
    }
    base.update(over)
    return Namespace(**base)


def _run(args: Namespace, reply=REDACTED) -> MagicMock:
    from r4._cli import project_storage as cli

    client = MagicMock()
    for verb in ("get", "set", "check", "clear"):
        getattr(client, f"{verb}_project_storage").return_value = reply
    with patch("r4.client.Client", return_value=client):
        cli.project_storage(args, MagicMock())
    return client


def test_env_file_supplies_both_locators(tmp_path, capsys):
    env = tmp_path / "project-example-project-b.env"
    env.write_text(
        "# generated\nPROJECT_PG_PASSWORD=x\n"
        f"UMRS_PROJECT_POSTGRES_URI={PG}\nUMRS_PROJECT_S3_URL={S3}\n"
    )
    client = _run(_args("set", env_file=str(env)))
    client.set_project_storage.assert_called_once_with(
        "example-project-b", s3_url=S3, postgres_uri=PG, check=True
    )
    out = capsys.readouterr().out
    assert "s3cr3t-key" not in out and "pg-pass" not in out
    assert "http://10.0.0.31:28900" in out
    assert "example-project-b@10.0.0.31:25432/example-project-b" in out
    assert "probe: ok" in out and "pgvector 0.8.1 (round-trip ok)" in out


def test_stdin_env_file_and_no_check(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(f"UMRS_PROJECT_S3_URL='{S3}'\n"))
    client = _run(_args("set", env_file="-", no_check=True))
    client.set_project_storage.assert_called_once_with(
        "example-project-b", s3_url=S3, postgres_uri=None, check=False
    )


def test_flag_beats_files_and_unset_sends_blank(tmp_path):
    f = tmp_path / "s3.txt"
    f.write_text("http://other:key@10.0.0.9:9000\n")
    client = _run(_args("set", s3_url=S3, s3_url_file=str(f), unset_postgres=True))
    client.set_project_storage.assert_called_once_with(
        "example-project-b", s3_url=S3, postgres_uri="", check=True
    )


def test_set_without_locators_is_a_named_failure(capsys):
    with pytest.raises(SystemExit):
        _run(_args("set"))
    assert "nothing to set" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("action", "verb"), [("show", "get"), ("check", "check"), ("clear", "clear")]
)
def test_read_and_clear_verbs(action, verb, capsys):
    client = _run(_args(action))
    getattr(client, f"{verb}_project_storage").assert_called_once_with("example-project-b")
    out = capsys.readouterr().out
    if action == "clear":
        assert "binding cleared" in out
    else:
        assert "external storage bound" in out


def test_unbound_rendering_and_json(capsys):
    unbound = {"status": "ok", "storage": {"project_id": "example-project-b", "bound": False}}
    _run(_args("show"), reply=unbound)
    assert "server's own stores are used" in capsys.readouterr().out
    _run(_args("show", json=True))
    assert json.loads(capsys.readouterr().out)["storage"]["bound"] is True


def test_failed_probe_lines():
    from r4._cli.project_storage import render_probe

    lines = render_probe(
        {"ok": False, "s3": {"ok": False, "error": "EndpointConnectionError", "detail": "refused"}}
    )
    assert lines == ["probe: FAILED", "  s3: FAILED EndpointConnectionError: refused"]


def test_client_verbs_and_body_shapes():
    from r4.client import Client

    c = Client.__new__(Client)
    c._config = MagicMock(gateway_url="https://gw.example")
    c._http_core = http = MagicMock()
    c.get = MagicMock(return_value={})
    c.post = MagicMock(return_value={})
    c.delete = MagicMock(return_value={})
    c.set_project_storage("example-project-b", s3_url=S3, check=False)
    url = http.put.call_args.args[0]
    assert url == "https://gw.example/gw/projects/example-project-b/storage"
    assert http.put.call_args.kwargs["json_body"] == {"check": False, "s3_url": S3}
    c.get_project_storage("example-project-b")
    assert c.get.call_args.args[0] == "/gw/projects/example-project-b/storage"
    c.check_project_storage("example-project-b")
    assert c.post.call_args.args[0] == "/gw/projects/example-project-b/storage/check"
    c.clear_project_storage("example-project-b")
    assert c.delete.call_args.args[0] == "/gw/projects/example-project-b/storage"


def test_parser_registered():
    import argparse

    from r4._cli.project import _project_handlers
    from r4._cli.project_storage import add_storage_parser, project_storage

    assert _project_handlers()["storage"] is project_storage

    parser = argparse.ArgumentParser()
    add_storage_parser(parser.add_subparsers(dest="cmd"))
    ns = parser.parse_args(["storage", "set", "example-project-b", "--env-file", "-", "--no-check"])
    assert (ns.storage_action, ns.project_id, ns.env_file, ns.no_check) == (
        "set",
        "example-project-b",
        "-",
        True,
    )
