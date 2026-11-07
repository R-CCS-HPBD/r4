"""Client-side workflow lint — schema + wiring checks (Phase 2l, 2026-07-14).

Runs local validation against r4-contracts WorkflowDraft/StepDraft DTOs
before sending to the server. Output is compiler-style for easy IDE
integration.

Every diagnostic emitted here is also produced by the server lint engine,
so the client set is a strict subset of the server set (no false positives).

Pipeline bundles (2026-10-02): a directory holding ``workflow.json``, or a
``workflow.json`` whose steps name entrypoints, is checked against the
upload contract instead (``r4_contracts.workflow_package`` — the same
function UMRS refuses uploads with): a description and every step's
``depends_on`` are mandatory; advice comes as warnings.

Usage
-----
    from r4.workflow_lint import lint_workflow_draft, format_diagnostics
    diagnostics = lint_workflow_draft(workflow)
    print(format_diagnostics(diagnostics))  # file:line:col: severity: message
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# ── Diagnostic data model (mirrors r4_contracts.LintDiagnostic) ──────────────


@dataclass
class LintDiagnostic:
    """A single client-side lint diagnostic."""

    file_path: str
    line: int
    column: int
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "file": self.file_path,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }

    def to_compiler_line(self) -> str:
        """Format as `file:line:col: severity: message`."""
        return (
            f"{self.file_path}:{self.line}:{self.column}: "
            f"{self.severity}: [{self.code}] {self.message}"
        )


# ── UUID validation (RFC 4122 v4) ────────────────────────────────────────────

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_valid_uuid4(value: str) -> bool:
    """Check whether *value* is a UUID v4 string."""
    return bool(_UUID4_RE.match(value))


# ── Step kind validator ──────────────────────────────────────────────────────

_VALID_STEP_KINDS: frozenset[str] = frozenset(
    ("rag_index", "vector_index", "graph_build", "catalog", "scan"),
)


# ── Public API ───────────────────────────────────────────────────────────────


def lint_workflow_draft(
    workflow: Any,
    *,
    file_path: str = "",
) -> list[LintDiagnostic]:
    """Validate a WorkflowDraft (or dict-equivalent) locally.

    Checks: workflow_id, name, steps[].
    Every diagnostic here is also produced by the server lint engine.

    Args:
        workflow: A r4_contracts.WorkflowDraft instance or a dict with the
                  same keys.
        file_path: Source file name for diagnostic output.

    Returns:
        List of LintDiagnostic (empty == valid).
    """
    diagnostics: list[LintDiagnostic] = []

    if workflow is None:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E001_null",
                message="Workflow draft is null",
                severity="error",
            )
        )
        return diagnostics

    # Accept both real DTO and plain dict
    if hasattr(workflow, "workflow_id"):
        wf_id = getattr(workflow, "workflow_id", None)
        name = getattr(workflow, "name", None)
        steps = getattr(workflow, "steps", None)
    else:
        wf_id = workflow.get("workflow_id") if isinstance(workflow, dict) else None
        name = workflow.get("name") if isinstance(workflow, dict) else None
        steps = workflow.get("steps") if isinstance(workflow, dict) else None

    _check_workflow_id(wf_id, file_path, diagnostics)
    _check_name(name, file_path, diagnostics)
    _check_steps(steps, file_path, diagnostics)

    return diagnostics


def _check_workflow_id(wf_id: Any, file_path: str, diagnostics: list[LintDiagnostic]) -> None:
    """Check workflow_id field."""
    if wf_id is None:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E101_missing_workflow_id",
                message="workflow_id is required",
                severity="error",
            )
        )
    elif not isinstance(wf_id, str) or not _is_valid_uuid4(wf_id):
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E102_bad_workflow_id",
                message=f"workflow_id must be a valid UUID v4, got {wf_id!r}",
                severity="error",
            )
        )


def _check_name(name: Any, file_path: str, diagnostics: list[LintDiagnostic]) -> None:
    """Check name field."""
    if name is None:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E201_missing_name",
                message="name is required",
                severity="error",
            )
        )
    elif not isinstance(name, str) or not name.strip():
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E202_bad_name",
                message=f"name must be a non-empty string, got {name!r}",
                severity="error",
            )
        )


def _check_steps(steps: Any, file_path: str, diagnostics: list[LintDiagnostic]) -> None:
    """Check steps field."""
    if steps is None:
        return

    if not isinstance(steps, (list, tuple)):
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E301_bad_steps_type",
                message="steps must be a list of StepDraft or dict",
                severity="error",
            )
        )
        return

    for idx, step in enumerate(steps):
        step_diag = _lint_step(step, idx, file_path)
        diagnostics.extend(step_diag)


def _lint_step(
    step: Any,
    index: int,
    file_path: str,
) -> list[LintDiagnostic]:
    """Validate a single StepDraft or dict-equivalent step."""
    diagnostics: list[LintDiagnostic] = []
    base = f"[steps.{index}]"

    if step is None:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E302_null_step",
                message=f"{base}: step is null",
                severity="error",
            )
        )
        return diagnostics

    # Accept real DTO or plain dict
    if hasattr(step, "step_id"):
        step_id = getattr(step, "step_id", None)
        kind = getattr(step, "kind", None)
        config = getattr(step, "config", None)
    else:
        step_id = step.get("step_id") if isinstance(step, dict) else None
        kind = step.get("kind") if isinstance(step, dict) else None
        config = step.get("config") if isinstance(step, dict) else None

    # step_id
    if step_id is None:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E303_missing_step_id",
                message=f"{base}: step_id is required",
                severity="error",
            )
        )
    elif not isinstance(step_id, str) or not _is_valid_uuid4(step_id):
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E304_bad_step_id",
                message=f"{base}: step_id must be a valid UUID v4, got {step_id!r}",
                severity="error",
            )
        )

    # kind
    if kind is None:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E305_missing_kind",
                message=f"{base}: kind is required",
                severity="error",
            )
        )
    elif kind not in _VALID_STEP_KINDS:
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E306_bad_kind",
                message=(f"{base}: kind must be one of {sorted(_VALID_STEP_KINDS)}, got {kind!r}"),
                severity="error",
            )
        )

    # config — must be a dict if provided
    if config is not None and not isinstance(config, dict):
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code="E307_bad_config",
                message=f"{base}: config must be a dict, got {type(config).__name__}",
                severity="error",
            )
        )

    # --- reference integrity (client-side format check only) ──────────
    # The client cannot verify existence without a server call; it only
    # validates that reference fields look like valid identifiers.
    _check_reference_integrity(step, index, file_path, diagnostics)

    return diagnostics


def _check_reference_integrity(
    step: Any,
    index: int,
    file_path: str,
    diagnostics: list[LintDiagnostic],
) -> None:
    """Validate reference fields inside a step config.

    Client-side only: checks format, not existence.
    """
    if step is None:
        return

    # Accept DTO or dict
    if hasattr(step, "config"):
        config = getattr(step, "config", None)
    else:
        config = step.get("config") if isinstance(step, dict) else None

    if config is None or not isinstance(config, dict):
        return

    base = f"[steps.{index}]"
    _check_uuid_ref(
        config,
        "job_id",
        base,
        file_path,
        diagnostics,
        "E401_bad_job_id_type",
        "E402_bad_job_id_format",
    )
    _check_uuid_ref(
        config,
        "parent_workflow_id",
        base,
        file_path,
        diagnostics,
        "E403_bad_parent_wf_id_type",
        "E404_bad_parent_wf_id_format",
    )
    _check_uuid_ref(
        config,
        "artifact_id",
        base,
        file_path,
        diagnostics,
        "E405_bad_artifact_id_type",
        "E406_bad_artifact_id_format",
    )


def _check_uuid_ref(
    config: dict,
    key: str,
    base: str,
    file_path: str,
    diagnostics: list[LintDiagnostic],
    err_type: str,
    err_format: str,
) -> None:
    """Check one UUID-reference field in *config* for type/format errors."""
    value = config.get(key)
    if value is None:
        return
    if not isinstance(value, str):
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code=err_type,
                message=f"{base}: {key} must be a string, got {type(value).__name__}",
                severity="error",
            )
        )
        return
    if not _is_valid_uuid4(value):
        diagnostics.append(
            LintDiagnostic(
                file_path=file_path,
                line=0,
                column=0,
                code=err_format,
                message=f"{base}: {key} must be a valid UUID v4, got {value!r}",
                severity="error",
            )
        )


def _is_pipeline_bundle(data: Any) -> bool:
    """A workflow.json (a source, or steps naming entrypoints/gates/edges),
    not a knowledge-indexing draft (drafts carry a workflow_id)."""
    if not isinstance(data, dict) or "workflow_id" in data:
        return False
    if "source" in data:
        return True
    steps = data.get("steps")
    return isinstance(steps, list) and any(
        isinstance(st, dict) and bool({"entrypoint", "gate", "depends_on"} & st.keys())
        for st in steps
    )


def _problem_line(text: str, where: str) -> int:
    """1-based line a contract problem points at (its step's name, or its key)."""
    lines = [line.replace('":"', '": "') for line in text.splitlines()]
    step = re.match(r'steps\[\d+\] "([^"]+)"', where)
    if step:
        start = next((i for i, line in enumerate(lines) if '"steps"' in line), 0)
        needle = f'"name": "{step.group(1)}"'
    else:
        start, needle = 0, f'"{where.split(".")[0].split("[")[0]}"'
    for number in range(start, len(lines)):
        if needle in lines[number]:
            return number + 1
    return 1


def lint_bundle_manifest(text: str, *, file_path: str) -> list[LintDiagnostic]:
    """The upload contract for one workflow.json text, as diagnostics."""
    import json

    from r4_contracts.workflow_package import workflow_package_problems

    return [
        LintDiagnostic(
            file_path=file_path,
            line=_problem_line(text, problem.where),
            column=1,
            code=problem.code,
            message=str(problem),
            severity=problem.severity,
        )
        for problem in workflow_package_problems(json.loads(text))
    ]


def lint_json_file(
    filepath: str,
) -> list[LintDiagnostic]:
    """Lint a WorkflowDraft JSON file, or a pipeline bundle (a directory
    holding workflow.json, or the workflow.json itself) against the upload
    contract.

    Args:
        filepath: Path to the JSON file or bundle directory.

    Returns:
        List of LintDiagnostic (empty == valid).
    """
    import json
    import os

    if os.path.isdir(filepath):
        filepath = os.path.join(filepath, "workflow.json")
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            text = fh.read()
        data = json.loads(text)
    except FileNotFoundError:
        return [
            LintDiagnostic(
                file_path=filepath,
                line=0,
                column=0,
                code="E901_file_not_found",
                message=f"File not found: {filepath}",
                severity="error",
            )
        ]
    except json.JSONDecodeError as exc:
        return [
            LintDiagnostic(
                file_path=filepath,
                line=exc.lineno or 0,
                column=exc.colno or 0,
                code="E902_json_parse_error",
                message=f"Invalid JSON: {exc.msg}",
                severity="error",
            )
        ]

    if _is_pipeline_bundle(data):
        return lint_bundle_manifest(text, file_path=filepath)

    diagnostics: list[LintDiagnostic] = []
    if isinstance(data, list):
        for i, item in enumerate(data):
            diagnostics.extend(lint_workflow_draft(item, file_path=filepath))
    else:
        diagnostics.extend(lint_workflow_draft(data, file_path=filepath))

    return diagnostics


def format_diagnostics(
    diagnostics: Sequence[LintDiagnostic],
    *,
    include_source: Optional[str] = None,
) -> str:
    """Format diagnostics as compiler-style output.

    Args:
        diagnostics: List of diagnostics to format.
        include_source: If set, appends a source label (client/server).

    Returns:
        Multi-line string of compiler-style lines.
    """
    if not diagnostics:
        return "OK: no diagnostics."

    lines: list[str] = []
    for diag in diagnostics:
        line = diag.to_compiler_line()
        if include_source:
            line += f"  [{include_source}]"
        lines.append(line)

    return "\n".join(lines)


# ── Contract: client diagnostics ⊆ server diagnostics ────────────────────────

CLIENT_RULES: frozenset[str] = frozenset(
    (
        # Workflow-level
        "E101_missing_workflow_id",
        "E102_bad_workflow_id",
        "E201_missing_name",
        "E202_bad_name",
        # Steps
        "E301_bad_steps_type",
        "E302_null_step",
        "E303_missing_step_id",
        "E304_bad_step_id",
        "E305_missing_kind",
        "E306_bad_kind",
        "E307_bad_config",
        # Reference integrity
        "E401_bad_job_id_type",
        "E402_bad_job_id_format",
        "E403_bad_parent_wf_id_type",
        "E404_bad_parent_wf_id_format",
        "E405_bad_artifact_id_type",
        "E406_bad_artifact_id_format",
        # File I/O
        "E901_file_not_found",
        "E902_json_parse_error",
    )
)
"""Rule codes emitted by the client lint engine.

Server lint must cover all of these codes — every client error is also a
server error (golden contract).
"""


def validate_subset_contract(server_diagnostics: Sequence[dict]) -> bool:
    """Verify that every client rule code has a server counterpart.

    This is the golden-file / contract assertion: client diagnostics
    must be a strict subset of server diagnostics (no false positives).

    Args:
        server_diagnostics: Server-side diagnostic dicts with ``"rule"`` key.

    Returns:
        True if the subset contract holds.
    """
    server_rules: set[str] = {d.get("rule") for d in server_diagnostics if isinstance(d, dict)}
    server_rules.discard(None)
    return CLIENT_RULES.issubset(server_rules)


def merge_client_server_diagnostics(
    client_diagnostics: Sequence[LintDiagnostic],
    server_diagnostics: Sequence[dict],
) -> list[LintDiagnostic]:
    """Merge client and server diagnostics into a single sorted list.

    Client diagnostics keep their `LintDiagnostic` objects and are tagged
    with source ``"client"``.  Server diagnostics (dicts) are converted
    to `LintDiagnostic` with source ``"server"``.

    Args:
        client_diagnostics: Output from :func:`lint_workflow_draft` or
            :func:`lint_json_file`.
        server_diagnostics: Server-side diagnostic dicts with keys like
            ``file``, ``line``, ``column``, ``code``, ``message``,
            ``severity`` (or ``rule`` as alias for ``code``).

    Returns:
        Deduplicated, sorted list of LintDiagnostic.
    """
    merged: list[LintDiagnostic] = []
    seen: set[tuple[str, str]] = set()

    for diag in client_diagnostics:
        key = (diag.file_path, diag.code)
        if key not in seen:
            merged.append(diag)
            seen.add(key)

    for sdiag in server_diagnostics:
        if not isinstance(sdiag, dict):
            continue
        code = sdiag.get("code") or sdiag.get("rule") or ""
        file_path = sdiag.get("file") or ""
        key = (file_path, code)
        if key in seen:
            continue  # client already emitted this
        seen.add(key)
        merged.append(
            LintDiagnostic(
                file_path=file_path,
                line=sdiag.get("line", 0),
                column=sdiag.get("column", 0),
                code=code,
                message=sdiag.get("message", ""),
                severity=sdiag.get("severity", "error"),
            )
        )

    # Sort: errors first, then by file:line:col
    severity_order = {"error": 0, "warning": 1, "info": 2}
    merged.sort(key=lambda d: (severity_order.get(d.severity, 3), d.file_path, d.line, d.column))
    return merged
