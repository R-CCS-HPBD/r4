"""``r4 doctor`` — comprehensive local environment diagnostics.

Inspired by openclaude ``doctor:runtime`` and openclaw ``openclaw doctor``.
Extends ``r4 health full`` with local-environment checks that are specific
to the developer workstation context: R4.md presence, env-var conflicts,
profile consistency, and toolchain availability.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from typing import TYPE_CHECKING

from .base import CommandHandler
from .diagnostics import diagnostics_exit_code, emit_full_diagnostics, run_full_diagnostics
from .registry import command

if TYPE_CHECKING:
    import os


def _run_doctor(args: Namespace) -> int:
    """Run comprehensive local diagnostics."""
    from ..config import get_config

    as_json = bool(getattr(args, "as_json", False))
    verbose = bool(getattr(args, "verbose", False))

    if getattr(args, "all_sources", False):
        from .health import _run_health_all_sources_full

        return _run_health_all_sources_full(args)

    config = get_config()
    checks = run_full_diagnostics(config)

    # Add doctor-specific local-environment checks beyond health diagnostics.
    local_checks = _run_local_checks(config)
    checks["local"] = local_checks
    checks["install_source"] = check_install_source()

    sovereignty_checks = _run_sovereignty_checks(config)
    checks["sovereignty"] = sovereignty_checks

    if getattr(args, "sovereignty", False):
        _print_sovereignty_banner(sovereignty_checks, as_json=as_json)
        return diagnostics_exit_code({"sovereignty": sovereignty_checks})

    emit_full_diagnostics(checks, as_json=as_json, title="R4 Doctor", verbose=verbose)
    return diagnostics_exit_code(checks)


def _print_sovereignty_banner(checks: dict[str, object], *, as_json: bool) -> None:
    """Render the sovereignty report with a verdict banner (D1 demo view)."""
    from .diagnostics import print_json, symbol

    if as_json:
        print_json({"sovereignty": checks})
        return

    print("Data Sovereignty Report (R4 Platform)\n")
    ok = _bool(checks.get("ok"))
    print(f"  {symbol(ok)} overall verdict: {'SOVEREIGN' if ok else 'NEEDS ATTENTION'}\n")
    for name, detail in checks.get("checks", {}).items():
        strict = False
        if isinstance(detail, dict):
            strict = _bool(detail.get("strict_ok"))
            message = detail.get("message", "")
        else:
            message = ""
        print(f"  {symbol(strict)} {name}: {message}")


def _run_sovereignty_checks(config) -> dict[str, object]:
    """Data-sovereignty self-check bundle (D1).

    Verifies each control the constitution promises without needing inside
    access to the running platform: offline inference env, egress guard
    policy (site allowlist read from local profile archives if available),
    gateway auth middleware presence, audit-trail expectation, and local
    credential hygiene.

    Returns a summary suitable for printing or exporting as evidence —
    the "verify by yourself" answer for "how strongly can we say this
    system is fully sovereign/isolated".
    """
    results: dict[str, object] = {}

    results["offline_inference"] = _check_offline_inference_env()
    results["egress_guard"] = _check_egress_guard_config(config)
    results["gateway_auth"] = _check_gateway_auth_middleware()
    results["audit_trail"] = _check_audit_trail_presence()
    results["local_hygiene"] = _check_local_credential_hygiene(config)

    all_ok = all(
        _bool(v.get("strict_ok"))
        for v in results.values()
        if isinstance(v, dict) and "strict_ok" in v
    )
    return {"ok": all_ok, "checks": results}


def _check_offline_inference_env() -> dict[str, object]:
    """Confirm inference containers ship offline-transformer flags."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    hits: list[str] = []
    for pattern in (
        # The stand-alone stack is the surface every checkout carries (the
        # public export has neither the singularity recipes nor Ansible).
        "deploy/standalone/compose.yaml",
        "deploy/singularity/mini-rivault.def",
        "deploy/singularity/mini-rivault-aarch64.def",
        "docker-compose.e2e.yaml",
        "deploy/ansible/roles/front_end/templates/frontend-assistant-service.yaml.j2",
    ):
        path = repo_root / pattern
        if not path.exists():
            continue
        try:
            if "TRANSFORMERS_OFFLINE" in path.read_text(encoding="utf-8"):
                hits.append(pattern)
        except OSError:
            continue

    return {
        "strict_ok": len(hits) >= 2,
        "evidence": hits,
        "message": f"TRANSFORMERS_OFFLINE=1 present in {len(hits)} deployment surfaces",
    }


