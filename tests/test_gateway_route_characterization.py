"""S4 characterization: pin the NGINX gateway route table (server.conf).

Wave: UMRS Stability & Governed Surface Alignment (S1–S12). Premise tests
— they encode CURRENT routing behavior so the S6 `/gw/v1` + `/gw/v1beta`
route publication and S7 internal-tier enforcement fail loudly here before
silently changing the production edge.

Not correctness judgments: every table below was recorded from the live
config on 2026-08-17 and must be updated consciously, with a wave-log
entry, when the route table moves.
"""

from __future__ import annotations

import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "gateway" / "nginx" / "conf.d" / "server.conf"

# Auth mode per /gw location: everything is "required" (auth_request
# /auth/validate) except these documented probes and guards.
GW_AUTH_EXCEPTIONS: dict[str, str] = {
    "= /gw/ops/ping": "none",  # anonymous login handshake probe
    "= /gw/ops/connect-info": "optional",  # validate-optional; s3cfg gated
    # S6 lane guards: 404 before any auth subrequest, by design — a guard
    # exposes nothing (unlisted families simply have no v-lane surface).
    "/gw/v1/": "none",
    "/gw/v1beta/": "none",
    # Cluster job write-back lane (WP-05 round 2, user-approved 2026-09-10):
    # no session auth_request — durable machine identity arrives pre-signed
    # (X-Verified-HMAC over UMRS_HANDSHAKE_HMAC_SECRET) and UMRS strict-mode
    # validates the signature itself. Narrow surface: loop routes only.
    "/gw/loopjob/": "none",
    "/gw/loopjob/approvals/": "none",  # same HMAC contract; park+decide routes
    # Cluster-driver workflow lane (WP-05 r2 venue leg, 2026-09-17): same
    # HMAC-pre-signed contract as the loopjob write-back lane — the engine
    # triggers registered workflows through it (trigger/status/results/cancel).
    "/gw/loopjob/workflows/": "none",
    # S4 codename retirement (f9759a5d, 2026-08-23): "k2" became
    # /gw/knowledge/ingest/ at the edge. These three blocks only `return 308`
    # — they reach no upstream and disclose nothing, so an auth subrequest
    # would buy nothing; the 308 target /gw/knowledge/ingest/ carries the
    # full auth_request /auth/validate + verified-header set. Enforced
    # structurally by test_redirect_only_blocks_reach_no_upstream below, so
    # this exemption cannot silently grow into an unauthenticated proxy.
    "= /gw/knowledge/k2": "none",
    "= /gw/knowledge/ingest": "none",
}

# a71d2ed4: the legacy /gw/knowledge/k2/* 308 needs a REGEX location because
# it carries a named capture into the redirect target.
LEGACY_K2_REDIRECT_REGEX = "~ ^/gw/knowledge/k2/(?<kg_path>.*)$"

# Blocks exempt from auth ONLY because they are pure 308 redirects. Every
# member must prove it (below); the 308 targets themselves authenticate.
REDIRECT_ONLY_BLOCKS: set[str] = {
    "= /gw/knowledge/k2",
    "= /gw/knowledge/ingest",
    LEGACY_K2_REDIRECT_REGEX,
}

