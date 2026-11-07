"""``r4 knowledge allowlist`` and the URL ingest request (2026-10-25).

The site allowlist (``extra.knowledge_ingest.allowed_domains``) decides
which web sites UMRS fetches for URL and repo ingest. Users read it with
``r4 knowledge allowlist`` (``GET /k2/ingest/allowlist``) and check one URL
with ``--check``. ``ingest_paths`` no longer narrows the list to the URLs'
own hosts, which refused every redirect to another host (doi.org to the
publisher).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from r4._cli.knowledge_allowlist import group_entries, handle_allowlist
from r4.knowledge import KnowledgeClient

_HOW = "Ask your UMRS operator to edit extra.knowledge_ingest.allowed_domains ..."


class _Inner:
    _api_prefix = "/gw"

    def __init__(self, answer: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer = answer if answer is not None else {"job_id": "job-1"}

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.answer

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": "POST", "url": url, "json": json, **kwargs})
        return self.answer


def _view(**extra: Any) -> dict[str, Any]:
    return {
        "allow_all": False,
        "entries": ["doi.org", "*.doi.org", "*.nature.com", "lab.example.org:8443"],
        "configured": ["@recommended"],
        "how_to_change": _HOW,
        "site": "rccs-front",
        **extra,
    }


def _run(monkeypatch, view: Any, *argv: str) -> tuple[int, list[dict[str, Any]]]:
    """Run the handler against a fake server answer; return (rc, calls)."""
    seen: list[dict[str, Any]] = []

    class _Knowledge:
        @staticmethod
        def ingest_allowlist(**kwargs: Any) -> Any:
            seen.append(kwargs)
            if isinstance(view, Exception):
                raise view
            return view

    class _Client:
        knowledge = _Knowledge()

    monkeypatch.setattr("r4.client.Client", lambda: _Client())
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="knowledge_command")
    from r4._cli.knowledge_allowlist import register_allowlist

    register_allowlist(sub)
    return handle_allowlist(parser.parse_args(["allowlist", *argv])), seen


def test_group_entries_folds_apex_and_subdomains() -> None:
    assert group_entries(["doi.org", "*.doi.org", "*.nature.com", "pypi.org", "h:8443"]) == [
        "[*.]doi.org",
        "*.nature.com",
        "pypi.org",
        "h:8443",
    ]


def test_the_listing_groups_and_says_how_to_change(monkeypatch, capsys) -> None:
    rc, seen = _run(monkeypatch, _view())
    out = capsys.readouterr().out
    assert rc == 0 and seen == [{"check": None, "project_id": None}]
    assert "Site rccs-front: knowledge ingest may fetch 3 web sites:" in out
    assert "[*.]doi.org" in out and "*.nature.com" in out and "lab.example.org:8443" in out
    assert _HOW in out


def test_allow_all_is_said_in_words(monkeypatch, capsys) -> None:
    rc, _ = _run(monkeypatch, _view(allow_all=True, entries=["*"]))
    out = capsys.readouterr().out
    assert rc == 0 and "every public web site" in out and "stay blocked" in out


def test_the_denied_networks_are_named(monkeypatch, capsys) -> None:
    """The site's own network is never fetched, even under "*" (review
    2026-10-25: RIKEN's intranet is public address space)."""
    _run(monkeypatch, _view(allow_all=True, entries=["*"], denied_networks=["134.160.0.0/16"]))
    out = capsys.readouterr().out
    assert "Never fetched (this site's own network): 134.160.0.0/16." in out


def test_an_old_server_without_denied_networks_prints_no_line(monkeypatch, capsys) -> None:
    _run(monkeypatch, _view())
    assert "Never fetched" not in capsys.readouterr().out


# ── wave 79: the caller's ADR-099 web policy in the current project ──────


def _policy(**extra: Any) -> dict[str, Any]:
    return {
        "mode": "allowlist",
        "domains": [".arxiv.org"],
        "set_by": "project:owner",
        "domains_set_by": "project:owner",
        "project_id": "proj-a",
        "per_request": False,
        "entries": ["arxiv.org", "*.arxiv.org"],
        "summary": "web policy 'allowlist' for project 'proj-a': .arxiv.org (set by "
        "the project owner's rule (project:owner)); ingest fetches only hosts on both "
        "this list and the site list",
        **extra,
    }


