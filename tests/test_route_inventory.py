"""X2 route inventory audit tests — baseline coverage + drift detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import route_inventory
from tools.route_inventory import (
    _auth_note,
    _norm_method,
    _route_auth,
    cmd_annotate,
    cmd_generate,
)


class TestAuthNote:
    def test_whitelist_covers_health_metrics_readiness(self):
        assert _auth_note(None, "/health").startswith("open")
        assert _auth_note(None, "/metrics").startswith("open")
        assert _auth_note(None, "/readiness").startswith("open")

    def test_umrs_web_open_only_for_the_static_mount_and_probes(self):
        # H-1 (2026-10-18): the "/umrs/web" prefix whitelist marked every
        # dashboard route open, though each one resolves the gateway-verified
        # caller (401 in strict mode). Only the static shell, /health and
        # /metrics are open by design now; this pin used to expect "open"
        # for /umrs/web/dashboard/overview.
        assert _auth_note(None, "/umrs/web").startswith("open")
        assert _auth_note(None, "/umrs/web/dashboard/health").startswith("open")
        assert _auth_note(None, "/umrs/web/dashboard/metrics").startswith("open")
        assert _auth_note(None, "/umrs/web/dashboard/overview") == "unknown"
        assert _auth_note(None, "/umrs/web/dashboard/jobs") == "unknown"

    def test_umrs_web_overview_resolves_to_verified(self):
        from umrs.api.web_dashboard import get_overview

        assert _auth_note(get_overview, "/umrs/web/dashboard/overview") == "verified"

    def test_route_level_scope_dependency_resolves_to_verified(self):
        from fastapi import APIRouter, Depends
        from umrs.api._scope_identity import get_caller_scope

        router = APIRouter()

        @router.get("/umrs/x", dependencies=[Depends(get_caller_scope)])
        def handler():  # pragma no-cover
            return {}

        route = router.routes[0]
        assert _route_auth(route, handler, "/umrs/x") == "verified"

    def test_same_named_local_resolver_is_not_verified(self):
        # Review follow-up: a resolver is "verified" by its qualified name, not
        # its bare __name__; a router-local get_caller reading an unsigned
        # header must stay "unknown" (so `check` still fails on it).
        from fastapi import APIRouter, Depends, Header

        def get_caller(x_user_id: str = Header(None)):  # pragma no-cover
            return x_user_id

        def handler(caller=Depends(get_caller)):  # pragma no-cover
            return {}

        router = APIRouter()
        router.add_api_route("/umrs/y", handler, dependencies=[Depends(get_caller)])
        assert _auth_note(handler, "/umrs/y") == "unknown"
        assert _route_auth(router.routes[0], handler, "/umrs/y") == "unknown"

    def test_canonical_resolvers_are_verified(self):
        from fastapi import Depends
        from umrs.api._scope_identity import get_caller_scope
        from umrs.api._verified_headers import get_caller, get_verified_caller
        from umrs.api.files import get_project_id

        for resolver in (get_caller_scope, get_caller, get_verified_caller, get_project_id):

            def handler(caller=Depends(resolver)):  # pragma no-cover
                return caller

            assert _auth_note(handler, "/umrs/z") == "verified", resolver

    def test_docstring_auth_marker_resolves(self):
        def handler():
            """X. # auth: verified"""
            pass

        assert _auth_note(handler, "/x") == "verified"

    def test_plain_endpoint_unknown(self):
        def handler():  # pragma no-cover
            """Plain, no marker"""

        assert _auth_note(handler, "/umrs/x") == "unknown"

    def test_token_parameter_flagged_suspect(self):
        def handler(authorization: str | None = None):  # pragma no-cover
            pass

        assert _auth_note(handler, "/umrs/x") == "suspect-param"


class TestNormMethod:
    def test_options_drop_head_dedup(self):
        assert _norm_method({"GET", "POST", "OPTIONS", "HEAD"}) == ["GET", "POST"]
        assert _norm_method({"POST"}) == ["POST"]
        assert _norm_method(set()) == ["GET"]


