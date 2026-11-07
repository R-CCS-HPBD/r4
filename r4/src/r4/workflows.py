"""
R4 Workflow Client.

N8n pipeline triggers and management for the R4 SDK.

Separation of concerns:
- DataClient: File upload/download (UMRS ZeroMQ)
- RAGClient: Embedding and semantic search (HTTP)
- WorkflowClient: n8n pipeline triggers (HTTP)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Dict, List, Optional
from urllib.parse import quote

from r4_contracts.workflows import (
    WorkflowCatalogItem as WorkflowDefinition,
)
from r4_contracts.workflows import (
    WorkflowResult,
    WorkflowStatus,
    WorkflowUploadResult,
)

from .errors import R4ConnectionError, R4Error

if TYPE_CHECKING:
    from .client import Client


def _catalog_ownership(item: Dict[str, Any]) -> Dict[str, Any]:
    """Ownership fields of one UMRS catalog row. UMRS keeps the owner's
    ``user_id``/``scope``/``access`` on a row it shares and marks it
    ``shared: true`` (workflow_registry_handler._append_shared_workflows)."""
    access = item.get("access")
    return {
        "scope": str(item.get("scope") or ""),
        "owner": str(item.get("user_id") or ""),
        "shared": item.get("shared") is True,
        "access": [str(entry) for entry in access] if isinstance(access, list) else [],
    }


def _zip_manifest(path: Path) -> Optional[bytes]:
    """workflow.json of a zip bundle (root or one level deep, as UMRS reads
    it); None when the file is no readable zip or carries none — the server
    then refuses it by name."""
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as zf:
        names = sorted(
            (
                n
                for n in zf.namelist()
                if os.path.basename(n) == "workflow.json" and n.count("/") <= 1
            ),
            key=lambda n: n.count("/"),
        )
        return zf.read(names[0]) if names else None


def _check_upload_contract(raw: bytes, origin: str) -> None:
    """Refuse before the round trip what UMRS would refuse (2026-10-02):
    the upload contract (r4_contracts.workflow_package) makes a description
    and every step's ``depends_on`` mandatory. All errors in one message;
    warnings come back in the server's upload notice."""
    from r4_contracts.workflow_package import errors_of, format_problems, workflow_package_problems

    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise R4Error(f"{origin}: workflow.json is not valid JSON: {exc}") from exc
    errors = errors_of(workflow_package_problems(manifest))
    if errors:
        raise R4Error(
            f"{origin}: workflow.json does not meet the upload contract — "
            f"{format_problems(errors)}. `r4 workflow lint {origin}` lists them with line numbers."
        )


def _zip_directory(path: Path) -> str:
    """Pack a workflow directory into a temp ZIP; return the archive path."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
        archive_path = tmp_zip.name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(path):
            for file_name in files:
                abs_path = os.path.join(root, file_name)
                arc_name = os.path.relpath(abs_path, path)
                zf.write(abs_path, arc_name)
    return archive_path


# ── Workflow export (2026-11-07) ──────────────────────────────────────────
#: The manifest UMRS writes into every ``GET /workflows/{name}/export`` zip.
EXPORT_MANIFEST = "EXPORT.json"
#: Archive members EXPORT.json may leave out of its own ``files`` list.
_EXPORT_UNLISTED = frozenset({EXPORT_MANIFEST, "workflow.json"})
_EXPORT_TIMEOUT_S = 600.0


def _names_directory(dest: str | Path) -> bool:
    """*dest* ends with a separator (``exports/``): a directory is meant even
    when none exists yet. Checked on ``str(dest)`` because ``Path`` drops the
    trailing separator (2026-11-07)."""
    seps = tuple(sep for sep in ("/", os.sep, os.altsep) if sep)
    return str(dest).endswith(seps)


def _export_target(workflow_name: str, dest: str | Path | None) -> Path:
    """Where an export lands: ``./<name>.zip`` by default, ``<dir>/<name>.zip``
    for an existing directory or a *dest* ending in a separator (the caller
    creates it, parents included), else *dest* itself."""
    filename = f"{workflow_name.replace('/', '_')}.zip"
    if dest is None:
        return Path.cwd() / filename
    target = Path(dest).expanduser()
    if _names_directory(dest) or target.is_dir():
        return target / filename
    return target


def read_export_manifest(path: str | Path) -> Dict[str, Any]:
    """EXPORT.json of a workflow export archive (unverified); R4Error when
    the file is no export archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read(EXPORT_MANIFEST)
        manifest = json.loads(raw)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise R4Error(f"{path}: not a readable workflow export archive ({exc})") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise R4Error(f"{path}: {EXPORT_MANIFEST} carries no file list")
    return manifest


