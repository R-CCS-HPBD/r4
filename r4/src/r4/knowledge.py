"""
R4 Knowledge Client — Phase 13 UI Integration.

Provides R4 SDK methods for the UMRS knowledge modules (Phases 3-12):
- Ingest artifacts (Phase 3)
- Extract knowledge (Phase 4)
- Link knowledge (Phase 5)
- Verify knowledge (Phase 6)
- Coverage dashboard (Phase 7)
- External knowledge (Phase 8)
- Scientific memory (Phase 9)
- Readiness gate (Phase 10)
- Research planner (Phase 11)
- Autonomous research loop (Phase 12)

All methods proxy through the Gateway's ``/gw/knowledge/{path}`` route,
which forwards to UMRS. The R4 client remains a thin wrapper — no
duplicate state machines, registries, or retry semantics.

Usage::

    from r4 import Client

    client = Client()
    result = client.knowledge.ingest(
        project_id="my-project",
        files=["paper1.pdf", "notebook1.ipynb"],
    )
    verification = client.knowledge.verify(
        project_id="my-project",
        question="What supports hypothesis H7?",
    )
    readiness = client.knowledge.readiness(project_id="my-project")
    plan = client.knowledge.plan(
        project_id="my-project",
        research_goal="Optimize MOF-5 for CO2 capture",
    )
    loop = client.knowledge.start_loop(
        project_id="my-project",
        research_goal="Optimize MOF-5 for CO2 capture",
        max_iterations=10,
    )
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional

from .errors import R4Error

if TYPE_CHECKING:
    from .client import Client

logger = logging.getLogger(__name__)

AGENT_ACTOR = "agent"

#: Set (to ``1``) in the environment of every Companion tool subprocess. An
#: ``r4`` CLI started there is the agent's hands, not the person's: its
#: knowledge calls go out marked ``x-r4-actor: agent``, and the review
#: decisions it would send as the person's act go out marked too, so UMRS
#: refuses them (review of K1 part A, 2026-10-24: ``r4 knowledge review
#: establish`` through ``shell_exec`` read as a human). Client-honest (K-12).
COMPANION_SHELL_ENV = "R4_COMPANION_SESSION"

#: The KnowledgeClients (by id) inside an agent mutation in THIS context
#: (thread / task). Per context, so two threads sharing one client — a team
#: run beside the lead agent, a bridge RPC beside a chat turn — never see each
#: other's flag: the shared save/restore attribute let one thread's exit send
#: the other's next request out as the person (review of K1 part A).
_AGENT_SCOPED: ContextVar[FrozenSet[int]] = ContextVar(
    "r4_knowledge_agent_scoped", default=frozenset()
)


def companion_shell() -> bool:
    """True inside a Companion tool subprocess (:data:`COMPANION_SHELL_ENV`)."""
    return os.environ.get(COMPANION_SHELL_ENV, "").strip() not in ("", "0")


# Small UTF-8 files ride the spec as inline_text (matches the server's 2 MiB
# inline cap); everything else is uploaded as bytes. Workstation paths are
# never sent: KNOWLEDGE_INGEST runs inside UMRS, where they do not exist (the
# HTTP ingest route refuses server-path kinds by name).
_INLINE_FILE_MAX_BYTES = 2 * 1024 * 1024
_SKIP_DIR_NAMES = frozenset({"__pycache__", "node_modules"})


def _inline_file_spec(path: str) -> Optional[Dict[str, str]]:
    """Small UTF-8 file → ``inline_text`` spec; None when it must be uploaded."""
    import os

    try:
        with open(path, "rb") as handle:
            raw = handle.read(_INLINE_FILE_MAX_BYTES + 1)
        if len(raw) > _INLINE_FILE_MAX_BYTES:
            return None
        return {
            "kind": "inline_text",
            "uri": os.path.basename(path),
            "content": raw.decode("utf-8"),
        }
    except (OSError, UnicodeDecodeError):
        return None


def _expand_paths(paths: List[str]) -> List[str]:
    """Files named directly plus every file under named directories (sorted;
    dot-dirs and cache dirs skipped)."""
    import os

    files: List[str] = []
    for path in paths:
        if not os.path.isdir(path):
            files.append(path)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIR_NAMES
            )
            files.extend(os.path.join(dirpath, f) for f in sorted(filenames))
    return files


def _plan_path_ingest(
    paths: List[str], reader: Any = None
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Split workstation paths into inline specs and files to upload as bytes.

    With a :class:`r4.git_provenance.ProvenanceReader`, each inline spec
    carries its file's git provenance (ADR-108 D-6)."""
    inline: List[Dict[str, Any]] = []
    uploads: List[str] = []
    for path in _expand_paths(paths):
        spec: Optional[Dict[str, Any]] = _inline_file_spec(path)
        if spec is None:
            uploads.append(path)
            continue
        provenance = reader.of(path) if reader is not None else None
        if provenance:
            spec["provenance"] = provenance
        inline.append(spec)
    return inline, uploads