# Prefixed /gw/<family>/ blocks whose upstream shape does NOT follow the
# default rule (rewrite ^/gw/<fam>/(.*)$ /umrs/<fam>/$1 → proxy http://umrs).
# rewrite_target=None means "no rewrite — the URI rides inside proxy_pass".
GW_PREFIX_EXCEPTIONS: dict[str, dict[str, str | None]] = {
    # A2L is served by UMRS's a2l-ledger router on the a2l upstream (c4d938f4,
    # 2026-10-09: the bare /$1 rewrite 404'd once the standalone service went).
    "/gw/a2l/": {"rewrite_target": "/umrs/a2l/ledger/$1", "proxy_pass": "http://a2l"},
    # Stable ingest lane still lands on the versioned server prefix
    # /umrs/k2/* — only the GATEWAY prefix was renamed (f9759a5d, S4).
    "/gw/knowledge/ingest/": {"rewrite_target": "/umrs/k2/$1", "proxy_pass": "http://umrs"},
    # Data-plane exceptions (stream/fetch paths, never LLM bodies).
    "/gw/files/upload": {"rewrite_target": None, "proxy_pass": "http://umrs/umrs/files/upload"},
    # NOTE: /gw/files/download/ left the exception table in c0ddc7cb
    # (2026-08-26) — the sidecar validate-download presign chain was replaced
    # by UMRS-side streaming, so the lane now follows the default rule
    # (rewrite -> /umrs/files/download/$1, proxy http://umrs) and keeps
    # auth_request plus the verified-identity headers. Presigned URLs stay
    # internal-only (CONSTITUTION §2): content streams via UMRS's own S3
    # client, never a presigned URL handed to an external client.
    "/gw/batch/": {"rewrite_target": None, "proxy_pass": "http://umrs/batch/"},
    # S6 lane guards: no rewrite and no upstream — they only return 404
    # for families outside the lane allowlists.
    "/gw/v1/": {"rewrite_target": None, "proxy_pass": None},
    "/gw/v1beta/": {"rewrite_target": None, "proxy_pass": None},
    # Cluster job lane lands on the server's internal loop prefix (the lane
    # name intentionally differs from the server family to keep the session
    # lane /gw/loops/ untouched).
    "/gw/loopjob/": {"rewrite_target": "/umrs/loops/$1", "proxy_pass": "http://umrs"},
    "/gw/loopjob/approvals/": {
        "rewrite_target": "/umrs/companion/approvals/$1",
        "proxy_pass": "http://umrs",
    },
    "/gw/loopjob/workflows/": {
        "rewrite_target": "/umrs/workflows/$1",
        "proxy_pass": "http://umrs",
    },
}

# S6 lane publication: one regex allowlist block per lane, sourced from
# the S2/S3 classification. Guards (above) 404 anything outside these.
V1_LANE_REGEX = "~ ^/gw/v1/(endpoints|models|llm)(/.*)?$"
V1BETA_LANE_REGEX = "~ ^/gw/v1beta/(knowledge|k2|loops|research)(/.*)?$"
LANE_GUARDS: set[str] = {"/gw/v1/", "/gw/v1beta/"}

# Dynamic proxy_pass is a containment risk; it is pinned to exactly these
# blocks (dynamic S3 presigned fetch stays internal; stream URLs are
# sidecar-issued per request).
DYNAMIC_PROXY_PASS: dict[str, str] = {
    "/internal/s3/": "$arg_url",
    "/gw/stream/": "$upstream_url",
}

# SSE lanes (long-lived streams; buffering + cache must stay off).
SSE_LANES: set[str] = {"/gw/llm/", "/gw/chat/", "/gw/companion/"}

# Internal locations must never answer external requests or run the auth
# sidecar (they exist only as X-Accel/presigned targets).
INTERNAL_LOCATIONS: set[str] = {"/internal/files/", "/internal/s3/"}


def _location_blocks() -> dict[str, str]:
    """location path → block body, extracted with brace matching."""
    text = CONF.read_text()
    blocks: dict[str, str] = {}
    for match in re.finditer(r"^[ \t]*location([^{]+)\{", text, re.M):
        name = match.group(1).strip()
        depth, j = 1, match.end()
        while depth and j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        blocks[name] = text[match.start() : j]
    return blocks


def _gw_blocks() -> dict[str, str]:
    return {n: b for n, b in _location_blocks().items() if n.lstrip("= ").startswith("/gw/")}


def _rewrite_target(body: str) -> str | None:
    match = re.search(r"rewrite\s+\^\S*\s+(\S+?)\s+break;", body)
    return match.group(1) if match else None


def _proxy_pass_values(body: str) -> list[str]:
    return re.findall(r"proxy_pass\s+(\S+);", body)


def _auth_mode_of(body: str) -> str:
    if "auth_request /auth/validate;" in body:
        return "required"
    if "auth_request /auth/validate-optional;" in body:
        return "optional"
    return "none"


