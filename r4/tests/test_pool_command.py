"""Tests for `r4 pool up` (MM-4-MA Phase D)."""

from __future__ import annotations

import textwrap
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("R4_CONFIG", raising=False)


def _write_roster(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pool.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def _ns(file: Path, **overrides) -> Namespace:
    base = Namespace(
        pool_command="up",
        file=str(file),
        continue_on_error=False,
        as_json=False,
        dry_run=False,
        force=False,
        default_timeout=None,
        sequential=False,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class TestRosterParsing:
    def test_resolve_entries_applies_defaults(self, tmp_path):
        from r4._cli.pool import _load_roster, _resolve_entries

        path = _write_roster(
            tmp_path,
            """
            pool: dev
            defaults:
              tier: project
            endpoints:
              - name: chat
                model: chat-3b
              - name: coder
                model: coder-7b
                tier: user
            """,
        )
        entries = _resolve_entries(_load_roster(path))
        assert len(entries) == 2
        assert entries[0]["tier"] == "project"  # default applied
        assert entries[1]["tier"] == "user"  # entry override

    def test_missing_endpoints_exits(self, tmp_path):
        from r4._cli.pool import _load_roster, _resolve_entries

        path = _write_roster(tmp_path, "pool: dev\n")
        with pytest.raises(SystemExit) as exc_info:
            _resolve_entries(_load_roster(path))
        assert exc_info.value.code == 2

    def test_missing_model_exits(self, tmp_path):
        from r4._cli.pool import _load_roster, _resolve_entries

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
            """,
        )
        with pytest.raises(SystemExit) as exc_info:
            _resolve_entries(_load_roster(path))
        assert exc_info.value.code == 2


class TestPoolUp:
    def test_dry_run_does_not_call_client(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            pool: dev
            endpoints:
              - name: chat
                model: chat-3b
            """,
        )
        with patch("r4.client.Client") as ClientCls:
            rc = _cmd_pool_up(_ns(path, dry_run=True))
            ClientCls.assert_not_called()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Pool: dev" in out
        assert "chat-3b" in out

    def test_provisions_each_entry_sequentially(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
              - name: coder
                model: coder-7b
                tier: project
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = [
            {"instance_id": "ep-1", "status": "ready"},
            {"instance_id": "ep-2", "status": "ready"},
        ]
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path, sequential=True))
        assert rc == 0
        assert fake_client.provision_endpoint.call_count == 2
        first_kwargs = fake_client.provision_endpoint.call_args_list[0].kwargs
        assert first_kwargs["model"] == "chat-3b"
        assert first_kwargs["tier"] == "user"  # default for first entry
        assert first_kwargs["wait"] is True
        second_kwargs = fake_client.provision_endpoint.call_args_list[1].kwargs
        assert second_kwargs["tier"] == "project"
        out = capsys.readouterr().out
        assert "ep-1" in out and "ep-2" in out

    def test_provisions_in_parallel_by_default(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
              - name: coder
                model: coder-7b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = [
            {"instance_id": "ep-1", "status": "ready"},
            {"instance_id": "ep-2", "status": "ready"},
        ]
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path))  # sequential=False (default)
        assert rc == 0
        assert fake_client.provision_endpoint.call_count == 2
        assert all(
            call.kwargs["wait"] is False for call in fake_client.provision_endpoint.call_args_list
        )
        out = capsys.readouterr().out
        assert "parallel" in out.lower()
        assert "ep-1" in out and "ep-2" in out

    def test_warns_about_existing_pool_state(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.return_value = {
            "instance_id": "ep-1",
            "status": "ready",
        }

        with patch("r4.client.Client", return_value=fake_client):
            # First provision — creates state
            rc1 = _cmd_pool_up(_ns(path))
            assert rc1 == 0
            capsys.readouterr()  # drain

            # Second provision — should warn about existing state
            fake_client.provision_endpoint.return_value = {
                "instance_id": "ep-2",
                "status": "ready",
            }
            rc2 = _cmd_pool_up(_ns(path))
            assert rc2 == 0
            out = capsys.readouterr().out
            assert "pool state already exists" in out
            assert "ep-1" in out  # shows old instance id

    def test_progress_counter_in_parallel(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: a
                model: a
              - name: b
                model: b
              - name: c
                model: c
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = [
            {"instance_id": "ep-1", "status": "ready"},
            {"instance_id": "ep-2", "status": "ready"},
            {"instance_id": "ep-3", "status": "ready"},
        ]
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path))
        assert rc == 0
        out = capsys.readouterr().out
        # Should contain progress counters like [1/3], [2/3], [3/3]
        assert "/3]" in out
        assert "[3/3]" in out

    def test_progress_counter_in_sequential(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: a
                model: a
              - name: b
                model: b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = [
            {"instance_id": "ep-1", "status": "ready"},
            {"instance_id": "ep-2", "status": "ready"},
        ]
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path, sequential=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "[1/2]" in out
        assert "[2/2]" in out

    def test_forwards_entry_max_model_len(self, tmp_path):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
                max_model_len: 32k
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.return_value = {
            "instance_id": "ep-1",
            "status": "ready",
        }
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path))
        assert rc == 0
        assert fake_client.provision_endpoint.call_args.kwargs["max_model_len"] == 32768

    def test_forwards_numeric_entry_max_model_len(self, tmp_path):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
                max_model_len: 32768
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.return_value = {
            "instance_id": "ep-1",
            "status": "ready",
        }
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path))
        assert rc == 0
        assert fake_client.provision_endpoint.call_args.kwargs["max_model_len"] == 32768

    def test_forwards_entry_nodelist(self, tmp_path):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
                nodelist: dgx-spark-1
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.return_value = {
            "instance_id": "ep-1",
            "status": "ready",
        }
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path))
        assert rc == 0
        assert fake_client.provision_endpoint.call_args.kwargs["nodelist"] == "dgx-spark-1"

    def test_aborts_on_first_failure_by_default(self, tmp_path, capsys):
        """Sequential mode aborts on first failure."""
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: chat
                model: chat-3b
              - name: coder
                model: coder-7b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = RuntimeError("boom")
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path, sequential=True))
        assert rc == 1
        # Should bail after the first failure, not call the second.
        assert fake_client.provision_endpoint.call_count == 1
        assert "FAILED" in capsys.readouterr().out

    def test_continue_on_error_provisions_remaining_entries(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            endpoints:
              - name: a
                model: a
              - name: b
                model: b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = [
            RuntimeError("nope"),
            {"instance_id": "ep-2", "status": "ready"},
        ]
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path, continue_on_error=True, sequential=True))
        assert rc == 1  # at least one failure
        assert fake_client.provision_endpoint.call_count == 2
        out = capsys.readouterr().out
        assert "FAILED" in out
        assert "ep-2" in out

    def test_json_output(self, tmp_path, capsys):
        import json as _json

        from r4._cli.pool import _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            pool: x
            endpoints:
              - name: chat
                model: chat-3b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.return_value = {
            "instance_id": "ep-1",
            "status": "ready",
        }
        with patch("r4.client.Client", return_value=fake_client):
            rc = _cmd_pool_up(_ns(path, as_json=True))
        assert rc == 0
        payload = _json.loads(capsys.readouterr().out)
        assert payload["pool"] == "x"
        assert payload["results"][0]["instance_id"] == "ep-1"


