"""Signed gate release manifests (ADR-112 wave E).

A production release is verified against an explicit maintainer FIDO public
identity. There is deliberately no built-in, generated or fallback production
key. Configuring any part of release verification makes the whole check
mandatory; an entirely unconfigured installation keeps the pre-wave-E legacy
path and the CLI labels that compatibility mode.

The signed payload is the canonical JSON object ``{gate_version, sha256,
min_client, expires}``. ``expires`` is a Unix timestamp and ``min_client``
uses the existing R4 release grammar (PEP 440 release plus a/b/rc suffix).
The detached SSHSIG uses ``umrs-release-v1@<domain>``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from r4_contracts import hpc_gate

from . import __version__ as R4_VERSION
from . import hpc_signing as hs

TRUST_ENV = "R4_HPC_RELEASE_TRUST"
MANIFEST_ENV = "R4_HPC_RELEASE_MANIFEST"
SIGNATURE_ENV = "R4_HPC_RELEASE_SIGNATURE"
_MANIFEST_FIELDS = frozenset({"gate_version", "sha256", "min_client", "expires"})
_TRUST_FIELDS = frozenset({"principal", "domain", "public_key", "fingerprint"})
_GATE_VERSION_RE = re.compile(r"0|[1-9][0-9]*")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VERSION_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?"
    r"(?:(a|b|rc)(0|[1-9][0-9]*))?"
)
_FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")

#: Tests only: real ssh-keygen can verify a synthetic Ed25519 signature on
#: runners without a FIDO authenticator. Production configuration never reads
#: this hook and always requires an ``sk-`` signature with UP and UV.
_ALLOW_SYNTHETIC_NON_SK_KEYS = False


class ReleaseError(hs.HpcSigningError):
    """A named release-integrity refusal."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ReleaseTrust:
    """The pinned maintainer release verification identity."""

    principal: str
    domain: str
    public_key: str
    fingerprint: str


@dataclass(frozen=True)
class ReleaseManifest:
    gate_version: str
    sha256: str
    min_client: str
    expires: int


def manifest_bytes(
    gate_source: bytes, *, gate_version: str, min_client: str, expires: int
) -> bytes:
    """Canonical unsigned release bytes; this performs no signing."""
    try:
        payload = bytes(
            hpc_gate.canonical_bytes(
                {
                    "gate_version": gate_version,
                    "sha256": hashlib.sha256(gate_source).hexdigest(),
                    "min_client": min_client,
                    "expires": expires,
                }
            )
        )
    except ValueError as exc:
        raise ReleaseError("release_manifest_malformed", str(exc)) from exc
    parse_manifest(payload, gate_source, min_client, now=0)
    return payload


def _public_key_parts(public_key: Any) -> tuple[str, str, bytes]:
    if not isinstance(public_key, str):
        raise ReleaseError("release_key_config", "public_key must be one OpenSSH public-key line")
    parts = public_key.split()
    if len(parts) not in (2, 3) or public_key != " ".join(parts):
        raise ReleaseError(
            "release_key_config", "public_key must contain no options or extra whitespace"
        )
    key_type, key_b64 = parts[:2]
    if key_type not in hpc_gate.SIGNER_KEY_TYPES:
        raise ReleaseError("release_key_config", f"unsupported public-key type {key_type!r}")
    try:
        blob = base64.b64decode(key_b64, validate=True)
        size = struct.unpack(">I", blob[:4])[0]
        embedded = blob[4 : 4 + size].decode("ascii")
    except (ValueError, binascii.Error, struct.error, UnicodeDecodeError) as exc:
        raise ReleaseError("release_key_config", "public_key does not decode") from exc
    if embedded != key_type:
        raise ReleaseError("release_key_config", "public-key type and key blob differ")
    return key_type, key_b64, blob


