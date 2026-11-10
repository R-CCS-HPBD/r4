"""r4's side of a gated HPC account: the signing key, signing, and the strict
checks on what the server sends (ADR-112 D-4, D-8; plan B1.5 and B1.5a).

Keys are generated with the real ``ssh-keygen`` into ``tmp_path`` at test
time; no key file is committed and ``~/.ssh`` is never read.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from r4_contracts import hpc_gate

from r4 import hpc_signing as hs
from r4.knowledge import COMPANION_SHELL_ENV

KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"
needs_keygen = pytest.mark.skipif(not os.access(KEYGEN, os.X_OK), reason="ssh-keygen missing")

DOMAIN = "r4.example.org"
SITE = "bdrt"
KEY_ID = "tk-0123456789ab"
ED25519_HEAD = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
TRANSPORT_B64 = base64.b64encode(ED25519_HEAD + bytes(range(32))).decode()
TRANSPORT = f"ssh-ed25519 {TRANSPORT_B64} umrs-transport-{SITE}-{KEY_ID}"


def make_key(tmp_path: Path, name: str = "signer", passphrase: str = "") -> Path:
    path = tmp_path / name
    subprocess.run(
        [KEYGEN, "-q", "-t", "ed25519", "-N", passphrase, "-C", "test", "-f", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def person(monkeypatch):
    """The person at a terminal: no companion marker, a TTY."""
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)


@pytest.fixture
def test_key_hook(monkeypatch):
    """The private test hook: a passphrase-less signing key is allowed."""
    monkeypatch.setattr(hs, "_ALLOW_UNENCRYPTED_KEYS", True)


def offer_answer(**overrides):
    answer = {
        "key_id": KEY_ID,
        "public_key": TRANSPORT,
        "site": SITE,
        "domain": DOMAIN,
        "from": "",
        "gate": {"version": hpc_gate.GATE_VERSION, "sha256": hs.gate_sha256()},
    }
    answer.update(overrides)
    return answer


# ── who may sign ─────────────────────────────────────────────────────────────


def test_signing_refuses_in_a_companion_shell(monkeypatch, tmp_path):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(hs, "_run", lambda *a, **k: pytest.fail("ssh-keygen must not run"))
    with pytest.raises(hs.HpcSigningError, match="person's act"):
        hs.sign(b"{}", f"umrs-control-v1@{DOMAIN}", tmp_path / "k")
    with pytest.raises(hs.HpcSigningError, match="person's act"):
        hs.ensure_signing_key("ed25519")


def test_signing_refuses_without_a_terminal(monkeypatch, tmp_path):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(hs, "_run", lambda *a, **k: pytest.fail("ssh-keygen must not run"))
    with pytest.raises(hs.HpcSigningError, match="needs a terminal"):
        hs.sign(b"{}", f"umrs-control-v1@{DOMAIN}", tmp_path / "k")
    with pytest.raises(hs.HpcSigningError, match="needs a terminal"):
        hs.ensure_signing_key("sk")


def test_a_zero_marker_is_not_a_companion_shell(monkeypatch):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "0")
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    assert hs.person_refusal("add") is None


# ── sign and verify with real ssh-keygen ─────────────────────────────────────


def _verify(tmp_path, key, payload, signature, namespace, principal="alice"):
    key_type, key_b64, _ = hs.public_key_info(key)
    signers = tmp_path / "allowed_signers"
    signers.write_text(hs.allowed_signers_line(principal, DOMAIN, key_type, key_b64) + "\n")
    sig = tmp_path / "payload.sig"
    sig.write_text(signature)
    argv = [KEYGEN, "-Y", "verify", "-f", str(signers), "-I", principal, "-n", namespace]
    return subprocess.run(argv + ["-s", str(sig)], input=payload, capture_output=True)


@needs_keygen
def test_sign_round_trips_through_ssh_keygen_verify(tmp_path, person, test_key_hook):
    key = make_key(tmp_path)
    payload = hs.canonical_bytes({"v": 1, "type": "control", "op": "revoke"})
    namespace = f"umrs-control-v1@{DOMAIN}"
    signature = hs.sign(payload, namespace, key)
    assert signature.startswith("-----BEGIN SSH SIGNATURE-----")
    assert _verify(tmp_path, key, payload, signature, namespace).returncode == 0
    # the namespace is bound: the same signature is no intent
    assert _verify(tmp_path, key, payload, signature, f"umrs-intent-v1@{DOMAIN}").returncode != 0
    # and the bytes are bound
    assert _verify(tmp_path, key, payload + b" ", signature, namespace).returncode != 0


@needs_keygen
def test_signed_control_is_what_the_gate_accepts(tmp_path, person, test_key_hook):
    key = make_key(tmp_path)
    body = hs.control_body("revoke", site=SITE, principal="alice", key_id=KEY_ID, now=1000)
    envelope = hs.signed_control(body, key, DOMAIN)
    assert set(envelope) == {"payload", "signature"}
    payload = base64.b64decode(envelope["payload"])
    assert payload == hpc_gate.canonical_bytes(hpc_gate.parse_strict(payload))
    hpc_gate.validate_control(hpc_gate.parse_strict(payload), "revoke")
    namespace = hpc_gate.namespace("control", DOMAIN)
    assert _verify(tmp_path, key, payload, envelope["signature"], namespace).returncode == 0
    sig = hpc_gate.parse_sshsig(envelope["signature"])
    hpc_gate.check_signature_policy(sig, uv_required=False, allow_non_sk=True)
    with pytest.raises(hpc_gate.GateError):
        hpc_gate.check_signature_policy(sig, uv_required=False, allow_non_sk=False)


@needs_keygen
def test_sign_refuses_a_key_without_a_passphrase(tmp_path, person):
    key = make_key(tmp_path)
    with pytest.raises(hs.HpcSigningError, match="no passphrase"):
        hs.sign(b"{}", f"umrs-control-v1@{DOMAIN}", key)


@needs_keygen
def test_key_is_encrypted_reads_the_cipher(tmp_path):
    assert hs.key_is_encrypted(make_key(tmp_path, "plain")) is False
    assert hs.key_is_encrypted(make_key(tmp_path, "locked", "correct horse")) is True


def test_sign_uses_the_key_file_never_the_agent(tmp_path, person, test_key_hook, monkeypatch):
    seen = {}
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("SSH_ASKPASS", "/bin/false")
    monkeypatch.setattr(hs, "_require_protected", lambda path: None)

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"])
        Path(argv[-1] + ".sig").write_text("SIG")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(hs, "_run", fake_run)
    assert hs.sign(b"x", f"umrs-control-v1@{DOMAIN}", tmp_path / "k") == "SIG"
    assert seen["argv"][1:7] == [
        "-Y",
        "sign",
        "-f",
        str(tmp_path / "k"),
        "-n",
        f"umrs-control-v1@{DOMAIN}",
    ]
    assert "-U" not in seen["argv"]
    assert seen["env"]["SSH_AUTH_SOCK"] == ""
    assert "SSH_ASKPASS" not in seen["env"]


@pytest.mark.parametrize(
    "namespace", ["umrs-control-v1", "git@example.org", "umrs-control-v1@a b", "file"]
)
def test_sign_refuses_other_namespaces(tmp_path, person, namespace):
    with pytest.raises(hs.HpcSigningError, match="namespace"):
        hs.sign(b"{}", namespace, tmp_path / "k")


# ── key creation ─────────────────────────────────────────────────────────────


def test_sk_key_creation_argv_and_the_no_authenticator_message(person, monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "openssh_version", lambda: (9, 6))
    monkeypatch.setattr(
        hs, "_run", lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 1)
    )
    with pytest.raises(hs.HpcSigningError, match="--key-type ed25519"):
        hs.ensure_signing_key("sk")
    path = hs.signing_dir() / "id_umrs_sk"
    assert calls == [
        [
            hs.ssh_keygen(),
            "-t",
            "ed25519-sk",
            "-O",
            "verify-required",
            "-f",
            str(path),
            "-C",
            "umrs-signing",
        ]
    ]
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"


@needs_keygen
def test_an_ed25519_key_left_without_a_passphrase_is_discarded(person, monkeypatch):
    monkeypatch.setattr(hs, "openssh_version", lambda: (9, 6))
    seen = []

    def keygen_without_passphrase(argv, **kwargs):
        seen.append(argv)
        return subprocess.run([*argv[:3], "-N", "", "-q", *argv[3:]], check=False)

    monkeypatch.setattr(hs, "_run", keygen_without_passphrase)
    with pytest.raises(hs.HpcSigningError, match="no passphrase"):
        hs.ensure_signing_key("ed25519")
    assert seen[0][1:3] == ["-t", "ed25519"] and "-N" not in seen[0]
    assert not (hs.signing_dir() / "id_umrs_ed25519").exists()


def test_key_creation_can_be_declined(person, monkeypatch):
    monkeypatch.setattr(hs, "openssh_version", lambda: (9, 6))
    monkeypatch.setattr(hs, "_run", lambda *a, **k: pytest.fail("no key without consent"))
    with pytest.raises(hs.HpcSigningError, match="declined"):
        hs.ensure_signing_key("ed25519", confirm=lambda prompt: False)


@pytest.mark.parametrize(
    ("version", "kind", "ok"),
    [
        ((8, 0), "ed25519", False),
        ((8, 1), "ed25519", True),
        ((8, 1), "sk", False),
        ((8, 2), "sk", True),
        (None, "ed25519", False),
    ],
)
def test_openssh_floor(monkeypatch, version, kind, ok):
    monkeypatch.setattr(hs, "openssh_version", lambda: version)
    if ok:
        assert hs.check_openssh(kind) == version
    else:
        with pytest.raises(hs.HpcSigningError, match="OpenSSH 8"):
            hs.check_openssh(kind)


def test_openssh_version_reads_ssh_dash_v(monkeypatch):
    monkeypatch.setattr(
        hs,
        "_run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, "", "OpenSSH_9.6p1, OpenSSL 3\n"),
    )
    assert hs.openssh_version() == (9, 6)


@needs_keygen
def test_public_key_info_matches_ssh_keygen_fingerprint(tmp_path):
    key = make_key(tmp_path)
    key_type, key_b64, fp = hs.public_key_info(key)
    listed = subprocess.run([KEYGEN, "-lf", f"{key}.pub"], capture_output=True, text=True)
    assert key_type == "ssh-ed25519" and fp == listed.stdout.split()[1]
    assert key_b64 == Path(f"{key}.pub").read_text().split()[1]


# ── control bodies ───────────────────────────────────────────────────────────


def test_control_body_follows_the_contract():
    body = hs.control_body("revoke", site=SITE, principal="alice", key_id=KEY_ID, now=1000)
    nonce = body.pop("nonce")
    assert body == {
        "v": 1,
        "type": "control",
        "op": "revoke",
        "site": SITE,
        "principal": "alice",
        "key_id": KEY_ID,
        "issued": 1000,
        "expires": 1000 + hs.CONTROL_TTL_S,
    }
    assert re.fullmatch(r"[0-9a-f]{32}", nonce)
    body["nonce"] = nonce
    hpc_gate.validate_control(body, "revoke")
    other = hs.control_body("revoke", site=SITE, principal="alice", key_id=KEY_ID, now=1000)
    assert other["nonce"] != body["nonce"]


def test_rotate_control_carries_the_new_key():
    body = hs.control_body(
        "rotate", site=SITE, principal="alice", key_id=KEY_ID, new_public_key=TRANSPORT, now=5
    )
    hpc_gate.validate_control(body, "rotate")
    with pytest.raises(hs.HpcSigningError):
        hs.control_body("rotate", site=SITE, principal="alice", key_id=KEY_ID)
    with pytest.raises(hs.HpcSigningError):
        hs.control_body(
            "revoke", site=SITE, principal="alice", key_id=KEY_ID, new_public_key=TRANSPORT
        )


@pytest.mark.parametrize("ttl", [0, 601])
def test_control_lifetime_is_bounded(ttl):
    with pytest.raises(hs.HpcSigningError, match="600"):
        hs.control_body("revoke", site=SITE, principal="alice", key_id=KEY_ID, ttl=ttl)


# ── strict checks on the server's transport key ─────────────────────────────


def test_a_well_formed_transport_key_passes():
    assert hs.validate_transport_key(TRANSPORT, SITE, KEY_ID) == TRANSPORT_B64


@pytest.mark.parametrize(
    "public_key",
    [
        TRANSPORT + "\n",
        TRANSPORT + "\nssh-ed25519 " + TRANSPORT_B64 + " evil",
        "\n" + TRANSPORT,
        'command="/bin/sh" ' + TRANSPORT,
        "restrict " + TRANSPORT,
        f"ssh-ed25519 {TRANSPORT_B64} umrs-transport-{SITE}-{KEY_ID} extra",
        f"ssh-ed25519 {TRANSPORT_B64}  umrs-transport-{SITE}-{KEY_ID}",
        f"ssh-ed25519 {TRANSPORT_B64} umrs-transport-other-{KEY_ID}",
        f"ssh-ed25519 {TRANSPORT_B64} umrs-transport-{SITE}-tk-ffffffffffff",
        f"ssh-ed25519 {TRANSPORT_B64}",
        f"ssh-rsa {TRANSPORT_B64} umrs-transport-{SITE}-{KEY_ID}",
        # 51 bytes, but the blob declares a 31-byte key and trails one byte
        "ssh-ed25519 "
        + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x1f" + bytes(32)).decode()
        + f" umrs-transport-{SITE}-{KEY_ID}",
        "ssh-ed25519 "
        + base64.b64encode(b"\0\0\0\x0bssh-ed25518" + bytes(36)).decode()
        + f" umrs-transport-{SITE}-{KEY_ID}",
        None,
        42,
    ],
)
def test_transport_key_injection_is_refused(public_key):
    with pytest.raises(hs.HpcSigningError, match="refused"):
        hs.validate_transport_key(public_key, SITE, KEY_ID)


@pytest.mark.parametrize(
    "overrides",
    [
        {"site": "bdrt x"},
        {"site": 'bdrt"'},
        {"key_id": "tk-0123"},
        {"domain": "evil.org\n"},
        {"domain": ""},
        {"from": '10.0.0.1",command="sh'},
        {"from": "10.0.0.1 x"},
        # the gate's own from= rule (2026-11-08 integration): no empty items
        {"from": "10.0.0.1,,10.0.0.2"},
        {"from": "10.0.0.1,"},
        {"gate": {"version": "1", "sha256": "abc"}},
        {"gate": {"version": "1;rm", "sha256": "0" * 64}},
        {"gate": None},
    ],
)
def test_offer_fields_are_checked(overrides):
    with pytest.raises(hs.HpcSigningError, match="refused"):
        hs.parse_offer(offer_answer(**overrides))


def test_offer_parses_and_builds_the_contract_line():
    offer = hs.parse_offer(offer_answer(**{"from": "10.1.2.3,!10.1.2.4"}))
    assert offer.comment == f"umrs-transport-{SITE}-{KEY_ID}"
    line = hs.authorized_keys_line(offer, "/home/alice/")
    assert line == (
        'restrict,from="10.1.2.3,!10.1.2.4",command="/usr/bin/python3 -I '
        f'/home/alice/.umrs/gate/umrs_gate.py --site {SITE}" '
        f"ssh-ed25519 {TRANSPORT_B64} umrs-transport-{SITE}-{KEY_ID}"
    )
    bare = hs.authorized_keys_line(hs.parse_offer(offer_answer()), "/home/alice")
    assert bare.startswith('restrict,command="/usr/bin/python3 -I /home/alice/.umrs/gate/')


@pytest.mark.parametrize("home", ["relative/home", "/home/a b", '/home/a"', "/home/../etc", ""])
def test_a_bad_home_is_refused(home):
    with pytest.raises(hs.HpcSigningError, match="refused"):
        hs.authorized_keys_line(hs.parse_offer(offer_answer()), home)


# ── the gate files ───────────────────────────────────────────────────────────


def test_local_gate_sha_is_the_gate_file():
    assert hs.gate_sha256() == hpc_gate.gate_sha256()
    assert hs.gate_source() == Path(hpc_gate.__file__).read_bytes()


@needs_keygen
def test_build_install_writes_the_contract_files(tmp_path):
    key = make_key(tmp_path)
    plan = hs.build_install(
        hs.parse_offer(offer_answer()),
        principal="alice",
        home="/home/alice",
        signer=key,
        kind="ed25519",
    )
    assert set(plan.files) == {"umrs_gate.py", "gate.json", "allowed_signers"}
    assert plan.files["umrs_gate.py"] == hs.gate_source()
    config = json.loads(plan.files["gate.json"])
    assert config == {
        "site": SITE,
        "domain": DOMAIN,
        "principal": "alice",
        "uv_required": False,
        "allow_non_sk": True,
        "gate_version": hpc_gate.GATE_VERSION,
    }
    hpc_gate._validate_config(config)
    key_type, key_b64, _ = hs.public_key_info(key)
    assert plan.files["allowed_signers"].decode() == (
        f'alice namespaces="umrs-intent-v1@{DOMAIN},umrs-grant-v1@{DOMAIN},'
        f'umrs-control-v1@{DOMAIN}" {key_type} {key_b64}\n'
    )
    assert plan.gate_sha256 == hs.gate_sha256()


@needs_keygen
@pytest.mark.parametrize(
    "gate",
    [
        {"version": hpc_gate.GATE_VERSION, "sha256": "0" * 64},
        {"version": str(int(hpc_gate.GATE_VERSION) + 1), "sha256": None},
    ],
)
def test_gate_version_skew_is_refused(tmp_path, gate):
    gate = dict(gate, sha256=gate["sha256"] or hs.gate_sha256())
    offer = hs.parse_offer(offer_answer(gate=gate))
    with pytest.raises(hs.HpcSigningError, match="version skew"):
        hs.build_install(
            offer, principal="alice", home="/home/alice", signer=make_key(tmp_path), kind="ed25519"
        )


# ── B1 review fixes (2026-11-08) ─────────────────────────────────────────────


def _fake_openssh(tmp_path: Path) -> Path:
    bindir = tmp_path / "brew" / "bin"
    bindir.mkdir(parents=True)
    for name in ("ssh-keygen", "ssh"):
        tool = bindir / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    return bindir


def test_the_version_checked_is_the_openssh_that_signs(tmp_path, monkeypatch):
    bindir = _fake_openssh(tmp_path)
    monkeypatch.setattr(hs.shutil, "which", lambda name: str(bindir / name))
    seen: list = []

    def fake_run(argv, **_k):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "OpenSSH_9.8p1\n")

    monkeypatch.setattr(hs, "_run", fake_run)
    assert hs.ssh_keygen() == str(bindir / "ssh-keygen")
    assert hs.openssh_version() == (9, 8)
    assert seen == [[str(bindir / "ssh"), "-V"]]


def test_no_ssh_beside_the_keygen_is_an_unknown_version(tmp_path, monkeypatch):
    bindir = _fake_openssh(tmp_path)
    (bindir / "ssh").unlink()
    monkeypatch.setattr(hs.shutil, "which", lambda name: str(bindir / name))
    monkeypatch.setattr(hs, "_run", lambda *a, **k: pytest.fail("no other ssh is asked"))
    assert hs.openssh_version() is None


@needs_keygen
def test_a_signing_key_without_its_pub_is_a_signing_error(person, test_key_hook):
    key = hs.key_path("ed25519")
    key.parent.mkdir(parents=True, exist_ok=True)
    make_key(key.parent, key.name)
    Path(f"{key}.pub").unlink()
    with pytest.raises(hs.HpcSigningError, match="has no .pub"):
        hs.ensure_signing_key("ed25519")
    with pytest.raises(hs.HpcSigningError, match="unreadable"):
        hs.public_key_info(key)


@needs_keygen
def test_with_both_key_kinds_the_site_record_or_the_person_chooses(person):
    for kind in hs.KEY_TYPES:
        key = hs.key_path(kind)
        key.parent.mkdir(parents=True, exist_ok=True)
        make_key(key.parent, key.name)
    with pytest.raises(hs.HpcSigningError, match="--key-type"):
        hs.site_signer(SITE)
    assert hs.site_signer(SITE, "sk")[0] == "sk"
    hs.record_site_signer(SITE, "ed25519", hs.key_path("ed25519"))
    assert hs.site_signer(SITE) == ("ed25519", hs.key_path("ed25519"))
    assert hs.site_signer("other-site", "sk")[0] == "sk"
    hs.key_path("sk").unlink()
    assert hs.site_signer("other-site") == ("ed25519", hs.key_path("ed25519"))