class TestInventoryBaseline:
    def test_baseline_is_well_formed(self):
        path = Path(__file__).resolve().parents[1] / "tools/route_inventory.base.json"
        data = json.loads(path.read_text())
        keys = {(row["path"], tuple(row["methods"])) for row in data["routes"]}
        # Duplicates (same path+method distinct register separately) are
        # acceptable — hold out for DRY duplicates across router mounts.
        assert len(keys) >= len(data["routes"]) - 2
        assert all(row["auth"] for row in data["routes"])
        assert any(k[0].startswith("/umrs/") for k in keys)

    def test_baseline_fully_annotated(self):
        """Zero-unknown floor: every baseline row carries a concrete note."""
        path = Path(__file__).resolve().parents[1] / "tools/route_inventory.base.json"
        data = json.loads(path.read_text())
        unknown = [r for r in data["routes"] if r["auth"] == "unknown"]
        assert unknown == [], f"{len(unknown)} routes still un-annotated"


_SYNTH_ROWS = {
    "fresh": [
        {"path": "/health", "methods": ["GET"], "router": "m", "auth": "open(white)"},
        {"path": "/umrs/jobs", "methods": ["GET"], "router": "m", "auth": "unknown"},
    ],
    "mixed": [
        {"path": "/health", "methods": ["GET"], "router": "m", "auth": "open(white)"},
        {"path": "/umrs/jobs", "methods": ["GET"], "router": "m", "auth": "unknown"},
        {"path": "/umrs/x", "methods": ["POST"], "router": "m", "auth": "unknown"},
    ],
}


@pytest.fixture()
def synth_inventory(monkeypatch):
    def _set(rows_key: str) -> None:
        rows = [dict(r) for r in _SYNTH_ROWS[rows_key]]
        monkeypatch.setattr(route_inventory, "build_inventory", lambda: rows)

    return _set


class TestGenerateMerge:
    def _seed(self, out: Path) -> None:
        out.write_text(
            json.dumps(
                {
                    "routes": [
                        {
                            "path": "/umrs/jobs",
                            "methods": ["GET"],
                            "router": "m",
                            "auth": "edge-auth(nginx)",
                        }
                    ],
                    "summary": {},
                }
            )
        )

    def test_generate_merges_existing_notes(self, tmp_path, synth_inventory):
        out = tmp_path / "base.json"
        self._seed(out)
        synth_inventory("mixed")
        assert cmd_generate(out) == 0
        written = json.loads(out.read_text())
        notes = {r["path"]: r["auth"] for r in written["routes"]}
        assert notes["/umrs/jobs"] == "edge-auth(nginx)"  # preserved
        assert notes["/umrs/x"] == "unknown"  # new row keeps inferred
        assert written["summary"]["edge-auth(nginx)"] == 1
        assert written["summary"]["unknown"] == 1

    def test_generate_fresh_ignores_existing_notes(self, tmp_path, synth_inventory):
        out = tmp_path / "base.json"
        self._seed(out)
        synth_inventory("fresh")
        assert cmd_generate(out, fresh=True) == 0
        written = json.loads(out.read_text())
        notes = {r["path"]: r["auth"] for r in written["routes"]}
        assert notes["/umrs/jobs"] == "unknown"  # re-inferred


class TestAnnotate:
    def test_annotate_rewrites_matching_rows(self, tmp_path, synth_inventory):
        out = tmp_path / "base.json"
        synth_inventory("fresh")
        cmd_generate(out, fresh=True)
        assert cmd_annotate(out, "unknown", "edge-auth(nginx)") == 0
        written = json.loads(out.read_text())
        assert all(r["auth"] != "unknown" for r in written["routes"])
        assert written["summary"]["edge-auth(nginx)"] == 1

    def test_annotate_no_match_is_noop(self, tmp_path, synth_inventory):
        out = tmp_path / "base.json"
        synth_inventory("fresh")
        cmd_generate(out, fresh=True)
        assert cmd_annotate(out, "bogus-note", "edge-auth(nginx)") == 0
        written = json.loads(out.read_text())
        assert any(r["auth"] == "unknown" for r in written["routes"])

    def test_annotate_unknown_note_rejected(self, tmp_path, synth_inventory):
        out = tmp_path / "base.json"
        synth_inventory("fresh")
        cmd_generate(out, fresh=True)
        assert cmd_annotate(out, "open(white)", "unknown") == 1
