"""``r4 hpc`` — your HPC sites and your jobs across them (multi-HPC plan S2/S3).

Commands:

    r4 hpc sites [--json] [--refresh]     Every site: health, access mode, account
    r4 hpc jobs [--json] [--status S]     Your jobs at every site, each once
    r4 hpc cancel <job>[@<site>]          Cancel through the site that runs it
    r4 hpc retry <job>[@<site>]           Retry through the site that runs it

My own account at a site that offers ``user_account`` (ADR-112, wave B1):

    r4 hpc add <site> [--ssh HOST] [--key-type sk|ed25519] [--manual --home DIR]
    r4 hpc verify <site> --login LOGIN    The site reaches my gate, stores my allocations
    r4 hpc status [site] [--json]         My account, links, and the gate version check
    r4 hpc link --project P --account A [--site S]
    r4 hpc unlink --project P [--site S]
    r4 hpc revoke <site> [--remove-line --ssh HOST]
    r4 hpc rotate <site> [--ssh HOST] [--manual --home DIR]

Gated runs (ADR-112, wave B2):

    r4 hpc profile <site> [--ssh HOST] [--manual] [--login LOGIN]
                                          Reinstall the site profile, then verify
    r4 hpc status <site> --witness [--ack INTENT_ID] [--ssh HOST]
                                          Check my gate's log over my own SSH
    r4 hpc status [site] --live           The site's live hello of my gate
    r4 hpc status <site> --witness --clear-fork
                                          Accept the log as the new baseline after a fork

Standing permissions and usage (plan §6 and §7, waves C and D):

    r4 hpc grant --site S --project P --workflow NAME[@VERSION] --max-jobs N
                 --max-gpu-hours H --days D [--inputs listed|chained|any] ...
                                          Sign a bounded grant for unattended runs
    r4 hpc grants [--site S] [--json]     My grants, their limits and live usage
    r4 hpc grant-cancel ID [--site S]     Sign a cancel; my gate refuses the grant from then on
    r4 hpc usage [--project P] [--site S] [--since DATE] [--json]
                                          My HPC usage (or a project's, with its budget)

Every account command except ``status``, ``grants`` and ``usage`` is the
person's act: it refuses in the assistant's shell (``R4_COMPANION_SESSION``),
and ``add``, ``profile``, ``revoke``, ``rotate``, ``grant`` and
``grant-cancel`` also need a terminal. The gate is installed over your own
SSH session; r4 never holds a credential for the site.

A site is a registered platform source (``r4 source list``) or this profile's
own gateway. A site that has not declared its HPC access mode is listed but
marked not selectable: the agent will not route work there from another site.
"""

from __future__ import annotations

import functools
import json
import sys
import time
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from typing import Any, Callable, Optional

from .base import CommandHandler
from .registry import command


def _sites(args: Namespace) -> int:
    from ..sites import describe, inventory

    rows = inventory(refresh=bool(args.refresh))
    if args.as_json:
        print(json.dumps({"sites": [r.to_dict() for r in rows]}, indent=2, default=str))
        return 0
    if not rows:
        print("No HPC sites: register one with `r4 source add <name> <url>`.")
        return 0
    for row in rows:
        print(describe(row))
    return 0


def _job_text(row: Any) -> str:
    job = row.job
    seen = "" if len(row.seen_via) < 2 else f"  (listed by {len(row.seen_via)} gateways)"
    where = "" if "@" in row.handle else f"  (site: {row.site})"
    return f"{row.handle:<36} {job.status:<10} {job.job_type:<22} {job.created_at}{where}{seen}"


def _jobs(args: Namespace) -> int:
    """Exit 1 when every site failed (nothing could be listed)."""
    from ..hpc_jobs import list_across_sites
    from ..sites import site_sources

    jobs, errors = list_across_sites(status=args.status, limit=args.limit)
    status = 1 if errors and len(errors) >= len(site_sources()) else 0
    if args.as_json:
        payload = {"jobs": [j.to_dict() for j in jobs], "errors": errors}
        print(json.dumps(payload, indent=2, default=str))
        return status
    for row in jobs:
        print(_job_text(row))
    if not jobs:
        print("No jobs found at any reachable site.")
    for site, error in errors.items():
        print(f"{site}: error: {error}", file=sys.stderr)
    return status


def _act(args: Namespace) -> int:
    from ..errors import R4Error
    from ..hpc_jobs import act_on_job

    try:
        site, job = act_on_job(args.hpc_command, args.job)
    except R4Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Job {job.job_id}@{site}: {args.hpc_command} sent (status={job.status})")
    return 0


# ── my account at a site (ADR-112 B1) ────────────────────────────────────────

_Handler = Callable[[Namespace], int]


def _account_command(
    action: str, *, person: bool = True, needs_tty: bool = False
) -> Callable[[_Handler], _Handler]:
    """One ``r4 hpc <action>`` handler: the person-only refusal (rc 2) and
    the error boundary (rc 1) around it."""

    def wrap(fn: _Handler) -> _Handler:
        @functools.wraps(fn)
        def run(args: Namespace) -> int:
            from ..errors import R4Error
            from ..hpc_signing import person_refusal

            reason = person_refusal(action, needs_tty=needs_tty) if person else None
            if reason:
                print(f"✗ {reason}", file=sys.stderr)
                return 2
            try:
                return fn(args)
            except R4Error as exc:
                print(f"✗ r4 hpc {action} failed: {exc}", file=sys.stderr)
                return 1

        return run

    return wrap


