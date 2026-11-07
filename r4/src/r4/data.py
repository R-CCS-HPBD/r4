"""
R4 Data Client.

File transfer operations for the R4 SDK using UMRS ZeroMQ transport.

Separation of concerns:
- DataClient: File upload/download (UMRS ZeroMQ)
- RAGClient: Embedding and semantic search (HTTP)
- WorkflowClient: n8n pipeline triggers (HTTP)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .errors import R4Error

if TYPE_CHECKING:
    from .client import Client

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    """Result of a file upload."""

    file_id: str
    filename: str
    status: str
    size_bytes: int = 0
    chunks: int = 0
    s3_key: Optional[str] = None
    presigned_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DownloadResult:
    """Result of a file download."""

    filename: str
    local_path: str
    size_bytes: int = 0
    status: str = "success"
    error: Optional[str] = None


@dataclass
class FileInfo:
    """Information about a file in storage."""

    file_id: str
    filename: str
    size_bytes: int
    content_type: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class DataClient:
    """
    Data transfer client using UMRS ZeroMQ transport.

    Access via Client.data:
        client = Client()
        result = client.data.upload("ct_scan.tif", project_id="example-project-c")
        results = client.data.upload_directory("ct_scans/", pattern="*.tif")

    Features:
    - File upload via ZeroMQ (efficient binary transfer)
    - Directory batch upload
    - File download
    - Presigned URL generation

    Transport:
    - Uses UMRS ZeroMQ transport when available (Control: 5555, Data: 5556)
    - Falls back to HTTP when ZeroMQ unavailable
    - Configure via R4_USE_UMRS_TRANSPORT=false to force HTTP
    """

    def __init__(self, parent: "Client"):
        """Initialize Data client."""
        self._parent = parent
        self._umrs_transport = None  # Lazy-init

    @property
    def _config(self):
        """Get config from parent."""
        return self._parent.config

    @property
    def _transport(self):
        """Get UMRS transport (lazy initialization)."""
        if self._umrs_transport is None and self._config.use_umrs_transport:
            try:
                from .source_context import transport_session_fields
                from .umrs_transport import UMRSTransport, UMRSTransportConfig

                config = UMRSTransportConfig.for_server(
                    server_host=self._config.umrs_server_host,
                    control_port=self._config.umrs_control_port,
                    tunnel_mode=self._config.umrs_tunnel_mode,
                    **transport_session_fields(self._config),
                )
                self._umrs_transport = UMRSTransport(config)
            except ImportError:
                logger.debug("UMRS transport not available (pyzmq not installed)")
                self._umrs_transport = False  # Mark as unavailable
        return self._umrs_transport if self._umrs_transport else None

    _RETIRED_DATA_HTTP = "No server route for {path} — UMRS's HTTP data API (legacy Gen1)"

    def _raise_retired_http(self, path: str) -> "R4Error":
        return R4Error(
            f"{self._RETIRED_DATA_HTTP.format(path=path)} has no server route "
            "(retired R4, drift remediation 2026-08-22)."
            " Use UMRS ZeroMQ transport (default) transport for upload/download, "
            " or the catalog API (/gw/catalog) for project file lookup."
        )

    def _get_username(self) -> str:
        """Get username for UMRS operations."""
        return self._config.umrs_username or self._config.project_id or "default"

    def upload(
        self,
        file_path: str,
        *,
        project_id: Optional[str] = None,
        bucket_alias: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force_http: bool = False,
    ) -> UploadResult:
        """
        Upload a file to storage.

        Uses UMRS ZeroMQ transport when available for efficient binary transfer.
        Falls back to HTTP when ZeroMQ is not available or force_http=True.

        Args:
            file_path: Path to the file to upload.
            project_id: Project to upload to.
            bucket_alias: UMRS bucket alias (for ZeroMQ transport).
            metadata: Optional metadata to attach.
            force_http: Force HTTP transport even if ZeroMQ available.

        Returns:
            UploadResult with file ID and status.
        """
        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required for file uploads")

        path = Path(file_path)
        if not path.exists():
            raise R4Error(f"File not found: {file_path}")

        # Round-5 task 2: the HTTP upload route is PERMANENTLY retired
        # (2026-08-22 drift remediation). No fallback onto a dead route; a
        # transport failure surfaces as a typed error the caller can act on
        # (connectivity vs auth vs construction).
        if not force_http and self._transport is not None:
            try:
                return self._upload_via_umrs(path, project_id, bucket_alias, metadata)
            except Exception as e:
                raise _zmq_unavailable(e) from e
        raise _zmq_unavailable(None)

    def _upload_via_umrs(
        self,
        path: Path,
        project_id: str,
        bucket_alias: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> UploadResult:
        """Upload via UMRS ZeroMQ transport."""
        transport = self._transport
        if transport is None:
            raise R4Error("UMRS transport not available")

        # Connect if not already connected
        if not transport.is_connected:
            if not transport.connect():
                raise R4Error(
                    "Cannot connect to UMRS server: "
                    f"{transport.last_connect_error or 'no response'}"
                )

        username = self._get_username()
        bucket = bucket_alias or project_id

        # Perform upload
        status = transport.upload_file(
            str(path),
            bucket,
            username,
            metadata=metadata,
        )

        if status.success:
            logger.info(f"Uploaded {path.name} via ZeroMQ ({status.chunks_sent} chunks)")
            return UploadResult(
                file_id=status.s3_key or path.name,
                filename=path.name,
                status="success",
                size_bytes=path.stat().st_size,
                chunks=status.chunks_sent,
                s3_key=status.s3_key,
            )
        else:
            raise R4Error(f"UMRS upload failed: {status.error}")

    def _upload_via_http(
        self,
        path: Path,
        project_id: str,
        metadata: Optional[Dict[str, Any]],
    ) -> UploadResult:
        """Upload via HTTP (retired R4, drift remediation 2026-08-22).

        /gw/data/upload has no server route in current platform builds; this
        method exists only to fail with a clear error instead of a 404 trail.
        """
        raise self._raise_retired_http("/gw/data/upload")

    def upload_directory(
        self,
        dir_path: str,
        *,
        project_id: Optional[str] = None,
        bucket_alias: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        pattern: str = "*",
        force_http: bool = False,
    ) -> List[UploadResult]:
        """
        Upload all files in a directory.

        Uses UMRS ZeroMQ transport when available for efficient batch upload.

        Args:
            dir_path: Path to directory.
            project_id: Project to upload to.
            bucket_alias: UMRS bucket alias (for ZeroMQ transport).
            metadata: Optional metadata to attach to all files.
            pattern: Glob pattern for file selection (default: "*").
            force_http: Force HTTP transport.

        Returns:
            List of UploadResult for each file.
        """

        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required for directory uploads")

        dir_path_obj = Path(dir_path)
        if not dir_path_obj.is_dir():
            raise R4Error(f"Not a directory: {dir_path}")

        files = self._collect_directory_files(dir_path_obj, pattern, dir_path)
        if not files:
            return []

        # Try UMRS ZeroMQ batch upload
        if not force_http and self._transport is not None:
            try:
                return self._upload_directory_via_umrs(files, project_id, bucket_alias, metadata)
            except Exception as e:
                logger.warning(f"UMRS batch upload failed, falling back to HTTP: {e}")

        # Fallback: upload files one by one via HTTP
        return self._upload_files_individually(files, project_id, metadata)

    def _collect_directory_files(
        self, dir_path_obj: Path, pattern: str, dir_path: str
    ) -> List[Path]:
        """Match files under a directory; warn+empty when nothing matches."""
        files = [f for f in dir_path_obj.glob(pattern) if f.is_file()]
        if not files:
            logger.warning(f"No files matching pattern '{pattern}' in {dir_path}")
        return files

    def _upload_files_individually(
        self,
        files: List[Path],
        project_id: str,
        metadata: Optional[Dict[str, Any]],
    ) -> List[UploadResult]:
        """HTTP fallback: upload each file, collecting per-file failures."""
        results = []
        for file_path in files:
            try:
                results.append(self._upload_via_http(file_path, project_id, metadata))
            except Exception as e:
                results.append(
                    UploadResult(
                        file_id="",
                        filename=file_path.name,
                        status="error",
                        error=str(e),
                    )
                )
        return results

    def _upload_directory_via_umrs(
        self,
        files: List[Path],
        project_id: str,
        bucket_alias: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> List[UploadResult]:
        """Upload directory via UMRS ZeroMQ transport."""
        transport = self._transport
        if transport is None:
            raise R4Error("UMRS transport not available")

        if not transport.is_connected:
            if not transport.connect():
                raise R4Error("Cannot connect to UMRS server")

        username = self._get_username()
        bucket = bucket_alias or project_id

        results = []
        for file_path in files:
            status = transport.upload_file(
                str(file_path),
                bucket,
                username,
                metadata=metadata,
            )

            if status.success:
                results.append(
                    UploadResult(
                        file_id=status.s3_key or file_path.name,
                        filename=file_path.name,
                        status="success",
                        size_bytes=file_path.stat().st_size,
                        chunks=status.chunks_sent,
                        s3_key=status.s3_key,
                    )
                )
            else:
                results.append(
                    UploadResult(
                        file_id="",
                        filename=file_path.name,
                        status="error",
                        error=status.error,
                    )
                )

        logger.info(f"Uploaded {len(results)} files via ZeroMQ")
        return results

    def download(
        self,
        file_id: str,
        local_path: str,
        *,
        project_id: Optional[str] = None,
        force_http: bool = False,
    ) -> DownloadResult:
        """
        Download a file from storage.

        Args:
            file_id: File ID or S3 key to download.
            local_path: Local path to save the file.
            project_id: Project the file belongs to.
            force_http: Force HTTP transport.

        Returns:
            DownloadResult with local path and status.
        """
        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required for file downloads")

        # Try UMRS ZeroMQ transport first
        if not force_http and self._transport is not None:
            try:
                return self._download_via_umrs(file_id, local_path, project_id)
            except Exception as e:
                logger.warning(f"UMRS download failed, falling back to HTTP: {e}")

        # Fallback to HTTP
        return self._download_via_http(file_id, local_path, project_id)

    def _download_via_umrs(
        self,
        file_id: str,
        local_path: str,
        project_id: str,
    ) -> DownloadResult:
        """Download via UMRS ZeroMQ transport."""
        transport = self._transport
        if transport is None:
            raise R4Error("UMRS transport not available")

        if not transport.is_connected:
            if not transport.connect():
                raise R4Error("Cannot connect to UMRS server")

        username = self._get_username()
        bucket = project_id

        success = transport.download_file(file_id, bucket, username, local_path)

        if success:
            path = Path(local_path)
            return DownloadResult(
                filename=path.name,
                local_path=str(path.resolve()),
                size_bytes=path.stat().st_size if path.exists() else 0,
                status="success",
            )
        else:
            raise R4Error(f"UMRS download failed for {file_id}")

    def _download_via_http(
        self,
        file_id: str,
        local_path: str,
        project_id: str,
    ) -> DownloadResult:
        """Download via HTTP (retired R4, drift remediation 2026-08-22)."""
        raise self._raise_retired_http("/gw/data/download")

    def get_presigned_url(
        self,
        file_id: str,
        *,
        project_id: Optional[str] = None,
        expiry_seconds: int = 3600,
    ) -> str:
        """
        Get a presigned URL for direct file access.

        Args:
            file_id: File ID or S3 key.
            project_id: Project the file belongs to.
            expiry_seconds: URL expiry time in seconds (default: 1 hour).

        Returns:
            Presigned URL string.
        """
        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required")

        # Try UMRS transport first
        if self._transport is not None:
            try:
                transport = self._transport
                if not transport.is_connected:
                    transport.connect()
                return transport.get_presigned_url(file_id, project_id, expiry_seconds)
            except Exception as e:
                logger.warning(f"UMRS presigned URL failed, falling back to HTTP: {e}")

        # HTTP fallback retired (R4): /gw/data/presigned-url has no server route
        raise self._raise_retired_http("/gw/data/presigned-url")

    def list_files(
        self,
        *,
        project_id: Optional[str] = None,
        prefix: Optional[str] = None,
        limit: int = 100,
    ) -> List[FileInfo]:
        """
        List files in storage.

        Args:
            project_id: Project to list files from.
            prefix: Optional prefix filter.
            limit: Maximum number of files to return.

        Returns:
            List of FileInfo objects.
        """
        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required")

        # Retired R4 (drift 2026-08-22): /gw/data/files has no server route.
        raise self._raise_retired_http("/gw/data/files")


def _zmq_unavailable(exc: Exception | None) -> "R4Error":
    """Round-5 task 2: typed transport error replacing any retired-route path.

    When the client's ZMQ upload path failed at runtime, this text is what the
    on-agent model sees: `[zmq_unavailable:<reason>]` plus a flat recovery list.
    No hidden route escape hatch remains for the data plane.
    """
    reason = "not_constructible"
    if exc is not None:
        exc_text = str(exc).lower()
        if "401" in exc_text or "unauthorized" in exc_text or "auth" in exc_text:
            reason = "auth_failed"
        elif any(
            marker in exc_text for marker in ("timeout", "unreachable", "refused", "no route")
        ):
            reason = "connect_failed"
        else:
            reason = "zmq_failed"
    return R4Error(
        f"[zmq_unavailable:{reason}] file upload via ZeroMQ transport failed. "
        "HTTP fallback is retired. Retry after re-authenticating, or use the catalog API."
    )
