"""The person's signing key and the checks around a gated HPC account.

ADR-112 D-3, D-4, D-8 and the B1.5a wire contract. The signing key is
dedicated: never the login key, never forwarded, never loaded through an
agent. It lives under ``~/.config/r4/hpc/signing/`` (0700): a FIDO2
``ed25519-sk`` key with ``verify-required`` (a PIN) where possible, else an
``ed25519`` key with a passphrase.

Signing is the person's act, never the assistant's. Every entry point that
creates or uses the key refuses in a Companion tool shell
(``R4_COMPANION_SESSION``) and without a terminal; the ``r4 hpc`` commands
that reach here are also fenced as never-auto in the permission ladder.

What the server sends (the transport public key, the site, the key id, the
domain) is validated strictly here, and the ``authorized_keys`` and
``allowed_signers`` lines are composed locally from those checked parts,
never copied from server text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from r4_contracts import hpc_gate

from .errors import R4Error

KEY_TYPES = ("sk", "ed25519")
_KEY_FILES = {"sk": "id_umrs_sk", "ed25519": "id_umrs_ed25519"}
_KEYGEN_TYPES = {"sk": "ed25519-sk", "ed25519": "ed25519"}
SIGNING_COMMENT = "umrs-signing"
#: Public key types a signing key may have (what ``ensure_signing_key`` makes).
SIGNER_KEY_TYPES = ("sk-ssh-ed25519@openssh.com", "ssh-ed25519")

OPENSSH_FLOOR = (8, 1)
OPENSSH_SK_FLOOR = (8, 2)
CONTROL_TTL_S = 300
CONTROL_TTL_MAX_S = hpc_gate.CONTROL_MAX_WINDOW

#: The gate's path in the person's HPC home (B1.5a install layout); one
#: definition, the gate's own.
GATE_DIR = hpc_gate.GATE_DIR
GATE_FILE = hpc_gate.GATE_SCRIPT
GATE_PYTHON = hpc_gate.GATE_PYTHON
#: The site profile in the gate directory (plan §5 B2.0 item 2).
SITE_PROFILE_FILE = "site.json"
#: The grant epoch, relative to the gate directory (plan §6, K7), and the
#: name of its member in the install tar.
GRANT_EPOCH_FILE = "state/grants/epoch"
GRANT_EPOCH_MEMBER = "grant_epoch"
#: The install time, written only where the gate has no ``state/grants`` yet
#: (a first install, or a gate whose state was lost): grants signed before a
#: state loss must not run again with fresh counters (review 2026-11-09).
GRANT_EPOCH_FRESH_MEMBER = "grant_epoch_fresh"
#: An active upgrade with existing grant state keeps its grants: bootstrap 0.
GRANT_EPOCH_LEGACY_MEMBER = "grant_epoch_legacy"

#: Tests only: allow a passphrase-less non-``sk`` signing key. A key with no
#: passphrase signs for anything that can read the file.
_ALLOW_UNENCRYPTED_KEYS = False

_OPENSSH_RE = re.compile(r"OpenSSH_(\d+)\.(\d+)")
_KEY_ID_RE = re.compile(r"tk-[0-9a-f]{12}")
#: The gate's site pattern (``hpc_gate._SITE_RE``): the site lands in its
#: forced command and in the transport comment.
_SITE_RE = re.compile(r"[a-z0-9-]{1,40}")
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DOMAIN_RE = re.compile(rf"(?=.{{1,253}}\Z){_LABEL}(?:\.{_LABEL})*")
_FROM_RE = re.compile(r"[A-Za-z0-9.:/*?!,_-]{1,255}")
#: The gate's principal pattern (``hpc_gate._PRINCIPAL_RE``).
_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@+-]{0,127}")
_LOGIN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_HOME_RE = re.compile(r"/[A-Za-z0-9._/+-]{0,255}")
_HOST_RE = re.compile(r"[A-Za-z0-9_\[][A-Za-z0-9_.@:%+\[\]-]{0,254}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GATE_VERSION_RE = re.compile(r"[0-9]{1,6}")
_TRANSPORT_RE = re.compile(r"ssh-ed25519 ([A-Za-z0-9+/]{68}) (\S{1,200})")
_OPENSSH_KEY_MAGIC = b"openssh-key-v1\0"


class HpcSigningError(R4Error):
    """A refusal or failure around the signing key or a server answer."""


def _gate(fn: Callable[..., Any], *args: Any) -> Any:
    """Call a shared ``hpc_gate`` helper; its ``ValueError`` is a refusal here."""
    try:
        return fn(*args)
    except ValueError as exc:
        raise HpcSigningError(f"refused: {exc}") from exc


# ── who may sign ─────────────────────────────────────────────────────────────


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def person_refusal(
    action: str, *, needs_tty: bool = True, command: str = "r4 hpc"
) -> Optional[str]:
    """Why ``<command> <action>`` may not run here, or None.

    The Companion's tool shells carry ``R4_COMPANION_SESSION``: there the
    command is the assistant's hands, and this is the person's act."""
    from .knowledge import COMPANION_SHELL_ENV, companion_shell

    if companion_shell():
        return (
            f"{command} {action} is the person's act: run it in your own terminal, "
            f"not through the assistant's shell ({COMPANION_SHELL_ENV} is set)."
        )
    if needs_tty and not _stdin_is_tty():
        return f"{command} {action} needs a terminal: run it interactively, not from a script."
    return None


def refuse_unless_person(action: str) -> None:
    reason = person_refusal(action)
    if reason:
        raise HpcSigningError(reason)


def recorded_fork(site: str) -> Optional[dict[str, Any]]:
    """The fork of the site's gate log the witness recorded, or None."""
    fork = read_site_record(site).get("witness_fork")
    return fork if isinstance(fork, dict) else None