def _site_client(name: Optional[str]) -> Any:
    """A client for site ``name`` (a source name), or the home site."""
    from .. import sites as _sites
    from ..hpc_jobs import _home_source, source_for_site

    base = _sites._base_config(None)
    source = source_for_site(name, base) if name else _home_source(base)
    return _sites._client_for(source, base)


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _host(args: Namespace) -> str:
    from ..hpc_signing import check_host

    return check_host(getattr(args, "ssh", None) or args.site)


def _where(args: Namespace) -> Any:
    """Login and home at the site: asked over SSH, or ``--home`` by hand."""
    from ..hpc_remote import RemoteHome, probe
    from ..hpc_signing import HpcSigningError, check_home

    if not args.manual:
        return probe(_host(args))
    if not args.home:
        raise HpcSigningError("--manual needs --home <your home directory at the site>")
    return RemoteHome(login="", home=check_home(args.home))


def _account_lines(account: Any) -> list[str]:
    from ..sites import safe_text

    if not isinstance(account, dict):
        return ["No HPC account at this site."]
    assocs = account.get("verified_assocs") or []
    allocations = ", ".join(safe_text(a.get("account"), 64) for a in assocs if isinstance(a, dict))
    login = account.get("verified_login") or account.get("site_login")
    return [
        f"status:       {safe_text(account.get('status'), 32)}",
        f"site login:   {safe_text(login, 64)}",
        f"allocations:  {allocations or '-'}",
        f"gate:         {safe_text(account.get('gate_version'), 16)} "
        f"sha256 {safe_text(account.get('gate_sha256'), 64)}",
        f"probe:        {safe_text(account.get('gate_probe'), 16)}",
        f"transport:    {safe_text(account.get('transport_key_id'), 32)}",
    ]


def _record_verified(site: str, answer: Any) -> None:
    """Keep the login the site verified: an intent must name exactly it."""
    from ..hpc_signing import update_site_record

    account = answer.get("account") if isinstance(answer, dict) else None
    if isinstance(account, dict) and account.get("status") == "active":
        update_site_record(site, login=account.get("verified_login"))
    if isinstance(answer, dict) and answer.get("site_profile_current") is False:
        print(
            f"warning: the site's profile differs from the one installed at {site}; "
            f"gated runs are refused until you run `r4 hpc profile {site}`.",
            file=sys.stderr,
        )


def _verify_with(client: Any, login: str, key: Any, kind: str, site: str) -> int:
    from ..hpc_signing import check_login, public_key_info

    answer = client.hpc_verify(
        check_login(login),
        signers=[public_key_info(key)[2]],
        uv_required=kind == "sk",
        allow_non_sk=kind == "ed25519",
    )
    print("\n".join(_account_lines((answer or {}).get("account"))))
    _record_verified(site, answer)
    return 0


def _install_prompt(host: str, plan: Any) -> str:
    return (
        f"This appends to {host}:~/.ssh/authorized_keys the line\n  {plan.line}\n"
        f"and installs the gate ~/.umrs/gate/umrs_gate.py (sha256 {plan.gate_sha256}).\n"
        "Install over your own SSH session now?"
    )


def _fetch_profile(client: Any, site: str, venue: str, domain: str) -> Any:
    """The site's profile, checked by the gate's validator, shown with a
    diff against the copy this r4 installed last."""
    from ..hpc_site_profile import installed_profile, parse_site_profile, render

    profile = parse_site_profile(client.hpc_site_profile(), venue=venue, domain=domain)
    print(render(profile, installed_profile(site)))
    return profile


def _release_check(*, domain: Optional[str] = None, hello: Optional[dict[str, Any]] = None) -> Any:
    """Wave-E verification when configured; None is the legacy envelope."""
    from ..hpc_release import verify_environment
    from ..hpc_signing import gate_source

    return verify_environment(gate_source=gate_source(), domain=domain, hello=hello)


def _record_install(args: Namespace, offer: Any, profile: Any, key: Any) -> None:
    from ..hpc_signing import record_site_signer, update_site_record
    from ..hpc_site_profile import remember

    record_site_signer(args.site, args.key_type, key)
    remember(args.site, profile)
    update_site_record(
        args.site,
        venue=offer.site,
        domain=offer.domain,
        ssh_destination=_host(args),
        site_profile_sha256=profile.sha256,
    )


def _clock() -> float:
    return time.time()


def _install_epoch(client: Any, site: str) -> Optional[int]:
    """The grant epoch to install (K7): the install time unless the site
    reports an active (or suspended) account and r4 recorded no revoke for
    it, so every grant signed before it ends at the gate; None for an
    account that is still there."""
    from ..hpc_signing import needs_epoch, read_site_record

    account = (client.hpc_account() or {}).get("account")
    if not needs_epoch(account, read_site_record(site)):
        return None
    print("This is a new or re-enrolled account: grants signed before now end at your gate.")
    return int(_clock())


