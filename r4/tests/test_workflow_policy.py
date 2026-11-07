"""Verification tests for workflow policy defaults."""

from r4.companion.workflow_policy import load_workflow_policy


def test_default_policy_allows_golden_path_reads_and_run():
    """Default posture (2026-08-30): full workflow lifecycle allowed locally;
    the guard is a veto layer, server-side ADR-027 stays the authority."""
    policy = load_workflow_policy()
    assert policy.allows("list")
    assert policy.allows("describe")
    assert policy.allows("run")
    assert policy.allows("wait")
    assert policy.allows("upload")


def test_missing_policy_file_fails_closed_for_mutations_only(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(tmp_path / "missing.json"))
    policy = load_workflow_policy()
    assert policy.allows("list")
    assert policy.allows("describe")
    assert policy.allows("status")
    assert not policy.allows("upload")
    assert not policy.allows("run")
    assert not policy.allows("cancel")


def test_policy_denied_error_names_local_surface_and_remediation():
    """2026-08-30 misdiagnosis guard: deny must say evaluation is client-side."""
    from r4.companion.workflow_policy import policy_denied_error

    policy = load_workflow_policy()
    payload = policy_denied_error("upload", policy)

    assert payload["error"] == "policy_denied"
    assert payload["surface"] == "r4_companion.local_policy"
    assert "no request reached the gateway/UMRS" in payload["message"]
    assert "not a server-side deny" in payload["message"]
    assert "R4_WORKFLOW_POLICY_FILE" in payload["remediation"]
    assert "ADR-027" in payload["remediation"]