def _expected_rewrite_target(name: str) -> str | None:
    if name in REDIRECT_ONLY_BLOCKS:
        return None
    if name.startswith("= "):
        return "/umrs/" + name[2:].removeprefix("/gw/")
    if name in GW_PREFIX_EXCEPTIONS:
        return GW_PREFIX_EXCEPTIONS[name]["rewrite_target"]
    fam = name.removeprefix("/gw/").strip("/")
    return f"/umrs/{fam}/$1"


def _expected_proxy_passes(name: str) -> list[str]:
    if name in REDIRECT_ONLY_BLOCKS:
        return []
    if name in GW_PREFIX_EXCEPTIONS:
        expected = GW_PREFIX_EXCEPTIONS[name]["proxy_pass"]
        return [] if expected is None else [str(expected)]
    return ["http://umrs"]


def _static_gw_blocks() -> dict[str, str]:
    return {n: b for n, b in _gw_blocks().items() if n not in DYNAMIC_PROXY_PASS}


# ── premise: every gateway route authenticates except documented probes ────


def test_every_gw_route_authenticates_except_documented_probes():
    blocks = _gw_blocks()
    violations = [
        f"{name}: auth mode {_auth_mode_of(body)} != {GW_AUTH_EXCEPTIONS.get(name, 'required')}"
        for name, body in blocks.items()
        if _auth_mode_of(body) != GW_AUTH_EXCEPTIONS.get(name, "required")
    ]
    violations += [
        f"{name}: documented auth exception not served"
        for name in sorted(set(GW_AUTH_EXCEPTIONS) - set(blocks))
    ]
    assert not violations, "gateway auth coverage drifted:\n" + "\n".join(violations)


def test_redirect_only_blocks_reach_no_upstream():
    """Redirect-only blocks are auth-exempt ONLY while they stay redirects.

    If one ever grows a proxy_pass it becomes an unauthenticated route to an
    upstream (CONSTITUTION §2), so the exemption is re-proved from the
    config on every run rather than trusted from the table above.
    """
    blocks = _location_blocks()
    violations: list[str] = []
    for name in sorted(REDIRECT_ONLY_BLOCKS):
        body = blocks.get(name)
        if body is None:
            violations.append(f"{name}: redirect-only block missing")
            continue
        if not re.search(r"^\s*return 308 /gw/knowledge/ingest/", body, re.M):
            violations.append(f"{name}: must be a 308 to the authenticated ingest lane")
        if _proxy_pass_values(body):
            violations.append(f"{name}: auth-exempt redirect must never proxy")
        if _rewrite_target(body):
            violations.append(f"{name}: auth-exempt redirect must never rewrite upstream")
    assert not violations, "redirect-only discipline drifted:\n" + "\n".join(violations)


def test_redirect_targets_are_authenticated():
    """The lane every auth-exempt redirect points at must require auth."""
    body = _gw_blocks().get("/gw/knowledge/ingest/")
    assert body is not None, "308 target /gw/knowledge/ingest/ is not served"
    assert _auth_mode_of(body) == "required", (
        "the 308 target lost auth_request — the redirect exemption above assumed it authenticates"
    )


# ── premise: rewrite/proxy targets match the recorded table ───────────────


def test_family_rewrites_match_expected_targets():
    violations = [
        f"{name}: rewrite {_rewrite_target(body)!r} != {_expected_rewrite_target(name)!r}"
        for name, body in _static_gw_blocks().items()
        if _rewrite_target(body) != _expected_rewrite_target(name)
    ]
    assert not violations, "gateway rewrite table drifted:\n" + "\n".join(violations)


def test_family_proxy_passes_match_expected_targets():
    violations = [
        f"{name}: proxy_pass {_proxy_pass_values(body)} != {_expected_proxy_passes(name)}"
        for name, body in _static_gw_blocks().items()
        if _proxy_pass_values(body) != _expected_proxy_passes(name)
    ]
    assert not violations, "gateway proxy table drifted:\n" + "\n".join(violations)


def test_exceptions_table_matches_served_blocks():
    missing = sorted(set(GW_PREFIX_EXCEPTIONS) - set(_gw_blocks()))
    assert not missing, "documented rewrite exceptions absent from the route table:\n" + "\n".join(
        missing
    )