@_account_command("add", needs_tty=True)
def _add(args: Namespace) -> int:
    from .. import hpc_remote, hpc_signing

    hpc_signing.check_gate_destination(args.site, _host(args))
    hpc_signing.check_openssh(args.key_type)
    key = hpc_signing.ensure_site_signing_key(args.key_type, site=args.site, confirm=_confirm)
    client = _site_client(args.site)
    epoch = _install_epoch(client, args.site)
    offer = hpc_signing.parse_offer(client.hpc_transport_key(rotate=False))
    if _release_check(domain=offer.domain) is None:
        print(
            "legacy gate compatibility: no signed release is configured; "
            "maintainer release authenticity was not checked.",
            file=sys.stderr,
        )
    hpc_signing.check_venue_free(offer.site, args.site)
    principal = hpc_signing.check_principal((client.site_me() or {}).get("user_id"))
    profile = _fetch_profile(client, args.site, offer.site, offer.domain)
    where = _where(args)
    plan = hpc_signing.build_install(
        offer,
        principal=principal,
        home=where.home,
        signer=key,
        kind=args.key_type,
        site_profile=profile.data,
        epoch=epoch,
        fresh_epoch=None if epoch is not None else int(_clock()),
    )
    if args.manual:
        print(hpc_remote.manual_install_text(plan, args.site))
        _record_install(args, offer, profile, key)
        return 0
    if not _confirm(_install_prompt(_host(args), plan)):
        print("✗ nothing installed.", file=sys.stderr)
        return 1
    hpc_remote.install(_host(args), plan)
    _record_install(args, offer, profile, key)
    if epoch is not None:
        hpc_signing.update_site_record(args.site, epoch_due=False)
    return _verify_with(client, where.login, key, args.key_type, args.site)


@_account_command("verify")
def _verify(args: Namespace) -> int:
    from ..hpc_signing import site_signer

    kind, key = site_signer(args.site, args.key_type)
    return _verify_with(_site_client(args.site), args.login, key, kind, args.site)


def _profile_login(args: Namespace, record: dict[str, Any]) -> str:
    from ..hpc_signing import HpcSigningError, check_login

    login = args.login or record.get("login")
    if not login:
        raise HpcSigningError("r4 has no verified login for this site: pass --login.")
    return check_login(login)


def _manual_profile_text(site: str, data: bytes, login: str) -> str:
    from ..hpc_remote import manual_profile_command

    return f"{manual_profile_command(data)}\n\nThen run: r4 hpc verify {site} --login {login}"


@_account_command("profile", needs_tty=True)
def _profile(args: Namespace) -> int:
    """Reinstall ``site.json`` only (over my own SSH), then verify."""
    from .. import hpc_remote
    from ..hpc_signing import HpcSigningError, read_site_record, site_signer, update_site_record
    from ..hpc_site_profile import remember

    record = read_site_record(args.site)
    if not (record.get("venue") and record.get("domain")):
        raise HpcSigningError(f"r4 has no record of the gate at {args.site}: run `r4 hpc add`.")
    kind, key = site_signer(args.site, args.key_type)
    login = _profile_login(args, record)
    client = _site_client(args.site)
    profile = _fetch_profile(client, args.site, record["venue"], record["domain"])
    if args.manual:
        print(_manual_profile_text(args.site, profile.data, login))
    elif _confirm(f"Install this profile as ~/.umrs/gate/site.json at {_host(args)}?"):
        hpc_remote.install_profile(_host(args), profile.data)
    else:
        print("✗ nothing installed.", file=sys.stderr)
        return 1
    remember(args.site, profile)
    update_site_record(args.site, site_profile_sha256=profile.sha256)
    return 0 if args.manual else _verify_with(client, login, key, kind, args.site)


def _gate_report(view: Any) -> dict[str, Any]:
    from ..hpc_signing import gate_sha256

    account = view.get("account") if isinstance(view, dict) else None
    remote = account.get("gate_sha256") if isinstance(account, dict) else None
    local = gate_sha256()
    return {"local_gate_sha256": local, "gate_match": (remote == local) if remote else None}


def _link_line(link: Any) -> str:
    from ..sites import safe_text

    return (
        f"link:         {safe_text(link.get('project_id'), 64)} -> "
        f"{safe_text(link.get('slurm_account'), 64)} ({safe_text(link.get('status'), 16)})"
    )


def _live_lines(live: dict[str, Any]) -> list[str]:
    """``GET …/hpc/gate``: the site's live ``hello`` of my gate (no state change)."""
    from ..hpc_signing import gate_sha256
    from ..sites import safe_text

    current: dict[str, Any] = live.get("current") or {}
    head: dict[str, Any] = live.get("log_head") or {}
    if not (isinstance(current, dict) and isinstance(head, dict)):
        current, head = {}, {}
    ours = "this r4's" if live.get("gate_sha256") == gate_sha256() else "NOT this r4's"
    profile = "current" if current.get("site_profile") else "stale: run `r4 hpc profile <site>`"
    return [
        f"live gate:    {safe_text(live.get('gate_version'), 16)} "
        f"sha256 {safe_text(live.get('gate_sha256'), 64)} ({ours})",
        f"site profile: {safe_text(live.get('site_profile_sha256'), 64)} ({profile})",
        f"gate log:     {safe_text(head.get('n'), 16)} entries, "
        f"head {safe_text(head.get('h'), 64)}",
    ]


def _fork_lines(site: str, reason: str) -> list[str]:
    return [
        f"✗ FORK at {site}: {reason}.",
        f"✗ FORK r4 keeps the head it witnessed and signs no intent, grant or rotate for "
        f"{site} until you investigate and run `r4 hpc status {site} --witness --clear-fork`.",
    ]


