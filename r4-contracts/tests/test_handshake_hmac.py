"""Handshake-HMAC contract tests: identity tuple + ADR-027 wf-claim basis.

Two signature bases exist by design (deploy-safety): the identity tuple
(4-line, unchanged since the live-spoof fix) and the workflow-author
claim (own canonical). A basis change on the identity tuple would have
desynced routes that never forward the author header.
"""

from __future__ import annotations

import os

from r4_contracts.handshake_hmac import (
    ENV_VAR,
    HEADER_WORKFLOW_AUTHOR,
    HEADER_WORKFLOW_AUTHOR_HMAC,
    sign_claims,
    sign_workflow_author_claim,
    verify_claims,
    verify_workflow_author_claim,
)


def test_identity_tuple_form_unchanged() -> None:
    sig = sign_claims("s", "alice", "proj-a", True)
    assert verify_claims("s", sig, "alice", "proj-a", True)
    assert not verify_claims("s", sig, "alice", "proj-a", False)


def test_missing_inputs_still_invalid() -> None:
    assert not verify_claims(None, "x", "u", "p", False)
    assert not verify_claims("s", None, "u", "p", False)
    assert not verify_claims("s", "x", "", "p", False)


def test_wf_claim_signs_and_verifies() -> None:
    sig = sign_workflow_author_claim("s", "bob", "proj-b", True)
    assert verify_workflow_author_claim("s", sig, "bob", "proj-b", True)
    # flag flip invalidates — a forged author header cannot reuse the sig
    assert not verify_workflow_author_claim("s", sig, "bob", "proj-b", False)
    # wrong subject invalidates
    assert not verify_workflow_author_claim("s", sig, "mallory", "proj-b", True)


def test_wf_claim_basis_is_independent_of_identity_basis() -> None:
    id_sig = sign_claims("s", "alice", "proj-a", False)
    wf_sig = sign_workflow_author_claim("s", "alice", "proj-a", True)
    assert id_sig != wf_sig
    assert not verify_workflow_author_claim("s", id_sig, "alice", "proj-a", True)
    assert not verify_claims("s", wf_sig, "alice", "proj-a", False)


def test_wf_claim_missing_inputs_invalid() -> None:
    assert not verify_workflow_author_claim(None, "x", "u", "p", True)
    assert not verify_workflow_author_claim("s", None, "u", "p", True)
    assert not verify_workflow_author_claim("s", "x", "", "p", True)


def test_wf_header_constants_stable() -> None:
    assert HEADER_WORKFLOW_AUTHOR == "X-Verified-Workflow-Author"
    assert HEADER_WORKFLOW_AUTHOR_HMAC == "X-Verified-Workflow-Author-HMAC"
    assert os.environ.get("ENV_VAR_SENTINEL", ENV_VAR) == ENV_VAR
