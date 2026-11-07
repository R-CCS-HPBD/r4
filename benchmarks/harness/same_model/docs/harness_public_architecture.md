# Claude Code 2.1.288 and OpenCode 2.0.18: architecture notes (2026-10-04)

Sources: Claude Code docs at code.claude.com (retrieved 2026-10-04; they track release 2.1.289); OpenCode source at tag
v2.0.18 (commit cd9a14a6, 2026-09-25; sst/opencode redirects to anomalyco/opencode). The opencode.ai docs still use v1
names, so they were not used for v2 specifics. "Observed" = our benchmark.

## Claude Code (`claude -p --output-format json`)
1. Loop: each request carries the system prompt, tool definitions and the full history. Claude Code runs the requested
   tools, returns the results as a user turn and calls the model again. The loop ends on a response with no tool calls
   [C2]. Print mode has no turn limit unless `--max-turns` / `--max-budget-usd` is set [C17]. Read-only tools (Read, Glob,
   Grep, read-only MCP) run concurrently, capped by `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` (default 10); tools that change
   state, Bash included, run one after another [C2][C4]. Our build had no Grep/Glob, so multi-call responses containing
   Bash ran serially (inference).
2. System prompt: the full Claude Code prompt (tool guidance, security and safety). CLAUDE.md and environment are sent in
   the conversation, not in the system prompt [C16][C1]. `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT=1` abbreviates it [C4].
   Observed first request: ~14.6k tokens.
3. Tools and output limits:
   - Tool set: Glob/Grep left out by default on Linux (search goes through Bash with built-in bfs/ugrep); Task and
     TodoWrite left out for unrecognised model IDs since v2.1.268 [C3]. This gives the observed 18 tools.
   - Bash: successful output inline up to ~30,000 chars, beyond that a file path plus a 2,000-char preview; failed
     commands get a ~10,000-char head+tail. `BASH_MAX_OUTPUT_LENGTH` defaults to 30,000 (ceiling 150,000). Timeout 2 min
     by default, 10 min maximum [C3][C4].
   - Read: line-numbered output. An over-limit whole-file read returns the first page with a "PARTIAL view" notice; an
     over-limit explicit range returns an error [C3][C4].
   - Other: Glob returns at most 100 files; Grep pages with head_limit; MCP output is capped at 25k tokens; large
     outputs go to `<session>/tool-results/` [C3][C4][C13].
