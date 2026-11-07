"""H-1 (r4-client lane): the client fails CLOSED when the platform denies
the gated project's row.

Since H-1 the gateway answers 403/404 for a project the caller reaches only
through an unsigned membership, or a foreign one. Before this fix the
ADR-062 gate read that answer as "no class" and let a public route through,
and the research loop read it as "no wall-clock cap" and ran uncapped.
Now: a denied project row reads as ``sensitive`` (ADR-062's default class)
and a denied budget row refuses the loop start. An unreachable platform and
no active project keep the old, non-blocking answers.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from r4.client import Client
from r4.errors import APIError, AuthenticationError, R4Error


def _config(**over):
    cfg = SimpleNamespace(
        model="test-model",
        gateway_url="https://gw.example",
        api_key="jwt",
        endpoint_url=None,
        endpoint_provider=None,
        model_routes={},
    )
    for key, value in over.items():
        setattr(cfg, key, value)
    return cfg


class _FakePM:
    """The local project registry: ``current`` is active; ``records`` maps an
    id to its recorded gateway URL (None: recorded locally only). An id with
    no record at all came from elsewhere (env, login) and is expected on the
    platform."""

    def __init__(self, current, records=None):
        self._current = current
        self._records = records or {}

    def get_current(self):
        return self._current

    def get(self, project_id):
        if project_id not in self._records:
            return None
        return SimpleNamespace(project_id=project_id, gateway_url=self._records[project_id])


def _patch_pm(monkeypatch, current, records=None):
    monkeypatch.setattr("r4.project.get_project_manager", lambda: _FakePM(current, records))


def _client_on(monkeypatch, project, raised, records=None, **over):
    """A client whose gated project is ``project`` and whose GET raises."""
    _patch_pm(monkeypatch, project, records)
    client = Client(config=_config(**over))
    calls = []

    def denied(path, **kw):
        calls.append(path)
        raise raised

    monkeypatch.setattr(client, "get", denied)
    return client, calls


# What r4._http.raise_for_status raises for each denial.
_FORBIDDEN = AuthenticationError("Access denied (403)", status_code=403)
_NOT_FOUND = APIError("not found", status_code=404)


class TestDataClassFailsClosed:
    @pytest.mark.parametrize("raised", [_FORBIDDEN, _NOT_FOUND], ids=["403", "404"])
    def test_denied_row_reads_as_sensitive(self, monkeypatch, raised):
        client, _calls = _client_on(monkeypatch, "p-foreign", raised)
        assert client._project_data_class() == "sensitive"

    def test_denial_is_cached_like_any_answer(self, monkeypatch):
        client, calls = _client_on(monkeypatch, "p-foreign", _NOT_FOUND)
        assert client._project_data_class() == "sensitive"
        assert client._project_data_class() == "sensitive"
        assert calls == ["/gw/projects/p-foreign"]  # one GET per TTL window
        assert client._data_class_cache["p-foreign"][1] == "sensitive"

    def test_denial_logs_a_warning_naming_the_project(self, monkeypatch, caplog):
        client, _calls = _client_on(monkeypatch, "p-foreign", _FORBIDDEN)
        with caplog.at_level(logging.WARNING, logger="r4.client"):
            client._project_data_class()
        assert any("p-foreign" in r.getMessage() for r in caplog.records)

    def test_denied_project_refuses_a_public_route(self, monkeypatch):
        """End to end through the gate: the public route is refused, not dialled."""
        client, _calls = _client_on(
            monkeypatch, "p-foreign", _FORBIDDEN, endpoint_url="https://api.openai.com/v1"
        )
        with pytest.raises(R4Error, match="ADR-062"):
            client._normalize_route({"model": "gpt-5"})

    def test_unreachable_platform_still_never_blocks(self, monkeypatch):
        from r4.errors import R4ConnectionError

        client, _calls = _client_on(monkeypatch, "p1", R4ConnectionError("gw down"))
        assert client._project_data_class() == ""

    def test_server_error_is_not_a_denial(self, monkeypatch):
        client, _calls = _client_on(monkeypatch, "p1", APIError("boom", status_code=500))
        assert client._project_data_class() == ""

    def test_no_active_project_still_empty(self, monkeypatch):
        client, calls = _client_on(monkeypatch, None, _FORBIDDEN)
        assert client._project_data_class() == ""
        assert calls == []

    def test_recorded_platform_project_404_reads_as_sensitive(self, monkeypatch):
        """A project recorded against a gateway (``r4 config --project``,
        ``r4 project create --gateway``) is expected there: its 404 is a denial."""
        records = {"p-member": "https://gw.example"}
        client, _calls = _client_on(monkeypatch, "p-member", _NOT_FOUND, records=records)
        assert client._project_data_class() == "sensitive"


class TestLocalOnlyProject:
    """Review fix (2026-10-18): a project recorded only locally (``r4 project
    create`` without --gateway, the companion's project_ensure, ACP) has no
    server row; the server's 404 for it is "no row", which the ADR-062
    amendment admits ("Known limits": no row -> admit). Only a 403, which says
    the row exists, is a denial for it."""

    _LOCAL = {"mermaids-seismic": None}

    def test_local_only_404_is_no_row(self, monkeypatch):
        client, _calls = _client_on(
            monkeypatch, "mermaids-seismic", _NOT_FOUND, records=self._LOCAL
        )
        assert client._project_data_class() == ""

    def test_local_only_404_keeps_public_routes(self, monkeypatch):
        client, _calls = _client_on(
            monkeypatch,
            "mermaids-seismic",
            _NOT_FOUND,
            records=self._LOCAL,
            endpoint_url="https://api.openai.com/v1",
        )
        _payload, route = client._normalize_route({"model": "gpt-5"})  # not refused
        assert route.base_url.startswith("https://api.openai.com")

    def test_local_only_403_is_still_a_denial(self, monkeypatch):
        client, _calls = _client_on(
            monkeypatch, "mermaids-seismic", _FORBIDDEN, records=self._LOCAL
        )
        assert client._project_data_class() == "sensitive"


class TestDenialRefusalWording:
    """Review fix (2026-10-18): `r4 project data-class set` cannot work on a
    row the platform denies (PATCH answers 403/404 for the same caller), so a
    denial's refusal must not point at it."""

    def test_denial_refusal_names_the_project_and_the_real_remedy(self, monkeypatch):
        client, _calls = _client_on(
            monkeypatch, "p-foreign", _NOT_FOUND, endpoint_url="https://api.openai.com/v1"
        )
        with pytest.raises(R4Error) as exc:
            client._normalize_route({"model": "gpt-5"})
        message = str(exc.value)
        assert "data-class set" not in message
        assert "p-foreign" in message
        assert "HTTP 404" in message
        assert "r4 project switch" in message

    def test_real_sensitive_row_still_suggests_widening(self, monkeypatch):
        _patch_pm(monkeypatch, "p1")
        client = Client(config=_config(endpoint_url="https://api.openai.com/v1"))
        monkeypatch.setattr(
            client,
            "get",
            lambda path, **kw: {"status": "ok", "project": {"data_class": "sensitive"}},
        )
        with pytest.raises(R4Error, match="r4 project data-class set p1 non_sensitive"):
            client._normalize_route({"model": "gpt-5"})


class _BudgetClient:
    def __init__(self, raised=None, payload=None):
        self._raised = raised
        self._payload = payload

    def get(self, path, **kw):
        assert path == "/gw/projects/p-foreign/budget"
        if self._raised is not None:
            raise self._raised
        return self._payload


class TestWallClockFailsClosed:
    @pytest.fixture(autouse=True)
    def _platform_project(self, monkeypatch):
        """p-foreign has no local record: it is expected on the platform."""
        _patch_pm(monkeypatch, None)

    @pytest.mark.parametrize("raised", [_FORBIDDEN, _NOT_FOUND], ids=["403", "404"])
    def test_denied_budget_refuses_the_start(self, raised):
        from r4.companion.research_loop_tools import _project_wall_clock_hours

        with pytest.raises(R4Error) as exc:
            _project_wall_clock_hours(_BudgetClient(raised=raised), "p-foreign")
        message = str(exc.value)
        assert "p-foreign" in message
        assert "refus" in message.lower()

    def test_unreachable_budget_stays_uncapped(self):
        from r4.companion.research_loop_tools import _project_wall_clock_hours
        from r4.errors import R4ConnectionError

        client = _BudgetClient(raised=R4ConnectionError("gw down"))
        assert _project_wall_clock_hours(client, "p-foreign") == 0.0

    def test_readable_budget_is_the_cap(self):
        from r4.companion.research_loop_tools import _project_wall_clock_hours

        client = _BudgetClient(payload={"budget": {"max_wall_clock_hours": 2.5}})
        assert _project_wall_clock_hours(client, "p-foreign") == 2.5

    def test_denied_budget_with_a_caller_cap_runs_under_that_cap(self):
        """Review fix (2026-10-18): a caller cap means the loop is not uncapped,
        so the denial does not refuse it: the caller's cap applies."""
        from r4.companion.research_loop_tools import _iteration_wiring
        from r4.runtime.umrs import UmrsRuntime

        client = _BudgetClient(raised=_NOT_FOUND)
        runtime = object.__new__(UmrsRuntime)
        policy = {"mode": "auto", "triggers": [], "max_wall_clock_hours": 2.0}
        _executor, fields = _iteration_wiring(
            client, "p-foreign", runtime, "research-round", {}, policy
        )
        assert fields["max_wall_clock_hours"] == 2.0

    def test_denied_budget_without_a_caller_cap_is_refused_through_wiring(self):
        from r4.companion.research_loop_tools import _iteration_wiring
        from r4.runtime.umrs import UmrsRuntime

        client = _BudgetClient(raised=_NOT_FOUND)
        runtime = object.__new__(UmrsRuntime)
        policy = {"mode": "auto", "triggers": [], "max_wall_clock_hours": None}
        with pytest.raises(R4Error, match="max_wall_clock_hours"):
            _iteration_wiring(client, "p-foreign", runtime, "research-round", {}, policy)

    def test_local_only_project_404_stays_uncapped(self, monkeypatch):
        """A project recorded only locally has no server row: 404 is "no
        budget", uncapped as before H-1 (offline local loops keep working)."""
        from r4.companion.research_loop_tools import _project_wall_clock_hours

        _patch_pm(monkeypatch, None, {"p-foreign": None})
        client = _BudgetClient(raised=_NOT_FOUND)
        assert _project_wall_clock_hours(client, "p-foreign") == 0.0


class _LoopClient(_BudgetClient):
    """What research_loop_start reads besides the budget."""

    config = SimpleNamespace(project_id=None)

    def get_platform_project(self, project_id):
        return {}


def test_research_loop_start_refuses_a_denied_budget_before_starting(monkeypatch, tmp_path):
    """Through the tool (plan path): the reply is a structured start failure
    naming the project, and the runtime never starts the loop."""
    import json

    from r4.companion import research_loop_tools as rlt
    from r4.runtime import LocalRuntime

    _patch_pm(monkeypatch, None)
    started = []

    class _Runtime(LocalRuntime):
        def start_loop(self, spec, **kw):
            started.append(spec)
            raise AssertionError("the loop must not start")

    runtime = _Runtime(tmp_path / "rt.db", poll_seconds=0.01)
    monkeypatch.setattr(rlt, "_runtime_for", lambda client, project_id: runtime)
    start = rlt._build_research_loop_start(_LoopClient(raised=_NOT_FOUND))["execute"]
    reply = json.loads(start("find the catalyst", project_id="p-foreign", plan="research-round"))
    assert reply["code"] == "research_loop_start_failed"
    assert "p-foreign" in reply["message"]
    assert started == []
