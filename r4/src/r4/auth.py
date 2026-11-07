"""
R4 Authentication Module.

Handles token storage, refresh, and API key management.
A-101: Token management
A-102: API key support
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ._atomic import atomic_write_text

if TYPE_CHECKING:
    from .curve_session import CurveSession

logger = logging.getLogger(__name__)


@dataclass
class TokenInfo:
    """Stored token information."""

    access_token: str
    token_type: str = "Bearer"
    expires_at: Optional[float] = None  # Unix timestamp
    refresh_token: Optional[str] = None
    scope: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        """Check if token is expired (with 60s buffer)."""
        if self.expires_at is None:
            return False
        return time.time() > (self.expires_at - 60)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenInfo":
        """Create from dictionary."""
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=data.get("expires_at"),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
        )


class TokenStore:
    """
    Persistent token storage.

    Stores tokens in XDG-compliant config directory:
    - $XDG_CONFIG_HOME/r4/tokens.json
    - ~/.config/r4/tokens.json (fallback)
    """

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir:
            self._config_dir = config_dir
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            if xdg_config:
                self._config_dir = Path(xdg_config) / "r4"
            else:
                self._config_dir = Path.home() / ".config" / "r4"

        self._token_file = self._config_dir / "tokens.json"

    def save(self, token: TokenInfo, profile: str = "default") -> None:
        """Save token for a profile.

        Uses atomic write (write-to-temp + rename) so the token file is
        never world-readable, even momentarily.
        """
        self._config_dir.mkdir(parents=True, exist_ok=True)

        # Load existing tokens
        tokens = self._load_all()
        tokens[profile] = token.to_dict()

        # Atomic write: create temp file with 0o600, write, rename.
        content = json.dumps(tokens, indent=2).encode()
        fd, tmp_path = tempfile.mkstemp(dir=str(self._config_dir), prefix=".tokens-", suffix=".tmp")
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 before any content
            os.write(fd, content)
            os.close(fd)
            fd = -1  # mark as closed
            os.rename(tmp_path, str(self._token_file))
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.debug(f"Saved token for profile: {profile}")

    def load(self, profile: str = "default") -> Optional[TokenInfo]:
        """Load token for a profile."""
        tokens = self._load_all()
        if profile in tokens:
            return TokenInfo.from_dict(tokens[profile])
        return None

    def delete(self, profile: str = "default") -> bool:
        """Delete token for a profile (atomic rewrite)."""
        tokens = self._load_all()
        if profile in tokens:
            del tokens[profile]
            content = json.dumps(tokens, indent=2).encode()
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._config_dir), prefix=".tokens-", suffix=".tmp"
            )
            try:
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                os.write(fd, content)
                os.close(fd)
                fd = -1
                os.rename(tmp_path, str(self._token_file))
            except BaseException:
                if fd >= 0:
                    os.close(fd)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            return True
        return False

    def list_profiles(self) -> list[str]:
        """List all stored profiles."""
        return list(self._load_all().keys())

    def _load_all(self) -> dict:
        """Load all tokens from file."""
        if not self._token_file.exists():
            return {}
        try:
            return json.loads(self._token_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load tokens: {e}")
            return {}


class APIKeyManager:
    """
    API key management.

    API keys are stored in:
    - Environment: R4_API_KEY
    - Config file: $XDG_CONFIG_HOME/r4/config.json
    - Keyring (if available)
    """

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir:
            self._config_dir = config_dir
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            if xdg_config:
                self._config_dir = Path(xdg_config) / "r4"
            else:
                self._config_dir = Path.home() / ".config" / "r4"

        self._config_file = self._config_dir / "config.json"

    def get_api_key(self, profile: str = "default") -> Optional[str]:
        """
        Get API key from multiple sources (in priority order).

        1. Environment variable (R4_API_KEY)
        2. Profile-specific env var (R4_API_KEY_{PROFILE})
        3. Config file
        """
        # Check environment
        env_key = os.environ.get("R4_API_KEY")
        if env_key:
            return env_key

        profile_env = os.environ.get(f"R4_API_KEY_{profile.upper()}")
        if profile_env:
            return profile_env

        # Check config file
        config = self._load_config()
        profiles = config.get("profiles", {})
        if profile in profiles:
            return profiles[profile].get("api_key")

        return None

    def set_api_key(self, api_key: str, profile: str = "default") -> None:
        """Save API key to config file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        config = self._load_config()
        if "profiles" not in config:
            config["profiles"] = {}

        if profile not in config["profiles"]:
            config["profiles"][profile] = {}

        config["profiles"][profile]["api_key"] = api_key

        # Atomic and 0600 from the first byte (before 2026-10-21: write_text at
        # the umask mode, 0644, then chmod — and a crash mid-write truncated
        # every profile).
        atomic_write_text(self._config_file, json.dumps(config, indent=2), mode=0o600)

        logger.info(f"Saved API key for profile: {profile}")

    def delete_api_key(self, profile: str = "default") -> bool:
        """Delete API key from config file."""
        config = self._load_config()
        profiles = config.get("profiles", {})

        if profile in profiles and "api_key" in profiles[profile]:
            del profiles[profile]["api_key"]
            atomic_write_text(self._config_file, json.dumps(config, indent=2), mode=0o600)
            return True

        return False

    def _load_config(self) -> dict:
        """Load config file."""
        if not self._config_file.exists():
            return {}
        try:
            return json.loads(self._config_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config: {e}")
            return {}


def _non_empty(*values: str) -> tuple[str, ...]:
    return tuple(value for value in values if value)


def _env_gateways() -> tuple[str, ...]:
    """The deployments this environment names (``R4_GATEWAY_URL``, ``UMRS_BASE_URL``)."""
    return _non_empty(os.environ.get("R4_GATEWAY_URL", ""), os.environ.get("UMRS_BASE_URL", ""))


def _env_keys(profile: str) -> tuple[str, ...]:
    return _non_empty(
        os.environ.get("R4_API_KEY", ""), os.environ.get(f"R4_API_KEY_{profile.upper()}", "")
    )


def _login_deployments(
    credentials: tuple[str, ...], stored: dict[str, Any], row: Any, profile: str
) -> tuple[str, ...]:
    """The deployments a CURVE session file of this login may name (a local delete only).

    What the login records (``login_gateway_url``, its JWTs' ``gateway_url``
    claims, the profile's ``gateway_url``, its platform row's URL); the env
    gateways only for a login that records none or uses an env key. Nothing
    is ever sent to them: stab cycle 3 (2026-10-22), ``r4 logout`` sends no
    credential (``--revoke-server-session`` aside, :func:`revocation_home`).
    """
    from .config import _jwt_gateway_claim

    claims = (_jwt_gateway_claim(c) or "" for c in credentials)
    recorded = _non_empty(
        str(stored.get("login_gateway_url") or ""),
        *claims,
        str(stored.get("gateway_url") or ""),
        str(getattr(row, "url", "") or ""),
    )
    if recorded and not set(credentials) & set(_env_keys(profile)):
        return recorded
    return recorded + _env_gateways()


#: The profile field binding ``login_gateway_url`` to the one credential the
#: login that named it stored (:func:`credential_digest`; stab cycle 3 r5).
LOGIN_BINDING = "login_credential_sha256"


def credential_digest(credential: str) -> str:
    """A one-way digest of ``credential`` (stored, never sent): which credential
    a login's ``login_gateway_url`` was recorded for."""
    import hashlib

    return hashlib.sha256(credential.encode()).hexdigest()


def revocation_home(credential: str, stored: dict[str, Any]) -> str:
    """The deployment ``credential`` provably belongs to, else "" (unproven).

    Its own JWT ``gateway_url`` claim (UMRS mints it); for a credential that
    carries none, the ``login_gateway_url`` recorded by the login that NAMED
    that gateway and stored this very credential (:data:`LOGIN_BINDING`).
    The record alone proves nothing: logout keeps it, and ``r4 source add``,
    ``r4 init`` or a token refresh store another credential beside it — a
    stale record sent a deployment Y's key to X (stab cycle 3, round 5).
    Env URLs and other credentials' claims never count.
    """
    import hmac

    from .config import _jwt_gateway_claim

    if not credential:
        return ""
    claim = _jwt_gateway_claim(credential)
    if claim:
        return claim
    bound = str(stored.get(LOGIN_BINDING) or "")
    if bound and hmac.compare_digest(bound, credential_digest(credential)):
        return str(stored.get("login_gateway_url") or "")
    return ""


class AuthManager:
    """
    Unified authentication manager.

    Handles both token-based and API key authentication.
    """

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        profile: str = "default",
    ):
        self.profile = profile
        self._token_store = TokenStore(config_dir)
        self._api_key_manager = APIKeyManager(config_dir)
        self._cached_token: Optional[TokenInfo] = None
        #: The CURVE sessions the last :meth:`logout` deleted (local only;
        #: ``r4 logout`` reports them and how to revoke them on the server).
        self.forgotten_curve_sessions: list[CurveSession] = []

    def get_auth_header(self) -> Optional[dict[str, str]]:
        """
        Get authentication header.

        Tries in order:
        1. Valid cached token
        2. Stored token (if not expired)
        3. API key
        """
        # Try cached token
        if self._cached_token and not self._cached_token.is_expired:
            token_type = self._cached_token.token_type
            access_token = self._cached_token.access_token
            return {"Authorization": f"{token_type} {access_token}"}

        # Try stored token
        token = self._token_store.load(self.profile)
        if token and not token.is_expired:
            self._cached_token = token
            return {"Authorization": f"{token.token_type} {token.access_token}"}

        # Try API key
        api_key = self._api_key_manager.get_api_key(self.profile)
        if api_key:
            return {"Authorization": f"Bearer {api_key}"}

        return None

    def save_token(self, token: TokenInfo) -> None:
        """Save token and update cache."""
        self._token_store.save(token, self.profile)
        self._cached_token = token

    def save_api_key(self, api_key: str) -> None:
        """Save API key."""
        self._api_key_manager.set_api_key(api_key, self.profile)

    def logout(self) -> bool:
        """Clear all stored credentials for profile.

        Returns True if any credentials were actually cleared.
        """
        # Delete this login's CURVE session file first (it needs the stored
        # credentials to tell which file is this login's). Nothing is sent.
        self._forget_curve_session()
        token_cleared = self._token_store.delete(self.profile)
        key_cleared = self._api_key_manager.delete_api_key(self.profile)
        self._cached_token = None
        self._unbind_login()
        return token_cleared or key_cleared

    def _unbind_login(self) -> None:
        """Drop :data:`LOGIN_BINDING`: the credential it names is gone. The
        ``login_gateway_url`` record stays (config resolution reads it)."""
        from .config import ProfileConfigStore

        store = ProfileConfigStore(self._api_key_manager._config_dir)
        if LOGIN_BINDING in store.load(self.profile):
            store.save(self.profile, **{LOGIN_BINDING: None})

    def _forget_curve_session(self) -> None:
        """Delete this login's CURVE session file — local only, nothing is sent.

        The session's client secret key exists only in that file, so deleting
        it ends the session for this machine; the server side expires on its
        TTL. The default file holds the LAST login's session, maybe another
        deployment's and identity's: it is deleted only when it is this
        login's (:func:`r4.curve_session.owned_by_login`). Before 2026-10-22
        logout revoked it on the server, and three rounds of inferring where
        that may go sent this profile's credential to other deployments
        (stab cycle 3, python-runtime#1). ``r4 logout --revoke-server-session``
        revokes on request. The deleted session is kept in
        :attr:`forgotten_curve_sessions` so the caller can report it.
        """
        from .curve_session import CurveSessionManager, owned_by_login

        self.forgotten_curve_sessions = []
        try:
            credentials, deployments = self._login_ownership()
            session = CurveSessionManager().forget_session(
                lambda s: owned_by_login(s, credentials, deployments)
            )
        except Exception as exc:  # noqa: BLE001 - local cleanup never fails a logout
            logger.debug("CURVE session cleanup skipped: %s", exc)
            return
        if session is not None:
            self.forgotten_curve_sessions.append(session)

    def _login_ownership(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """This login's credentials and the deployments it names (local proof only).

        The credentials are the one in use (an env key included), every one
        the profile stores (an expired token included: the OAuth login
        registered its session with it) and the platform row's copy.
        """
        stored, row = self._login_row()
        header = (self.get_auth_header() or {}).get("Authorization", "").split()[-1:]
        row_key = (row.api_key if row is not None else None) or ""
        credentials = _non_empty(*header, *self.stored_credentials(), row_key)
        return credentials, _login_deployments(credentials, stored, row, self.profile)

    def revocation_credential(self) -> tuple[str, str]:
        """``(credential, home)`` for ``r4 logout --revoke-server-session``.

        The stored login's own credential (:meth:`stored_login_credential`,
        else the platform row's copy; never an env key) and the deployment it
        provably belongs to (:func:`revocation_home`, "" = unproven). Read
        before :meth:`logout` deletes them.
        """
        stored, row = self._login_row()
        row_key = (row.api_key if row is not None else None) or ""
        credential = self.stored_login_credential() or row_key
        return credential, revocation_home(credential, stored)

    def stored_login_credential(self) -> str:
        """The credential the profile's login stores: a valid token, else the
        auth store's API key ("" = none; an env key never counts)."""
        token = self._token_store.load(self.profile)
        valid = token.access_token if token is not None and not token.is_expired else ""
        return valid or self._stored_api_key()

    def _stored_api_key(self) -> str:
        """The API key the profile stores in the auth store (no env var)."""
        profiles = self._api_key_manager._load_config().get("profiles", {})
        entry = profiles.get(self.profile) if isinstance(profiles, dict) else None
        key = entry.get("api_key") if isinstance(entry, dict) else None
        return key if isinstance(key, str) else ""

    def stored_credentials(self) -> tuple[str, ...]:
        """Every credential the profile stores, an expired token included."""
        token = self._token_store.load(self.profile)
        return _non_empty(token.access_token if token else "", self._stored_api_key())

    def _login_row(self) -> tuple[dict[str, Any], Any]:
        """The profile's stored config and its platform source row (best effort, local)."""
        try:
            from .config import ProfileConfigStore
            from .source import get_source_store

            stored = ProfileConfigStore(self._api_key_manager._config_dir).load(self.profile)
            row = get_source_store().get(self.profile)
        except Exception as exc:  # noqa: BLE001 - local lookups never fail a logout
            logger.debug("Login gateway lookup failed: %s", exc)
            return {}, None
        return stored, (row if row is not None and row.kind == "platform" else None)

    def is_authenticated(self) -> bool:
        """Check if any valid credentials are available."""
        return self.get_auth_header() is not None

    @property
    def token_store(self) -> TokenStore:
        """Access token store directly."""
        return self._token_store

    @property
    def api_key_manager(self) -> APIKeyManager:
        """Access API key manager directly."""
        return self._api_key_manager


# Global auth manager singleton
_auth_manager: Optional[AuthManager] = None


def get_auth_manager(profile: str = "default") -> AuthManager:
    """Get the global auth manager."""
    global _auth_manager
    if _auth_manager is None or _auth_manager.profile != profile:
        _auth_manager = AuthManager(profile=profile)
    return _auth_manager


# =============================================================================
# JWT Claims Decode (UX Only)
# =============================================================================


def decode_jwt_claims(token: str) -> dict[str, Any] | None:
    """Decode JWT claims *without* signature verification.

    This is for **client-side UX display only** — showing the
    username, project, expiry countdown, etc. after login.

    **Never use the result for access-control or routing decisions.**
    The server re-validates the full JWT on every request.

    Returns:
        Claims dict, or ``None`` if the token is not a valid JWT.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        # Pad base64url to a multiple of 4.
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None
