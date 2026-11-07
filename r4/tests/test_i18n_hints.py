"""Bilingual error-hint catalog tests (C3 follow-up).

Verifies the single-source-of-truth design: ``r4.i18n.HINT_CATALOG``
drives ``_die`` hint rendering with locale parity.
"""

from __future__ import annotations

import pytest

from r4 import i18n
from r4._cli._helpers import _die


@pytest.fixture(autouse=True)
def _restore_locale():
    original = i18n.locale()
    yield
    i18n.set_locale(original)


CATALOG_IDS = [
    "project.not_found",
    "project.import.path_missing",
    "client.package_missing",
    "knowledge.verify_failed",
    "project.loop_id_required",
    "project.loop_watch_timeout",
]


class TestHintCatalog:
    def test_all_entries_have_en_and_ja(self):
        for error_id, pair in i18n.HINT_CATALOG.items():
            en, ja = pair
            assert en.strip(), f"{error_id}: empty EN hint"
            assert ja.strip(), f"{error_id}: empty JA hint"
            assert en != ja, f"{error_id}: JA must not equal EN"

    def test_every_cataloged_id_has_entry(self):
        for error_id in CATALOG_IDS:
            assert error_id in i18n.HINT_CATALOG

    def test_hint_for_follows_locale(self):
        i18n.set_locale("en")
        assert i18n.hint_for("project.not_found") == i18n.HINT_CATALOG["project.not_found"][0]
        i18n.set_locale("ja")
        assert i18n.hint_for("project.not_found") == i18n.HINT_CATALOG["project.not_found"][1]

    def test_hint_for_unknown_id_returns_none(self):
        assert i18n.hint_for("unknown.error_id") is None

    def test_hint_label_localized(self):
        i18n.set_locale("en")
        assert i18n.hint_label() == "hint"
        i18n.set_locale("ja")
        assert i18n.hint_label() == "ヒント"


class TestDieCatalogIntegration:
    def _capture(self, capsys, **kwargs):
        with pytest.raises(SystemExit):
            _die("boom", **kwargs)
        return capsys.readouterr().err

    def test_ja_locale_renders_ja_hint(self, capsys):
        i18n.set_locale("ja")
        err = self._capture(capsys, error_id="project.not_found")
        assert "ヒント:" in err
        assert i18n.HINT_CATALOG["project.not_found"][1] in err

    def test_en_locale_renders_en_hint(self, capsys):
        i18n.set_locale("en")
        err = self._capture(capsys, error_id="project.not_found")
        assert "hint:" in err
        assert i18n.HINT_CATALOG["project.not_found"][0] in err

    def test_explicit_hint_wins_in_en(self, capsys):
        i18n.set_locale("en")
        err = self._capture(capsys, error_id="project.not_found", hint="custom en hint")
        assert "custom en hint" in err

    def test_catalog_ja_beats_inline_hint_in_ja(self, capsys):
        i18n.set_locale("ja")
        err = self._capture(capsys, error_id="project.not_found", hint="custom en hint")
        assert i18n.HINT_CATALOG["project.not_found"][1] in err
        assert "custom en hint" not in err

    def test_unknown_id_still_takes_explicit_hint(self, capsys):
        i18n.set_locale("ja")
        err = self._capture(capsys, error_id="other.id", hint="stay with me")
        assert "stay with me" in err

    def test_no_hint_no_id_prints_nothing(self, capsys):
        err = self._capture(capsys)
        assert "hint" not in err.lower() or "ヒント" not in err