def fork_refusal(site: str, action: str) -> Optional[str]:
    """While a fork is recorded, r4 signs no intent, grant or ``rotate`` for
    the site; ``grant-cancel`` and ``revoke`` still go through (they take
    authority away) and show this text beside them. None without a fork."""
    fork = recorded_fork(site)
    if fork is None:
        return None
    verb = "refused" if action not in ("grant-cancel", "revoke") else "allowed"
    return (
        f"✗ FORK recorded for {site} at {fork.get('seen_at', '?')}: the gate log no longer "
        f"extends the head r4 witnessed. {action} is {verb} while it stands. Investigate, then "
        f"run `r4 hpc status {site} --witness --clear-fork`."
    )


# ── local OpenSSH ────────────────────────────────────────────────────────────


def _run(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
    """Every subprocess here: an argv list, never a shell."""
    return subprocess.run(argv, check=False, **kwargs)


def ssh_keygen() -> str:
    """The ``ssh-keygen`` on PATH (``/usr/bin/ssh-keygen`` without one): the
    OpenSSH the person chose, e.g. Homebrew's with FIDO support on macOS."""
    found = shutil.which("ssh-keygen")
    if found:
        return found
    if os.access("/usr/bin/ssh-keygen", os.X_OK):
        return "/usr/bin/ssh-keygen"
    raise HpcSigningError("ssh-keygen not found: install OpenSSH 8.1 or newer.")


def ssh_client() -> str:
    """The ``ssh`` of the same OpenSSH as :func:`ssh_keygen`: the version
    checked is the version that creates the key and signs."""
    sibling = Path(ssh_keygen()).with_name("ssh")
    if os.access(sibling, os.X_OK):
        return str(sibling)
    raise HpcSigningError(f"no ssh beside {ssh_keygen()}: cannot tell its OpenSSH version.")


def openssh_version() -> Optional[tuple[int, int]]:
    """The local OpenSSH ``(major, minor)``, or None when unknown."""
    try:
        proc = _run([ssh_client(), "-V"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, HpcSigningError):
        return None
    found = _OPENSSH_RE.search(f"{proc.stderr or ''} {proc.stdout or ''}")
    return (int(found.group(1)), int(found.group(2))) if found else None


def check_openssh(kind: str) -> tuple[int, int]:
    """Refuse below 8.1 (``-Y sign``), and below 8.2 for ``sk`` keys."""
    floor = OPENSSH_SK_FLOOR if kind == "sk" else OPENSSH_FLOOR
    version = openssh_version()
    if version is None or version < floor:
        found = "not found" if version is None else "%d.%d" % version
        raise HpcSigningError(
            f"OpenSSH {floor[0]}.{floor[1]} or newer is needed for a {kind} "
            f"signing key (local OpenSSH: {found})."
        )
    return version


# ── the key ──────────────────────────────────────────────────────────────────


def signing_dir() -> Path:
    from .config import _resolve_config_dir

    return _resolve_config_dir() / "hpc" / "signing"


def _check_kind(kind: str) -> str:
    if kind not in KEY_TYPES:
        raise HpcSigningError(f"unknown key type {kind!r}: use one of {', '.join(KEY_TYPES)}")
    return kind


def key_path(kind: str, site: Optional[str] = None) -> Path:
    """The legacy key path, or a distinct key scoped to one registered site."""
    if site is not None:
        return signing_dir() / check_site(site) / _KEY_FILES[_check_kind(kind)]
    return signing_dir() / _KEY_FILES[_check_kind(kind)]


def _usable(kind: str, site: Optional[str] = None) -> bool:
    path = key_path(kind, site)
    return path.is_file() and Path(f"{path}.pub").is_file()


def local_signer(kind: Optional[str] = None) -> tuple[str, Path]:
    """The existing signing key of ``kind``, or the only one there is, as
    ``(kind, path)``. With both kinds present and none named, the choice is
    the person's (``--key-type``), never a guess."""
    kinds = [_check_kind(kind)] if kind else [k for k in KEY_TYPES if _usable(k)]
    if len(kinds) > 1:
        raise HpcSigningError(
            "both an sk and an ed25519 signing key exist: pass --key-type with the one "
            "this site's gate was installed with."
        )
    if kinds and _usable(kinds[0]):
        return kinds[0], key_path(kinds[0])
    raise HpcSigningError("no signing key yet: run `r4 hpc add <site>` first.")


# ── which key each site's gate trusts ────────────────────────────────────────

_SITE_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def site_record_path(site: str) -> Optional[Path]:
    """``~/.config/r4/hpc/sites/<site>.json`` (None for a name unfit for a file)."""
    if not _SITE_FILE_RE.fullmatch(site or ""):
        return None
    return signing_dir().parent / "sites" / f"{site}.json"


def read_site_record(site: str) -> dict[str, Any]:
    """What r4 recorded for ``site`` (``{}`` when nothing or unreadable).

    Keys (each optional): ``kind`` and ``fingerprint`` of the installed
    signing key, ``venue`` and ``domain`` from the transport offer, the
    ``login`` the site verified, ``ssh_destination`` of that physical gate,
    ``site_profile_sha256`` of the installed profile, and ``witness`` (the gate
    log head r4 last checked)."""
    path = site_record_path(site)
    try:
        record = json.loads(path.read_text()) if path else None
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def update_site_record(site: str, **fields: Any) -> None:
    """Merge ``fields`` into the site record (0600 in a 0700 directory)."""
    path = site_record_path(site)
    if path is None:
        return
    record = {**read_site_record(site), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def record_site_signer(site: str, kind: str, key: Path) -> None:
    """Remember which signing key ``r4 hpc add <site>`` installed there."""
    update_site_record(site, kind=_check_kind(kind), fingerprint=public_key_info(key)[2])


def _recorded_kind(site: str) -> Optional[str]:
    kind = read_site_record(site).get("kind")
    return kind if kind in KEY_TYPES else None


def site_signer(site: str, kind: Optional[str] = None) -> tuple[str, Path]:
    """The site's scoped key when installed, else its legacy global key."""
    selected = kind or _recorded_kind(site)
    if selected is not None and _usable(selected, site):
        return selected, key_path(selected, site)
    return local_signer(selected)


def _ssh_string(raw: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(raw):
        raise ValueError("truncated")
    (size,) = struct.unpack(">I", raw[offset : offset + 4])
    end = offset + 4 + size
    if end > len(raw):
        raise ValueError("truncated")
    return raw[offset + 4 : end], end


def key_is_encrypted(path: Path) -> bool:
    """True when an OpenSSH private key file is passphrase-protected."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError as exc:
        raise HpcSigningError(f"the signing key {path} is unreadable: {exc}") from exc
    body = "".join(line.strip() for line in lines if line and not line.startswith("-----"))
    try:
        raw = base64.b64decode(body)
        cipher, _ = _ssh_string(raw, len(_OPENSSH_KEY_MAGIC))
    except ValueError:
        return False
    return raw.startswith(_OPENSSH_KEY_MAGIC) and cipher != b"none"


def _require_protected(path: Path) -> None:
    """A non-``sk`` signing key must carry a passphrase."""
    key_type = public_key_info(path)[0]
    if key_type.startswith("sk-") or _ALLOW_UNENCRYPTED_KEYS or key_is_encrypted(path):
        return
    raise HpcSigningError(
        f"the signing key {path} has no passphrase: anything that reads the "
        "file could sign as you. Remove it and run `r4 hpc add` again."
    )


def _require_public_half(path: Path) -> None:
    if not Path(f"{path}.pub").is_file():
        raise HpcSigningError(
            f"the signing key {path} has no .pub: recreate it with "
            f"`ssh-keygen -y -f {path} > {path}.pub`, or remove the key."
        )


def _keygen_argv(kind: str, path: Path) -> list[str]:
    argv = [ssh_keygen(), "-t", _KEYGEN_TYPES[kind]]
    if kind == "sk":
        argv += ["-O", "verify-required"]
    return argv + ["-f", str(path), "-C", SIGNING_COMMENT]


def _creation_failed(kind: str, path: Path) -> HpcSigningError:
    if kind == "sk":
        return HpcSigningError(
            "could not create a FIDO2 signing key (no security key found, or "
            "it does not support a PIN). Plug one in and retry, or use "
            "`--key-type ed25519` for a passphrase-protected key."
        )
    return HpcSigningError(f"ssh-keygen could not create the signing key at {path}.")


def _discard(path: Path) -> None:
    for each in (path, Path(f"{path}.pub")):
        each.unlink(missing_ok=True)


def _ensure_key_at_path(kind: str, path: Path, confirm: Optional[Callable[[str], bool]]) -> Path:
    refuse_unless_person("add")
    if path.is_file():
        _require_public_half(path)
        _require_protected(path)
        return path
    check_openssh(kind)
    if confirm is not None and not confirm(f"Create a dedicated {kind} signing key at {path}?"):
        raise HpcSigningError("signing key not created: declined.")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if _run(_keygen_argv(kind, path)).returncode != 0 or not path.is_file():
        _discard(path)
        raise _creation_failed(kind, path)
    try:
        _require_protected(path)
    except HpcSigningError:
        _discard(path)
        raise
    os.chmod(path, 0o600)
    return path


def ensure_signing_key(kind: str = "sk", confirm: Optional[Callable[[str], bool]] = None) -> Path:
    """Create or reuse the legacy global key; ``add`` uses the site-aware form.

    ``ssh-keygen`` inherits the terminal: it asks for the PIN and touch
    (``sk``) or the passphrase (``ed25519``) itself."""
    return _ensure_key_at_path(kind, key_path(kind), confirm)


def signer_holder(fingerprint: str, site: str) -> Optional[str]:
    """Another site whose installed gate trusts ``fingerprint``, or None."""
    for name in site_names():
        if name != site and read_site_record(name).get("fingerprint") == fingerprint:
            return name
    return None


def _check_signer_free(site: str, key: Path) -> None:
    other = signer_holder(public_key_info(key)[2], site)
    if other is not None:
        raise HpcSigningError(
            f"the signing key for {site} is also recorded for {other}: a grant or intent "
            "could replay across those gates. Create a distinct key for this site."
        )


def ensure_site_signing_key(
    kind: str = "sk", *, site: str, confirm: Optional[Callable[[str], bool]] = None
) -> Path:
    """A key unique to ``site`` whenever another site holds the legacy key.

    A gate accepts a signed artifact only from its own ``allowed_signers``;
    distinct keys therefore prevent replay without changing that wire format.
    """
    selected = _check_kind(kind)
    legacy = key_path(selected)
    shared = signer_holder(public_key_info(legacy)[2], site) if _usable(selected) else None
    if shared is None:
        key = ensure_signing_key(selected, confirm)
    else:
        key = _ensure_key_at_path(selected, key_path(selected, site), confirm)
    _check_signer_free(site, key)
    return key


def unambiguous_site_signer(site: str, kind: Optional[str] = None) -> tuple[str, Path]:
    """The site's key for a new grant/intent, refusing an old shared key.

    Cancellation and revocation deliberately call :func:`site_signer` instead:
    taking authority away must stay possible during migration to unique keys.
    """
    selected, key = site_signer(site, kind)
    other = signer_holder(public_key_info(key)[2], site)
    if other is not None:
        raise HpcSigningError(
            f"the signing key for {site} is also recorded for {other}: run `r4 hpc add {site}` "
            "again to install a distinct key before signing a new grant or intent. "
            "grant-cancel and revoke remain available."
        )
    return selected, key


def public_key_info(key: Path) -> tuple[str, str, str]:
    """``(type, base64 blob, fingerprint)`` of a signing key's ``.pub``."""
    try:
        parts = Path(f"{key}.pub").read_text().split()
    except OSError as exc:
        raise HpcSigningError(f"{key}.pub is unreadable: {exc}") from exc
    if len(parts) < 2 or parts[0] not in SIGNER_KEY_TYPES:
        raise HpcSigningError(f"{key}.pub is not an ed25519 or ed25519-sk public key")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        embedded, _ = _ssh_string(blob, 0)
    except ValueError as exc:
        raise HpcSigningError(f"{key}.pub does not decode: {exc}") from exc
    if embedded.decode("ascii", "replace") != parts[0]:
        raise HpcSigningError(f"{key}.pub names one key type and holds another")
    return parts[0], parts[1], hpc_gate.fingerprint(blob)


# ── signing ──────────────────────────────────────────────────────────────────


_NAMESPACE_RE = re.compile(rf"umrs-(intent|grant|control)-v1@{_DOMAIN_RE.pattern}")


def _signing_env() -> dict[str, str]:
    """No agent: ``ssh-keygen`` signs with the key file, asking on the TTY."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SSH_ASKPASS")}
    env["SSH_AUTH_SOCK"] = ""
    return env


def sign(payload: bytes, namespace: str, key: Path) -> str:
    """The armored SSHSIG of ``payload`` (``ssh-keygen -Y sign -f <key>``)."""
    refuse_unless_person("sign")
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise HpcSigningError(f"not a UMRS signing namespace: {namespace!r}")
    _require_protected(Path(key))
    with tempfile.TemporaryDirectory(prefix="r4-hpc-sign-") as tmp:
        message = Path(tmp) / "payload"
        message.write_bytes(payload)
        argv = [ssh_keygen(), "-Y", "sign", "-f", str(key), "-n", namespace, str(message)]
        proc = _run(argv, env=_signing_env())
        signature = Path(f"{message}.sig")
        if proc.returncode != 0 or not signature.is_file():
            raise HpcSigningError(f"signing failed (ssh-keygen exit {proc.returncode}).")
        return signature.read_text()


def canonical_bytes(obj: Any) -> bytes:
    """The one canonical encoding (UMRS, r4 and the gate share it)."""
    return bytes(_gate(hpc_gate.canonical_bytes, obj))


def control_body(
    op: str,
    *,
    site: str,
    principal: str,
    key_id: str,
    new_public_key: Optional[str] = None,
    now: Optional[float] = None,
    ttl: int = CONTROL_TTL_S,
) -> dict[str, Any]:
    """A ``umrs-control-v1`` body (B1.5a): ``revoke`` or ``rotate``."""
    if op not in ("revoke", "rotate") or (op == "rotate") != (new_public_key is not None):
        raise HpcSigningError(f"bad control: op={op!r} with new_public_key={new_public_key!r}")
    if not 0 < int(ttl) <= CONTROL_TTL_MAX_S:
        raise HpcSigningError(f"a control lives at most {CONTROL_TTL_MAX_S} s")
    issued = int(time.time() if now is None else now)
    body: dict[str, Any] = {
        "v": 1,
        "type": "control",
        "op": op,
        "site": check_site(site),
        "principal": check_principal(principal),
        "key_id": check_key_id(key_id),
        "nonce": secrets.token_hex(16),
        "issued": issued,
        "expires": issued + int(ttl),
    }
    if new_public_key is not None:
        body["new_public_key"] = new_public_key
    return body


def signed_control(body: dict[str, Any], key: Path, domain: str) -> dict[str, str]:
    """The signed envelope ``{payload, signature}`` the gate reads on stdin."""
    payload = canonical_bytes(body)
    signature = sign(payload, _gate(hpc_gate.namespace, "control", check_domain(domain)), key)
    return {"payload": base64.b64encode(payload).decode("ascii"), "signature": signature}


# ── strict checks on what the server (or the person) names ──────────────────


def _checked(what: str, value: Any, pattern: "re.Pattern[str]") -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise HpcSigningError(f"refused: {what} {str(value)[:80]!r} is not well-formed.")
    return value


def check_site(value: Any) -> str:
    return _checked("site", value, _SITE_RE)


def check_key_id(value: Any) -> str:
    return _checked("key id", value, _KEY_ID_RE)


def check_domain(value: Any) -> str:
    return _checked("signing domain", value, _DOMAIN_RE)


def check_principal(value: Any) -> str:
    return _checked("principal", value, _PRINCIPAL_RE)


def check_login(value: Any) -> str:
    return _checked("login", value, _LOGIN_RE)


def check_home(value: Any) -> str:
    return _checked("home directory", value, _HOME_RE)


def check_host(value: Any) -> str:
    return _checked("ssh destination", value, _HOST_RE)


def check_from(value: Any) -> str:
    """The ``from=`` pattern list; empty means no ``from=`` option."""
    if value in ("", None):
        return ""
    _checked("from address", value, _FROM_RE)
    return str(_gate(hpc_gate.check_from_address, value))


def transport_comment(site: str, key_id: str) -> str:
    return str(_gate(hpc_gate.transport_comment, check_site(site), check_key_id(key_id)))


def validate_transport_key(public_key: Any, site: str, key_id: str) -> str:
    """The base64 blob of a server-sent transport key, checked strictly.

    Exactly ``ssh-ed25519 <b64> umrs-transport-<site>-<key_id>``: one line,
    no options, a 32-byte ed25519 key, the expected comment."""
    comment = transport_comment(site, key_id)
    found = _TRANSPORT_RE.fullmatch(public_key) if isinstance(public_key, str) else None
    if found is None:
        raise HpcSigningError(
            "refused: the transport key is not exactly 'ssh-ed25519 <key> <comment>'."
        )
    try:
        hpc_gate.ed25519_blob(found.group(1))
    except ValueError as exc:
        raise HpcSigningError("refused: the transport key is not a 32-byte ed25519 key.") from exc
    if found.group(2) != comment:
        raise HpcSigningError(f"refused: the transport key's comment is not {comment!r}.")
    return found.group(1)


@dataclass(frozen=True)
class TransportOffer:
    """A checked ``POST transport-key`` answer."""

    site: str
    key_id: str
    domain: str
    from_address: str
    key_b64: str
    gate_version: str
    gate_sha256: str

    @property
    def comment(self) -> str:
        return transport_comment(self.site, self.key_id)


def parse_offer(answer: Any) -> TransportOffer:
    """Check every field of the transport-key answer before anything uses it."""
    data: dict[str, Any] = answer if isinstance(answer, dict) else {}
    raw_gate = data.get("gate")
    gate: dict[str, Any] = raw_gate if isinstance(raw_gate, dict) else {}
    site = check_site(data.get("site"))
    key_id = check_key_id(data.get("key_id"))
    return TransportOffer(
        site=site,
        key_id=key_id,
        domain=check_domain(data.get("domain")),
        from_address=check_from(data.get("from")),
        key_b64=validate_transport_key(data.get("public_key"), site, key_id),
        gate_version=_checked("gate version", gate.get("version"), _GATE_VERSION_RE),
        gate_sha256=_checked("gate sha256", gate.get("sha256"), _SHA256_RE),
    )


# ── the gate and the lines r4 installs ──────────────────────────────────────


def gate_source() -> bytes:
    """The gate shipped with this r4 (``r4_contracts/hpc_gate.py``)."""
    from importlib import resources

    return resources.files("r4_contracts").joinpath("hpc_gate.py").read_bytes()


def gate_sha256() -> str:
    return hashlib.sha256(gate_source()).hexdigest()


def authorized_keys_line(offer: TransportOffer, home: str) -> str:
    """The forced-command line pinning the transport key to the gate
    (composed by the gate's own helper from checked parts)."""
    return str(
        _gate(
            hpc_gate.authorized_keys_line,
            check_home(home),
            offer.site,
            offer.key_id,
            offer.key_b64,
            offer.from_address,
        )
    )


def allowed_signers_line(principal: str, domain: str, key_type: str, key_b64: str) -> str:
    """One ``allowed_signers`` line: the principal, its three namespaces, the key."""
    if key_type not in SIGNER_KEY_TYPES:
        raise HpcSigningError(f"not a signing key type: {key_type!r}")
    args = (check_principal(principal), check_domain(domain), key_type, key_b64)
    return str(_gate(hpc_gate.allowed_signers_line, *args))


def epoch_bytes(epoch: int) -> bytes:
    """Canonical epoch bytes; 0 bootstraps an active legacy upgrade."""
    if type(epoch) is not int or epoch < 0:
        raise HpcSigningError(f"bad grant epoch {epoch!r}")
    return canonical_bytes({"epoch": epoch})


def needs_epoch(account: Any, record: Optional[dict[str, Any]] = None) -> bool:
    """True unless the site reports an ``active`` or ``suspended`` account
    and r4's own record holds no revoke: grants signed before this install
    must end at the gate (K7). A failed re-enrolment leaves the account
    ``pending``, and the site's answer is the operator's, so r4's own
    ``epoch_due`` (set by ``r4 hpc revoke``) wins (review 2026-11-09). An
    upgrade of an active account ends no grant."""
    if (record or {}).get("epoch_due") is True:
        return True
    return not isinstance(account, dict) or account.get("status") not in ("active", "suspended")


def site_names() -> list[str]:
    """The sites r4 holds a record for (``~/.config/r4/hpc/sites/*.json``)."""
    directory = signing_dir().parent / "sites"
    try:
        names = [p.stem for p in directory.iterdir() if p.suffix == ".json"]
    except OSError:
        return []
    return sorted(n for n in names if _SITE_FILE_RE.fullmatch(n))


def check_gate_destination(site: str, destination: str) -> str:
    """Refuse to move one recorded signer to another physical gate.

    Records made before destination binding cannot prove that a re-add reaches
    the old gate. They keep their old signer for cancel/revoke, but a new gate
    must be registered under a separate site name and receive a distinct key.
    """
    checked = check_host(destination)
    record = read_site_record(site)
    previous = record.get("ssh_destination")
    if not record.get("fingerprint") or previous == checked:
        return checked
    if not previous:
        detail = "has no recorded SSH destination"
    else:
        detail = f"is bound to a different SSH destination ({previous!r})"
    raise HpcSigningError(
        f"the signing record for {site} {detail}; this re-enrolment is at {checked!r}. "
        "Refusing to reuse one signer across physical gates. Register the other gate under a "
        "new site name; grant-cancel and revoke remain available under the existing record."
    )


def venue_holder(venue: str, site: str) -> Optional[str]:
    """Another site whose record already holds ``venue``, or None. A grant
    or intent binds the venue name, so two gates with one venue would both
    honour it, each with its own counters (review 2026-11-09)."""
    for name in site_names():
        if name != site and read_site_record(name).get("venue") == venue:
            return name
    return None


def check_venue_free(venue: str, site: str) -> None:
    other = venue_holder(venue, site)
    if other is not None:
        raise HpcSigningError(
            f"the site offers venue {venue!r}, which r4 already records for site {other!r}: "
            "a grant or intent signed for one would also pass at the other's gate. Each site "
            "must be its own venue; check the gateway, or `r4 hpc revoke` the other site first."
        )


@dataclass(frozen=True)
class GateInstall:
    """What ``r4 hpc add`` writes into the person's HPC home."""

    files: dict[str, bytes]
    line: str
    gate_sha256: str


def _check_gate_skew(offer: TransportOffer, local_sha: str) -> None:
    if offer.gate_sha256 != local_sha or offer.gate_version != hpc_gate.GATE_VERSION:
        raise HpcSigningError(
            f"version skew: the site expects gate {offer.gate_version} "
            f"(sha256 {offer.gate_sha256[:16]}…) but this r4 ships gate "
            f"{hpc_gate.GATE_VERSION} (sha256 {local_sha[:16]}…). Upgrade r4 (or ask the "
            "site operator which r4 release it serves) and retry."
        )


def build_install(
    offer: TransportOffer,
    *,
    principal: str,
    home: str,
    signer: Path,
    kind: str,
    site_profile: Optional[bytes] = None,
    epoch: Optional[int] = None,
    fresh_epoch: Optional[int] = None,
) -> GateInstall:
    """The gate files and the ``authorized_keys`` line, built locally.

    ``site_profile`` is the checked profile's exact bytes (gate v2,
    ``site.json``); the gate reads it, so it is written byte for byte.
    ``epoch`` (an install time, in seconds) is written to
    ``state/grants/epoch`` when the account is new or was revoked: the gate
    then refuses every grant issued before it (plan §6, K7). ``fresh_epoch``
    is written only where the gate has no ``state/grants`` yet; an active
    upgrade with existing state but no epoch gets an explicit zero instead."""
    source = gate_source()
    local_sha = hashlib.sha256(source).hexdigest()
    _check_gate_skew(offer, local_sha)
    key_type, key_b64, _ = public_key_info(signer)
    config = {
        "site": offer.site,
        "domain": offer.domain,
        "principal": check_principal(principal),
        "uv_required": kind == "sk",
        "allow_non_sk": kind == "ed25519",
        "gate_version": hpc_gate.GATE_VERSION,
    }
    signers = allowed_signers_line(principal, offer.domain, key_type, key_b64) + "\n"
    files = {
        GATE_FILE: source,
        "gate.json": canonical_bytes(config) + b"\n",
        "allowed_signers": signers.encode("ascii"),
    }
    if site_profile is not None:
        files[SITE_PROFILE_FILE] = bytes(site_profile)
    if epoch is not None:
        files[GRANT_EPOCH_MEMBER] = epoch_bytes(epoch)
    if fresh_epoch is not None:
        files[GRANT_EPOCH_FRESH_MEMBER] = epoch_bytes(fresh_epoch)
        files[GRANT_EPOCH_LEGACY_MEMBER] = epoch_bytes(0)
    return GateInstall(files=files, line=authorized_keys_line(offer, home), gate_sha256=local_sha)
