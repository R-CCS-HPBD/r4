# Skill Routing: When to Use Which R4 Skill

The autoresearch skill orchestrates — R4 platform skills execute. This reference maps research activities to the skills R4 ships.

## Routing Principle

When you meet a domain-specific task during research, pick the skill from the discovery catalog (name — one-line description) and load it with `activate_skill(skill_name=...)`. Its body applies from the next turn. It contains the tools, workflows, and common issues for that area.

## R4 Skill Map

### Compute and Experiments

| Task | Skill |
|---|---|
| Slurm jobs, GPU allocations, containers on a cluster | `hpc` |
| Registered UMRS workflows (register, run, monitor, author) | `workflow_ops` |
| Server-hosted autonomous research loop | `research_loop` |
| Multi-agent fan-out review / adversarial review under a budget | `team_plans` |
| C/C++/Fortran/R/Python build, run, and test | `polyglot_coding` |
| Reading, editing, searching, and verifying code | `code` |

### Data, Models, and Serving

| Task | Skill |
|---|---|
| File storage, lab data catalog, RAG documents | `data_management` |
| Model registry, downloads, staging | `model_management` |
| Inference endpoints, durable jobs, platform health | `platform_ops` |
| Customized inference environment (MCP/RAG mounts) | `inference_env` |
| Vision inference (segmentation, detection, classification) | `vision` |
| MCP servers and tools | `mcp_tools` |

### Analysis and Judgment

| Task | Skill |
|---|---|
| Reproducible analysis, statistics, plots | `analysis` |
| Fast typed judgments (choice / score / yes-no) | `decisions` |

### Research Output

| Task | Skill |
|---|---|
| Literature search and synthesis | `research` |
| Generate research ideas | `brainstorming-research-ideas`, `creative-thinking-for-research` |
| Publication-quality figures | `academic-plotting` |
| Write an ML/AI paper (NeurIPS, ICML, ICLR, ACL, AAAI, COLM) | `ml-paper-writing` |
| Write a systems paper (OSDI, SOSP, ASPLOS, NSDI, EuroSys) | `systems-paper-writing` |
| Conference talk slides | `presenting-conference-talks` |
| Research artifact (ARA) compilation and review | `ara-compiler`, `ara-rigor-reviewer`, `ara-research-manager` |

## Libraries Without an R4 Skill

R4 does not bundle per-library domain skills. For fine-tuning frameworks (Axolotl, Unsloth, TRL), serving engines (vLLM, SGLang, llama.cpp), interpretability toolkits (TransformerLens, SAELens, NNsight), distributed training (DeepSpeed, FSDP, Accelerate), evaluation harnesses (lm-evaluation-harness, Inspect AI) and experiment tracking (W&B, MLflow, TensorBoard), use the library directly from your experiment code and its own documentation. Run it on a cluster through the `hpc` or `workflow_ops` skill.

## Common Research Workflows

### "I need to fine-tune a model and evaluate it"

1. Stage the base model (`model_management`)
2. Write the training and evaluation code with your chosen library
3. Run it on the cluster (`hpc` for a Slurm job, `workflow_ops` for a registered workflow)
4. Save metrics to `data/` and plot them (`analysis`, `academic-plotting`)

### "I need to run many iterations unattended"

1. Build project knowledge first (`knowledge_ingest`, see the `research_loop` skill)
2. Register the experiment as a workflow (`workflow_ops`)
3. Start the server-hosted loop (`research_loop`) and monitor it

### "I need related work"

1. Search and synthesize with the `research` skill
2. Verify every citation (see `ml-paper-writing`'s `references/citation-workflow.md`)

## Finding Skills

If you're not sure which skill to use, read the discovery catalog in your system prompt and load the best match with `activate_skill`. The user can list skills with `r4 skill list`.