def _grant_lines(report: Any) -> tuple[list[str], list[str]]:
    """The grant entries since the last head and each grant's whole-log
    totals; the unknown and over-``max_jobs`` grants as alarms (K17)."""
    good = [
        f"  submit #{s.n}: grant {s.grant_id} run {s.run} step {s.step}"
        for s in report.grant_submits
    ]
    good += [f"  grant-cancel #{n}: grant {grant}" for n, grant in report.grant_cancels]
    good += [
        f"  grant {grant}: {total.jobs} submits in the log, reserved {total.gpu_s} GPU-s "
        f"and {total.core_s} core-s, charged {total.charged_gpu_s} GPU-s and "
        f"{total.charged_core_s} core-s"
        for grant, total in sorted(report.grant_totals.items())
    ]
    bad = [f"✗ grant {grant} was not signed by this r4" for grant in report.unknown_grants]
    bad += [
        f"✗ OVER CAP: grant {grant} has {report.grant_totals[grant].jobs} submits in the log, "
        "more than the max-jobs you signed"
        for grant in report.over_cap
    ]
    bad += [
        f"✗ OVER CHARGED CAP: grant {grant} has logged charge "
        f"{report.grant_totals[grant].charged_gpu_s} GPU-s / "
        f"{report.grant_totals[grant].charged_core_s} core-s beyond its signed hour caps"
        for grant in report.over_hour_cap
    ]
    bad += [f"✗ {problem}" for problem in report.cancel_problems]
    return good, bad


def _witness_lines(site: str, report: Any) -> tuple[list[str], list[str]]:
    """``(lines for stdout, alarms for stderr)``."""
    if not report.chain_ok:
        return [], _fork_lines(site, "the gate log does not verify: its hash chain is broken")
    if report.forked:
        return [], _fork_lines(site, report.fork_reason)
    good = [f"✓ gate log verified: {report.head['n']} entries, head {report.head['h'][:16]}"]
    good += [f"  submit #{n}: intent {ident}" for n, ident in report.submits]
    bad = [f"✗ intent {ident} was not signed by this r4" for ident in report.unknown]
    grant_good, grant_bad = _grant_lines(report)
    return good + grant_good, bad + grant_bad


def _server_head(site: str) -> Any:
    """The server's view of my log head (``GET …/hpc/gate``), read before
    the log so the log is at least as long; None when the site cannot say."""
    from ..errors import R4Error

    try:
        return (_site_client(site).hpc_gate() or {}).get("log_head")
    except R4Error as exc:
        print(
            f"warning: no split-view check (the site did not report its view: {exc}).",
            file=sys.stderr,
        )
        return None


def _still_forked(site: str) -> list[str]:
    from ..hpc_signing import recorded_fork

    fork = recorded_fork(site)
    return (
        _fork_lines(site, f"recorded {fork.get('seen_at', '?')}: {fork.get('reason', '')}")
        if fork
        else []
    )


def _acknowledge(site: str, idents: list[str]) -> None:
    """``--ack``: clear intents (64 hex) or grants (32 hex) I checked."""
    from ..hpc_intents import acknowledge_unknown

    for ident in idents:
        done = acknowledge_unknown(site, ident)
        kind = "grant" if len(ident) == 32 else "intent"
        print(f"{'acknowledged' if done else 'not pending'}: {kind} {ident}")


def _witness(args: Namespace) -> int:
    """I-9: read my gate log over my own SSH and witness its head."""
    from ..hpc_intents import witness_site
    from ..hpc_remote import read_gate_log

    if not args.site:
        print("✗ --witness needs a site name.", file=sys.stderr)
        return 2
    _acknowledge(args.site, getattr(args, "ack", None) or [])
    server = _server_head(args.site)
    clear = bool(getattr(args, "clear_fork", False))
    report = witness_site(args.site, read_gate_log(_host(args)), server, clear_fork=clear)
    good, bad = _witness_lines(args.site, report)
    bad += [] if report.forked or not report.chain_ok else _still_forked(args.site)
    if good:
        print("\n".join(good))
    if bad:
        print("\n".join(bad), file=sys.stderr)
    return 1 if bad else 0


def _live_domain(site: Optional[str]) -> Optional[str]:
    """The enrollment domain to bind a live hello, required in release mode."""
    from .. import hpc_release
    from ..hpc_signing import HpcSigningError, check_domain, read_site_record

    domain = read_site_record(site or "").get("domain")
    if domain is None and not hpc_release.release_requested():
        return None  # pre-wave-E compatibility: no release verification is active.
    try:
        return check_domain(domain)
    except HpcSigningError as exc:
        raise hpc_release.ReleaseError(
            "release_domain_unknown",
            f"{site or 'home'} has no trustworthy recorded enrollment domain; "
            "run `r4 hpc add <site>` to enroll it before a verified live status.",
        ) from exc


def _live_hello(site: Optional[str], client: Any) -> tuple[dict[str, Any], Any]:
    """Fetch the real gate hello once and bind it to the enrollment domain."""
    live = client.hpc_gate() or {}
    verified = _release_check(domain=_live_domain(site), hello=live)
    return live, verified


def _live(site: Optional[str], client: Any) -> int:
    """``--live``: the live hello, and a fork when the live head is behind
    or beside the one r4 witnessed."""
    from ..hpc_intents import live_fork

    live, _ = _live_hello(site, client)
    print("\n".join(_live_lines(live)))
    reason = live_fork(site, live.get("log_head")) if site else ""
    if reason:
        print("\n".join(_fork_lines(site or "", reason)), file=sys.stderr)
        return 1
    return 0


def _clear_fork_verdict(args: Namespace) -> Optional[int]:
    """Reject clear-fork without witness, or outside a person's terminal."""
    if not getattr(args, "clear_fork", False):
        return None
    if not getattr(args, "witness", False):
        print("✗ --clear-fork is valid only with --witness.", file=sys.stderr)
        return 2
    from ..hpc_signing import person_refusal

    reason = person_refusal("status --witness --clear-fork", needs_tty=True)
    if reason:
        print(f"✗ {reason}", file=sys.stderr)
        return 2
    return None