class TestPoolDown:
    def test_pool_down_uses_saved_state(self, tmp_path, capsys):
        from r4._cli.pool import _cmd_pool_down, _cmd_pool_up

        path = _write_roster(
            tmp_path,
            """
            pool: dev
            endpoints:
              - name: chat
                model: chat-3b
              - name: coder
                model: coder-7b
            """,
        )
        fake_client = MagicMock()
        fake_client.provision_endpoint.side_effect = [
            {"instance_id": "ep-1", "status": "ready"},
            {"instance_id": "ep-2", "status": "ready"},
        ]
        fake_client.stop_endpoint.side_effect = [
            {"status": "stopped"},
            {"status": "stopped"},
        ]

        with patch("r4.client.Client", return_value=fake_client):
            assert _cmd_pool_up(_ns(path)) == 0
            rc = _cmd_pool_down(_ns(path, pool_command="down"))

        assert rc == 0
        assert fake_client.stop_endpoint.call_args_list[0].args[0] == "ep-1"
        assert fake_client.stop_endpoint.call_args_list[1].args[0] == "ep-2"
        out = capsys.readouterr().out
        assert "stop summary" in out.lower()


class TestRegistration:
    def test_pool_command_registered(self):
        """PoolCommand is registered as a top-level command."""
        from r4._cli.registry import registry

        assert "pool" in registry


