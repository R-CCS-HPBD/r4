"""ADR-108 D-3 (W2b): the client carries a work level's scope token.

Inside ``acting_in_scope`` every platform request carries ``X-R4-Scope``;
outside, nothing changes; a direct provider request never carries it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from r4._http import HTTPCore
from r4.scopes import HEADER, ScopeClient, acting_in_scope, current_scope_token


def _core() -> HTTPCore:
    cfg = SimpleNamespace(
        api_key="k",
        timeout=1,
        verify_ssl=False,
        project_id="proj",
        umrs_username=None,
        bound_source="x",
    )
    return HTTPCore(cfg)  # type: ignore[arg-type]


def test_platform_requests_carry_the_token_only_inside_the_scope() -> None:
    core = _core()
    assert HEADER not in core.build_headers()
    with acting_in_scope("tok-parent"):
        assert core.build_headers()[HEADER] == "tok-parent"
        with acting_in_scope("tok-child"):
            assert core.build_headers()[HEADER] == "tok-child"
        assert current_scope_token() == "tok-parent"
    assert HEADER not in core.build_headers()


def test_a_provider_request_never_carries_it() -> None:
    with acting_in_scope("tok"):
        assert HEADER not in _core().build_provider_headers()


def test_issue_and_revoke_bodies() -> None:
    posted: list[tuple[str, dict[str, Any]]] = []

    def post(path: str, json: Any = None, **_kw: Any) -> dict[str, Any]:
        posted.append((path, json))
        return {"token": "t", "scope": {"path": json.get("path")}}

    scopes = ScopeClient(SimpleNamespace(_api_prefix="/gw", post=post))
    scopes.issue(
        "s-1/loop-7",
        ["ingest", "workflow:fit"],
        parent_token="p",
        ttl_seconds=600,
        provenance={
            "repo": "github.com/lab/mof",
            "branch": "r4/x",
            "commit": "abc1234",
            "path": "ignored",
        },
    )
    scopes.revoke("s-1")
    assert posted == [
        (
            "/gw/scopes",
            {
                "path": "s-1/loop-7",
                "grants": ["ingest", "workflow:fit"],
                "parent_token": "p",
                "ttl_seconds": 600,
                "repo": "github.com/lab/mof",
                "branch": "r4/x",
                "commit": "abc1234",
            },
        ),
        ("/gw/scopes/revoke", {"path": "s-1"}),
    ]