def _trust_fields(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != _TRUST_FIELDS:
        raise ReleaseError("release_key_config", f"expected exactly {sorted(_TRUST_FIELDS)}")
    return data


def load_trust(path: Path) -> ReleaseTrust:
    """Read the production pin; only a FIDO key identity is accepted."""
    try:
        data = _trust_fields(json.loads(Path(path).read_text()))
        principal = hs.check_principal(data["principal"])
        domain = hs.check_domain(data["domain"])
        key_type, _, blob = _public_key_parts(data["public_key"])
        fingerprint = data["fingerprint"]
    except FileNotFoundError as exc:
        raise ReleaseError(
            "release_key_unpinned", f"maintainer release trust file is missing: {path}"
        ) from exc
    except (OSError, ValueError, KeyError, hs.HpcSigningError) as exc:
        raise ReleaseError("release_key_config", f"invalid release trust {path}: {exc}") from exc
    if key_type not in hpc_gate.SK_KEY_TYPES:
        raise ReleaseError(
            "release_key_config", "the production release identity must be a FIDO sk- key"
        )
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise ReleaseError("release_key_config", "fingerprint is not an OpenSSH SHA256 fingerprint")
    if fingerprint != hpc_gate.fingerprint(blob):
        raise ReleaseError("release_key_config", "fingerprint does not match the pinned public key")
    return ReleaseTrust(principal, domain, data["public_key"], fingerprint)


def _version_key(value: Any, code: str) -> tuple[int, ...]:
    match = _VERSION_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ReleaseError(code, f"invalid R4 version {value!r}")
    core = [int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)]
    phase, number = match.group(4), match.group(5)
    rank = {"a": 0, "b": 1, "rc": 2, None: 3}[phase]
    return (*core, rank, int(number or 0))


def _gate_version(value: Any, local_version: str) -> str:
    if not isinstance(value, str) or not _GATE_VERSION_RE.fullmatch(value):
        raise ReleaseError("release_gate_version_malformed", f"invalid gate_version {value!r}")
    if int(value) < int(local_version):
        raise ReleaseError(
            "release_downgrade", f"manifest gate {value} is older than local gate {local_version}"
        )
    if int(value) > int(local_version):
        raise ReleaseError("release_client_too_old", f"gate {value} needs an r4 carrying that gate")
    return value


def _manifest_object(payload: bytes) -> dict[str, Any]:
    try:
        body: dict[str, Any] = hpc_gate.parse_strict(payload, json_values=True)
    except ValueError as exc:
        raise ReleaseError("release_manifest_malformed", str(exc)) from exc
    if set(body) != _MANIFEST_FIELDS:
        raise ReleaseError(
            "release_manifest_fields", f"expected exactly {sorted(_MANIFEST_FIELDS)}"
        )
    return body


def _expiry(value: Any, now: int) -> int:
    if type(value) is not int or not 0 < value <= hpc_gate.MAX_INT:
        raise ReleaseError("release_expiry_malformed", f"invalid expires {value!r}")
    if now >= value:
        raise ReleaseError("release_expired", f"manifest expired at {value}")
    return value


def parse_manifest(
    payload: bytes, gate_source: bytes, current_version: str, *, now: int
) -> ReleaseManifest:
    """Validate schema, client/gate versions, expiry and the local gate bytes."""
    body = _manifest_object(payload)
    version = _gate_version(body["gate_version"], hpc_gate.GATE_VERSION)
    minimum = body["min_client"]
    floor = _version_key(minimum, "release_min_client_malformed")
    running = _version_key(current_version, "release_client_version_malformed")
    if running < floor:
        raise ReleaseError(
            "release_min_client", f"r4 {current_version} is older than required {minimum}"
        )
    expires = _expiry(body["expires"], now)
    digest = body["sha256"]
    actual = hashlib.sha256(gate_source).hexdigest()
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReleaseError("release_manifest_malformed", f"invalid sha256 {digest!r}")
    if digest != actual:
        raise ReleaseError(
            "release_gate_hash_mismatch", f"manifest sha256 {digest}, local gate {actual}"
        )
    return ReleaseManifest(version, digest, minimum, expires)


def _allowed_signers(trust: ReleaseTrust) -> tuple[str, str]:
    key_type, key_b64, blob = _public_key_parts(trust.public_key)
    if trust.fingerprint != hpc_gate.fingerprint(blob):
        raise ReleaseError("release_key_config", "trust fingerprint differs from its public key")
    namespace = f"umrs-release-v1@{trust.domain}"
    line = f'{trust.principal} namespaces="{namespace}" {key_type} {key_b64}\n'
    return line, namespace


