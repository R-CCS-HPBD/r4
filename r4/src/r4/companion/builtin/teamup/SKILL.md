---
name: teamup
description: "Multi-agent teams: the HPC optimisation session, and crews that run as plan templates"
allowed-tools: "teamup_list teamup_start teamup_run teamup_status teamup_conclusions team_plan_result team_plan_cancel"
metadata:
  x-r4-keywords: "teamup, team up, multi-agent, team, swarm, parallel agents, optimize code, sota, hardware target, crew"
---

You start and monitor teamup sessions: multi-agent teams that iterate on a
goal. Start one only when the task genuinely wants parallel roles; keep it
small first.

teamup is NOT a loop: a session is a BOUNDED local team run (manual
iteration, max_iterations ceiling, lives with the client). For an ongoing
autonomous research agenda that ticks on the server without a client, use
research_loop_start (see the research_loop skill) — never chain teamup_run
as a scheduler.

## Flow (start = gated, never silent)

1. teamup_start — pick the crew by task class:
   - hpc — pm/se/pg/cd for HPC code optimization: creates a session, tailors
     every role's prompt from THIS project and STARTS the first iteration
     async (optional hardware_target, code_path, max_iterations,
     parallel_roles). The permission ladder gates it (autopilot fires).
   - research, code, paper-writing, kakenhi-report, peer-review — each is a
     team plan template (ADR-092 slice 4; see the team_plans skill): the
     call runs it exactly as team_plan_run(template=...) would. The user
     approves the plan's card first, in every mode; then it runs in the
     background and you read it with team_plan_result(run_id), not with the
     teamup_* tools (team_plan_cancel(run_id) stops it). models={"reviewer":
     ...} sets its reviewer_model (research also takes "producer";
     peer-review takes no model).
   Parameters: goal (natural language), crew, run?, models?, plus the hpc
   knobs above.
2. Hand the user the watch point, not another command: the Work pane's teams
   row (Enter = roster + per-agent prompts) or teamup_status.
3. teamup_run — CONTINUE an existing session with 1-3 more iterations
   (refuses while busy — never starts two). Ask before burning more budget.
4. teamup_conclusions — what the team CONCLUDED (every role's persisted
   final answers). The authoritative answer to "what did the team produce":
   read it here, never via rag_query/search_agentic — those index documents,
   not team iteration outcomes.

## Budget discipline

An hpc session's rounds are charged to this chat's usage and caps; its own
ceiling is max_iterations. Never inflate iterations on your own: if
teamup_status shows iterations spent without progress, surface that fact
plainly instead of proposing a silent restart. A crew's plan has its own hard
token budget (team_plans skill).
