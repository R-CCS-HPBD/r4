"""R4c: dead-gateway slash-command families surface a retirement note."""

from types import SimpleNamespace

from r4.tui.commands import (
    _cmd_adaptive,
    _cmd_checkpoint,
    _cmd_lineage,
    _cmd_outcome,
    _cmd_recovery,
    _cmd_replay,
    _cmd_vectors,
    _cmd_visibility,
)


class _FakeRenderer:
    def __init__(self):
        self.errors: list[str] = []
        self.infos: list[str] = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)

    def system(self, msg):
        self.infos.append(msg)


def _app():
    return SimpleNamespace(renderer=_FakeRenderer(), client=object())


def test_checkpoint_retired_note():
    app = _app()
    _cmd_checkpoint(app, ["list"])
    assert any("not available on this gateway" in e for e in app.renderer.infos)


def test_vectors_retired_note():
    app = _app()
    _cmd_vectors(app, ["versions"])
    assert any("retired" in e.lower() for e in app.renderer.infos)


def test_all_dead_families_gated():
    families = [
        (_cmd_recovery, []),
        (_cmd_replay, []),
        (_cmd_outcome, []),
        (_cmd_lineage, []),
        (_cmd_adaptive, []),
        (_cmd_visibility, []),
    ]
    for handler, args in families:
        app = _app()
        handler(app, args)
        assert app.renderer.infos, f"No retirement note from {handler}"
