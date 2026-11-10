"""Wave-E signed gate releases.

Every key is an explicitly synthetic, ephemeral Ed25519 test key.  No test
reads a maintainer release key, an HPC signing key, or a host/home keyring.
The plain-test-key bypass exists only in these in-memory fixtures; production
trust files always require a FIDO ``sk-`` identity and UP+UV assertions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from r4_contracts import hpc_gate

from r4 import hpc_release as hr
from r4 import hpc_signing as hs

KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
needs_keygen = pytest.mark.skipif(not os.access(KEYGEN, os.X_OK), reason="ssh-keygen missing")
DOMAIN = "r4.example.org"
PRINCIPAL = "synthetic-release-test"
NOW = 1_900_000_000
EXPIRES = 2_000_000_000


def make_key(tmp_path: Path, name: str = "synthetic-release") -> Path:
    path = tmp_path / name
    subprocess.run(
        [KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "synthetic-test", "-f", str(path)],
        check=True,
    )
    return path


def trust_for(key: Path, domain: str = DOMAIN) -> hr.ReleaseTrust:
    public = Path(f"{key}.pub").read_text().strip()
    blob = base64.b64decode(public.split()[1])
    return hr.ReleaseTrust(
        principal=PRINCIPAL,
        domain=domain,
        public_key=public,
        fingerprint=hpc_gate.fingerprint(blob),
    )


@pytest.fixture(autouse=True)
def synthetic_non_sk_policy(monkeypatch):
    """Permit only this suite's generated keys through the real SSHSIG parser."""
    monkeypatch.setattr(hr, "_ALLOW_SYNTHETIC_NON_SK_KEYS", True)


def sign(payload: bytes, key: Path, kind: str = "release", domain: str = DOMAIN) -> str:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(hpc_gate, "SSH_KEYGEN", KEYGEN)
        tmp = key.parent / f"payload-{key.name}"
        tmp.write_bytes(payload)
        done = subprocess.run(
            [KEYGEN, "-Y", "sign", "-f", str(key), "-n", f"umrs-{kind}-v1@{domain}", str(tmp)],
            check=False,
        )
        assert done.returncode == 0
        return Path(f"{tmp}.sig").read_text()


def synthetic_manifest(gate: bytes | None = None, **changes) -> bytes:
    values = {
        "gate_version": hpc_gate.GATE_VERSION,
        "sha256": hashlib.sha256(gate or hs.gate_source()).hexdigest(),
        "min_client": "0.2.0rc4",
        "expires": EXPIRES,
    }
    values.update(changes)
    return hpc_gate.canonical_bytes(values, json_values=True)


def verify(payload: bytes, signature: str, trust: hr.ReleaseTrust, gate: bytes | None = None):
    return hr.verify_manifest(
        payload,
        signature,
        trust,
        gate_source=gate or hs.gate_source(),
        current_version="0.2.0rc4",
        now=NOW,
    )


def release_error(match: str):
    return pytest.raises(hr.ReleaseError, match=match)


@needs_keygen
def test_a_synthetic_signed_manifest_verifies_and_binds_hello(tmp_path):
    key = make_key(tmp_path)
    payload = synthetic_manifest()
    verified = verify(payload, sign(payload, key), trust_for(key))
    assert verified.gate_version == hpc_gate.GATE_VERSION
    hr.verify_gate_hello(
        {"gate_version": hpc_gate.GATE_VERSION, "gate_sha256": hs.gate_sha256()}, verified
    )


@needs_keygen
def test_manifest_hash_mismatch_is_refused(tmp_path):
    key = make_key(tmp_path)
    payload = synthetic_manifest()
    with release_error("release_gate_hash_mismatch"):
        verify(payload, sign(payload, key), trust_for(key), hs.gate_source() + b"\n")


@needs_keygen
def test_gate_hello_hash_mismatch_is_refused(tmp_path):
    key = make_key(tmp_path)
    payload = synthetic_manifest()
    verified = verify(payload, sign(payload, key), trust_for(key))
    with release_error("release_gate_hash_mismatch"):
        hr.verify_gate_hello(
            {"gate_version": hpc_gate.GATE_VERSION, "gate_sha256": "0" * 64}, verified
        )


