"""R1-6: source push/unpush CLI flows + remote badges (mocked server client)."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock, patch

from r4._cli.source import (
    SourceCommand,
    _key_fingerprint,
    _remote_badge,
)
from r4.source import Source, SourceStore


def _source_env(tmp_path, monkeypatch, sources: list[Source]):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = SourceStore()
    for src in sources:
        store.add(src)
    return store


def _args(**kw):
    return Namespace(**kw)


class TestRemoteBadge:
    def test_states(self):
        src = Source(
            name="openai-main", kind="public", url="https://api.openai.com", api_key="sk-a"
        )
        fp = _key_fingerprint("sk-a")
        assert _remote_badge(src, {"openai-main": {"key_fingerprint": fp}}) == "pushed"
        assert (
            _remote_badge(src, {"openai-main": {"key_fingerprint": "sha256:aaaaaaaa"}}) == "stale"
        )
        assert (
            _remote_badge(
                src,
                {"openai-main": {"key_fingerprint": fp, "tombstoned_at": "2026-09-09T00:00:00"}},
            )
            == "tombstoned"
        )
        assert _remote_badge(src, {}) == "-"
        assert _remote_badge(src, None) == "?"

    def test_no_local_key_still_shows_pushed(self):
        src = Source(name="x", kind="public", url="https://x", api_key=None)
        assert _remote_badge(src, {"x": {"key_fingerprint": "sha256:abcd1234"}}) == "pushed"


class TestPush:
    def _mocks(self, result):
        """Returns (src push mock). Tests set via _install_patches."""
        us = MagicMock()
        us.push.return_value = result
        us.unpush.return_value = result
        return us

    def test_happy_path_confirmed_prompt(self, tmp_path, monkeypatch, capsys):
        _source_env(
            tmp_path,
            monkeypatch,
            [
                Source(
                    name="openai-main",
                    kind="public",
                    url="https://api.example.com/v1",
                    api_key="sk-1",
                )
            ],
        )
        us = self._mocks(
            {
                "id": "43",
                "allow_host": "https://api.example.com",
                "key_fingerprint": _key_fingerprint("sk-1"),
                "seal_key_id": "v1:abc",
                "replaced": False,
            }
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_push(
                _args(name="openai-main", allow_paths="", yes=False)
            )
        assert code == 0
        us.push.assert_called_once()
        kwargs = us.push.call_args.kwargs
        assert kwargs["name"] == "openai-main"
        assert kwargs["provider_kind"] == "openai_compat"
        assert kwargs["api_key"] == "sk-1"
        assert "Pushed 'openai-main'" in capsys.readouterr().out

    def test_prompt_abort(self, tmp_path, monkeypatch, capsys):
        _source_env(
            tmp_path,
            monkeypatch,
            [
                Source(
                    name="openai-main", kind="public", url="https://api.example.com", api_key="sk-1"
                )
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
        code = SourceCommand()._handle_push(_args(name="openai-main", allow_paths="", yes=False))
        assert code == 1
        assert "Aborted" in capsys.readouterr().out

    def test_yes_flag_skips_prompt(self, tmp_path, monkeypatch):
        _source_env(
            tmp_path,
            monkeypatch,
            [Source(name="x", kind="public", url="https://api.example.com", api_key="sk-1")],
        )
        us = self._mocks(
            {
                "id": "1",
                "allow_host": "https://api.example.com",
                "key_fingerprint": _key_fingerprint("sk-1"),
                "seal_key_id": "v1:abc",
                "replaced": True,
            }
        )
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_push(
                _args(name="x", allow_paths="files,batches", yes=True)
            )
        assert code == 0
        assert us.push.call_args.kwargs["allow_paths"] == ["files", "batches"]

    def test_refuses_source_without_key(self, tmp_path, monkeypatch, capsys):
        _source_env(
            tmp_path, monkeypatch, [Source(name="x", kind="public", url="https://api.example.com")]
        )
        code = SourceCommand()._handle_push(_args(name="x", allow_paths="", yes=True))
        assert code == 1
        assert "no api_key" in capsys.readouterr().err

    def test_refuses_credentialed_http(self, tmp_path, monkeypatch, capsys):
        _source_env(
            tmp_path,
            monkeypatch,
            [Source(name="x", kind="public", url="https://api.example.com", api_key="sk-1")],
        )
        http_src = Source(
            name="bad",
            kind="http-typo" if False else "public",
            url="http://api.example.com",
            api_key="sk-1",
        )
        _source_env(tmp_path, monkeypatch, [http_src])
        code = SourceCommand()._handle_push(_args(name="bad", allow_paths="", yes=True))
        assert code == 1
        assert "refusing to push" in capsys.readouterr().err

    def test_not_found(self, tmp_path, monkeypatch, capsys):
        _source_env(tmp_path, monkeypatch, [])
        code = SourceCommand()._handle_push(_args(name="ghost", allow_paths="", yes=True))
        assert code == 1
        assert "not found" in capsys.readouterr().err


class TestUnpush:
    def test_prints_revoke_count(self, capsys):
        us = MagicMock()
        us.unpush.return_value = {"leases_revoked": 3}
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_unpush(_args(name="openai-main"))
        assert code == 0
        out = capsys.readouterr().out
        assert "leases revoked: 3" in out or "leases revoked=3" in out or "3" in out


class TestAuditVerb:
    def _args(self, **kw):
        base = {"name": "openai-main", "limit": 20, "as_json": False}
        base.update(kw)
        return Namespace(**base)

    def _client(self, rows, entries):
        us = MagicMock()
        us.list_mine.return_value = rows
        us.audit.return_value = entries
        return us

    def test_audit_renders_rows(self, capsys):
        rows = [
            {
                "name": "openai-main",
                "id": "src-1",
                "allow_host": "https://api.example.com",
                "key_fingerprint": "sha256:deadbeef",
            }
        ]
        entries = [
            {
                "created_at": "2026-09-09T12:00:00",
                "action": "push",
                "actor_type": "user",
                "detail": {"replaced": False},
            },
            {
                "created_at": "2026-09-09T12:05:00",
                "action": "unpush",
                "actor_type": "user",
                "detail": {"leases_revoked": 1},
            },
        ]
        us = self._client(rows, entries)
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_audit(self._args())
        assert code == 0
        out = capsys.readouterr().out
        assert "audit: openai-main" in out
        assert "push" in out and "unpush" in out
        assert '"leases_revoked":1' in out.replace(" ", "")

    def test_not_pushed_error(self, capsys):
        us = self._client([], [])
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_audit(self._args(name="ghost"))
        assert code == 1
        assert "not pushed" in capsys.readouterr().err

    def test_json_output(self, capsys):
        rows = [{"name": "openai-main", "id": "src-1", "allow_host": "x"}]
        us = self._client(rows, [{"action": "push"}])
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_audit(self._args(as_json=True))
        assert code == 0
        assert '"push"' in capsys.readouterr().out


class TestLeaseCountdown:
    def test_countdown_formats(self):
        from datetime import datetime, timedelta, timezone

        from r4._cli.source import _lease_countdown

        now = datetime.now(timezone.utc)
        soon = (now + timedelta(minutes=47)).isoformat()
        far = (now + timedelta(hours=2, minutes=5)).isoformat()
        assert _lease_countdown({"next_lease_expiry": soon}, _now=now) == "47m"
        assert _lease_countdown({"next_lease_expiry": far}, _now=now) == "2h"
        assert _lease_countdown({}) is None
        assert _lease_countdown({"next_lease_expiry": "not-a-date"}, _now=now) is None

    def test_badge_carries_countdown_only_with_live_leases(self):
        from datetime import datetime, timedelta, timezone

        from r4._cli.source import _remote_badge

        src = Source(name="x", kind="public", url="https://x", api_key="sk-a")
        row = {
            "key_fingerprint": _key_fingerprint("sk-a"),
            "next_lease_expiry": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        }
        badge = _remote_badge(src, {"x": row})
        assert badge == "pushed"  # no active_leases count → no countdown
        row["active_leases"] = 1
        badge = _remote_badge(src, {"x": row})
        assert badge.startswith("pushed (")
        assert badge.endswith("h)") or badge.endswith("m)")

    def test_stale_carries_countdown_too(self):
        from datetime import datetime, timedelta, timezone

        from r4._cli.source import _remote_badge

        src = Source(name="x", kind="public", url="https://x", api_key="sk-a")
        row = {
            "key_fingerprint": "sha256:wrong",
            "next_lease_expiry": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "active_leases": 1,
        }
        badge = _remote_badge(src, {"x": row})
        assert badge.startswith("stale (")


class TestListRemote:
    def test_json_rows_carry_remote(self, tmp_path, monkeypatch, capsys):
        src = Source(
            name="openai-main", kind="public", url="https://api.example.com", api_key="sk-1"
        )
        _source_env(tmp_path, monkeypatch, [src])
        us = MagicMock()
        us.list_mine.return_value = [
            {"name": "openai-main", "key_fingerprint": _key_fingerprint("diff")}
        ]
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_list(_args(as_json=True, remote=True))
        assert code == 0
        import json as _json

        rows = _json.loads(capsys.readouterr().out)
        assert rows[0]["remote"] == "stale"

    def test_remote_unreachable_badge_is_labeled(self, tmp_path, monkeypatch, capsys):
        src = Source(
            name="openai-main", kind="public", url="https://api.example.com", api_key="sk-1"
        )
        _source_env(tmp_path, monkeypatch, [src])
        with patch("r4.user_sources.UserSourcesClient", side_effect=RuntimeError("gateway down")):
            code = SourceCommand()._handle_list(_args(as_json=True, remote=True))
        assert code == 0
        import json as _json

        captured = capsys.readouterr()
        rows = _json.loads(captured.out)
        assert rows[0]["remote"] == "?"
        assert "unreachable" in captured.err

    def test_table_remote_column(self, tmp_path, monkeypatch, capsys):
        src = Source(
            name="openai-main", kind="public", url="https://api.example.com", api_key="sk-1"
        )
        _source_env(tmp_path, monkeypatch, [src])
        us = MagicMock()
        us.list_mine.return_value = [
            {"name": "openai-main", "key_fingerprint": _key_fingerprint("sk-1")}
        ]
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            code = SourceCommand()._handle_list(_args(as_json=False, remote=True))
        assert code == 0
        out = capsys.readouterr().out
        assert "REMOTE" in out
        assert "pushed" in out


class TestPushJson:
    def test_push_json_shape(self, tmp_path, monkeypatch, capsys):
        import json

        _source_env(
            tmp_path,
            monkeypatch,
            [
                Source(
                    name="openai-main",
                    kind="public",
                    url="https://api.example.com/v1",
                    api_key="sk-1",
                )
            ],
        )
        us = MagicMock()
        us.push.return_value = {
            "allow_host": "https://api.example.com",
            "key_fingerprint": _key_fingerprint("sk-1"),
            "seal_key_id": "v1:abc",
            "replaced": True,
        }
        with (
            patch("r4.user_sources.UserSourcesClient", return_value=us),
            patch("r4.client.Client"),
        ):
            rc = SourceCommand().handle(
                _args(
                    source_command="push",
                    name="openai-main",
                    allow_paths="",
                    yes=True,
                    as_json=True,
                )
            )
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["ok"] is True and out["name"] == "openai-main" and out["replaced"] is True
        assert out["key_fingerprint"] == _key_fingerprint("sk-1")

    def test_unpush_json_shape(self, tmp_path, monkeypatch, capsys):
        import json

        _source_env(tmp_path, monkeypatch, [])
        us = MagicMock()
        us.unpush.return_value = {"leases_revoked": 3}
        with (
            patch("r4.user_sources.UserSourcesClient", return_value=us),
            patch("r4.client.Client"),
        ):
            rc = SourceCommand().handle(
                _args(source_command="unpush", name="openai-main", as_json=True)
            )
        assert rc == 0
        assert json.loads(capsys.readouterr().out.strip()) == {
            "ok": True,
            "name": "openai-main",
            "leases_revoked": 3,
        }
