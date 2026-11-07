"""The built-in ``web_search`` backend (2026-10-30): a SearXNG instance.

``R4_WEB_SEARCH_URL`` names the instance (``https://searx.example.org``);
unset, the local one ``r4 search-server up`` started is used while it answers
(``r4.agent.searxng_local``, ADR-102). The backend calls the instance's JSON
API (``/search?format=json``, which the instance must enable in
``search.formats``). With neither, there is no backend and the availability
gate does not offer ``web_search``. The companion runs it only while the
session's web access is ``public`` (ADR-099 D-9, ``companion/web_access.py``);
a callback set with ``r4.agent.tools.set_web_search_callback`` replaces it.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

#: The SearXNG base URL; unset = the local instance, if one runs.
SEARXNG_URL_ENV = "R4_WEB_SEARCH_URL"

_TIMEOUT_SEC = 15
_USER_AGENT = "r4-agent/1.0"


def base_url() -> Optional[str]:
    """``R4_WEB_SEARCH_URL``, else the running local instance, else None."""
    named = os.environ.get(SEARXNG_URL_ENV, "").strip()
    if named:
        return named
    from r4.agent.searxng_local import local_url

    return local_url()


def configured() -> bool:
    """True when a search instance is named or running locally."""
    return base_url() is not None


def search(query: str, num_results: int) -> List[Dict[str, str]]:
    """Up to *num_results* ``{title, snippet, url}`` hits for *query*."""
    base = base_url()
    if not base:
        raise RuntimeError(
            f"no web search backend configured (run `r4 search-server up` or set {SEARXNG_URL_ENV})"
        )
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    payload = json.loads(_get(url))
    return [
        {
            "title": str(hit.get("title") or ""),
            "snippet": str(hit.get("content") or ""),
            "url": str(hit.get("url") or ""),
        }
        for hit in payload.get("results", [])[:num_results]
    ]


def _get(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as resp:
        body: bytes = resp.read()
    return body.decode("utf-8", errors="replace")
