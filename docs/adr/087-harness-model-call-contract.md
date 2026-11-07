# ADR-087: Harness model-call contract — stream every agent round, send an output limit, continue on exhaustion

**Status:** Accepted (2026-10-05, maintainer: "make updates of those waves"; decisions taken autonomously per the 2026-09-24 delegation, reasons below)
**Date:** 2026-10-05
**Relates to:** D-26 (agent harness), D-28 (context budget), D-11 (one inference API), ADR-076 (decision model). Plan: r4-umrs-harness-repair-plan-2026-10-05.md wave W1. Not D-01/D-02: the transport (HTTPS route to `/gw/llm` or a public source) is unchanged.

## Context

Reasoning models (glm-5.3 measured on Terminal-Bench 4.0, 2026-10-04/05) routinely spend minutes, sometimes more than an hour, inside one reply before the first answer token or tool call:
- median request about 17 s, p90 about 110 s, longest 93 min;
- one first turn produced 90,688 completion tokens before acting.

Four parts of R4's model call broke on that:
1. **Non-streaming agent loop.** `ResearchCompanion._chat` sent `"stream": False` (`r4/src/r4/companion/agent.py`, `_chat_payload`), so no byte flowed until the reply was complete. Proxies cut such requests: rikyu's gateway at 600 s (502, reproduced three times), nginx `/gw/llm/` at 600 s (`gateway/nginx/conf.d/server.conf`), and the UMRS LLM proxy at 120 s (`umrs/src/umrs/api/llm.py:80`).
2. **No output limit.** R4 sent no `max_tokens`, so the server default applied (rikyu: 32,768). One reply could spend the whole budget on reasoning and end with `finish_reason=length` and no content.
3. **The recovery made the agent give up.** On such a reply R4 nudged "Write the final answer now… no tool calls" and stopped at the second one in the turn (a Terminal-Bench task is one turn). Of the 17 failed TB 4.0 tasks, 6 had a cut-off reply. 4 of them ended after 3–11 model calls: 2 with R4's stop text, 2 "gave up" after the nudge.
4. **An empty reply disabled tools for the session.** `_looks_like_tool_mode_artifact("")` is true, so `_should_retry_without_tools` set `_tool_calling_disabled = True` and the turn ended on the "Checkpoint after N tool rounds" digest (TB 4.0 `satb-audio-transcription`).

## Decision

**D-1 Stream every agent round.** A blocking chat call that carries a turn-cancel token (every companion agent-loop round) is sent upstream with `stream: true, stream_options.include_usage: true`. It is assembled into the same response dict a non-streaming call returns (`r4/src/r4/stream_assembly.py`, `Client._post_streamed`), so the loop is unchanged:
- content, reasoning (`reasoning_content` or `reasoning`), split tool-call deltas, finish reason and usage are all assembled;
- a JSON reply to the stream is parsed as a full response;
- an HTTP 400 to the streamed form is retried once as a plain POST;
- `R4_STREAM_BLOCKING=0` restores the plain POST;
- calls without a turn token (CLI chat, scripts) are unchanged.

`R4_TIMEOUT` thus becomes an idle-between-chunks limit for agent rounds.

**D-2 Send an output limit.** Every agent round sends `max_tokens`, resolved in this order:
1. `R4_MAX_OUTPUT_TOKENS` (0 = never send);
2. the model's published limit (`model_output_limit`, from `/v1/models` metadata — W5/W4);
3. 131,072 for known reasoning families (`r4.model_context.default_max_output_tokens`: glm-5/4.5/4.6, kimi-k2/k3, deepseek-r1/v3.1/v3.2, qwen3, qwq, gpt-oss);
4. otherwise nothing (server default).

The value is clamped to the context window left after the prompt, and nothing is sent when less than 4,096 tokens of room remain.

**D-3 Continue, do not wrap up, on output exhaustion.**
- A `finish_reason=length` reply with no content and no tool call is answered with a continuation nudge: "do not restart and do not stop: make the next tool call, or give the final answer if done; keep reasoning short".
- The nudge carries the last 2,000 characters of the cut reasoning.
- Up to `R4_COMPANION_LENGTH_RECOVERIES` (default 4) per turn are allowed, then the named stop.

**D-4 An empty reply is not a tool-mode artifact.** In tool mode, an empty reply (no text, no tool call) gets up to two continuation nudges per turn. Only after that does the old artifact/digest path apply. Tools are disabled for the session only for genuinely structured bogus text.

