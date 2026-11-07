# R4 + UMRS — Introduction

> **Audience:** New users, evaluators, management, new developers
> **Status:** Current as of 2026-09-22 (rewritten from [the design](../R4-UMRS-DESIGN.md))

---

## What it is

R4 + UMRS is an AI platform for working scientists. It lets a research
group's own knowledge, tools and compute be used directly by AI agents, so
that AI augments the efficiency, accuracy and speed of everyday research.

It is built for scientists whose workflow already depends on specialised
infrastructure — HPC and GPU clusters, image and video recognition, robotics,
instrument control — who are experts in their domain and should not need to
become experts in building AI ingestion pipelines.

## The problem it removes

A general-purpose assistant knows the public literature and nothing about
your group. Your group carries three kinds of knowledge it cannot reach:

| Kind of knowledge | Examples | What the agent needs |
|---|---|---|
| Domain materials | papers, theses, lab notes, datasets, measurement logs, images, video | ingestion into a retrieval index, a vector store and a knowledge graph |
| Tacit know-how | how this group runs an experiment, which parameters matter, what failure looks like | skills and a knowledge graph that capture procedures and relationships |
| Dedicated tools | robot controllers, instrument APIs, HPC job scripts, in-house analysis codes | custom MCP servers so the agent can call them safely |

Building that pipeline is a specialist engineering job most groups cannot
staff. The platform provides it as a product: you supply materials and
answer questions; the platform turns them into a **Scientific Catalog** the
agents can use, and keeps the catalog growing as you work.

## The pieces

| Piece | What it is | Where it runs |
|---|---|---|
| **R4** | The side you work in: a terminal console, the agent harness, skills, context control, multi-agent teams, local and remote sessions, projects and workflows, and the autonomous research loop. | Your workstation; remote sessions on the cluster front-end. |
| **UMRS** | The backbone behind it: accounts, secure data transfer, job and session scheduling, self-deployed LLM / Vision / VLM servers in sandboxes on the GPU cluster, S3 and Postgres + pgvector storage, the MCP registry. | The cluster front-end, dispatching to Slurm / Kubernetes / Podman. |
| **Scientific Catalog** | A portable bundle of everything a domain or project knows: materials, retrieval index and vectors, knowledge graph, skills, MCP server definitions, metadata. Exportable to share with other groups. | Built through R4 and UMRS; stored behind UMRS. |
| **Custom MCP servers** | Your tools wrapped once (with Hatch) and run where you choose (with Hatchling): locally, on the front-end, or on the trusted host wired to an instrument. | Local, front-end or trusted host. |

Everything heavy — models, jobs, data — stays inside your institution.
Research data never leaves the site for inference.

## How a project moves through the platform

1. **Enrol and set up.** You get a UMRS account; the front-end already has models deployable in sandboxes. In R4 you create a project.
2. **Ingest.** You point R4 at papers, protocols, notebooks and sample data. UMRS lands them in S3, embeds them into Postgres + pgvector and builds the first knowledge graph.
3. **Wire the tools.** From the registry you pull the MCP servers your domain needs.
4. **First assisted tasks.** Through the console you ask an agent to re-run a known analysis on new data. R4 packages it as a workflow; UMRS runs it as a sandboxed job on the cluster and stores the outputs. Your corrections go into the catalog.
5. **The loop begins.** You set a research goal. A team of agents plans, runs jobs, observes, updates the knowledge graph, and asks you when a step is costly, ambiguous or changes direction.
6. **The catalog matures.** The graph holds what was tried, what failed, what holds. The loop asks less often.
7. **Output.** The loop drafts the paper from the catalog; you edit, validate and submit.
8. **Share.** You export the catalog; a collaborating lab imports it and starts where you are.

Steps 1–4 and the server-hosted loop of step 5 work today; the catalog as a
single exportable object, the ask policy of step 5, and the catalog-grounded
draft of step 7 are being built. The precise state of every capability is in
[the gap analysis](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md).

## Try it

```bash
curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash
export PATH="$HOME/.local/bin:$PATH"
r4 auth login --api-key <TOKEN>      # token from your administrator
                                     #   …also adopts your server-visible
                                     #   projects (owned ∪ circle-affiliated)
                                     #   into this machine's registry
r4 tui install && r4 console         # the console
r4 project create mylab && r4 project import ./papers && r4 project init
r4 chat "Summarise our standard defect-analysis protocol"
```

Full walkthroughs: [Quickstart](../QUICKSTART.md), [User guide](user-guide.md),
[Knowledge management](../user-manual/27-knowledge-management.md),
[Autonomous research](../user-manual/26-autonomous-research.md).

## Where next

- The design in full: [R4-UMRS-DESIGN.md](../R4-UMRS-DESIGN.md)
- What runs today: [R4-UMRS-PLATFORM-OVERVIEW.md](../R4-UMRS-PLATFORM-OVERVIEW.md)
- Operating a deployment: [Deployment overview](../user-manual/16-deployment.md), [stand-alone stack](../../deploy/standalone/README.md)
- Contributing: [CONSTITUTION.md](../../CONSTITUTION.md), [CONTRIBUTING.md](../../CONTRIBUTING.md)
