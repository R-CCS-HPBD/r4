#!/usr/bin/env python3
"""Transport boundary audit — map ZMQ vs HTTP surface and verify isolation.

Identifies which modules use ZMQ directly vs through the MessageQueue
abstraction, and which use HTTP via the sidecar. Reports boundary
violations (unexpected direct zmq imports outside approved modules).

Usage:
    python tools/transport_boundary_audit.py          # Human summary
    python tools/transport_boundary_audit.py --json   # JSON output
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UMRS_SRC = REPO_ROOT / "umrs" / "src" / "umrs"

# Modules approved for direct zmq import (per ADR-001)
APPROVED_ZMQ_MODULES = {
    "core/MessageQueue.py",
    "core/zmq_security.py",
    "server/step_worker.py",
    "server/step_executor.py",
    "client/session_bootstrap.py",  # CURVE keypair generation via zmq.curve_keypair()
}

# Modules that constitute the HTTP sidecar surface
HTTP_SIDECAR_MODULES = {
    "api/",
    "server/http_sidecar.py",
}


@dataclass(frozen=True)
class TransportUsage:
    """One transport usage site found in the source."""

    file: str
    line: int
    transport: str  # "zmq" | "http" | "messagequeue"
    usage_type: str  # "import" | "socket_create" | "api_route" | "client_call"
    approved: bool  # Within approved boundary
    detail: str


@dataclass
class TransportReport:
    """Complete transport boundary audit report."""

    zmq_direct: list[TransportUsage] = field(default_factory=list)
    zmq_via_mq: list[TransportUsage] = field(default_factory=list)
    http_sidecar: list[TransportUsage] = field(default_factory=list)
    boundary_violations: list[TransportUsage] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "zmq_direct_files": len({u.file for u in self.zmq_direct}),
            "zmq_via_messagequeue_files": len({u.file for u in self.zmq_via_mq}),
            "http_sidecar_files": len({u.file for u in self.http_sidecar}),
            "boundary_violations": len(self.boundary_violations),
            "total_transport_sites": (
                len(self.zmq_direct) + len(self.zmq_via_mq) + len(self.http_sidecar)
            ),
        }


def _rel_path(path: Path) -> str:
    """Return path relative to UMRS_SRC."""
    try:
        return str(path.relative_to(UMRS_SRC))
    except ValueError:
        return str(path)


def _is_approved_zmq(rel: str) -> bool:
    """Check if a file is in the approved ZMQ boundary."""
    return rel in APPROVED_ZMQ_MODULES


def _is_zmq_module(name: str) -> bool:
    """Return True when *name* is the zmq package or a submodule."""
    return name == "zmq" or name.startswith("zmq.")


def _record_zmq_import(rel: str, node: ast.Import, alias: ast.alias) -> TransportUsage:
    """Return a TransportUsage for ``import zmq*``."""
    return TransportUsage(
        file=rel,
        line=node.lineno,
        transport="zmq",
        usage_type="import",
        approved=_is_approved_zmq(rel),
        detail=f"import {alias.name}",
    )


def _record_zmq_importfrom(rel: str, node: ast.ImportFrom) -> TransportUsage | None:
    """Return a TransportUsage for ``from zmq* import ...``, or None."""
    if node.module is None:
        return None
    if not _is_zmq_module(node.module):
        return None
    names = ", ".join(a.name for a in node.names)
    return TransportUsage(
        file=rel,
        line=node.lineno,
        transport="zmq",
        usage_type="import",
        approved=_is_approved_zmq(rel),
        detail=f"from {node.module} import {names}",
    )


def _scan_zmq_imports(py_file: Path) -> list[TransportUsage]:
    """Find direct zmq imports in a Python file."""
    rel = _rel_path(py_file)

    try:
        source = py_file.read_text()
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    results: list[TransportUsage] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_zmq_module(alias.name):
                    results.append(_record_zmq_import(rel, node, alias))
        elif isinstance(node, ast.ImportFrom):
            usage = _record_zmq_importfrom(rel, node)
            if usage is not None:
                results.append(usage)

    return results


def _scan_messagequeue_imports(py_file: Path) -> list[TransportUsage]:
    """Find imports of the MessageQueue abstraction."""
    results = []
    rel = _rel_path(py_file)

    try:
        source = py_file.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    for lineno, line in enumerate(source.splitlines(), 1):
        if "MessageQueue" in line and ("import" in line or "from" in line):
            results.append(
                TransportUsage(
                    file=rel,
                    line=lineno,
                    transport="messagequeue",
                    usage_type="import",
                    approved=True,
                    detail=line.strip(),
                )
            )

    return results


def _scan_http_routes(py_file: Path) -> list[TransportUsage]:
    """Find FastAPI/HTTP route definitions."""
    results = []
    rel = _rel_path(py_file)

    try:
        source = py_file.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    route_patterns = ["@app.", "@router.", "APIRouter", "FastAPI"]
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        for pattern in route_patterns:
            if pattern in stripped:
                results.append(
                    TransportUsage(
                        file=rel,
                        line=lineno,
                        transport="http",
                        usage_type="api_route",
                        approved=True,
                        detail=stripped[:80],
                    )
                )
                break

    return results


def run_audit() -> TransportReport:
    """Scan UMRS source tree for transport usage patterns."""
    report = TransportReport()

    for py_file in sorted(UMRS_SRC.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue

        # Direct ZMQ imports
        zmq_usages = _scan_zmq_imports(py_file)
        for usage in zmq_usages:
            if usage.approved:
                report.zmq_direct.append(usage)
            else:
                report.boundary_violations.append(usage)

        # MessageQueue abstraction imports
        mq_usages = _scan_messagequeue_imports(py_file)
        report.zmq_via_mq.extend(mq_usages)

        # HTTP sidecar routes
        http_usages = _scan_http_routes(py_file)
        report.http_sidecar.extend(http_usages)

    return report


def format_human(report: TransportReport) -> str:
    """Format report as human-readable text."""
    lines = []
    lines.append("Transport Boundary Audit")
    lines.append("=" * 66)

    s = report.summary
    lines.append(f"\nDirect ZMQ imports:      {s['zmq_direct_files']} files (approved boundary)")
    lines.append(f"MessageQueue consumers:  {s['zmq_via_messagequeue_files']} files")
    lines.append(f"HTTP sidecar routes:     {s['http_sidecar_files']} files")
    lines.append(f"Boundary violations:     {s['boundary_violations']}")
    lines.append(f"Total transport sites:   {s['total_transport_sites']}")

    if report.boundary_violations:
        lines.append("\nBOUNDARY VIOLATIONS (zmq import outside approved modules):")
        lines.append("-" * 66)
        for v in report.boundary_violations:
            lines.append(f"  {v.file}:{v.line} — {v.detail}")

    lines.append("\nApproved ZMQ modules:")
    for f in sorted(APPROVED_ZMQ_MODULES):
        lines.append(f"  {f}")

    lines.append("\nZMQ via MessageQueue abstraction:")
    seen = set()
    for u in report.zmq_via_mq:
        if u.file not in seen:
            seen.add(u.file)
            lines.append(f"  {u.file}")

    lines.append("\nHTTP sidecar files:")
    seen = set()
    for u in report.http_sidecar:
        if u.file not in seen:
            seen.add(u.file)
            lines.append(f"  {u.file}")

    return "\n".join(lines)


def format_json(report: TransportReport) -> str:
    """Format report as JSON."""
    data = {
        "summary": report.summary,
        "zmq_direct": [asdict(u) for u in report.zmq_direct],
        "zmq_via_messagequeue": [asdict(u) for u in report.zmq_via_mq],
        "http_sidecar": [asdict(u) for u in report.http_sidecar],
        "boundary_violations": [asdict(u) for u in report.boundary_violations],
    }
    return json.dumps(data, indent=2)


def main() -> int:
    use_json = "--json" in sys.argv

    report = run_audit()

    if use_json:
        print(format_json(report))
    else:
        print(format_human(report))

    return 1 if report.boundary_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
