# ADR-071: Account-bound session keys — no derivation from the account key

**Status:** Accepted (2026-09-24, agent decision under the maintainer's delegation of the same date; closes gap row D-03's escalated tail)
**Date:** 2026-09-24
**Relates to:** ADR-059 (dual transport), D-03 (ZAP with per-user keys managed by UMRS accounts)

## Context

The design asks for ZAP with per-user keys managed by UMRS accounts. The
tree has two key kinds on one ZAP callback:

- **Account keys** (long-lived): the CURVE public key on the UMRS account
  record. The record is the authority; the live keystore sync registers it,
  and unregisters it on revocation or disable within one sweep tick.
- **Session keys** (ephemeral): generated on the workstation at
  `r4 auth login`, registered via `POST /umrs/zmq/session` under the
  caller's JWT (grants from the JWT; TTL ≤ 24 h, at most 5 per user).

The open ask was whether session keys should instead be *derived from* the
account key (for example an HKDF over the account secret plus a session
nonce).

## Decision

1. **No derivation.** Session keys stay independently generated and
   registered under a JWT that the account's identity issued. Derivation
   would require every workstation to hold the long-lived account
   **secret**, which widens the blast radius of one compromised laptop to
   every future session. A deterministic derivation also weakens
   per-session independence. And it gains nothing on the design's actual
   requirement: session keys are already account-managed, because they
   exist only under the account's JWT, carry its grants, and live in the
   account's per-user session cap.
2. **The account is the authority for both kinds.** Disabling an account
   now ends *all* its keys at the next sync tick, session keys included.
   Previously, JWT-bootstrapped sessions survived until their TTL.
   Revoking only the account key retires that key and leaves the user's
   sessions alone (key rotation is not account suspension).
3. **Custody split:** account keys are for non-interactive and service
   clients that keep a secret on a managed host; session keys are for
   people at workstations.

## Rejected options

- **Session keys derived from the account key** — see decision 1.
- **Account keys only** (no sessions) — every workstation would need a
  long-lived secret; logout could not kill a stolen laptop's key without
  re-keying the account.

## Consequences

- D-03 is Implemented: per-user keys managed by UMRS accounts, with live
  revocation for both kinds.
- JWT validity is still checked where a session registers, not
  continuously. A disabled account's sessions end at the next sweep tick
  (the live-sync cadence), not instantly.

## Verification pointers

- `umrs/src/umrs/core/session_curve_provider.py` — `sync_account_keys` (disabled → all keys end)
- `umrs/tests/test_account_curve_authority.py` — `test_disabling_the_account_ends_its_session_keys`, `test_revoking_only_the_account_key_keeps_sessions`
