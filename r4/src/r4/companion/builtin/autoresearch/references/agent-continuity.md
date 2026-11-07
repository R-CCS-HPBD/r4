# Agent Continuity: Keeping Research Running

Autonomous research runs for hours, overnight, or days. This reference covers how research keeps going in R4.

## R4

R4 has no interval timer the agent can set, and the agent cannot type slash commands. Do not try an interval command (such as `/loop 20m`) or a cron tool: neither exists for you. There are two ways research continues.

### 1. In the chat: turn by turn, with the workspace as memory

Within a session you keep working turn by turn. Between turns and sessions, the workspace files are your memory (see [Research State as Ground Truth](#research-state-as-ground-truth)). On every resume:

1. **Read state** — `research-state.yaml` and `findings.md` to remember where you are and what you've learned
2. **Check health** — is the current experiment running? Did it error? Is training diverging?
3. **If on track** → continue whatever you were doing (don't restart or re-plan)
4. **If stuck or broken** → step back, diagnose the issue, fix it, then continue
5. **If idle** → you shouldn't be. Pick the next hypothesis and start an experiment

### 2. Unattended: the server-hosted research loop

For durable, unattended continuation, hand the inner loop to the research-loop engine. Follow the `research_loop` skill (load it with `activate_skill`).

1. **Prerequisites** — start a loop only after `knowledge_ingest` has produced a non-empty knowledge graph for the project, and `workflow_list` shows the registered workflow each experiment runs.
2. **Session** — when a new line of inquiry starts, create a durable session with `research_session_create`.
3. **Start** — `research_loop_start(research_goal=..., max_iterations=..., session="latest" or "sess-...", workflow_name=...)`. Take `workflow_name` from `workflow_list`; never invent one. With exactly one registered workflow UMRS uses it automatically.
4. **Monitor** — `research_loop_status` / `research_loop_list`. Ask-mode loops park for approvals: `research_loop_approvals` lists them, and only the human answers them (`research_loop_decide` records the human's stated answer).
5. **Stop** — `research_loop_stop`.

The user can do the same from the TUI with `/loop start <goal>`, `/loop status <id>`, `/loop stop <id>`, and `/loop list`.

The loop runs as a separate iteration loop on UMRS (or the local runtime). It does not re-enter this chat on a timer. After the handoff, do not run your own parallel inner loop on the same project. When the user returns or the session resumes, re-read the workspace files and check the loop with `research_loop_status`.

### When Experiments Run Longer Than a Turn

This is normal and expected. On the next turn or resume:
- Check whether the experiment finished (`hpc_job_status` for a cluster job, `workflow_status` for a workflow run)
- If still running: monitor for errors, do lightweight work (update notes, search papers)
- Don't restart or duplicate the experiment

### Progress Reports

Write the HTML report into `to_human/` with `file_write` and tell the user its path. If they need a PDF, convert it with `shell_exec` (`weasyprint`, `playwright pdf`, or `wkhtmltopdf`, whichever is installed). Include: research question, key results, optimization trajectory plot, current understanding, next steps.

## Research State as Ground Truth

Both paths share the same ground truth: the workspace files.

| File | Purpose | Update Frequency |
|---|---|---|
| `research-state.yaml` | Machine-readable state | After every experiment and reflection |
| `research-log.md` | Decision timeline | After every significant action |
| `findings.md` | Narrative understanding + project memory | After every outer loop |
| `experiments/*/results/` | Raw experimental data | After every experiment |

If you lose context, read `research-state.yaml`, `findings.md` and `research-log.md` to reconstruct where you are. Keep them current.
