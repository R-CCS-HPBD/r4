"""H-1 memberships lane (2026-10-18): the memberships list gets its own HMAC.

``X-Verified-Project-Memberships`` was forwarded by the gateway with no
signature covering it, so a caller that reached UMRS directly could widen
its project set. The fix is additive: ``sign_claims`` (the identity tuple)
is unchanged, and a second signature binds the canonical memberships list
to the same (user, project, admin) tuple under its own domain prefix.
"""

from __future__ import annotations

from r4_contracts.handshake_hmac import (
    HEADER_MEMBERSHIPS_HMAC,
    HEADER_PROJECT_MEMBERSHIPS,
    canonical_memberships,
    sign_claims,
    sign_memberships,
    verify_claims,
    verify_memberships,
)

SECRET = "memberships-secret"


def test_sign_and_verify_round_trip() -> None:
    sig = sign_memberships(SECRET, "alice", "proj-a", False, ["proj-a", "proj-b"])
    assert verify_memberships(SECRET, sig, "alice", "proj-a", False, ["proj-a", "proj-b"])


def test_canonical_form_is_sorted_deduplicated_and_trimmed() -> None:
    assert canonical_memberships(["proj-b", " proj-a ", "proj-b", ""]) == "proj-a,proj-b"
    # The header string and the claim list canonicalise identically.
    assert canonical_memberships("proj-b, proj-a,,proj-a") == "proj-a,proj-b"


def test_order_and_duplicates_do_not_change_the_signature() -> None:
    sig = sign_memberships(SECRET, "alice", "proj-a", False, ["proj-b", "proj-a"])
    presented = "proj-a,proj-b,proj-a"
    assert verify_memberships(SECRET, sig, "alice", "proj-a", False, presented)


def test_tampered_list_is_rejected() -> None:
    sig = sign_memberships(SECRET, "alice", "proj-a", False, ["proj-a"])
    assert not verify_memberships(SECRET, sig, "alice", "proj-a", False, "proj-a,victim")
    assert not verify_memberships(SECRET, sig, "alice", "proj-a", False, "victim")


def test_signature_is_bound_to_the_identity_tuple() -> None:
    sig = sign_memberships(SECRET, "alice", "proj-a", False, ["proj-a", "proj-b"])
    members = ["proj-a", "proj-b"]
    assert not verify_memberships(SECRET, sig, "mallory", "proj-a", False, members)
    assert not verify_memberships(SECRET, sig, "alice", "proj-b", False, members)
    assert not verify_memberships(SECRET, sig, "alice", "proj-a", True, members)
    assert not verify_memberships("other-secret", sig, "alice", "proj-a", False, members)


def test_domain_separated_from_the_identity_signature() -> None:
    identity = sign_claims(SECRET, "alice", "proj-a", False)
    members = sign_memberships(SECRET, "alice", "proj-a", False, ["proj-a"])
    assert identity != members
    assert not verify_memberships(SECRET, identity, "alice", "proj-a", False, ["proj-a"])
    assert not verify_claims(SECRET, members, "alice", "proj-a", False)


def test_missing_inputs_are_invalid() -> None:
    sig = sign_memberships(SECRET, "alice", "proj-a", False, ["proj-a"])
    assert not verify_memberships(None, sig, "alice", "proj-a", False, ["proj-a"])
    assert not verify_memberships(SECRET, None, "alice", "proj-a", False, ["proj-a"])
    assert not verify_memberships(SECRET, sig, "", "proj-a", False, ["proj-a"])
    assert not verify_memberships(SECRET, sig, "alice", "", False, ["proj-a"])
    empty = sign_memberships(SECRET, "alice", "proj-a", False, [])
    assert not verify_memberships(SECRET, empty, "alice", "proj-a", False, [])


def test_non_hex_presented_value_is_invalid() -> None:
    assert not verify_memberships(SECRET, "not-hex-é", "alice", "proj-a", False, ["proj-a"])


def test_header_constants_stable() -> None:
    assert HEADER_PROJECT_MEMBERSHIPS == "X-Verified-Project-Memberships"
    assert HEADER_MEMBERSHIPS_HMAC == "X-Verified-Memberships-HMAC"