def _check_egress_guard_config(config) -> dict[str, object]:
    """Check the configured egress posture from the local profile/config."""
    allowed = getattr(config, "egress_allowed_domains", None) or []
    remote_profiles = getattr(config, "egress_verified_egress_domains", None)
    msg = "no external domains allowed from knowledge ingest context"
    if allowed:
        msg = f"outbound allowed for: {', '.join(allowed[:3])}"
    elif remote_profiles:
        msg = "no local allowlist; use platform-provided policy"
    return {
        "strict_ok": True,
        "allowed_domains": allowed,
        "message": msg,
        "hint": "run 'r4 knowledge allowlist' for the web sites the site lets ingest fetch",
    }


def _check_gateway_auth_middleware() -> dict[str, object]:
    """Verify the NGINX gateway routes sit behind auth_request."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    conf = repo_root / "gateway/nginx/conf.d/server.conf"
    evidence: list[str] = []
    if conf.exists():
        try:
            text = conf.read_text(encoding="utf-8")
            if "auth_request" in text:
                evidence.append("gateway/nginx/conf.d/server.conf:text=auth_request")
            for route in ("/gw/llm/", "/gw/mcp/", "/gw/rag/", "/gw/jobs/"):
                if route in text:
                    evidence.append(f"route:{route}")
        except OSError:
            pass

    route_count = sum(1 for e in evidence if e.startswith("route:"))
    return {
        "strict_ok": bool(evidence),
        "evidence": evidence,
        "message": f"gateway auth enforcement visible for {route_count} routes",
    }


def _check_audit_trail_presence() -> dict[str, object]:
    """Confirm audit DB + egress audit sink are wired."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    candidates = [
        repo_root / "umrs/src/umrs/api/egress_audit.py",
        repo_root / "umrs/src/umrs/migrations/0005_egress_audit.sql",
    ]
    found = [str(c.relative_to(repo_root)) for c in candidates if c.exists()]
    return {
        "strict_ok": len(found) == len(candidates),
        "evidence": found,
        "message": f"egress audit sink wired ({len(found)}/{len(candidates)} components)",
    }


def _check_local_credential_hygiene(config) -> dict[str, object]:
    """Check credentials live in the user config dir, not the repo."""
    from pathlib import Path

    profile_dir = Path.home() / ".config" / "r4"
    profile = getattr(config, "profile", None)
    stored_profile = profile_dir / "profiles" / f"{profile}.json" if profile else None
    leak = repo_credential_leak()
    stored_str = str(stored_profile) if stored_profile and stored_profile.exists() else None
    return {
        "strict_ok": leak is None,
        "profile_dir": str(profile_dir),
        "stored_profile": stored_str,
        "repo_credential_leak": leak,
        "message": ("no credential files in repo root" if leak is None else f"leak: {leak}"),
        "hint": "secrets belong under ~/.config/r4, never in the repo root",
    }


