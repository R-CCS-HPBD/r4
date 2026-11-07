#!/usr/bin/env python3
"""Tool registry → route liveness validator (advisory, round-2 finding D.5).

Checks that every companion tool whose backing route was retired — or whose
executor callback is never wired by any surface — is covered by the
availability gate (``r4.companion.tool_availability``). Otherwise the tool
is still offered to the model even though it can only fail.

Findings:
- DEAD_CALLBACK: ``set_<x>_callback`` defined in r4/agent/tools.py with no
  call sites in r4/src, and the registered tool is not gated as unavailable
  when the callback is unwired.
- RETIRED_ROUTE: a retired route (``_raise_retired_http`` in r4/data.py)
  whose bound companion tool is not gated by ``unavailable_reason`` under a
  no-transport client.

Enforcement class: hard-fail (WP-13 round 2 promotion #4, 2026-09-10:
baseline sweep brought coverage to grade A before the flip; previously
advisory). Exit 1 when any finding exists; 0 when every dead/retired tool
is availability-gated.

Usage:
    python tools/tool_route_liveness.py          # Human summary
    python tools/tool_route_liveness.py --json   # JSON output
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
R4_SRC = REPO_ROOT / "r4" / "src"

SETTER_RE = re.compile(r"def (set_(\w+)_callback)\(")
RETIRED_ROUTE_RE = re.compile(r'_raise_retired_http\("([^"]+)"\)')

# Maps retired routes to the companion tools bound to them (executor calls).
# Source of truth: r4/src/r4/companion/tools.py executor bodies.
ROUTE_TOOLS: dict[str, list[str]] = {
    "/gw/data/upload": ["file_upload_platform"],
    "/gw/data/files": ["file_list_platform"],
}

# Setter name → tool name it powers (registration sits right below in
# r4/agent/tools.py; kept explicit to avoid fragile text proximity parsing).
SETTER_TOOLS: dict[str, str] = {
    "set_tool_search_callback": "tool_search",
    "set_web_search_callback": "web_search",
    "set_ask_user_callback": "ask_user",
}


@dataclass(frozen=True)
class Finding:
    kind: str  # DEAD_CALLBACK | RETIRED_ROUTE
    tool: str
    detail: str


@dataclass
class LivenessReport:
    findings: list[Finding] = field(default_factory=list)
    checked_setters: list[str] = field(default_factory=list)
    retired_routes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _setter_call_sites(setter: str) -> int:
    """Count call sites of *setter* under r4/src, excluding its definition."""
    count = 0
    for path in R4_SRC.rglob("*.py"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if setter not in line:
                continue
            if f"def {setter}" in line:
                continue
            count += 1
    return count


def _gated(tool_name: str, *, no_transport: bool = True) -> bool:
    """``True`` when the availability gate removes *tool_name* for a client
    without ZMQ transport (or an unwired callback)."""
    from r4.companion.tool_availability import unavailable_reason

    tool = {"name": tool_name, "description": "", "parameters": {"type": "object"}}
    client = None if no_transport else _stub_client()
    return unavailable_reason(tool, client) is not None


def _stub_client():
    from types import SimpleNamespace

    return SimpleNamespace(
        config=SimpleNamespace(
            use_umrs_transport=True,
            umrs_server_host="127.0.0.1",
            umrs_control_port=5555,
            umrs_tunnel_mode=False,
        )
    )


def check() -> LivenessReport:
    report = LivenessReport()
    agent_tools = R4_SRC / "r4" / "agent" / "tools.py"
    data_client = R4_SRC / "r4" / "data.py"

    # 1. Dead-by-omission callbacks.
    for match in SETTER_RE.finditer(agent_tools.read_text(errors="replace")):
        setter = match.group(1)
        tool = SETTER_TOOLS.get(setter)
        if tool is None:
            continue
        report.checked_setters.append(setter)
        if _setter_call_sites(setter) == 0 and not _gated(tool):
            report.findings.append(
                Finding(
                    kind="DEAD_CALLBACK",
                    tool=tool,
                    detail=(
                        f"{setter} has no call sites under r4/src; tool "
                        f"'{tool}' is still offered but can only stub"
                    ),
                )
            )

    # 2. Retired routes.
    retired = RETIRED_ROUTE_RE.findall(data_client.read_text(errors="replace"))
    report.retired_routes = sorted(set(retired))
    for route in report.retired_routes:
        for tool in ROUTE_TOOLS.get(route, []):
            if not _gated(tool):
                report.findings.append(
                    Finding(
                        kind="RETIRED_ROUTE",
                        tool=tool,
                        detail=(
                            f"{route} retired but tool '{tool}' is not "
                            "availability-gated under a no-transport client"
                        ),
                    )
                )
    return report


def main() -> int:
    report = check()
    if "--json" in sys.argv:
        print(json.dumps(asdict(report), indent=2))
    else:
        print("Tool route liveness (hard-fail; findings exit 1)")
        print(f"  callbacks checked : {len(report.checked_setters)}")
        print(f"  retired routes    : {len(report.retired_routes)}")
        if report.findings:
            print(f"  FINDINGS          : {len(report.findings)}")
            for f in report.findings:
                print(f"    [{f.kind}] {f.tool}: {f.detail}")
        else:
            print("  findings          : none — every dead/retired tool is gated")
    # hard-fail contract (WP-13 r2 #4): findings exit 1.
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
