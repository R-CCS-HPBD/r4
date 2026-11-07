# ADR-015: Knowledge Ingest Egress Job Class

**Status:** Accepted
**Date:** 2026-07-12
**Relates to:** ADR-006 (offline inference), ADR-013 (knowledge portability),
INV-09 (RAG/MCP sandbox), INV-13 (knowledge portability), Platform Contract
invariant 2.2 (model download egress monopoly)

## Context

ADR-006 established that inference containers run fully offline. Model download
jobs became the **only** job class with controlled egress (Platform Contract
invariant 2.2), gated by EgressGuard and site-config domain allowlists.

ADR-013 introduced the knowledge portability model: documents, embeddings,
knowledge graphs, and science catalogs must be **acquirable** by the platform
from external sources (HTTP URLs, Git repositories) before they can be stored,
indexed, or ported as `.kb` bundles.

The gap: today there is no sanctioned path for the platform to fetch external
knowledge sources. Users must manually stage files, which defeats portability
and auditability. The same security discipline used for model downloads must
extend to knowledge acquisition.

### Requirements

| ID | Requirement |
|----|-------------|
| FR-1 | `http_url` and `git_repo` acquisition executes ONLY inside a `KNOWLEDGE_INGEST` job of the egress-enabled class (same execution authority tier as model downloads) |
| FR-2 | All knowledge egress flows through EgressGuard with domain allowlist |
| FR-3 | Domain allowlist is site-configurable (`knowledge_ingest.allowed_domains`), default empty = disabled |
| FR-4 | Client-side acquisition of remote sources is forbidden except explicit `--local-fetch` developer flag |
| FR-5 | All knowledge egress is audit-logged (same as model download audit) |

## Decision

### 1. Define KNOWLEDGE_INGEST as a Second Egress-Enabled Job Class

| Attribute | Value |
|-----------|-------|
| Job class | `KNOWLEDGE_INGEST` |
| Execution authority | Same as model downloads (`slurm` on HPC, `k8s` on Kubernetes) |
| Runtime | Same egress-enabled container class as model downloads |
| Egress | Through EgressGuard, domain-allowlisted |
| Default | Disabled (no domains = no egress) |

```yaml
# site.yaml
knowledge_ingest:
  # Domain allowlist (empty = completely disabled)
  allowed_domains:
    - huggingface.co
    - github.com
    - "*.rijken.riken.jp"   # wildcard supported
  # Maximum download size (MB)
  max_download_mb: 10240
  # EgressGuard settings (inherits from egress_guard if not overridden)
  egress_guard:
    log_request_bodies: false
    retention_days: 90

  # Content scanning
  content_scan:
    enabled: true
    block_patterns:
      - "(?i)(password|secret|api_key)\\s*[:=]"
      - "(?i)(sql|drop|alter|truncate)\\s"
```

*Note (2026-10-24, knowledge plan K1.1, wave ledger (70)):* the code reads this block under `extra:` in `site.yaml` (`extra.knowledge_ingest.allowed_domains`, `umrs/src/umrs/knowledge/sources.py`), not at the top level shown above. A caller's `allow_domains` can only narrow the site list; an empty or absent site list refuses every URL and repo ingest. The ingest fetcher connects directly to the address it checked (public addresses only, re-checked on every redirect) and does not use an HTTP proxy. The decision behind that, and what the code does with `max_download_mb` and intranet hosts, is the amendment at the end of this ADR.

### 2. EgressGuard Domain Allowlist Enforcement

The EgressGuard proxy enforces the allowlist at the network layer:

```python
# In EgressGuard middleware
class KnowledgeIngressFilter:
    def __init__(self, allowed_domains: list[str]):
        self.allowed = {d.lower() for d in allowed_domains}

    def __call__(self, request: HTTPRequest) -> AllowOrDeny:
        domain = extract_domain(request.url)
        match = any(is_match(domain, pat) for pat in self.allowed)
        if match:
            return Allow()
        return Deny(reason=f"domain {domain} not in knowledge_ingest allowlist")
```

**Default behavior:** `allowed_domains: []` → all egress denied. Site operator
must explicitly add domains. This is a **fail-closed** default.

### 3. Forbidden Client-Side Acquisition

Remote sources (`http_url`, `git_repo`) must be fetched inside the egress-enabled
job container. Client-side fetching is forbidden because:

1. **Bypasses EgressGuard.** Direct `curl`/`wget` from the client machine has no
   audit, no content scanning, no domain allowlist.
2. **Bypasses size limits.** No server-side enforcement of `max_download_mb`.
3. **No job lifecycle.** Fetches outside UMRS durable job tracking cannot be
   tracked, retried, or audited.

**Exception:** `--local-fetch` developer flag. When a user explicitly requests
client-side acquisition (e.g., for offline development), the flag must:
- Be clearly named as a developer/development-only option
- Require an explicit acknowledgement prompt (not a silent flag)
- Log the action with `source=local_fetch` in the audit trail
- Be excluded from CI and production pipelines