**D-5 Close a long deliberation instead of waiting it out (amendment 2026-10-06).** A round whose reasoning passes a thinking budget before any answer text or tool call is stopped by the client, and the agent closes the deliberation:
- the budget is `R4_COMPANION_THINK_BUDGET` tokens (default 16,000, counted as 4 chars per token; `0` = off);
- the client stops reading the stream (`Client._post_streamed`, `Client._collect_sse_deltas`); closing the connection makes the server abort the generation, and the reply carries `finish_reason="think_budget"`;
- the agent re-sends the round with an assistant prefill holding the reasoning so far plus one closing sentence, with `continue_final_message: true, add_generation_prompt: false` (vLLM). The chat template closes the thinking block and the model continues with its answer or tool call (`ResearchCompanion._close_deliberation`);
- the closed reasoning stays on the assistant message, so the replay window (W4) keeps it for the next round;
- a backend that refuses the prefill (any error), or deliberates again past the budget, turns the budget off for the session; that round then counts as an output-limit cut, and D-3 carries on.

**D-5b The thinking budget is also a time budget (amendment 2026-10-07).** At 8 parallel TB trials, rikyu decoded about 14 tokens/s per stream, so the 16k-token budget took 18–20 min and one step reached 29 min (budget, then a tool-call cut). A deliberation with no answer or tool call yet is now also closed after `R4_COMPANION_THINK_BUDGET_SEC` seconds from the request (default 600; 0 = off), whichever limit comes first. The clock includes queueing and prefill, so one step stays bounded on a slow endpoint.

**D-6 Stop planning written into tool calls (amendment 2026-10-06, later).** With D-5 in place, glm-5.3 moved its deliberation into tool calls: heredoc scripts of 44k–268k chars whose lines were 92–99% comments ("let me carefully plan…"); the 268k one took 59 min. D-5 cannot see this, because it counts reasoning only.
- A round is now also stopped while its tool-call arguments stream, when they pass `R4_COMPANION_TOOL_ARGS_BUDGET` (default 100,000 chars), or a quarter of it with at least 80% comment lines over 100+ lines (`stream_assembly.planning_in_args`, checked every 4k chars). The reply then carries `finish_reason="args_budget"`.
- The unfinished call never runs (`_planning_cut`). The output-budget ladder (D-3, same counter) asks for a runnable step: plan in the reasoning, write large files in parts. The nudge carries the last 4,000 chars of what was written.
- On the 14 tool calls over 20k chars in the two TB 4.0 lanes, it stops all 8 that are 78–99% comments at about 25.5k chars, and lets all 6 real code files (2–46% comments, up to 58k chars) run.

**D-7 A cut text reply is not an answer; unattended runs budget reply text (amendment 2026-10-07).** With D-5 and D-6 in place, a TB 4.0 round deliberated in plain reply text: 131,072 tokens (89 min) with no tool call, cut at the output limit. D-3 only handled cut replies with no text, so R4 took the cut text as the final answer and the task ended at 4.9 of its 8 hours.
- In tool mode, a reply cut at the output limit (or the text budget) without a tool call goes to the ladder: its last 8,000 chars stay in the history as the assistant's message, and a nudge asks for the tool call of the next step.
- Unattended runs (`R4_AUTONOMOUS=1`) also stop reply text past `R4_COMPANION_TEXT_BUDGET` chars (default 64,000 ≈ 16k tokens) before any tool call starts (`finish_reason="text_budget"`). Interactive sessions default to no text budget: a long written answer can be what the user asked for.

Also fixed with D-5: a streamed round (TUI path) that produced only reasoning, such as a cut or an empty answer, was re-sent as a blocking call, which repeated the whole deliberation. It now returns its reasoning and `finish_reason`, and D-3/D-4 apply.

### Why

