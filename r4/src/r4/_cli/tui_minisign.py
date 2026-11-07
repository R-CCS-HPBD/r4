"""Minisign signature verification for ``r4 tui install`` (pure Python, stdlib).

Phase 1 of TUI binary signing (cycle-3 hard lane, 2026-10-21; decision:
minisign, the public key pinned in r4). Minisign signs with Ed25519; its
default ("ED", prehashed) signature covers the BLAKE2b-512 digest of the
file, the legacy one ("Ed") the file itself. A ``.minisig`` file is::

    untrusted comment: <text>
    base64(<alg:2> <key id:8> <signature:64>)
    trusted comment: <text>
    base64(<global signature:64>)   # over <signature> + <trusted comment>

and a public key (``minisign.pub``, second line) is
``base64("Ed" <key id:8> <public key:32>)``. No third-party crypto package:
r4 does not depend on one, and one signature check per install is cheap
enough in pure Python (RFC 8032 section 5.1.7).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]  # extended coordinates (X, Y, Z, T)


class SignatureError(Exception):
    """The signature, the public key or the file does not verify."""


def _add(p: Point, q: Point) -> Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def scalar_mult(s: int, p: Point) -> Point:
    q: Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _same(p: Point, q: Point) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


def decompress(raw: bytes) -> Point | None:
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def compress(p: Point) -> bytes:
    zinv = pow(p[2], _P - 2, _P)
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0) or 0
BASE: Point = (_GX, _GY, 1, _GX * _GY % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 Ed25519 verification."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a, r = decompress(public_key), decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    digest = hashlib.sha512(signature[:32] + public_key + message).digest()
    h = int.from_bytes(digest, "little") % _L
    return _same(scalar_mult(s, BASE), _add(r, scalar_mult(h, a)))


def _b64(text: str, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(f"{what} is not base64") from exc
    if len(raw) != size:
        raise SignatureError(f"{what} has {len(raw)} bytes, not {size}")
    return raw


def parse_public_key(text: str) -> tuple[bytes, bytes]:
    """``(key_id, public_key)`` from a minisign public key (the base64 line,
    or the whole ``minisign.pub`` file)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    raw = _b64(lines[-1] if lines else "", 42, "public key")
    if raw[:2] != b"Ed":
        raise SignatureError("public key is not an Ed25519 minisign key")
    return raw[2:10], raw[10:]


def _file_digest(path: Path) -> bytes:
    h = hashlib.blake2b(digest_size=64)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def _signature_lines(text: str) -> tuple[str, str, str]:
    lines = text.splitlines()
    if len(lines) < 4 or not lines[2].startswith("trusted comment: "):
        raise SignatureError("not a minisign signature file")
    return lines[1], lines[2][len("trusted comment: ") :], lines[3]


def verify_file(path: Path, signature_text: str, public_key_text: str) -> str:
    """Verify ``path`` against a ``.minisig`` text; return the trusted
    comment. Raises :class:`SignatureError` when anything does not verify."""
    key_id, public_key = parse_public_key(public_key_text)
    sig_line, trusted, global_line = _signature_lines(signature_text)
    raw = _b64(sig_line, 74, "signature")
    alg, sig_key_id, signature = raw[:2], raw[2:10], raw[10:]
    if sig_key_id != key_id:
        raise SignatureError(
            f"signed by key {sig_key_id[::-1].hex().upper()}, "
            f"not the pinned key {key_id[::-1].hex().upper()}"
        )
    if alg == b"ED":
        message = _file_digest(path)
    elif alg == b"Ed":
        message = path.read_bytes()
    else:
        raise SignatureError(f"unknown signature algorithm {alg!r}")
    if not ed25519_verify(public_key, message, signature):
        raise SignatureError("signature does not match the file")
    global_sig = _b64(global_line, 64, "trusted-comment signature")
    if not ed25519_verify(public_key, signature + trusted.encode(), global_sig):
        raise SignatureError("trusted comment signature does not verify")
    return trusted
