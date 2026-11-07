"""``r4 job`` command handler (Phase 2m, 2026-04-28)."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from .base import CommandHandler
from .registry import command


@command
class JobCommand(CommandHandler):
    name = "job"
    help = "Job management"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Inspect, cancel, and retry durable UMRS jobs.",
            epilog=(
                "Examples:\n"
                "  r4 job list --status running\n"
                "  r4 job describe <job-id>\n"
                "  r4 job retry <job-id>"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="job_command",
            title="subcommands",
            metavar="<job-command>",
        )
        sub.required = True

        job_list = sub.add_parser("list", help="List jobs")
        job_list.add_argument("--project", "-p", help="Project ID")
        job_list.add_argument(
            "--status",
            "-s",
            choices=["planned", "queued", "running", "completed", "failed", "cancelled"],
            help="Filter by status",
        )
        job_list.add_argument(
            "--type",
            "-t",
            dest="job_type",
            choices=[
                "index_build",
                "embedding_batch",
                "pipeline_run",
                "endpoint_provision",
                "cleanup",
            ],
            help="Filter by job type",
        )
        job_list.add_argument("--limit", "-l", type=int, default=20, help="Max results")
        job_list.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        job_describe = sub.add_parser("describe", help="Show job details")
        job_describe.add_argument("job_id", help="Job ID")
        job_describe.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        job_cancel = sub.add_parser("cancel", help="Cancel a job")
        job_cancel.add_argument("job_id", help="Job ID to cancel")
        job_cancel.add_argument("--force", "-f", action="store_true", help="Force cancel")

        job_retry = sub.add_parser(
            "retry",
            help="Retry a failed job",
            description=(
                "Retry a failed job. The retry inherits the original job's "
                "execution_authority (slurm|k8s|compose); that invariant "
                "never changes across attempts. See development.instructions.md."
            ),
        )

        job_reproduce = sub.add_parser(
            "reproduce",
            help="Executor-backed replay of a job (drift honesty printed)",
            description=(
                "Executor-backed replay (G-P7-2): requeues the job's spec so "
                "the runtime runs it again with the same inputs, and prints "
                "the drift window between the recorded and current code."
            ),
        )
        job_reproduce.add_argument("job_id", help="Job ID to reproduce")
        job_reproduce.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        job_creds = sub.add_parser(
            "attempt-credentials",
            help="Mint attempt-scoped credentials (or STS identity) for one attempt (WP-11)",
            description=(
                "WP-11: mints (idempotent) the attempt's scoped credential "
                "record — key space jobs/<job>/attempt_<n>/, 900s default "
                "TTL — or, with --sts, Federates it into real per-attempt "
                "STS credentials (AssumeRole prefix-scoped policy, "
                "fail-closed on venue fault, token printed once to stdout "
                "only — never persisted anywhere)."
            ),
        )
        job_creds.add_argument("job_id", help="Job ID")
        job_creds.add_argument("attempt_id", help="Attempt ID")
        job_creds.add_argument("--sts", action="store_true", help="Mint real STS credentials")
        job_creds.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        job_presign = sub.add_parser(
            "attempt-presign",
            help="Attempt-scoped presigned URL for a key under the minted scope (WP-11)",
        )
        job_presign.add_argument("job_id", help="Job ID")
        job_presign.add_argument("attempt_id", help="Attempt ID")
        job_presign.add_argument("key", help="S3 object key under the attempt scope")
        job_presign.add_argument("--method", choices=["get", "put"], default="get")
        job_presign.add_argument("--ttl-sec", type=int, default=None)
        job_presign.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )
        job_retry.add_argument("job_id", help="Job ID to retry")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="job")

        return cmd_job(args)


def cmd_job(args):
    """Handle job commands (U-741 to U-744)."""
    import sys

    from ..client import Client

    client = Client()
    handler = _job_command_handlers().get(args.job_command)
    if handler is None:
        print("Unknown job command. Use: list, describe, cancel, retry", file=sys.stderr)
        sys.exit(1)
    return handler(args, client)


def _json_print(data) -> None:
    import json

    print(json.dumps(data, indent=2))


def _job_status_icons() -> dict[str, str]:
    return {
        "planned": "📋",
        "queued": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "🚫",
    }


def _job_list_item(job) -> dict:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": len(job.attempts),
        "max_attempts": job.max_attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def _print_job_list(jobs: list) -> None:
    status_icons = _job_status_icons()
    print(f"{'JOB ID':<36}  {'TYPE':<18}  {'STATUS':<12}  {'ATTEMPTS':<8}  CREATED")
    print("-" * 100)
    for job in jobs:
        icon = status_icons.get(job.status, "❓")
        attempt_str = f"{len(job.attempts)}/{job.max_attempts}"
        created = job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "N/A"
        print(
            f"{job.job_id:<36}  {job.job_type:<18}  {icon} {job.status:<10}  "
            f"{attempt_str:<8}  {created}"
        )


def _cmd_job_list(args, client):
    jobs = client.jobs.list(
        project_id=args.project,
        status=args.status,
        job_type=args.job_type,
        limit=args.limit,
    )
    if not jobs:
        print("No jobs found.")
        return None
    if getattr(args, "as_json", False):
        _json_print([_job_list_item(job) for job in jobs])
    else:
        _print_job_list(jobs)
    return None


def _job_attempt_item(attempt) -> dict:
    return {
        "attempt_id": attempt.attempt_id,
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "backend": attempt.backend,
        "backend_ref": attempt.backend_ref,
        "exit_reason": attempt.exit_reason,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
    }


def _job_description(job) -> dict:
    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "user_id": job.user_id,
        "job_type": job.job_type,
        "status": job.status,
        "priority": job.priority,
        "max_attempts": job.max_attempts,
        "restart_policy": job.restart_policy,
        "spec": job.spec,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "active_attempt_id": job.active_attempt_id,
        "commit_attempt_id": job.commit_attempt_id,
        "attempts": [_job_attempt_item(attempt) for attempt in job.attempts],
    }


def _print_job_timestamps(job) -> None:
    if job.created_at:
        print(f"  Created:        {job.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    if job.updated_at:
        print(f"  Updated:        {job.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")


def _print_job_spec(spec: dict | None) -> None:
    if not spec:
        return
    print("\nSpec:")
    for key, value in spec.items():
        print(f"  {key}: {value}")


def _attempt_status_icons() -> dict[str, str]:
    return {
        "created": "📝",
        "submitted": "📤",
        "running": "🔄",
        "succeeded": "✅",
        "failed": "❌",
        "lost": "💀",
    }


def _print_attempt(attempt, status_icons: dict[str, str]) -> None:
    icon = status_icons.get(attempt.status, "❓")
    print(f"\n  #{attempt.attempt_no} [{attempt.attempt_id[:8]}...] {icon} {attempt.status}")
    print(f"    Backend: {attempt.backend}")
    optional_fields = [
        ("Ref", attempt.backend_ref),
        (
            "Started",
            attempt.started_at.strftime("%Y-%m-%d %H:%M:%S") if attempt.started_at else None,
        ),
        (
            "Ended",
            attempt.ended_at.strftime("%Y-%m-%d %H:%M:%S") if attempt.ended_at else None,
        ),
        ("Exit Reason", attempt.exit_reason),
        ("Detail", attempt.status_detail),
    ]
    for label, value in optional_fields:
        if value:
            print(f"    {label}: {value}")


def _print_job_attempts(attempts: list) -> None:
    if not attempts:
        return
    print(f"\nAttempts ({len(attempts)}):")
    status_icons = _attempt_status_icons()
    for attempt in attempts:
        _print_attempt(attempt, status_icons)


def _print_job_description(job) -> None:
    icon = _job_status_icons().get(job.status, "❓")
    print(f"Job: {job.job_id}")
    print("=" * 60)
    print(f"  Type:           {job.job_type}")
    print(f"  Status:         {icon} {job.status}")
    print(f"  Project:        {job.project_id}")
    print(f"  User:           {job.user_id}")
    print(f"  Priority:       {job.priority}")
    print(f"  Restart Policy: {job.restart_policy}")
    print(f"  Max Attempts:   {job.max_attempts}")
    _print_job_timestamps(job)
    _print_job_spec(job.spec)
    _print_job_attempts(job.attempts)


def _cmd_job_describe(args, client):
    job = client.jobs.get(args.job_id)
    if args.as_json:
        _json_print(_job_description(job))
    else:
        _print_job_description(job)


def _cmd_job_cancel(args, client):
    job = client.jobs.cancel(args.job_id)
    print(f"✓ Cancelled job: {job.job_id}")
    print(f"  Status: {job.status}")


def _cmd_job_retry(args, client):
    job = client.jobs.retry(args.job_id)
    print(f"✓ Queued job for retry: {job.job_id}")
    print(f"  Status: {job.status}")
    print(f"  Attempts: {len(job.attempts)}/{job.max_attempts}")


def _cmd_job_reproduce(args, client):
    """Executor-backed replay + drift-window print (G-P7-2 remedy)."""
    result = client.jobs.reproduce(args.job_id)
    if getattr(args, "as_json", False):
        import json

        print(json.dumps(result, indent=2))
        return
    print(f"✓ Reproduced job (executor-backed replay): {result['job_id']}")
    drift = result.get("drift") or {}
    same = drift.get("same_code")
    if drift.get("pinned") is not True:
        print("  drift: prior attempt has no recorded code version — window undeclared")
    elif same is True:
        print(f"  drift: none (code {drift.get('current_code_sha', '')[:12]} unchanged)")
    else:
        recorded = drift.get("recorded_code_sha") or ""
        current = drift.get("current_code_sha") or ""
        print(f"  drift: code changed {recorded[:12]} → {current[:12]}")


def _cmd_job_attempt_credentials(args, client):
    """Mint (idempotent) attempt credentials, or STS identity (WP-11)."""
    if getattr(args, "sts", False):
        result = client.jobs.attempt_credentials_sts(args.job_id, args.attempt_id)
    else:
        result = client.jobs.attempt_credentials_mint(args.job_id, args.attempt_id)
    if getattr(args, "as_json", False):
        _json_print(result)
        return
    status = result.get("status", "?")
    creds = result.get("credentials") or {}
    scope = result.get("scope_prefixes") or creds.get("scope_prefixes") or []
    print(f"✓ attempt credentials {status}: {args.job_id}/{args.attempt_id}")
    for prefix in scope:
        print(f"  scope: {prefix}")
    if "ttl_sec" in creds:
        print(f"  ttl: {creds['ttl_sec']}s (expires {creds.get('expires_at', '')})")
    if status == "federated":
        # STS leg: token fields print once, loudly, and are never persisted.
        tok = result.get("credentials", {})
        print("  ⚠ STS session credential follows — note it down now; it is never stored.")
        print(f"  access_key:    {tok.get('access_key')}")
        print(f"  secret_key:    {tok.get('secret_key')}")
        print(f"  session_token: {tok.get('session_token')}")
        print(f"  expiration:    {tok.get('expiration')}")


def _cmd_job_attempt_presign(args, client):
    """Presign a key under the attempt's minted scope (WP-11)."""
    result = client.jobs.attempt_presign(
        args.job_id,
        args.attempt_id,
        args.key,
        method=getattr(args, "method", "get"),
        ttl_sec=getattr(args, "ttl_sec", None),
    )
    if getattr(args, "as_json", False):
        _json_print(result)
        return
    print(f"✓ presigned ({result.get('method')}, {result.get('expires_in_sec')}s)")
    print(f"  url: {result.get('url')}")


def _job_command_handlers():
    return {
        "list": _cmd_job_list,
        "describe": _cmd_job_describe,
        "cancel": _cmd_job_cancel,
        "retry": _cmd_job_retry,
        "reproduce": _cmd_job_reproduce,
        "attempt-credentials": _cmd_job_attempt_credentials,
        "attempt-presign": _cmd_job_attempt_presign,
    }
