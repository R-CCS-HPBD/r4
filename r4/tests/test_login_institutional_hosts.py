"""`r4 login` stores the site's institutional-provider allowlist (ADR-062 amendment).

connect-info advertises ``institutional_provider_hosts``; login copies it
onto the platform source (``Source.institutional_hosts``), where the
workstation gate reads it (r4.source_locality). For direct-dial routes
that gate is the only enforcement point, so this leg is pinned end to end
(review TV-2 / R4-5, 2026-10-16):

- a list is stored, lower-cased and stripped;
- ``[]`` (the site has no institutional providers) is kept distinct from
  an absent field;
- a later reply WITHOUT the field leaves the stored list (fail closed: the
  packaged default never comes back by omission);
- entries that are not strings are dropped, never stringified.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from r4._cli import login as login_cli

GW = "https://univ.example"


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, body: dict) -> list:
    """Answer /gw/ops/connect-info with ``body``; returns the requested URLs."""
    seen: list = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        return _Reply(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def _connect_info(**extra) -> dict:
    return {"zmq": {"host": "zmq.univ.example", "control_port": 5555}, **extra}


class TestFetchConnectInfo:
    def test_the_list_is_copied(self, monkeypatch):
        seen = _serve(monkeypatch, _connect_info(institutional_provider_hosts=["llm.univ.ac.jp"]))
        info = login_cli._fetch_connect_info(GW, jwt_token="jwt")
        assert seen == [f"{GW}/gw/ops/connect-info"]
        assert info["_institutional_hosts"] == ["llm.univ.ac.jp"]

    def test_an_empty_list_is_kept(self, monkeypatch):
        _serve(monkeypatch, _connect_info(institutional_provider_hosts=[]))
        info = login_cli._fetch_connect_info(GW)
        assert "_institutional_hosts" in info
        assert info["_institutional_hosts"] == []

    @pytest.mark.parametrize("value", [None, "llm.univ.ac.jp", {"h": 1}])
    def test_absent_or_malformed_is_absent(self, monkeypatch, value):
        body = (
            _connect_info() if value is None else _connect_info(institutional_provider_hosts=value)
        )
        _serve(monkeypatch, body)
        assert "_institutional_hosts" not in login_cli._fetch_connect_info(GW)


def _source(hosts=None):
    return SimpleNamespace(institutional_hosts=hosts)


class TestApplyInstitutionalHosts:
    def test_list_is_stored_normalised(self):
        src = _source()
        assert login_cli._apply_institutional_hosts(
            src, {"_institutional_hosts": [" LLM.Univ.AC.jp ", "b.univ.jp"]}
        )
        assert src.institutional_hosts == ["llm.univ.ac.jp", "b.univ.jp"]

    def test_empty_list_is_stored_not_none(self):
        src = _source()
        assert login_cli._apply_institutional_hosts(src, {"_institutional_hosts": []})
        assert src.institutional_hosts == []

    @pytest.mark.parametrize("stored", [["old.univ.jp"], []])
    def test_absent_field_keeps_the_stored_list(self, stored):
        src = _source(list(stored))
        assert not login_cli._apply_institutional_hosts(src, {})
        assert src.institutional_hosts == stored

    def test_unchanged_list_reports_no_change(self):
        src = _source(["llm.univ.ac.jp"])
        assert not login_cli._apply_institutional_hosts(
            src, {"_institutional_hosts": ["LLM.univ.ac.jp"]}
        )

    def test_non_string_entries_are_dropped(self):
        """``None`` must not become a host called "none", nor 5 a host "5"."""
        src = _source()
        login_cli._apply_institutional_hosts(
            src, {"_institutional_hosts": ["A.example ", 5, None, {"h": 1}, " ", "b.example"]}
        )
        assert src.institutional_hosts == ["a.example", "b.example"]

    def test_zmq_fields_carry_the_list(self):
        src = SimpleNamespace(
            institutional_hosts=None,
            zmq_control_port=None,
            zmq_auth_type=None,
            zmq_tunnel_mode=None,
            zmq_host=None,
        )
        assert login_cli._apply_zmq_fields(src, {"_institutional_hosts": []})
        assert src.institutional_hosts == []


class TestLoginStoresTheSitesList:
    """The whole leg: connect-info reply → the stored platform source → the
    allowlist the workstation gate uses for that deployment."""

    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch):
        monkeypatch.setattr("r4.source.sync_umrs_connection_profile", lambda *a, **k: None)
        monkeypatch.setattr(login_cli, "_apply_discovered_services", lambda *a, **k: None)
        monkeypatch.setattr(login_cli, "_print_zmq_summary", lambda *a, **k: None)
        monkeypatch.setattr(login_cli, "_deliver_s3cfg", lambda *a, **k: None)

    def _login(self, monkeypatch, body):
        _serve(monkeypatch, body)
        login_cli._autodiscover_zmq_ports("univ", GW, "jwt")

    def _stored(self):
        from r4.source import get_source_store

        return get_source_store().get("univ").institutional_hosts

    def test_login_stores_and_the_gate_uses_it(self, monkeypatch):
        from r4.source import Source, get_source_store
        from r4.source_locality import effective_institutional_hosts

        get_source_store().add(Source(name="univ", kind="platform", url=GW))
        self._login(monkeypatch, _connect_info(institutional_provider_hosts=["LLM.univ.ac.jp"]))
        assert self._stored() == ["llm.univ.ac.jp"]
        assert effective_institutional_hosts(GW) == frozenset({"llm.univ.ac.jp"})

    def test_empty_list_survives_and_a_later_absent_field_keeps_it(self, monkeypatch):
        from r4.source import Source, get_source_store
        from r4.source_locality import effective_institutional_hosts

        get_source_store().add(Source(name="univ", kind="platform", url=GW))
        self._login(monkeypatch, _connect_info(institutional_provider_hosts=[]))
        assert self._stored() == []
        self._login(monkeypatch, _connect_info())  # an older server: no field
        assert self._stored() == []
        assert effective_institutional_hosts(GW) == frozenset()

    def test_a_public_source_of_that_name_is_never_written(self, monkeypatch):
        from r4.source import Source, get_source_store

        get_source_store().add(Source(name="univ", kind="public", url=GW))
        self._login(monkeypatch, _connect_info(institutional_provider_hosts=["x.example"]))
        assert self._stored() is None
