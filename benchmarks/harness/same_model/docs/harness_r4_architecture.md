# R4 companion harness — architecture (code as of 2026-10-04)

Paths are relative to `r4/src/r4/`; `c/` = `companion/`; `adapter` = `benchmarks/harness/adapters/r4_companion.py`.

## 1. Entry and lifecycle

**Construction.** Each case runs in its own subprocess, which `chdir`s into the workdir and builds `Client()` and `CompanionSession(model=<--model>, permission_mode=TRUST, endpoint_refresh_interval_sec=86400)`. It installs an always-`True` approval callback and calls `session.send(prompt)` once, without streaming (adapter:50–74, 149–161). The session wires a `ContextManager`, a `SkillRegistry` with `umrs` pre-activated, a `PermissionManager`, the server trust checker and the ADR-076 escalation hook (c/session.py:226–248, 296–318; c/skills.py:149). Temperature defaults to 0.4 (c/agent.py:1582), and tools are built once per session (c/session.py:194–199).

**Turn flow.** `send()` runs `ensure_model_ready` (provisioning check plus context-budget sync) on a thread joined before the turn (c/session.py:405–413, 497; c/model_resolution.py:298–333). `send_turn` then:

1. appends the prompt verbatim as the user message, auto-activates skills and selects tools (c/session_flow.py:14–35);
2. refreshes endpoints once; the guard can only warn or block (c/endpoint_policy.py:97–142);
3. assembles extra system text, compacts prior history, fits the payload (c/session_turns.py:136–170);
4. runs `agent.send()`, then appends the final text and runs post-turn hooks (c/session_flow.py:87–98).

History excludes the new message, so it is empty for a single-turn case (c/session_turns.py:60–61).

**Skill auto-activation.** A skill activates, together with its dependencies, when any of its keywords appears as a case-insensitive substring of the prompt. It stays active for the session (c/skills.py:125–128, 208–235). Activation adds the skill's body to the system prompt and its `allowed-tools` to the offered tools. "knowledge graph" activates `research_loop` (23 tools, 5,560-char body), which is consistent with the jump from 53 to 78 tools. "file" activates `code` and `data_management`.

**Routing.** `glm-5.3@rikyu` splits at the last `@`; the inline source wins and maps to `<url>/gw/llm` (platform) or `<url>` (public) (model_routing.py:41–55, 93–129). The suffix is stripped from the payload (client.py:947–954); a code comment calls rikyu a public source (model_context.py:79–80). Because the session always sets a cancel token, `OpenAICompatibleProvider` is bypassed for a cancellable HTTP POST to `/v1/chat/completions`, 300 s read timeout (providers/factory.py:27–60; client.py:634–650, 870–891, 996–1014; config.py:352).

**Request body.** The body contains only `model`, `messages`, `temperature: 0.4`, `stream: false` and `tools` (c/agent.py:2860–2874; providers/litellm.py:99–108). It sets no `max_tokens`, `tool_choice` or `parallel_tool_calls`, so the server defaults apply.

## 2. Prompt assembly

Each request is one system message followed by the user message (c/agent.py:2746–2780). The system content, in order:

| # | Block | Chars | Condition |
|---|---|---|---|
| 1 | `COMPANION_SYSTEM_PROMPT`: RiVault research-colleague persona; HPC rules; tool etiquette (`ask_user` for decisions, `final_output` once); local vs `*_platform` tools; evidence rules (commit hash, diff, test output) (c/agent.py:197–244) | 2,462 | always |
| 2 | Workspace root = cwd, "never scan home" (c/session.py:826–843, 861–863) | ~400 | always |
| 3 | Research-spine disambiguation + `platform_guide` pointer (c/session_turns.py:29–32) | 793 | always |
| 4 | Active skill bodies (c/skills.py:239–244) | `umrs` 12,558; `code` 1,590; `data_management` 1,188 | `umrs` always; others by keyword |
| 5 | Skill catalog, ≤40 one-liners (c/skills.py:246–265) | 3,532 | always |
| 6 | `[Persistent memory]` (c/context.py:413–420) | varies | `memory.json` non-empty |
| 7 | Harness: `[Environment]` probe, `[Session Continuity]`, ≤10 lessons, domain guide, workflow template (c/harness/__init__.py:65–98) | varies | probe always; continuity if the global progress file has turns; domain guide needs ≥2 messages (never on turn 1); template on ≥2 keyword hits |
| 8 | `[Active endpoints]` + guard warning (c/endpoint_state.py:134–159) | varies | endpoints listed |
| 9 | `<repo_briefing>`: R4.md→AGENTS.md→…→CLAUDE.md→OPENCODE.md, found by walking up from cwd (briefing.py:67–81, 129–160) | ≤4,000/file, ≤8,000 | any found |
| 10 | `<project_state>`: ~10 platform status sections (c/agent.py:2782–2795; c/status_report.py:241–261) | ≤4,000 | sections print errors rather than fail |

