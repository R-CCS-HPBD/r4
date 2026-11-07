"""D-16 pins: login no longer writes per-user s3cfg by default; the legacy
file survives only behind R4_WRITE_S3CFG=1 (transition window)."""

from __future__ import annotations

_CONTENT = "[default]\naccess_key = AKIA_LEGACY\nsecret_key = sekret\n"
_KW = {
    "profile": "default",
    "s3cfg_content": _CONTENT,
    "username": "alice",
    "services": None,
}


def test_s3cfg_write_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("R4_WRITE_S3CFG", raising=False)
    monkeypatch.setattr("r4.source._resolve_umrs_cfg_dir", lambda: tmp_path, raising=False)
    import r4._cli.login as m

    wrote = m._write_s3cfg_from_connect_info(**_KW)
    assert wrote is False
    assert not (tmp_path / "s3cfg_alice").exists()


def test_s3cfg_write_opt_in_transition_window(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_WRITE_S3CFG", "1")
    monkeypatch.setattr("r4.source._resolve_umrs_cfg_dir", lambda: tmp_path, raising=False)
    import r4._cli.login as m

    wrote = m._write_s3cfg_from_connect_info(**_KW)
    assert wrote is True
    assert "AKIA_LEGACY" in (tmp_path / "s3cfg_alice").read_text()


def test_skip_message_names_d16_and_umrs_path(caplog, monkeypatch, tmp_path):
    import logging

    monkeypatch.delenv("R4_WRITE_S3CFG", raising=False)
    import r4._cli.login as m

    with caplog.at_level(logging.INFO):
        m._write_s3cfg_from_connect_info(**_KW)
    assert any("D-16" in r.getMessage() and "UMRS" in r.getMessage() for r in caplog.records)