```bash
# Production path (required)
r4 knowledge ingest --source https://huggingface.co/datasets/...

# Developer path (explicit, logged, discouraged in CI)
r4 knowledge ingest --source file:///local/path --local-fetch
```

### 4. Audit Requirements

Knowledge ingest egress audit entries must include:

| Field | Source |
|-------|--------|
| `timestamp` | Job launcher |
| `job_id` | Durable job tracking |
| `attempt_id` | Attempt-scoped execution |
| `user_id` | Authenticated user (JWT) |
| `source_type` | `http_url` or `git_repo` |
| `source_url` | Full URL (redacted if auth credentials present) |
| `egress_action` | `allowed` / `blocked` (EgressGuard) |
| `matched_domain` | Domain that matched allowlist |
| `content_scan` | `clean` / `blocked:<reason>` |
| `bytes_downloaded` | Size of fetched content |
| `destination` | S3 path or local staging directory |

Audit log format follows the same JSON Lines pattern as model download audit
(Section 3.4 of the operational runbook).

### 5. Relationship to Model Download Precedent

| Aspect | Model Download | Knowledge Ingest |
|--------|---------------|------------------|
| Job class | `MODEL_DOWNLOAD` | `KNOWLEDGE_INGEST` |
| Execution authority | `slurm` / `k8s` (same) | `slurm` / `k8s` (same) |
| Egress | EgressGuard, domain allowlist | EgressGuard, domain allowlist |
| Default | Disabled | Disabled |
| Audit | Immutable log, 30-day retention | Immutable log, 90-day retention (longer: compliance) |
| Size limit | Configured per site | Configured per site |
| Content scan | Not applicable (binary weights) | Required (documents may contain PII) |
| Reusability | Model registry dedup | Document dedup via content hash |

**Key difference:** Knowledge ingest requires content scanning (PII, SQL, etc.)
because it processes user-supplied text/structured data, not opaque binary
weights. The content scanner blocks downloads matching `block_patterns`.

## Consequences

### Positive

- **Auditability.** All remote knowledge sources flow through controlled egress
  with full audit trail. No shadow fetches.
- **Fail-closed.** Default empty allowlist means nothing downloads until the
  operator explicitly permits domains.
- **Consistent security model.** Same EgressGuard proxy, same container
  isolation, same durable job tracking as model downloads.
- **Compliance-ready.** 90-day retention, content scanning, and per-user audit
  support regulatory requirements.
- **Developer ergonomics.** `--local-fetch` flag enables offline development
  without sacrificing production security.

### Negative

- **Deployment burden.** Site operators must configure `knowledge_ingest.allowed_domains`
  for any external knowledge acquisition. Empty = disabled.
- **Content scanning latency.** Document content scanning adds 100-500ms per file
  to the ingest pipeline.
- **Operational complexity.** Two egress-enabled job classes (MODEL_DOWNLOAD +
  KNOWLEDGE_INGEST) share the same container infrastructure. Must keep them
  in sync during runtime updates.

### Risks

| Risk | Mitigation |
|------|-----------|
| Domain allowlist misconfiguration (too permissive) | Validation on site.yaml load: warn on `*.*.*` wildcards, require explicit opt-in for multi-level wildcards |
| Content scanner false negatives | Periodic signature updates, allowlist fallback, manual review queue |
| `--local-fetch` leakage into production | CI lint excludes `--local-fetch` flag, operator check in production readiness gate |
| EgressGuard proxy becomes single point of failure | Health check in orchestrator, fallback to denied (fail-closed) on proxy unreachable |

## Implementation Checklist

- [ ] Define `KNOWLEDGE_INGEST` job class in UMRS job scheduler
- [ ] Add `knowledge_ingest.allowed_domains` to site.yaml schema
- [ ] Wire domain allowlist into EgressGuard middleware
- [ ] Implement content scanner for knowledge ingest jobs
- [ ] Add `--local-fetch` flag to `r4 knowledge ingest` CLI with acknowledgement
- [ ] Add audit log entry type for knowledge egress
- [ ] Update `MODEL_DOWNLOAD` audit retention from 30 to 90 days (align with KNOWLEDGE_INGEST)
- [ ] Add `adr_linkage_validator` test case for this ADR

## Alternatives Considered

- **Single egress class.** Merge KNOWLEDGE_INGEST into MODEL_DOWNLOAD. Rejected:
  different content scanning requirements, different retention policies, different
  audit fields. Separate class keeps concerns clean.
- **Gateway-mediated fetch.** Have Gateway fetch URLs on behalf of the client,
  then stage to S3. Rejected: Gateway is stateless proxy only (mutation safety
  invariant); adding fetch logic creates state management complexity.
- **Manual staging only.** No egress at all; operators manually upload files.
  Rejected: Defeats ADR-013 portability goal, creates operational bottleneck.

## Amendment (2026-10-24): the ingest fetcher connects directly