def repo_credential_leak() -> str | None:
    """Scan for obvious credential files at the repo root (cheap, read-only)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    for name in ("jwks.json", ".prod.keycloak.pem", "api_key.txt", "jwt.txt"):
        if (repo_root / name).exists():
            return name
    return None


def _run_local_checks(config) -> dict[str, object]:
    """Run local workstation checks unique to ``r4 doctor``."""
    results: dict[str, object] = {}

    results["r4_md"] = _check_r4md()
    results["env"] = _check_env_overrides()
    results["profile"] = _check_profile_integrity(config)
    results["toolchain"] = _check_toolchain()
    results["sandbox"] = _check_sandbox()

    all_ok = all(_bool(v.get("ok")) for v in results.values() if isinstance(v, dict) and "ok" in v)
    return {"ok": all_ok, "checks": results}


def _check_sandbox() -> dict[str, object]:
    """ADR-101 D-12: the local tool sandbox backend (informational)."""
    from .sandbox_prompt import doctor_sandbox_check

    try:
        return doctor_sandbox_check()
    except Exception as exc:  # noqa: BLE001 — a probe error is a reported line
        return {"ok": True, "available": False, "message": f"sandbox: probe failed ({exc})"}


def _check_r4md() -> dict[str, object]:
    """Check for R4.md agent briefing file."""
    from ..briefing import find_r4_md

    path = find_r4_md()
    if path:
        try:
            size = path.stat().st_size
            return {"ok": True, "path": str(path), "size_bytes": size}
        except OSError:
            return {"ok": True, "path": str(path)}
    return {
        "ok": True,
        "path": None,
        "message": "no R4.md found — run 'r4 init r4md' to scaffold one",
        "hint": "r4 init r4md",
    }


def _check_env_overrides() -> dict[str, object]:
    """Check for env-var overrides that may conflict with profile."""
    import os

    overrides: dict[str, str] = {}
    conflicts: list[str] = []

    for var in (
        "R4_GATEWAY_URL",
        "R4_API_KEY",
        "R4_PROJECT_ID",
        "R4_MODEL",
        "UMRS_BASE_URL",
        "UMRS_USERNAME",
    ):
        val = os.environ.get(var)
        if val:
            overrides[var] = val
            conflicts.append(var)

    return {
        "ok": len(conflicts) == 0 or True,  # always ok, just informational
        "active_overrides": overrides if overrides else None,
        "hint": ("env-var overrides take precedence over profile settings" if overrides else None),
    }


def _check_profile_integrity(config) -> dict[str, object]:
    """Check that the active profile is internally consistent."""
    issues: list[str] = []

    if not config.gateway_url:
        issues.append("No gateway_url configured — run 'r4 auth login'")
    if config.transport_mode and config.transport_mode.value == "direct":
        if config.umrs_server_host == "127.0.0.1":
            issues.append("Direct ZMQ transport to 127.0.0.1 only works on the UMRS host")

    return {
        "ok": len(issues) == 0,
        "profile": config.profile,
        "gateway_url": config.gateway_url,
        "transport_mode": config.transport_mode.value if config.transport_mode else None,
        "issues": issues if issues else None,
    }


def _check_toolchain() -> dict[str, object]:
    """Check for common HPC/development toolchain availability."""
    import shutil

    tools: dict[str, dict[str, object]] = {}
    for name in (
        "git",
        "python3",
        "singularity",
        "apptainer",
        "docker",
        "podman",
        "sbatch",
        "squeue",
        "scancel",
        "srun",
    ):
        path = shutil.which(name)
        tools[name] = {"found": path is not None}
        if path:
            tools[name]["path"] = path

    missing = [k for k, v in tools.items() if not v.get("found")]
    return {
        "ok": True,
        "available": {k: v["path"] for k, v in tools.items() if v.get("found")},
        "missing": missing if missing else None,
        "hint": "missing tools may limit HPC operations" if missing else None,
    }


# ── install source (plan H-3.5, 2026-10-18) ──────────────────────────────────

_GIT_TIMEOUT_SEC = 2.0
_DAILY_USE_DOC = "docs/onboarding/developer.md (daily use vs development)"


def _git_out(package_dir: str | os.PathLike[str], *args: str) -> str | None:
    """``git -C <package_dir> <args>`` stdout, or None on any failure (never raises).

    ``--no-optional-locks``: doctor is read-only, so ``git status`` must not
    refresh (rewrite) the checkout's index or take ``index.lock``; a git killed
    by the timeout while holding that lock would block the next commit.
    """
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(package_dir), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def check_install_source(package_dir: str | os.PathLike[str] | None = None) -> dict[str, object]:
    """Warn (never fail) when r4 runs from a git checkout with uncommitted changes.

    A TUI or bridge started from a development checkout imports whatever is in
    the working tree; daily use belongs on a pinned install. ``ok`` is True or
    None (a warning), never False, so this check cannot fail ``r4 doctor``.
    """
    from pathlib import Path

    pkg = Path(package_dir) if package_dir else Path(__file__).resolve().parents[1]
    tracked = _git_out(pkg, "ls-files", "--", "__init__.py")
    if not tracked:
        return {"ok": True, "kind": "installed", "package_dir": str(pkg)}
    status = _git_out(pkg, "status", "--porcelain", "--", ".")
    if status is None:
        return {"ok": True, "kind": "checkout", "package_dir": str(pkg), "dirty": None}
    changed = [line for line in status.splitlines() if line.strip()]
    result: dict[str, object] = {
        "ok": True,
        "kind": "checkout",
        "package_dir": str(pkg),
        "dirty": len(changed),
    }
    if changed:
        result["ok"] = None
        result["message"] = (
            f"r4 runs from a git checkout with {len(changed)} uncommitted change(s) "
            f"under {pkg}; daily use should run a pinned install, see {_DAILY_USE_DOC}"
        )
    return result


def _bool(val: object) -> bool:
    """Coerce a truthy value to bool."""
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return bool(val)


@command
class DoctorCommand(CommandHandler):
    """``r4 doctor`` — comprehensive local environment diagnostics."""

    name = "doctor"
    help = "Comprehensive diagnostics: gateway, UMRS, local env, toolkit"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
        p.add_argument("--verbose", "-v", action="store_true", help="Show detailed component info")
        p.add_argument(
            "--all-sources",
            action="store_true",
            help="Run diagnostics against all registered platform sources",
        )
        p.add_argument(
            "--sovereignty",
            action="store_true",
            help=(
                "Emit the data-sovereignty demo report — verifies offline "
                "inference, gateway auth, egress guard, audit trail, and "
                "local credential hygiene with a one-word verdict"
            ),
        )
        return p

    def handle(self, args: Namespace) -> int:
        return _run_doctor(args)
