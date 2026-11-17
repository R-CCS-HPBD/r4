"""``r4 knowledge ontology`` / ``graph-bind`` / ``graph-create --ontology``
(ADR-116 D-5): parsing, format inference, confirmation, error messages.

The knowledge client is a fake (no HTTP); the parser is the real one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from r4._cli import knowledge_ontology as ko
from r4._cli.knowledge import KnowledgeCommand
from r4.errors import APIError, AuthenticationError

_LINKML = """\
id: https://example.org/lab
name: lab
version: "1"
classes:
  Sample: {description: A physical sample}
  Instrument: {}
  Measurement: {}
slots:
  measured_by: {domain: Measurement, range: Instrument}
"""


class _FakeKnowledge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.rows: list[dict[str, Any]] = []
        self.raise_exc: Exception | None = None

    def _call(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc

    def ontology_register(self, source: str, **kwargs: Any) -> dict[str, Any]:
        self._call("register", source, **kwargs)
        ontology = {"ontology_id": "lab", "version": "1", "entity_types": ["a", "b", "c"]}
        ontology["relation_types"] = ["measured_by"]
        return {"ontology": ontology, "record": {"project_id": "proj-a"}}

    def ontology_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        self._call("list", **kwargs)
        return self.rows

    def ontology_show(self, ontology_id: str, version: str, **kwargs: Any) -> dict[str, Any]:
        self._call("show", ontology_id, version, **kwargs)
        ontology = {
            "ontology_id": ontology_id,
            "version": version,
            "entity_types": ["sample", "instrument"],
            "relation_types": ["measured_by"],
            "descriptions": {"sample": "A physical sample"},
            "aliases": [["sample", ["specimen"]]],
            "relation_signatures": [["measured_by", "measurement", "instrument"]],
            "class_buckets": {"sample": "entity"},
        }
        return {"ontology": ontology, "record": {"status": "active"}, "source_format": "linkml"}

    def ontology_retire(self, ontology_id: str, version: str, **kwargs: Any) -> dict[str, Any]:
        self._call("retire", ontology_id, version, **kwargs)
        return {"retired": True}

    def graph_info(self, graph: str) -> dict[str, Any]:
        """GET graphs/{id or name}: a name resolves to its ``g-`` id."""
        self._call("info", graph)
        return {"graph_id": graph if graph.startswith("g-") else f"g-{graph}", "name": graph}

    def graph_bind(self, graph_id: str, ontology_id: str, version: str | None) -> dict[str, Any]:
        self._call("bind", graph_id, ontology_id, version)
        return {"graph_id": graph_id, "name": "lab-graph", "ontology_version": version or "3"}

    def graph_create(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self._call("create", name, **kwargs)
        return {"graph_id": "g-new", "name": name, "kind": "research", **kwargs}


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeKnowledge:
    knowledge = _FakeKnowledge()

    class _Client:
        def __init__(self) -> None:
            self.knowledge = knowledge

    monkeypatch.setattr("r4.client.Client", _Client)
    return knowledge


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    KnowledgeCommand().register(sub)
    return parser.parse_args(["knowledge", *argv])


def _run(*argv: str) -> int:
    return KnowledgeCommand().handle(_parse(*argv))


# ── coordinates and formats ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [("lab", ("lab", None)), ("lab@1", ("lab", "1")), (" lab@1.2.0 ", ("lab", "1.2.0"))],
)
def test_parse_coordinate(text, expected):
    assert ko.parse_coordinate(text) == expected


@pytest.mark.parametrize("text", ["", "@1", "lab@", "a@b@c"])
def test_parse_coordinate_refuses(text):
    with pytest.raises(ko.OntologyUsageError):
        ko.parse_coordinate(text)


def test_parse_coordinate_needs_version_for_retire():
    with pytest.raises(ko.OntologyUsageError, match="version is required"):
        ko.parse_coordinate("lab", need_version=True)


@pytest.mark.parametrize(
    ("path", "explicit", "fmt"),
    [
        ("a.yaml", None, "linkml"),
        ("a.YML", None, "linkml"),
        ("a.json", None, "json"),
        ("a.json", "linkml", "linkml"),
        ("a.txt", "json", "json"),
    ],
)
def test_infer_format(path, explicit, fmt):
    assert ko.infer_format(path, explicit) == fmt


def test_infer_format_unknown_extension():
    with pytest.raises(ko.OntologyUsageError, match="--format"):
        ko.infer_format("schema.txt", None)


# ── register / list / show / retire ─────────────────────────────────────────


def test_register_reads_file_and_infers_linkml(fake, tmp_path: Path, capsys):
    path = tmp_path / "lab.yaml"
    path.write_text(_LINKML, encoding="utf-8")
    assert _run("ontology", "register", str(path)) == 0
    name, args, kwargs = fake.calls[0]
    assert name == "register"
    assert args == (_LINKML,)
    assert kwargs == {"fmt": "linkml", "scope": "project", "project_id": None}
    out = capsys.readouterr().out
    assert "lab@1" in out and "3 classes, 1 relations" in out


def test_register_global_json_and_project(fake, tmp_path: Path):
    path = tmp_path / "lab.json"
    path.write_text("{}", encoding="utf-8")
    assert _run("ontology", "register", str(path), "--global", "-p", "proj-b") == 0
    assert fake.calls[0][2] == {"fmt": "json", "scope": "global", "project_id": "proj-b"}


def test_register_explicit_format_wins(fake, tmp_path: Path):
    path = tmp_path / "lab.txt"
    path.write_text(_LINKML, encoding="utf-8")
    assert _run("ontology", "register", str(path), "--format", "linkml") == 0
    assert fake.calls[0][2]["fmt"] == "linkml"


def test_register_unknown_extension_is_usage_error(fake, tmp_path: Path, capsys):
    path = tmp_path / "lab.txt"
    path.write_text(_LINKML, encoding="utf-8")
    assert _run("ontology", "register", str(path)) == 2
    assert fake.calls == []
    assert "--format" in capsys.readouterr().err


def test_register_missing_file(fake, tmp_path: Path, capsys):
    assert _run("ontology", "register", str(tmp_path / "nope.yaml")) == 2
    assert "cannot read" in capsys.readouterr().err


def test_list_table(fake, capsys):
    fake.rows = [
        {"ontology_id": "r4-science", "version": "1", "builtin": True, "n_classes": 20},
        {
            "ontology_id": "lab",
            "version": "2",
            "project_id": "proj-a",
            "status": "active",
            "n_classes": 3,
            "n_relations": 1,
            "description": "Lab vocabulary",
        },
    ]
    assert _run("ontology", "list", "--all") == 0
    assert fake.calls[0] == ("list", (), {"include_retired": True, "project_id": None})
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "ID",
        "VERSION",
        "SCOPE",
        "STATUS",
        "CLASSES",
        "RELATIONS",
        "DESCRIPTION",
    ]
    assert lines[1].split()[:3] == ["r4-science", "1", "builtin"]
    assert "proj-a" in lines[2] and "Lab vocabulary" in lines[2]


def test_list_json(fake, capsys):
    fake.rows = [{"ontology_id": "lab", "version": "1"}]
    assert _run("ontology", "list", "--json") == 0
    assert json.loads(capsys.readouterr().out) == {"ontologies": fake.rows}


def test_list_empty(fake, capsys):
    assert _run("ontology", "list") == 0
    assert "No ontologies" in capsys.readouterr().out


def test_show_with_version(fake, capsys):
    assert _run("ontology", "show", "lab@2") == 0
    assert fake.calls == [("show", ("lab", "2"), {"project_id": None})]
    out = capsys.readouterr().out
    assert "lab@2" in out
    assert "sample [entity]" in out
    assert "aliases: specimen" in out
    assert "measured_by: measurement -> instrument" in out


def test_show_without_version_picks_newest_live(fake, capsys):
    fake.rows = [
        {"ontology_id": "lab", "version": "9", "status": "active", "created_at": "2026-11-01"},
        {"ontology_id": "lab", "version": "10", "status": "active", "created_at": "2026-11-02"},
        {"ontology_id": "lab", "version": "11", "status": "retired", "created_at": "2026-11-03"},
        {"ontology_id": "other", "version": "99", "status": "active", "created_at": "2026-11-09"},
    ]
    assert _run("ontology", "show", "lab") == 0
    assert fake.calls[-1] == ("show", ("lab", "10"), {"project_id": None})


def test_newest_live_version_compares_numbers_without_dates():
    rows = [{"ontology_id": "x", "version": v} for v in ("1.9", "1.10", "1.2")]
    assert ko.newest_live_version(rows, "x") == "1.10"
    assert ko.newest_live_version(rows, "y") is None


def test_show_without_version_none_live(fake, capsys):
    assert _run("ontology", "show", "lab") == 2
    assert "no live ontology" in capsys.readouterr().err


def test_show_json(fake, capsys):
    assert _run("ontology", "show", "lab@1", "--json") == 0
    assert json.loads(capsys.readouterr().out)["source_format"] == "linkml"


def test_retire_requires_version(fake, capsys):
    assert _run("ontology", "retire", "lab", "--yes") == 2
    assert fake.calls == []
    assert "version is required" in capsys.readouterr().err


def test_retire_asks_and_aborts_on_no(fake, monkeypatch, capsys):
    prompts: list[str] = []

    def _input(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", _input)
    assert _run("ontology", "retire", "lab@1") == 1
    assert fake.calls == []
    assert "lab@1" in prompts[0]
    assert "Aborted." in capsys.readouterr().out


def test_retire_eof_is_no(fake, monkeypatch):
    def _eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert _run("ontology", "retire", "lab@1") == 1
    assert fake.calls == []


def test_retire_confirmed(fake, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert _run("ontology", "retire", "lab@1") == 0
    assert fake.calls == [("retire", ("lab", "1"), {"project_id": None})]
    assert "Retired ontology lab@1" in capsys.readouterr().out


def test_retire_yes_skips_prompt(fake, monkeypatch):
    def _never(prompt: str) -> str:
        raise AssertionError("prompted despite --yes")

    monkeypatch.setattr("builtins.input", _never)
    assert _run("ontology", "retire", "lab@1", "--yes") == 0
    assert fake.calls[0][0] == "retire"


# ── graph-create --ontology / graph-bind ────────────────────────────────────


def test_graph_create_with_ontology_passes_both_fields(fake, capsys):
    assert _run("graph-create", "lab-graph", "--ontology", "lab@2") == 0
    name, args, kwargs = fake.calls[0]
    assert (name, args) == ("create", ("lab-graph",))
    assert kwargs["ontology_id"] == "lab"
    assert kwargs["ontology_version"] == "2"
    assert "ontology: lab@2" in capsys.readouterr().out


def test_graph_create_ontology_without_version(fake):
    assert _run("graph-create", "lab-graph", "--ontology", "lab") == 0
    kwargs = fake.calls[0][2]
    assert kwargs["ontology_id"] == "lab"
    assert kwargs["ontology_version"] is None


def test_graph_create_without_ontology_sends_none(fake):
    assert _run("graph-create", "plain") == 0
    kwargs = fake.calls[0][2]
    assert kwargs["ontology_id"] is None and kwargs["ontology_version"] is None


def test_graph_create_bad_ontology_coordinate(fake, capsys):
    assert _run("graph-create", "g", "--ontology", "lab@") == 2
    assert fake.calls == []


def test_graph_create_maps_server_ontology_error(fake, capsys):
    fake.raise_exc = _api_error(400, "ontology.not_found", "unknown ontology lab@7")
    assert _run("graph-create", "g", "--ontology", "lab@7") == 1
    err = capsys.readouterr().err
    assert "No live ontology with that id and version" in err
    assert "unknown ontology lab@7" in err


def test_graph_bind(fake, capsys):
    assert _run("graph-bind", "g-1", "lab@2") == 0
    assert fake.calls == [("info", ("g-1",), {}), ("bind", ("g-1", "lab", "2"), {})]
    assert "bound to lab@2" in capsys.readouterr().out


def test_graph_bind_by_name_patches_the_graphs_id(fake, capsys):
    """The PATCH matches graph_id exactly: `graph-bind lab-notes lab` (the
    manual's flow after `graph-create lab-notes`) got 404."""
    assert _run("graph-bind", "lab-notes", "lab@2") == 0
    assert fake.calls[-1] == ("bind", ("g-lab-notes", "lab", "2"), {})


def test_graph_bind_without_version_reports_pinned(fake, capsys):
    assert _run("graph-bind", "g-1", "lab") == 0
    assert fake.calls[-1] == ("bind", ("g-1", "lab", None), {})
    assert "bound to lab@3" in capsys.readouterr().out


def test_graph_create_with_ontology_json_is_one_json_document(fake, capsys):
    assert _run("graph-create", "g", "--ontology", "lab@1", "--json") == 0
    assert json.loads(capsys.readouterr().out)["ontology_id"] == "lab"


def test_show_prints_the_servers_relation_signatures(fake, monkeypatch, capsys):
    """Ontology.to_dict() sends a list of {relation, domain, range}; only
    [[rel, d, r]] was read, so `show` printed no domain -> range."""
    shown = fake.ontology_show("lab", "1")
    shown["ontology"]["relation_signatures"] = [
        {"relation": "measured_by", "domain": "measurement", "range": "instrument"},
        {"relation": "measured_by", "domain": "sample", "range": "instrument"},
    ]
    monkeypatch.setattr(fake, "ontology_show", lambda *_a, **_k: shown)
    assert _run("ontology", "show", "lab@1") == 0
    out = capsys.readouterr().out
    assert "measured_by: measurement -> instrument; sample -> instrument" in out


def test_newest_live_version_is_the_servers_highest_version():
    """The server pins the highest live version; `show` took the latest
    registered one, so a 1.1 fix registered after 2 was shown, 2 bound."""
    rows = [
        {"ontology_id": "lab", "version": "2", "created_at": "2026-11-01"},
        {"ontology_id": "lab", "version": "1.1", "created_at": "2026-11-05"},
    ]
    assert ko.newest_live_version(rows, "lab") == "2"
    words = [{"ontology_id": "x", "version": v} for v in ("1.0.1", "1.0-rc")]
    assert ko.newest_live_version(words, "x") == "1.0-rc"  # a word part ranks above a number


def test_graph_bind_403_is_not_called_ontology_forbidden(fake, capsys):
    fake.raise_exc = AuthenticationError("Access denied (403): graph scope", status_code=403)
    assert _run("graph-bind", "g", "lab@1") == 1
    err = capsys.readouterr().err
    assert "Ontologies are registered" not in err
    assert "graph scope" in err


# ── server error codes ──────────────────────────────────────────────────────


def _api_error(status: int, code: str | None, message: str = "why") -> APIError:
    detail: dict[str, Any] = {"message": message}
    if code:
        detail["error"] = code
    body = json.dumps({"detail": detail})
    return APIError(f"API error: {status}: {message}", status_code=status, response_body=body)


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (_api_error(400, "ontology.invalid", "class Foo has no name"), "rejected as invalid"),
        (_api_error(409, "ontology.conflict"), "already registered in this project"),
        (_api_error(404, "ontology.not_found"), "No live ontology"),
        (AuthenticationError("Access denied (403): agents may not", status_code=403), "by people"),
        (_api_error(404, None, "Not Found"), "no ontology routes"),
    ],
)
def test_error_messages(exc, needle):
    assert needle in ko.ontology_error_message(exc)