**Status:** Accepted. Decided by the agent under the maintainer's delegation
of 2026-10-24 (knowledge plan K1.1 and its security review, wave ledger (70, review round));
the maintainer can reverse it.

**Context.** K1.1 replaced `urllib.request.urlopen` in
`umrs/src/umrs/knowledge/egress_fetch.py`, which had no private-address
block, checked the domain on the first URL only and read the whole body
before the cap. This ADR's FR-2 routes knowledge egress through the
EgressGuard proxy. No such proxy exists for `KNOWLEDGE_INGEST` in any
deployment, and a proxy that resolves the name itself would undo the address
check below.

**Decision.**

1. The fetcher connects **directly** to the address it checked. On every hop
   (the first URL and each redirect, at most 5) it checks that the scheme is
   http(s) and that the host is on the allowlist. It resolves the name once
   and refuses the hop if any answer is not a global unicast address
   (private, loopback, link-local, multicast, reserved, unspecified, CGNAT,
   IPv4-mapped forms unwrapped, cloud metadata). The connection then goes to
   that address, with TLS still checking the host name.
   `HTTP(S)_PROXY` is not used. A network-layer EgressGuard in front of the
   container, if a site deploys one, stays an extra layer; it is not the
   control.
2. **Ports:** 80 and 443 only, unless the allowlist entry names the port
   (`docs.example.org:8443`). An https hop never redirects down to http.
3. **Bounds:** `EGRESS_MAX_BYTES` (default 10 MiB) caps the body while it
   streams, and a larger Content-Length is refused up front. A body shorter
   than its Content-Length is refused. `EGRESS_TOTAL_SEC` (default 120 s)
   bounds the whole fetch, every hop and every read; `EGRESS_TIMEOUT_SEC`
   (default 30 s) bounds each connect and read. `max_download_mb` in the
   block above is **not read**. The environment cap is the one in force.
4. **Intranet hosts are refused.** A name on the allowlist that resolves to
   an RFC 1918 or other non-global address (this ADR's own example,
   `*.rijken.riken.jp`, may) is refused. Allowing it would need an explicit
   site list of private ranges (`allowed_private_cidrs`), and that is a
   separate decision for the maintainer. None is built.

**Consequences.** URL and repo ingest works only from public hosts, and only
once a front's `site.yaml` sets `extra.knowledge_ingest.allowed_domains`.
Tests: `umrs/tests/test_k1_governance_egress.py` covers the pinned connect,
mixed DNS answers, mapped IPv6, redirects, ports, the downgrade, the
truncated body and the total deadline.

*Note (2026-10-25):* `extra.knowledge_ingest.allowed_domains` takes two
more entry forms. `@recommended` expands to the curated list in
`umrs/src/umrs/knowledge/ingest_domains_recommended.yaml` (packaged with
umrs) and combines with other entries; `*` admits every host name. The
guards in this amendment are unchanged under both: global unicast addresses
only (every resolved address), the pinned connection, ports 80/443 unless an
entry names `host:port` (`*:port` is refused at load and opens nothing),
every redirect hop re-checked, the size and time caps, the content scan. An
unknown `@` entry is refused at load and the rest apply. Expansion happens
in one place (`umrs/src/umrs/knowledge/ingest_allowlist.py`); a caller list
still only narrows the site list. All four fronts ship `["*"]` with
`denied_networks: ["134.160.0.0/16"]` (maintainer, 2026-10-25: an agent can
already fetch any public page and save it, so the guards above, not a domain
list, are what protects the server; `@recommended` is for stricter sites).
Users read the list in force through `GET /umrs/k2/ingest/allowlist`
(`r4 knowledge allowlist`), and a refusal names the host and the setting
instead of printing the list. `r4`'s URL ingest no longer sends the URLs'
own hosts as its list, which refused every redirect to another host (a DOI
to its publisher). Tests: `umrs/tests/test_k1_ingest_allowlist.py`.

*Note (2026-10-25, review):* the global-unicast check does not keep a fetch
inside the internet: RIKEN's 134.160.0.0/16 is global, so a listed
`*.riken.jp` name (or `*`) reached intranet hosts and umrs-front itself. The
recommended list now names RIKEN's public sites only (`www.riken.jp`,
`www.r-ccs.riken.jp`), and `extra.knowledge_ingest.denied_networks` (CIDR)
refuses every resolved address of every hop inside a listed range; all four
fronts set `["134.160.0.0/16"]`. `*.` is read as `*`; the server logs the
list in force, refused entries and the `*` warning at start.

*Note (2026-10-25, wave (79)):* the site list is no longer the only bound.
The requester's ADR-099 web policy in the job's project narrows it: `none`
refuses URL and git ingest, `allowlist` keeps only the `web.domains` the
agent may browse there (squid `dstdomain` matching, redirects included),
`public` leaves the site list. The guards above apply in every mode.
Decision and details: [ADR-099 Amendment 5](099-session-policy-and-quotas.md#amendment-5-2026-10-25-knowledge-url-ingest-follows-the-web-policy).
