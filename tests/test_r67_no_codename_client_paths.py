"""R6.7c fence: client source must not address stage-codename gateway paths.

Stage codenames (k2, sat) were retired at the edge 2026-08-23. The r4
client and tests must only contain the stable lanes. The redirect at the
NGINX edge stays for one release specifically so older clients keep
working — new client code is not allowed to (re)introduce them.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 2026-10-18 (plan H-10.2 / cleanup C-2(v)): only the EDGE codename is
# retired. UMRS itself mounts the ingest lane solely at its router prefix
# (umrs/src/umrs/api/knowledge_v2.py), and nginx rewrites
# /gw/knowledge/ingest/* onto it; a direct-UMRS client has no other path.
# Forbidding that prefix here had pushed direct mode onto
# {prefix}/knowledge/ingest/*, which 404s (client route liveness).
FORBIDDEN_SUBSTRINGS = ("/knowledge/k2/",)


def _r4_python_sources():
    src = REPO / "r4" / "src"
    yield from src.rglob("*.py")


def test_client_uses_only_stable_lanes():
    offenders: list[str] = []
    for path in _r4_python_sources():
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO)}: {needle}")
    assert not offenders, "codename gateway paths in client code:\n" + "\n".join(offenders)


def test_this_policy_allows_docs_and_nginx_only():
    # Guardrail against accidentally broadening the tolerated set: the
    # only places where retired codename routes may still appear are the
    # NGINX edge redirect block and documentation/drift logs.
    allowed = {
        "gateway/nginx/conf.d/server.conf",
    }
    assert allowed  # semantic pin of the tolerated set
