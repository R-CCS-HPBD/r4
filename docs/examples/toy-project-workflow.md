# Toy Example: Full Project Knowledge Workflow

A complete end-to-end walkthrough of the R4 knowledge platform, using the **active project** pattern so you never need to specify `<project_id>` after `r4 project switch`.

## Prerequisites

- Active deployment (compose or umrs-front): `r4 project current` should show a gateway URL
- A test directory with code/papers: see `tests/fixtures/research-corpus/` below

```bash
# Verify active deployment
r4 project current

# Verify active profile
r4 profile
```

---

## Step 1: Create & Switch to a Project

```bash
# Create a test project
r4 project create toy --name "Toy Project"

# Switch to it (this sets the current project)
r4 project switch toy

# Verify
r4 project current
```

**Output:**
```
✓ Created project: toy
✓ Switched to project: toy
Current project: toy
  Name: Toy Project
  Gateway: http://gateway:8000
  Model: qwen3-14b
```

**Now every knowledge command defaults to `toy` — no need to pass `toy` every time.**

---

## Step 2: Ingest Test Knowledge

Create a small test corpus:

```bash
# Create test directory structure
mkdir -p /tmp/toy-corpus/papers /tmp/toy-corpus/code

# Create test files
echo "# Transformer Attention Mechanisms
This paper surveys attention mechanisms in transformer models.
Key findings: multi-head attention improves over single-head.
References: Vaswani et al. 2017" > /tmp/toy-corpus/papers/attention_survey.txt

echo "def compute_attention(q, k, v):
    scores = q @ k.T / sqrt(d_k)
    weights = softmax(scores)
    return weights @ v" > /tmp/toy-corpus/code/attention.py

# Ingest (project_id defaults to current = 'toy')
r4 project import /tmp/toy-corpus/papers /tmp/toy-corpus/code
```

**Output:**
```
✓ Ingested 2 artifact(s) into project toy
  Workspace: ~/.r4/projects/toy
```

---

## Step 3: Extract Structured Knowledge

```bash
# Extract (project_id defaults to current)
r4 project init
```

**Output:**
```
✓ Extracted knowledge for project toy
  Phase: understanding
  Extracted: 2 items
  Graph: 5 nodes, 3 edges
  Coverage: 0.65
```

---

## Step 4: Ask Questions (with Citations)

```bash
# Ask a question about the project knowledge
r4 project ask "What does attention computation look like?"
```

**Output:**
```
Q: What does attention computation look like?
A: Attention is computed as softmax(q @ k.T / sqrt(d_k)) @ v.
   This is the multi-head attention mechanism described in the survey.

Evidence:
  - code/attention.py:compute_attention (confidence: 0.85)
  - papers/attention_survey.txt:Transformer Survey (confidence: 0.72)
```

---

## Step 5: Verify Knowledge

```bash
# Ask a verification question (returns confidence score)
r4 project verify "Is the attention implementation mathematically correct?"
```

**Output:**
```
Q: Is the attention implementation mathematically correct?
A: The implementation follows the standard scaled dot-product attention formula.
   Missing: dropout, layer normalization, and multi-head composition.

Confidence: 0.72
```

---

## Step 6: Check Findings

```bash
# List verification findings (now works with current project)
r4 project findings
```

**Output:**
```
Findings for toy: 3 total

  [MEDIUM] 1 finding(s)
    - Attention missing dropout regularization [open]

  [LOW] 2 finding(s)
    - No multi-head composition in attention [open]
    - Layer normalization not applied [open]
```

---

## Step 7: Transition Findings

```bash
# Resolve a finding (acknowledge it's known)
r4 project transition finding-001 resolved --evidence "Documented in issue #42"
```

**Output:**
```
✓ Resolved finding: finding-001
  Old status: open
  New status: resolved
  Evidence: Documented in issue #42
```

---

## Step 8: Check Project Status

```bash
# Full project status overview
r4 project status
```

**Output:**
```
Project: toy
  Artifacts ingested: 2
  Extraction: complete
  Graph: 5 nodes, 3 edges
  Verifications: 1 total (1 open, 0 resolved, 0 waived)
  Readiness: pending
  Registered workflows: 0
  Research plans: 0
  Last update: 2026-07-16T10:30:00Z
```

---

## Step 9: Generate Research Plan

```bash
# Generate a research plan based on knowledge gaps
r4 project plan "Improve the attention implementation"
```

**Output:**
```
Research Plan: "Improve the attention implementation"

Generated 4 tasks:
  1. Add dropout regularization to attention (high priority)
  2. Implement multi-head composition (high priority)
  3. Add layer normalization (medium priority)
  4. Add unit tests for attention (low priority)

Recommended next: Task 1 (dropout regularization)
```

---

## Step 10: Emit Workflow from Plan

```bash
# Convert plan to executable workflow
r4 project emit-workflow workflow.yaml
```