The system message is built once per turn. System nudges added during the loop are later folded into it (§4).

## 3. Tool surface

**Build.** `get_builtin_tools` combines core local tools (c/tools.py:1046–1421), client tools for endpoints, jobs, models, storage, RAG and MCP (c/tools.py:1705–2705), six family builders (workflow, project, research-loop, knowledge, first-class MCP, decision; c/tools.py:1695–1702), client extras, and the agent built-ins `ask_user`, `todo_write`, `tool_search`, `web_*`, `final_output`. Read-only names are flagged `concurrency_safe` (c/tools.py:3147–3205).

**Per-turn filters.**

1. **Skill allowlist.** `umrs` declares `allowed-tools`, so only these tools survive: the union of active allowlists, the always-on set {`ask_user`, `todo_write`, `tool_search`, `final_output`, `platform_guide`} and any tool marked `always_on`. They are sorted by name (c/session_turns.py:64–82; c/skills.py:136–144).
2. **Pruning.** Tools denied earlier in the session and tools closed by the plan gate are removed (c/agent.py:1710–1713).
3. **Strategy.** `passthrough`, unless `R4_TOOL_SELECTION_STRATEGY=family` is set (c/agent.py:1751–1766).
4. **Availability.** `file_list_platform`, `file_upload_platform` without ZMQ, and unwired `tool_search`/`web_search` are dropped (c/tool_availability.py:111–121).

As a result, first-class `mcp.<server>.<tool>` tools (c/mcp_first_class.py:137) and `activate_skill` (c/session.py:252–294) are never offered.

| Core tool | Limits |
|---|---|
| `shell_exec(command, cwd?, timeout=60)` | `find` capped at 25 s; output >8,000 chars → first 4,000 + last 2,000 (c/tools.py:152–195) |
| `python_exec(code, timeout=60)` | same cap (c/tools.py:198–220) |
| `file_read(path, max_lines=500, offset, limit)` | whole file verbatim if ≤500 lines, else a numbered slice (c/tools.py:228–260) |
| `grep_search` / `glob_search` / `list_dir` | ≤200 lines, matches or entries; ripgrep with a 30 s timeout; `list_dir` is non-recursive and honours .gitignore (c/tools.py:474–610) |
| `final_output(summary, result?)` | returns `summary\n\nresult` (agent/tools.py:380–409) |

The platform tools offered through `umrs`/`data_management` (endpoints, jobs, sources, pools, RAG, lab catalog) need UMRS and are idle for a local corpus. Headless, `ask_user` returns a stub (agent/tools.py:135–141).

## 4. Agent loop

Each round (c/agent.py:1962–2056) runs these steps:

1. **Fit.** `fit_payload`; **every** system message is folded into the leading one; pairing repaired (c/agent.py:1490–1527, 3010–3039).
2. **Request** with bounded history and retries (c/agent.py:2825–2891).
3. **Parse** `tool_calls`, else tagged/open-pipe/XML dialects (c/agent.py:3041–3064).
4. **Record** one assistant message with content, all calls and echoed `reasoning_content` (c/agent.py:1926–1938).
5. **Prepare** (serially). Repair arguments, including GLM's stringified containers, then run pre-hooks, the permission gate and the tool lookup (c/agent.py:1101–1188, 3205–3258).
6. **Execute.** Consecutive eligible calls share a pool of up to 8 threads. Eligible calls are `concurrency_safe` tools, or `shell_exec` led by ls/cat/head/find/wc/… with no redirect. Everything else runs serially, and result order is kept (c/agent.py:604–703, 3138–3195).
7. **Append results.** Add one `role:tool` message per call and record steps for the guards (c/agent.py:1940–1954). The `[-8:]` trim rebinds a local name (c/agent.py:1953–1954). Once 8 steps exist, the guards see only the first call of each round.
8. **Finish on `final_output`.** Its result is the answer (c/agent.py:2039–2042).
9. **Guards.** Replays, failure streaks or repeated ssh calls to one host trigger one corrective nudge. If they persist, the loop switches to answer mode with no tools (c/agent.py:445–459, 2226–2265).
10. **Checkpoints and roof.** Every 25 rounds without new evidence, a checkpoint message is added and the loop continues. At the 1,000-round roof, a wrap-up request runs without tools (c/agent.py:54–55, 2267–2323).

**Final text.** A reply with no tool calls ends the turn, with these exceptions:

- `finish_reason=length` with empty content gets one nudge (c/agent.py:2110–2132).
- Empty content, scaffold text, or JSON with keys such as `result` counts as a tool-mode artifact, unless the prompt mentions json/csv/schema/object (c/agent.py:337–351, 1016–1054). An artifact turns tools off for the session.
- Empty content after at least one tool round returns the canned "Checkpoint after N tool rounds" digest as the answer (c/agent.py:2325–2418, 2797–2823).

**Retries.** Three attempts with backoff `min(max(1,hint)·2^(n−1), 20 s)·U(0.8,1.2)`. They cover 408/409/425/429/500/502–504, timeouts and transport errors (c/agent.py:1195–1268, 2957–3008). A 400 overflow drops the oldest quarter of non-system messages once, and a 400 tool-parser error retries without tools (c/agent.py:311–334, 2893–2930).

## 5. Context management

| Layer | Behaviour |
|---|---|
| Per result | Up to 12k chars verbatim; 12k–36k → head 6,000 + tail 3,000; over 36k → written to `$TMPDIR/r4_large_<tool>_<sha12>.txt`, with 500 chars inline (c/agent.py:48, 3092–3097; agent/large_response.py:51–83). Shell and Python output is already capped near 6k. |
| History bounding (wire only) | Only the **last `role:tool` message** stays full. Earlier bodies over 2,048 chars become 1,024 head + stamp + 1,024 tail (c/agent.py:1409–1448, 2868). The docstring promises the last *turn* in full, but in a k-call round, k−1 results are cut before the model reads them. A body also changes one request after it stops being last. |
| Reasoning | Echoed on every tool-round message and never stripped (c/agent.py:1933–1937); counted in estimates (c/context.py:135–137). |
| Budget | Endpoint `max_model_len` if the name matches exactly, otherwise the name table: `glm-5` → 1,048,576 × 0.9 − 4,096 = 939,622 tokens, estimated at chars/3.5 (c/context_budget.py:12–73; model_context.py:102). |
| Trim / compaction | `fit_payload` trims only above the budget (c/context.py:200–266). Compaction runs only at turn start, on prior turns, at 80 % (`R4_COMPANION_COMPACTION_THRESHOLD`), and never inside the loop (c/session_turns.py:85–107; c/context.py:293–387). |

At the measured 42k-token peak, neither trimming nor compaction engages.

## 6. Permissions and decision model

`check()` runs these tiers in order (c/permissions.py:143–151, 474–532):

1. DENY.
2. `NEVER_AUTO_APPROVE` {`knowledge_graph_decide`, `research_loop_decide`} and the taint fence.
3. Credential and shell fences.
4. TRUST.
5. Session approvals.
6. Explicit approval.

In TRUST mode with the server checker, tools on the server allowlist are approved automatically. The cache holds an allowed result for 60 s and a refusal for 10 s. Tools off the allowlist need explicit approval (c/permissions.py:400–436). The bench callback approves every explicit request (adapter:71), so no tool is ever hidden (c/agent.py:3321–3339).