def _status_json(args: Namespace, client: Any, view: Any, report: Any) -> int:
    """Cached account JSON normally; live JSON only after the real release check."""
    if not getattr(args, "live", False):
        print(json.dumps({**view, **report}, indent=2, default=str))
        return 0
    from ..hpc_intents import live_fork

    live, verified = _live_hello(args.site, client)
    reason = live_fork(args.site, live.get("log_head")) if args.site else ""
    result = {**view, **report, "live_gate": live, "release_verified": verified is not None}
    if reason:
        result["witness_fork"] = {"reason": reason}
    print(json.dumps(result, indent=2, default=str))
    return 1 if reason else 0


def _status_prelude(args: Namespace) -> Optional[int]:
    """The unambiguous witness/live verdict before any account or hello call."""
    if getattr(args, "witness", False) and getattr(args, "live", False):
        print(
            "✗ --witness and --live cannot be combined; choose one verification path.",
            file=sys.stderr,
        )
        return 2
    verdict = _clear_fork_verdict(args)
    if verdict is not None:
        return verdict
    if getattr(args, "witness", False):
        return _witness(args)
    return None


@_account_command("status", person=False)
def _status(args: Namespace) -> int:
    verdict = _status_prelude(args)
    if verdict is not None:
        return verdict
    client = _site_client(args.site)
    view = client.hpc_account() or {}
    report = _gate_report(view)
    if args.as_json:
        return _status_json(args, client, view, report)
    print("\n".join(_account_lines(view.get("account"))))
    for link in view.get("links") or []:
        print(_link_line(link))
    status = _live(args.site, client) if getattr(args, "live", False) else 0
    if report["gate_match"] is False:
        print(
            f"warning: the gate this r4 ships (sha256 {report['local_gate_sha256']}) differs "
            "from the one the site verified; upgrade r4 or run `r4 hpc add` again.",
            file=sys.stderr,
        )
    return status


@_account_command("link")
def _link(args: Namespace) -> int:
    from ..sites import safe_text

    answer = _site_client(args.site).hpc_link(args.project, args.account) or {}
    link = answer.get("link") or {}
    print(
        f"✓ project {safe_text(link.get('project_id', args.project), 64)} -> allocation "
        f"{safe_text(link.get('slurm_account', args.account), 64)}"
    )
    return 0


@_account_command("unlink")
def _unlink(args: Namespace) -> int:
    _site_client(args.site).hpc_unlink(args.project)
    print(f"✓ project {args.project}: link removed.")
    return 0


def _current_comment(client: Any) -> str:
    """The comment of my current transport line at this site."""
    from ..hpc_signing import HpcSigningError, check_key_id, check_site, transport_comment

    account = (client.hpc_account() or {}).get("account")
    if not isinstance(account, dict) or not account.get("transport_key_id"):
        raise HpcSigningError("no HPC account with a transport key at this site.")
    site = check_site(account.get("venue"))
    return transport_comment(site, check_key_id(account.get("transport_key_id")))


@_account_command("revoke", needs_tty=True)
def _revoke(args: Namespace) -> int:
    from ..hpc_remote import edit_lines, write_epoch
    from ..hpc_signing import epoch_bytes, fork_refusal, update_site_record

    client = _site_client(args.site)
    comment = _current_comment(client)
    forked = fork_refusal(args.site, "revoke")  # K20: never blocks the remedy
    if forked:
        print(forked, file=sys.stderr)
    if not _confirm(f"Revoke your account at {args.site}? The site destroys its transport key."):
        return 1
    now = int(_clock())
    # r4's own record: the next `add` ends earlier grants whatever the site reports
    update_site_record(args.site, epoch_due=True, revoked_at=now)
    client.hpc_revoke()
    print("✓ revoked: the site destroyed your transport key.")
    if args.remove_line:
        removed = edit_lines(_host(args), drop=[comment])
        print(f"✓ removed {removed} line(s) ending in '{comment}' from ~/.ssh/authorized_keys.")
        write_epoch(_host(args), epoch_bytes(now))
        print("✓ your gate now refuses every grant signed before this revoke.")
        return 0
    print(f"Now delete the line ending in ' {comment}' from ~/.ssh/authorized_keys at the site.")
    return 0


def _rotate_prompt(site: str, old: str) -> str:
    return (
        f"Rotate your transport key at {site}? The site issues a new key and your "
        f"account waits for it; the line ending in ' {old}' is replaced."
    )


def _swap_line(args: Namespace, old: str, line: str, new_comment: str) -> None:
    """Replace the old transport line over the person's own SSH. If that
    fails after the site rotated, the exact manual swap is printed."""
    from .. import hpc_remote
    from ..errors import R4Error

    try:
        removed = hpc_remote.edit_lines(_host(args), drop=[old, new_comment], add=line)
    except R4Error:
        print(hpc_remote.manual_rotate_text(old, line, args.site), file=sys.stderr)
        raise
    if removed == 0:
        print(
            f"warning: no line ending in ' {old}' was found; the new line was added.",
            file=sys.stderr,
        )


@_account_command("rotate", needs_tty=True)
def _rotate(args: Namespace) -> int:
    from .. import hpc_remote, hpc_signing

    forked = hpc_signing.fork_refusal(args.site, "rotate")
    if forked:
        raise hpc_signing.HpcSigningError(forked)
    kind, key = hpc_signing.site_signer(args.site, args.key_type)
    client = _site_client(args.site)
    old = _current_comment(client)
    where = _where(args)
    if not _confirm(_rotate_prompt(args.site, old)):
        print("✗ nothing rotated.", file=sys.stderr)
        return 1
    offer = hpc_signing.parse_offer(client.hpc_transport_key(rotate=True))
    line = hpc_signing.authorized_keys_line(offer, where.home)
    if args.manual:
        print(hpc_remote.manual_rotate_text(old, line, args.site))
        return 0
    _swap_line(args, old, line, offer.comment)
    return _verify_with(client, where.login, key, kind, args.site)


