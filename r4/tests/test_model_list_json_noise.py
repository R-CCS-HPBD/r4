"""Regression: `r4 model list --json` stdout must be a parseable JSON document,
never polluted by duplicate-name warnings that used to be printed on the same
loop pass (callers commonly merge 2>&1 — e.g. demos/lib/r4_demo.sh resolver).

Bug found on ng-dgx demo run: parse dies when stderr warnings merge into the
JSON blob, so the whole demo suite stopped at 'Unable to resolve a demo model'.
"""

from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace

from r4._cli import model as model_cli


def _mk_model(name: str, scope: str = "system", mid: str = "mdl-1"):
    return SimpleNamespace(
        model_id=mid,
        name=name,
        scope=SimpleNamespace(value=scope),
        model_type=SimpleNamespace(value="llm"),
        status=SimpleNamespace(value="available"),
        storage_path=None,
        capabilities=SimpleNamespace(
            to_dict=lambda: {},
            supports_tool_calling=False,
            supports_vision=False,
            max_position_embeddings=None,
        ),
        created_at=None,
        serving_profile=None,
    )


def _run_list(args, client):
    saved_stdout, saved_stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(saved_stdout), contextlib.redirect_stderr(saved_stderr):
        model_cli._cmd_model_list(args, client)
    return saved_stdout.getvalue(), saved_stderr.getvalue()


class _Client:
    def list_models(self, **_kwargs):
        return [
            _mk_model("dup.X", mid="mdl-a"),
            _mk_model("dup.X", mid="mdl-b"),
            _mk_model("agents-a1", mid="mdl-c"),
        ]


class TestModelListJsonNoise:
    def _args(self, as_json: bool) -> SimpleNamespace:
        return SimpleNamespace(
            as_json=as_json,
            project="default",
            scope=None,
            model_type=None,
            type=None,
            status=None,
            limit=100,
        )

    def test_json_stdout_is_pure_document(self, monkeypatch):
        monkeypatch.setattr(model_cli, "_public_model_context", lambda: (None, [], [], "public"))
        stdout, _stderr = _run_list(self._args(as_json=True), _Client())
        # must parse as a whole document
        import json

        doc = json.loads(stdout)
        assert isinstance(doc, list)
        names = {m.get("name") for m in doc if isinstance(m, dict)}
        assert "agents-a1" in names

    def test_no_duplicate_warning_in_json_stdout(self, monkeypatch):
        monkeypatch.setattr(model_cli, "_public_model_context", lambda: (None, [], [], "public"))
        stdout, _stderr = _run_list(self._args(as_json=True), _Client())
        assert "appears" not in stdout
        assert "disambiguate" not in stdout

    def test_table_mode_still_shows_duplicate_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(model_cli, "_public_model_context", lambda: (None, [], [], "public"))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with contextlib.redirect_stdout(io.StringIO()):
                model_cli._cmd_model_list(self._args(as_json=False), _Client())
        assert "appears" in err.getvalue()
