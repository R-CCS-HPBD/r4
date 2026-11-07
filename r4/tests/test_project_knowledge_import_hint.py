"""The missing-umrs hint names an install a public user can run (2026-11-07).

Before, it said "Install umrs from the ai4s-platform workspace": the old
name of the private development repository, which a public user cannot act on.
"""

from __future__ import annotations

import sys

import pytest

from r4._cli import project_knowledge


def test_missing_umrs_names_the_submodule_install(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "umrs.knowledge.project", None)
    with pytest.raises(SystemExit) as raised:
        project_knowledge._import_research_project()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "ai4s-platform" not in err
    assert 'pip install -e "./umrs/src[client]"' in err
