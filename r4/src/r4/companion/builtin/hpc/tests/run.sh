#!/usr/bin/env bash
# D-27 contract suite for the hpc builtin skill.
# Pins the SKILL.md invariants: front-matter, the D-37 venue rule,
# the pre-stage and runtime rules, and the job workflow verbs.
# Runs anywhere bash + coreutils exist — no cluster required.
set -u
SKILL_MD="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"

fail() { echo "hpc skill test FAILED: $1" >&2; exit 1; }

[ -f "$SKILL_MD" ] || fail "SKILL.md missing"

# Front-matter contract
sed -n '1p' "$SKILL_MD" | grep -qxF -- '---' || fail "front-matter opening '---' missing"
awk '/^---$/{c++} c==2{exit} END{exit(c<2)}' "$SKILL_MD" || fail "front-matter block unterminated"
grep -qxF -- 'name: hpc' "$SKILL_MD" || fail "front-matter name must be 'name: hpc'"
grep -q '^description: "' "$SKILL_MD" || fail "front-matter description missing"
grep -q '^allowed-tools: "' "$SKILL_MD" || fail "front-matter allowed-tools missing"
grep -q 'x-r4-keywords:' "$SKILL_MD" || fail "metadata x-r4-keywords missing"

# Venue tools present in allowed-tools
grep -q 'hpc_submit' "$SKILL_MD" || fail "allowed-tools must include hpc_submit"
grep -q 'hpc_job_status' "$SKILL_MD" || fail "allowed-tools must include hpc_job_status"

# D-37 venue rule stanza
grep -q 'cluster-venue tools' "$SKILL_MD" || fail "venue rule (D-37) missing"
grep -q 'refuse by name' "$SKILL_MD" || fail "venue rule must keep the refuse-by-name contract"
grep -q 'r4 workflow trigger' "$SKILL_MD" || fail "venue rule must point to the durable UMRS path"
grep -q 'r4 job submit' "$SKILL_MD" && fail "venue rule names 'r4 job submit', which is not an r4 verb"

# Hard rules
grep -q 'Singularity/Apptainer' "$SKILL_MD" || fail "runtime rule missing (Singularity/Apptainer default)"
grep -q 'Never assume Docker' "$SKILL_MD" || fail "runtime rule must keep 'Never assume Docker'"
grep -q 'PRE-STAGED' "$SKILL_MD" || fail "pre-stage rule missing"
grep -q 'never download' "$SKILL_MD" || fail "pre-stage rule must keep the never-download invariant"
grep -q 'Validate Slurm parameters' "$SKILL_MD" || fail "validate-parameters rule missing"

# Job workflow verbs
grep -q 'job id' "$SKILL_MD" || fail "workflow must record the job id"
grep -q 'sbatch' "$SKILL_MD" || fail "workflow must cover sbatch"
grep -q 'squeue' "$SKILL_MD" || fail "workflow must cover squeue"

echo "hpc skill contract green: $SKILL_MD"
