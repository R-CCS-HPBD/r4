#!/usr/bin/env bash
# D-27 contract suite for the workflow_ops builtin skill.
# Pins the workflow-skill invariants: tool grants (incl. the authoring
# set), the lifecycle order line, structured-JSON result fidelity, the
# approval gate, the save/replay pair, the one-fix authoring loop, and
# the shipped bundle reference.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "workflow_ops skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: workflow_ops' "$SKILL_MD" || fail "front-matter name must be 'name: workflow_ops'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"

allowed=$(grep '^allowed-tools: "' "$SKILL_MD" | head -n 1) || true
[ -n "$allowed" ] || fail "front-matter allowed-tools missing"
for tool in workflow_list workflow_describe workflow_upload workflow_run \
    workflow_trigger workflow_status workflow_wait workflow_results \
    workflow_cancel workflow_approve_gate workflow_save_definition workflow_replay; do
    grep -q "$tool" <<<"$allowed" || fail "allowed-tools must grant $tool"
done
grep -q 'x-r4-keywords:' "$SKILL_MD" || fail "metadata x-r4-keywords missing"
grep -q 'x-r4-dependencies: "platform_ops"' "$SKILL_MD" || fail "x-r4-dependencies must name platform_ops"

# Lifecycle order line
grep -q 'workflow_list → workflow_describe → workflow_run' "$SKILL_MD" || fail "lifecycle order line missing"

# Result fidelity + approval gate + replay
grep -q 'Return structured JSON tool results; do not paraphrase errors' "$SKILL_MD" || fail "structured-JSON-results rule missing"
grep -q 'approve them with workflow_approve_gate' "$SKILL_MD" || fail "approval-gate rule missing"
grep -q 're-executes it exactly' "$SKILL_MD" || fail "workflow_replay exactness rule missing"

# Authoring feedback loop: one fix at a time, bumped version re-upload
grep -q 'fix ONE thing' "$SKILL_MD" || fail "one-fix-at-a-time rule missing"
grep -q 'bumped version' "$SKILL_MD" || fail "bumped-version re-upload rule missing"

# Referenced bundle example must ship with the skill
[ -f "$(dirname "$SKILL_MD")/references/workflow-bundle.md" ] || fail "references/workflow-bundle.md missing"

echo "workflow_ops skill contract green: $SKILL_MD"
