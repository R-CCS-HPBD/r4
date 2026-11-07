#!/usr/bin/env bash
# D-27 contract suite for the platform_ops builtin skill.
# Pins the platform-ops invariants: health-before-mutation, Gateway-only
# LLM traffic, endpoint lifecycle order, the Job/Attempt model, and the
# offline-inference egress rule.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "platform_ops skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: platform_ops' "$SKILL_MD" || fail "front-matter name must be 'name: platform_ops'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"

allowed=$(grep '^allowed-tools: "' "$SKILL_MD" | head -n 1) || true
[ -n "$allowed" ] || fail "front-matter allowed-tools missing"
for tool in platform_health endpoint_list endpoint_status endpoint_provision \
    endpoint_stop job_list job_status job_cancel job_retry; do
    grep -q "$tool" <<<"$allowed" || fail "allowed-tools must grant $tool"
done
grep -q 'x-r4-keywords:' "$SKILL_MD" || fail "metadata x-r4-keywords missing"

# Health before mutation; Gateway-only LLM traffic
grep -q 'verify the platform is reachable before any mutation' "$SKILL_MD" || fail "health-before-mutation rule missing"
grep -q 'Route ALL LLM traffic through the Gateway' "$SKILL_MD" || fail "Gateway-routing rule missing"
grep -q 'never to vLLM' "$SKILL_MD" || fail "direct-vLLM prohibition missing"

# Endpoint lifecycle order
grep -q 'ALWAYS first' "$SKILL_MD" || fail "endpoint_list-first rule missing"
grep -q 'reuse an existing ready endpoint' "$SKILL_MD" || fail "reuse-before-duplicate rule missing"
grep -q 'poll .endpoint_status.' "$SKILL_MD" || fail "poll-endpoint_status rule missing"

# Job/Attempt model
grep -q 'Job = immutable intent' "$SKILL_MD" || fail "Job-immutability rule missing"
grep -q 'Attempt = one execution try' "$SKILL_MD" || fail "Attempt-definition missing"
grep -q 'never submit a duplicate job' "$SKILL_MD" || fail "no-duplicate-jobs rule missing"

# Constraints
grep -q 'Singularity on HPC' "$SKILL_MD" || fail "Singularity-runtime rule missing"
grep -q 'KNOWLEDGE_INGEST jobs have egress' "$SKILL_MD" || fail "offline-inference egress rule missing"

echo "platform_ops skill contract green: $SKILL_MD"