class TestProvisionReconciliation:
    """Reconcile a failed HTTP submission against UMRS endpoint registry."""

    def _make_entry(self, name="ep1", model="m-A", tier="user"):
        return {
            "name": name,
            "model": model,
            "tier": tier,
            "models": [model],
            "serving_mode": "single",
            "loras": None,
            "max_model_len": None,
        }

    def test_reconciliation_recovers_submitted_endpoint_on_timeout(self):
        """If provision_endpoint raises but UMRS has the job, treat as submitted."""
        from datetime import datetime, timezone

        from r4._cli.pool import _provision_one_entry

        client = MagicMock()
        client.provision_endpoint.side_effect = TimeoutError("read timeout")
        # Endpoint shows up in list_endpoints with a fresh created_at.
        now_iso = datetime.now(timezone.utc).isoformat()
        client.list_endpoints.return_value = {
            "endpoints": [
                {
                    "instance_id": "umrs-job-42",
                    "job_id": "umrs-job-42",
                    "model": "m-A",
                    "tier": "user",
                    "status": "provisioning",
                    "created_at": now_iso,
                }
            ]
        }

        result = _provision_one_entry(client, self._make_entry(), default_timeout=600, wait=False)

        assert result["ok"] is True
        assert result["state"] == "submitted"
        assert result["reconciled"] is True
        assert result["instance_id"] == "umrs-job-42"
        assert "warning" in result

    def test_reconciliation_returns_failed_when_no_match(self):
        """Without a matching record, the entry is reported as failed."""
        from r4._cli.pool import _provision_one_entry

        client = MagicMock()
        client.provision_endpoint.side_effect = RuntimeError("connection refused")
        client.list_endpoints.return_value = {"endpoints": []}

        result = _provision_one_entry(client, self._make_entry(), default_timeout=600, wait=False)

        assert result["ok"] is False
        assert result["state"] == "failed"
        assert "connection refused" in result["error"]

    def test_reconciliation_ignores_old_endpoints(self):
        """An endpoint created long before the submission is not a match."""
        from datetime import datetime, timedelta, timezone

        from r4._cli.pool import _provision_one_entry

        client = MagicMock()
        client.provision_endpoint.side_effect = RuntimeError("timeout")
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        client.list_endpoints.return_value = {
            "endpoints": [
                {
                    "instance_id": "old-job",
                    "model": "m-A",
                    "tier": "user",
                    "status": "ready",
                    "created_at": old_iso,
                }
            ]
        }

        result = _provision_one_entry(client, self._make_entry(), default_timeout=600, wait=False)

        assert result["ok"] is False
        assert result["state"] == "failed"

    def test_normal_submission_yields_submitted_state(self):
        """Successful provision (status=provisioning) maps to state=submitted."""
        from r4._cli.pool import _provision_one_entry

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "umrs-job-7",
            "status": "provisioning",
            "endpoint_url": None,
        }

        result = _provision_one_entry(client, self._make_entry(), default_timeout=600, wait=False)

        assert result["ok"] is True
        assert result["state"] == "submitted"
        assert result.get("reconciled") is None
        assert result["instance_id"] == "umrs-job-7"


class TestStopEndpointRetry:
    """stop_endpoint retries on 404 to absorb the registration race."""

    def test_stop_endpoint_retries_on_404(self, monkeypatch):
        from r4 import client as _client_mod
        from r4.errors import APIError

        c = _client_mod.Client.__new__(_client_mod.Client)
        c._config = MagicMock(gateway_url="http://gw")
        fake_http = MagicMock()
        fake_http.delete.side_effect = [
            APIError("not found", status_code=404, response_body=""),
            APIError("not found", status_code=404, response_body=""),
            {"instance_id": "ep1", "status": "deprovisioned"},
        ]
        c._http_core = fake_http
        # Avoid 4s of real sleep.
        monkeypatch.setattr("time.sleep", lambda *_: None)

        result = c.stop_endpoint("ep1")
        assert result["status"] == "deprovisioned"
        assert fake_http.delete.call_count == 3
        assert (
            fake_http.delete.call_args_list[0].args[0] == "http://gw/gw/endpoints/ep1/deprovision"
        )

    def test_stop_endpoint_does_not_retry_on_500(self, monkeypatch):
        from r4 import client as _client_mod
        from r4.errors import APIError, R4Error

        c = _client_mod.Client.__new__(_client_mod.Client)
        c._config = MagicMock(gateway_url="http://gw")
        fake_http = MagicMock()
        fake_http.delete.side_effect = APIError("boom", status_code=500, response_body="")
        c._http_core = fake_http
        monkeypatch.setattr("time.sleep", lambda *_: None)

        with pytest.raises(R4Error):
            c.stop_endpoint("ep1")
        assert fake_http.delete.call_count == 1
        assert fake_http.delete.call_args.args[0] == "http://gw/gw/endpoints/ep1/deprovision"
