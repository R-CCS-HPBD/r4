"""``r4 knowledge allowlist`` — which web sites knowledge ingest may fetch.

URL and git-repo ingest run on the UMRS server, which fetches only from the
web sites its site allowlist names (``extra.knowledge_ingest.allowed_domains``
in the front's ``site.yaml``; ``@recommended`` expands to a curated list of
scientific sites, ``*`` allows every public site). This command shows that
list as the server applies it (``GET /k2/ingest/allowlist``), says how to get
it changed, and with ``--check URL`` whether one URL's host would be fetched.
Non-global addresses, and the networks the site denies (its own intranet,
``denied_networks``), are refused whatever the list says.

Since wave 79 (2026-10-25) the server also applies your ADR-099 web policy
in the current project (the same one that bounds the agent's browsing:
``none`` refuses URL ingest, ``allowlist`` keeps only its domains,
``public`` leaves the site list); the command prints it and the layer that
set it, and ``--check`` applies both.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

_ALLOWLIST_HELP = """\
Show which web sites knowledge ingest (a URL or git repo) may fetch here.

  r4 knowledge allowlist                         the sites, grouped
  r4 knowledge allowlist --check doi.org/10.1/x  would this URL's host be fetched?
  r4 knowledge allowlist --json                  the server's answer, as JSON

"[*.]example.org" means example.org and its subdomains. The list is the
site's (site.yaml, extra.knowledge_ingest.allowed_domains): ask your UMRS
operator to add a site. Your web policy in the current project (--project)
narrows it: the same policy that bounds the agent's web access (off,
allowlist or public), set by an admin and the project owner. --check looks
at the host name and port only; the network checks (public address, size,
content scan) run when a fetch happens. --check exits 0 when the host is
allowed and 1 when it is refused.
"""

_WIDTH = 78


def register_allowlist(sub: Any) -> None:
    """Add ``allowlist`` to the ``r4 knowledge`` subparsers."""
    p = sub.add_parser(
        "allowlist",
        help="Show which web sites knowledge ingest may fetch (and how to add one)",
        description=_ALLOWLIST_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--check", metavar="URL", help="Would ingest fetch this URL's host?")
    p.add_argument("--project", "--project-id", "-p", help="Project ID (default: current project)")
    p.add_argument("--json", "-j", action="store_true", dest="as_json", help="Print JSON")


class UsageError(ValueError):
    """A bad argument: exit 2 with the remedy (not a server failure)."""


def group_entries(entries: list[str]) -> list[str]:
    """``x.org`` + ``*.x.org`` → ``[*.]x.org``; other entries as they are,
    in the server's order."""
    present = set(entries)
    grouped: list[str] = []
    for entry in entries:
        if entry.startswith("*.") and entry[2:] in present:
            continue
        grouped.append(f"[*.]{entry}" if f"*.{entry}" in present else entry)
    return grouped


def _print_denied(view: dict[str, Any]) -> None:
    """The networks never fetched, whatever the list says (2026-10-25)."""
    denied = [str(n) for n in view.get("denied_networks") or []]
    if denied:
        print(f"Never fetched (this site's own network): {', '.join(denied)}.")


def _print_entries(view: dict[str, Any]) -> None:
    site = view.get("site") or "this site"
    entries = [str(e) for e in view.get("entries") or []]
    if view.get("allow_all"):
        print(f'Site {site}: knowledge ingest may fetch every public web site ("*").')
        print("Private and other non-global addresses stay blocked.")
        return
    if not entries:
        print(f"Site {site}: knowledge ingest may fetch no web site (the allowlist is empty).")
        return
    grouped = group_entries(entries)
    print(f"Site {site}: knowledge ingest may fetch {len(grouped)} web sites:")
    print(
        textwrap.fill(
            "  ".join(grouped),
            width=_WIDTH,
            initial_indent="  ",
            subsequent_indent="  ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def _fill(text: str, indent: str = "") -> str:
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _print_policy(view: dict[str, Any]) -> None:
    """Your ADR-099 web policy in the project, as the server resolved it
    (wave 79); an older server sends none and nothing is printed."""
    policy = view.get("web_policy")
    if not isinstance(policy, dict):
        return
    if policy.get("error"):
        print(_fill(f"Your web policy here could not be read: {policy['error']}"))
        return
    print(_fill(f"Your {policy.get('summary') or 'web policy: ' + str(policy.get('mode'))}."))
    entries = policy.get("entries")
    if policy.get("mode") == "allowlist" and entries:
        print(f"URL ingest in this project may fetch {len(group_entries(entries))} web sites:")
        print(_fill("  ".join(group_entries(entries)), indent="  "))


def _print_check(check: dict[str, Any]) -> None:
    host = check.get("host") or check.get("url")
    mark = "✓ allowed" if check.get("allowed") else "✗ refused"
    print(f"{mark}: {host}: {check.get('reason', '')}")


def _print_view(view: dict[str, Any], checking: bool) -> None:
    if checking:
        _print_check(view["check"])
    else:
        _print_entries(view)
        _print_policy(view)
        _print_denied(view)
    print()
    print(textwrap.fill(str(view.get("how_to_change") or ""), width=_WIDTH))


def _fetch(args: argparse.Namespace) -> dict[str, Any]:
    from ..client import Client

    check = args.check
    if check is not None and not check.strip():
        raise UsageError("--check takes a URL or host name (https://doi.org/10.1/x or doi.org)")
    view: dict[str, Any] = Client().knowledge.ingest_allowlist(
        check=check.strip() if check else None, project_id=args.project
    )
    if check and "check" not in view:
        raise RuntimeError("this UMRS server does not answer --check yet (update UMRS)")
    return view


def _failed(args: argparse.Namespace, exc: Exception) -> int:
    """Exit 2 for a usage error, 1 for a failure; JSON on stdout under
    ``--json`` (a script reads one shape), else one line on stderr."""
    code = 2 if isinstance(exc, UsageError) else 1
    if args.as_json:
        print(json.dumps({"error": str(exc), "exit_code": code}))
    elif code == 2:
        print(f"✗ {exc}", file=sys.stderr)
    else:
        print(f"✗ knowledge allowlist failed: {exc}", file=sys.stderr)
    return code


def handle_allowlist(args: argparse.Namespace) -> int:
    """``r4 knowledge allowlist``."""
    try:
        view = _fetch(args)
    except Exception as exc:  # noqa: BLE001 — one line, the server's reason
        return _failed(args, exc)
    if args.as_json:
        print(json.dumps(view, indent=2, default=str))
    else:
        _print_view(view, checking=bool(args.check))
    if args.check:
        return 0 if view["check"].get("allowed") else 1
    return 0


__all__ = ["UsageError", "group_entries", "handle_allowlist", "register_allowlist"]
