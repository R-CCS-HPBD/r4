#!/usr/bin/env bash
# D-27 contract suite for the mcp_tools builtin skill.
# Pins the MCP-skill invariants: tool grants, the discover-then-invoke
# order, registration verification, the mounted-server sandbox profile,
# the EgressGuard rule, and verbatim protocol errors.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "mcp_tools skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: mcp_tools' "$SKILL_MD" || fail "front-matter name must be 'name: mcp_tools'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"

allowed=$(grep '^allowed-tools: "' "$SKILL_MD" | head -n 1) || true
[ -n "$allowed" ] || fail "front-matter allowed-tools missing"
for tool in mcp_list_servers mcp_list_tools mcp_invoke \
    mcp_register_server mcp_unregister_server mcp_refresh; do
    grep -q "$tool" <<<"$allowed" || fail "allowed-tools must grant $tool"
done
grep -q 'x-r4-keywords:' "$SKILL_MD" || fail "metadata x-r4-keywords missing"

# Invocation order: discover → read schemas → invoke (never guess)
grep -q 'mcp_list_servers` — discover' "$SKILL_MD" || fail "invocation order must start with mcp_list_servers discovery"
grep -q 'mcp_list_tools` — read the tool names' "$SKILL_MD" || fail "invocation order must read schemas before calling"
grep -q 'Never guess tool names' "$SKILL_MD" || fail "never-guess rule missing"

# Registration must be verified; silent failure must not leak into invokes
grep -q 'VERIFY' "$SKILL_MD" || fail "registration VERIFY step missing"
grep -q 'silent registration failure' "$SKILL_MD" || fail "silent-registration-failure rule missing"

# Mounted-server sandbox profile (ADR-061 era trust rules)
grep -q 'filesystem=read_only' "$SKILL_MD" || fail "mounted-server sandbox must keep filesystem=read_only"
grep -q 'network=egress_guard' "$SKILL_MD" || fail "mounted-server sandbox must keep network=egress_guard"
grep -q 'shell entry points' "$SKILL_MD" || fail "mounted-server sandbox must forbid shell entry points"

# Egress + error fidelity
grep -q 'do not bypass EgressGuard' "$SKILL_MD" || fail "EgressGuard non-bypass rule missing"
grep -q 'verbatim' "$SKILL_MD" || fail "verbatim-protocol-error rule missing"

echo "mcp_tools skill contract green: $SKILL_MD"