# ── standing grants and usage (plan §6 C5, §7 D4) ───────────────────────────


def _grant_request(args: Namespace) -> Any:
    from pathlib import Path

    from ..hpc_grants import GrantRequest
    from ..hpc_intents import split_name

    return GrantRequest(
        site=args.site,
        project=args.project,
        workflows=tuple(split_name(w) for w in args.workflow),
        max_jobs=args.max_jobs,
        max_gpu_hours=args.max_gpu_hours,
        days=args.days,
        max_core_hours=args.max_core_hours,
        max_concurrent=args.max_concurrent,
        inputs=args.inputs,
        listed_paths=tuple(Path(p) for p in args.listed_input),
        yes=bool(args.yes),
    )


@_account_command("grant", needs_tty=True)
def _grant(args: Namespace) -> int:
    """Sign a standing permission (the person's act, at a terminal)."""
    from ..hpc_grants import run_grant
    from ..sites import safe_text

    answer = run_grant(_site_client(args.site), _grant_request(args), confirm=_confirm)
    view = answer.get("grant")
    view = view if isinstance(view, dict) else {}
    print(
        f"✓ grant {answer['grant_id']} stored at {args.site} "
        f"(status {safe_text(view.get('status', '?'), 16)}); "
        f"cancel it with `r4 hpc grant-cancel {answer['grant_id']}`."
    )
    return 0


def _direct_cancellation(args: Namespace, site: str) -> Any:
    if not getattr(args, "direct", False):
        return None
    from .. import hpc_remote
    from ..hpc_signing import read_site_record

    venue = str(read_site_record(site)["venue"])
    host = _host(args)
    return lambda envelope: hpc_remote.direct_grant_cancel(host, venue, envelope)


@_account_command("grant-cancel", needs_tty=True)
def _grant_cancel(args: Namespace) -> int:
    """Sign ``grant-cancel``; allowed while a fork is recorded (K20)."""
    from .. import hpc_remote
    from ..hpc_grants import cancel_grant, cancel_site, check_grant_id

    site = cancel_site(check_grant_id(args.grant_id), args.site)
    args = Namespace(**{**vars(args), "site": site})
    direct = _direct_cancellation(args, site)
    result = cancel_grant(
        _site_client(site),
        args.grant_id,
        site,
        direct=direct,
        gate_log=lambda: hpc_remote.read_gate_log(_host(args)),
    )
    if result.fork_note:
        print(result.fork_note, file=sys.stderr)
    if not result.recorded:
        mirror = "pending" if result.mirror_pending else "unconfirmed"
        print(
            f"✗ Grant {args.grant_id} has no verified gate receipt; UMRS mirror is {mirror}. "
            "The cancel is not proven complete. Run "
            f"`r4 hpc grant-cancel {args.grant_id}` again, or add --direct (and --ssh HOST if "
            "needed) to deliver a fresh signed cancel over your own SSH.",
            file=sys.stderr,
        )
        return 1
    if result.direct:
        print(f"✓ grant {args.grant_id} cancelled at your gate (direct receipt verified).")
    else:
        print(f"✓ grant {args.grant_id} cancelled; your gate refuses it from now on.")
    if not result.mirror_recorded:
        mirror = "pending" if result.mirror_pending else "unconfirmed"
        print(
            f"  UMRS mirror is {mirror}: no recorded mirror response confirmed the cancel.",
            file=sys.stderr,
        )
    print("  Jobs already running are not stopped: cancel them with `r4 hpc cancel <job>`.")
    return 0


def _all_sites() -> list[str]:
    from ..sites import site_sources

    return [source.name for source, _home in site_sources()]


def _undeclared(exc: Exception) -> bool:
    details = getattr(exc, "details", None)
    code = details.get("code") if isinstance(details, dict) else None
    return code == "hpcaccess.mode_undeclared"


def _per_site(
    names: list[str], fetch: Callable[[Any], Any], quiet_undeclared: bool
) -> tuple[dict[str, Any], dict[str, str]]:
    """``fetch(client)`` at each site: answers and errors by site. A site
    that does not offer personal accounts has nothing to list (quiet when
    the person did not name it)."""
    from ..errors import R4Error

    answers: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in names:
        try:
            answers[name] = fetch(_site_client(name)) or {}
        except R4Error as exc:
            if not (quiet_undeclared and _undeclared(exc)):
                errors[name] = str(exc)
    return answers, errors


def _report_errors(errors: dict[str, str], total: int) -> int:
    for site, error in errors.items():
        print(f"{site}: error: {error}", file=sys.stderr)
    return 1 if errors and len(errors) >= total else 0


@_account_command("grants", person=False)
def _grants(args: Namespace) -> int:
    """Read only, not fenced: my grants at each site, with live usage."""
    from ..hpc_grants import grant_lines

    names = [args.site] if args.site else _all_sites()
    answers, errors = _per_site(names, lambda c: c.hpc_grants(refresh=True), not args.site)
    if args.as_json:
        print(json.dumps({"sites": answers, "errors": errors}, indent=2, default=str))
        return _report_errors(errors, len(names))
    lines = [
        line
        for site, answer in answers.items()
        for view in answer.get("grants") or []
        if isinstance(view, dict)
        for line in grant_lines(site, view)
    ]
    print("\n".join(lines) if lines else "No grants.")
    return _report_errors(errors, len(names))