def _bundle_archive_bytes(bundle: str) -> bytes:
    """A bundle as ``.kb.tar.gz`` bytes: archives pass through, directories
    (``manifest.json`` inside) are packed under ``bundle/``."""
    import io
    import os
    import tarfile

    if not os.path.isdir(bundle):
        with open(bundle, "rb") as handle:
            return handle.read()
    if not os.path.isfile(os.path.join(bundle, "manifest.json")):
        raise ValueError(f"not a bundle directory (no manifest.json): {bundle}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(bundle, arcname="bundle")
    return buf.getvalue()


@dataclass(frozen=True)
class ArtifactDeleteResult:
    """What ``DELETE /knowledge/artifacts/{id}`` deleted (ADR-093 MD-16).

    ``counts`` holds the rows deleted per kind (``artifacts``, ``nodes``,
    ``edges``, ``embeddings``, ``ir``, ``findings``) and ``events_detached``
    (kept, unlinked). ``objects`` has one outcome per object-store object:
    ``deleted``, ``kept_shared``, ``kept``, ``failed`` or ``not_attempted``.
    ``document_id`` is set when the delete came through the rag facade.
    """

    artifact_id: str
    project_id: str
    deleted: bool
    counts: Dict[str, int] = field(default_factory=dict)
    objects: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    document_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactDeleteResult":
        return cls(
            artifact_id=str(data.get("artifact_id", "")),
            project_id=str(data.get("project_id", "")),
            deleted=bool(data.get("deleted", False)),
            counts={str(k): int(v) for k, v in (data.get("counts") or {}).items()},
            objects=[dict(o) for o in data.get("objects") or []],
            notes=[str(n) for n in data.get("notes") or []],
            document_id=data.get("document_id"),
        )

    @property
    def failed_objects(self) -> List[Dict[str, Any]]:
        """Objects whose delete failed (the rows are deleted regardless)."""
        return [o for o in self.objects if o.get("outcome") == "failed"]


class KnowledgeClient:
    """
    Knowledge client for the R4 SDK.

    Access via ``Client.knowledge``::

        client = Client()
        result = client.knowledge.ingest(...)

    All methods proxy through the Gateway to UMRS knowledge modules.
    """

    def __init__(self, client: "Client"):
        self._client = client
        # Actor context for mutation authorization (0069). Companion sessions
        # mark agent-originated mutations (``agent_scope``) and set the
        # session_id, so the server-side guard can tell them from human ones.
        self._actor: Optional[str] = None
        self.actor = None
        self.session_id: Optional[str] = None
        # Session-level graph constraints (set by the Studio selector or ACP):
        # {"include": [...], "exclude": [...], "discovery": bool}. Explicit
        # per-query constraints always win over these session defaults.
        self.graph_constraints: Dict[str, Any] = {}
        # Session-level acquisition mode (WRITE side; see
        # r4/companion/knowledge_write_gate.py): {"mode": open|pinned|disabled,
        # "graphs": [names/ids]}. Mirrors session.metadata; enforced by the
        # knowledge_ingest / knowledge_graph_propose tool guards.
        self.write_constraints: Dict[str, Any] = {"mode": "open", "graphs": []}

    @property
    def actor(self) -> Optional[str]:
        """``agent`` inside :meth:`agent_scope` (this thread/task only) or in
        a Companion tool subprocess; else the actor set on the client."""
        if companion_shell() or id(self) in _AGENT_SCOPED.get():
            return AGENT_ACTOR
        return self._actor

    @actor.setter
    def actor(self, value: Optional[str]) -> None:
        self._actor = value

    @contextmanager
    def agent_scope(self) -> Iterator[None]:
        """Mark this client's requests as the agent's for the ``with`` body,
        in the current thread/task only (a context variable, not shared
        state), and unmark them when it returns or raises."""
        token = _AGENT_SCOPED.set(_AGENT_SCOPED.get() | {id(self)})
        try:
            yield
        finally:
            _AGENT_SCOPED.reset(token)

    def _knowledge_url(self, path: str) -> str:
        """Build URL for knowledge endpoint through Gateway."""
        return f"{self._client._api_prefix}/knowledge/{path}"

    # ── Retired surfaces (2026-10-20, plan H-10.2) ────────────────────────
    # The standalone KnowledgeClient Phases 4–13 (extract / link /
    # verify-status / coverage / memory / planner / correct) were never
    # mounted in UMRS — every call failed in a 404 trail in both client
    # modes. They remain, loudly retired (the r4/data.py retirement shape,
    # drift remediation 2026-08-22), each naming its live lane.

    _RETIRED_PHASE_HTTP = (
        "No server route for {path} — this KnowledgeClient phase was never "
        "mounted in UMRS (retired 2026-10-20, plan H-10.2)."
    )

    def _raise_retired_http(self, path: str, *, hint: str = "") -> "R4Error":
        return R4Error(f"{self._RETIRED_PHASE_HTTP.format(path=path)} {hint}".strip())

    def _k2_url(self, path: str) -> str:
        """Build URL for the K2 ingest lane in the client's mode.

        Behind the edge it is ``/gw/knowledge/ingest/*``, which nginx
        rewrites to ``/umrs/k2/*``; UMRS itself mounts only ``/umrs/k2/*``,
        so direct-UMRS mode calls that (2026-10-18, cleanup C-2(v)).
        """
        prefix = self._client._api_prefix
        lane = "k2" if prefix == "/umrs" else "knowledge/ingest"
        return f"{prefix}/{lane}/{path}"

    def _actor_headers(self) -> Dict[str, str]:
        """Actor/session headers sent on mutation calls."""
        headers: Dict[str, str] = {}
        if self.actor:
            headers["x-r4-actor"] = self.actor
        if self.session_id:
            headers["x-r4-session-id"] = self.session_id
        return headers

    # ── K4: bundle restore ─────────────────────────────────────────────

    def restore(
        self,
        bundle_dir: str,
        *,
        project_id: Optional[str] = None,
        verify: bool = True,
        allow_unsigned: bool = False,
    ) -> Dict[str, Any]:
        """Restore a .kb bundle from a server-side path (K4).

        Args:
            bundle_dir: Server-side bundle directory (contains manifest.json).
            project_id: Optional retarget project (all rows re-scoped).
            verify: Verify manifest hash + HMAC before restoring.
            allow_unsigned: Restore an unsigned/unverifiable bundle anyway
                (explicit opt-out; server refuses unsigned bundles without it).

        Returns:
            Restore payload with per-table counts and integrity outcome.
        """
        payload: Dict[str, Any] = {
            "bundle_dir": bundle_dir,
            "verify": verify,
            "allow_unsigned": allow_unsigned,
        }
        if project_id:
            payload["project_id"] = project_id
        return self._client.post(
            self._knowledge_url("restore"),
            json=payload,
            headers=self._actor_headers() or None,
        )

    # ── K3: Unified knowledge query facade ─────────────────────────────

    def query(
        self,
        query: str,
        *,
        project_id: str,
        scope: str = "all",
        graphs: Optional[List[str]] = None,
        exclude_graphs: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Unified search across UMRS knowledge backends (K3 facade).

        ``graphs`` / ``exclude_graphs`` select *which knowledge graphs*
        (ids or names) — distinct from ``scope`` which selects a backend
        type. Graph hits carry provenance (metadata.graph_id/graph_name).
        Returns the facade payload with results plus
        ``wired_backends`` / ``unavailable_backends`` coverage truth.
        """
        params: Dict[str, Any] = {
            "q": query,
            "project_id": project_id,
            "scope": scope,
            "limit": limit,
        }
        if graphs:
            params["graphs"] = ",".join(graphs)
        if exclude_graphs:
            params["exclude_graphs"] = ",".join(exclude_graphs)
        return self._client.get(
            self._knowledge_url("query"),
            params=params,
        )

    # ── Multi-graph: registry (0069) ────────────────────────────────────

    def graphs(
        self,
        *,
        project_id: Optional[str] = None,
        status: Optional[str] = "active",
        query: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List registered knowledge graphs (0069 registry).

        With ``project_id`` the server's deterministic default graph for
        that project is materialized automatically (legacy projects appear
        without manual migration).
        """
        params: Dict[str, Any] = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status
        if query:
            params["q"] = query
        payload = self._client.get(
            self._knowledge_url("graphs"),
            params=params,
        )
        return payload.get("graphs", [])

    def graph_info(self, graph_id: str) -> Dict[str, Any]:
        """Fetch one graph by id (or exact name)."""
        return self._client.get(self._knowledge_url(f"graphs/{graph_id}"))

    def graph_content(
        self,
        graph_id: str,
        *,
        node_limit: int = 200,
        edge_limit: int = 400,
    ) -> Dict[str, Any]:
        """Bounded nodes+edges of one registered graph (webview constellation).

        Read-only visualization read; 404 surfaces when the graph is unknown.
        """
        return self._client.get(
            self._knowledge_url(f"graphs/{graph_id}/content"),
            params={"node_limit": node_limit, "edge_limit": edge_limit},
        )

    def graph_create(
        self,
        name: str,
        *,
        description: str = "",
        project_id: Optional[str] = None,
        kind: str = "research",
        ontology_id: Optional[str] = None,
        ontology_version: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a named, independent knowledge graph."""
        return self._client.post(
            self._knowledge_url("graphs"),
            json={
                "name": name,
                "description": description,
                "project_id": project_id,
                "kind": kind,
                "ontology_id": ontology_id,
                "ontology_version": ontology_version,
                "provenance": provenance or {},
            },
            headers=self._actor_headers() or None,
        )

    def graph_update(
        self,
        graph_id: str,
        *,
        description: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update graph metadata or lifecycle (active | archived)."""
        body: Dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if kind is not None:
            body["kind"] = kind
        if status is not None:
            body["status"] = status
        return self._client.patch(
            self._knowledge_url(f"graphs/{graph_id}"),
            json=body,
            headers=self._actor_headers() or None,
        )

    def graph_archive(self, graph_id: str) -> Dict[str, Any]:
        """Archive a graph (soft delete — archive is the only lifecycle out)."""
        return self.graph_update(graph_id, status="archived")

    # ── Multi-graph: extension proposals (0069) ─────────────────────────

    def graph_propose(
        self,
        graph_id: str,
        *,
        reason: str,
        missing_information: Optional[List[Any]] = None,
        source_artifacts: Optional[List[Any]] = None,
        proposed_operations: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Stage a graph-extension proposal (pending — no mutation yet)."""
        return self._client.post(
            self._knowledge_url(f"graphs/{graph_id}/proposals"),
            json={
                "reason": reason,
                "missing_information": missing_information or [],
                "source_artifacts": source_artifacts or [],
                "proposed_operations": proposed_operations or [],
                "session_id": self.session_id or "",
            },
            headers=self._actor_headers() or None,
        )

    def graph_proposals(
        self,
        graph_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List proposals for a graph."""
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        payload = self._client.get(
            self._knowledge_url(f"graphs/{graph_id}/proposals"),
            params=params,
        )
        return payload.get("proposals", [])

    def graph_proposal_info(self, proposal_id: str) -> Dict[str, Any]:
        """Fetch one proposal by id."""
        return self._client.get(self._knowledge_url(f"proposals/{proposal_id}"))

    def graph_decide(self, proposal_id: str, *, approved: bool) -> Dict[str, Any]:
        """Record the user's approval decision for a proposal.

        This is the human authorization step for agent-initiated graph
        extension — the server records the verified caller as decider.

        Always sent as the person's act, never with the agent actor: the
        server refuses agent decisions (ADR-084 D-3), and this call happens
        only from the CLI or behind the Companion's never-auto-approved
        prompt. The actor stays sticky after an agent ingest, which made
        every later decision in that session a 403 (2026-10-09).
        """
        return self._client.post(
            self._knowledge_url(f"proposals/{proposal_id}/decision"),
            json={"approved": approved},
            headers=self._human_headers() or None,
        )

    def _human_headers(self) -> Dict[str, str]:
        """Session headers without the agent actor: a person's act. In a
        Companion tool subprocess the actor stays, so UMRS refuses an agent
        that runs the review CLI through its shell."""
        if companion_shell():
            return self._actor_headers()
        return {k: v for k, v in self._actor_headers().items() if k != "x-r4-actor"}

    # ── Two-tier review (D-35; knowledge plan K1.5) ─────────────────────

    def graph_proposed(
        self, graph_id: str, *, limit: int = 200, run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Proposed nodes and edges of one graph, awaiting a person (K1.5).

        Each row carries ``kind`` (node|edge), ``id``, ``summary``,
        ``origin`` and ``run_id`` (ADR-084 D-2 "establish run"); ``run_id``
        narrows the listing to one run, past the newest-``limit`` window.
        """
        params: Dict[str, Any] = {"limit": limit}
        if run_id:
            params["run_id"] = run_id
        return self._client.get(
            self._knowledge_url(f"graphs/{graph_id}/proposed"),
            params=params,
        )

    def establish(
        self,
        graph_id: str,
        row_id: str,
        *,
        kind: str = "node",
        evidence_refs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Promote one proposed node or edge to established (D-35).

        Human-only on the server (agent actor refused), so it is always sent
        as the person's act, like :meth:`graph_decide`.
        """
        if kind not in ("node", "edge"):
            raise ValueError(f"kind must be node or edge, got {kind!r}")
        return self._client.post(
            self._knowledge_url(f"graphs/{graph_id}/{kind}s/{row_id}/establish"),
            json={"evidence_refs": list(evidence_refs or [])},
            headers=self._human_headers() or None,
        )

    # ── Phase 3: Unified Ingestion ──────────────────────────────────────

    def ingest(
        self,
        project_id: str,
        files: List[str],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Ingest workstation files (papers, notebooks, code, datasets) durably.

        Compat entry point over :meth:`ingest_paths` (the historic
        ``POST /knowledge/ingest {"files": [...]}`` shape has no server route
        and sent paths the server cannot see). ``proposal_id`` / ``graph_id``
        are honoured from ``kwargs``.
        """
        return self.ingest_paths(
            project_id,
            files,
            proposal_id=kwargs.get("proposal_id"),
            graph_id=kwargs.get("graph_id"),
        )

    def ingest_status(self, project_id: str) -> Dict[str, Any]:
        """Get ingestion status for a project.

        Serves from /knowledge/status — the historic ``ingest/status`` path
        has no downstream route (client-side 404 since the k2 lane).
        """
        return self._client.get(
            self._knowledge_url("status"),
            params={"project_id": project_id},
        )

    def ingest_sources(
        self,
        project_id: str,
        sources: List[Dict[str, Any]],
        *,
        allow_domains: Optional[List[str]] = None,
        proposal_id: Optional[str] = None,
        graph_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queue a durable KNOWLEDGE_INGEST job (k2 layer, ADR-015).

        Args:
            project_id: Project identifier.
            sources: Source specs — ``{"kind": kind, "uri": uri}`` where kind is
                ``inline_text`` (with ``content``), ``http_url`` or ``git_repo``;
                the server refuses server-path kinds (use :meth:`ingest_paths`
                for workstation files). Remote kinds execute only inside the
                egress-enabled job.
            allow_domains: Domain allowlist for remote sources for this run:
                it only narrows the site allowlist
                (``extra.knowledge_ingest.allowed_domains``; see
                :meth:`ingest_allowlist`), which checks every redirect hop.
            proposal_id: Approved graph-extension proposal id. Required when
                this client's actor is ``agent`` (server-side guard, 0069);
                human-initiated calls omit it.
            graph_id: Named target graph (id or name). Absent ⇒ the project's
                default graph. Server resolves and rejects unknown/archived
                graphs with 400.

        Returns:
            ``{"job_id": ..., "job_type": "knowledge_ingest", "status": "queued"}``
            — track progress through the durable job APIs / ingest_status.
        """
        payload: Dict[str, Any] = {
            "project_id": project_id,
            "sources": sources,
        }
        if allow_domains:
            payload["allow_domains"] = allow_domains
        if proposal_id:
            payload["proposal_id"] = proposal_id
        if graph_id:
            payload["graph_id"] = graph_id
        return self._client.post(
            # Stable lane (S4, 2026-08-23): the retired codename lane is
            # 308-redirected here for one release; clients use this path.
            self._k2_url("ingest"),
            json=payload,
            headers=self._actor_headers() or None,
        )

    def save_session_turns(
        self,
        project_id: str,
        *,
        session_id: str,
        turns: List[Dict[str, str]],
        title: str = "",
        tags: Optional[List[str]] = None,
        vouch: bool = False,
        graph_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Keep chat turns verbatim in project knowledge (K1.6 ``/save``).

        ``turns`` are ``{"turn_id", "role", "text"}`` rows read from the local
        session store, never re-typed by a model. UMRS scans the text (a
        token-shaped string refuses the save by name), records the session
        back-link and ``attested_by_client``, and tiers it (K-1): your own
        text lands established; an assistant answer lands proposed unless
        ``vouch`` (you vouch for it: established, you are its establisher).
        Returns the ingest job receipt.
        """
        source: Dict[str, Any] = {
            "kind": "session_turns",
            "session_id": session_id,
            "turns": turns,
            "title": title,
            "tags": list(tags or []),
            "vouch": bool(vouch),
        }
        return self.ingest_sources(project_id, [source], graph_id=graph_id)

    def upload_file(
        self,
        project_id: str,
        path: str,
        *,
        graph_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Upload one workstation file's bytes → a durable KNOWLEDGE_INGEST job.

        ``POST /knowledge/upload`` stages the bytes under the project's
        prefix and submits the ingest server-side. Returns the job receipt.
        """
        import mimetypes
        import os

        with open(path, "rb") as handle:
            content = handle.read()
        headers = self._actor_headers()
        if proposal_id:
            headers["x-proposal-id"] = proposal_id
        params: Dict[str, Any] = {"project_id": project_id}
        if graph_id:
            params["graph_id"] = graph_id
        return self._client.post_multipart(
            f"{self._client._api_prefix}/knowledge/upload",
            filename=os.path.basename(path),
            content=content,
            content_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
            # ADR-108 D-6: where the file came from in git (a form field).
            data={"provenance": json.dumps(provenance)} if provenance else None,
            params=params,
            headers=headers,
        )

    # ── MD-16: artifact delete ──────────────────────────────────────────

    def delete_artifact(
        self, artifact_id: str, *, project_id: Optional[str] = None
    ) -> ArtifactDeleteResult:
        """Delete one knowledge artifact and everything derived from it.

        ``DELETE /knowledge/artifacts/{artifact_id}`` (ADR-093 MD-16): the
        server deletes the artifact row, its nodes, vectors, edges, IR rows
        and findings in one transaction, keeps its events (unlinked), then
        deletes its object-store bytes best-effort. ``project_id`` defaults
        to the caller's verified project; another project needs write scope
        there.

        A user verb: an agent-context client (``actor="agent"``) is refused
        by the server. Raises :class:`r4.errors.APIError` (404) when the
        project holds no artifact of that id, also when it was already
        deleted. One request, no retry.
        """
        from urllib.parse import quote

        params = {"project_id": project_id} if project_id else None
        data = self._client.delete(
            self._knowledge_url(f"artifacts/{quote(artifact_id, safe='')}"),
            params=params,
            headers=self._actor_headers() or None,
        )
        return ArtifactDeleteResult.from_dict(data or {})

    def ingest_allowlist(
        self, *, check: Optional[str] = None, project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Which web sites knowledge ingest may fetch on this site.

        ``GET /k2/ingest/allowlist``: ``{"allow_all", "entries", "configured",
        "how_to_change", "site"}``, plus ``"check"`` (``allowed``, ``reason``)
        for *check*, a URL or host (names only; the address, size and content
        checks run when a fetch happens).
        """
        return self._client.get(
            self._k2_url("ingest/allowlist"),
            params={"check": check} if check else None,
            headers={"X-Project-ID": project_id} if project_id else None,
        )

    # ── K1.3: one listing truth ─────────────────────────────────────────

    def list_artifacts(
        self,
        project_id: Optional[str] = None,
        *,
        created_by: Optional[str] = None,
        graph_id: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> Dict[str, Any]:
        """The project's knowledge artifacts the caller may read, newest first.

        ``GET /k2/artifacts`` (knowledge plan K1.2/K1.3): each artifact lists
        its readable contributions (who added it, to which graph, as which
        actor); another member's personal-graph file is never listed. The
        filters are the server's: ``created_by`` (a user id), ``graph_id``,
        ``kind``, ``status`` (default ``active``) and ``since`` (ISO 8601).
        The project rides ``X-Project-ID`` (the route scopes by the verified
        project). Returns ``{artifacts, total, page, page_size}``.
        """
        project = project_id or self._client._config.project_id
        if not project:
            raise ValueError("project_id required (Client config has no active project)")
        filters = {
            "created_by": created_by,
            "graph_id": graph_id,
            "kind": kind,
            "status": status,
            "since": since,
        }
        params: Dict[str, Any] = {k: v for k, v in filters.items() if v}
        params.update(page=page, page_size=page_size)
        return self._client.get(
            self._k2_url("artifacts"),
            params=params,
            headers={"X-Project-ID": project},
        )

    def ingest_paths(
        self,
        project_id: str,
        paths: Optional[List[str]] = None,
        *,
        urls: Optional[List[str]] = None,
        proposal_id: Optional[str] = None,
        graph_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest workstation files/directories and URLs durably.

        Small UTF-8 files and URLs go in ONE ingest job (inline_text +
        http_url specs); every other file is uploaded as bytes (one job per
        file). Returns ``{"job_id": <first job>, "jobs": [...], "inlined": n,
        "uploaded": n}``. Raises ValueError when there is nothing to ingest.
        """
        from .git_provenance import ProvenanceReader

        reader = ProvenanceReader()
        inline, uploads = _plan_path_ingest(list(paths or []), reader)
        specs: List[Dict[str, Any]] = [*inline]
        specs.extend({"kind": "http_url", "uri": url} for url in urls or [])
        jobs: List[Dict[str, Any]] = []
        if specs:
            jobs.append(
                # No allow_domains (2026-10-25): the URLs' own hosts narrowed
                # the site allowlist to them, so a redirect (doi.org to the
                # publisher, github.com to githubusercontent.com) was refused.
                # The site allowlist checks every hop on the server.
                self.ingest_sources(
                    project_id,
                    specs,
                    proposal_id=proposal_id,
                    graph_id=graph_id,
                )
            )
        for path in uploads:
            receipt = self.upload_file(
                project_id,
                path,
                graph_id=graph_id,
                proposal_id=proposal_id,
                provenance=reader.of(path),
            )
            jobs.append({**receipt, "path": path})
        if not jobs:
            raise ValueError("nothing to ingest — pass files and/or urls")
        return {
            "job_id": jobs[0].get("job_id"),
            "status": "queued",
            "jobs": jobs,
            "inlined": len(inline),
            "uploaded": len(uploads),
        }

    def import_bundle(
        self,
        bundle: str,
        *,
        allow_unsigned: bool = False,
        proposal_id: Optional[str] = None,
        accept_raw: bool = True,
    ) -> Dict[str, Any]:
        """Import a knowledge bundle into the caller's own project (D-40).

        ``bundle`` is a ``.kb.tar.gz`` archive or a bundle directory (packed
        here). The server verifies the manifest; ``allow_unsigned`` is the
        explicit opt-out for unsigned / foreign-installation bundles.
        """
        import os

        content = _bundle_archive_bytes(bundle)
        headers = self._actor_headers()
        if proposal_id:
            headers["x-proposal-id"] = proposal_id
        params: Dict[str, str] = {}
        if allow_unsigned:
            params["allow_unsigned"] = "true"
        if not accept_raw:  # take the knowledge, decline the raw objects
            params["accept_raw"] = "false"
        return self._client.post_multipart(
            f"{self._client._api_prefix}/knowledge/import",
            filename=os.path.basename(bundle.rstrip("/")) or "bundle",
            content=content,
            content_type="application/gzip",
            params=params or None,
            headers=headers,
        )

    def download_export(self, archive_key: str, dest: str) -> str:
        """Download an export archive (``archive_key`` from the export receipt)
        through the gateway's project-scoped files route; returns ``dest``."""
        from .files import FilesClient

        FilesClient(self._client._config).download_file(archive_key, dest=dest)
        return dest

    def graph_ingest(
        self,
        graph: str,
        *,
        project_id: str,
        files: Optional[List[str]] = None,
        urls: Optional[List[str]] = None,
        proposal_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest files/URLs INTO a named graph (server resolves by id/name).

        Convenience over :meth:`ingest_paths`. The named graph must exist
        (server: 400 otherwise).
        """
        return self.ingest_paths(
            project_id, files, urls=urls, proposal_id=proposal_id, graph_id=graph
        )

    # ── Phase 4: Knowledge Extraction ───────────────────────────────────

    def extract(
        self,
        project_id: str,
        source_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``POST /knowledge/extract`` was never mounted.

        Extraction happens inside the ingest/understand lane:
        :meth:`ingest_paths` / :meth:`upload_file`, then UMRS's
        ``POST /knowledge/understand``. (Historical docstring: extract
        structured knowledge from a source; the route never existed.)
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/extract",
            hint=(
                "Use the ingest/understand lane: knowledge.ingest_paths(…) or "
                "upload_file(…), then POST /knowledge/understand."
            ),
        )

    # ── Phase 5: Knowledge Linking ──────────────────────────────────────

    def link(
        self,
        project_id: str,
        source_id: str,
        target_id: str,
        relationship_type: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``POST /knowledge/link`` was never mounted.

        Node relations are written through graph proposals
        (:meth:`graph_propose` → :meth:`graph_decide`, ADR-084), which every
        knowledge write goes through. (Historical docstring: link two
        knowledge nodes with a relationship; the route never existed.)
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/link",
            hint="Write relations via graph proposals: knowledge.graph_propose/graph_decide.",
        )

    def get_graph(self, project_id: str) -> Dict[str, Any]:
        """Get the knowledge graph for a project."""
        return self._client.get(
            self._knowledge_url("graph"),
            params={"project_id": project_id},
        )

    # ── Phase 6: Knowledge Verification ─────────────────────────────────

    def verify(
        self,
        project_id: str,
        question: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Verify a knowledge claim by asking a question.

        Args:
            project_id: Project identifier.
            question: Verification question.
            **kwargs: Additional verification options.

        Returns:
            Verification answer with reasoning_path, evidence_ids, confidence, etc.
        """
        return self._client.post(
            self._knowledge_url("verify"),
            json={
                "project_id": project_id,
                "question": question,
                **kwargs,
            },
        )

    def verify_status(self, project_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``GET /knowledge/verify/status`` was never mounted.

        Verification runs as a durable job: :meth:`submit_verify`, then
        :meth:`verify_job_status`. (:meth:`verify` itself — one-shot POST
        ``/knowledge/verify`` — is mounted and unaffected.)
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/verify/status",
            hint="Use submit_verify(…) + verify_job_status(job_id) (the durable-job lane).",
        )

    # ── Phase 7: Coverage Dashboard ─────────────────────────────────────

    def coverage(self, project_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``GET /knowledge/coverage`` was never mounted.

        The live per-project state is :meth:`readiness` ( readiness level +
        gate) and :meth:`status` (artifact counts).
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/coverage",
            hint="Use readiness(project_id) / status(project_id).",
        )

    def coverage_gaps(self, project_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``GET /knowledge/coverage/gaps`` was never mounted."""
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/coverage/gaps",
            hint="Use readiness(project_id) / status(project_id).",
        )

    # ── Phase 8: External Knowledge ─────────────────────────────────────

    def external_search(
        self,
        project_id: str,
        query: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search external knowledge sources.

        Args:
            project_id: Project identifier.
            query: Search query.
            **kwargs: Additional search options.

        Returns:
            Search results from external knowledge sources.
        """
        return self._client.post(
            self._knowledge_url("external/search"),
            json={
                "project_id": project_id,
                "query": query,
                **kwargs,
            },
        )

    # ── Phase 9: Scientific Memory ──────────────────────────────────────

    def memory(self, project_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``GET /knowledge/memory`` was never mounted.

        The TUI retired its ``/memory`` surface for the same missing routes
        on 2026-10-17 (TUI audit). What the platform already records lives
        in :meth:`findings` / :meth:`graphs` and the loops API.
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/memory",
            hint="Recorded knowledge lives in findings(…)/graphs(…); plans run as research loops.",
        )

    def memory_add(
        self,
        project_id: str,
        memory_type: str,
        content: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Add an entry to the scientific memory.

        Args:
            project_id: Project identifier.
            memory_type: Type of memory entry (experiment, finding, observation, etc.).
            content: Memory content.
            **kwargs: Additional memory options.

        RETIRED 2026-10-20 — ``POST /knowledge/memory`` was never mounted;
        new knowledge lands through ingest/proposals, not a memory route.
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/memory",
            hint="New knowledge lands through ingest_paths(…)/graph_propose(…).",
        )

    # ── Phase 10: Readiness Gate ─────────────────────────────────────────

    def readiness(self, project_id: str) -> Dict[str, Any]:
        """Get the research readiness assessment for a project.

        Returns:
            Readiness assessment with readiness_level, readiness_checks, etc.
        """
        return self._client.get(
            self._knowledge_url("readiness"),
            params={"project_id": project_id},
        )

    def readiness_approve(self, project_id: str, reason: str = "") -> Dict[str, Any]:
        """Approve one project's knowledge base for autonomous operation.

        UMRS records a per-project ``readiness_approvals`` row (approver = the
        verified caller, threshold = the latest verification confidence) and
        ``readiness()`` then reports ``user_approved`` / ``approved_at``.

        Args:
            project_id: Project identifier (the caller must be able to write it).
            reason: Why the project is approved (audited).

        Returns:
            The approval row (``approval_id``, ``approved_at``, ``expires_at``, ...).
        """
        body: Dict[str, Any] = {"project_id": project_id}
        if reason:
            body["reason"] = reason
        # The actor header lets UMRS refuse an agent's approval (403).
        return self._client.post(
            self._knowledge_url("readiness/approve"),
            json=body,
            headers=self._actor_headers() or None,
        )

    # ── Phase 11: Research Planner ──────────────────────────────────────

    def plan(
        self,
        project_id: str,
        research_goal: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Generate a research plan from project knowledge.

        Args:
            project_id: Project identifier.
            research_goal: Research goal statement.
            **kwargs: Additional planning options.

        RETIRED 2026-10-20 — ``POST /knowledge/planner/plan`` was never
        mounted. Research planning runs as a research loop
        (:meth:`start_loop` and the loops API; ADR-092 team plans are a
        separate, team-engine lane).
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/planner/plan",
            hint="Research planning runs as a research loop: start_loop(…)/list_loops(…).",
        )

    def get_plan(self, project_id: str, plan_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``GET /knowledge/planner/plans/{id}`` was never
        mounted (:meth:`plan` names the live loop lane)."""
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/planner/plans/{plan_id}",
            hint="Research loops: get_loop(…)/list_loops(…).",
        )

    def list_plans(self, project_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``GET /knowledge/planner/plans`` was never
        mounted (:meth:`plan` names the live loop lane)."""
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/planner/plans",
            hint="Research loops: list_loops(…).",
        )

    def approve_plan(self, project_id: str, plan_id: str) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``POST /knowledge/planner/plans/{id}/approve``
        was never mounted (:meth:`plan` names the live loop lane)."""
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/planner/plans/{plan_id}/approve",
            hint="Research loops gate through their own start approvals (start_loop …, approved=).",
        )

    # ── Phase 12: Autonomous Research Loop ──────────────────────────────
    # Delegate to the parent Client's loop API (must add client-side loops -> /gw/loops) —
    # the standalone KnowledgeClient routes never existed server-side.

    def start_loop(
        self,
        project_id: str,
        research_goal: str,
        max_iterations: int = 10,
        *,
        approved: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Start an autonomous research loop on the platform (delegates to r4.Client)."""
        del kwargs
        return self._client.start_loop(
            project_id,
            research_goal,
            max_iterations=max_iterations,
            approved=approved,
        )

    def stop_loop(self, project_id: str, loop_id: str) -> Dict[str, Any]:
        """Stop a running research loop (delegates to r4.Client)."""
        return self._client.stop_loop(loop_id)

    def get_loop(self, project_id: str, loop_id: str) -> Dict[str, Any]:
        """Status snapshot of a research loop (delegates to r4.Client)."""
        return self._client.get_loop_status(loop_id)

    def list_loops(self, project_id: str) -> Dict[str, Any]:
        """List research loops; payloads mirror UMRS {loops: [...]}.

        NOTE: the flat UMRS `/umrs/loops` list has no project filter
        server-side; *project_id* is accepted for call compatibility but
        the listing intentionally crosses projects on the UMRS venue
        (runtime/umrs.py). Project filtering is a future additive
        server change — see docs/development/ui-tiering-2026-08-23.md
        residuals.
        """
        del project_id  # not honored server-side today
        return self._client.list_loops()

    def reconcile_project(self, project_id: str, *, sample: int = 50) -> Dict[str, Any]:
        """ADM-031 plane reconciliation scan for this project.

        Read-side consumption of the server-side authoritative checker:
        the returned counts classify every asset/document as COMPLETE /
        EMBEDDING_PENDING / PARTIAL / ORPHANED. Consistency state is
        derived server-side only; callers must not re-derive it locally.

        Returns {project_id, catalogue_available, counts, assets_sampled,
        assets[], orphaned_vector_rows, orphaned_chunk_nodes}.
        """
        project = project_id or self._client._config.project_id
        if not project:
            raise ValueError("project_id required (Client config has no active project)")
        # The resolved project rides X-Project-ID: the route scopes by that
        # header, so a tab's project was otherwise read from the profile
        # config (TUI audit r2 #54).
        return self._client.get(
            self._knowledge_url("reconcile"),
            params={"sample": sample},
            headers={**self._actor_headers(), "X-Project-ID": project},
        )

    def lineage(
        self,
        artifact_id: str,
        *,
        project_id: Optional[str] = None,
        depth: int = 3,
    ) -> Dict[str, Any]:
        """Provenance chain for one knowledge artifact.

        Returns ``{artifact_id, ancestors[], descendants[]}`` — a read-side
        projection of the server-side lineage join; empty lists mean "no
        recorded chain", which is not an error (workstream §7 honesty).
        """
        project = project_id or self._client._config.project_id
        if not project:
            raise ValueError("project_id required (Client config has no active project)")
        return self._client.get(
            self._knowledge_url(f"lineage/{artifact_id}"),
            params={"project_id": project, "depth": depth},
        )

    # ── Phase 13: Knowledge Correction ─────────────────────────────────

    def correct(
        self,
        project_id: str,
        node_id: str,
        correction: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """RETIRED 2026-10-20 — ``POST /knowledge/correct`` was never mounted.

        Corrections land through graph proposals (:meth:`graph_propose` →
        :meth:`graph_decide`, ADR-084) or :meth:`reconcile_project` for
        plane drift. (Historical docstring: correct an incorrect
        relationship or fact in the knowledge graph; the route never
        existed.)
        """
        raise self._raise_retired_http(
            f"{self._client._api_prefix}/knowledge/correct",
            hint="Corrections land via graph_propose/graph_decide or reconcile_project(…).",
        )

    # ── Project Status / Findings / Approve / Export ─────────────────────

    def status(self, project_id: str) -> Dict[str, Any]:
        """Get project status: ingestion state, knowledge graph stats, verification state.

        Args:
            project_id: Project identifier.

        Returns:
            Status dict with artifacts_ingested, knowledge_nodes, verification_state,
            readiness_level, coverage_score, etc.
        """
        return self._client.get(
            self._knowledge_url("status"),
            params={"project_id": project_id},
        )

    def findings(self, project_id: str) -> Dict[str, Any]:
        """Get verification findings for a project.

        Args:
            project_id: Project identifier.

        Returns:
            Findings dict with list of findings (severity, status, claim, evidence).
        """
        return self._client.get(
            self._knowledge_url("findings"),
            params={"project_id": project_id},
        )

    def approve(
        self,
        project_id: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Approve project knowledge for autonomous operation.

        Args:
            project_id: Project identifier.
            force: If True, approve even with audited findings (override).

        Returns:
            The gate's decision on the project's latest verification report:
            ``{approved, report_id, confidence, threshold,
            blocking_findings, reason}``. Without a report it is
            ``{approved: False, reasons: [...]}`` and nothing is posted.
        """
        # The route judges one report (report_id is required; sending only
        # project_id answered 422), so approve the newest one.
        reports = self.ingest_status(project_id).get("reports") or []
        report_id = str(reports[0].get("report_id") or "") if reports else ""
        if not report_id:
            return {
                "approved": False,
                "reasons": ["no verification report; run r4 project verify"],
            }
        return self._client.post(
            self._knowledge_url("approve"),
            json={"report_id": report_id, "force": force},
            headers=self._actor_headers() or None,
        )

    def export(
        self,
        project_id: str,
        *,
        include_raw: bool = False,
        acknowledge_sensitive: bool = False,
        chunk_text: bool = False,
        allow_unsigned: bool = False,
    ) -> Dict[str, Any]:
        """Trigger project knowledge bundle creation.

        Falls back to client-side aggregation if the server returns 404
        (older server without /export endpoint).

        Args:
            project_id: Project identifier.
            chunk_text: ADR-093 D-12 opt-in — each chunk's full md5-verified
                text (r4-kb v1.3); a sensitive project: its owner only.
            allow_unsigned: the server's explicit opt-out when it has no
                signing key (UMRS_SECRET_KEY): the manifest says
                ``unsigned: true``. Without it such a server refuses.

        Returns:
            Export result with bundle_id, status, download_url (when ready),
            partial flag, and partial_reason on fallback.
        """
        from .errors import APIError

        try:
            body: Dict[str, Any] = {"project_id": project_id}
            if include_raw:  # ADR-032 raw-materials amendment (owner/admin)
                body["include_raw"] = True
                body["acknowledge_sensitive"] = acknowledge_sensitive
            if chunk_text:  # ADR-093 D-12 (MD-5: owner only when sensitive)
                body["chunk_text"] = True
            if allow_unsigned:
                body["allow_unsigned"] = True
            return self._client.post(self._knowledge_url("export"), json=body)
        except APIError as exc:
            # Only an older server WITHOUT the export endpoint (404) falls
            # back to client-side aggregation; any other server error is the
            # caller's to see, never masked as a "ready" partial export.
            if exc.status_code != 404:
                raise
            return self._export_fallback(project_id, exc)

    def _export_fallback(
        self,
        project_id: str,
        upstream_error: Exception,
    ) -> Dict[str, Any]:
        """Client-side aggregation fallback for older servers.

        Collects artifacts via existing endpoints and bundles them locally.
        Returns a result with partial=True since we cannot guarantee full coverage.
        """
        partial_data: dict[str, Any] = {}
        warnings: list[str] = []

        # Try to fetch artifacts list: the K2 lane serves it (paged, at most
        # 100); /knowledge/artifacts has no route — 2026-10-18, C-2(v).
        try:
            resp = self._client.get(self._k2_url("artifacts"), params={"page_size": 100})
            if isinstance(resp, dict):
                partial_data["artifacts"] = resp.get("artifacts", [])
            else:
                warnings.append("could not fetch artifacts list")
        except Exception:
            warnings.append("could not fetch artifacts list")

        # Try to fetch graph
        try:
            url = f"{self._knowledge_url('graph')}?project_id={project_id}"
            resp = self._client.get(url)
            if isinstance(resp, dict):
                partial_data["graph"] = resp.get("nodes", [])
            else:
                warnings.append("could not fetch knowledge graph")
        except Exception:
            warnings.append("could not fetch knowledge graph")

        # Try to fetch catalog: UMRS serves no catalog listing, only
        # /catalog/search (a filter is required; the key prefix selects the
        # project's objects, at most 100) — 2026-10-18, cleanup C-2(v).
        try:
            resp = self._client.get(
                f"{self._client._api_prefix}/catalog/search",
                params={"prefix": f"{project_id}/", "limit": 100},
            )
            if isinstance(resp, dict):
                partial_data["catalog"] = resp.get("results", [])
            else:
                warnings.append("could not fetch catalog")
        except Exception:
            warnings.append("could not fetch catalog")

        parts = [
            "server-side export unavailable",
            f"({upstream_error})",
            f"fallback collected {len(partial_data)} sources",
        ]
        if warnings:
            parts.append(f"warnings: {'; '.join(warnings)}")
        partial_reason = "; ".join(parts)

        return {
            "status": "ready",
            "partial": True,
            "partial_reason": partial_reason,
            "fallback_data": partial_data,
            "bundle_id": f"fallback-{project_id}",
            "error": str(upstream_error),
        }

    def get_lineage(
        self,
        project_id: str,
        artifact_id: str,
        depth: int = 3,
    ) -> Dict[str, Any]:
        """Query lineage for a knowledge artifact.

        Returns ancestors and descendants joined from knowledge_a2l_links
        and a2l_artifact_refs.

        Args:
            project_id: Project identifier.
            artifact_id: Artifact ID to query.
            depth: Max chain depth (default 3).

        Returns:
            Lineage dict with ancestors and descendants lists.
        """
        try:
            url = f"{self._knowledge_url('lineage')}/{artifact_id}"
            return self._client.get(
                url,
                params={"project_id": project_id, "depth": depth},
            )
        except Exception:
            return {"artifact_id": artifact_id, "ancestors": [], "descendants": []}

    # ── Knowledge Verification (durable job layer) ────────────────────

    def submit_verify(
        self,
        project_id: str,
        verifier_names: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Submit a KNOWLEDGE_VERIFY durable job.

        Args:
            project_id: Project identifier.
            verifier_names: Optional list of specific verifiers to run.
            **kwargs: Additional options.

        Returns:
            Job submission result with job_id and status.
        """
        return self._client.post(
            self._k2_url("verify"),
            json={
                "project_id": project_id,
                "verifier_names": verifier_names,
                **kwargs,
            },
        )

    def verify_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a knowledge verification durable job.

        Args:
            job_id: Verification job ID.

        Returns:
            Job status with job_type, status, report_id, etc.
        """
        return self._client.get(
            f"{self._client._api_prefix}/jobs/{job_id}",
        )

    # ── Knowledge Verification Store ─────────────────────────────────

    def transition_finding(
        self,
        project_id: str,
        finding_key: str,
        from_status: str,
        new_status: str,
        evidence: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Transition a finding status.

        Args:
            project_id: Project identifier.
            finding_key: Finding identifier.
            from_status: Current status.
            new_status: Target status (acknowledged, resolved, waived).
            evidence: Evidence for the transition.
            **kwargs: Additional options.

        Returns:
            Transition result with updated status.
        """
        return self._client.patch(
            self._knowledge_url(f"findings/{finding_key}"),
            json={
                "finding_key": finding_key,
                "from_status": from_status,
                "new_status": new_status,
                "evidence": evidence,
                **kwargs,
            },
        )

    # ── Phase 12: Workflow Emitter ─────────────────────────────────────

    def emit_workflow(
        self,
        plan: Dict[str, Any],
        project_id: str = "",
        model: Optional[str] = None,
        partition: Optional[str] = None,
        account: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Emit a WorkflowDraft from a ResearchPlan dict.

        Args:
            plan: ResearchPlan dict with objectives, hypotheses, experiments.
            project_id: Project ID (for k2 layer, empty string for legacy).
            model: Model name for project context prefill.
            partition: Slurm partition for project context prefill.
            account: Slurm account for project context prefill.

        Returns:
            WorkflowDraft dict with workflow_id, name, steps, project_context, _meta.
        """
        return self._client.post(
            # The ingest lane, not the retired codename redirect (2026-10-18).
            self._k2_url("emit-workflow"),
            json={
                "plan": plan,
                "project_id": project_id or None,
                "model": model,
                "partition": partition,
                "account": account,
                **kwargs,
            },
        )
