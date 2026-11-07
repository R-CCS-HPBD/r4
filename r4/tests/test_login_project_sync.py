"""Login-time project/circle sync pins (2026-10-20, maintainer ask).

After ``r4 auth login`` the client adopts every server-visible project into
the machine's local registry: owned rows (``list_platform_projects``) plus
ADR-086 circle-derived projects (``circles/mine``). Pins cover the union +
dedupe, owned-row naming, gateway recording, the never-touch rules
(existing entries, current selection), and per-leg degradation (one leg or
both failing never raises and never breaks the login).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from r4.project import ProjectConfig, ProjectManager
from r4.project_sync import sync_server_projects


class _Client:
    """Client stub: two scripted reads + a gateway_config (no network)."""

    def __init__(
        self, owned=None, circles=None, owned_exc=None, circles_exc=None, memberships=None
    ):
        self._memberships = memberships
        self._owned = owned
        self._owned_exc = owned_exc
        self._circles = circles
        self._circles_exc = circles_exc
        self._config = SimpleNamespace(gateway_url="https://gw.example/rccs")

    def list_platform_projects(self, *, timeout: float) -> dict[str, Any]:
        if self._owned_exc:
            raise self._owned_exc
        payload: dict[str, Any] = {"projects": self._owned or [], "count": len(self._owned or [])}
        if self._memberships is not None:
            payload["memberships"] = self._memberships
        return payload

    def list_my_circles(self, timeout: float = 10) -> dict[str, Any]:
        if self._circles_exc:
            raise self._circles_exc
        return {"circles": [], "projects": self._circles or [], "count": 0}


def _sync(client: _Client, pm: ProjectManager) -> dict[str, list[str]]:
    return sync_server_projects(client, pm, project_config_cls=ProjectConfig)


class TestAdoptionUnion:
    def test_owned_and_derived_ids_adopt_with_names_and_gateway(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(
            owned=[
                {"project_id": "r4-dev", "name": "R4 dev"},
                {"project_id": "example-project-b", "name": "Example B"},
            ],
            # example-project-b overlaps → adopted once
            circles=["example-project-b", "example-circle"],
        )
        result = _sync(client, pm)
        assert result["adopted"] == ["r4-dev", "example-project-b", "example-circle"]
        assert result["known"] == []
        assert pm.get("example-project-b").name == "Example B"  # owned row carries the name
        assert pm.get("example-circle").name is None  # derived circle projects carry no name
        for pid in ("r4-dev", "example-project-b", "example-circle"):
            assert pm.get(pid).gateway_url == "https://gw.example/rccs"

    def test_an_empty_server_adopts_nothing_and_stays_quiet(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        assert _sync(_Client(owned=[], circles=[]), pm) == {"adopted": [], "known": []}
        assert pm.list() == []


class TestNeverTouchRules:
    def test_existing_entries_keep_local_customizations(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        pm.create(
            ProjectConfig(
                project_id="example-project-b",
                name="my local label",
                gateway_url="https://other-edge/site-b",
                model="glm-5.3",
                metadata={"security_level": "high"},
            )
        )
        client = _Client(
            owned=[{"project_id": "example-project-b", "name": "Example B"}],
            circles=["example-circle"],
        )
        result = _sync(client, pm)
        assert result["adopted"] == ["example-circle"]
        assert result["known"] == ["example-project-b"]
        kept = pm.get("example-project-b")
        assert kept.name == "my local label"
        assert kept.gateway_url == "https://other-edge/site-b"
        assert kept.model == "glm-5.3"
        assert kept.metadata.get("security_level") == "high"

    def test_current_selection_is_never_moved(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        pm.create(ProjectConfig(project_id="r4-dev"))
        pm.set_current("r4-dev")
        _sync(_Client(owned=[{"project_id": "example-project-b", "name": ""}]), pm)
        assert pm.get_current() == "r4-dev"


class TestPerLegDegradation:
    def test_owned_leg_failing_still_adopts_circle_projects(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(owned_exc=RuntimeError("gateway 503"), circles=["example-project-b"])
        assert _sync(client, pm)["adopted"] == ["example-project-b"]

    def test_circles_leg_failing_still_adopts_owned_projects(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(
            owned=[{"project_id": "r4-dev", "name": ""}], circles_exc=TimeoutError("slow edge")
        )
        assert _sync(client, pm)["adopted"] == ["r4-dev"]

    def test_both_legs_failing_adopts_nothing_and_never_raises(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(owned_exc=RuntimeError("x"), circles_exc=RuntimeError("y"))
        assert _sync(client, pm) == {"adopted": [], "known": []}
        assert pm.list() == []


class TestMalformedPayloads:
    def test_garbage_rows_are_skipped_not_adopted(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(
            owned=[{"name": "no id"}, {"project_id": ""}, "not-a-dict", {"project_id": "ok"}],
            circles=["ok", "", None],
        )
        assert _sync(client, pm)["adopted"] == ["ok"]


class TestLiveMemberships:
    """ADR-097: a project the account is a member of (``umrs admin user
    add-to-project``) is adopted at login, so the console can select it —
    2026-10-22, a member account was in `example-project-b` but owned nothing and held no
    circle, and never saw it."""

    def test_member_projects_adopt_after_owned_and_before_circles(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(
            owned=[{"project_id": "lab", "name": "Lab"}],
            memberships=["default", "lab", "example-project-b"],
            circles=["example-circle", "example-project-b"],
        )
        assert _sync(client, pm)["adopted"] == [
            "lab",
            "default",
            "example-project-b",
            "example-circle",
        ]
        assert pm.get("example-project-b").gateway_url == "https://gw.example/rccs"

    def test_a_server_without_memberships_or_with_null_adopts_owned_and_circles(self, tmp_path):
        for memberships in (None, []):
            pm = ProjectManager(config_dir=tmp_path / str(memberships))
            client = _Client(
                owned=[{"project_id": "lab"}], circles=["example-circle"], memberships=memberships
            )
            assert _sync(client, pm)["adopted"] == ["lab", "example-circle"]

    def test_an_unreadable_listing_still_adopts_circles(self, tmp_path):
        pm = ProjectManager(config_dir=tmp_path)
        client = _Client(
            owned_exc=OSError("gateway down"), circles=["example-circle"], memberships=["x"]
        )
        assert _sync(client, pm)["adopted"] == ["example-circle"]
