#!/usr/bin/env bash
# D-27 contract suite for the umrs builtin skill.
# Pins the UMRS baseline-skill invariants: front-matter tool grants, the
# mechanically-current inventory block, the bridge-verb registry, and the
# slash-suggestion guardrails. Runs anywhere bash + coreutils exist.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "umrs skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: umrs' "$SKILL_MD" || fail "front-matter name must be 'name: umrs'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"

allowed=$(grep '^allowed-tools: "' "$SKILL_MD" | head -n 1) || true
[ -n "$allowed" ] || fail "front-matter allowed-tools missing"
for tool in platform_health endpoint_list endpoint_status job_list job_status \
    list_models source_list source_add source_use source_remove \
    pool_up pool_down endpoint_upgrade; do
    grep -q "$tool" <<<"$allowed" || fail "allowed-tools must grant $tool"
done

# Inventory block (mechanically current) must stay well-formed
grep -q '<!-- INVENTORY:START -->' "$SKILL_MD" || fail "inventory block START marker missing"
grep -q '<!-- INVENTORY:END -->' "$SKILL_MD" || fail "inventory block END marker missing"

# Bridge verb registry must be named (audit-coverage contract). ADR-105 S4b:
# the skill points at it; the inventory lives in references/ (not a prompt).
grep -q 'BRIDGE_RPC_NAMES' "$SKILL_MD" || fail "bridge-verb registry (BRIDGE_RPC_NAMES) pointer missing"
BRIDGE_MD="$(dirname "$SKILL_MD")/references/bridge-verbs.md"
grep -q 'BRIDGE_RPC_NAMES' "$BRIDGE_MD" || fail "bridge-verb inventory (references/bridge-verbs.md) missing"

# Slash catalog must carry the key read-only control surfaces
for slash in /health /quota /job /traces /rag /catalog /usage; do
    grep -q "$slash" "$SKILL_MD" || fail "slash catalog must list $slash"
done

# Suggestion guardrails
grep -q 'never invent command names' "$SKILL_MD" || fail "guardrail 'never invent command names' missing"
grep -q 'never suggest twice in the same turn' "$SKILL_MD" || fail "guardrail 'never suggest twice' missing"
grep -q 'never suggest a slash if the user' "$SKILL_MD" || fail "guardrail 'never suggest same family' missing"

# Referenced schema doc must ship with the skill
[ -f "$(dirname "$SKILL_MD")/references/experience-schema.md" ] || fail "references/experience-schema.md missing"

echo "umrs skill contract green: $SKILL_MD"
