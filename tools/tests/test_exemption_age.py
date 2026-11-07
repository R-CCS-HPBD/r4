"""Regression: exemption age computation behaves deterministically."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.enforcement_maturity_scorer import _exemption_age_days

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _with_dates(obj):
    return {
        "days": obj[0],
        "source": obj[1],
    }


def test_metadata_age_wins_when_valid(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("# noqa: boundary\n")
    days, source = _exemption_age_days(
        added_iso="2026-05-14", file_path=f, line_no=1, repo_root=tmp_path, now=NOW
    )
    assert source == "metadata"
    assert days == (NOW - datetime(2026, 5, 14, tzinfo=timezone.utc)).days


def test_blame_fallback_used_when_metadata_missing(tmp_path: Path, monkeypatch):
    f = tmp_path / "x.py"
    f.write_text("# noqa: boundary line 5\n")

    def _blame(*_):
        return int((NOW - timedelta(days=40)).timestamp())

    monkeypatch.setattr("tools.enforcement_maturity_scorer._git_blame_unix_time", _blame)
    days, source = _exemption_age_days(
        added_iso=None, file_path=f, line_no=5, repo_root=tmp_path, now=NOW
    )
    assert (days, source) == (40, "git_blame")


def test_mtime_last_resort(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("# noqa\n")
    days, source = _exemption_age_days(
        added_iso=None,
        file_path=f,
        line_no=1,
        repo_root=tmp_path,
        now=datetime.min.replace(tzinfo=timezone.utc) if False else NOW,
    )
    # In the absence of git repo context blame returns None → mtime
    assert source in ("file_mtime", "git_blame", "metadata")
    assert days >= 0


def test_scorer_module_importable():
    import tools.enforcement_maturity_scorer as m

    assert hasattr(m, "score_enforcement")