def test_error_message_keeps_server_reason():
    msg = ko.ontology_error_message(_api_error(400, "ontology.invalid", "class Foo has no name"))
    assert "(class Foo has no name)" in msg
    assert "API error" not in msg


def test_error_code_from_header_and_flat_envelope():
    header = APIError("x", status_code=400, error_code="ontology.invalid")
    flat = APIError("x", status_code=409, response_body=json.dumps({"error": "ontology.conflict"}))
    nested = APIError(
        "x", status_code=403, response_body=json.dumps({"error": {"code": "ontology.forbidden"}})
    )
    assert ko.error_code(header) == "ontology.invalid"
    assert ko.error_code(flat) == "ontology.conflict"
    assert ko.error_code(nested) == "ontology.forbidden"


def test_other_errors_pass_through():
    exc = APIError("API error: 500: boom", status_code=500)
    assert ko.ontology_error_message(exc) == "API error: 500: boom"
    assert ko.error_code(RuntimeError("x")) is None


def test_register_conflict_exit_and_json(fake, tmp_path: Path, capsys):
    path = tmp_path / "lab.yaml"
    path.write_text(_LINKML, encoding="utf-8")
    fake.raise_exc = _api_error(409, "ontology.conflict", "lab@1 exists")
    assert _run("ontology", "register", str(path), "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 1
    assert "already registered" in payload["error"]


def test_help_lists_ontology_actions(capsys):
    with pytest.raises(SystemExit):
        _parse("ontology", "--help")
    out = capsys.readouterr().out
    for action in ("register", "list", "show", "retire", "graph-bind"):
        assert action in out