# ── premise: internal locations stay internal-only and unauthenticated ─────


def test_internal_locations_are_internal_only_and_unauthenticated():
    blocks = _location_blocks()
    missing = sorted(INTERNAL_LOCATIONS - set(blocks))
    assert not missing, f"internal locations absent: {missing}"
    violations = []
    for name in sorted(INTERNAL_LOCATIONS):
        body = blocks[name]
        if not re.search(r"^\s*internal;", body, re.M):
            violations.append(f"{name}: missing internal; marker")
        if "auth_request" in body:
            violations.append(f"{name}: must not run the auth sidecar")
    assert not violations, "internal-location discipline drifted:\n" + "\n".join(violations)


# ── premise: dynamic proxy_pass is contained to its pinned blocks ──────────


def test_dynamic_proxy_pass_is_contained():
    observed: dict[str, str] = {}
    for name, body in _location_blocks().items():
        for value in _proxy_pass_values(body):
            if value.startswith("$"):
                observed[name] = value
    assert observed == DYNAMIC_PROXY_PASS, (
        f"dynamic proxy_pass surface moved: {observed} (premise: {DYNAMIC_PROXY_PASS})"
    )


# ── premise: SSE lanes stay unbuffered and uncached ────────────────────────


def test_sse_lanes_unbuffered_and_uncached():
    blocks = _gw_blocks()
    missing = sorted(SSE_LANES - set(blocks))
    assert not missing, f"SSE lanes absent from route table: {missing}"
    violations = [
        f"{name}: SSE lane lost proxy_buffering/proxy_cache off"
        for name in sorted(SSE_LANES)
        if "proxy_buffering off;" not in blocks[name] or "proxy_cache off;" not in blocks[name]
    ]
    assert not violations, "SSE lane drift:\n" + "\n".join(violations)


# ── premise: the m1 companion fold-in is live on the edge ──────────────────


def test_companion_fold_in_present():
    blocks = _gw_blocks()
    body = blocks.get("/gw/companion/")
    assert body is not None, "m1 companion fold-in missing: no /gw/companion/ block"
    assert "auth_request /auth/validate;" in body, "companion lane lost auth"
    assert _rewrite_target(body) == "/umrs/companion/$1", (
        f"companion rewrite drifted: {_rewrite_target(body)!r}"
    )


# ── premise: S6 lane publication (guards + regex allowlist blocks) ─────────


def test_lane_guards_return_404_and_never_proxy():
    blocks = _gw_blocks()
    violations = []
    for name in sorted(LANE_GUARDS):
        body = blocks.get(name)
        if body is None:
            violations.append(f"{name}: S6 guard missing")
            continue
        if not re.search(r"^\s*return 404;", body, re.M):
            violations.append(f"{name}: guard must return 404")
        if _proxy_pass_values(body):
            violations.append(f"{name}: guard must not proxy")
    assert not violations, "S6 guard discipline drifted:\n" + "\n".join(violations)


def test_lane_regex_blocks_published():
    blocks = _location_blocks()
    for regex in (V1_LANE_REGEX, V1BETA_LANE_REGEX):
        body = blocks.get(regex)
        assert body is not None, f"S6 lane block missing: {regex}"
        assert "auth_request /auth/validate;" in body, f"{regex}: lane lost auth"
        assert _rewrite_target(body) == "/umrs/$1", (
            f"{regex}: rewrite drifted to {_rewrite_target(body)!r}"
        )
        assert "proxy_pass http://umrs;" in body, f"{regex}: must proxy to umrs"
        assert "proxy_buffering off;" in body, f"{regex}: SSE lane lost buffering-off"
        assert "proxy_cache off;" in body, f"{regex}: SSE lane lost cache-off"