def _usage_evidence(args: Namespace, site: str, answer: dict[str, Any]) -> list[str]:
    """The witness problems for one site's already returned ledger rows."""
    from .. import hpc_usage
    from ..errors import R4Error
    from ..hpc_remote import read_gate_log
    from ..hpc_signing import check_host

    try:
        host = check_host(getattr(args, "ssh", None) or site)
        data = read_gate_log(host)
    except R4Error as exc:
        return [f"witness unavailable: {exc}"]
    rows = hpc_usage._rows(answer)
    return hpc_usage.usage_evidence_problems(data, rows)


def _merge_usage_evidence(errors: dict[str, str], site: str, problems: list[str]) -> None:
    if problems:
        errors[site] = "\n".join(filter(None, [errors.get(site, ""), *problems]))


def _project_usage_result(args: Namespace, answer: dict[str, Any]) -> int:
    from .. import hpc_usage

    problems = _usage_evidence(args, args.site, answer) if args.witness else []
    shown_answer = {**answer, "witness_errors": problems} if args.witness else answer
    text = json.dumps(shown_answer, indent=2, default=str) if args.as_json else None
    print(text or hpc_usage.project_text(answer))
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    return 1 if problems else 0


def _project_usage(args: Namespace) -> int:
    if args.witness and not args.site:
        print("✗ usage --witness with --project needs --site.", file=sys.stderr)
        return 2
    answer = _site_client(args.site).project_hpc_usage(args.project, since=args.since) or {}
    return _project_usage_result(args, answer)


def _add_usage_evidence(args: Namespace, answers: dict[str, Any], errors: dict[str, str]) -> None:
    for name, answer in answers.items():
        _merge_usage_evidence(errors, name, _usage_evidence(args, name, answer))


@_account_command("usage", person=False)
def _usage(args: Namespace) -> int:
    """Read only, not fenced: my usage across sites, or one project's."""
    from .. import hpc_usage

    if args.project:
        return _project_usage(args)
    names = [args.site] if args.site else _all_sites()
    answers, errors = _per_site(names, lambda c: c.hpc_usage(since=args.since), False)
    if args.witness:
        _add_usage_evidence(args, answers, errors)
    if args.as_json:
        print(json.dumps({"sites": answers, "errors": errors}, indent=2, default=str))
    else:
        print(hpc_usage.sites_text(answers))
    status = _report_errors(errors, len(names))
    return 1 if args.witness and errors else status


_HANDLERS: dict[str, _Handler] = {
    "sites": _sites,
    "jobs": _jobs,
    "cancel": _act,
    "retry": _act,
    "add": _add,
    "verify": _verify,
    "status": _status,
    "link": _link,
    "unlink": _unlink,
    "revoke": _revoke,
    "rotate": _rotate,
    "profile": _profile,
    "grant": _grant,
    "grants": _grants,
    "grant-cancel": _grant_cancel,
    "usage": _usage,
}

_ACCOUNT_EPILOG = (
    "My account at a site that offers user_account (ADR-112):\n"
    "  r4 hpc add bdrt --ssh bdrt-login\n"
    "  r4 hpc status bdrt\n"
    "  r4 hpc link --project my-proj --account my-alloc --site bdrt\n"
)
_KEY_TYPES = ("sk", "ed25519")


def _site_arg(parser: ArgumentParser, *, optional: bool = False) -> None:
    if optional:
        parser.add_argument("site", nargs="?", default=None, help="Site (default: home)")
    else:
        parser.add_argument("site", help="Site name (`r4 hpc sites`)")


def _ssh_args(parser: ArgumentParser, *, manual: bool) -> None:
    parser.add_argument("--ssh", default=None, help="My ssh destination (default: the site)")
    if manual:
        parser.add_argument("--manual", action="store_true", help="Print instead of SSHing")
        parser.add_argument("--home", default=None, help="My home at the site (with --manual)")


def _register_links(sub: Any) -> None:
    link = sub.add_parser("link", help="Link a project to my allocation (needs user_account)")
    link.add_argument("--account", required=True, help="Slurm account the site lists for me")
    unlink = sub.add_parser("unlink", help="Remove a project's link (needs user_account)")
    for cmd in (link, unlink):
        cmd.add_argument("--project", required=True, help="Project id")
        cmd.add_argument("--site", default=None, help="Site (default: home)")


def _register_account(sub: Any) -> None:
    add = sub.add_parser("add", help="Register my own account here (needs user_account)")
    _site_arg(add)
    _ssh_args(add, manual=True)
    add.add_argument("--key-type", choices=_KEY_TYPES, default="sk", help="Signing key type")
    verify = sub.add_parser("verify", help="Let the site reach my gate (needs user_account)")
    _site_arg(verify)
    verify.add_argument("--login", required=True, help="My login at the site")
    verify.add_argument("--key-type", choices=_KEY_TYPES, default=None)
    status = sub.add_parser("status", help="My account here, links, gate version check")
    _site_arg(status, optional=True)
    status.add_argument("--json", "-j", action="store_true", dest="as_json")
    status.add_argument(
        "--witness", action="store_true", help="Check my gate's log over my own SSH (I-9)"
    )
    status.add_argument(
        "--ack",
        action="append",
        default=[],
        metavar="ID",
        help="With --witness: clear an intent or grant I checked that this r4 did not sign",
    )
    status.add_argument(
        "--clear-fork",
        action="store_true",
        help="With --witness: after investigating a fork, take this log as the new baseline",
    )
    status.add_argument("--ssh", default=None, help="My ssh destination (default: the site)")
    status.add_argument(
        "--live", action="store_true", help="Ask the site for a live hello of my gate"
    )
    profile = sub.add_parser("profile", help="Reinstall the site profile at my gate, then verify")
    _site_arg(profile)
    _ssh_args(profile, manual=False)
    profile.add_argument("--manual", action="store_true", help="Print instead of SSHing")
    profile.add_argument("--login", default=None, help="My login at the site (default: recorded)")
    profile.add_argument("--key-type", choices=_KEY_TYPES, default=None)
    _register_links(sub)
    revoke = sub.add_parser("revoke", help="Revoke my account here (needs user_account)")
    _site_arg(revoke)
    _ssh_args(revoke, manual=False)
    revoke.add_argument(
        "--remove-line", action="store_true", help="Also remove the line over my own SSH"
    )
    rotate = sub.add_parser("rotate", help="Replace my transport key (needs user_account)")
    _site_arg(rotate)
    _ssh_args(rotate, manual=True)
    rotate.add_argument("--key-type", choices=_KEY_TYPES, default=None)
    _register_grants(sub)