- Streaming fixes all three proxy cuts at once, for every route, without touching proxies we do not own (rikyu). The server-side timeouts (W5) become a backstop, not the fix.
- The output limit and the continuation nudge address the cause measured on TB 4.0: the harness, not the model, ended the work.
- The defaults (131,072; 4 recoveries; 2 empty nudges) match the public glm-5.3 runs (Vals: 128,000 max output; Z.AI's own Claude Code runs 128K) and the TB 4.0 evidence. All three are configurable.
- Scoping D-1 to agent rounds keeps every other client call, and the many tests that mock `http.post`, unchanged.
- D-5: glm-5.3 at a design point of a hard task deliberates past 16k tokens whatever R4 sends (24/24 samples over six request shapes), and harbor's reference agent terminus-2 has 17–26 min single replies on the same tasks, so the length is the model's. What R4 controlled was how long it waited: up to 131k tokens, 30–80 min at rikyu's ~25 tokens/s. A prompt line asking for incremental work changed nothing (6/6 still past 16k), and a continuation nudge after a cut kept deliberating (6/6), while closing the deliberation made the model act at once (6/6, 115–479 tokens). The action it took was the experiment it had been designing around. Repair plan §5.9 has the numbers.

## Consequences

**Positive:**
- Long reasoning turns survive any proxy that cuts idle connections.
- Reasoning models get room to finish a turn.
- An output-limit cut or an empty reply no longer ends a task.

**Cost:**
- One more code path, the stream assembler (tests: `r4/tests/test_harness_model_call_contract.py`).
- The output limit is sent for reasoning families even when a server's own default would have sufficed.
- A served model with a smaller context than the name table claims can reject the limit. W4 replaces the name-table window with the served one from `/v1/models` (`model_context_window`).

**NOT in scope:** transport (D-01/D-02); reasoning replay across rounds (W4, plan row H4); tool limits (W2).

## Alternatives Considered

- **Raise proxy timeouts only.** Rejected: rikyu's 600 s cut is outside our control, and any future proxy adds another cut.
- **Stream every client call.** Rejected: it changes CLI chat and script behaviour, and dozens of tests rely on `post` mocks, for no benefit outside long agent turns.
- **Keep "write the final answer now".** Rejected: TB 4.0 shows it ends agentic tasks that were still in progress.

## Rollout-status (2026-10-05)

- D-1–D-4 landed repo-side (wave W1 of the repair plan; committed to `main` 2026-10-06):
  - code: `r4/src/r4/stream_assembly.py`, `r4/src/r4/client.py` (`_post_streamed`, `_stream_blocking_enabled`), `r4/src/r4/companion/agent.py` (`_output_token_limit`, `_requested_output_tokens`, the ladder, `_nudge_empty_reply`), `r4/src/r4/model_context.py` (`default_max_output_tokens`);
  - tests: `r4/tests/test_harness_model_call_contract.py` (15) plus the updated `test_companion_output_budget.py`, `test_companion/test_agent.py` and `test_client.py`; 1,893 related tests pass.
- Receipt pending: the TB 4.0 re-run after waves W1–W4 (repair plan §2).
- 2026-10-05 (later): the output-limit source now also includes the route's `/v1/models` entry (`Client.model_entries`, cached 300 s), which is where UMRS publishes `max_output_tokens` (W5/U3); the endpoint listing carries only `max_model_len`. The Apache edge (`ProxyTimeout`/`Timeout` 600 → 7200 s) was the last 600 s cut on non-streaming calls through a front, template-side only (plan §5.1 row U2). TB 4.0 re-run from scratch launched as `tb40-20261005` (plan §5.3).

## Rollout-status (2026-10-06, D-5)

- D-5 landed repo-side (repair plan §5.9; ledger 2026-10-06 (6)):
  - code: `r4/src/r4/stream_assembly.py` (`THINK_BUDGET_FINISH`, `past_think_budget`), `r4/src/r4/client.py` (`think_budget_chars` through `_request` → `_post_streamed`, and `_stream_request_collect` → `_collect_sse_deltas`, which also records `_last_stream_finish`), `r4/src/r4/companion/agent.py` (`_think_budget_chars`, `_close_deliberation`, `_think_close_refused`, `_agentic_round_response`, `_stream_agentic_round`);
  - tests: `r4/tests/test_harness_think_budget.py` (13); the r4 suite passes (4,110).
- Live check (2026-10-06, rikyu glm-5.3 via the bench proxy, R4's captured biped mid-task request, budget 4,000 tokens): both the blocking and the streamed path stopped at 16,000 chars, closed the deliberation and returned a `shell_exec` call 3.5 min after the start. Without the budget the same request ran past 16k tokens in 24/24 samples.
- Not verified: whether the UMRS route (`/gw/llm` → LiteLLM → vLLM) passes `continue_final_message` through. If it does not, the first refusal turns the budget off for that session and D-3 applies as before.

## Related

- r4-harness-benchmark-detailed-2026-10-04.md — same-model benchmark
- `benchmarks/harness/terminal/model_proxy.py` — the stream-and-assemble approach first proven on the benchmark host
