"""V-01/D-37 pins: companion HPC tools are cluster-venue scoped — named
refusal off-cluster, legacy behavior only where sbatch exists."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

from r4.companion import tools


def test_submit_refuses_off_cluster(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\necho hi\n")
    out = tools._hpc_submit(str(script))
    assert "unavailable off-cluster (D-37)" in out
    assert "durable UMRS path" in out


def test_status_refuses_off_cluster(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    out = tools._hpc_job_status("123")
    assert "unavailable off-cluster (D-37)" in out


def test_submit_dry_run_works_on_cluster(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sbatch")
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\necho hi\n")
    out = tools._hpc_submit(str(script), dry_run=True)
    assert "[DRY RUN] Would submit:" in out


def test_missing_script_error_on_cluster(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sbatch")
    assert "script not found" in tools._hpc_submit("/ghost/x.sh")


def test_tool_registration_names_venue_scope():
    from r4.companion.tools import _HPC_OFF_CLUSTER_REFUSAL

    assert "durable UMRS path" in _HPC_OFF_CLUSTER_REFUSAL
    assert "cluster venue" in _HPC_OFF_CLUSTER_REFUSAL


def test_hpc_skill_md_documents_venue_rule():
    from pathlib import Path

    text = (Path(tools.__file__).parent / "builtin" / "hpc" / "SKILL.md").read_text()
    assert "cluster-venue" in text
    assert "D-37" in text


# ── D-37 scheduler leg: the durable UMRS path (ROADMAP 3.3) ────────────


def test_submit_prefers_durable_umrs_path(monkeypatch, tmp_path):
    """On-cluster: the tool POSTs an hpc_script job FIRST (design: the
    client never talks to the scheduler); the note names the fallback."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sbatch")
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\necho hi\n")

    class _Client:
        calls = []
        _api_prefix = "/gw"  # mode-aware since C-2(v) 2026-10-19

        def post(self, path, json=None):
            self.calls.append((path, json))
            return {"job_id": "job-42", "status": "queued"}

    import r4.companion.tools as tools_mod

    monkeypatch.setattr("r4.client.Client", _Client)
    out = tools_mod._hpc_submit(str(script))
    assert "durable job job-42" in out
    assert _Client.calls[0][0] == "/gw/jobs"
    assert _Client.calls[0][1]["job_type"] == "hpc_script"
    assert _Client.calls[0][1]["spec"]["script_path"] == str(script)


def test_submit_surfaces_an_honest_error_when_umrs_down(monkeypatch, tmp_path):
    """C-2(vi) 2026-10-19: no fake fallback note and no silent local sbatch —
    the unarmed durable failure is an error naming that nothing submitted
    (the D-37 legacy lane's fate is a design decision, not a side effect)."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sbatch")
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/bash\necho hi\n")

    import r4.companion.tools as tools_mod

    def boom(self, *a, **kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr("r4.client.Client", type("C", (), {"post": boom, "_api_prefix": "/gw"}))
    local = MagicMock()
    monkeypatch.setattr(tools_mod.subprocess, "run", local)
    out = tools_mod._hpc_submit(str(script))
    assert "durable UMRS submit failed" in out
    assert "nothing was submitted" in out
    local.assert_not_called()


def test_durable_status_resolves_numeric_ids_via_umrs(monkeypatch):
    import r4.companion.tools as tools_mod

    class _Client:
        _api_prefix = "/gw"  # mode-aware since C-2(v) 2026-10-19

        def get(self, path):
            assert path == "/gw/jobs/77"
            return {"status": "completed", "spec": {"script_path": "/x/run.sh"}}

    monkeypatch.setattr("r4.client.Client", _Client)
    out = tools_mod._hpc_durable_status("77")
    assert "Durable job 77" in out and "completed" in out
    # non-numeric (scheduler-era) ids stay on the legacy lane
    assert tools_mod._hpc_durable_status("abc") is None
    assert tools_mod._hpc_durable_status(None) is None