def _register_grant(sub: Any) -> None:
    grant = sub.add_parser("grant", help="Sign a bounded standing permission (needs user_account)")
    grant.add_argument("--site", required=True, help="Site name (`r4 hpc sites`)")
    grant.add_argument("--project", required=True, help="Project id (linked at the site)")
    grant.add_argument(
        "--workflow", action="append", required=True, metavar="NAME[@VERSION]", help="Repeatable"
    )
    grant.add_argument("--max-jobs", type=int, required=True, help="Jobs in all (1-10000)")
    grant.add_argument("--max-gpu-hours", type=int, required=True, help="GPU-hours in all")
    grant.add_argument("--max-core-hours", type=int, default=0, help="Core-hours (0: no cap)")
    grant.add_argument(
        "--max-concurrent", type=int, default=None, help="Jobs at a time (default 4, at most N)"
    )
    grant.add_argument("--days", type=int, required=True, help="Lifetime in days (1-30)")
    grant.add_argument(
        "--inputs", choices=("listed", "chained", "any"), default="chained", help="Data rule"
    )
    grant.add_argument(
        "--listed-input", action="append", default=[], metavar="FILE", help="A file r4 hashes"
    )
    grant.add_argument("--yes", action="store_true", help="Skip only the confirmation prompt")


def _register_grants(sub: Any) -> None:
    _register_grant(sub)
    grants = sub.add_parser("grants", help="My grants, their limits and live usage")
    grants.add_argument("--site", default=None, help="One site (default: every site)")
    grants.add_argument("--json", "-j", action="store_true", dest="as_json")
    cancel = sub.add_parser("grant-cancel", help="Sign a cancel of one of my grants")
    cancel.add_argument("grant_id", help="Grant id (32 hex)")
    cancel.add_argument("--site", default=None, help="Site (default: where r4 signed it)")
    cancel.add_argument(
        "--direct", action="store_true", help="If relay has no receipt, use my own SSH to the gate"
    )
    cancel.add_argument(
        "--ssh", default=None, help="My ssh destination for gate verification/direct delivery"
    )
    usage = sub.add_parser("usage", help="My HPC usage, or a project's with its budget")
    usage.add_argument("--project", default=None, help="A project's usage and budget")
    usage.add_argument("--site", default=None, help="One site (default: every site)")
    usage.add_argument("--since", default=None, help="Rows from this date (ISO 8601)")
    usage.add_argument("--json", "-j", action="store_true", dest="as_json")
    usage.add_argument(
        "--witness", action="store_true", help="Verify the gate log against the usage ledger"
    )
    usage.add_argument("--ssh", default=None, help="With --witness: my ssh destination")


@command
class HpcCommand(CommandHandler):
    """``r4 hpc`` — my HPC sites and jobs across them."""

    name = "hpc"
    help = "My HPC sites (health, access mode, account) and my jobs across them"

    def register(self, subparsers: Any) -> ArgumentParser:
        parser: ArgumentParser = subparsers.add_parser(
            self.name,
            help=self.help,
            formatter_class=RawDescriptionHelpFormatter,
            description=(
                "Every HPC site you can reach (registered platform sources and "
                "this profile's gateway), and your jobs across them, each "
                "labelled with the site that runs it."
            ),
            epilog=(
                "Examples:\n"
                "  r4 hpc sites\n"
                "  r4 hpc jobs --status running\n"
                "  r4 hpc cancel 1234@bdrt\n\n" + _ACCOUNT_EPILOG
            ),
        )
        sub = parser.add_subparsers(dest="hpc_command", metavar="<hpc_command>")
        sites = sub.add_parser("sites", help="List my HPC sites with live status")
        sites.add_argument("--json", "-j", action="store_true", dest="as_json")
        sites.add_argument("--refresh", action="store_true", help="Probe again (skip the cache)")
        jobs = sub.add_parser("jobs", help="My jobs at every site, each once")
        jobs.add_argument("--json", "-j", action="store_true", dest="as_json")
        jobs.add_argument("--status", default=None, help="queued/running/completed/failed")
        jobs.add_argument("--limit", type=int, default=50, help="Max jobs per site")
        for action in ("cancel", "retry"):
            act = sub.add_parser(action, help=f"{action.title()} a job through its own site")
            act.add_argument("job", help="Job id, or <id>@<site>")
        _register_account(sub)
        return parser

    def handle(self, args: Namespace) -> int:
        handler = _HANDLERS.get(getattr(args, "hpc_command", None) or "")
        if handler is None:
            print(f"usage: r4 hpc {{{','.join(_HANDLERS)}}} ...", file=sys.stderr)
            return 2
        return handler(args)