@needs_keygen
def test_signature_from_the_wrong_identity_is_refused(tmp_path):
    signer = make_key(tmp_path, "signer")
    other = make_key(tmp_path, "other")
    payload = synthetic_manifest()
    with release_error("release_bad_signature"):
        verify(payload, sign(payload, signer), trust_for(other))


@needs_keygen
def test_another_namespace_is_refused(tmp_path):
    key = make_key(tmp_path)
    payload = synthetic_manifest()
    with release_error("release_bad_signature"):
        verify(payload, sign(payload, key, "control"), trust_for(key))


@needs_keygen
def test_cross_domain_signature_succeeds_at_a_and_fails_at_b(tmp_path):
    key = make_key(tmp_path)
    payload = synthetic_manifest()
    armored = sign(payload, key, domain="a.example")
    assert verify(payload, armored, trust_for(key, "a.example")).sha256 == hs.gate_sha256()
    with release_error("release_bad_signature"):
        verify(payload, armored, trust_for(key, "b.example"))


@needs_keygen
def test_downgrade_is_refused(tmp_path):
    key = make_key(tmp_path)
    payload = synthetic_manifest(gate_version=str(int(hpc_gate.GATE_VERSION) - 1))
    with release_error("release_downgrade"):
        verify(payload, sign(payload, key), trust_for(key))


@pytest.mark.parametrize("gate_version", [3, "3.0", "03", "-1", ""])
def test_malformed_gate_version_is_refused_before_signature_work(gate_version):
    payload = synthetic_manifest(gate_version=gate_version)
    with release_error("release_gate_version_malformed"):
        hr.parse_manifest(payload, hs.gate_source(), current_version="0.2.0rc4", now=NOW)


@pytest.mark.parametrize("expires", [True, "2033-01-01", -1, 1.5])
def test_malformed_expiry_is_refused(expires):
    payload = synthetic_manifest(expires=expires)
    with release_error("release_expiry_malformed"):
        hr.parse_manifest(payload, hs.gate_source(), current_version="0.2.0rc4", now=NOW)


def test_expired_manifest_is_refused():
    payload = synthetic_manifest(expires=NOW)
    with release_error("release_expired"):
        hr.parse_manifest(payload, hs.gate_source(), current_version="0.2.0rc4", now=NOW)


@pytest.mark.parametrize("version", ["1", "0.2.0post1", "0.2.0-rc4", "0.2.0rc-1", ""])
def test_malformed_min_client_is_refused(version):
    payload = synthetic_manifest(min_client=version)
    with release_error("release_min_client_malformed"):
        hr.parse_manifest(payload, hs.gate_source(), current_version="0.2.0rc4", now=NOW)


def test_a_newer_min_client_is_refused():
    payload = synthetic_manifest(min_client="0.2.0rc5")
    with release_error("release_min_client"):
        hr.parse_manifest(payload, hs.gate_source(), current_version="0.2.0rc4", now=NOW)


def test_manifest_fields_are_exact():
    body = json.loads(synthetic_manifest())
    body["unexpected"] = True
    with release_error("release_manifest_fields"):
        hr.parse_manifest(hs.canonical_bytes(body), hs.gate_source(), "0.2.0rc4", now=NOW)


def test_missing_release_trust_fails_closed_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv(hr.MANIFEST_ENV, str(tmp_path / "gate-release.json"))
    monkeypatch.setenv(hr.SIGNATURE_ENV, str(tmp_path / "gate-release.json.sig"))
    with release_error("release_key_unpinned"):
        hr.verify_environment(gate_source=hs.gate_source(), current_version="0.2.0rc4", now=NOW)


def test_partial_manifest_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(hr.TRUST_ENV, str(tmp_path / "release-trust.json"))
    with release_error("release_manifest_missing"):
        hr.verify_environment(gate_source=hs.gate_source(), current_version="0.2.0rc4", now=NOW)


def test_unconfigured_legacy_mode_is_explicitly_distinguished(monkeypatch):
    for name in (hr.TRUST_ENV, hr.MANIFEST_ENV, hr.SIGNATURE_ENV):
        monkeypatch.delenv(name, raising=False)
    assert hr.verify_environment(gate_source=hs.gate_source(), current_version="0.2.0rc4") is None


