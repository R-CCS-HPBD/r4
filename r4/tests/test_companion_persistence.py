"""Tests for the companion persistence protocol (Plan 5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from r4.companion.persistence import (
    NullPersistence,
    Persistence,
    SQLitePersistence,
    UMRSSyncPersistence,
    get_default_persistence,
)


def test_null_persistence_drops_save_and_load() -> None:
    p = NullPersistence()
    session = MagicMock()
    p.save(session)  # should not raise
    assert p.load("anything", MagicMock()) is None


def test_null_persistence_matches_protocol() -> None:
    assert isinstance(NullPersistence(), Persistence)


def test_sqlite_persistence_matches_protocol(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    assert isinstance(SQLitePersistence(db_path=db), Persistence)


def test_sqlite_persistence_exposes_store(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    p = SQLitePersistence(db_path=db)
    assert p.store.db_path == db


def test_umrs_sync_mirrors_on_save(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    mirror = MagicMock()
    p = UMRSSyncPersistence(db_path=db, sync_callable=mirror)
    session = MagicMock()
    # Force save to no-op locally by mocking the local store
    p._local = MagicMock()
    p.save(session)
    p._local.save.assert_called_once_with(session)
    mirror.assert_called_once_with(session)


def test_umrs_sync_swallows_mirror_errors(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"

    def _broken(_session):
        raise RuntimeError("umrs down")

    p = UMRSSyncPersistence(db_path=db, sync_callable=_broken)
    p._local = MagicMock()
    session = MagicMock()
    # Must not raise even if the mirror call fails.
    p.save(session)
    p._local.save.assert_called_once_with(session)


def test_get_default_persistence_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("R4_COMPANION_UMRS_SYNC", raising=False)
    p = get_default_persistence(db_path=tmp_path / "s.db")
    assert isinstance(p, SQLitePersistence)


def test_get_default_persistence_with_umrs_sync(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("R4_COMPANION_UMRS_SYNC", "1")
    p = get_default_persistence(db_path=tmp_path / "s.db")
    assert isinstance(p, UMRSSyncPersistence)
