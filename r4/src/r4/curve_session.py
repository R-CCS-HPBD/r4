"""Client-side CURVE session manager.

Handles the ephemeral session lifecycle:
1. Generate a one-time CURVE keypair.
2. Register the public key with the UMRS HTTP API.
3. Store the session locally (``~/.config/r4/curve-session.json``).
4. Provide the keys to ``UMRSTransport`` for ZMQ CURVE auth.
5. Forget on logout (local only; ``revoke_on_server`` on explicit request)
   and clean up expired sessions.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SESSION_FILE_NAME = "curve-session.json"


def session_file_name_for(base_url: str | None) -> str:
    """The session file of a transport bound to one non-active deployment.

    Onboarding plan S1 (2026-10-15): ``--source`` and ``model@source`` reach a
    deployment that is not the profile's own. Its CURVE session lives in a
    file of its own, so the active deployment's ``curve-session.json`` (also
    read by the umrs client) is never overwritten with another site's keys.
    """
    import hashlib

    digest = hashlib.sha256(str(base_url or "").rstrip("/").encode()).hexdigest()[:12]
    return f"curve-session-{digest}.json"


def forget_bound_session(base_url: str | None, config_dir: Path | None = None) -> bool:
    """Delete the session file a bound transport keeps for ``base_url`` (local only).

    Used when a source is removed, replaced, re-pointed or given a new key:
    its CURVE keys must not be reused under another credential or identity.
    No network call is made (the server-side session expires with its TTL).
    Returns True when a file was deleted.
    """
    manager = CurveSessionManager(
        umrs_base_url=base_url or None,
        config_dir=config_dir,
        session_file_name=session_file_name_for(base_url),
    )
    try:
        manager._session_file.unlink()
    except OSError:
        return False
    return True


def revoke_command(session: "CurveSession") -> str:
    """The command that revokes ``session`` on its server, with a placeholder token.

    ``r4 logout`` deletes the session file without contacting any server
    (stab cycle 3, 2026-10-22) and prints this for a session that records its
    deployment. The credential is never part of it: the user substitutes a
    token of that deployment deliberately. r4 has no command for this.
    """
    return f'curl -fsS -X DELETE -H "Authorization: Bearer <TOKEN>" {_revoke_url(session)}'


def _revoke_url(session: "CurveSession") -> str:
    """Where ``session`` is revoked: its deployment's session endpoint (the id
    quoted, so a hand-edited file cannot point the request at another path),
    shown without userinfo (:func:`display_url`)."""
    from urllib.parse import quote

    base = display_url(session.umrs_base_url).rstrip("/")
    return f"{base}/umrs/zmq/session/{quote(session.session_id, safe='')}"


def display_url(url: str) -> str:
    """``url`` safe to print: any userinfo (``user:password@``) masked as ``***@``.

    A gateway URL given as ``http://u:PW@host`` is recorded in the session
    file as is; ``r4 logout`` printed that password in the revoke command
    (stab cycle 3, round 6). An unparseable URL is not shown at all.
    """
    from urllib.parse import urlsplit

    from .source_locality import display_url as masked

    try:
        urlsplit(url).port  # noqa: B018 - raises ValueError on a malformed URL
    except ValueError:
        return "<unparseable URL>"
    return masked(url)


@dataclass
class CurveSession:
    """Persisted CURVE session state."""

    session_id: str
    server_public_key: str
    client_public_key: str
    client_secret_key: str
    expires_at: float
    umrs_base_url: str
    #: Who registered it (:func:`session_subject`): a session is never reused
    #: under another identity. Empty in files written before 2026-10-21.
    subject: str = ""

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def ttl_remaining(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "server_public_key": self.server_public_key,
            "client_public_key": self.client_public_key,
            "client_secret_key": self.client_secret_key,
            "expires_at": self.expires_at,
            "umrs_base_url": self.umrs_base_url,
            "subject": self.subject,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurveSession:
        """Parse a stored session; ValueError/TypeError/KeyError when malformed."""
        keys = {name: str(data[name]) for name in _KEY_FIELDS}
        if any(len(key) != _Z85_KEY_LEN for key in keys.values()):
            raise ValueError("CURVE key is not 40 Z85 characters")
        return cls(
            session_id=str(data["session_id"]),
            expires_at=float(data["expires_at"]),
            umrs_base_url=str(data.get("umrs_base_url") or ""),
            subject=str(data.get("subject") or ""),
            **keys,
        )


_KEY_FIELDS = ("server_public_key", "client_public_key", "client_secret_key")
_Z85_KEY_LEN = 40


def session_subject(credential: str) -> str:
    """A cache key for who a credential speaks for (never sent anywhere).

    A JWT's unverified ``iss`` + ``sub`` (a refreshed token keeps it); any
    other credential's own digest.
    """
    import hashlib

    from .auth import decode_jwt_claims

    claims = decode_jwt_claims(credential) or {}
    sub = claims.get("sub") if isinstance(claims, dict) else None
    basis = f"jwt|{claims.get('iss', '')}|{sub}" if sub else f"key|{credential}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def _at(url: str, deployments: tuple[str, ...]) -> bool:
    """Whether ``url`` is one of ``deployments`` (:func:`same_deployment`)."""
    from .source_context import same_deployment

    return bool(url) and any(same_deployment(url, d) for d in deployments)


def _is_subject(subject: str, credentials: tuple[str, ...]) -> bool:
    """Whether one of ``credentials`` is ``subject`` (:func:`session_subject`)."""
    return any(c and session_subject(c) == subject for c in credentials)


def owned_by_login(
    session: CurveSession, credentials: tuple[str, ...], deployments: tuple[str, ...]
) -> bool:
    """Whether ``session`` is the login's whose ``credentials`` and ``deployments``
    are given — for a LOCAL delete only; nothing is sent on this evidence.

    A session naming its subject: one of ``credentials`` registered it, and it
    records no deployment or one of ``deployments`` (a UMRS JWT has no
    ``iss``, so one username on two deployments is one subject). A file
    written before 2026-10-21 names no subject: registered at one of
    ``deployments``.
    """
    url = session.umrs_base_url
    if session.subject:
        return _is_subject(session.subject, credentials) and (not url or _at(url, deployments))
    return _at(url, deployments)


def _plain_http_url(url: str) -> bool:
    """Whether ``url`` is a plain http(s) URL: no userinfo, backslash or
    whitespace, which parsers disagree on (a hand-edited session file)."""
    from urllib.parse import urlsplit

    if any(char in url for char in "@\\ \t\r\n"):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _written_origin(url: str) -> tuple[str, str, int | None, str] | None:
    """``url``'s scheme, host as written, port and path; None when unparseable.

    No two spellings of one address are one host here: ``localhost`` resolves
    to ``::1`` before ``127.0.0.1``, so a key proven for one is not sent to
    the other (:func:`revoke_refusal`).
    """
    from urllib.parse import urlsplit

    from .config import _normalize_gateway_url

    try:
        parts = urlsplit(_normalize_gateway_url(url))
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
    except ValueError:
        return None
    host = (parts.hostname or "").rstrip(".")
    return (parts.scheme.lower(), host, port, parts.path.rstrip("/")) if host else None


def _proven_home(home: str, url: str) -> bool:
    """Whether ``home`` is ``url`` both as a deployment and as written."""
    origin = _written_origin(url)
    return _at(home, (url,)) and origin is not None and _written_origin(home) == origin


def revoke_refusal(session: CurveSession, credential: str, home: str) -> str | None:
    """Why ``credential`` may not be sent to revoke ``session``; None = proven.

    ``r4 logout --revoke-server-session`` sends only when the session file
    records its deployment and subject, ``credential`` is that subject, and
    ``home`` (where the credential provably belongs, ``auth.revocation_home``)
    is that deployment. Any doubt refuses.
    """
    url = session.umrs_base_url
    if not url:
        return "the session file records no deployment"
    if not _plain_http_url(url):
        return "the session file's deployment is not a plain http(s) URL"
    if not session.subject:
        return "the session file records no subject (written before 2026-10-21)"
    if not credential:
        return "this login stores no usable credential (an env key is never sent)"
    if session_subject(credential) != session.subject:
        return "the session was registered by another identity"
    if not _proven_home(home, url):
        return f"the stored credential is not proven to belong to {url}"
    return None


class CurveSessionManager:
    """Manage ephemeral CURVE sessions for ZMQ transport.

    The manager coordinates with the UMRS HTTP API to register
    one-time CURVE keypairs and persists the active session to disk.

    Usage::

        mgr = CurveSessionManager(umrs_base_url="http://umrs:8082")
        session = mgr.ensure_session(jwt_token="ey...")
        # session.client_public_key / client_secret_key / server_public_key
        # ready for UMRSTransportConfig
    """

    def __init__(
        self,
        umrs_base_url: str | None = None,
        config_dir: Path | None = None,
        ttl_seconds: int = 3600,
        session_file_name: str | None = None,
    ) -> None:
        self._umrs_base_url = umrs_base_url or os.environ.get(
            "UMRS_BASE_URL", "http://127.0.0.1:8082"
        )
        if config_dir:
            self._config_dir = config_dir
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            if xdg_config:
                self._config_dir = Path(xdg_config) / "r4"
            else:
                self._config_dir = Path.home() / ".config" / "r4"
        self._session_file = self._config_dir / (session_file_name or _SESSION_FILE_NAME)
        self._ttl_seconds = ttl_seconds
        self._cached_session: CurveSession | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_session(self, jwt_token: str) -> CurveSession:
        """Return a valid CURVE session, creating one if needed.

        A persisted session is reused only while it has >60 s remaining, was
        registered at this deployment, and by the identity ``jwt_token``
        speaks for. Before 2026-10-21 any live file was reused: after
        ``r4 login`` as bob, bob's commands rode alice's session and the
        server bound them to alice (an admin's grants included).
        Otherwise a new keypair is generated and registered.
        """
        session = self.load_session()
        if session is not None and self._reusable(session, jwt_token):
            logger.debug(
                "Reusing CURVE session %s (TTL %.0fs)",
                session.session_id,
                session.ttl_remaining,
            )
            return session

        return self._register_new_session(jwt_token)

    def _reusable(self, session: CurveSession, jwt_token: str) -> bool:
        return (
            session.ttl_remaining > 60
            and session.umrs_base_url.rstrip("/") == self._umrs_base_url.rstrip("/")
            and session.subject == session_subject(jwt_token)
        )

    def load_session(self) -> CurveSession | None:
        """Load session from disk, or return cached if valid."""
        if self._cached_session is not None and not self._cached_session.is_expired:
            return self._cached_session

        if not self._session_file.exists():
            return None
        try:
            data = json.loads(self._session_file.read_text(encoding="utf-8"))
            session = CurveSession.from_dict(data)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            # Malformed (`[]`, `null`, a string expiry, bad UTF-8, a short key):
            # drop it so the next connect registers afresh instead of failing
            # every time until someone deletes the file by hand.
            logger.warning("Discarding unreadable CURVE session file: %s", exc)
            self._delete_session_file()
            return None
        if session.is_expired:
            self._delete_session_file()
            return None
        self._cached_session = session
        return session

    def forget_session(
        self, owned: Callable[[CurveSession], bool] | None = None
    ) -> CurveSession | None:
        """Delete the local session file (when ``owned`` says it is this login's).

        No network call: the client secret key exists only in this file, so
        deleting it ends the session's use from here; the server-side session
        expires on its TTL (:meth:`revoke_on_server` ends it now, on request).
        Returns the live session deleted; None when there was none, it was
        expired or unreadable (deleted all the same), or it is another login's.
        """
        session = self.load_session()
        if session is None or (owned is not None and not owned(session)):
            return None
        self._delete_link_target()
        self.invalidate()
        return session

    def _delete_link_target(self) -> None:
        """A symlinked session file: delete the file it points to as well.

        It holds this session's client secret key; unlinking only the link
        left that copy behind (stab cycle 3, round 5).
        """
        if not self._session_file.is_symlink():
            return
        try:
            target = self._session_file.resolve(strict=True)
            if target.is_file():
                target.unlink()
        except OSError as exc:
            logger.debug("CURVE session link target not deleted: %s", exc)

    @classmethod
    def revoke_on_server(cls, session: CurveSession, credential: str) -> bool:
        """DELETE ``session`` at the deployment it records, with ``credential``.

        Only for ``r4 logout --revoke-server-session`` after
        :func:`revoke_refusal` proved the match. True when the server revoked it.
        """
        try:
            cls._http_delete(_revoke_url(session), jwt_token=credential)
        except Exception as exc:  # noqa: BLE001 - the local delete already happened
            logger.debug("Server revocation failed: %s", exc)
            return False
        logger.info("CURVE session %s revoked on server", session.session_id)
        return True

    def get_transport_keys(self) -> dict[str, str] | None:
        """Return keys suitable for ``UMRSTransportConfig`` fields.

        Returns ``None`` if no valid session exists.
        """
        session = self.load_session()
        if session is None:
            return None
        return {
            "curve_server_public_key": session.server_public_key,
            "curve_client_public_key": session.client_public_key,
            "curve_client_secret_key": session.client_secret_key,
        }

    def invalidate(self) -> None:
        """Discard the cached session and remove the local file.

        This forces the next ``ensure_session()`` call to register a brand-new
        session.  Use this when the server has lost the session (e.g. restart
        or the server-side sweep ran before the client's local expiry).
        """
        self._cached_session = None
        self._delete_session_file()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _register_new_session(self, jwt_token: str) -> CurveSession:
        """Generate keypair, register with server, persist locally."""
        import zmq  # type: ignore

        pub_z85, sec_z85 = zmq.curve_keypair()
        pub_z85_str = pub_z85.decode("ascii") if isinstance(pub_z85, bytes) else str(pub_z85)
        sec_z85_str = sec_z85.decode("ascii") if isinstance(sec_z85, bytes) else str(sec_z85)

        # Register with server
        resp = self._http_post(
            f"{self._umrs_base_url}/umrs/zmq/session",
            jwt_token=jwt_token,
            body={
                "client_public_key": pub_z85_str,
                "ttl_seconds": self._ttl_seconds,
            },
        )

        session = CurveSession(
            session_id=resp["session_id"],
            server_public_key=resp["server_public_key"],
            client_public_key=pub_z85_str,
            client_secret_key=sec_z85_str,
            expires_at=resp["expires_at"],
            umrs_base_url=self._umrs_base_url,
            subject=session_subject(jwt_token),
        )

        self._save_session(session)
        self._cached_session = session
        logger.info(
            "CURVE session registered: session=%s ttl=%ds",
            session.session_id,
            int(session.ttl_remaining),
        )
        return session

    def _save_session(self, session: CurveSession) -> None:
        """Persist session to disk with restrictive permissions."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(session.to_dict(), indent=2).encode("utf-8")

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._config_dir), prefix=".curve-session-", suffix=".tmp"
        )
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            os.write(fd, content)
            os.close(fd)
            fd = -1
            os.rename(tmp_path, str(self._session_file))
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _delete_session_file(self) -> None:
        """Remove the local session file."""
        try:
            self._session_file.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # HTTP helpers (stdlib-only, no requests dependency)
    # ------------------------------------------------------------------

    @staticmethod
    def _http_post(url: str, *, jwt_token: str, body: dict[str, Any]) -> dict[str, Any]:
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    @staticmethod
    def _http_delete(url: str, *, jwt_token: str) -> None:
        import urllib.request

        req = urllib.request.Request(
            url,
            method="DELETE",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