def test_default_trust_file_alone_does_not_activate_release(tmp_path, monkeypatch):
    _write_default_trust(hs.signing_dir().parent / "release-trust.json")
    for name in (hr.TRUST_ENV, hr.MANIFEST_ENV, hr.SIGNATURE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        hr, "load_trust", lambda _path: pytest.fail("default pin was activated alone")
    )
    assert hr.release_requested() is False
    assert hr.verify_environment(gate_source=hs.gate_source(), current_version="0.2.0rc4") is None


@needs_keygen
def test_production_trust_rejects_structurally_valid_ordinary_ed25519(tmp_path):
    key = make_key(tmp_path)
    trusted = trust_for(key)
    path = tmp_path / "trust.json"
    path.write_text(
        json.dumps(
            {
                "principal": trusted.principal,
                "domain": trusted.domain,
                "public_key": trusted.public_key,
                "fingerprint": trusted.fingerprint,
            }
        )
    )
    with release_error("production release identity must be a FIDO sk- key"):
        hr.load_trust(path)


def _synthetic_sk_public() -> str:
    key_type = b"sk-ssh-ed25519@openssh.com"
    blob = struct.pack(">I", len(key_type)) + key_type + b"synthetic-public-only-fixture"
    return key_type.decode() + " " + base64.b64encode(blob).decode() + " synthetic-test"


def test_synthetic_sk_trust_round_trips_without_private_material(tmp_path):
    line = _synthetic_sk_public()
    fingerprint = hpc_gate.fingerprint(base64.b64decode(line.split()[1]))
    path = tmp_path / "trust.json"
    path.write_text(
        json.dumps(
            {
                "principal": PRINCIPAL,
                "domain": DOMAIN,
                "public_key": line,
                "fingerprint": fingerprint,
            }
        )
    )
    trust = hr.load_trust(path)
    assert trust.fingerprint == fingerprint and isinstance(trust.public_key, str)


def _write_default_trust(path: Path, suffix: str = "") -> None:
    line = _synthetic_sk_public() + suffix
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "principal": PRINCIPAL,
                "domain": DOMAIN,
                "public_key": line,
                "fingerprint": hpc_gate.fingerprint(base64.b64decode(line.split()[1])),
            }
        )
    )


def _stub_verified_payload(tmp_path, monkeypatch):
    payload = tmp_path / "gate-release.json"
    payload.write_bytes(synthetic_manifest())
    signature = tmp_path / "gate-release.json.sig"
    signature.write_text("synthetic")
    monkeypatch.setenv(hr.MANIFEST_ENV, str(payload))
    monkeypatch.setenv(hr.SIGNATURE_ENV, str(signature))
    seen = {}
    expected = hr.ReleaseManifest(hpc_gate.GATE_VERSION, hs.gate_sha256(), "0.2.0rc4", EXPIRES)

    def check(_payload, _armored, trust, **_kwargs):
        seen["trust"] = trust
        return expected

    monkeypatch.setattr(hr, "verify_manifest", check)
    return seen, expected


def test_default_trust_file_is_used_with_named_manifest_and_signature(tmp_path, monkeypatch):
    _write_default_trust(hs.signing_dir().parent / "release-trust.json")
    monkeypatch.delenv(hr.TRUST_ENV, raising=False)
    seen, expected = _stub_verified_payload(tmp_path, monkeypatch)
    found = hr.verify_environment(gate_source=hs.gate_source(), current_version="0.2.0rc4", now=NOW)
    assert found == expected
    assert seen["trust"].public_key == _synthetic_sk_public()


def test_trust_env_overrides_the_default_trust_file(tmp_path, monkeypatch):
    _write_default_trust(hs.signing_dir().parent / "release-trust.json")
    explicit = tmp_path / "explicit-trust.json"
    _write_default_trust(explicit, "-environment")
    monkeypatch.setenv(hr.TRUST_ENV, str(explicit))
    seen, expected = _stub_verified_payload(tmp_path, monkeypatch)
    assert (
        hr.verify_environment(gate_source=hs.gate_source(), current_version="0.2.0rc4", now=NOW)
        == expected
    )
    assert seen["trust"].public_key == _synthetic_sk_public() + "-environment"


def test_fido_up_and_uv_are_required(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "_ALLOW_SYNTHETIC_NON_SK_KEYS", False)
    key = make_key(tmp_path)
    payload = synthetic_manifest()
    with release_error("release_signature_policy"):
        verify(payload, sign(payload, key), trust_for(key))
