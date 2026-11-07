"""R4 CLI handlers for UMRS knowledge workflow (ingest → understand → verify)."""

from __future__ import annotations

import json
import sys
import threading
from argparse import Namespace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..project import ProjectManager

_ARTIFACT_MANIFEST = "artifact_manifest.json"
_ARTIFACTS_SUBDIR = "artifacts"
_RESEARCH_PLAN_FILE = "research_plan.json"


def _import_research_project():
    try:
        from umrs.knowledge.project import (  # noqa: boundary — lazy server-model fetch
            ResearchProject,
        )
    except ImportError as exc:
        print(
            "UMRS knowledge package is required for this command. "
            "Install umrs from the umrs/ submodule of the R4 repository: "
            'pip install -e "./umrs/src[client]".',
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return ResearchProject


def _resolve_project_id(args: Namespace, pm: ProjectManager) -> str:
    from ..source_context import selected_project

    # Under --source on another deployment, only that source's own project
    # (onboarding plan S1): never the active deployment's switched project.
    project_id = getattr(args, "project_id", None) or selected_project(config_fallback=False)
    if not project_id:
        print(
            "No project specified. Pass <project-id> or run: r4 project switch <id>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if pm.get(project_id) is None:
        from ._helpers import _die

        _die(
            f"Project not found: {project_id}",
            error_id="project.not_found",
        )
    return project_id


def _build_research_project(pm: ProjectManager, project_id: str):
    ResearchProject = _import_research_project()
    config = pm.get(project_id)
    storage_path = pm.data_dir(project_id)
    storage_path.mkdir(parents=True, exist_ok=True)
    project = ResearchProject(
        project_id=project_id,
        name=(config.name if config else None) or project_id,
        storage_path=storage_path,
    )
    # Wire durable workflow port when Gateway client is available (real mode).
    try:
        from r4.client import Client
        from r4.research.workflow_executor import R4ClientWorkflowExecutor

        # Bind Client to the RC1 project id — profile/JWT often stay on
        # "default" after `r4 project switch`, which leaks into durable jobs.
        client = Client(project_id=project_id)
        project.set_workflow_port(R4ClientWorkflowExecutor(client, require_durable=True))
    except Exception:  # noqa: BLE001 — simulate mode still works without a port
        pass
    return project


def _safe_artifact_filename(artifact_id: str) -> str:
    return f"{quote(artifact_id, safe='')}.bin"


def _is_local_knowledge_store(project) -> bool:
    from umrs.knowledge.verify import InMemoryKnowledgeStore  # noqa: boundary — lazy store probe

    return isinstance(project.knowledge_store, InMemoryKnowledgeStore)


def save_artifact_manifest(project, data_dir: Path) -> int:
    """Persist ingested artifact bytes for later CLI sessions."""
    if not _is_local_knowledge_store(project):
        return len(project.knowledge_store.list_artifacts())

    artifacts_dir = data_dir / _ARTIFACTS_SUBDIR
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    for artifact in project.knowledge_store.list_artifacts():
        artifact_id = artifact["artifact_id"]
        content = artifact.get("content", b"")
        if isinstance(content, str):
            content = content.encode("utf-8")
        content_file = f"{_ARTIFACTS_SUBDIR}/{_safe_artifact_filename(artifact_id)}"
        (data_dir / content_file).write_bytes(content)
        entry = {key: value for key, value in artifact.items() if key != "content"}
        entry["content_file"] = content_file
        manifest.append(entry)

    (data_dir / _ARTIFACT_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return len(manifest)


def load_artifact_manifest(project, data_dir: Path) -> int:
    """Restore artifacts saved by ``save_artifact_manifest``."""
    if not _is_local_knowledge_store(project):
        project.knowledge_store.refresh()
        return len(project.knowledge_store.list_artifacts())

    manifest_path = data_dir / _ARTIFACT_MANIFEST
    if not manifest_path.is_file():
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    store = project.knowledge_store
    store.artifacts.clear()

    for entry in manifest:
        content_path = data_dir / entry["content_file"]
        artifact = {key: value for key, value in entry.items() if key != "content_file"}
        artifact["content"] = content_path.read_bytes()
        store.add_artifact(artifact)

    return len(manifest)


def save_research_plan(data_dir: Path, plan, research_goal: str) -> None:
    """Persist the latest research plan for cross-session CLI runs."""
    payload = plan.to_dict()
    payload["research_goal"] = research_goal
    (data_dir / _RESEARCH_PLAN_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_research_goal(data_dir: Path) -> str | None:
    """Return a saved research goal from a prior ``project plan`` run."""
    plan_path = data_dir / _RESEARCH_PLAN_FILE
    if not plan_path.is_file():
        return None
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    goal = payload.get("research_goal")
    return goal if isinstance(goal, str) and goal.strip() else None


def project_import(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Import files into the UMRS knowledge store for a project.

    Default path (V-02/D-38): a thin client of the durable ingest chain —
    file contents reach UMRS inline or as uploaded bytes and the server runs
    KNOWLEDGE_INGEST → UNDERSTAND → EMBED → VERIFY. ``--local``
    keeps the legacy in-process simulation mode (nothing durable).
    """
    project_id = _resolve_project_id(args, pm)
    paths = [Path(p) for p in args.paths]
    for path in paths:
        if not path.exists():
            from ._helpers import _die

            _die(
                f"Path not found: {path}",
                action="project.import",
                error_id="project.import.path_missing",
            )

    if getattr(args, "local", False):
        return _project_import_local(args, pm, project_id, paths)
    return _project_import_durable(args, project_id, paths)


def _project_import_durable(args, project_id: str, paths: list) -> None:
    """Thin client: workstation files → the durable KNOWLEDGE_INGEST chain (V-02).

    Small text files ride inline; everything else is uploaded as bytes —
    paths never travel (the ingest job runs inside UMRS, not here).
    """
    from ..client import Client
    from ..errors import R4Error
    from ..knowledge import KnowledgeClient

    client = KnowledgeClient(Client(project_id=project_id))
    try:
        result = client.ingest_paths(project_id, [str(p) for p in paths])
    except (R4Error, ValueError, OSError) as exc:
        from ._helpers import _die

        _die(
            f"Durable ingest failed: {exc}",
            action="project.import",
            error_id="project.import.ingest_failed",
        )
        return
    if getattr(args, "json", False):
        print(json.dumps({"project_id": project_id, **result}, indent=2))
        return
    jobs = result["jobs"]
    print(
        f"✓ Durable ingest queued for {project_id} "
        f"({result['inlined']} inline, {result['uploaded']} uploaded; {len(jobs)} job(s))"
    )
    print("  Chain: ingest → understand → embed → verify")
    for job in jobs:
        job_id = job.get("job_id", "unknown")
        print(f"  Job: {job_id}  (track: r4 job describe {job_id})")


def _project_import_local(args, pm: ProjectManager, project_id: str, paths: list) -> None:
    """Legacy in-process simulation mode (deprecated — nothing durable)."""
    print(
        "⚠ --local: legacy in-process mode (deprecated) — artifacts live only "
        "in this workstation's in-memory store; nothing durable reaches UMRS.",
        file=sys.stderr,
    )
    project = _build_research_project(pm, project_id)
    count = project.ingest_directory(*paths)
    saved = save_artifact_manifest(project, pm.data_dir(project_id))

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "artifacts_ingested": count,
                    "artifacts_persisted": saved,
                    "phase": project.phase.value,
                    "mode": "local-legacy",
                },
                indent=2,
            )
        )
        return

    print(f"✓ Ingested {count} artifact(s) into project {project_id} (local mode)")
    print(f"  Workspace: {pm.data_dir(project_id)}")


def project_init(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Extract structured knowledge and build the knowledge graph."""
    project_id = _resolve_project_id(args, pm)
    data_dir = pm.data_dir(project_id)
    project = _build_research_project(pm, project_id)

    restored = load_artifact_manifest(project, data_dir)
    if restored == 0 and not project.knowledge_store.list_artifacts():
        print(
            f"✗ No artifacts for {project_id}. Run: r4 project ingest {project_id} <dirs...>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    project.understand()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "phase": project.phase.value,
                    "extracted_count": len(project.extracted_knowledge),
                    "graph_nodes": len(project.knowledge_graph.nodes),
                    "graph_edges": len(project.knowledge_graph.edges),
                    "coverage_score": (
                        project.coverage_dashboard.coverage_score
                        if project.coverage_dashboard
                        else 0.0
                    ),
                },
                indent=2,
            )
        )
        return

    coverage = project.coverage_dashboard.coverage_score if project.coverage_dashboard else 0.0
    print(f"✓ Understood {len(project.extracted_knowledge)} artifact(s) for {project_id}")
    print(
        f"  Graph: {len(project.knowledge_graph.nodes)} nodes, "
        f"{len(project.knowledge_graph.edges)} edges, "
        f"coverage {coverage:.2f}"
    )


def project_verify(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Answer a knowledge verification question with evidence."""
    project_id = _resolve_project_id(args, pm)
    project = _verify_load_project(pm, project_id)

    if not project.extracted_knowledge and project.coverage_dashboard is None:
        project.understand()

    answer = project.verify(args.question)

    if getattr(args, "json", False):
        print(json.dumps(_verify_result_json(args.question, answer), indent=2))
        return

    print(f"Q: {args.question}")
    print(f"A: {answer.answer if hasattr(answer, 'answer') else answer}")
    if hasattr(answer, "confidence"):
        print(f"Confidence: {answer.confidence:.2f}")
    if hasattr(answer, "evidence") and answer.evidence:
        print(f"Evidence ({len(answer.evidence)}):")


def _verify_load_project(pm: ProjectManager, project_id: str):
    """Load a project's artifacts, exiting when nothing was ingested."""
    data_dir = pm.data_dir(project_id)
    project = _build_research_project(pm, project_id)
    restored = load_artifact_manifest(project, data_dir)
    if restored == 0 and not project.knowledge_store.list_artifacts():
        print(
            f"✗ No artifacts for {project_id}. Run: r4 project import {project_id} <dirs...>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return project


def _evidence_row(item) -> dict:
    """Normalize one evidence item (Object/Dict/str) into a display row."""
    if hasattr(item, "to_dict"):
        data = item.to_dict()
        return {
            "source_id": data.get("source_id", ""),
            "source_name": data.get("source_name", ""),
            "source": data.get("source_name") or data.get("source_id", ""),
            "text": data.get("content", ""),
        }
    if hasattr(item, "source"):
        return {"source": item.source, "text": getattr(item, "text", "")}
    return {"text": str(item)}


def _evidence_row_id(row: dict) -> str:
    """Best available identifier for an evidence row, empty string if none."""
    return str(row.get("source_name") or row.get("source_id") or row.get("source") or "")


def _verify_result_json(question: str, answer) -> dict:
    """Build the JSON payload for ``project verify --json``."""
    result = {
        "answered": True,
        "question": question,
        "answer": answer.answer if hasattr(answer, "answer") else str(answer),
        "confidence": answer.confidence if hasattr(answer, "confidence") else 0.0,
    }
    evidence = getattr(answer, "evidence", None) or []
    if not evidence:
        return result
    rows = [_evidence_row(item) for item in evidence]
    result["evidence"] = rows
    result["evidence_ids"] = [identifier for row in rows if (identifier := _evidence_row_id(row))]
    return result


def _progress_spinner(done_event: threading.Event) -> None:
    """Render a rotating spinner until done_event is set."""
    frames = ["\u2502", "\u2570", "\u2554", "\u2560"]
    idx = 0
    while not done_event.is_set():
        sys.stdout.write(f"\r\u2501\u2501\u2501\u2501 [{frames[idx % len(frames)]}] ")
        sys.stdout.flush()
        done_event.wait(0.15)
    sys.stdout.write("\r\u2501\u2501\u2501\u2501 [✓] ")
    sys.stdout.flush()


def _graph_file_overlay(pm: ProjectManager, project_id: str) -> dict:
    """Fallback graph counts read from the persisted knowledge_graph.json.

    Status rebuilds a fresh ResearchProject without re-running understand, so
    the in-memory graph is empty; prefer the persisted graph file in that case.
    """
    graph_path = pm.data_dir(project_id) / "knowledge_graph.json"
    if not graph_path.is_file():
        return {}
    try:
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    node_count = len(payload.get("nodes") or {})
    edge_count = len(payload.get("edges") or [])
    overlay: dict = {"knowledge_nodes": node_count, "knowledge_edges": edge_count}
    if node_count:
        overlay["extraction_state"] = "complete"
    return overlay


def _readiness_overlay_level(project) -> str | None:
    """Readiness level string from the project's readiness report, if any."""
    report = project.readiness_report
    if report is None:
        return None
    if report.user_approved:
        return "approved"
    return str(getattr(report, "readiness_level", "checked"))


def _graph_counts(graph) -> tuple[int, int]:
    """(node_count, edge_count) for an in-memory knowledge graph object."""
    if graph is None:
        return 0, 0
    nodes = len(getattr(graph, "nodes", []) or [])
    edges = len(getattr(graph, "edges", []) or [])
    return nodes, edges


def _collect_local_overlay(
    project, pm: ProjectManager, project_id: str, artifacts_ingested: int, nodes: int, edges: int
) -> dict:
    """Assemble the overlay dict from an already-loaded research project."""
    overlay: dict = {
        "artifacts_ingested": artifacts_ingested,
        "knowledge_nodes": nodes,
        "knowledge_edges": edges,
    }
    if getattr(project, "extracted_knowledge", None):
        overlay["extraction_state"] = "complete"
    if nodes == 0 and edges == 0:
        overlay.update(_graph_file_overlay(pm, project_id))
    level = _readiness_overlay_level(project)
    if level is not None:
        overlay["readiness_level"] = level
    if project.research_plan is not None:
        overlay["local_plan"] = True
    return overlay


def _local_project_status_overlay(pm: ProjectManager, project_id: str) -> dict:
    """Populate status fields from the local RC1 workspace when present.

    Import/understand write locally; gateway /knowledge/status often stays empty
    until server-side ingest exists. Prefer local counts so CLI status matches
    what `r4 project loop start` actually uses.
    """
    try:
        project = _build_research_project(pm, project_id)
        restored = load_artifact_manifest(project, pm.data_dir(project_id))
        artifacts = list(project.knowledge_store.list_artifacts())
        nodes, edges = _graph_counts(getattr(project, "knowledge_graph", None))
        has_data = bool(artifacts) or bool(restored) or nodes > 0 or edges > 0
        if not has_data:
            return {}
        return _collect_local_overlay(
            project, pm, project_id, len(artifacts) or restored, nodes, edges
        )
    except Exception:  # noqa: BLE001 — status must still render gateway data
        return {}


def _apply_local_status_overlay(data: dict, pm: ProjectManager, project_id: str) -> None:
    """Merge the local RC1 workspace overlay into fetched status data in place.

    Local workspace is authoritative for import/understand counts, so overlay
    values replace the gateway-reported knowledge state.
    """
    overlay = _local_project_status_overlay(pm, project_id)
    if not overlay:
        return
    knowledge = dict(data.get("knowledge") or {})
    for key, value in overlay.items():
        if key == "local_plan":
            continue
        knowledge[key] = value
    knowledge.pop("error", None)
    data["knowledge"] = knowledge
    if overlay.get("local_plan") and not data.get("plan_count"):
        data["plan_count"] = max(int(data.get("plan_count") or 0), 1)


def _get_project_status_data(client, project_id: str, pm: ProjectManager | None = None) -> dict:
    """Fetch project status from knowledge + workflows APIs (with local overlay)."""
    data: dict = {}

    # Knowledge status
    try:
        knowledge = client.knowledge.status(project_id)
        data["knowledge"] = knowledge if isinstance(knowledge, dict) else {}
    except Exception as exc:  # noqa: BLE001
        data["knowledge"] = {"error": f"unavailable: {exc}"}

    if pm is not None:
        _apply_local_status_overlay(data, pm, project_id)

    # Workflow status (active executions)
    try:
        workflows = client.workflows.list_workflows()
        data["workflow_count"] = len(workflows)
    except Exception:  # noqa: BLE001
        data["workflow_count"] = 0
        data["workflows_error"] = "unavailable"

    # Plan / loop state
    try:
        plans = client.knowledge.list_plans(project_id)
        plan_list = plans.get("plans", [])
        data["plan_count"] = len(plan_list)
        if plan_list:
            data["latest_plan_id"] = plan_list[-1].get("plan_id", "unknown")
    except Exception:  # noqa: BLE001
        data.setdefault("plan_count", data.get("plan_count", 0))
        data.setdefault("latest_plan_id", None)

    return data


def _format_status(data: dict, project_id: str) -> list[str]:
    """Format project status data into display lines."""
    lines: list[str] = []
    lines.append(f"Project: {project_id}")

    knowledge = data.get("knowledge", {})
    if knowledge.get("error"):
        lines.append(f"  Knowledge API: {knowledge['error']}")

    # Knowledge state (UMRS uses confidence; local overlay may set coverage_score)
    artifacts = knowledge.get("artifacts_ingested", 0)
    nodes = knowledge.get("knowledge_nodes", 0)
    edges = knowledge.get("knowledge_edges", 0)
    extraction = knowledge.get("extraction_state", "unstarted")
    verifications = knowledge.get("verifications_pending", 0)
    readiness_level = knowledge.get("readiness_level", "unknown")
    coverage = knowledge.get("coverage_score", knowledge.get("confidence"))

    lines.append(f"  Artifacts ingested: {artifacts}")
    lines.append(f"  Extraction: {extraction}")
    lines.append(f"  Graph: {nodes} nodes, {edges} edges")
    if coverage is not None:
        try:
            lines.append(f"  Coverage: {float(coverage):.2f}")
        except (TypeError, ValueError):
            lines.append(f"  Coverage: {coverage}")
    lines.append(f"  Verifications pending: {verifications}")
    lines.append(f"  Readiness: {readiness_level}")

    # Workflow status
    wf_count = data.get("workflow_count", 0)
    lines.append(f"  Registered workflows: {wf_count}")

    # Plan / loop state
    plan_count = data.get("plan_count", 0)
    plan_id = data.get("latest_plan_id")
    lines.append(f"  Research plans: {plan_count}")
    if plan_id:
        lines.append(f"  Latest plan: {plan_id}")

    return lines


def project_status(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Show project status (imported artifacts, extraction, verifications, workflows, loops)."""
    project_id = _resolve_project_id(args, pm)
    watch = getattr(args, "watch", False)
    interval = getattr(args, "interval", 5.0)
    as_json = getattr(args, "json", False)

    from ..client import Client

    client = Client(project_id=project_id)

    def _display():
        data = _get_project_status_data(client, project_id, pm)
        if as_json:
            print(json.dumps(data, indent=2, default=str))
        else:
            for line in _format_status(data, project_id):
                print(line)

    if watch:
        _display()
        try:
            while True:
                import time

                time.sleep(interval)
                # Clear screen for clean re-render
                print("\033[2J\033[H", end="")
                _display()
        except KeyboardInterrupt:
            if not as_json:
                print("\nWatching stopped.")
            return
    else:
        _display()


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _group_findings_by_severity(findings: list) -> list[tuple[str, list]]:
    """Group findings by severity in a stable severity order."""
    by_severity: dict[str, list] = {}
    for f in findings:
        sev = f.get("severity", "info")
        by_severity.setdefault(sev, []).append(f)
    return sorted(by_severity.items(), key=lambda kv: _SEVERITY_ORDER.get(kv[0], 99))


def _print_findings_by_severity(findings: list) -> None:
    """Render findings grouped by severity, capped at ten items per group."""
    for sev, items in _group_findings_by_severity(findings):
        print(f"\n  [{sev.upper()}] {len(items)} finding(s)")
        for f in items[:10]:
            claim = f.get("claim", "(no claim)")
            status = f.get("status", "unknown")
            print(f"    - {claim} [{status}]")
        if len(items) > 10:
            print(f"    ... and {len(items) - 10} more")


def _fetch_findings(client, project_id: str, as_json: bool) -> dict:
    """Verification findings from the gateway.

    The local RC1 run-store fallback retired with the spine (WP-07); loop
    findings are knowledge artifacts now (``r4 project loop status`` /
    ``knowledge_show``), so an unreachable gateway is an honest error.
    """
    from ..errors import AuthenticationError, R4Error

    try:
        return client.knowledge.findings(project_id)
    except (AuthenticationError, R4Error, Exception) as exc:  # noqa: BLE001
        if as_json:
            print(json.dumps({"findings": [], "total": 0, "error": str(exc)}, indent=2))
        else:
            print(
                f"✗ Findings unavailable from gateway ({exc}). Loop findings live in the "
                f"knowledge graph: r4 project loop status <loop-id> or knowledge_show.",
                file=sys.stderr,
            )
        raise SystemExit(1) from exc


def project_findings(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """List verification findings with severity and status."""
    project_id = _resolve_project_id(args, pm)
    from ..client import Client

    client = Client(project_id=project_id)
    as_json = bool(getattr(args, "json", False))
    result = _fetch_findings(client, project_id, as_json)

    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return

    findings = result.get("findings", [])
    total = result.get("total", len(findings))
    print(f"Findings for {project_id}: {total} total")
    _print_findings_by_severity(findings)


def project_approve(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Approve project knowledge for autonomous operation.

    Two calls: the gate judges the latest verification report, and only a
    passed gate records the per-project readiness approval that
    ``GET /knowledge/readiness`` then reports as ``user_approved`` (the gate
    alone wrote no approval; TUI audit r2, readiness approval).
    """
    project_id = _resolve_project_id(args, pm)
    from ..client import Client

    client = Client()
    force = getattr(args, "force", False)
    result = client.knowledge.approve(project_id, force=force)
    if result.get("approved"):
        reason = getattr(args, "reason", "") or ("force override" if force else "")
        result["readiness_approval"] = client.knowledge.readiness_approve(project_id, reason=reason)

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str))
        return
    _print_approve_result(project_id, result, force)


def _print_approve_result(project_id: str, result: dict[str, Any], force: bool) -> None:
    """Human output of ``r4 project approve``."""
    if not result.get("approved"):
        _print_approve_denied(project_id, result)
        return
    status_msg = "FORCE" if force else "approved"
    print(f"\u2714 Project {project_id} {status_msg} for autonomous operation")
    approval = result.get("readiness_approval") or {}
    if approval.get("approval_id"):
        expires = approval.get("expires_at") or "?"
        print(f"  approval: {approval['approval_id']} (expires {expires})")


def _print_approve_denied(project_id: str, result: dict[str, Any]) -> None:
    """The gate's refusal: its reasons list, or the route's one reason."""
    print(f"\u2718 Approval denied for {project_id}", file=sys.stderr)
    reasons = [r for r in (result.get("reasons") or [result.get("reason")]) if r]
    if not reasons:
        return
    print("  Reasons:", file=sys.stderr)
    for r in reasons:
        print(f"    - {r}", file=sys.stderr)


def project_export(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> int:
    """Trigger project knowledge bundle export with strict/partial semantics."""
    project_id = _resolve_project_id(args, pm)
    from ..client import Client

    client = Client()
    detach = getattr(args, "detach", False)
    strict = getattr(args, "strict", False)

    try:
        result = client.knowledge.export(
            project_id,
            include_raw=getattr(args, "with_raw", False),
            acknowledge_sensitive=getattr(args, "acknowledge_sensitive", False),
            chunk_text=getattr(args, "chunk_text", False),
            allow_unsigned=_allow_unsigned(args),
        )
    except Exception as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str))
        return 0

    return _print_export_result(result, project_id, strict, detach)


def _allow_unsigned(args: Namespace) -> bool:
    """``--allow-unsigned``: the server's explicit opt-out when it has no
    signing key, never a default. Announced on stderr every time (stdout stays
    clean for ``--json``).

    Cycle-3 client-server-compat#2 (2026-10-22): a keyless server refuses a
    signed export and names this flag; no r4 export command had it, so its
    users could not export their own project at all."""
    if not getattr(args, "allow_unsigned", False):
        return False
    print(
        "WARNING: --allow-unsigned: if this server has no bundle signing key, the "
        "bundle is UNSIGNED (manifest unsigned: true) and nobody can verify where "
        "it came from; importing it needs --allow-unsigned too.",
        file=sys.stderr,
    )
    return True


def _print_archive_hint(result: dict) -> None:
    """Name the downloadable archive (D-40 transport) when the server returned one."""
    key = result.get("archive_key")
    if key:
        print(f"  Archive: {key}")
        print(f"  Download when done: r4 knowledge export-download {result.get('job_id', key)}")


def _print_export_result(
    result: dict,
    project_id: str,
    strict: bool,
    detach: bool,
) -> int:
    """Print export result and return exit code (0 or 1)."""
    status = result.get("status", "processing")
    partial = result.get("partial", False)

    print(f"Export triggered for {project_id}")
    print(f"  Bundle ID: {result.get('bundle_id', 'unknown')}")
    print(f"  Status: {status}")
    _print_archive_hint(result)

    if partial:
        reason = result.get("partial_reason", "")
        print(f"  Partial export: {reason}", file=sys.stderr)
        if strict:
            print("--strict mode: exiting non-zero due to partial export", file=sys.stderr)
            return 1

    if detach or status == "processing":
        print("  Running in background. Use 'r4 project status' to check progress.")
        return 0

    if url := result.get("download_url"):
        print(f"  Download: {url}")

    if status == "failed":
        error = result.get("error", "unknown error")
        print(f"Export failed: {error}", file=sys.stderr)
        return 1 if strict else 0

    if status == "ready":
        print("  Export ready.")
    return 0


def project_lineage(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> int:
    """Query lineage for a knowledge artifact."""
    project_id = _resolve_project_id(args, pm)
    from ..client import Client

    client = Client()
    artifact_id = args.artifact_id
    depth = getattr(args, "depth", 3)

    try:
        lineage = client.knowledge.get_lineage(project_id, artifact_id, depth=depth)
    except Exception as exc:
        print(f"Lineage query failed: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(lineage, indent=2, default=str))
        return 0

    print(f"Lineage for {artifact_id} (depth={depth}):")
    ancestors = lineage.get("ancestors", [])
    descendants = lineage.get("descendants", [])

    if ancestors:
        print(f"\n  Ancestors ({len(ancestors)}):")
        for a in ancestors:
            kind = a.get("artifact_type", "?")
            title = a.get("title", "?")[:40]
            edge = a.get("edge_kind", "")
            print(f"    [{kind}] {title}  (via {edge})")

    if descendants:
        print(f"\n  Descendants ({len(descendants)}):")
        for d in descendants:
            kind = d.get("artifact_type", "?")
            title = d.get("title", "?")[:40]
            edge = d.get("edge_kind", "")
            print(f"    [{kind}] {title}  (via {edge})")

    if not ancestors and not descendants:
        print("  No lineage links found.")

    return 0


def _render_citation(
    artifact: dict,
    lineage_cache: dict[str, dict] | None = None,
) -> str:
    """Format a citation artifact as a short reference string.

    If the artifact has A2L links (checked via lineage_cache or lineage
    endpoint), render the A2L short-id (e.g. "A2L:a3f...") in the
    citation header.
    """
    aid = artifact.get("artifact_id", "?")
    kind = artifact.get("kind", "document")
    title = artifact.get("title", artifact.get("name", aid[:24]))
    short = aid[:8]

    a2l_short = _citation_a2l_short(aid, lineage_cache)
    if a2l_short:
        return f"[{kind}/{short} A2L:{a2l_short}] {title}"
    return f"[{kind}/{short}] {title}"


def _citation_a2l_short(aid: str, lineage_cache: dict[str, dict] | None) -> str | None:
    """Resolve an A2L short-id for a citation from the lineage cache."""
    if lineage_cache is None or aid not in lineage_cache:
        return None
    for anc in lineage_cache[aid].get("ancestors", []):
        if anc.get("artifact_type") == "a2l":
            a2l_id = anc.get("title", "")
            if a2l_id and a2l_id != aid[:24]:
                return a2l_id[:12]
    return None


def _ask_resolve_evidence(project, evidence_ids, lineage_cache=None) -> list[dict]:
    """Resolve artifact details for evidence ids.

    ``lineage_cache`` is accepted for call-site uniformity (citations are
    rendered from the same shape as the remote path after this helper).
    """
    artifacts = []
    for eid in evidence_ids or []:
        try:
            art = project.knowledge_store.get_artifact(eid)
            if art:
                artifacts.append(
                    {
                        "artifact_id": eid,
                        "kind": art.get("kind", "document"),
                        "title": art.get("title", art.get("name", "")),
                        "content_preview": (art.get("content", "") or "")[:200],
                    }
                )
        except Exception:
            artifacts.append(
                {
                    "artifact_id": eid,
                    "kind": "unknown",
                    "title": "",
                    "content_preview": "",
                }
            )
    return artifacts


def _fetch_lineage_cache(get_lineage, project_id: str, evidence_ids) -> dict[str, dict]:
    """Pre-fetch lineage for evidence artifacts to populate A2L short-ids."""
    lineage_cache: dict[str, dict] = {}
    try:
        for eid in evidence_ids:
            try:
                lineage_cache[eid] = get_lineage(project_id, eid)
            except Exception:
                lineage_cache[eid] = {"ancestors": [], "descendants": []}
    except Exception:
        lineage_cache = {}
    return lineage_cache


def project_ask(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Ask a question about the project's knowledge graph, returning evidence-cited answers."""
    project_id = _resolve_project_id(args, pm)
    question = args.question

    try:
        _ask_local(args, pm, project_id, question)
        return
    except SystemExit:
        # Local path failed (e.g. no UMRS importable, no artifacts).
        # Fall through to the Gateway/UMRS knowledge path.
        pass

    _ask_remote(args, project_id, question)


def _ask_local(args, pm: ProjectManager, project_id: str, question: str) -> None:
    """Local knowledge store path (existing project with extracted knowledge)."""
    project = _build_research_project(pm, project_id)
    data_dir = pm.data_dir(project_id)
    restored = load_artifact_manifest(project, data_dir)
    if restored == 0 and not project.knowledge_store.list_artifacts():
        print(
            f"✗ No artifacts for {project_id}. Run: r4 project import {project_id} <dirs...>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not project.extracted_knowledge:
        project.understand()

    answer, evidence_ids, confidence = _ask_local_verify(project, question)
    get_lineage = getattr(project.knowledge_store, "get_lineage", None)
    lineage_cache = (
        _fetch_lineage_cache(get_lineage, project_id, evidence_ids)
        if evidence_ids and callable(get_lineage)
        else {}
    )

    if getattr(args, "json", False):
        _print_ask_local_json(
            project, project_id, question, answer, confidence, evidence_ids, lineage_cache
        )
        return

    _print_ask_local_text(project, question, answer, confidence, evidence_ids, lineage_cache)


def _print_ask_local_text(
    project, question: str, answer, confidence, evidence_ids, lineage_cache
) -> None:
    """Human-readable form of the local ask answer."""
    print(f"Q: {question}")
    print(f"A: {answer}")
    if confidence is not None:
        print(f"Confidence: {confidence:.2f}")
    if evidence_ids:
        print(f"\nEvidences ({len(evidence_ids)}):")
        for art in _ask_resolve_evidence(project, evidence_ids, lineage_cache):
            print(f"  {_render_citation(art, lineage_cache)}")


def _ask_local_verify(project, question: str):
    """Run the local verify path and decompose the result.

    Backed by ``ResearchProject.verify`` (umrs.knowledge.project), which
    returns a ``VerificationAnswer`` carrying answer text, Evidence objects,
    confidence, and identified gaps. Evidence ids are mapped onto the
    artifact ids the CLI lineage view expects.
    """
    result = project.verify(question)
    answer = getattr(result, "answer", None)
    if answer is None:  # tolerate dict-shaped fakes used by older tests
        answer = result.get("answer", "No answer returned.")
    raw_evidence = getattr(result, "evidence", None)
    if raw_evidence is None:
        raw_evidence = result.get("evidence_ids", [])
    confidence = getattr(result, "confidence", None)
    if confidence is None and isinstance(result, dict):
        confidence = result.get("confidence")
    return (answer, _extract_evidence_ids(raw_evidence), confidence)


def _extract_evidence_ids(raw_evidence) -> list:
    """Map evidence objects/dicts onto artifact ids for the lineage view."""
    evidence_ids = []
    for ev in raw_evidence:
        if isinstance(ev, dict):
            eid = ev.get("artifact_id") or ev.get("source_id")
        else:
            eid = getattr(ev, "artifact_id", None) or getattr(ev, "source_id", None)
        if eid:
            evidence_ids.append(eid)
    return evidence_ids


def _print_ask_local_json(
    project, project_id, question, answer, confidence, evidence_ids, lineage_cache
) -> None:
    """Print the JSON form of the local ask answer."""
    print(
        json.dumps(
            {
                "project_id": project_id,
                "question": question,
                "answer": answer,
                "confidence": confidence,
                "evidence_ids": evidence_ids,
                "citations": _ask_resolve_evidence(project, evidence_ids, lineage_cache),
            },
            indent=2,
            default=str,
        )
    )


def _ask_remote(args, project_id: str, question: str) -> None:
    """Gateway/UMRS knowledge verify path (remote)."""
    try:
        from r4.client import Client as R4Client
        from r4.knowledge import KnowledgeClient
    except ImportError:
        from ._helpers import _die

        _die(
            "r4 client package required.",
            error_id="client.package_missing",
        )

    client = R4Client()
    knowledge = KnowledgeClient(client)

    try:
        result = knowledge.verify(project_id, question)
    except Exception as exc:
        from ._helpers import _die

        _die(
            f"Knowledge verify failed: {exc}",
            error_id="knowledge.verify_failed",
        )

    answer = result.get("answer", result.get("response", "No answer returned."))
    evidence_ids = result.get("evidence_ids", result.get("evidence", []))
    confidence = result.get("confidence", None)
    reasoning = result.get("reasoning_path", result.get("reasoning", ""))

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "question": question,
                    "answer": answer,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "evidence_ids": evidence_ids,
                },
                indent=2,
                default=str,
            )
        )
        return

    print(f"Q: {question}")
    print(f"A: {answer}")
    if reasoning:
        print(f"Reasoning: {reasoning}")
    if confidence is not None:
        print(f"Confidence: {confidence:.2f}")
    if evidence_ids:
        print(f"\nCitations ({len(evidence_ids)}):")
        lineage_cache = _fetch_lineage_cache(knowledge.get_lineage, project_id, evidence_ids)
        for eid in evidence_ids:
            art = {
                "artifact_id": eid,
                "kind": "unknown",
                "title": eid[:24],
            }
            print(f"  {_render_citation(art, lineage_cache)}")
    else:
        print("\n(no citations — answer is general knowledge)")


def project_readiness(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Generate a research readiness report for the project."""
    project_id = _resolve_project_id(args, pm)
    data_dir = pm.data_dir(project_id)
    project = _build_research_project(pm, project_id)

    restored = load_artifact_manifest(project, data_dir)
    if restored == 0 and not project.knowledge_store.list_artifacts():
        print(
            f"✗ No artifacts for {project_id}. Run: r4 project ingest {project_id} <dirs...>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not project.extracted_knowledge:
        project.understand()

    report = project.check_readiness()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "readiness_level": report.readiness_level.value,
                    "confidence_level": report.confidence_level.value,
                    "critical_issues": report.critical_issue_count,
                    "recommendation": report.recommendation,
                    "next_steps": report.next_steps,
                },
                indent=2,
            )
        )
        return

    print(f"Readiness: {report.readiness_level.value}")
    print(f"Confidence: {report.confidence_level.value}")
    print(f"Critical issues: {report.critical_issue_count}")
    print(f"Recommendation: {report.recommendation}")


def project_plan(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Generate a graph-grounded research plan for the project."""
    project_id = _resolve_project_id(args, pm)
    data_dir = pm.data_dir(project_id)
    project = _build_research_project(pm, project_id)

    restored = load_artifact_manifest(project, data_dir)
    if restored == 0 and not project.knowledge_store.list_artifacts():
        print(
            f"✗ No artifacts for {project_id}. Run: r4 project ingest {project_id} <dirs...>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not project.extracted_knowledge:
        project.understand()

    plan = project.plan_research(args.research_goal)
    save_research_plan(data_dir, plan, args.research_goal)
    workflows = _registered_workflow_names(project_id)

    if getattr(args, "json", False):
        payload = plan.to_dict()
        payload["research_goal"] = args.research_goal
        payload["registered_workflows"] = workflows
        print(json.dumps(payload, indent=2))
        return

    print(f"✓ Research plan for {project_id}")
    print(
        f"  Objectives: {len(plan.objectives)}, "
        f"hypotheses: {len(plan.hypotheses)}, "
        f"experiments: {len(plan.experiments)}"
    )
    print(f"  Success probability: {plan.success_probability:.2f}")
    if plan.source_knowledge_ids:
        print(f"  Grounded in {len(plan.source_knowledge_ids)} knowledge artifact(s)")
    for line in _workflow_suggestion_lines(project_id, workflows):
        print(line)


def _registered_workflow_names(project_id: str) -> list[str] | None:
    """Registered UMRS workflow names (None when the catalog is unreachable).

    Best-effort discovery at plan time (G-P1-3): the scientist should never
    have to know an internal registry identifier to start the loop.
    """
    try:
        from ..client import Client

        items = Client(project_id=project_id).workflows.list_workflows()
    except Exception:  # noqa: BLE001 — a suggestion, never a gate
        return None
    return sorted({str(getattr(w, "name", "") or "") for w in items} - {""})


def _workflow_suggestion_lines(project_id: str, names: list[str] | None) -> list[str]:
    """What to run next, phrased from the workflow catalog state."""
    start = f"r4 project loop start {project_id}"
    if names is None:
        return [
            "  Workflows: catalog unreachable — discover with: r4 workflow list",
            f"  Next: {start} --workflow <name>",
        ]
    if not names:
        return [
            "  Workflows: none registered — register one first: "
            "r4 workflow upload <bundle> --name <name>",
            f"  Then: {start} --workflow <name>",
        ]
    if len(names) == 1:
        return [
            f"  Workflow: {names[0]} (the only registered one; loop start uses it automatically)",
            f"  Next: {start}",
        ]
    shown = ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
    return [f"  Workflows registered: {shown}", f"  Next: {start} --workflow <name>"]


def project_verify_job(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Submit a knowledge verification durable job.

    This uses the durable job layer (k2) rather than the M3.5 in-memory model.
    """
    project_id = _resolve_project_id(args, pm)
    from ..client import Client

    client = Client()
    verifier_names = getattr(args, "verifier_names", None) or None
    detach = getattr(args, "detach", False)

    result = client.knowledge.submit_verify(
        project_id=project_id,
        verifier_names=verifier_names,
    )

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str))
        return

    job_id = result.get("job_id", "unknown")
    job_type = result.get("job_type", "knowledge_verify")
    status = result.get("status", "queued")

    print(f"✓ Verification job submitted for {project_id}")
    print(f"  Job ID: {job_id}")
    print(f"  Job type: {job_type}")
    print(f"  Status: {status}")

    if not detach and status == "queued":
        print("  Waiting for completion...")
        _wait_for_job(client, job_id, args)


def _wait_for_job(client: Any, job_id: str, args: Namespace) -> None:
    """Poll a job until it completes or fails."""
    import threading

    from .._cli.project_knowledge import _progress_spinner

    done_event = threading.Event()
    spinner = threading.Thread(target=_progress_spinner, args=(done_event,), daemon=True)
    spinner.start()

    max_attempts = 120
    for i in range(max_attempts):
        import time

        time.sleep(5)
        try:
            result = client.knowledge.verify_job_status(job_id)
            status = result.get("status", result.get("job_status", "unknown"))

            if status in ("completed", "succeeded", "COMPLETED", "SUCCEEDED"):
                done_event.set()
                spinner.join(timeout=2)
                report_id = result.get("report_id") or result.get("attempt_outputs", {}).get(
                    "report_id"
                )
                print(f"\n✓ Verification complete (job {job_id})")
                if report_id:
                    print(f"  Report: {report_id}")
                if getattr(args, "json", False):
                    print(json.dumps(result, indent=2, default=str))
                return
            elif status in ("failed", "canceled", "FAILED", "CANCELED", "LOST"):
                done_event.set()
                spinner.join(timeout=2)
                print(f"\n✗ Verification failed (job {job_id}): {status}", file=sys.stderr)
                if getattr(args, "json", False):
                    print(json.dumps(result, indent=2, default=str))
                return
        except Exception:
            pass

    done_event.set()
    spinner.join(timeout=2)
    print("\n✗ Timeout waiting for job", file=sys.stderr)


def project_transition(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Transition a finding to a new status."""
    project_id = _resolve_project_id(args, pm)
    from ..client import Client

    client = Client()
    finding_key = args.finding_key
    from_status = getattr(args, "from_status", "open")
    new_status = args.new_status
    evidence = getattr(args, "evidence", "")

    result = client.knowledge.transition_finding(
        project_id=project_id,
        finding_key=finding_key,
        from_status=from_status,
        new_status=new_status,
        evidence=evidence,
    )

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"✓ Finding {finding_key} transitioned: {from_status} → {new_status}")


def _load_plan_from_file(plan_file: str) -> dict:
    """Load a research plan from a JSON file."""
    from pathlib import Path

    plan_path = Path(plan_file)
    if not plan_path.is_file():
        print(f"✗ Plan file not found: {plan_file}", file=sys.stderr)
        raise SystemExit(1)
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _generate_plan_from_project(pm: ProjectManager, project_id: str) -> dict:
    """Generate a research plan from the project's saved goal + knowledge."""
    data_dir = pm.data_dir(project_id)
    project = _build_research_project(pm, project_id)

    restored = load_artifact_manifest(project, data_dir)
    if restored == 0 and not project.knowledge_store.list_artifacts():
        print(
            f"✗ No artifacts for {project_id}. Run: r4 project import {project_id} <dirs...>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not project.extracted_knowledge:
        project.understand()

    saved_goal = load_research_goal(data_dir)
    if not saved_goal:
        print("No saved research goal. Please run 'r4 project plan' first,")
        print("or provide --plan-file with a plan JSON file.")
        raise SystemExit(1)

    plan = project.plan_research(saved_goal)
    return plan.to_dict() if hasattr(plan, "to_dict") else plan


def _write_workflow_draft(result: dict, emit_out: str, quiet: bool) -> None:
    """Write an emitted workflow draft to a YAML file."""
    from pathlib import Path

    import yaml

    output_path = Path(emit_out)
    draft_data = {
        "workflow_id": result.get("workflow_id", ""),
        "name": result.get("name", ""),
        "steps": result.get("steps", []),
        "project_context": result.get("project_context", {}),
    }
    output_path.write_text(
        yaml.dump(draft_data, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )
    if not quiet:
        print(f"✓ Workflow draft written: {output_path}")


def _print_emit_summary(result: dict, project_id: str) -> None:
    """Print a human-readable workflow draft summary."""
    steps = result.get("steps", [])
    meta = result.get("_meta", {})
    print(f"✓ Workflow draft for {project_id}")
    print(f"  ID: {result.get('workflow_id', 'unknown')}")
    print(f"  Name: {result.get('name', 'Untitled')}")
    print(
        f"  Steps: {meta.get('step_count', len(steps))} auto, {meta.get('manual_count', 0)} manual"
    )
    if meta.get("has_manual", False):
        print("  ⚠ Contains manual placeholder steps — review before execution")


def project_emit_workflow(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Emit a workflow draft from a research plan.

    Accepts a plan file or generates one from the project's knowledge.
    Writes the workflow draft YAML to the specified output file.
    """
    from ..client import Client

    project_id = _resolve_project_id(args, pm)

    plan_file = getattr(args, "plan_file", None)
    if plan_file:
        plan = _load_plan_from_file(plan_file)
    else:
        plan = _generate_plan_from_project(pm, project_id)

    client = Client()
    result = client.knowledge.emit_workflow(
        plan=plan,
        project_id=project_id,
        model=getattr(args, "model", None),
        partition=getattr(args, "partition", None),
        account=getattr(args, "account", None),
    )

    as_json = getattr(args, "json", False)
    emit_out = getattr(args, "emit_workflow", None)
    if emit_out:
        _write_workflow_draft(result, emit_out, quiet=as_json)

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    elif not emit_out:
        _print_emit_summary(result, project_id)


# ── Research sessions (/gw/research/sessions API, ADR-025) ─────────────────


def project_sessions(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """List or inspect durable research sessions (ADR-025)."""
    from ..client import Client

    action = getattr(args, "session_action", None) or "list"
    target = getattr(args, "target", None) or _resolve_project_id(args, pm)
    client = Client()

    if action == "list":
        data = client.list_research_sessions(
            target,
            status=getattr(args, "status", None),
        )
        _print_sessions_list(data, target, as_json=getattr(args, "json", False))
        return

    data = client.get_research_session(target)
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    _print_session_detail(data)


def _print_sessions_list(data: dict, target: str, *, as_json: bool = False) -> None:
    """Render the session list for ``project sessions list``."""
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    sessions = data.get("sessions") or []
    if not sessions:
        print(f"No research sessions on project {target}.")
        return
    for item in sessions:
        title = item.get("title") or ""
        status = item.get("status", "?")
        count = len(item.get("loops") or [])
        print(f"  {item.get('session_id')}  {status:10s}  {count} loop(s)  {title}")


def _print_session_detail(data: dict) -> None:
    """Render one durable research session for ``project sessions view``."""
    session = data.get("session") or {}
    print(f"Session {session.get('session_id')} — project {session.get('project_id')}")
    print(
        f"  Title: {session.get('title') or '(untitled)'}  "
        f"Status: {session.get('status', '?')}  "
        f"Runtime: {session.get('authoritative_runtime', '?')}"
    )
    print(f"  Goal: {session.get('research_goal') or '(none)'}")
    loops = session.get("loops") or []
    if not loops:
        return
    print("  Loops:")
    for loop_ref in loops:
        print(
            f"    {loop_ref.get('loop_id')}  venue={loop_ref.get('venue', '?')}  "
            f"role={loop_ref.get('loop_role', '?')}"
        )


# ── Autonomous research loop control (/gw/loops API) ───────────────────────

_LOOP_TERMINAL_STATUSES = {"completed", "stopped", "failed", "cancelled"}


def _loop_target(args: Namespace) -> str | None:
    """Positional target: project_id for ``start``, loop_id otherwise."""
    return getattr(args, "target", None) or getattr(args, "project_id", None)


def _print_loop_status(payload: dict[str, Any]) -> None:
    print(f"Loop {payload.get('loop_id', '?')} — project {payload.get('project_id', '?')}")
    print(
        "  Status: "
        f"{payload.get('status', '?')}  phase: {payload.get('phase', '?')}  "
        f"iteration {payload.get('current_iteration', 0)}/{payload.get('max_iterations', 0)}"
    )
    print(
        "  Hypotheses: "
        f"{payload.get('total_hypotheses_tested', 0)}  "
        f"experiments: {payload.get('total_experiments_run', 0)}  "
        f"findings: {payload.get('total_findings', 0)}  "
        f"success: {payload.get('success_rate', 0):.0%}"
    )


_LOOP_TARGETED_ACTIONS = {"status", "iterations", "watch", "stop", "pause", "resume", "replay"}


def project_loop(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """Control autonomous research loops through the resolved ResearchRuntime.

    Default mode ``umrs`` preserves the historical /gw/loops behavior;
    ``--runtime local`` runs the SQLite local runtime with no gateway.
    """
    action = getattr(args, "loop_action", None) or getattr(args, "action", None) or "status"
    target = _loop_target(args)
    as_json = getattr(args, "json", False)
    runtime = _resolve_loop_runtime(args)

    if action == "start":
        _loop_start(args, pm, runtime, target, as_json)
        return
    if action == "list":
        _loop_list(runtime, as_json)
        return
    if action == "approvals":
        _loop_approvals(runtime, target, as_json)
        return

    if not target:
        from ._helpers import _die

        _die(
            "Loop ID required",
            error_id="project.loop_id_required",
        )

    if action == "decide":
        _loop_decide(args, runtime, target, as_json)
        return
    if action in _LOOP_TARGETED_ACTIONS:
        _loop_targeted(action, args, runtime, target, as_json)
        return

    print(
        f"✗ Unknown loop action '{action}'. "
        "Use: start, status, list, iterations, watch, stop, pause, resume, approvals, decide",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _resolve_loop_runtime(args: Namespace):
    """Resolve the ResearchRuntime for a ``project loop`` invocation."""
    from ..client import Client
    from ..runtime import get_runtime

    project_hint = getattr(args, "target", None)
    return get_runtime(
        mode=getattr(args, "runtime", None),
        project_id=project_hint,
        client_factory=Client,
    )


def _parse_cli_requires(raw: list[str] | None) -> dict[str, str]:
    """KEY=VALUE pairs → requires mapping; bare words become True."""
    out: dict[str, str] = {}
    for item in raw or []:
        key, _, value = item.partition("=")
        out[key.strip()] = value
    return out


def _loop_spec_from_args(args, project_id: str, goal: str):
    """The LoopSpec one ``r4 project loop start`` invocation asks for."""
    from ..runtime import LoopSpec

    return LoopSpec(
        project_id=project_id,
        research_goal=goal,
        max_iterations=getattr(args, "max_iterations", 10),
        approved=getattr(args, "approve", False),
        requires=_parse_cli_requires(getattr(args, "requires", None)),
        session_id=getattr(args, "session", "") or "",
        workflow_name=getattr(args, "workflow", "") or "",
        max_tracks=int(getattr(args, "tracks", 1) or 1),
    )


def _loop_start(args, pm, runtime, target, as_json) -> None:
    """Handle ``r4 project loop start``."""
    project_id = target or _resolve_project_id(args, pm)
    goal = getattr(args, "goal", None)
    if not goal:
        goal = load_research_goal(pm.data_dir(project_id))
    if not goal:
        print(
            f'✗ No research goal. Provide one or run: r4 project plan {project_id} "<goal>"',
            file=sys.stderr,
        )
        raise SystemExit(1)
    state = runtime.start_loop(_loop_spec_from_args(args, project_id, goal))
    resp = state.to_dict()
    if as_json:
        print(json.dumps(resp, indent=2, default=str))
    else:
        print(f"✓ Loop started: {resp.get('loop_id')}")
        print(f"  {resp.get('message', 'running')} (status: {resp.get('status')})")
        workflow = (resp.get("metadata") or {}).get("workflow_name")
        if workflow:
            print(f"  workflow: {workflow} (each experiment runs this registered UMRS workflow)")
        print(f"  watch: r4 project loop watch {resp.get('loop_id')}")


def _loop_list(runtime, as_json) -> None:
    """Handle ``r4 project loop list``."""
    loops = [state.to_dict() for state in runtime.list_loops()]
    if as_json:
        print(json.dumps({"loops": loops}, indent=2, default=str))
    elif not loops:
        print("No loops registered on the platform.")
    else:
        for item in loops:
            print(
                f"  {item.get('loop_id', '?')}  "
                f"status={item.get('status', '?')}  phase={item.get('phase', '?')}"
            )


def _loop_targeted(action, args, runtime, target, as_json) -> None:
    """Dispatch actions that operate on a single loop ID."""
    if action == "status":
        payload = runtime.get_loop(target).to_dict()
        if as_json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_loop_status(payload)
        return
    if action == "iterations":
        _loop_iterations(runtime, target, as_json)
        return
    if action == "watch":
        _loop_watch(args, runtime, target)
        return
    if action == "replay":
        _loop_replay(runtime, target, as_json)
        return
    _loop_stop_pause_resume(action, runtime, target, as_json)


def _loop_iterations(runtime, target, as_json) -> None:
    """Handle ``r4 project loop iterations``."""
    iterations = [state.to_dict() for state in runtime.list_iterations(target)]
    if as_json:
        print(json.dumps({"iterations": iterations}, indent=2, default=str))
    elif not iterations:
        print(f"Loop {target}: no iterations yet.")
    else:
        for it in iterations:
            print(
                f"  #{it.get('iteration_number', '?')} {it.get('status', '?')} "
                f"hypothesis: {str(it.get('hypothesis', ''))[:80]}"
            )


def _loop_replay(runtime, target, as_json) -> None:
    """Handle ``r4 project loop replay`` (D-52): one trace id through loop,
    iterations and knowledge writes — replayed chronologically.

    Read-only: everything comes from the durable loop record + the approval
    inbox; the trace id is the loop id (stamped at start, D-52 server leg).
    """
    loop = runtime.get_loop(target).to_dict()
    metadata = loop.get("metadata") or {}
    iterations = [state.to_dict() for state in runtime.list_iterations(target, limit=200)]
    approvals = runtime.list_approvals(loop_id=target)
    trace_id = metadata.get("trace_id", target)

    if as_json:
        print(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "companion_session_id": metadata.get("companion_session_id", ""),
                    "loop": loop,
                    "iterations": iterations,
                    "approvals": approvals,
                },
                indent=2,
                default=str,
            )
        )
        return

    for line in _replay_lines(trace_id, loop, metadata, iterations, approvals):
        print(line)


def _replay_origin_lines(metadata) -> list[str]:
    """Hosting job + the companion session that started the loop (D-52)."""
    labels = (
        ("durable_job_id", "hosting job"),
        ("companion_session_id", "started from companion session"),
    )
    return [f"  {label}: {metadata[key]}" for key, label in labels if metadata.get(key)]


def _replay_lines(trace_id, loop, metadata, iterations, approvals) -> list[str]:
    """Text rendering of one replay (kept branch-light per ADR-052)."""
    lines = [f"Replay trace {trace_id}"]
    lines.append(
        f"  goal: {loop.get('research_goal', '?')[:80]}"
        f"  status={loop.get('status', '?')}"
        f"  driver={metadata.get('driver', '?')}"
        f"  venue={metadata.get('venue', 'frontend')}"
    )
    lines.extend(_replay_origin_lines(metadata))
    if not iterations:
        lines.append("  (no iterations)")
    for it in iterations:
        lines.append(
            f"  #{it.get('iteration_number', '?')} {it.get('phase', '?')}"
            f" {it.get('status', '?')}  {str(it.get('hypothesis', ''))[:70]}"
        )
        summary = it.get("summary") or {}
        if summary:
            keys = ", ".join(sorted(str(k) for k in summary.keys())[:5])
            lines.append(f"      results: {keys}")
    for ap in approvals:
        action = ap.get("action") or {}
        lines.append(
            f"  [gate] {ap.get('id', '?')} {ap.get('status', '?')}"
            f"  kind={action.get('kind', '?')} iter={action.get('iteration', '?')}"
        )
    return lines


def _loop_watch(args, runtime, target) -> None:
    """Handle ``r4 project loop watch`` — poll until terminal or timeout."""
    import time

    from ._helpers import _die

    interval = getattr(args, "interval", 5.0)
    last_key = None
    remaining = 720  # safety bound (~1 hour at 5s)
    try:
        while remaining > 0:
            payload = runtime.get_loop(target).to_dict()
            key = (
                payload.get("status"),
                payload.get("phase"),
                payload.get("current_iteration"),
                payload.get("metadata", {}).get("total_findings"),
            )
            if key != last_key:
                _print_loop_status(payload)
                last_key = key
            if str(payload.get("status", "")).lower() in _LOOP_TERMINAL_STATUSES:
                print(f"✓ Loop {target} finished (status: {payload.get('status')})")
                return
            remaining -= 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n  stopped watching loop {target} (runtime keeps running)")
        return

    _die(
        f"Watch timed out — loop {target} still running.",
        error_id="project.loop_watch_timeout",
        code=2,
    )


def _loop_approvals(runtime, target, as_json) -> None:
    """Handle ``r4 project loop approvals [LOOP_ID]`` — the durable inbox (ADR-036 (a))."""
    rows = runtime.list_approvals(target)
    if as_json:
        print(json.dumps({"pending": rows}, indent=2, default=str))
        return
    if not rows:
        print("No pending approvals.")
        return
    for row in rows:
        action = row.get("action") or {}
        print(
            f"  {row.get('id', '?')}  loop {row.get('loop_id', '?')} "
            f"iteration {row.get('iteration', '?')}: "
            f"{action.get('kind', '?')} {action.get('workflow_name', '')}".rstrip()
        )
    print("Decide with: r4 project loop decide APPROVAL_ID --decision {once|always|no}")


def _loop_decide(args, runtime, target, as_json) -> None:
    """Handle ``r4 project loop decide APPROVAL_ID --decision …``."""
    decision = getattr(args, "decision", None)
    if not decision:
        from ._helpers import _die

        _die(
            "--decision {once|always|no} required",
            error_id="project.loop_decision_required",
        )
    resp = runtime.decide_approval(target, decision)
    if as_json:
        print(json.dumps(resp, indent=2, default=str))
    else:
        print(
            f"✓ {decision}: approval {target} → {resp.get('status', 'ok')} "
            f"(loop {resp.get('loop_id', '?')})"
        )


def _loop_stop_pause_resume(action, runtime, target, as_json) -> None:
    """Handle stop/pause/resume — same shape for all three verbs."""
    fn = {
        "stop": runtime.stop_loop,
        "pause": runtime.pause_loop,
        "resume": runtime.resume_loop,
    }[action]
    state = fn(target)
    resp = state.to_dict()
    if as_json:
        print(json.dumps(resp, indent=2, default=str))
    else:
        msg = resp.get("message", "")
        print(f"✓ {action}: loop {target} → {resp.get('status', 'ok')} {msg}")


def project_ask_policy(
    args: Namespace,
    pm: ProjectManager,
    _active_profile=None,
    _reload_config=None,
    _project_config=None,
) -> None:
    """``r4 project ask_policy``: show, or set (stage / explicit / clear), the
    project's persisted ask-the-scientist default (D-33, ADR-066 amendment 3)."""
    from ..client import Client

    project_id = _resolve_project_id(args, pm)
    client = Client(project_id=project_id)
    try:
        payload = _apply_ask_policy_args(client, project_id, args)
    except Exception as exc:  # noqa: BLE001 — named CLI failure
        from ._helpers import _die

        _die(
            f"ask policy failed: {exc}",
            action="project.ask_policy",
            error_id="project.ask_policy.failed",
        )
        return
    project = payload.get("project", payload)
    if getattr(args, "json", False):
        print(json.dumps(project, indent=2, default=str))
        return
    stage = project.get("maturity_stage") or "(none)"
    mode = project.get("ask_mode") or "(request default)"
    print(f"{project_id}: stage={stage} mode={mode}")
    triggers = project.get("pause_triggers") or []
    if triggers:
        print(f"  pauses for: {', '.join(triggers)}")


def _apply_ask_policy_args(client, project_id: str, args: Namespace) -> dict:
    """GET when no change was asked for; otherwise the PATCH receipt."""
    if getattr(args, "stage", None):
        return client.set_project_ask_policy(project_id, maturity_stage=args.stage)
    if getattr(args, "mode", None):
        triggers = [t.strip() for t in (args.triggers or "").split(",") if t.strip()]
        return client.set_project_ask_policy(
            project_id, ask_mode=args.mode, pause_triggers=triggers
        )
    if getattr(args, "clear", False):
        return client.set_project_ask_policy(project_id)
    return client.get_platform_project(project_id)