4. Context: history grows each turn. Near the limit Claude Code first clears old tool outputs, then summarises [C1][C7].
   For non-`claude-` model IDs the window comes from `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (we set 1M) [C5]. At our ~59k peak,
   neither clearing nor compaction fired (inference).
5. Headless permissions: mode from flags, then settings, then the built-in default. Prompts that need an answer are
   denied in -p [C8][C9][C10].
6. Extra model calls: no title for `-p` [C12]. Subagents (Agent: Explore, Plan, general-purpose; Workflow) get their own
   context [C11]. Non-essential-traffic disabling suppresses no model calls [C4].
7. Persistence: one `~/.claude/projects/<project>/<session>.jsonl` per session with every message, tool call and result
   (30-day retention) [C12][C13].
8. Reasoning: for unknown IDs it sends adaptive thinking/effort fields, retries without them if the upstream rejects
   them, and strips thinking blocks whose signature is rejected [C15]. Default max output for an unknown ID is 32,000
   tokens, which is the observed max_tokens=32000 [C4].

## OpenCode v2 (`opencode run --format json --standalone`)
- Process model: the CLI is a client of a background service. `--standalone` starts a private `serve --stdio` child for
  the run's lifetime [O16].
1. Loop: each step streams the model response. Every tool call is started concurrently as soon as it appears in the
   stream, then all are awaited [O1]; no concurrency cap was found (inference). Another step runs if any local tool was
   called; the loop ends on a step with none [O3][O2]. An optional per-agent `steps` cap (unset by default) appends
   "MAXIMUM STEPS REACHED" and disables tools [O4]. On a context-overflow error it compacts and retries [O1][O2].
2. System prompt: a ~750-char template (agent intro, markdown output, system-reminder blocks, "Prefer parallelizing
   independent tool calls") plus tool-specific lines [O5]. Model-specific prompts exist for gpt, claude, kimi, trinity
   and muse IDs; glm-5.3 matches none (inference). Cwd, platform and date are added as instruction blocks [O25].
   Observed first request: ~5.9k tokens.
3. Tools and output limits:
   - Tool set: read, write, edit, patch, glob, grep, shell, subagent, skill, question, webfetch, websearch, and execute
     ("Code Mode" JS sandbox; file and shell tools excluded) [O10].
   - Generic cap: any tool output is cut at 2,000 lines / 50 KiB; the full output is saved to `<data>/tool-output/` with
     a marker [O6].
   - Read: up to 2,000 lines per call (default and cap), 50 KiB, 2,000 chars per line, plus a continue-offset hint [O7].
   - Shell: returns the last 2,000 lines / 50 KiB plus the full-output path; 2-min default timeout [O8].
   - Grep and glob: 100 results with a truncation notice [O9].
4. Context: v2 does not prune old tool outputs (v1's `compaction.prune` is rejected) [O12], so results stay in full until
   compaction. Compaction triggers at min(input − 20k, context − max(min(output, 32k), 20k)); unknown models default to
   200k context / 32k output, which puts the trigger near 168k [O11][O22]. The summary has fixed headings and keeps the
   last ~15k tokens of transcript with tool results cut to 2,000 chars [O11]. At our ~62k peak compaction never ran
   (inference).
5. Permissions: last matching rule wins, unmatched means ask. The default allows `*` except paths outside the workdir
   and `.env` files [O17][O26]. `run` auto-rejects asks unless --auto [O16].
6. Extra model calls: one title request per session (hidden title agent, small model or main model, lowest reasoning)
   [O13][O14]; a compaction agent [O14]; the subagent tool (general, explore) [O15].
7. Persistence: SQLite (WAL) for events, sessions and messages; per-step usage; git snapshots in git projects
   [O18][O1][O19].
8. Reasoning: reasoning text is replayed in its native field (`reasoning_content` / `reasoning_details`) [O20]. No
   max_tokens unless configured [O21].

## Comparison
| Dimension | Claude Code | OpenCode v2 |
|---|---|---|
| Loop end | No tool calls (optional max-turns/budget) | Step with no local tool calls (optional steps cap) |
| Parallel tools | Read-only ≤10 concurrent; Bash/Edit/Write serial | All calls concurrent, started while streaming |
| System prompt | Full CC prompt, ~14.6k first request | ~750-char template, ~5.9k first request |
| Shell output | ~30k chars inline, else file + 2k preview | Last 2,000 lines / 50 KiB + file path |
| Old tool results | Kept; cleared, then summarised near the limit | Kept in full until compaction (no pruning) |
| Compaction trigger | Declared window (1M here) | ~168k by default |
| Title call | None in -p | One per session |
| Transcript | JSONL | SQLite + git snapshots |
| Reasoning | Thinking fields; retry without; max_tokens 32k | Native replay; no max_tokens |

References: [C1] how-claude-code-works, [C2] agent-sdk/agent-loop, [C3] tools-reference, [C4] env-vars, [C5]
model-config, [C6] context-window, [C7] glossary, [C8] headless, [C9] permission-modes, [C10] permissions, [C11]
sub-agents, [C12] sessions, [C13] claude-directory, [C14] costs, [C15] llm-gateway-protocol, [C16]
agent-sdk/modifying-system-prompts, [C17] cli-reference — all under https://code.claude.com/docs/en/. OpenCode:
https://github.com/anomalyco/opencode/blob/v2.0.18/packages/ — [O1] core/src/session/runner/step.ts, [O2] .../llm.ts,
[O3] .../publish-llm-event.ts, [O4] .../max-steps.ts, [O5] .../prompt/system.txt + core/src/session/system-prompt.ts,
[O6] core/src/tool-output.ts, [O7] core/src/tool/read-filesystem.ts, [O8] core/src/shell.ts, [O9]
core/src/tool/plugin/grep.ts, [O10] core/src/codemode/tool.ts, [O11] core/src/session/compaction.ts, [O12]
core/src/config/normalize.ts, [O13] core/src/session/title.ts, [O14] core/src/plugin/agent.ts, [O15]
core/src/tool/plugin/subagent.ts, [O16] cli/src/commands/commands.ts + cli/src/services/standalone.ts, [O17]
core/src/permission.ts, [O18] core/src/database/database.ts, [O19] core/src/snapshot.ts, [O20]
ai/src/protocols/openai-chat.ts, [O21] core/src/session/model-request.ts, [O22] schema/src/model.ts, [O25]
core/src/instructions/builtins.ts, [O26] schema/src/agent.ts.
