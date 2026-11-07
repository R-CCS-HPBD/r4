---
name: research_loop
description: "Runs the canonical research spine: build project knowledge, start/watch/stop server-hosted autonomous research loops, answer their approvals, read findings. Use for research loop, autonomous research, run research, start the loop, findings, knowledge graph requests."
allowed-tools: "project_list project_use project_scaffold project_ensure project_status knowledge_status knowledge_ingest knowledge_show knowledge_query knowledge_graph_list knowledge_graph_propose knowledge_graph_decide knowledge_build_from_context research_loop_start research_loop_list research_loop_status research_loop_stop research_loop_approvals research_loop_decide research_session_create research_session_view workflow_list workflow_describe"
metadata:
  x-r4-keywords: "research loop, autonomous research, run research, start research, start the loop, run the loop, start a loop, persistent loop, autonomous loop, iterate over, watch the loop, stop the loop, loop status, loop list, research findings, show findings, list findings, pending approvals, approve the loop, research session, knowledge ingest, ingest artifacts, add papers, add files, add sources, knowledge graph, knowledge status, show knowledge, add to knowledge, research_loop, knowledge_ingest"
---

You drive the one research spine (ADR-036): knowledge_* builds the project
knowledge graph, research_loop_* runs the server-hosted autonomous loop on
top of it, workflow_* is the compute layer each experiment runs on.

A loop is NOT a team session: the loop engine ticks autonomously on the
server (survives client exit) toward papers/findings. For a bounded
one-goal multi-agent task (a code change, one optimization sweep) whose
iterations you drive by hand, use teamup_start (see the teamup skill). Per
the D-29 tail the sanctioned direction is the loop SPAWNING a team inside
an iteration — never a team session masquerading as a loop.
{{contract:r4_contracts.agent_tool_playbook:SPINE_DISAMBIGUATION}}
{{contract:r4_contracts.agent_tool_playbook:RESEARCH_LOOP_AGENT_GUIDANCE}}
{{contract:r4_contracts.agent_tool_playbook:KNOWLEDGE_AGENT_GUIDANCE}}
Workflow discovery: never invent a workflow name — workflow_list shows what is
registered; with exactly one registered workflow UMRS uses it automatically.
Approvals are the human's: list them with research_loop_approvals and record
only the human's stated answer with research_loop_decide.
Prefer these tools over raw CLI.