def _verify_signature(payload: bytes, armored: str, trust: ReleaseTrust) -> None:
    signers, namespace = _allowed_signers(trust)
    try:
        with tempfile.TemporaryDirectory(prefix="r4-release-verify-") as tmp:
            path = Path(tmp) / "allowed_signers"
            path.write_text(signers, encoding="ascii")
            hpc_gate.ssh_keygen_verify(str(path), trust.principal, namespace, armored, payload, tmp)
    except (OSError, hpc_gate.GateError) as exc:
        raise ReleaseError("release_bad_signature", str(exc)) from exc
    try:
        sig = hpc_gate.parse_sshsig(armored)
    except ValueError as exc:
        raise ReleaseError("release_bad_signature", str(exc)) from exc
    if sig.namespace != namespace or sig.signer_fingerprint != trust.fingerprint:
        raise ReleaseError(
            "release_bad_signature", "signature identity or namespace differs from the pin"
        )
    try:
        hpc_gate.check_signature_policy(
            sig, uv_required=True, allow_non_sk=_ALLOW_SYNTHETIC_NON_SK_KEYS
        )
    except hpc_gate.GateError as exc:
        raise ReleaseError("release_signature_policy", str(exc)) from exc


def verify_manifest(
    payload: bytes,
    armored: str,
    trust: ReleaseTrust,
    *,
    gate_source: bytes,
    current_version: str,
    now: int,
) -> ReleaseManifest:
    """Verify identity/signature first, then every release and local-gate invariant."""
    _verify_signature(payload, armored, trust)
    return parse_manifest(payload, gate_source, current_version, now=now)


def verify_gate_hello(hello: Any, manifest: ReleaseManifest) -> None:
    """The site's live hello must name exactly the signed release."""
    if not isinstance(hello, dict):
        raise ReleaseError("release_hello_malformed", "gate hello is not an object")
    if hello.get("gate_version") != manifest.gate_version:
        raise ReleaseError(
            "release_gate_version_mismatch", "gate hello version differs from the manifest"
        )
    if hello.get("gate_sha256") != manifest.sha256:
        raise ReleaseError(
            "release_gate_hash_mismatch", "gate hello hash differs from the manifest"
        )


def _read_release_file(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseError(code, f"cannot read {path}: {exc}") from exc


def release_requested(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether any explicit release-verification input was selected."""
    values = os.environ if environ is None else environ
    return any(values.get(name) for name in (TRUST_ENV, MANIFEST_ENV, SIGNATURE_ENV))


def _release_paths(environ: Mapping[str, str]) -> Optional[tuple[Path, Path, Path]]:
    configured = {name: environ.get(name) for name in (TRUST_ENV, MANIFEST_ENV, SIGNATURE_ENV)}
    if not release_requested(environ):
        return None
    if not configured[MANIFEST_ENV]:
        raise ReleaseError("release_manifest_missing", f"set {MANIFEST_ENV} to the signed payload")
    if not configured[SIGNATURE_ENV]:
        raise ReleaseError(
            "release_signature_missing", f"set {SIGNATURE_ENV} to the detached SSHSIG"
        )
    trust = configured[TRUST_ENV]
    if not trust:
        # Explicit manifest+signature selections may use the default public
        # pin. An explicit trust env always wins; a default alone selects nothing.
        default = hs.signing_dir().parent / "release-trust.json"
        if not default.is_file():
            raise ReleaseError(
                "release_key_unpinned", f"set {TRUST_ENV}; no maintainer release identity is pinned"
            )
        trust = str(default)
    return (
        Path(str(trust)),
        Path(str(configured[MANIFEST_ENV])),
        Path(str(configured[SIGNATURE_ENV])),
    )


def verify_environment(
    *,
    gate_source: bytes,
    current_version: str = R4_VERSION,
    domain: Optional[str] = None,
    hello: Optional[dict[str, Any]] = None,
    now: Optional[int] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[ReleaseManifest]:
    """Verify configured release files, or return ``None`` for legacy mode."""
    paths = _release_paths(os.environ if environ is None else environ)
    if paths is None:
        return None
    trust_path, manifest_path, signature_path = paths
    trust = load_trust(trust_path)
    if domain is not None and trust.domain != domain:
        raise ReleaseError(
            "release_domain_mismatch", f"release key is for {trust.domain}, site uses {domain}"
        )
    payload = _read_release_file(manifest_path, "release_manifest_missing")
    try:
        armored = _read_release_file(signature_path, "release_signature_missing").decode("ascii")
    except UnicodeDecodeError as exc:
        raise ReleaseError(
            "release_bad_signature", "detached signature is not ASCII armor"
        ) from exc
    manifest = verify_manifest(
        payload,
        armored,
        trust,
        gate_source=gate_source,
        current_version=current_version,
        now=int(time.time()) if now is None else now,
    )
    if hello is not None:
        verify_gate_hello(hello, manifest)
    return manifest