def test_the_effective_policy_for_the_project_is_shown(monkeypatch, capsys) -> None:
    rc, seen = _run(
        monkeypatch, _view(allow_all=True, entries=["*"], web_policy=_policy()), "-p", "proj-a"
    )
    out = capsys.readouterr().out
    assert rc == 0 and seen == [{"check": None, "project_id": "proj-a"}]
    assert "every public web site" in out  # the site list
    assert "Your web policy 'allowlist' for project 'proj-a'" in out
    assert "(project:owner)" in out
    assert "URL ingest in this project may fetch 1 web sites:" in out
    assert "[*.]arxiv.org" in out


def test_a_policy_none_and_an_unreadable_policy_are_said(monkeypatch, capsys) -> None:
    off = _policy(mode="none", entries=[], summary="web access is off for project 'proj-a'")
    _run(monkeypatch, _view(web_policy=off))
    out = capsys.readouterr().out
    assert "Your web access is off for project 'proj-a'." in out
    assert "URL ingest in this project may fetch" not in out
    _run(monkeypatch, _view(web_policy={"error": "rules unreadable", "mode": None}))
    assert "could not be read: rules unreadable" in capsys.readouterr().out


def test_an_old_server_without_a_policy_prints_no_policy_line(monkeypatch, capsys) -> None:
    _run(monkeypatch, _view())
    assert "web policy" not in capsys.readouterr().out.lower()


def test_an_empty_list_is_said_in_words(monkeypatch, capsys) -> None:
    _run(monkeypatch, _view(entries=[], configured=[]))
    assert "may fetch no web site" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("allowed", "rc", "mark"), [(True, 0, "✓ allowed"), (False, 1, "✗ refused")]
)
def test_check_says_allowed_or_refused(monkeypatch, capsys, allowed, rc, mark) -> None:
    check = {"host": "doi.org", "allowed": allowed, "reason": "why"}
    got, seen = _run(monkeypatch, _view(check=check), "--check", " https://doi.org/10.1/x ")
    out = capsys.readouterr().out
    assert got == rc and f"{mark}: doi.org: why" in out and _HOW in out
    assert seen[0]["check"] == "https://doi.org/10.1/x"


def test_an_empty_check_is_a_usage_error(monkeypatch, capsys) -> None:
    rc, seen = _run(monkeypatch, _view(), "--check", " ")
    assert rc == 2 and seen == [] and "--check takes a URL" in capsys.readouterr().err


def test_a_server_without_check_is_named(monkeypatch, capsys) -> None:
    rc, _ = _run(monkeypatch, _view(), "--check", "doi.org")
    assert rc == 1 and "does not answer --check" in capsys.readouterr().err


def test_json_prints_the_server_answer(monkeypatch, capsys) -> None:
    rc, _ = _run(monkeypatch, _view(), "--json", "-p", "proj-a")
    assert rc == 0 and json.loads(capsys.readouterr().out)["site"] == "rccs-front"


def test_a_server_failure_is_one_line_exit_1(monkeypatch, capsys) -> None:
    rc, _ = _run(monkeypatch, RuntimeError("HTTP 502"), "--json")
    assert rc == 1 and json.loads(capsys.readouterr().out) == {"error": "HTTP 502", "exit_code": 1}


def test_the_command_is_registered_under_knowledge() -> None:
    from r4._cli.knowledge import KnowledgeCommand

    parser = argparse.ArgumentParser()
    KnowledgeCommand().register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["knowledge", "allowlist", "--check", "doi.org"])
    assert (args.knowledge_command, args.check) == ("allowlist", "doi.org")


def test_the_client_reads_the_k2_route() -> None:
    inner = _Inner(_view())
    KnowledgeClient(inner).ingest_allowlist(check="doi.org", project_id="proj-a")
    call = inner.calls[0]
    assert call["url"] == "/gw/knowledge/ingest/ingest/allowlist"
    assert call["params"] == {"check": "doi.org"}
    assert call["headers"] == {"X-Project-ID": "proj-a"}


def test_url_ingest_sends_no_host_list() -> None:
    """The hosts of the URLs used to go as allow_domains, which narrowed the
    site list to them: a DOI's redirect to the publisher was refused."""
    inner = _Inner()
    KnowledgeClient(inner).ingest_paths("proj-a", [], urls=["https://doi.org/10.1038/x"])
    body = inner.calls[0]["json"]
    assert body["sources"] == [{"kind": "http_url", "uri": "https://doi.org/10.1038/x"}]
    assert "allow_domains" not in body
