"""D-52: the companion session rides loop calls so UMRS joins the loop trace
with the session that started / steered it."""

from __future__ import annotations

from types import SimpleNamespace

from r4._cli import project_knowledge
from r4.client import Client
from r4.companion.session import _bind_client_session


class _Http:
    def __init__(self):
        self.calls = []

    def post(self, url, json_body=None, **kw):
        self.calls.append(("POST", url, kw.get("headers")))
        return {}

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw.get("params")))
        return {}


class _StubClient(Client):
    """Client with a recording transport (no config, no network)."""

    def __init__(self):  # noqa: D107 — skips Client.__init__ on purpose
        self._config = SimpleNamespace(gateway_url="https://gw")
        self.companion_session_id = None
        self._stub_http = _Http()

    @property
    def http(self):
        return self._stub_http

    @property
    def _api_prefix(self):
        return "/gw"


def _client() -> Client:
    return _StubClient()


def test_loop_calls_carry_the_bound_session():
    client = _client()
    client.start_loop("p", "g")
    assert client.http.calls[-1][2] is None  # nothing bound → no header
    client.companion_session_id = "sess-7"
    client.start_loop("p", "g")
    client.decide_loop_approval("appr-1", "once")
    assert client.http.calls[-2][2] == {"x-r4-session-id": "sess-7"}
    assert client.http.calls[-1][2] == {"x-r4-session-id": "sess-7"}


def test_list_loops_filters_by_session():
    client = _client()
    client.list_loops(companion_session_id="sess-7")
    assert client.http.calls[-1][2] == {"companion_session_id": "sess-7"}


def test_each_turn_binds_its_session_on_the_shared_client():
    knowledge = SimpleNamespace(session_id=None)
    client = SimpleNamespace(companion_session_id=None, knowledge=knowledge)
    _bind_client_session(client, "sess-a")
    _bind_client_session(client, "sess-b")
    assert (client.companion_session_id, knowledge.session_id) == ("sess-b", "sess-b")


def test_replay_names_the_starting_session():
    lines = project_knowledge._replay_origin_lines(
        {"durable_job_id": "job-1", "companion_session_id": "sess-7"}
    )
    assert lines == ["  hosting job: job-1", "  started from companion session: sess-7"]
    assert project_knowledge._replay_origin_lines({}) == []
