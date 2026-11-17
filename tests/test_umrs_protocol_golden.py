"""Golden wire vectors through the parent: the r4_contracts names and the sidecar twins.

``umrs/protocol/tests/golden/`` holds vectors generated once from ``r4_contracts``
before the move (U2.0 P0) and the gate pin. umrs replays them against
``umrs_protocol``; this test replays them through what the parent ships:

- the ``r4_contracts.*`` aliases r4 and the gateway import (U2.0b);
- the gateway sidecar's inlined stdlib signers (``gateway/sidecar/main.py``,
  KEEP IN SYNC twins of ``handshake_hmac``), the r4-side twin guard;
- the gate pin: ``r4_contracts.hpc_gate`` hashes to the anchored release.

The parent reads its submodule; umrs never reads the parent. A missing golden
file fails (the pin must hold U-A0), never skips. ADR-117 D-2, U2.0 spec §7.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from r4_contracts import errors, handshake_hmac, hpc_gate, protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "umrs" / "protocol" / "tests" / "golden"
SIDECAR = REPO_ROOT / "gateway" / "sidecar" / "main.py"
SIDECAR_SIGNERS = ("_sign_identity_hmac", "_sign_workflow_author_hmac", "_sign_memberships_hmac")

VECTORS: dict[str, Any] = json.loads((GOLDEN / "wire_vectors.json").read_text(encoding="utf-8"))
GATE_PIN: dict[str, str] = json.loads((GOLDEN / "gate.json").read_text(encoding="utf-8"))
HMAC = VECTORS["handshake_hmac"]
PROTO = VECTORS["protocol"]
ERRS = VECTORS["errors"]


def _sidecar_signers() -> dict[str, Any]:
    """The sidecar's signer functions, compiled from their own source.

    Only the three stdlib-only functions are taken (they import hashlib/hmac
    locally), so the sidecar's FastAPI/asyncpg imports are not needed here.
    """
    tree = ast.parse(SIDECAR.read_text(encoding="utf-8"), filename=str(SIDECAR))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in SIDECAR_SIGNERS]
    assert sorted(n.name for n in nodes) == sorted(SIDECAR_SIGNERS)
    namespace: dict[str, Any] = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SIDECAR), "exec"), namespace)  # noqa: S102
    return {name: namespace[name] for name in SIDECAR_SIGNERS}


SIGNERS = _sidecar_signers()


# ── handshake_hmac through r4_contracts and through the sidecar ─────────────


def test_hmac_constants() -> None:
    assert HMAC["ENV_VAR"] == handshake_hmac.ENV_VAR
    assert HMAC["MEMBERSHIPS_DOMAIN"] == handshake_hmac.MEMBERSHIPS_DOMAIN
    live = {n: getattr(handshake_hmac, n) for n in vars(handshake_hmac) if n.startswith("HEADER_")}
    assert live == HMAC["headers"]


@pytest.mark.parametrize(
    "row", HMAC["sign_claims"], ids=lambda r: f"{r['user_id']}-{r['is_admin']}"
)
def test_identity_claims(row: dict[str, Any]) -> None:
    args = (row["secret"], row["user_id"], row["project_id"], row["is_admin"])
    assert handshake_hmac.sign_claims(*args) == row["hmac"]
    assert SIGNERS["_sign_identity_hmac"](*args) == row["hmac"]


@pytest.mark.parametrize(
    "row", HMAC["sign_workflow_author_claim"], ids=lambda r: f"{r['user_id']}-{r['author']}"
)
def test_workflow_author_claims(row: dict[str, Any]) -> None:
    args = (row["secret"], row["user_id"], row["project_id"], row["author"])
    assert handshake_hmac.sign_workflow_author_claim(*args) == row["hmac"]
    assert SIGNERS["_sign_workflow_author_hmac"](*args) == row["hmac"]


@pytest.mark.parametrize("row", HMAC["sign_memberships"], ids=lambda r: repr(r["memberships"]))
def test_memberships(row: dict[str, Any]) -> None:
    args = (row["secret"], row["user_id"], row["project_id"], row["is_admin"], row["memberships"])
    assert handshake_hmac.sign_memberships(*args) == row["hmac"]
    if isinstance(row["memberships"], list):  # the sidecar signs the header list it built
        assert SIGNERS["_sign_memberships_hmac"](*args) == row["hmac"]


def test_the_sidecar_is_checked_on_list_memberships() -> None:
    assert any(isinstance(r["memberships"], list) for r in HMAC["sign_memberships"])


@pytest.mark.parametrize("row", HMAC["canonical_memberships"], ids=lambda r: repr(r["input"]))
def test_canonical_memberships(row: dict[str, Any]) -> None:
    assert handshake_hmac.canonical_memberships(row["input"]) == row["output"]


# ── protocol: READY!:v1.x ───────────────────────────────────────────────────


def test_protocol_version() -> None:
    assert PROTO["PROTOCOL_VERSION"] == protocol.PROTOCOL_VERSION


@pytest.mark.parametrize("row", PROTO["handshake_reply"], ids=lambda r: repr(r["args"]))
def test_handshake_reply(row: dict[str, Any]) -> None:
    assert protocol.handshake_reply(*row["args"]) == row["output"]


@pytest.mark.parametrize("row", PROTO["parse_handshake_reply"], ids=lambda r: repr(r["input"]))
def test_parse_handshake_reply(row: dict[str, Any]) -> None:
    assert list(protocol.parse_handshake_reply(row["input"])) == row["output"]


@pytest.mark.parametrize(
    "row", PROTO["is_protocol_compatible"], ids=lambda r: f"{r['server']!r}-{r['client']!r}"
)
def test_is_protocol_compatible(row: dict[str, Any]) -> None:
    extra = [row["client"]] if row["client"] else []
    assert protocol.is_protocol_compatible(row["server"], *extra) is row["output"]


# ── errors: x-platform-error-code ───────────────────────────────────────────


def test_error_code_header() -> None:
    assert errors.ERROR_CODE_HEADER == ERRS["ERROR_CODE_HEADER"] == "x-platform-error-code"


def test_known_error_codes_are_append_only() -> None:
    live = errors.known_error_codes()
    golden = ERRS["known_error_codes"]
    assert not sorted(set(golden) - set(live)), "error codes removed"
    assert {c: s for c, s in golden.items() if live[c] != s} == {}, "error status changed"


@pytest.mark.parametrize("row", ERRS["factories"], ids=lambda r: f"{r['factory']}.{r['name']}")
def test_error_factories(row: dict[str, Any]) -> None:
    err = getattr(errors.PlatformError, row["factory"])(
        row["name"], row["message"], **row["detail"]
    )
    assert err.to_dict() == row["to_dict"]
    assert err.to_http_parts() == row["to_http_parts"]


# ── hpc_gate: the one gate, the anchored release ────────────────────────────


def test_gate_is_the_pinned_release() -> None:
    gate_bytes = Path(hpc_gate.__file__).read_bytes()
    assert hashlib.sha256(gate_bytes).hexdigest() == GATE_PIN["sha256"]
    assert hpc_gate.gate_sha256() == GATE_PIN["sha256"]
    assert GATE_PIN["gate_version"] == hpc_gate.GATE_VERSION
    assert VECTORS["hpc_gate"]["GATE_SCRIPT"] == hpc_gate.GATE_SCRIPT
