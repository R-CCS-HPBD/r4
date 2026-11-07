"""What a stack container runs, read from inside it (ADR-093 C0 provenance).

Stdlib only: the harness sends this file's source to ``python -c`` in the
umrs container and in the rag container (``standalone.sh exec umrs|rag``),
and imports it on the host for the same ``files_sha256``, so the checkout's
hash and the server's hash are computed one way and can be compared. It
prints one JSON object:

- ``code``: the sha256 of the umrs code the container's role runs, with the
  newest file mtime (a mounted tree edited after ``started_at`` is not what
  runs). The umrs role: the package ``umrs.knowledge.retrieval``, the routes
  that call it and the K3 route (``SERVER_CODE``). The rag role: the query
  encoder, its prefix and the reranker (``RAG_CODE``);
- ``contracts``: the same for ``r4_contracts`` (both containers mount it: the
  profile table and the pack renderer the server draws every pack with);
- ``started_at``: when the container's PID 1 started (epoch seconds);
- umrs role only: ``rag_stats`` (``GET /rag/stats``: embedder and reranker)
  and ``ollama_tags`` (``GET /api/tags``: chat model digests).

    python server_probe.py [OLLAMA_URL [umrs|rag]]
"""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import os
import sys
import urllib.request

#: The retrieval code a result depends on, relative to the ``umrs`` package:
#: the core, the routes that call it, and the K3 route (the k3-* arms).
SERVER_CODE = (
    "knowledge/retrieval/*.py",
    "api/rag_retrieve.py",
    "api/rag_spine.py",
    "api/rag.py",
    "api/knowledge_query.py",
)
#: What the rag container runs for retrieval: the query encoder and its
#: prefix (``UMRS_RAG_QUERY_PREFIX``), the embedder and the reranker.
RAG_CODE = (
    "api/rag_service.py",
    "rag/search.py",
    "shared/utils.py",
    "knowledge/text_embedder.py",
)
#: Every module of a package (``r4_contracts``, the imported ``r4``).
PACKAGE_CODE = ("**/*.py",)
DEFAULT_OLLAMA_URL = "http://models:11434"
DEFAULT_RAG_URL = "http://rag:8081"
ROLES = {"umrs": SERVER_CODE, "rag": RAG_CODE}


def files_sha256(base: str, patterns: tuple[str, ...] = SERVER_CODE) -> dict:
    """sha256 over (relative path, bytes) of every file the patterns match
    under ``base`` (``**`` recurses), in path order; plus the file count and
    the newest mtime."""
    found = {
        p for pattern in patterns for p in glob.glob(os.path.join(base, pattern), recursive=True)
    }
    files = sorted(p for p in found if os.path.isfile(p))
    digest = hashlib.sha256()
    for path in files:
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        with open(path, "rb") as fh:
            digest.update(rel.encode() + b"\0" + fh.read() + b"\0")
    newest = max((os.path.getmtime(p) for p in files), default=None)
    return {"sha256": digest.hexdigest(), "files": len(files), "mtime_max": newest}


def get_json(url: str) -> object:
    """The JSON at ``url``, or ``"unavailable: <why>"``."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 — fixed in-stack URLs
            return json.loads(response.read().decode())
    except Exception as exc:  # noqa: BLE001 — provenance records the failure
        return f"unavailable: {type(exc).__name__}: {exc}"[:200]


def started_at() -> object:
    """PID 1's start time (epoch seconds) from /proc, or why not."""
    try:
        with open("/proc/stat") as fh:
            boot = next(int(line.split()[1]) for line in fh if line.startswith("btime"))
        with open("/proc/1/stat") as fh:
            ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration) as exc:
        return f"unavailable: {type(exc).__name__}"


def package_dir(name: str) -> str:
    """The directory of an importable package, or ""."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return ""
    return os.path.dirname(spec.origin) if spec and spec.origin else ""


def _hashed(name: str, patterns: tuple[str, ...]) -> tuple[str, object]:
    base = package_dir(name)
    return base, files_sha256(base, patterns) if base else f"unavailable: {name} not importable"


def code_report(role: str = "umrs") -> dict:
    """The code a container of ``role`` runs, and when it started."""
    code_dir, code = _hashed("umrs", ROLES.get(role, SERVER_CODE))
    contracts_dir, contracts = _hashed("r4_contracts", PACKAGE_CODE)
    return {
        "role": role,
        "code_dir": code_dir,
        "code": code,
        "contracts_dir": contracts_dir,
        "contracts": contracts,
        "started_at": started_at(),
    }


def probe(ollama_url: str = DEFAULT_OLLAMA_URL, role: str = "umrs") -> dict:
    out = code_report(role)
    if role != "umrs":
        return out
    rag_url = os.environ.get("UMRS_RAG_DIRECT_URL") or DEFAULT_RAG_URL
    out["rag_stats"] = get_json(rag_url.rstrip("/") + "/rag/stats")
    out["ollama_tags"] = get_json(ollama_url.rstrip("/") + "/api/tags")
    return out


if __name__ == "__main__":
    print(json.dumps(probe(*(sys.argv[1:3] or [DEFAULT_OLLAMA_URL]))))
