"""Pins for ADR-075 wave-0: the workflow arm (G2) + the driver setting (G3).

G2: while armed, hpc_submit must refuse the non-UMRS lane BY NAME when the
durable UMRS path is unreachable; unarmed keeps the legacy note+local lane.
The arm rides session.metadata and is mirrored onto the process tool guards
(the knowledge-write-gate facade pattern). G3: driver round-trips; the
bridge refuses `autonomous` by name until U-03 (never faked).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import r4.companion.tools as tools_mod
from r4.companion.ui.bridge_server import CompanionBridgeServer


@pytest.fixture(autouse=True)
def _disarmed():
    tools_mod.set_workflow_armed_flag(False)
    yield
    tools_mod.set_workflow_armed_flag(False)


def test_arm_refuses_the_non_umrs_lane(tmp_path, monkeypatch) -> None:
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\necho hi\n")
    monkeypatch.setattr(tools_mod, "_off_cluster", lambda _t: None)
    monkeypatch.setattr(tools_mod, "_hpc_submit_via_umrs", lambda _p: None)
    tools_mod.set_workflow_armed_flag(True)
    out = tools_mod._hpc_submit(str(script))
    assert "workflow arm is ON" in out
    assert "refused" in out


def test_unarmed_keeps_the_legacy_local_lane(tmp_path, monkeypatch) -> None:
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\necho hi\n")
    monkeypatch.setattr(tools_mod, "_off_cluster", lambda _t: None)
    monkeypatch.setattr(tools_mod, "_hpc_submit_via_umrs", lambda _p: None)
    monkeypatch.setattr(
        tools_mod.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="submitted 42", stderr=""),
    )
    out = tools_mod._hpc_submit(str(script))
    assert "submitted 42" in out
    assert "workflow arm" not in out


def test_umrs_unreachable_note_becomes_refusal_only_when_armed(monkeypatch) -> None:
    """Unarmed: unreachable UMRS returns the honest error (never a fake win
    and — C-2(vi) 2026-10-19 — never a fake local-sbatch fallback note).
    Armed: the same condition returns None so the caller refuses by name."""
    import r4.client as client_mod

    class _DeadClient:
        _api_prefix = "/gw"  # mode-aware since C-2(v) 2026-10-19

        def post(self, *_a, **_kw):
            raise ConnectionError("unreachable")

    monkeypatch.setattr(client_mod, "Client", lambda: _DeadClient())
    tools_mod.set_workflow_armed_flag(False)
    note = tools_mod._hpc_submit_via_umrs("x.sh")
    assert isinstance(note, str) and "durable UMRS submit failed" in note
    assert "nothing was submitted" in note
    tools_mod.set_workflow_armed_flag(True)
    assert tools_mod._hpc_submit_via_umrs("x.sh") is None


def test_bridge_set_workflow_armed_persists_metadata_and_applies(monkeypatch) -> None:
    bridge = object.__new__(CompanionBridgeServer)
    session = SimpleNamespace(
        metadata={},
        client=SimpleNamespace(),
        agent=SimpleNamespace(model=None),
        name="s",
    )
    saved: list[bool] = []
    bridge._session = session
    bridge._store = SimpleNamespace(save=lambda _s: saved.append(True))
    bridge.snapshot = lambda *a, **kw: {
        "session": {"workflow_armed": bool(session.metadata.get("workflow_armed"))}
    }

    out = bridge.set_workflow_armed({"armed": True})
    assert out["workflow_armed"] is True
    assert session.metadata["workflow_armed"] is True
    assert tools_mod._WORKFLOW_ARMED is True
    assert saved, "the arm must persist with the session (resume keeps it)"

    read = bridge.set_workflow_armed({})
    assert read["workflow_armed"] is True

    bridge.set_workflow_armed({"armed": False})
    assert tools_mod._WORKFLOW_ARMED is False


# --- G3: driver setting -----------------------------------------------------


def test_driver_roundtrips_and_defaults_manual() -> None:
    from r4.teamup.session import TeamupSessionConfig

    config = TeamupSessionConfig(name="x", parallel_roles=1)
    assert TeamupSessionConfig.from_dict(config.to_dict()).driver == "manual"
    aut = TeamupSessionConfig(name="y", driver="manual")
    assert TeamupSessionConfig.from_dict(aut.to_dict()).driver == "manual"


def test_bridge_driver_knob_refuses_autonomous_by_name() -> None:
    bridge = object.__new__(CompanionBridgeServer)
    assert bridge._teamup_knobs({})["driver"] == "manual"
    assert bridge._teamup_knobs({"driver": "manual"})["driver"] == "manual"
    with pytest.raises(ValueError, match="autonomous team sessions land"):
        bridge._teamup_knobs({"driver": "autonomous"})


def test_build_team_config_threads_driver() -> None:
    from r4.teamup.bootstrap import build_team_config

    config = build_team_config("probe", crew="hpc", driver="manual")
    assert config.driver == "manual"
