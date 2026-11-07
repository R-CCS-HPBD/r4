# R4 Platform — New Contributor Path

This guide is the recommended onboarding path for **short-term contributors**
(interns, visiting researchers, collaborative project members) who want to
understand, use, and extend the R4 platform within a real research context.

It is structured around three sequential tasks that build on each other:

1. [Orientation — Understanding the Platform](#task-1-orientation--understanding-the-platform)
2. [Build — Implement a Small Component or Application](#task-2-build--implement-a-small-component-or-application)
3. [Evaluate — Document and Share Results](#task-3-evaluate--document-and-share-results)

---

## Before You Start (Admin Prerequisites)

The following must be completed by a platform administrator **before** the
contributor's first day.

### 1. RCCS Account and SSH Access

Ensure the contributor has:
- A clone of the public repository, https://github.com/R-CCS-HPBD/r4 (no
  access request needed), or a fork they can push to
- An RCCS cloud account with SSH key enrolled, needed only to work against
  the R-CCS deployment

### 2. UMRS User Provisioning

On the UMRS server:

```bash
# Create a user record (replace <username> and <project>)
umrs-server admin user add <username> --project <project>

# Generate a 90-day JWT token for the user
umrs-server admin token generate-jwt \
  --user <username> \
  --project <project> \
  --days 90
```

Share the `token:` line from the output with the contributor.

### 3. Verify Client Access

Ask the contributor to run this after installing `r4` (see Task 1 below):

```bash
r4 auth login --gateway-url https://<gateway-host> --project <project> \
               --api-key <TOKEN>
r4 health
r4 auth status
```

Expected result: all checks green.

---

## Task 1: Orientation — Understanding the Platform

**Goal:** Gain a working mental model of the platform and identify one concrete
research application where it can be applied.

**Deliverable:** A filled-in [Use-Case Worksheet](#use-case-worksheet-template)
submitted before starting Task 2.

### Day 1–2: Read the Architecture

| Document | What to focus on |
|---|---|
| [README.md](../../README.md) | Platform purpose and naming conventions |
| [ARCHITECTURE.md](../../ARCHITECTURE.md) | Component map: Gateway → UMRS → mini-RiVault |
| [docs/architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md](../architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md) | Package boundaries — what `r4`, `r4-contracts`, Gateway, and UMRS each own |
| [docs/r4/index.md](index.md) | CLI map and platform mental model |

Key concepts to understand before proceeding:

- **`r4` is the user-facing entry point** — you will spend most time here.
- **UMRS** owns durable jobs, files, models, RAG, and endpoint lifecycle.
- **Gateway** is the auth + routing boundary. Never talk to vLLM or LiteLLM directly.
- **mini-RiVault** is the per-session isolated inference environment provisioned by UMRS.

### Day 3–4: Hands-On Setup and Demos

#### Install the Client

```bash
# From the repository root
./scripts/setup_r4_client.sh
export PATH="$HOME/.local/bin:$PATH"

# Configure your profile (replace placeholders)
r4 auth login --gateway-url https://<gateway-host> --project <project> \
               --api-key <TOKEN>
r4 health
```

#### Run the Demo Sequence

Work through these demos in order. Each one has a `run_demo.sh`:

| Demo | Path | What you learn |
|---|---|---|
| 00 | `umrs/demos/00_onboarding/` | User provisioning and bucket setup |
| 01 | `umrs/demos/01_health_check/` | Verify server is reachable |
| 02 | `umrs/demos/02_text_embedding/` | Embed a text corpus |
| 05 | `umrs/demos/05_rag_search/` | Vector search and hybrid RAG |
| 29 | `umrs/demos/29_llm_completions/` | Chat completions via Gateway |

```bash
cd umrs/demos/<demo-dir>
bash run_demo.sh
```

#### Run the SDK Examples

```bash
# One-shot chat
python docs/r4/examples/simple_chat.py

# RAG: upload → index → query
python docs/r4/examples/rag_workflow.py

# Streaming response
python docs/r4/examples/streaming_chat.py
```

### Day 5: Define Your Use Case

Browse the shortlist below and pick one to develop in Task 2.
You may adapt or combine entries. Fill in the worksheet before proceeding.

#### Pre-seeded Use Case Menu

| # | Idea | Platform features used | Good for |
|---|---|---|---|
| A | **Literature review assistant** — feed domain PDFs → RAG → conversational Q&A | RAG, `r4 chat`, streaming | Any research domain |
| B | **Simulation log summarizer** — parse HPC job output → LLM → structured report | batch_processing, file upload, `r4 job` | Computational science |
| C | **Materials / dataset Q&A** — structured dataset + RAG + streaming retrieval | RAG, `r4 rag`, `r4 model` | Materials science, chemistry |
| D | **Custom MCP tool** — expose a domain-specific API (lab instrument, DB, web service) as an MCP tool inside mini-RiVault | `mcp-servers/`, demo 26 | Any domain with a callable API |
| E | **Multi-step image analysis pipeline** — lab images → vision model → structured annotations | demos 09/10, `r4 workflow` | Bio-imaging, remote sensing |
| F | **Agentic research assistant** — multi-turn conversation with tool calls and memory | `r4` TUI, MCP tools, companion | General research support |

---

## Use-Case Worksheet (Template)

Copy this section into a new file (e.g., `docs/research/<your-topic>.md`) and fill it in.

```markdown
# Use Case: <Title>

## Scientific Problem
<!-- What is the research question or challenge?
     Who has this problem? What makes it hard to do manually? -->

## How AI Is Used
<!-- Describe concretely: what input goes in, what the AI does, what output comes out. -->

## Platform Components Involved
<!-- Tick all that apply and briefly note how each is used. -->
- [ ] `r4` CLI / SDK — ...
- [ ] Gateway — ...
- [ ] UMRS RAG — ...
- [ ] UMRS job submission — ...
- [ ] mini-RiVault LLM inference — ...
- [ ] MCP tool/server — ...
- [ ] Other: ...

## Expected Output / Demonstration
<!-- What will you show at the end of Task 3?
     E.g.: "A Jupyter notebook that queries 50 papers and returns a ranked summary." -->

## Scope Constraints
<!-- What is out of scope for this internship period?
     Keep it honest — a working demo beats an ambitious non-finish. -->
```

---

## Task 2: Build — Implement a Small Component or Application

**Goal:** Design and implement the component or application identified in Task 1.
The focus is on a **working demo**, not production hardening.

### Development Setup

```bash
# Full dev install from the repository root
uv sync --all-packages --all-extras

# Activate the project venv
source .venv/bin/activate

# Verify tests pass before making changes
make test
```

### Contribution Workflow

```bash
# Create a feature branch
git checkout -b contrib/<your-name>/<short-description>

# Develop, test, commit incrementally
git add <files>
git commit -m "feat(<scope>): <what and why>"

# Push and open a PR for review when ready
git push -u origin contrib/<your-name>/<short-description>
```

### Where to Put Your Work

| What you build | Where to put it |
|---|---|
| Standalone script or notebook | `demos/NN_demo_name/` |
| New SDK usage example | `docs/r4/examples/` |
| Custom MCP server | `mcp-servers/src/r4_mcp/` |
| New UMRS demo | `umrs/demos/<next-number>_<short-name>/` |
| Data processing helper | `a2l/src/` (if reusable A2L logic) |

### Code Style and Lint

```bash
# Format and lint (must pass before opening a PR)
make lint

# Run the unit tests for the package you touched
.venv/bin/python -m pytest r4/tests/     # r4 SDK
.venv/bin/python -m pytest umrs/tests/   # UMRS
```

Refer to [CONTRIBUTING.md](../../CONTRIBUTING.md) for the full development setup
and test infrastructure details.

### Minimal Acceptance Bar for Task 2

Before moving to Task 3, your implementation should satisfy:

- [ ] Runs end-to-end against the live platform (or local compose stack)
- [ ] Does not bypass Gateway (no direct calls to LiteLLM/vLLM)
- [ ] Lint passes (`make lint`)
- [ ] At least one test or demo script demonstrates the happy path
- [ ] A `README.md` in the component directory explains how to run it

---

## Task 3: Evaluate — Document and Share Results

**Goal:** Evaluate the implementation with real or representative data, document
design decisions and limitations, and produce a short technical report and
presentation.

### Evaluation Checklist

- [ ] Run the component on at least one representative scientific dataset or query set
- [ ] Record qualitative observations (quality of output, latency, failure cases)
- [ ] Identify at least one clear limitation or direction for future work

### Documentation to Produce

Create a short report following this structure.
Place it in `docs/research/<your-topic>/report.md`.

```markdown
# <Use Case Title> — Technical Report

**Author:** <name>
**Period:** <start> – <end>

## 1. Problem and Motivation
<!-- One paragraph: scientific problem, why AI helps. -->

## 2. Design Decisions
<!-- What you built, key design choices, alternatives considered. -->

## 3. Platform Integration
<!-- Which components you used and how they are connected.
     Include a simple diagram if helpful. -->

## 4. Evaluation
<!-- How you tested it, what inputs you used, what the output looked like.
     Include example queries / responses where possible. -->

## 5. Lessons Learned and Limitations
<!-- What worked, what did not, what you would do differently. -->

## 6. How to Reproduce
<!-- Step-by-step: install, configure, run. A reader who was not present
     should be able to reproduce your demo. -->

## 7. Future Work
<!-- Concrete next steps if someone were to extend this. -->
```

### Final Presentation

A short (~15 min) technical presentation covering:

1. Scientific problem — why this use case matters
2. Platform components used — architecture diagram
3. Demo — live or recorded walkthrough
4. Evaluation results — what worked and what the limits are
5. Reproducibility — where the code lives and how to run it

---

## Quick Reference

### Useful Commands

```bash
# Platform health and auth
r4 health
r4 auth status

# Explore available models and endpoints
r4 model list
r4 endpoint list

# One-shot chat
r4 chat "Summarize the key findings of this paper: ..."

# Launch interactive TUI
r4

# Upload a file and query RAG
r4 rag upload myfile.pdf --project my-project
r4 rag query "What does the paper say about X?" --project my-project

# Inspect jobs
r4 job list
r4 job describe <job-id>
```

### Key Documents

| Need | Document |
|---|---|
| Platform quickstart | [docs/r4/quickstart.md](quickstart.md) |
| CLI reference | [docs/r4/reference/cli.md](reference/cli.md) |
| SDK reference | [docs/r4/reference/sdk.md](reference/sdk.md) |
| SDK examples | [docs/r4/examples/README.md](examples/README.md) |
| Architecture overview | [ARCHITECTURE.md](../../ARCHITECTURE.md) |
| Package boundaries | [docs/architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md](../architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md) |
| Admin guide | [docs/r4/admin-guide/README.md](admin-guide/README.md) |
| All UMRS demos | [umrs/demos/README.md](../../umrs/demos/README.md) |
| Contributing | [CONTRIBUTING.md](../../CONTRIBUTING.md) |

### Getting Help

- Open an issue on the repository with the `question` label
- For platform access problems (tokens, provisioning), contact the site admin
- For UMRS internals, see `umrs --help` and `umrs-server admin --help`
