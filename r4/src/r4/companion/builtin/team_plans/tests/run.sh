#!/usr/bin/env bash
# D-27 contract suite for the team_plans builtin skill (ADR-092 D-9).
# Pins the SKILL.md invariants: front-matter and tool grants, the one-plan-
# per-phase rhythm, the null and budget rules, the hand-offs to teamup and
# research_loop, and the grammar card's step kinds and untrusted envelope.
# Runs anywhere bash + coreutils exist; no platform required.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "team_plans skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: team_plans' "$SKILL_MD" || fail "front-matter name must be 'name: team_plans'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"
grep -q 'x-r4-keywords:' "$SKILL_MD" || fail "metadata x-r4-keywords missing"
allowed=$(grep '^allowed-tools: "' "$SKILL_MD" | head -n 1) || true
[ -n "$allowed" ] || fail "front-matter allowed-tools missing"
for tool in team_plan_templates team_plan_run team_plan_result; do
    grep -q "$tool" <<<"$allowed" || fail "allowed-tools must grant $tool"
done

# The rhythm (D-9): one plan per phase, read the result, then the next
grep -q 'ONE plan for' "$SKILL_MD" || fail "one-plan-per-phase rule missing"
grep -q 'Understand, then design, then implement, then review' "$SKILL_MD" \
    || fail "the understand/design/implement/review rhythm is missing"
grep -q 'READ its structured result' "$SKILL_MD" || fail "read-the-result rule missing"
grep -q 'Templates first' "$SKILL_MD" || fail "templates-first rule missing"

# Hard rules
grep -q 'A null is "unknown", never "passed"' "$SKILL_MD" || fail "null rule missing"
grep -q 'Budgets are hard' "$SKILL_MD" || fail "hard-budget rule missing"
grep -q 'adversarial_verify' "$SKILL_MD" || fail "adversarial_verify guidance missing"
grep -q 'schema' "$SKILL_MD" || fail "schema rule missing"
grep -q 'teamup_start' "$SKILL_MD" || fail "teamup hand-off missing"
grep -q 'research_loop_start' "$SKILL_MD" || fail "research_loop hand-off missing"

# Grammar card: step kinds, reducers, the untrusted envelope
for kind in agent map reduce pattern repeat gate plan merge_back; do
    grep -qF -- "kind: \"$kind\"" "$SKILL_MD" || fail "grammar card must cover step kind $kind"
done
for op in concat flatten dedup_by count majority partition mean rank subtract; do
    grep -q "$op" "$SKILL_MD" || fail "grammar card must list reduce op $op"
done
grep -q 'untrusted="true"' "$SKILL_MD" || fail "the untrusted envelope is missing"

# Slice 2: the patterns, gates that only a human passes, project knowledge
for pattern in adversarial_verify perspective_verify judge_panel completeness_critic multi_modal_sweep; do
    grep -q "$pattern" "$SKILL_MD" || fail "grammar card must name pattern $pattern"
done
grep -q 'never auto-approved' "$SKILL_MD" || fail "the gate rule (never auto-approved) is missing"
grep -q 'competing_hypotheses' "$SKILL_MD" || fail "the judge-panel near-tie rule is missing"
grep -q 'knowledge_query' "$SKILL_MD" || fail "the project-knowledge rule is missing"
grep -q 'plan_version: 1' "$SKILL_MD" || fail "grammar card must name plan_version 1"

# Slice 5: isolation, the parallel-writer rule, an approved merge-back
grep -q 'isolation: none (default) | worktree | copy' "$SKILL_MD" || fail "the isolation modes are missing"
grep -q 'when the step is isolated' "$SKILL_MD" || fail "the isolated parallel-writer rule is missing"
grep -q 'refused by name and nothing is written' "$SKILL_MD" || fail "the merge-back conflict rule is missing"
grep -q 'not a sandbox' "$SKILL_MD" || fail "the shell-text containment limit is missing"
grep -q 'discarded at run end' "$SKILL_MD" || fail "the discarded-changes warning is missing"
grep -q 'r4 team plan clean' "$SKILL_MD" || fail "the clean verb is missing"

# Slice 6: a plan as the loop's iteration, governed by the loop
grep -q 'research_loop_start(plan="research-round")' "$SKILL_MD" || fail "the loop-iteration spelling is missing"
grep -q 'ONLY the human answers, through research_loop_decide' "$SKILL_MD" || fail "the human-only decide rule is missing"
grep -q 'A gate the plan raises always parks' "$SKILL_MD" || fail "the gate-always-parks rule is missing"
grep -q 'smaller of its own and what the loop has' "$SKILL_MD" || fail "the wall-clock min rule is missing"
grep -q 'only PROPOSED' "$SKILL_MD" || fail "the proposed-only rule is missing"
grep -q 'plan_iteration_needs_server_venue' "$SKILL_MD" || fail "the server-venue refusal is missing"

# Slice 7: the durable record in UMRS, the run directory its cache
grep -q 'resume=<run_id> works' "$SKILL_MD" || fail "the resume-from-any-machine rule is missing"
grep -q 'the run directory is its cache' "$SKILL_MD" || fail "the local-cache rule is missing"
grep -qF "the state is \`degraded\` with the error named" "$SKILL_MD" || fail "the named-failure rule is missing"
grep -q 'durable=true requires the record' "$SKILL_MD" || fail "the durable-required rule is missing"
grep -q 'durable_record_failed' "$SKILL_MD" || fail "the fail-closed reason is missing"

# Slice 8: the server venue, single plan runs hosted by a sandboxed worker
grep -q 'venue="server", sources=' "$SKILL_MD" || fail "the server-venue spelling is missing"
grep -q 'no user credential and no' "$SKILL_MD" || fail "the worker credential rule is missing"
grep -q 'the worker never answers it and neither do you' "$SKILL_MD" || fail "the inbox-only gate rule is missing"
grep -q 'plan_exceeds_project_caps' "$SKILL_MD" || fail "the caps refusal is missing"
grep -q 'model@source is refused' "$SKILL_MD" || fail "the bare-model rule is missing"
# Slice 8b: site models, and the server-hosted loop of plan runs
grep -q 'model@site' "$SKILL_MD" || fail "the site-model spelling is missing"
grep -q 'each iteration is one' "$SKILL_MD" || fail "the server plan loop is missing"

echo "team_plans skill contract green"