def test_lane_publication_contained_to_two_regex_blocks():
    """Regex /gw locations are the two S6 lanes plus the S4 legacy redirect.

    a71d2ed4 made /gw/knowledge/k2/* a regex block because the 308 needs a
    named capture ((?<kg_path>...)). It publishes no surface — it is covered
    by test_redirect_only_blocks_reach_no_upstream — so it is enumerated
    here rather than relaxing the containment check.
    """
    regex_gw = {n for n in _location_blocks() if n.startswith("~") and "/gw/" in n}
    assert regex_gw == {V1_LANE_REGEX, V1BETA_LANE_REGEX, LEGACY_K2_REDIRECT_REGEX}, (
        f"unexpected regex /gw locations (lane publication must stay explicit): {regex_gw}"
    )


# ── premise: S7 internal-tier enforcement ───────────────────────────────────

# Registry authority is umrs.shared.lanes; this pinned copy exists because
# parent-repo tests do not cross the submodule import boundary. Drift is
# caught umrs-side (test_lane_registry pins the same trio).
INTERNAL_FAMILIES: set[str] = {"ops", "skills", "zmq"}

# Every external block serving an internal family must carry the S7 lane
# marker (X-API-Lane: internal + Deprecation headers). /umrs/zmq/ is the
# documented load-bearing raw passthrough (r4 client ZMQ bootstrap).
INTERNAL_LANE_BLOCKS: set[str] = {
    "/gw/ops/",
    "= /gw/ops/connect-info",
    "/gw/skills/",
    "= /gw/skills",
    "/gw/zmq/",
    "/umrs/zmq/",
}


def _lane_family_allowlist(regex: str) -> set[str]:
    """Exact family tokens of a lane regex alternation — substring checks
    false-positive on names nested inside others (loops ⊃ ops)."""
    group = re.search(r"\(([^)]+)\)", regex)
    assert group, f"lane regex carries no family alternation: {regex}"
    return set(group.group(1).split("|"))


def test_internal_families_never_in_lane_allowlists():
    published = set().union(
        *(_lane_family_allowlist(r) for r in (V1_LANE_REGEX, V1BETA_LANE_REGEX))
    )
    leaked = INTERNAL_FAMILIES & published
    assert not leaked, (
        f"internal families surfaced in lane allowlists: {sorted(leaked)} — "
        "lane edits must never publish the internal tier"
    )


def test_lane_allowlists_cover_only_classified_families():
    """The lane allowlists must exactly match the S2/S3 classification."""
    assert _lane_family_allowlist(V1_LANE_REGEX) == {"endpoints", "models", "llm"}
    assert _lane_family_allowlist(V1BETA_LANE_REGEX) == {
        "knowledge",
        "k2",
        "loops",
        "research",
    }  # 2026-09-17 (ADR-026 retire-law complete): knowledge-graph dropped — route unmounted already


def test_internal_blocks_carry_lane_marker():
    blocks = _location_blocks()
    violations: list[str] = []
    for name in sorted(INTERNAL_LANE_BLOCKS):
        body = blocks.get(name)
        if body is None:
            violations.append(f"{name}: internal block missing")
            continue
        if 'add_header X-API-Lane "internal" always;' not in body:
            violations.append(f"{name}: missing X-API-Lane internal marker")
        if 'add_header Deprecation "true" always;' not in body:
            violations.append(f"{name}: missing Deprecation header")
    assert not violations, "S7 lane-marker drift:\n" + "\n".join(violations)


def test_lane_guard_rejects_internal_paths_by_construction():
    """The published lanes structurally 404 internal families (S6 proof:
    /gw/v1/ops/ping → 404 live). Premise: guard set covers the lane roots
    internal paths would traverse."""
    assert not (LANE_GUARDS & INTERNAL_LANE_BLOCKS), (
        "guards must only cover lane roots, never internal blocks"
    )


# ── premise: ADR-027 workflow-author claim forwarded on uploads ────────────


def test_workflows_block_forwards_workflow_author_claim():
    body = _location_blocks().get("/gw/workflows/")
    assert body is not None, "workflows prefix block missing"
    for token in (
        "$auth_verified_workflow_author",
        "$auth_verified_workflow_author_hmac",
        "proxy_set_header X-Verified-Workflow-Author $auth_verified_workflow_author;",
        "proxy_set_header X-Verified-Workflow-Author-HMAC $auth_verified_workflow_author_hmac;",
    ):
        assert token in body, f"ADR-027: workflows block must forward {token}"
