"""
R4 UMRS Transport Layer.

Wraps UMRS ZeroMQ MessageQueue for efficient file transfer and
direct UMRS command operations.

Uses the same protocol as the UMRS CLI client:
- Control channel (DEALER → ROUTER): Commands as "{CMD}:{client_id}:{payload}"
- Data channel (PUSH → PULL): Binary file chunks

This transport is optimized for:
- Large file uploads (CT images, scientific data)
- HPC environments with SSH tunnel support
- Low-latency binary transfers
- Direct UMRS command execution (HPC-local deployments)
"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from r4_contracts.protocol import parse_handshake_reply

from .errors import R4Error

logger = logging.getLogger(__name__)

# Check for ZeroMQ availability
try:
    import zmq

    ZEROMQ_AVAILABLE = True
except ImportError:
    ZEROMQ_AVAILABLE = False
    logger.debug("ZeroMQ not available, UMRS transport will use HTTP fallback")


def _jwt_or_none(token: Optional[str]) -> Optional[str]:
    """``token`` when it looks like a JWT (three dot-separated segments)."""
    if token and token.count(".") == 2:
        return token
    return None


def _is_loopback_host(host: Optional[str]) -> bool:
    """``host`` names this machine (``localhost`` or a loopback address)."""
    name = (host or "").strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _env_value(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw:
        return raw.strip()
    file_path = os.environ.get(f"{name}_FILE")
    if not file_path:
        return None
    try:
        return Path(file_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise R4Error(f"Could not read {name}_FILE={file_path}: {exc}") from exc


@dataclass
class UMRSTransportConfig:
    """
    Configuration for UMRS ZeroMQ transport.

    Environment variables:
    - UMRS_SERVER_HOST: Server IP address (default: 127.0.0.1)
    - UMRS_CONTROL_PORT: Control channel port (default: 5555)
    - UMRS_TUNNEL_MODE: Use SSH tunnel mode (default: false)
    - UMRS_TIMEOUT_MS: Socket timeout in milliseconds (default: 8000)
    - UMRS_ZMQ_AUTH_TYPE: ZMQ auth mode (none|curve|session); unset = session with a
      valid JWT, else plaintext only to a loopback server (a remote one is refused)
    - UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY: server public CURVE key
    - UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY: client public CURVE key
    - UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY: client secret CURVE key
    """

    server_host: str = "127.0.0.1"
    control_port: int = 5555
    tunnel_mode: bool = False
    timeout_ms: int = 8000
    zmq_auth_type: str = "none"
    curve_server_public_key: Optional[str] = None
    curve_client_public_key: Optional[str] = None
    curve_client_secret_key: Optional[str] = None
    # Per-source isolation (onboarding plan 2026-10-15, S1). True: this
    # transport serves a config bound to one source (r4.source_context), so
    # the CURVE session registers at session_gateway_url with
    # session_credential and never with the active source's login. False: the
    # active source's login, as before. None (not given): follow the
    # process-wide config — bound when ``--source`` bound it.
    session_bound: Optional[bool] = None
    session_gateway_url: Optional[str] = None
    session_credential: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "UMRSTransportConfig":
        """Create config from environment variables."""
        tunnel_mode_str = os.environ.get("UMRS_TUNNEL_MODE", "").lower()
        return cls(
            server_host=os.environ.get("UMRS_SERVER_HOST", "127.0.0.1"),
            control_port=int(os.environ.get("UMRS_CONTROL_PORT", "5555")),
            tunnel_mode=tunnel_mode_str in ("1", "true", "yes"),
            timeout_ms=int(os.environ.get("UMRS_TIMEOUT_MS", "8000")),
            zmq_auth_type=os.environ.get("UMRS_ZMQ_AUTH_TYPE", "none").strip().lower(),
            curve_server_public_key=_env_value("UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY"),
            curve_client_public_key=_env_value("UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY"),
            curve_client_secret_key=_env_value("UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY"),
        )

    @classmethod
    def for_server(
        cls,
        *,
        server_host: str,
        control_port: int,
        tunnel_mode: bool,
        session_bound: Optional[bool] = None,
        session_gateway_url: Optional[str] = None,
        session_credential: Optional[str] = None,
    ) -> "UMRSTransportConfig":
        """The environment's auth settings, pointed at the profile's server.

        Every client path used to build ``cls(server_host=…, control_port=…,
        tunnel_mode=…)``, which dropped UMRS_ZMQ_AUTH_TYPE and the CURVE keys —
        and with the variable exported, session auto-detection is skipped too,
        so asking for ``curve`` gave a plaintext socket (defect-wave 2026-10-08 #1).
        The ``session_*`` fields come from
        :func:`r4.source_context.transport_session_fields`.
        """
        return replace(
            cls.from_env(),
            server_host=server_host,
            control_port=control_port,
            tunnel_mode=tunnel_mode,
            session_bound=session_bound,
            session_gateway_url=session_gateway_url,
            session_credential=session_credential,
        )


@dataclass
class UploadStatus:
    """Status of an upload operation."""

    success: bool
    s3_key: Optional[str] = None
    error: Optional[str] = None
    chunks_sent: int = 0


class UMRSTransport:
    """
    UMRS ZeroMQ-based transport for file operations.

    Provides efficient binary transfer using the same protocol as
    the UMRS CLI client. Falls back to HTTP when ZeroMQ is unavailable.

    Usage:
        transport = UMRSTransport()
        if transport.connect():
            result = transport.upload_file("/path/to/file.tif", "mybucket")
            transport.disconnect()
    """

    def __init__(
        self,
        config: Optional[UMRSTransportConfig] = None,
    ):
        """
        Initialize UMRS transport.

        Args:
            config: Transport configuration. Uses env vars if not provided.
        """
        self._config = config or UMRSTransportConfig.from_env()
        self._context: Optional[Any] = None  # zmq.Context
        self._control_socket: Optional[Any] = None  # zmq.Socket
        self._connected = False
        self._session_mgr: Optional[Any] = None  # CurveSessionManager (lazy)
        # Last failed connect's exception (for typed-error mapping, round-5 T2).
        self.last_connect_error: Exception | None = None

    @property
    def is_available(self) -> bool:
        """Check if ZeroMQ transport is available."""
        return ZEROMQ_AVAILABLE

    @property
    def is_connected(self) -> bool:
        """Check if connected to UMRS server."""
        return self._connected

    def connect(self, timeout_ms: Optional[int] = None) -> bool:  # noqa: C901 — reason=pre-existing complexity debt owner=agent-debug added=2026-09-05
        """
        Connect to UMRS server.

        Args:
            timeout_ms: Connection timeout in milliseconds.

        Returns:
            True if connection successful.
        """
        if not ZEROMQ_AVAILABLE:
            logger.warning("ZeroMQ not available, cannot connect via UMRS transport")
            return False

        if self._connected:
            return True

        try:
            timeout = timeout_ms or self._config.timeout_ms

            # Create ZeroMQ context
            self._context = zmq.Context()

            # Determine connection address
            if self._config.tunnel_mode:
                server_addr = "127.0.0.1"
            else:
                server_addr = self._config.server_host

            # Create control socket (DEALER for client)
            self._control_socket = self._context.socket(zmq.DEALER)
            self._control_socket.setsockopt(zmq.RCVTIMEO, timeout)
            self._control_socket.setsockopt(zmq.SNDTIMEO, timeout)
            self._control_socket.setsockopt(zmq.LINGER, 0)  # Don't block on close
            self._apply_zmq_security(self._control_socket)
            self._control_socket.connect(f"tcp://{server_addr}:{self._config.control_port}")

            # Test connection with ping
            if self._ping():
                self._connected = True
                logger.info(
                    f"Connected to UMRS server at {server_addr}:{self._config.control_port}"
                )
                return True

            # Session mode: if ping failed it may be a stale session that the
            # server no longer recognises (restart, sweep, clock skew).
            # Invalidate the cached session, re-register, and retry once.
            # Note: _session_mgr is set by _apply_session_curve() which may
            # be triggered by explicit config OR auto-detection from OIDC.
            if self._session_mgr is not None:
                logger.info(
                    "Session-mode CURVE handshake failed — invalidating "
                    "cached session and re-registering"
                )
                self._session_mgr.invalidate()
                self._cleanup()
                try:
                    self._context = zmq.Context()
                    self._control_socket = self._context.socket(zmq.DEALER)
                    self._control_socket.setsockopt(zmq.RCVTIMEO, timeout)
                    self._control_socket.setsockopt(zmq.SNDTIMEO, timeout)
                    self._control_socket.setsockopt(zmq.LINGER, 0)
                    self._apply_zmq_security(self._control_socket)
                    self._control_socket.connect(f"tcp://{server_addr}:{self._config.control_port}")
                    if self._ping():
                        self._connected = True
                        logger.info(
                            "Reconnected with fresh CURVE session at %s:%s",
                            server_addr,
                            self._config.control_port,
                        )
                        return True
                except Exception as retry_exc:
                    logger.warning("Session retry failed: %s", retry_exc)

            self._cleanup()
            logger.warning("UMRS server not responding")
            return False

        except Exception as e:
            self.last_connect_error = e
            self._cleanup()
            logger.error(f"Failed to connect to UMRS server: {e}")
            return False

    def disconnect(self):
        """Disconnect from UMRS server."""
        self._cleanup()
        self._connected = False
        logger.debug("Disconnected from UMRS server")

    def _cleanup(self):
        """Clean up ZeroMQ resources."""
        if self._control_socket:
            try:
                self._control_socket.close()
            except Exception:
                pass
            self._control_socket = None

        if self._context:
            try:
                self._context.term()
            except Exception:
                pass
            self._context = None

    def _ping(self) -> bool:
        """
        Ping the UMRS server to check if it's ready.

        Matches the versioned liveness handshake (protocol of record
        v1.0, D-04): sends HAND_CHECK, expects ``READY!`` (pre-1.0) or
        ``READY!:v<version>`` — parsed by the shared seam, never a bare
        string compare. On timeout, retries once with "READY?" fallback.

        Returns:
            True if the server's reply parses as ready.
        """
        if not self._control_socket:
            return False

        try:
            self._control_socket.send_string("HAND_CHECK")
            frames = self._control_socket.recv_multipart()
            answer = frames[-1].decode("utf-8") if frames else ""
            if parse_handshake_reply(answer)[0]:
                return True
        except zmq.Again:
            pass
        except Exception as e:
            logger.debug(f"Ping failed: {e}")
            return False

        # Retry once with READY? fallback (matches UMRS behaviour)
        try:
            self._reset_control_socket()
            self._control_socket.send_string("READY?")
            frames = self._control_socket.recv_multipart()
            answer = frames[-1].decode("utf-8") if frames else ""
            return parse_handshake_reply(answer)[0]
        except zmq.Again:
            return False
        except Exception as e:
            logger.debug(f"Ping retry failed: {e}")
            return False

    def _reset_control_socket(self) -> None:
        """Reset the control socket to clear any stale state."""
        if not self._context or not self._control_socket:
            return
        try:
            old_rcv = self._control_socket.getsockopt(zmq.RCVTIMEO)
            old_snd = self._control_socket.getsockopt(zmq.SNDTIMEO)
            self._control_socket.close()
            server_addr = "127.0.0.1" if self._config.tunnel_mode else self._config.server_host
            self._control_socket = self._context.socket(zmq.DEALER)
            self._control_socket.setsockopt(zmq.RCVTIMEO, old_rcv)
            self._control_socket.setsockopt(zmq.SNDTIMEO, old_snd)
            self._control_socket.setsockopt(zmq.LINGER, 0)
            self._apply_zmq_security(self._control_socket)
            self._control_socket.connect(f"tcp://{server_addr}:{self._config.control_port}")
        except Exception as e:
            logger.debug(f"Control socket reset failed: {e}")

    @staticmethod
    def _curve_key(name: str, value: Optional[str]) -> bytes:
        if not value:
            raise R4Error(f"{name} is required when UMRS_ZMQ_AUTH_TYPE=curve")
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise R4Error(f"{name} must be ASCII Z85 text") from exc
        if len(encoded) != 40:
            raise R4Error(f"{name} must be 40 characters of Z85-encoded CURVE key material")
        return encoded

    def _targets_remote(self) -> bool:
        """True when the socket reaches past this host (SSH tunnel or routable host)."""
        return bool(self._config.tunnel_mode) or not _is_loopback_host(self._config.server_host)

    def _resolve_auth_type(self) -> str:
        """The ZMQ auth mode to use; ``none`` only by opt-in or for a loopback server.

        Auto-detect: when UMRS_ZMQ_AUTH_TYPE is unset and the user has a valid
        JWT (OIDC or admin-issued HS256), use session-CURVE so ZMQ is secured
        without manual env vars (opaque API keys are excluded by
        _get_jwt_for_session()). Without one, a remote target is refused: an
        expired token or an opaque key used to open a plaintext socket, so a
        rogue listener on the tunnel port (tunnel down on a shared login node)
        received commands and upload bytes in clear (c2-transport Q1-a).
        """
        auth_type = (self._config.zmq_auth_type or "none").lower()
        if auth_type != "none" or os.environ.get("UMRS_ZMQ_AUTH_TYPE"):
            return auth_type
        if self._get_jwt_for_session() is not None:
            logger.debug("Auto-detected JWT — using ZMQ session-CURVE mode")
            return "session"
        if self._targets_remote():
            via = " (SSH tunnel)" if self._config.tunnel_mode else ""
            raise R4Error(
                "Refusing a plaintext ZMQ connection to "
                f"{self._config.server_host}:{self._config.control_port}{via}: "
                "no valid JWT — run `r4 login`, or set UMRS_ZMQ_AUTH_TYPE=none "
                "to opt into plaintext"
            )
        return "none"

    def _apply_zmq_security(self, socket: Any) -> None:
        auth_type = self._resolve_auth_type()
        if auth_type == "none":
            return
        if auth_type == "session":
            self._apply_session_curve(socket)
            return
        if auth_type != "curve":
            raise R4Error(
                f"Unsupported UMRS_ZMQ_AUTH_TYPE={self._config.zmq_auth_type!r}; "
                "expected 'none', 'curve', or 'session'"
            )
        socket.curve_serverkey = self._curve_key(
            "UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY",
            self._config.curve_server_public_key,
        )
        socket.curve_publickey = self._curve_key(
            "UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY",
            self._config.curve_client_public_key,
        )
        socket.curve_secretkey = self._curve_key(
            "UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY",
            self._config.curve_client_secret_key,
        )

    def _apply_session_curve(self, socket: Any) -> None:
        """Apply CURVE keys from ephemeral session (auto-bootstrapped).

        Always goes through ``ensure_session()`` so the 60-second TTL
        threshold is enforced — a nearly-expired session is replaced
        before a new socket is configured.
        """
        if self._session_mgr is None:
            self._session_mgr = self._new_session_manager()

        jwt_token = self._get_jwt_for_session()
        if jwt_token is None:
            raise R4Error("UMRS_ZMQ_AUTH_TYPE=session requires a valid JWT. Run 'r4 login' first.")
        session = self._session_mgr.ensure_session(jwt_token)
        keys = {
            "curve_server_public_key": session.server_public_key,
            "curve_client_public_key": session.client_public_key,
            "curve_client_secret_key": session.client_secret_key,
        }

        socket.curve_serverkey = self._curve_key(
            "session:server_public_key",
            keys["curve_server_public_key"],
        )
        socket.curve_publickey = self._curve_key(
            "session:client_public_key",
            keys["curve_client_public_key"],
        )
        socket.curve_secretkey = self._curve_key(
            "session:client_secret_key",
            keys["curve_client_secret_key"],
        )

    def _new_session_manager(self) -> Any:
        """CURVE session manager for this transport's deployment.

        Registration goes to the Gateway URL when available, else the
        UMRS_BASE_URL env var or the localhost default. A transport bound to
        a non-active source keeps its session in a file of its own
        (:func:`r4.curve_session.session_file_name_for`).
        """
        from .curve_session import CurveSessionManager, session_file_name_for

        umrs_base_url = self._resolve_session_base_url()
        if self._bound_session() is None:
            return CurveSessionManager(umrs_base_url=umrs_base_url)
        return CurveSessionManager(
            umrs_base_url=umrs_base_url, session_file_name=session_file_name_for(umrs_base_url)
        )

    def _bound_session(self) -> Optional[tuple[Optional[str], Optional[str]]]:
        """``(gateway_url, credential)`` when this transport serves a bound source.

        Onboarding plan S1: a transport for a config bound to one source
        (``r4.source_context``) registers its CURVE session at that source's
        gateway with that source's own credential — never the active login.
        ``None`` = not bound: the active-source behaviour below applies.
        """
        if self._config.session_bound:
            return self._config.session_gateway_url, self._config.session_credential
        if self._config.session_bound is False:
            return None
        # Not given: follow the process-wide config only when one is already
        # in place (``--source`` sets it); never load one just to ask.
        from . import config as _config_module

        cfg = getattr(_config_module, "_config", None)
        if getattr(cfg, "bound_source", None):
            return cfg.gateway_url, cfg.api_key
        return None

    @staticmethod
    def _active_session_profile() -> str:
        """The auth profile of the ACTIVE platform source (round-5 T3), else ``default``."""
        try:
            from .source import resolve_active_source

            active_src = resolve_active_source()
            if active_src is not None and getattr(active_src, "kind", None) == "platform":
                return active_src.name
        except Exception:
            pass
        return "default"

    def _get_jwt_for_session(self) -> Optional[str]:
        """Load the JWT bearer token from the R4 auth stack.

        Returns only tokens that look like JWTs (``header.payload.sig``).
        Opaque API keys are skipped so auto-detection doesn't attempt
        session-CURVE with a non-JWT credential.

        Profile resolution (round-5 T3): the AuthManager profile must be the
        ACTIVE platform source — `AuthManager()` alone falls back to the r4
        `default` profile whose stored token is an opaque test credential the
        platform rejects ("Invalid or missing API key" → the T6 chain's 401).
        A transport bound to one source uses only that source's credential.
        """
        bound = self._bound_session()
        if bound is not None:
            return _jwt_or_none(bound[1])
        try:
            from .auth import AuthManager

            header = AuthManager(profile=self._active_session_profile()).get_auth_header()
            parts = (header or {}).get("Authorization", "").split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return _jwt_or_none(parts[1])
        except Exception as exc:
            logger.debug("Could not load JWT for CURVE session: %s", exc)
        return None

    def _resolve_session_base_url(self) -> Optional[str]:
        """Resolve the base URL for CURVE session registration.

        Prefers the Gateway URL from the R4 config (remote deployments
        proxy ``/umrs/zmq/*`` to UMRS). Falls back to ``UMRS_BASE_URL``
        env var, then ``None`` (CurveSessionManager uses its own default).
        A transport bound to one source registers at that source's gateway.
        """
        bound = self._bound_session()
        if bound is not None:
            return (bound[0] or "").rstrip("/") or None
        try:
            from .config import get_config

            gw_url = get_config().gateway_url
            if gw_url:
                return gw_url.rstrip("/")
        except Exception:
            pass
        return os.environ.get("UMRS_BASE_URL")

    def _create_data_channel(self, client_id: str, timeout_ms: int = 30_000) -> Any:
        """
        Negotiate a data channel with UMRS via CREATE_DATA_QUEUE.

        Matches UMRS MessageQueue.get_data_queue() protocol:
        sends CREATE_DATA_QUEUE:{client_id}, expects tcp://host:port reply.
        Returns a connected PUSH socket.

        Args:
            client_id: Username or client identifier.
            timeout_ms: Timeout for data socket operations.

        Returns:
            A connected zmq.PUSH socket for the data channel.
        """
        safe_client_id = (client_id or "").replace(":", "_")
        request_str = f"CREATE_DATA_QUEUE:{safe_client_id}"
        self._control_socket.send_string(request_str)

        frames = self._control_socket.recv_multipart()
        reply = frames[-1].decode("utf-8") if frames else ""

        if not reply.startswith("tcp://"):
            raise R4Error(f"Unexpected reply from CREATE_DATA_QUEUE: {reply}")

        # Parse tcp://host:port
        endpoint = reply[len("tcp://") :]
        server_ip, port_str = endpoint.split(":")
        port = int(port_str)

        # In tunnel mode, always connect to localhost
        if self._config.tunnel_mode:
            server_ip = "127.0.0.1"

        data_socket = self._context.socket(zmq.PUSH)
        data_socket.setsockopt(zmq.SNDHWM, 10240)
        data_socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
        data_socket.setsockopt(zmq.LINGER, 0)
        self._apply_zmq_security(data_socket)
        data_socket.connect(f"tcp://{server_ip}:{port}")
        return data_socket

    def upload_file(
        self,
        file_path: str,
        bucket_alias: str,
        username: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: int = 4096,
    ) -> UploadStatus:
        """
        Upload a file via ZeroMQ using the UMRS wire protocol.

        Matches UMRS MessageQueue protocol:
        1. CREATE_DATA_QUEUE:{client_id} → tcp://host:port
        2. Stream binary chunks + __END_OF_FILE__ sentinel via data channel
        3. FILE:{client_id}:{nb_files} on control → ACKNOWLEDGE

        Args:
            file_path: Path to file to upload.
            bucket_alias: Target bucket alias.
            username: Username for bucket access.
            metadata: Optional metadata to attach.
            chunk_size: Size of chunks for transfer.

        Returns:
            UploadStatus with results.
        """
        if not self._connected:
            return UploadStatus(success=False, error="Not connected to UMRS server")

        path = Path(file_path)
        if not path.exists():
            return UploadStatus(success=False, error=f"File not found: {file_path}")

        data_socket = None
        try:
            # 1. Negotiate data channel
            data_socket = self._create_data_channel(username)

            # 2. Stream file via data channel (matches MessageQueue.send_file())
            chunks_sent = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    data_socket.send(chunk)
                    chunks_sent += 1
            # Send end-of-file sentinel
            data_socket.send(b"__END_OF_FILE__")

            # 3. Notify server: FILE:{client_id}:{nb_files}
            safe_client_id = (username or "").replace(":", "_")
            request_str = f"FILE:{safe_client_id}:1"
            self._control_socket.send_string(request_str)

            frames = self._control_socket.recv_multipart()
            reply = frames[-1].decode("utf-8") if frames else ""

            if reply == "ACKNOWLEDGE":
                return UploadStatus(
                    success=True,
                    s3_key=f"{bucket_alias}/{path.name}",
                    chunks_sent=chunks_sent,
                )
            elif reply == "NO_DATA_QUEUE":
                return UploadStatus(
                    success=False,
                    error=f"Server could not find data queue for user {username}",
                    chunks_sent=chunks_sent,
                )
            else:
                return UploadStatus(
                    success=False,
                    error=f"Unexpected server reply: {reply}",
                    chunks_sent=chunks_sent,
                )

        except zmq.Again:
            return UploadStatus(success=False, error="UMRS server timeout")
        except R4Error as e:
            return UploadStatus(success=False, error=str(e))
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return UploadStatus(success=False, error=str(e))
        finally:
            if data_socket:
                try:
                    data_socket.close()
                except Exception:
                    pass

    def upload_directory(
        self,
        dir_path: str,
        bucket_alias: str,
        username: str,
        *,
        pattern: str = "*",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[UploadStatus]:
        """
        Upload all files in a directory.

        Args:
            dir_path: Path to directory.
            bucket_alias: Target bucket alias.
            username: Username for bucket access.
            pattern: Glob pattern for file selection.
            metadata: Optional metadata to attach to all files.

        Returns:
            List of UploadStatus for each file.
        """
        from glob import glob

        path = Path(dir_path)
        if not path.is_dir():
            return [UploadStatus(success=False, error=f"Not a directory: {dir_path}")]

        results = []
        for file_path in glob(str(path / pattern)):
            if Path(file_path).is_file():
                status = self.upload_file(file_path, bucket_alias, username, metadata=metadata)
                results.append(status)

        return results

    def get_status(
        self,
        username: str,
        target: str = "all",
    ) -> Dict[str, Any]:
        """
        Get UMRS server status using the wire protocol.

        Delegates to _send_command with STATUS:{client_id}:{key}.

        Args:
            username: User requesting status.
            target: Status target (all, embedding, etc).

        Returns:
            Status response dict.
        """
        import json

        safe_key = (target or "all").replace(":", "_")
        reply = self._send_command("STATUS", username, safe_key)
        try:
            return json.loads(reply)
        except (json.JSONDecodeError, TypeError):
            return {"status": "ok", "raw": reply}

    # -----------------------------------------------------------------
    # Direct UMRS command methods (for HPC-local / direct transport)
    # -----------------------------------------------------------------

    def _send_command(
        self,
        cmd: str,
        client_id: str,
        payload: str = "",
        timeout_ms: int = 300_000,
    ) -> str:
        """
        Send a command to UMRS using the wire protocol: {CMD}:{client_id}:{payload}.

        This matches the MessageQueue.request_model_command() protocol used by
        the UMRS CLI client.

        Args:
            cmd: UMRS command string (e.g. "MODEL_LIST", "JOB_SUBMIT").
            client_id: Username or client identifier.
            payload: JSON-encoded payload string.
            timeout_ms: Receive timeout in milliseconds.

        Returns:
            Raw response string from UMRS server.
        """
        if not self._connected:
            raise R4Error("Not connected to UMRS server")

        safe_client_id = (client_id or "").replace(":", "_")
        request_str = f"{cmd}:{safe_client_id}:{payload}"

        try:
            # Set generous timeout for potentially slow operations
            old_rcv = self._control_socket.getsockopt(zmq.RCVTIMEO)
            self._control_socket.setsockopt(zmq.RCVTIMEO, timeout_ms)

            self._control_socket.send_string(request_str)

            # DEALER receives: [empty][payload] or [payload]
            frames = self._control_socket.recv_multipart()
            if len(frames) == 2:
                _, msg = frames
            elif len(frames) == 1:
                msg = frames[0]
            else:
                raise R4Error(f"Unexpected frame count from UMRS: {len(frames)}")

            return msg.decode("utf-8")

        except zmq.Again:
            raise R4Error(f"UMRS server timeout on {cmd}")
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"{cmd} request failed: {e}")
        finally:
            try:
                self._control_socket.setsockopt(zmq.RCVTIMEO, old_rcv)
            except Exception:
                pass

    def _send_command_json(
        self,
        cmd: str,
        client_id: str,
        payload_dict: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 300_000,
    ) -> Dict[str, Any]:
        """
        Send a command with JSON payload and parse the JSON response.

        Args:
            cmd: UMRS command string.
            client_id: Username or client identifier.
            payload_dict: Dict to JSON-encode as payload.
            timeout_ms: Receive timeout in milliseconds.

        Returns:
            Parsed response dict.
        """
        import json

        payload_str = json.dumps(payload_dict) if payload_dict else ""
        reply = self._send_command(cmd, client_id, payload_str, timeout_ms)

        try:
            return json.loads(reply)
        except (json.JSONDecodeError, TypeError):
            # Some commands return plain strings (e.g. "ACKNOWLEDGE")
            return {"status": "ok" if reply == "ACKNOWLEDGE" else "error", "raw": reply}

    def request_model_list(
        self,
        username: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """List models via ZMQ MODEL_LIST command."""
        return self._send_command_json("MODEL_LIST", username, filters)

    def request_model_register(
        self,
        username: str,
        model_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Register a model via ZMQ MODEL_REGISTER command."""
        return self._send_command_json("MODEL_REGISTER", username, model_data)

    def request_model_download(
        self,
        username: str,
        download_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Initiate model download via ZMQ MODEL_DOWNLOAD command."""
        return self._send_command_json("MODEL_DOWNLOAD", username, download_spec)

    def request_job_list(
        self,
        username: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """List jobs via ZMQ JOB_LIST command."""
        return self._send_command_json("JOB_LIST", username, filters)

    def request_job_submit(
        self,
        username: str,
        job_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Submit a job via ZMQ JOB_SUBMIT command."""
        return self._send_command_json("JOB_SUBMIT", username, job_spec)

    def request_job_cancel(
        self,
        username: str,
        cancel_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Cancel a job via ZMQ JOB_CANCEL command."""
        return self._send_command_json("JOB_CANCEL", username, cancel_spec)

    def request_workflow_list(
        self,
        username: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """List workflows via ZMQ LIST_WORKFLOWS command."""
        return self._send_command_json("LIST_WORKFLOWS", username, filters)

    def request_workflow_run(
        self,
        username: str,
        run_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run a workflow via ZMQ RUN_WORKFLOW command."""
        return self._send_command_json("RUN_WORKFLOW", username, run_spec)

    def request_workflow_upload(
        self,
        username: str,
        workflow_name: str,
        archive_path: str,
        timeout_ms: int = 120_000,
    ) -> Dict[str, Any]:
        """
        Upload a workflow archive via ZMQ UPLOAD_WORKFLOW command.

        Uses the UMRS wire protocol:
        1. CREATE_DATA_QUEUE:{client_id} → tcp://host:port
        2. Stream archive chunks + __END_OF_FILE__ via data channel
        3. UPLOAD_WORKFLOW:{client_id}:{workflow_name} on control → ACKNOWLEDGE
        """
        if not self._connected:
            raise R4Error("Not connected to UMRS server")

        archive = Path(archive_path)
        if not archive.is_file():
            raise R4Error(f"Workflow archive not found: {archive_path}")

        safe_client_id = (username or "").replace(":", "_")
        safe_workflow_name = (workflow_name or archive.stem).replace(":", "_")

        old_control_rcv = self._control_socket.getsockopt(zmq.RCVTIMEO)
        old_control_snd = self._control_socket.getsockopt(zmq.SNDTIMEO)
        data_socket = None

        try:
            self._control_socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
            self._control_socket.setsockopt(zmq.SNDTIMEO, timeout_ms)

            # 1. Negotiate data channel
            data_socket = self._create_data_channel(safe_client_id, timeout_ms)

            # 2. Stream archive via data channel
            with open(archive, "rb") as handle:
                while True:
                    chunk = handle.read(4096)
                    if not chunk:
                        break
                    data_socket.send(chunk)
            data_socket.send(b"__END_OF_FILE__")

            # 3. Notify server with UPLOAD_WORKFLOW command
            self._control_socket.send_string(
                f"UPLOAD_WORKFLOW:{safe_client_id}:{safe_workflow_name}"
            )

            frames = self._control_socket.recv_multipart()
            if len(frames) == 2:
                _, msg = frames
            elif len(frames) == 1:
                msg = frames[0]
            else:
                raise R4Error(f"Unexpected frame count from UMRS: {len(frames)}")

            reply = msg.decode("utf-8")
            if reply != "ACKNOWLEDGE":
                raise R4Error(f"Unexpected reply from server during workflow upload: {reply}")

            return {
                "status": "ok",
                "workflow_name": safe_workflow_name,
                "accepted": True,
                "message": f"Workflow '{safe_workflow_name}' upload accepted",
            }
        except zmq.Again:
            raise R4Error("UMRS server timeout on UPLOAD_WORKFLOW")
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"UPLOAD_WORKFLOW request failed: {e}")
        finally:
            if data_socket:
                try:
                    data_socket.close()
                except Exception:
                    pass
            try:
                self._control_socket.setsockopt(zmq.RCVTIMEO, old_control_rcv)
                self._control_socket.setsockopt(zmq.SNDTIMEO, old_control_snd)
            except Exception:
                pass

    def request_export_embeddings(
        self,
        username: str,
        path_to_bucket: str,
        export_format: str = "csv",
    ) -> str:
        """
        Export embeddings via ZMQ EXPORT_EMBEDDINGS command.

        Args:
            username: Client identifier.
            path_to_bucket: Bucket path for the embeddings.
            export_format: Output format (csv, json, etc).

        Returns:
            Raw response string with serialized embeddings data.
        """
        safe_args = f"{path_to_bucket};{export_format}".replace(":", "_")
        return self._send_command(
            "EXPORT_EMBEDDINGS",
            username,
            safe_args,
            timeout_ms=120_000,
        )

    def request_status_cmd(
        self,
        username: str,
        status_key: str = "all",
    ) -> str:
        """
        Get UMRS status using the wire protocol (STATUS:{client}:{key}).

        This is the low-level version matching MessageQueue.request_status().

        Args:
            username: Client identifier.
            status_key: Status key to query.

        Returns:
            Raw JSON string response.
        """
        safe_key = (status_key or "all").replace(":", "_")
        return self._send_command("STATUS", username, safe_key)


# Convenience function for simple uploads
def quick_upload(
    file_path: str,
    bucket_alias: str,
    username: str,
    *,
    server_host: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> UploadStatus:
    """
    Quick one-shot file upload.

    Args:
        file_path: Path to file.
        bucket_alias: Target bucket.
        username: Username.
        server_host: Optional server host override.
        metadata: Optional metadata.

    Returns:
        UploadStatus.
    """
    # Check file exists before attempting connection
    if not Path(file_path).exists():
        return UploadStatus(success=False, error=f"File not found: {file_path}")

    config = UMRSTransportConfig.from_env()
    if server_host:
        config.server_host = server_host

    transport = UMRSTransport(config)
    if not transport.connect():
        return UploadStatus(success=False, error="Cannot connect to UMRS server")

    try:
        return transport.upload_file(file_path, bucket_alias, username, metadata=metadata)
    finally:
        transport.disconnect()
