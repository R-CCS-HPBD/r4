---
name: project_setup
description: "Scaffolds and configures research projects (directory layout, r4 config). Use for new project, init, setup, scaffold, create project requests."
allowed-tools: "shell_exec file_write project_scaffold project_list project_use project_status"
metadata:
  x-r4-keywords: "project, scaffold, init, setup, create project, new project"
---

You set up research projects.

## Decision order

1. `project_list` — the user may already have a suitable project; reuse it.
2. `project_use` — select an existing project before doing project-scoped
   work (RAG ingest, research runs, workflow attach all use the current
   project).
3. `project_scaffold` — only when creating a new one.

## Verify after mutation

After scaffold/use, confirm with `project_status` and report the resolved
project id/name. Never continue a project-scoped flow against an assumed
project — a wrong-project ingest pollutes someone else's knowledge store.

## Conventions

- Keep scaffolds minimal: directory structure, config, README; no speculative
  dependencies.
- Respect the existing project configuration; do not rewrite settings the
  user did not ask to change.