def _member_digest(zf: zipfile.ZipFile, name: str) -> tuple[str, int]:
    """sha256 hex and byte size of one archive member, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with zf.open(name) as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _entry_problems(zf: zipfile.ZipFile, members: set[str], entry: Any) -> List[str]:
    """What is wrong with one EXPORT.json ``files`` entry (empty: it matches)."""
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        return [f"malformed {EXPORT_MANIFEST} entry {entry!r}"]
    rel = entry["path"]
    if rel not in members:
        return [f"{rel}: listed but missing from the archive"]
    sha, size = _member_digest(zf, rel)
    if sha != entry.get("sha256"):
        return [f"{rel}: sha256 mismatch"]
    if entry.get("size") is not None and entry.get("size") != size:
        return [f"{rel}: size mismatch ({size} bytes, listed {entry.get('size')})"]
    return []


def _export_problems(path: Path, files: List[Any]) -> List[str]:
    """Every mismatch between an archive and its EXPORT.json file list."""
    problems: List[str] = []
    with zipfile.ZipFile(path) as zf:
        members = {info.filename for info in zf.infolist() if not info.is_dir()}
        for entry in files:
            problems.extend(_entry_problems(zf, members, entry))
    listed = {str(e.get("path")) for e in files if isinstance(e, dict)}
    unlisted = sorted(members - listed - _EXPORT_UNLISTED)
    problems.extend(f"{name}: in the archive but not listed" for name in unlisted)
    return problems


def verify_export(path: str | Path, *, label: str | None = None) -> Dict[str, Any]:
    """Check an export archive against its EXPORT.json (sha256 + size of
    every listed file, no unlisted extras); return the manifest or raise
    R4Error naming the first problems. *label* names the file in errors."""
    shown = label or str(path)
    try:
        manifest = read_export_manifest(path)
    except R4Error as exc:
        raise R4Error(str(exc).replace(str(path), shown)) from exc
    problems = _export_problems(Path(path), manifest["files"])
    if problems:
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise R4Error(f"{shown}: export verification failed: " + "; ".join(problems[:5]) + more)
    return manifest


def _safe_member_target(root: Path, name: str) -> Path:
    """Destination of archive member *name* under *root*; R4Error when the
    name is absolute or climbs out of *root* (zip-slip)."""
    pure = Path(name)
    target = (root / pure).resolve()
    if pure.is_absolute() or ".." in pure.parts or not target.is_relative_to(root):
        raise R4Error(f"refusing archive member {name!r}: it escapes {root}")
    return target


def extract_export(path: str | Path, dest_dir: str | Path, *, overwrite: bool = False) -> Path:
    """Unpack a (verified) export archive into *dest_dir*.

    Every member path is checked before anything is written, so an archive
    with one escaping path writes nothing. Existing files are kept unless
    *overwrite*. Returns the resolved directory.
    """
    root = Path(dest_dir).expanduser().resolve()
    with zipfile.ZipFile(path) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        planned = [(_safe_member_target(root, info.filename), info) for info in infos]
        clashes = [str(t) for t, _ in planned if t.exists()]
        if clashes and not overwrite:
            raise R4Error(f"{clashes[0]} already exists; pass overwrite (CLI: --force)")
        for target, info in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return root


class WorkflowClient:
    """
    Workflow client for n8n pipeline management.

    Access via Client.workflows:
        client = Client()
        result = client.workflows.trigger("ct-analysis", input_files=["scan.tif"])
        status = client.workflows.status(result.execution_id)

    Features:
    - Upload user-defined workflows
    - Trigger n8n workflows
    - Monitor execution status
    - Retrieve results and artifacts
    - Cancel running workflows
    - List available workflows

    Common workflows:
    - ct-analysis: CT scan segmentation
    - video-analysis: video processing
    - embedding: Document embedding pipeline
    """

    def __init__(self, parent: "Client"):
        """Initialize Workflow client."""
        self._parent = parent

    @property
    def _config(self):
        """Get config from parent."""
        return self._parent.config

    @property
    def _router(self):
        router = getattr(self._parent, "router", None)
        if router is not None:
            return router
        from ._transport_router import TransportRouter

        return TransportRouter(self._config)

    @property
    def _api_prefix(self) -> str:
        """/gw (gateway) or /umrs (direct-UMRS) — same probe as the parent.

        Duck-typed parents (test stubs without the probe) default to /gw.
        """
        return getattr(self._parent, "_api_prefix", "/gw")

    def _wf_url(self, suffix: str = "") -> str:
        """Workflows route root (WP-08: no /gw hardcode in direct-UMRS mode)."""
        return f"{self._config.gateway_url}{self._api_prefix}/workflows{suffix}"

    def upload(
        self,
        workflow_path: str,
        *,
        workflow_name: Optional[str] = None,
        force_http: bool = False,
        timeout: Optional[int] = None,
    ) -> WorkflowUploadResult:
        """Upload a workflow directory or zip archive.

        Args:
            workflow_path: Local workflow directory or zip archive.
            workflow_name: Optional workflow name override.
            force_http: Force Gateway HTTP even if direct ZMQ is available.
            timeout: Request timeout in seconds.

        Returns:
            WorkflowUploadResult describing the accepted upload.
        """
        archive_path, resolved_workflow_name, should_cleanup = self._prepare_upload_archive(
            workflow_path,
            workflow_name,
        )

        try:
            if force_http:
                return self._upload_via_http(resolved_workflow_name, archive_path, timeout)

            return self._router.execute(
                "workflow_upload",
                http_fn=lambda: self._upload_via_http(
                    resolved_workflow_name,
                    archive_path,
                    timeout,
                ),
                zmq_fn=lambda: self._upload_via_zmq(resolved_workflow_name, archive_path),
            )
        finally:
            if should_cleanup:
                try:
                    os.remove(archive_path)
                except OSError:
                    pass

    def trigger(
        self,
        workflow_id: str,
        *,
        input_files: Optional[List[str]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
        wait: bool = False,
        timeout: Optional[int] = None,
    ) -> WorkflowResult:
        """
        Trigger a workflow execution.

        Uses ZMQ transport when configured for direct mode, otherwise HTTP.

        Args:
            workflow_id: ID of the workflow to trigger (alias: workflow_name).
            input_files: List of S3 keys or file IDs to process.
            parameters: Workflow-specific parameters.
            project_id: Project context for the workflow.
            wait: Wait for completion before returning.
            timeout: Timeout in seconds when waiting (default: config timeout).

        Returns:
            WorkflowResult with execution ID and status.
        """
        run_params = dict(parameters or {})
        if input_files:
            run_params["input_files"] = input_files
        effective_project_id = project_id or self._config.project_id
        if effective_project_id:
            run_params.setdefault("project_id", effective_project_id)

        run_spec = {
            "workflow_name": workflow_id,
            "params": run_params,
        }

        result = self._router.execute(
            "workflow_run",
            http_fn=lambda: self._trigger_via_http(workflow_id, run_spec, timeout),
            zmq_fn=lambda: self._trigger_via_zmq(workflow_id, run_spec),
        )

        if wait and result.execution_id:
            return self.wait_for_completion(
                result.execution_id,
                timeout=timeout,
            )
        return result

    def plan(
        self,
        workflow_name: str,
        *,
        parameters: Optional[Dict[str, Any]] = None,
        input_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Ask UMRS for a signed plan of a run (ADR-105 D-4); nothing runs.

        The answer carries what the run would use (lane, partition, account,
        GPU steps, an upper-bound wall time, quota), ``signed`` + ``digest``
        to commit it, and whether the project allows an auto commit.
        """
        payload: Dict[str, Any] = {"parameters": dict(parameters or {})}
        if input_files:
            payload["input_files"] = list(input_files)
        return self._parent.http.post(
            self._wf_url(f"/{workflow_name}/plan"),
            json_body=payload,
            timeout=self._config.timeout,
        )

    def commit(
        self,
        workflow_name: str,
        *,
        signed: Dict[str, Any],
        digest: str,
        auto: bool = False,
    ) -> Dict[str, Any]:
        """Run a plan UMRS signed (ADR-105 D-4). ``auto`` = no human approved
        this call; UMRS then applies the project's auto-commit threshold."""
        companion_headers = getattr(self._parent, "_companion_headers", None)
        return self._parent.http.post(
            self._wf_url(f"/{workflow_name}/commit"),
            json_body={"signed": signed, "digest": digest, "auto": bool(auto)},
            timeout=self._config.timeout,
            headers=companion_headers() if callable(companion_headers) else None,
        )

    def describe(self, workflow_name: str) -> Dict[str, Any]:
        """Return workflow metadata and parameter schema."""
        url = self._wf_url(f"/{workflow_name}")
        try:
            return self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to describe workflow: {e}")

    def definition_package(self, workflow_name: str) -> Dict[str, Any]:
        """Definition + registered code rows (2026-10-01).

        Work-pane definition card payload: describe fields, resources, the
        step graph and capped code files. A server without the route answers
        404 — callers surface that honestly, never fabricate a package.
        """
        url = self._wf_url(f"/{workflow_name}/package")
        try:
            return self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to fetch workflow package: {e}")

    def export(
        self,
        workflow_name: str,
        dest: str | Path | None = None,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Download a workflow you can see as a zip (2026-11-07).

        ``GET {workflows}/{name}/export`` answers for every workflow the
        caller may describe — own, ``system`` and shared ones (a project
        share follows the active project, sent as ``X-Project-ID``). The zip
        holds the registered source tree, ``workflow.json`` and
        ``EXPORT.json`` (checksums, excluded paths); container images are not
        included.

        *dest* defaults to ``./<name>.zip``; an existing directory, or a
        *dest* ending in a separator (created, parents included), receives
        ``<dir>/<name>.zip``. An existing file is refused unless *overwrite*.
        The body streams to a temp file beside the target, is verified
        against EXPORT.json and only then renamed into place — a failed
        download or checksum mismatch leaves no file behind (R4Error).
        """
        target = _export_target(workflow_name, dest)
        if target.exists() and not overwrite:
            raise R4Error(f"{target} already exists; pass overwrite=True (CLI: --force)")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".part", dir=target.parent
        )
        partial = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                self._stream_export(workflow_name, handle)
            verify_export(partial, label=str(target))
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return target

    def _stream_export(self, workflow_name: str, handle: BinaryIO) -> None:
        """Stream the export zip into *handle* (gateway auth + project headers)."""
        import httpx

        from ._http import map_httpx_error, raise_for_status, raise_if_web_login

        core = getattr(self._parent, "platform_http", None) or self._parent.http
        headers = core.build_headers()
        headers.pop("Content-Type", None)
        # JSON listed too: an expired credential then gets a JSON 401, not a
        # 302 to the HTML login page saved as the zip.
        headers["Accept"] = "application/zip, application/json"
        url = self._wf_url(f"/{quote(workflow_name, safe='')}/export")
        try:
            with core._client(_EXPORT_TIMEOUT_S) as client:  # noqa: SLF001 - shared HTTP config
                with client.stream("GET", url, headers=headers) as response:
                    if response.is_error:
                        response.read()
                    raise_for_status(response)
                    raise_if_web_login(response)
                    for chunk in response.iter_bytes(chunk_size=8 * 1024 * 1024):
                        handle.write(chunk)
        except httpx.RequestError as exc:
            raise map_httpx_error(exc, gateway_url=self._config.gateway_url) from exc

    def delete(self, workflow_name: str) -> Dict[str, Any]:
        """Delete a registered workflow (owner or admin only).

        Never raises on missing workflows beyond surfacing the server 404 —
        callers that want idempotent deletes should catch R4Error and check
        for 404.
        """
        url = self._wf_url(f"/{workflow_name}")
        try:
            return self._parent.http.delete(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to delete workflow: {e}")

    # ── Run definitions (persisted invocation templates) ──────────────────

    def save_run_definition(
        self,
        name: str,
        workflow_name: str,
        params: Optional[Dict[str, Any]] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """Create/update a named run definition (workflow + params template)."""
        url = self._wf_url("/run-definitions")
        body = {
            "name": name,
            "workflow_name": workflow_name,
            "params": dict(params or {}),
            "description": description,
        }
        try:
            return self._parent.http.request(
                "PUT", url, json_body=body, timeout=self._config.timeout
            )
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to save run definition: {e}")

    def list_run_definitions(self) -> Dict[str, Any]:
        """List run definitions visible to the caller (owner, or all if admin)."""
        url = self._wf_url("/run-definitions")
        try:
            return self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to list run definitions: {e}")

    def get_run_definition(self, name: str) -> Dict[str, Any]:
        """Fetch one run definition by name."""
        url = self._wf_url(f"/run-definitions/{name}")
        try:
            return self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to get run definition: {e}")

    def delete_run_definition(self, name: str) -> Dict[str, Any]:
        """Delete one run definition (owner or admin)."""
        url = self._wf_url(f"/run-definitions/{name}")
        try:
            return self._parent.http.delete(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to delete run definition: {e}")

    def trigger_run_definition(
        self,
        name: str,
        *,
        wait: bool = False,
        timeout: Optional[int] = None,
    ) -> WorkflowResult:
        """Replay a persisted run definition — exact (workflow, params).

        No parameter overrides: the definition is the reproducibility record.
        """
        definition = self.get_run_definition(name).get("definition", {})
        workflow_name = str(definition.get("workflow_name") or "")
        url = self._wf_url(f"/run-definitions/{name}/trigger")
        try:
            data = self._parent.http.post(url, json_body={}, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to trigger run definition: {e}")
        if isinstance(data, dict) and data.get("status") == "error":
            raise R4Error(str(data.get("message", "run definition trigger failed")))
        result = self._workflow_result_from_response(data, workflow_name or name)
        if wait and result.execution_id:
            return self.wait_for_completion(result.execution_id, timeout=timeout)
        return result

    def _trigger_via_zmq(
        self,
        workflow_id: str,
        run_spec: Dict[str, Any],
    ) -> WorkflowResult:
        """Trigger a workflow via ZMQ transport."""
        if not self._parent._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        transport = self._parent.transport
        username = self._parent._get_username()
        data = transport.request_workflow_run(username, run_spec)

        if data.get("status") == "error":
            raise R4Error(data.get("message", "Workflow trigger failed via ZMQ"))

        return self._workflow_result_from_response(data, workflow_id)

    def _trigger_via_http(
        self,
        workflow_id: str,
        run_spec: Dict[str, Any],
        timeout: Optional[int],
    ) -> WorkflowResult:
        """Trigger a workflow via HTTP Gateway."""
        url = self._wf_url(f"/{workflow_id}/trigger")
        params = dict(run_spec["params"])
        input_files = params.pop("input_files", None)
        project_id = params.pop("project_id", None)
        payload: Dict[str, Any] = {"parameters": params}
        if input_files:
            payload["input_files"] = input_files
        if project_id:
            payload["project_id"] = project_id
        try:
            # ADR-105 D-5: UMRS stamps the run's job with this session
            # (spec.origin.session_id), so its terminal event finds the
            # session even after a restart.
            companion_headers = getattr(self._parent, "_companion_headers", None)
            data = self._parent.http.post(
                url,
                json_body=payload,
                timeout=timeout or self._config.timeout,
                headers=companion_headers() if callable(companion_headers) else None,
            )
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Workflow trigger failed: {e}")
        return self._workflow_result_from_response(data, workflow_id)

    def _prepare_upload_archive(
        self,
        workflow_path: str,
        workflow_name: Optional[str],
    ) -> tuple[str, str, bool]:
        path = Path(workflow_path).expanduser().resolve()
        if not path.exists():
            raise R4Error(f"Workflow path not found: {workflow_path}")

        if path.is_dir():
            workflow_json = path / "workflow.json"
            if not workflow_json.is_file():
                raise R4Error(f"No workflow.json found in {path}")
            _check_upload_contract(workflow_json.read_bytes(), str(workflow_path))

            resolved_workflow_name = workflow_name or path.name
            archive_path = _zip_directory(path)
            return archive_path, resolved_workflow_name, True

        if path.is_file() and path.suffix.lower() == ".zip":
            manifest = _zip_manifest(path)
            if manifest is not None:
                _check_upload_contract(manifest, str(workflow_path))
            resolved_workflow_name = workflow_name or path.stem
            return str(path), resolved_workflow_name, False

        raise R4Error(f"Workflow path must be a directory or .zip archive: {workflow_path}")

    def _upload_via_zmq(
        self,
        workflow_name: str,
        archive_path: str,
    ) -> WorkflowUploadResult:
        if not self._parent._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        transport = self._parent.transport
        username = self._parent._get_username()
        data = transport.request_workflow_upload(username, workflow_name, archive_path)

        if data.get("status") == "error":
            raise R4Error(data.get("message", "Workflow upload failed via ZMQ"))

        return self._workflow_upload_result_from_response(data, workflow_name)

    def _upload_via_http(
        self,
        workflow_name: str,
        archive_path: str,
        timeout: Optional[int],
    ) -> WorkflowUploadResult:
        import httpx

        url = self._wf_url("/upload")
        headers = self._parent._build_headers()
        headers.pop("Content-Type", None)

        try:
            req_timeout = timeout or self._config.timeout
            verify = self._config.verify_ssl
            with open(archive_path, "rb") as archive:
                with httpx.Client(timeout=req_timeout, verify=verify) as client:
                    response = client.post(
                        url,
                        data={"workflow_name": workflow_name},
                        files={"file": (f"{workflow_name}.zip", archive, "application/zip")},
                        headers=headers,
                    )
                    self._parent._handle_response_errors(response)
                    data = response.json()
                    return self._workflow_upload_result_from_response(data, workflow_name)
        except httpx.RequestError as e:
            raise R4Error(f"Workflow upload failed: {e}")

    def _workflow_definition_from_item(self, item: Dict[str, Any]) -> WorkflowDefinition:
        workflow_name = (
            item.get("name") or item.get("workflow_name") or item.get("workflow_id") or ""
        )
        entry = WorkflowDefinition(
            workflow_name=workflow_name,
            name=workflow_name,
            description=item.get("description", ""),
            parameters=item.get("parameters", []),
            **_catalog_ownership(item),
        )
        # The display tag is the ownership label: a row another user shared
        # with the caller reads "shared · <owner>", never the owner's "user".
        entry.tags = [entry.scope_label] if entry.scope_label else []
        return entry

    def _workflow_result_from_response(
        self,
        data: Dict[str, Any],
        workflow_id: str,
    ) -> WorkflowResult:
        workflow_name = data.get("workflow_name") or data.get("workflow_id") or workflow_id
        outputs = {}
        for key in ("source_path", "steps", "params", "message"):
            if key in data:
                outputs[key] = data[key]

        return WorkflowResult(
            execution_id=data.get("execution_id", ""),
            workflow_name=workflow_name,
            status=data.get("status", "accepted"),
            outputs=outputs,
            artifacts=data.get("artifacts", []),
            metrics=data.get("metrics"),
            duration_seconds=data.get("duration_seconds"),
            error=data.get("error"),
            durable_job_id=data.get("durable_job_id"),
        )

    def _workflow_upload_result_from_response(
        self,
        data: Dict[str, Any],
        workflow_name: str,
    ) -> WorkflowUploadResult:
        resolved_name = data.get("workflow_name") or data.get("workflow_id") or workflow_name
        return WorkflowUploadResult(
            workflow_name=resolved_name,
            status=data.get("status", "accepted"),
            accepted=bool(data.get("accepted", data.get("status") == "ok")),
            source_path=data.get("source_path"),
            message=data.get("message"),
            error=data.get("error"),
        )

    def _execution_url(self, execution_id: str, suffix: str = "") -> str:
        return self._wf_url(f"/executions/{execution_id}") + (f"/{suffix}" if suffix else "")

    def trigger_ct_analysis(
        self,
        input_files: List[str],
        *,
        model: str,
        project_id: Optional[str] = None,
        wait: bool = False,
    ) -> WorkflowResult:
        """
        Trigger the CT analysis workflow.

        Args:
            input_files: List of CT scan file IDs or S3 keys.
            model: Analysis model to use (required; e.g. an hmae or sam3 adapter).
            project_id: Project context.
            wait: Wait for completion.

        Returns:
            WorkflowResult with execution details.
        """
        return self.trigger(
            "ct-analysis",
            input_files=input_files,
            parameters={"model": model},
            project_id=project_id,
            wait=wait,
        )

    def trigger_video_analysis(
        self,
        input_files: List[str],
        *,
        analysis_type: str,
        project_id: Optional[str] = None,
        wait: bool = False,
    ) -> WorkflowResult:
        """
        Trigger the video analysis workflow.

        Args:
            input_files: List of video file IDs or S3 keys.
            analysis_type: Type of analysis (required; e.g. event-detection,
                behavior-tracking).
            project_id: Project context.
            wait: Wait for completion.

        Returns:
            WorkflowResult with execution details.
        """
        return self.trigger(
            "video-analysis",
            input_files=input_files,
            parameters={"analysis_type": analysis_type},
            project_id=project_id,
            wait=wait,
        )

    def status(
        self,
        execution_id: str,
    ) -> WorkflowStatus:
        """Get the current status of a workflow execution."""
        url = self._execution_url(execution_id)
        try:
            data = self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to fetch execution status: {e}")
        return self._workflow_status_from_response(data, execution_id)

    def get_execution_detail(self, execution_id: str) -> Dict[str, Any]:
        """Raw execution record (unmangled dict).

        Exposes ``results.steps`` step-level statuses for live rendering
        (``r4 workflow watch``) without changing the mapped
        :class:`WorkflowStatus` contract.
        """
        url = self._execution_url(execution_id)
        try:
            data = self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to fetch execution detail: {e}")
        return data.get("execution", data) or {}

    def get_results(
        self,
        execution_id: str,
    ) -> WorkflowResult:
        """Get the structured results of an execution (partial or final)."""
        url = self._execution_url(execution_id, "results")
        try:
            data = self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to fetch execution results: {e}")
        return self._workflow_result_from_results_response(data, execution_id)

    def cancel(
        self,
        execution_id: str,
    ) -> WorkflowStatus:
        """Request cancellation of a running execution."""
        url = self._execution_url(execution_id, "cancel")
        try:
            data = self._parent.http.post(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to cancel execution: {e}")
        return self._workflow_status_from_response(data, execution_id)

    def approve_gate(
        self,
        execution_id: str,
        step_name: str,
        *,
        approved: bool = True,
        approver: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> WorkflowStatus:
        """Approve or reject a waiting manual-gate step (W5)."""
        url = self._execution_url(execution_id, f"steps/{step_name}/approve")
        payload: Dict[str, Any] = {"approved": approved}
        if approver:
            payload["approver"] = approver
        if comment:
            payload["comment"] = comment
        try:
            data = self._parent.http.post(url, json_body=payload, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to submit gate decision: {e}")
        return self._workflow_status_from_response(data, execution_id)

    def list_workflow_triggers(self, workflow_name: str) -> Dict[str, Any]:
        """W4: declared event trigger subscriptions for a workflow."""
        url = self._wf_url(f"/{workflow_name}/triggers")
        try:
            data = self._parent.http.get(url, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to fetch workflow triggers: {e}")
        return data

    def list_workflows(
        self,
        *,
        tags: Optional[List[str]] = None,
        all_users: bool = False,
    ) -> List[WorkflowDefinition]:
        """
        List available workflows.

        Args:
            tags: Optional filter by tags.
            all_users: Admin-only: list the full catalog across owners
                (both transports honour it; the server strips the claim
                for non-admins). Default lists own + shared + system.

        Returns:
            List of WorkflowDefinition objects.
        """
        filters = {}
        if tags:
            filters["tags"] = tags
        if all_users:
            filters["all_users"] = True

        return self._router.execute(
            "workflow_list",
            http_fn=lambda: self._list_workflows_via_http(tags, all_users),
            zmq_fn=lambda: self._list_workflows_via_zmq(filters),
        )

    def _list_workflows_via_zmq(self, filters: Dict[str, Any]) -> List[WorkflowDefinition]:
        """List workflows via ZMQ transport."""
        if not self._parent._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        transport = self._parent.transport
        username = self._parent._get_username()
        data = transport.request_workflow_list(username, filters)

        if data.get("status") == "error":
            raise R4Error(data.get("message", "Workflow list failed via ZMQ"))

        workflows = []
        for item in data.get("workflows", []):
            workflows.append(self._workflow_definition_from_item(item))
        return workflows

    def _list_workflows_via_http(
        self, tags: Optional[List[str]], all_users: bool = False
    ) -> List[WorkflowDefinition]:
        """List workflows via HTTP Gateway."""
        url = self._wf_url()
        params = {}
        if tags:
            params["tags"] = ",".join(tags)
        if all_users:
            params["all_users"] = "true"
        try:
            data = self._parent.http.get(url, params=params, timeout=self._config.timeout)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to list workflows: {e}")
        workflows = []
        for item in data.get("workflows", []):
            workflows.append(self._workflow_definition_from_item(item))
        return workflows

    def wait_for_completion(
        self,
        execution_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: Optional[int] = None,
        progress_cb: Optional[Any] = None,
    ) -> WorkflowResult:
        """Poll execution status until terminal, then return final results."""
        import time

        deadline = None if timeout is None else time.time() + timeout
        terminal = {"succeeded", "failed", "cancelled", "completed"}
        last_status: Optional[str] = None
        while True:
            current = self.status(execution_id)
            if progress_cb is not None and current.status != last_status:
                try:
                    progress_cb(current)
                except Exception:
                    pass
                last_status = current.status
            if current.status in terminal:
                return self.get_results(execution_id)
            if deadline is not None and time.time() >= deadline:
                raise R4Error(
                    f"Timed out waiting for execution {execution_id} (last status={current.status})"
                )
            time.sleep(poll_interval)

    def _workflow_status_from_response(
        self,
        data: Dict[str, Any],
        execution_id: str,
    ) -> WorkflowStatus:
        execution = data.get("execution", data) or {}
        return WorkflowStatus(
            execution_id=execution.get("execution_id", execution_id),
            workflow_name=execution.get("workflow_name", ""),
            status=execution.get("status", "unknown"),
            started_at=str(execution.get("started_at")) if execution.get("started_at") else None,
            finished_at=str(execution.get("finished_at")) if execution.get("finished_at") else None,
            error=execution.get("error"),
            progress=float(execution.get("progress", 0.0) or 0.0),
        )

    def lint_file(
        self,
        filepath: str,
    ) -> list[dict]:
        """Server-side lint of a workflow draft JSON file — retired.

        The route was never mounted server-side (no UMRS router serves it;
        verified 2026-10-20, plan H-10.2 — UMRS's only lint surface is the
        K2 knowledge-bundle linter ``/umrs/k2/lint``). The method remains to
        fail with a clear error instead of a 404 trail; ``r4 workflow lint``
        keeps its client-side diagnostics and prints its server-unavailable
        warning as before.

        Raises:
            R4Error: Always — the server lint endpoint does not exist.
        """
        raise self._raise_retired_http(f"{self._wf_url('/lint')}")

    _RETIRED_LINT_HTTP = (
        "server-side workflow lint is not available: the lint route was "
        "never mounted in UMRS (retired 2026-10-20, plan H-10.2). "
        "Client-side lint runs with `r4 workflow lint <file>` (no --server)."
    )

    def _raise_retired_http(self, url: str) -> "R4Error":
        return R4Error(f"{self._RETIRED_LINT_HTTP} (off: {url})")

    def _workflow_result_from_results_response(
        self,
        data: Dict[str, Any],
        execution_id: str,
    ) -> WorkflowResult:
        results = data.get("results") or {}
        artifacts = [a.get("key", "") for a in (data.get("artifacts") or []) if a.get("key")]
        return WorkflowResult(
            execution_id=data.get("execution_id", execution_id),
            workflow_name="",
            status=data.get("execution_status") or "unknown",
            outputs=results,
            artifacts=artifacts,
            error=data.get("error"),
            durable_job_id=data.get("durable_job_id"),
        )