The decision gate applies only to RISKY auto-approvals such as `shell_exec` and `python_exec` (c/permissions.py:95–117, 445–456). With `R4_DECISION_MODEL=off` it returns before making any call (decisions/settings.py:22–31; decisions/harness.py:87–96). When the model is on, each auto-approval adds one `/v1/completions` call (`max_tokens=1`, 5 s timeout) and escalates when P(harm) ≥ 0.5. That call is logged to `decision_log`, not `usage_summary`, and `decision_evaluate` is advertised too (decisions/harness.py:43, 142, 167–211; c/decision_tools.py:73–88).

## 7. Persistence and observability

- **No transcript.** Session history keeps only the prompt and the final text (c/session_flow.py:23, 95). Tool messages are local to the loop, and `sessions.db` is never written (c/session.py:96–98; adapter:78–80).
- **Events** go to listeners, and the bench registers none (c/agent.py:3393–3406). The SQLite `EventLog` is wired only into `c/ui/bridge_server.py`.
- **Journal.** An in-memory FIFO of 32 entries, at most 4 MiB each, holds the capped text (not the raw output) and is lost when the process exits (c/agent.py:468–483, 3341–3347).
- **Global files.** These are not isolated per case. The run reads `~/.config/r4/companion/{memory,lessons}.json` and the skill dirs, including `./skills` (c/context.py:28–33; c/harness/steering.py:26–30; c/skills.py:353–364). It writes `progress/current.json` after every turn (c/harness/progress.py:97–106), so later cases get a `[Session Continuity]` block.
- **Ablation:** `R4_HARNESS_ABLATE_{STEERING,GUIDES,MEMORY}` (c/ablation.py:21–39).
- **Knobs:** `R4_COMPANION_{MAX_TOOL_ROUNDS,TOTAL_TOOL_ROUNDS,TRANSIENT_RETRIES,TOOL_CONCURRENCY,COMPACTION_THRESHOLD,*_DIR}`, `R4_TOOL_SELECTION_STRATEGY`, `R4_TIMEOUT`.

## 8. One round

```
messages ─► fit_payload (fold system msgs; 939k budget) ─► repair pairs
   ▲                       ─► bound history (last tool msg full; others 1k+1k)
   │                       ─► POST {model, messages, temperature 0.4, tools}  (≤3 tries)
   │        ┌──── no tool_calls ─────────────┬──── tool_calls ─────────────────────────┐
   │        │ length+empty → nudge once      │ assistant{content,tool_calls,reasoning}  │
   │        │ artifact → tools off;          │ serial prepare: args→hooks→permission    │
   │        │   empty after tools → digest   │ execute: read-only batch ≤8 | serial     │
   │        │ else RETURN text               │ cap 12k / spill >36k → role:tool × k     │
   │        └────────────────────────────────┤ final_output → RETURN                    │
   │                                         │ guards → nudge → answer mode (no tools)  │
   └──────── every 25 rounds: progress check; 1,000 → wrap-up ──────────────────────────┘
```

## R4-side design choices that differ from Claude Code / OpenCode

| Area | R4 (code evidence) |
|---|---|
| Tool offer | Skill allowlists chosen by keyword substrings in the prompt; `umrs` always on; fixed for the turn (c/skills.py:125–149; c/session_turns.py:64–82) |
| System prompt | Persona + a skill body of at least 12.6k chars + catalog + harness + briefing + live platform status (§2) |
| Sampling | `temperature=0.4`; no `max_tokens`, `tool_choice` or `parallel_tool_calls` (c/agent.py:1582, 2860–2874) |
| Turn end | Any reply without tool calls, or `final_output` (c/agent.py:2039–2042, 2134–2172) |
| Parallelism | Threads for read-only calls; `python_exec` and `grep` run through the shell are serial (c/agent.py:3145–3195) |
| Old tool output | Cut to 1k+1k on the wire, including results from the same round (c/agent.py:1413–1448) |
| Large output | 12k cap; over 36k spills outside the workspace (c/agent.py:3092–3097) |
| Reasoning | Echoed on every tool round (c/agent.py:1933–1937) |
| Context | Window from the name table; compaction only between turns (§5) |
| Loop control | 25-round checkpoints, 1,000-round roof, empty reply → digest (§4) |
| State | Global memory, lessons and progress shared across cases; no transcript (§7) |