**Output:**
```
✓ Emitted workflow draft to: workflow.yaml
  Tasks: 4
  Dependencies: Task 4 depends on 1, 2, 3
  Manual placeholders: 0
```

---

## Step 11: Approve for Autonomous Research

```bash
# Approve the project (required before running the autonomous loop)
r4 project approve
```

**Output:**
```
✓ Approved project: toy
  Readiness: approved
  Confident: yes
  Blocking findings: 0
```

---

## Step 12: Run Autonomous Research Loop

```bash
# Run the autonomous loop (up to 2 iterations by default)
r4 project run --max-iterations 2
```

**Output:**
```
Loop iteration 1/2
  Plan: Improve the attention implementation
  Emit: workflow.yaml (4 tasks)
  Trigger: workflow run (2 tasks completed, 2 pending)
  Collect: 2 new artifacts
  Ingest: 2 artifacts → graph updated (5→7 nodes)
  Human gate: experiment design review

Loop iteration 2/2
  Plan: Review attention improvements
  Emit: workflow.yaml (4 tasks)
  Trigger: workflow run (4 tasks completed)
  Collect: 4 new artifacts
  Ingest: 4 artifacts → graph updated (7→11 nodes)
  Confidence: 0.85 → APPROVE
```

---

## Step 13: Check Lineage

```bash
# Query lineage for an artifact (shows A2L links if available)
r4 project lineage attention-py-001
```

**Output:**
```
Lineage for: attention-py-001
  Type: code artifact
  Created: 2026-07-16T10:30:00Z
  A2L links:
    - a2l:derive-from: source-code-001
    - a2l:used-by: workflow-run-002
  Provenance: ingested from /tmp/toy-corpus/code/attention.py
```

---

## Step 14: Export Knowledge Bundle

```bash
# Export project knowledge as .kb bundle
r4 project export
```

**Output:**
```
✓ Exported project knowledge bundle
  Bundle: toy.kb
  Artifacts: 6
  Graph nodes: 11
  Graph edges: 9
  Manifest: 6 files, sha256 verified
  Size: 12 KB
```

---

## Quick Reference: Current Project Pattern

| Old Pattern (verbose) | New Pattern (current project) |
|----------------------|-------------------------------|
| `r4 project import toy /papers` | `r4 project import /papers` |
| `r4 project init toy` | `r4 project init` |
| `r4 project ask toy "question"` | `r4 project ask "question"` |
| `r4 project verify toy "question"` | `r4 project verify "question"` |
| `r4 project findings` (failed) | `r4 project findings` (works now) |
| `r4 project approve toy` | `r4 project approve` |
| `r4 project run toy` | `r4 project run` |

**Switch to another project:**
```bash
r4 project switch rccs_doc
# Now all commands default to rccs_doc
r4 project ask "What's in the rccs_doc project?"
```

---

## Cleanup

```bash
# Delete the test project
r4 project delete toy

# Verify
r4 project list
```

---

## Troubleshooting

### "No project specified" error
```bash
# Set current project
r4 project switch <project-id>
```

### "Project not found" error
```bash
# List available projects
r4 project list

# Create missing project
r4 project create <id> --name "Name"
```

### "404" on findings/status
```bash
# Check gateway connectivity
r4 project current

# Restart compose if needed
cd tests
docker compose -f docker-compose.e2e.yaml up -d

# Re-run after compose is ready
r4 project status
```

### "No verify_claim support"
```bash
# This means the backend doesn't have the LLM verification engine
# Check the UMRS server log on your deployment, for example on the
# stand-alone stack (from deploy/standalone/):
./standalone.sh logs umrs

# Or run locally on compose:
python -m pytest tests/test_research_journey_e2e.py -v
```

---

## Testing Locally on Compose

```bash
# Start compose harness
cd tests
docker compose -f docker-compose.e2e.yaml up -d

# Wait for ready
sleep 10

# Test full journey
r4 project create test --name "Test"
r4 project switch test
r4 project import ./fixtures/research-corpus/papers ./fixtures/research-corpus/code
r4 project init
r4 project ask "What is this project about?"
r4 project status
r4 project findings
r4 project verify "Is the code correct?"
r4 project plan "Improve this project"
r4 project approve
r4 project run --max-iterations 1

# Cleanup
r4 project delete test
docker compose -f docker-compose.e2e.yaml down
```

---

## Next Steps

- **Deploy**: see [deploy/standalone/README.md](../../deploy/standalone/README.md) or [docs/user-manual/16-deployment.md](../user-manual/16-deployment.md)
- **Run E2E tests**: `python tests/run_e2e_tests.py` or `.venv/bin/python -m pytest tests/test_research_journey_e2e.py -v`
- **HPC journey E2E**: `tests/run_hpc_tests.sh` (requires an HPC-connected UMRS deployment)
