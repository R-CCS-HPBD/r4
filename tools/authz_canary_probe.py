#!/usr/bin/env python3
"""Authz canary probe runner (ADR-035, sprint item 4).

Reads ``tools/authz_canaries.yaml`` and, against a live gateway, fires ONE
unauthenticated GET per canary route. The probe invariant:

  an unauthenticated request MUST NOT receive data — expected responses are
  401/403 (denied), or 503 (surface down); 2xx-with-payload is a hard failure
  (route forgot its edge auth).

Modes:
  default            validate the canary file + dry-run echo (offline)
  --gateway URL      fire live probes (verbose per-route results)
  --json             machine-readable findings

Intended wiring: run on every deployment smoke AND ad hoc via
``python tools/authz_canary_probe.py --gateway https://...``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANARIES_PATH = REPO_ROOT / "tools" / "authz_canaries.yaml"

# Responses consistent with "edge auth worked": the request was challenged
# or the backend is unavailable. Everything else is judged against payload:
# 2xx + any JSON payload = leak; unexpected 4xx/5xx = soft warning (the
# surface changed shape, probe needs an update).
DENY_STATUSES = {401, 403}
DEGRADED_STATUSES = {503}
# Authenticated cross-scope probes read identity material from env:
#   CANARY_IDENTITY_USER / CANARY_IDENTITY_PROJECT / CANARY_IDENTITY_TOKEN
# without env they report verdict=skip (never fail offline/CI).
ENV_IDENTITY = ("CANARY_IDENTITY_USER", "CANARY_IDENTITY_PROJECT", "CANARY_IDENTITY_TOKEN")

REQUIRED_PROBE_FIELDS = {"id", "path", "expect_denied"}


@dataclass(frozen=True)
class ProbeFinding:
    """One canary-file validation finding."""

    code: str
    subject: str
    message: str
    severity: str = "fail"

    def format(self) -> str:
        return f"[{self.severity}] {self.code} {self.subject}: {self.message}"


@dataclass(frozen=True)
class ProbeResult:
    """One live-probe outcome."""

    id: str
    path: str
    status: int | None
    verdict: str  # pass | fail | warn | skip
    detail: str = ""

    def format(self) -> str:
        status = "----" if self.status is None else str(self.status)
        return f"[{self.verdict}] {self.id:24s} {status} {self.path}  {self.detail}".rstrip()


def load_canaries(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - venv always has it
        return None, ["PyYAML unavailable"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, [f"cannot parse {path}: {exc}"]
    if not isinstance(data, dict):
        return None, ["canary root must be a mapping"]
    return data, []


def _validate_one_probe(probe: Any, seen: set[str]) -> list[ProbeFinding]:
    """Per-probe field/id checks; dup tracking via ``seen``."""
    pid = str(probe.get("id") if isinstance(probe, dict) else repr(probe))
    missing = (
        REQUIRED_PROBE_FIELDS - set(probe) if isinstance(probe, dict) else REQUIRED_PROBE_FIELDS
    )
    if missing:
        return [ProbeFinding("probe-fields", pid, f"missing fields: {sorted(missing)}")]
    findings: list[ProbeFinding] = []
    if pid in seen:
        findings.append(ProbeFinding("probe-id", pid, "duplicate probe id"))
    seen.add(pid)
    if not str(probe["path"]).startswith("/"):
        findings.append(ProbeFinding("probe-path", pid, "path must start with '/'"))
    if probe["expect_denied"] is not True:
        findings.append(
            ProbeFinding("probe-expect", pid, "only expect_denied:true probes are supported")
        )
    return findings


def validate_canary_data(data: dict[str, Any]) -> list[ProbeFinding]:
    """Schema pass over the parsed canary file."""
    probes = data.get("probes")
    if not isinstance(probes, list) or not probes:
        return [ProbeFinding("probes", "probes", "top-level 'probes' must be a non-empty list")]
    seen: set[str] = set()
    findings: list[ProbeFinding] = []
    for probe in probes:
        findings.extend(_validate_one_probe(probe, seen))
    return findings


def _fire(
    url: str, timeout: float, headers: dict[str, str] | None = None
) -> tuple[int | None, bytes]:
    """Single GET; status+body, never raises on HTTP errors."""
    base_headers = {"User-Agent": "authz-canary/1.0"}
    base_headers.update(headers or {})
    req = urllib.request.Request(url, headers=base_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return None, str(exc).encode()


def _identity_headers() -> dict[str, str] | None:
    """Authenticated canary identity from env; None when any piece missing."""
    import os

    user, project, token = (os.environ.get(v, "").strip() for v in ENV_IDENTITY)
    if not (user and project and token):
        return None
    return {
        "Authorization": f"Bearer {token}",
        "X-Canary-User": user,
        "X-Canary-Project": project,
    }


def _substitute(text: str) -> str:
    """Expand ${VAR} from env in probe paths."""
    import os
    import re

    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), text)


def _verdict(status: int | None, body: bytes) -> tuple[str, str]:
    if status is None:
        return "warn", f"transport error: {body.decode(errors='replace')[:80]}"
    if status in DENY_STATUSES:
        return "pass", "denied as expected"
    if status in DEGRADED_STATUSES:
        return "pass", "endpoint unavailable (503) — denied-by-unavailability"
    if 200 <= status < 300:
        if body.strip():
            return "fail", "unauthenticated 2xx with payload — edge auth leak"
        return "warn", "unauthenticated 2xx with empty body — review route"
    return "warn", f"unexpected status {status}"


def run_probes(
    gateway: str, probes: list[dict[str, Any]], timeout: float = 10.0
) -> list[ProbeResult]:
    """Fire each canary probe against the live gateway."""
    results: list[ProbeResult] = []
    base = gateway.rstrip("/")
    identity = _identity_headers()
    for probe in probes:
        label = str(probe["id"])
        path = _substitute(str(probe["path"]))
        if probe.get("authenticated") and identity is None:
            results.append(
                ProbeResult(
                    id=label,
                    path=path,
                    status=None,
                    verdict="skip",
                    detail="no CANARY_IDENTITY_* env — authenticated probe skipped",
                )
            )
            continue
        headers = identity if probe.get("authenticated") else None
        status, body = _fire(f"{base}{path}", timeout, headers=headers)
        if probe.get("authenticated"):
            verdict, detail = _cross_scope_verdict(status, body, probe)
        else:
            verdict, detail = _verdict(status, body)
        results.append(
            ProbeResult(id=label, path=path, status=status, verdict=verdict, detail=detail)
        )
    return results


def _cross_scope_verdict(status: int | None, body: bytes, probe: dict[str, Any]) -> tuple[str, str]:
    """Authenticated probes: 2xx-with-payload from a NON-member project is the
    leak; 401/403/404-without-payload is correct behavior."""
    if status is None:
        return "warn", f"transport error: {body.decode(errors='replace')[:80]}"
    if status in DENY_STATUSES or status == 404 and not body.strip():
        return "pass", f"cross-scope read denied ({status})"
    if 200 <= status < 300:
        return "fail", "authenticated cross-scope 2xx with payload — project boundary leak"
    return "warn", f"unexpected status {status}"


def _report(results: list[ProbeResult], as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(r) for r in results], indent=2))
        return
    for result in results:
        print(result.format())


def _exit_code(results: list[ProbeResult]) -> int:
    """1 on any fail verdict, else 0 (warn tolerated, reported in output)."""
    return 1 if any(r.verdict == "fail" for r in results) else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canaries", type=Path, default=DEFAULT_CANARIES_PATH)
    parser.add_argument("--gateway", type=str, default="", help="live gateway base URL to probe")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_and_validate(path: Path) -> tuple[dict[str, Any] | None, int]:
    """Load + schema-validate; returns (data, error-code-or-0)."""
    data, errors = load_canaries(path)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return None, 2
    findings = validate_canary_data(data or {})
    for finding in findings:
        print(finding.format())
    return (None, 1) if findings else (data, 0)


def _run_live(data: dict[str, Any], args: argparse.Namespace) -> int:
    results = run_probes(args.gateway, list(data.get("probes") or []), timeout=args.timeout)
    _report(results, args.json)
    code = _exit_code(results)
    if code == 0:
        worst = "warn" if any(r.verdict == "warn" for r in results) else "pass"
        print(f"OK: {len(results)} canaries probed ({worst})")
    return code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    data, error = _load_and_validate(args.canaries)
    if error:
        return error
    if not args.gateway:
        print(
            f"OK: {len((data or {}).get('probes') or [])} canaries valid (offline; pass --gateway to fire live)"
        )
        return 0
    return _run_live(data or {}, args)


if __name__ == "__main__":
    sys.exit(main())
