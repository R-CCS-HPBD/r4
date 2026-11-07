#!/usr/bin/env bash
# D-27 contract suite for the research_loop builtin skill.
# Pins the one-research-spine invariants (ADR-036): tool grants across the
# knowledge/loop layers, no invented workflow names, human-owned
# approvals, and tools-over-raw-CLI.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "research_loop skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: research_loop' "$SKILL_MD" || fail "front-matter name must be 'name: research_loop'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"

allowed=$(grep '^allowed-tools: "' "$SKILL_MD" | head -n 1) || true
[ -n "$allowed" ] || fail "front-matter allowed-tools missing"
for tool in knowledge_status knowledge_ingest knowledge_query \
    knowledge_graph_propose research_loop_start research_loop_list \
    research_loop_status research_loop_stop research_loop_approvals \
    research_loop_decide; do
    grep -q "$tool" <<<"$allowed" || fail "allowed-tools must grant $tool"
done
grep -q 'x-r4-keywords:' "$SKILL_MD" || fail "metadata x-r4-keywords missing"

# One research spine (ADR-036)
grep -q 'one research spine (ADR-036)' "$SKILL_MD" || fail "one-research-spine (ADR-036) rule missing"
grep -q 'knowledge_\* builds the project' "$SKILL_MD" || fail "spine layering rule missing"

# No invented workflow names
grep -q 'never invent a workflow name' "$SKILL_MD" || fail "never-invent-workflow-name rule missing"

# Approvals are the human's
grep -q "Approvals are the human's" "$SKILL_MD" || fail "human-owned-approvals rule missing"
grep -q "human's stated answer" "$SKILL_MD" || fail "record-only-the-human's-answer rule missing"

# Tools over raw CLI
grep -q 'Prefer these tools over raw CLI' "$SKILL_MD" || fail "tools-over-raw-CLI rule missing"

echo "research_loop skill contract green: $SKILL_MD"
