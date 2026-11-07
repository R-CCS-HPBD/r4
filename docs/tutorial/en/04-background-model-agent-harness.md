# Background — model, agent, harness, knowledge

Dated 2026-10-29 · R4 + UMRS tutorial · EN ([日本語](../ja/04-background-model-agent-harness.md))

A reading companion to the "under the hood" block of Part 1 (slides
11–31, minutes 15–55). It answers four questions: what does the **model**
compute, how does text become action (the **agent**), what is sent to the
model and who decides what runs (the **harness**), and how RAG finds the
right page (**knowledge**).

Every number here is measured, not illustrative. Tokens and next-token
probabilities come from a 3-billion-parameter Qwen2.5 model
(`Qwen2.5-VL-3B-Instruct`, same family as the stack's default `qwen2.5:7b`)
run locally with plain-text prompts; retrieval scores from the stack's own
embedder `e5-base-v2` and from `multilingual-e5-base`. Scripts and raw
data: `docs/tutorial/background/` (`next_token_probe.py/.json`,
`embedding_probe.py/.json`). File references point into `r4/src/r4/` unless
they say otherwise; line numbers are as of 2026-10-29 (commit `d1569ef38`).

(2026-10-29: updated to G9–G20 on main — background agents, usage-calibrated
token counts, microcompact, the compaction breaker, permission rule files,
protected paths, user hooks, `tool_search`, the output-limit ladder and the
wrap-up round. Section numbers are unchanged.)

(2026-11-01, after review: the deck gained the whole-picture map
(slide 11, below), separate skills and MCP slides, a "Why RAG?" slide and a
two-outcome RAG check; the four deployment patterns became three (slide 10:
all cloud, hybrid = your agent with a hosted model, all local). Slide numbers
in the headings follow the 51-slide deck. R4 now has an opt-in OS sandbox
(ADR-101), see §11.)

## How to read the scope tags

Most of what a harness does is not R4's invention. Each mechanism below
carries one of the three tags the slides show as coloured badges:

| Tag | Meaning |
|---|---|
| **[General pattern]** | Found in mainstream agent harnesses: Claude Code / Claude Agent SDK, OpenAI Agents SDK, opencode, LangChain / LangGraph. Learn it once, use it everywhere. |
| **[General · R4 settings]** | The pattern is general, but the numbers or choices are R4's own (stated next to the tag). |
| **[R4-specific]** | Not found in those harnesses; R4 adds it, usually for small self-hosted models, shared HPC platforms or research data. |

Where a comparison helps, a "Elsewhere:" line names how one or two of those
harnesses do the same job.

## The whole picture: one turn (slide 11)

The map the slides keep returning to (a small copy sits top right on every
background slide, the part being explained in amber):

1. **Context builder** — your request joins the system rules, the history,
   any active skill and retrieved text (§6, §9).
2. **Model** — one HTTP request carries that context and the tool list to the
   model (§1–5, §7).
3. **Agent loop** — the reply is plain text (the answer: done) or a tool
   call (§7–8).
4. **Permission gate** — run, ask you, or block (§11).
5. **Tools** — the tool runs on your machine, or through MCP or knowledge
   search on UMRS (§12–14); its result is added to the context → back to 2.

Only the model is AI; everything else is ordinary software you can read,
configure and test — the harness.

## 1. Text becomes tokens (slide 12)

**[General pattern]** — a property of every LLM. R4 never tokenizes: it
sends text, and the model server (Ollama, vLLM) splits it.

A model never sees letters. A tokenizer cuts text into pieces from a fixed
vocabulary (here 151,665 pieces) and the model sees only their numbers.
Measured (`␣` marks a leading space):

| Text | Characters | Tokens |
|---|---|---|
| `The Pn wave arrived 33.5 s after the earthquake.` | 48 | 15: `The` `␣P` `n` `␣wave` `␣arrived` `␣` `3` `3` `.` `5` `␣s` `␣after` `␣the` `␣earthquake` `.` |
| `紀伊水道の地震の深さは 12 km だった。` | 22 | 17: `紀` `伊` `水` `道` `の` `地震` `の` `深` `さ` `は` `␣` `1` `2` `␣km` `␣` `だった` `。` |
| `p_picks.csv` | 11 | 4: `p` `_p` `icks` `.csv` |

What to notice:

- **33.5 is four tokens: 3 · 3 · . · 5.** The model never sees the number
  as a number, so arithmetic belongs in a tool (Python), not in the model.
- "Pn" is two tokens, ` P` + `n`: a domain term is not one unit.
- Japanese costs more tokens per character (17 for 22) than English (15
  for 48). Some kanji are even split into two byte-level pieces (in the
  probe, 観 of 観測所 became two unreadable pieces).
- Token counts matter twice: the context window is measured in tokens, and
  external AI services bill per token.

## 2. A model does one thing: score every possible next token (slide 13)

**[General pattern]** — the same for every model behind every harness. R4
reads no probabilities in its chat loop.

Given all tokens so far, the model gives every token in its vocabulary a
probability (they sum to 1). The harness picks one, appends it, and asks
again. Repeating this is "writing". Measured at temperature 1:

| Prompt | Top next tokens | Uncertainty |
|---|---|---|
| "Seismic waves refracted along the Moho and recorded far from the source are called head ___" | `␣waves` 0.87 · `waves` 0.06 · `␣and` 0.01 · `␣wave` 0.01 | 1.12 bits |
| 「地震波には P 波と S 波があり、先に到達するのは ___」 | `␣P` 0.70 · `␣S` 0.07 | 2.72 bits |

"Bits" is the entropy of the distribution: 0 means certain; 17.2 means
every one of the 151,665 tokens is equally likely. Physics the model saw
many times in training gives a sharp distribution.

## 3. Temperature: how boldly the model picks (slide 13)

**[General · R4 settings]** — R4 sends **T = 0.4** by default
(`_DEFAULT_TEMPERATURE`, `companion/agent.py:248`);
`R4_COMPANION_TEMPERATURE` (0–2) overrides it (`agent.py:251-264`), and
sub-agents use the same value. `top_p` is sent only when
`R4_COMPANION_TOP_P` is set. Elsewhere: the OpenAI Agents SDK sets it in
`ModelSettings(temperature=…)`, LangChain on the chat-model constructor;
Claude Code does not expose it.

Before picking, the probabilities are reshaped by a temperature T
(p_i ∝ exp(z_i / T)). For the "head ___" prompt, the probability of
` waves` was **1.00 at T = 0.2, 0.87 at T = 1.0 and 0.38 at T = 1.5**. Low T
almost always takes the top token — repeatable, good for analysis. High T
spreads the choice — good for brainstorming, not for numbers. For
repeatable analysis, start the console with `R4_COMPANION_TEMPERATURE=0 r4`.
Same prompt, different answer? Often that is just sampling. **Temperature
never adds knowledge**: it only sharpens or flattens what is already there.

## 4. When the model does not know, it still picks a token (slide 14)

**[General pattern]** — model behaviour. Harnesses differ only in the
counter-measures (retrieval, citations, checks); R4's are RAG and the
evidence rules in its base prompt (`agent.py:545-592`).

The invented observatory's Moho depth (28 km, in the knowledge pack) is in
no training data. Measured, temperature 1:

| Prompt | Top next tokens | Uncertainty |
|---|---|---|
| "Our observatory's reference model puts the Moho under the Kii Channel at a depth of ___" | `1` 0.26 · `3` 0.16 · `2` 0.14 · `4` 0.11 · `7` 0.07 · `5` 0.07 | 3.03 bits |
| same, with the KRSO-1D v2 passage placed before it | **`2` 0.54** · `1` 0.16 · `3` 0.14 · `4` 0.06 | 2.08 bits |
| 「私たちの観測所の参照モデルでは、紀伊水道の下のモホ面の深さは ___」 | `1` 0.26 · `2` 0.18 · `3` 0.14 | 3.38 bits |
| same, with the Japanese passage | `2` 0.27 · `1` 0.24 · `3` 0.13 | 2.93 bits |

Without the passage the probability spreads over digits. At a low
temperature (0.2) the model would still say "1…" with 87 % — fluent,
confident and invented. **That is a "hallucination": not a bug, but the
normal mechanism with nothing to go on.** Fluency is not knowledge.

With the passage in front of it, the right first digit "2" wins: 54 % at
T = 1, 99.7 % at T = 0.2 in English. In Japanese the small model is far less
sure (27 % vs 24 % at T = 1; 64 % vs 34 % at T = 0.2). **RAG moves the odds;
it does not make the model certain** — which is why you check the cited
source. The 7B model in the labs, with R4's instructions, does better;
Lab 1.4 lets you measure that yourself.

## 5. Where the knowledge lives: training vs inference (slide 15)

**[General pattern]** — true of every hosted or local model. The only R4
choice here is the stand-alone stack's model pick by memory size
(`deploy/standalone/lib/model.sh:7-10`).

**Training** happens once, at the model developer: trillions of tokens of
public text adjust billions of numbers (parameters), and it ends at a
knowledge cutoff. **Inference** happens at every question, on your laptop:
the parameters are read, **never changed**. Your chat does not teach the
model; new knowledge must come in through the context.

| Model (the stack picks by RAM) | Parameters | 16-bit | 4-bit (download) |
|---|---|---|---|
| `llama3.2:3b` | 3 B | ≈ 6 GB | ≈ 2 GB |
| `qwen2.5:7b` | 7 B | ≈ 14 GB | ≈ 4.5 GB |
| `qwen2.5:14b` | 14 B | ≈ 28 GB | ≈ 9 GB |
| `qwen3:30b` (MoE: ≈ 3 B active per token) | 30 B | ≈ 60 GB | ≈ 18 GB |

Memory ≈ parameters × bytes per parameter (weights only; the context needs
more). Quantizing to 4 bits is what lets a 7-billion-parameter model fit on
a laptop, at some cost in accuracy. A mixture-of-experts (MoE) model stores
all 30 B parameters but uses only about 3 B for each token, so it runs
faster than its size suggests — it still needs the memory for all of them.

## 6. The context window: the model's only working memory (slide 17)

**[General pattern]** — every chat-completions harness re-sends the
conversation on each call (Claude Code, the OpenAI Agents SDK's `Runner`,
opencode, LangChain). Server-side state hides the re-send; it does not
remove it.

Everything the model knows about your session must fit in one request: the
system prompt, your `R4.md` rules, the tool definitions, retrieved
passages, the conversation so far and tool results. The local stack's
window is **32,768 tokens** (`OLLAMA_CONTEXT_LENGTH`,
`deploy/standalone/env/base.env:36`). R4 plans with a smaller **budget**:
window × 0.90 − 4,096 = **25,395 tokens** on 32k
(`companion/context_budget.py:17-18, 80-85`).

- The model is **stateless**: it remembers nothing between calls. Every
  turn, the harness re-sends everything.
- What is not in the window does not exist for the model.
- A 50 MB CSV does not fit: ask the agent to compute, not to read it all.
- The window is also exactly what an external AI service would see.

`/context` in the console prints the real counts for your session.

> **Note:** R4 takes the window from what the server serves, not from its
> model-name table (which says 131,072 for `llama3.2` and `qwen3`,
> `r4/src/r4/model_context.py:73,95`). Through the stand-alone stack the
> endpoint publishes 32,768 (`resolve_model_context_limit`,
> `companion/context_budget.py`); a model R4 reaches directly on an Ollama
> server gets min(trained window, served window) from Ollama's `/api/ps` and
> `/api/show`, or 32,768 when Ollama does not say
> (`companion/ollama_window.py`). `R4_MODEL_CONTEXT_WINDOW` overrides both.
> `/context` shows the window and the budget in use. (2026-10-30: this
> replaces a "known gap" note; the fix is the Ollama step.)

## 7. From text to action: tool calling (slide 18)

**[General pattern]** — Claude `tool_use` / `tool_result`, OpenAI function
calling (`@function_tool` in the Agents SDK), opencode tools, LangChain
`bind_tools`: the model asks, the harness alone executes.

The model **never runs anything**. It writes JSON naming a tool; the
harness decides whether to run it, runs it, and shows the model the result.
The real shapes (OpenAI-compatible `/v1/chat/completions`; agent rounds are
sent as a stream and reassembled, `client.py:1426-1450`; content
shortened):

```text
1  harness → model
POST /v1/chat/completions
{"model": "qwen2.5:7b", "temperature": 0.4,
 "messages": [{"role": "system", ...},
   {"role": "user", "content": "How many stations are in p_picks.csv, and which is farthest?"}],
 "tools": [{"type": "function", "function": {
   "name": "python_exec",
   "parameters": {"properties": {"code": {"type": "string"},
     "timeout": {"type": "integer", "default": 180}},
     "required": ["code"]}}}, ...]}

2  model → harness: a request, not an action
{"role": "assistant", "tool_calls": [{"id": "call_1",
  "function": {"name": "python_exec",
   "arguments": "{\"code\": \"df = pd.read_csv(...); ...\"}"}}]}

3  harness runs it, appends the result, calls the model again
{"role": "tool", "tool_call_id": "call_1",
 "content": "25 stations; farthest JHT 267.6 km"}
```

Tool "definitions" are just more text in the request (the `python_exec`
schema: `companion/tools.py:1731-1746`). One assistant message with
`tool_calls` is followed by one `role: tool` message per call
(`_record_tool_round`, `companion/agent.py:3075`).

What R4 adds around this:

- **[General · R4 settings] Text fallback.** For models without native tool
  calling, R4 reads tool-call tags out of the reply text —
  `<tool_call>{json}</tool_call>`, `<invoke name=…>` and Kimi's dialects —
  in this order: native `tool_calls` → `<tool_calls_section_begin>` → Kimi
  `<|open|>` → `<tool_call>` / `<invoke>` (`_extract_tool_calls`,
  `companion/agent.py:4991-5013`). The idea goes back to ReAct (prompting a
  model to write its thought and action as text); today vLLM and Ollama
  usually do this parsing on the server, and Claude Code and the OpenAI
  Agents SDK accept native tool calls only. R4 does it in the client so
  that any served model works.
- **[R4-specific] Argument repair and null dropping.** Broken JSON is
  repaired before it is rejected (control characters, containers sent as
  strings, trailing text; `_repair_tool_arguments`, `agent.py:1721-1751`).
  An explicit `null` for an optional argument is dropped so the tool's
  default applies (`_without_null_optionals`, `agent.py:980-990`;
  `llama3.2:3b` broke `file_read` this way). An unrepairable call or an
  unknown tool comes back to the model as an error before the permission
  gate (`agent.py:5204-5221`) — never guessed.

## 8. The agent loop, as R4 implements it (slide 19)

**[General pattern]** for the core: call the model, run the tools it asks
for, append the results, repeat until it answers. Claude Code / the Claude
Agent SDK (the query loop), the OpenAI Agents SDK (`Runner.run`), opencode
(its session loop) and LangGraph (`create_react_agent`) all have this core.

Simplified from `_run_agentic_turn` (`companion/agent.py:3131-3241`):

```python
# r4/src/r4/companion/agent.py — simplified
messages = [system, *history, user]  # rules, past turns, your request
for round in range(1000):            # hard roof
    messages += finished_background_agents()
    fit(messages)                    # old rounds shortened
    reply = model(messages, tools)   # one streamed HTTP call
    calls = reply.tool_calls or parse_tags(reply.text)
    if not calls:
        if empty or "Let me …:": nudge; continue  # ≤2 / ≤3
        if cut at output limit: raise cap, continue
        return reply.text            # plain answer: done
    for call in calls:               # hook → gate → run
        messages += tool_message(call, run(call))
    if called("final_output"): return its text
    guards()                         # loop / failure checks
    checkpoint_if_no_new_evidence(every=25)
return wrap_up_round()               # one round, no tools
```

A **turn** is one request of yours; a **round** is one model call inside
it. Each line, with R4's numbers and how general it is:

| Line | What R4 does | Scope | Elsewhere |
|---|---|---|---|
| `range(1000)` + `wrap_up_round()` | `DEFAULT_TOTAL_TOOL_ROUNDS = 1000` (`agent.py:80`). At the roof, one more round **without tools** lets the model write a summary of what it found; a fixed digest is the fallback (`_wrap_up_summary`, `agent.py:3699-3720`). | General · R4 settings | OpenAI Agents SDK `max_turns` (default 10, then an exception); LangGraph `recursion_limit` (25, hard stop); opencode's step limit with a wrap-up prompt, which R4 copied |
| `finished_background_agents()` | A background agent that has finished lands as one user-role `<subagent-notification>` message at the next round; nothing is added while none has finished, so the cached prefix stays intact (G9, `agent.py:3242-3253`). At most 4 run at once (`async_agents.py:24`). | General · R4 settings | Claude Code's background agents and `<task-notification>` |
| `fit(messages)` | Old tool results are shortened inside the turn (§10). | General · R4 settings | Claude Code microcompact; Anthropic API context editing |
| `model(...)` | One streamed `/v1/chat/completions` call (`client.py:1426-1450`). Reasoning longer than 16,000 tokens or 600 s is closed by the harness (`agent.py:237, 277`). | R4-specific (the thinking budget) | Claude `budget_tokens` and OpenAI `reasoning_effort` are enforced by the provider instead |
| `parse_tags` | The text fallback (§7). | General · R4 settings | — |
| nudges | An **empty reply** gets a "continue" message, at most 2 times (`_EMPTY_REPLY_MAX_NUDGES`, `agent.py:141`); a reply that **announces a step** ("Let me …:") **but calls nothing** gets one, at most 3 times (`agent.py:143-150`). | R4-specific | Elsewhere a text-only reply simply ends the turn |
| output limit | `finish_reason = length`: first one retry with the output cap raised to max(2×, +8,192) tokens, up to 131,072 (G19a, `_escalate_output_budget`, `agent.py:3343-3372`); then up to 4 "continue" nudges that hand back the last 12,000 characters of the cut reasoning (`agent.py:110-128, 196-205`); then a named stop. | General · R4 settings | Claude Code raises its output cap once, then sends up to 3 "resume" messages |
| `for call in calls` | Each call: skill refusal → your `PreToolUse` hook → permission gate (§11) → run (`agent.py:5228-5245, 5315-5336`). Read-only calls in the same reply run in parallel, 8 workers by default (`R4_COMPANION_TOOL_CONCURRENCY`, `agent.py:5097-5117`). | General · R4 settings | Claude Code runs concurrency-safe tools in parallel the same way; OpenAI `parallel_tool_calls` |
| `final_output` | Calling `final_output` ends the turn; its text is the answer (`agent.py:997-1010`). | General · R4 settings | OpenAI Agents SDK: `final_output` / `output_type` ends `Runner.run` |
| `guards()` | The **same tool fails 4 times in a row** (`TOOL_FAILURE_STREAK_TRIP`, `agent.py:84`) → one corrective nudge, then **answer mode** (tools withdrawn for the rest of the turn). The **same call with the same result 3 times**, or the same failing result with drifting arguments 3 times → answer mode at once (`_is_repetitive_tool_loop`, `agent.py:1309-1320`; `_post_tool_round_mode_control`, `agent.py:3606-3647`). | General · R4 settings | opencode's "doom loop" check (the same call 3 times asks the user); Claude Code and the OpenAI Agents SDK rely on turn limits |
| `checkpoint_if_no_new_evidence(25)` | Every 25 rounds (`DEFAULT_SOFT_BLOCK_ROUNDS`, `agent.py:79`) the loop compares what it has found. New evidence → it simply continues. **No new evidence** → a checkpoint message asks the model to write what is established, what it tries next and why, and whether the approach still holds — and then the loop **continues** (`_block_boundary_control`, `agent.py:3666-3697`; message `agent.py:1046`). It is not a stop. | R4-specific | No equivalent: elsewhere the limits stop the run (LangGraph's 25 is a hard limit) |

**Unattended runs** (`R4_AUTONOMOUS=1`, used by benchmarks and scheduled
work; **[R4-specific]**): a tripped guard becomes a bounded nudge (at most
3) instead of answer mode; a same-call guard is switched on (the same name
and arguments 6 times in a row, or an A/B pair repeating, whatever the
results; `agent.py:855-905, 3649-3663`); the output-limit ladder allows 12
nudges; and one completion review checks the answer against the task
before the turn ends (H4, `agent.py:3454-3485`).

**Sub-agents** (**[General · R4 settings]**): `spawn_subagent` runs in the
same process and client with a fresh context, half the parent's block of
rounds (25 // 2 = 12) and the parent's model and temperature
(`agent.py:4498-4578`); the background form returns at once and reports
later (G9). Elsewhere: Claude Code's Task / Agent tool, OpenAI Agents SDK
hand-offs and agents-as-tools, opencode's task tool.

**Fallback model** (**[General · R4 settings]**, opt-in): with
`R4_COMPANION_FALLBACK_MODEL` set, a persistent provider failure switches
the model once per turn (G19b, `agent.py:4904-4918`). Elsewhere: Claude
Code `--fallback-model`, LangChain `with_fallbacks`.

## 9. What the harness sends, every turn (slide 17)

**Where the tool definitions land** (review 2026-11-05): R4 sends them in the
separate `tools` field of the request (≈ 10.5k tokens on the stand-alone
stack). The model server's chat template decides where they appear in the
text the model reads: Ollama's Qwen-family template writes them into the
system turn right after the system content, before the history, so slide 17
draws the window as system → tools → earlier turns → your message (+
`<turn_context>`) → this turn's tool rounds → room for the reply. Llama 3.2's
template puts them next to the last user message instead.

**[General · R4 settings]** — every harness sends system + messages +
tools in one request. R4's own choices: the order below (built for the
prefix cache of a self-hosted vLLM or Ollama server), and **no replay of
old tool traffic** between turns. Claude Code also puts changing state into
the user message (as `<system-reminder>` blocks) but replays tool calls and
results until it compacts; opencode keeps tool parts and prunes old
outputs.

In this order (`_build_messages`, `agent.py:4580-4607`; `_system_message`,
`agent.py:4609-4633`):

1. **system** — base prompt · workspace · skill catalog (one line per
   skill) · the instruction files · your HPC sites (only when you have two
   or more).
2. **history** — earlier questions and answers, **text only**; old tool
   calls and results are not re-sent (`session_turns.py:92-93`).
3. **user** — your message, plus a `<turn_context>` block at its **end**:
   active skill bodies, memory (key–value notes in
   `~/.config/r4/companion/memory.json`), project state, output of your
   `UserPromptSubmit` hook, and RAG passages `[S1] [S2] …` when the router is
   on (`session.py:1137-1151`; `turn_context.py`).
4. **tools** — the JSON schema of every tool, sorted by name, the **same
   array every turn** (`_tools_to_openai_schema`, `agent.py:620-634`).

**Why this order: the prefix cache.** A model server can skip recomputing
the beginning of a request it has seen before. So everything stable comes
first and stays byte-identical turn after turn: system and tools change
only on a session event (a tool you denied is pruned; a new MCP server; an
edited `R4.md`). Changing things ride at the end of your message. That is
also why an active skill's tool limit **refuses** calls instead of removing
schemas from the array (`session.py:970-997`): removing them would change
the prefix and throw the cache away. Elsewhere: Claude Code places cache
breakpoints for the Anthropic API; OpenAI caches long prefixes
automatically; vLLM has automatic prefix caching.

**Instruction files** (**[General · R4 settings]**): walking up from the
folder you start in, R4 loads **each** of `R4.md`, `AGENTS.md`,
`agent.md`, `AGENT.md`, `CLAUDE.md`, `OPENCODE.md` that it finds (the
nearest copy of each name, in that order), at most 4,000 characters per
file and 8,000 in total (`briefing.py:55-160`). They are read again at
every turn, so an edited `R4.md` applies at the next request; skills, by
contrast, are scanned once when the session starts (`session.py:355`).
Elsewhere: Claude Code reads `CLAUDE.md`, opencode and Codex read
`AGENTS.md`; the OpenAI Agents SDK and LangChain have no file convention.

## 10. Keeping the window from overflowing (slide 20)

**[General · R4 settings]** — trimming and summarising are standard in
every harness that runs long sessions; the thresholds below are R4's. The
last layer is **[R4-specific]**.

| Layer | What R4 does | Code |
|---|---|---|
| Counting tokens | characters ÷ a ratio that starts at 3.5 and is recalibrated after every reply from the server's own `usage.prompt_tokens` (moving average, weight 0.4, kept within 1.5–8 characters per token). No tokenizer call. Japanese (about 1–1.3 characters per token) is under-counted until the first reply. | `context.py:44-57, 268-300` |
| A tool prints a lot | python / shell output over 32,000 characters → first 20,000 + last 8,000; any result over 40,000 → head and tail; over 120,000 → saved to `.r4/outputs/`, a 2,000 + 1,000-character preview is sent | `tools.py:132-138`; `agent.py:73`; `agent/large_response.py:38-50` |
| Within one turn | the last round stays in full; once tool output passes 25 % of the window, older results → first and last 1,024 characters; rounds older than the latest 40 → 400 characters (moved in blocks of 10 so the cached prefix survives); all re-readable with `tool_result_fetch` | `agent.py:2044-2168, 5484-5489` |
| Between turns | when the next request (plus a 4,096-token reply reserve) would reach **80 %** of the budget: (1) **microcompact** — older messages over 4,000 characters keep their first ~1,333, the last 6 untouched, no model call; if that is enough, stop (G11). (2) Otherwise **compaction** — the model summarises all but the last 6 messages, on the turn's own cached prefix. 3 failed summaries in a row → a plain digest until the next success (G12 breaker). | `session_turns.py:119-152`; `context.py:131, 186-245, 533-560` |
| Still too big | trim extra context (memory, project state) → drop tool definitions that do not fit → keep only the newest history that fits, **with no fixed minimum** | `context.py:403, 738-780` |

**Worked example** (`qwen2.5:7b`, 32,768-token window): budget = 32,768 ×
0.90 − 4,096 = 25,395; compaction starts when prompt + 4,096 reaches 80 %
of the budget (20,316), that is, once your prompt passes about **16,200
tokens** — half the window.

Elsewhere: Claude Code auto-compacts near the limit (its breaker also
stops after 3 failures) and has `/compact`; opencode compacts on overflow;
LangChain has a summarisation middleware; Claude Code truncates long Bash
output too (about 30,000 characters). Dropping tool definitions to make a
request fit is unusual — other harnesses fail the request instead.

In practice: a summary can lose details, so restate key numbers in long
sessions, and ask for computation rather than "read the whole file".

Two "retries" are easy to mix up: a transient provider error is tried up to
3 times in all (`agent.py:4847-4852`, `R4_COMPANION_TRANSIENT_RETRIES`); a
context-overflow error trims the oldest quarter of the messages and retries
**once** (`agent.py:696-718, 4786-4794`). The slide shows only the
mechanisms; the counts are here.

## 11. Guardrails: a permission gate, and an opt-in sandbox (slide 21)

**[General pattern]** — every harness asks before a risky tool runs
(Claude Code permission prompts, opencode allow / ask / deny, the OpenAI
Agents SDK's tool approval, LangChain's human-in-the-loop middleware).
R4's rung order and several rungs are its own.

Each call passes, in this order: an active skill's tool limit (§12) → your
**`PreToolUse` hook**, if any (`~/.config/r4/hooks.json`; exit code 2
blocks, stderr is the reason; `user_hooks.py:1-30`, `agent.py:5233-5241`)
→ `PermissionManager.decide()`, which walks this ladder; **the first rung
that has an answer decides** (`companion/permissions.py`, the `_LADDER`
tuple and its `_rung_*` methods; ADR-104, 2026-10-31):

| # | Question | If yes | Scope | Rung |
|---|---|---|---|---|
| 1 | DENY mode? | block | General · R4 settings | `_rung_deny_mode` |
| 2 | Your rule file says deny? | block | General · R4 settings | `_rung_deny_rule`; `permission_rules.py` |
| 3 | A tool that always asks (knowledge-graph and research-loop decisions; in chat and plan also team plans and plan gates)? | ask you, every call | R4-specific | `_rung_never_auto` |
| 4 | Do the arguments copy ≥ 24 characters from a `search_docs` / RAG / `web_*` / MCP / lab result, or from data a command downloaded? (taint fence) | ask you (chat, plan; autopilot runs it and notes it) | R4-specific | `_rung_taint`; `r4-contracts/src/r4_contracts/taint.py`; `companion/taint_exec.py` |
| 5 | A shell command or path touching credentials or identity (`~/.ssh`, keys, tokens, force-push)? | ask you, every mode | R4-specific | `_rung_fence` |
| 6 | Your rule file says ask? | ask you | General · R4 settings | `_rung_ask_rule` |
| 7 | A write to a protected path (`.git/`, `.bashrc`, `.ssh/`, `.mcp.json`, `.r4/hooks.json`, the rule files …; `apply_patch` read from its patch)? | ask you (chat, plan; autopilot runs it and notes it) | General · R4 settings | `_rung_protected_path`; `permission_rules.dangerous_path_reason` |
| 8 | A template crew's `teamup_start` (its plan card asks)? | run | R4-specific | `_rung_plan_card` |
| 9 | Your rule file says allow? | run | General · R4 settings | `_rung_allow_rule` |
| 10 | A read-only tool (chat, plan or autopilot)? | run | General pattern | `_rung_safe` |
| 11 | You answered **a** for this tool in this session? | run | General · R4 settings | `_rung_session` |
| 12 | Autopilot? (any tool; with an organisation policy on, only tools on its allowlist) | run | General · R4 settings | `_rung_trust` |
| 13 | Otherwise — e.g. a write or a command not yet approved | ask you | General pattern | `decide()` default |

Rungs 1–7 are the floor no rule or answer gets past; autopilot (amendment 2,
below) passes only rungs 4 and 7 and the team-plan part of rung 3; only then do the
allowances (8–12) apply. The slide groups the 13 rungs into seven rows: the
floor (rows 1–4), "you already said yes" (allow rules and earlier
"always" answers, row 5), "the mode allows it" (row 6), otherwise ask
(row 7).

**Why your earlier "always" is not checked first** (review 2026-11-01):
remembered approvals are checked before R4 asks you again, but deliberately
after the floor, so an old "always" can never let a call built from
downloaded text, or one touching credentials, through (`permissions.py`
docstring at `_LADDER`). There is no per-call approval cache any more (the
1,800-second one had no caller and was removed, `_remembered_approval_ttl`).

- **One mode** (`companion/agent_mode.py`): **chat** (the default: read-only
  tools such as `file_read`, `list_dir`, `search_docs` run, the rest ask),
  **plan** (read-only tools only, a plan first) and **autopilot** (runs
  everything; it asks only for credential access and force-push, knowledge
  decisions, your `ask` rules, and a delete it cannot back up first — a delete
  nobody approved is copied to `~/.local/share/r4/trash` unless git holds the
  file committed (ADR-104 amendment 2, `companion/delete_guard.py`); an
  organisation can narrow it to its server
  allowlist with `R4_AUTOPILOT_SERVER_POLICY=1`, ADR-035, and the badge then
  reads `autopilot(policy)`). *(2026-11-03, ADR-104 amendment 1: autopilot
  used to come before rungs 10–11 and, signed in, asked the server for every
  tool, so `autopilot(server)` asked even for `file_read`.)*
  **Shift+Tab** cycles them; the permission mode (`AUTO_SAFE` / `TRUST`)
  follows from the mode and is never set on its own. Elsewhere: Claude Code
  has default / acceptEdits / plan / bypassPermissions; opencode has a build
  and a plan agent.
- **Rule files** (G14): `~/.config/r4/permissions.json` and
  `~/.config/r4/projects/<id>/permissions.json` honour `allow`, `ask` and
  `deny`; a project's checked-in `.r4/permissions.json` only `ask` and
  `deny` — a file in a repository cannot grant itself permission. Entries
  name a tool (wildcards: `mcp.*`) or a command prefix, e.g.
  `"allow": ["shell_exec(prefix:pytest)"]`. Every rule records its source.
  Claude Code's `settings.json` works the same way, except that its project
  file may allow.
- **Your answer at the prompt:** **y** this call · **a** this tool for the
  rest of the session (`approve_for_session`, kept by the companion) · **n**
  no — the agent is told it was your no, and the tool stays
  available (`agent.py` `_denied_call`: only a deny that holds for the whole
  tool hides it). The companion says which answers each card offers
  (`companion/approval_answers.py`); the console never answers by itself.
  **a** is not offered for the always-ask tools (rung 3). There is no
  "always for this project" answer (removed in ADR-104 amendment 1): one
  keypress wrote a durable rule that ignored the arguments — **p** on
  `shell_exec` allowed every command; standing rules go in a rule file.
- **[R4-specific] Fenced asks** (rungs 4–7): the card shows `fenced:
  <reason> — this call only` and offers only **y** / **n**; no earlier answer
  and no rule answers it, and an "always" sent for it counts as once — no
  session grant, no saved trust (`permissions.py`
  `_require_fenced_approval`, `approval_answers.normalize_decision`).
  Still open: an ask the decision model adds (below) is not fenced.
- **Why rung 4 exists:** text from documents, MCP servers and the web can
  carry hidden instructions (prompt injection), so a call built from it
  always asks. No comparable harness tracks where arguments came from.
  The results of `mcp.<server>.<tool>` calls count as MCP output, and a
  `shell_exec` / `python_exec` call that reads the network (curl, wget,
  `git clone`, urllib, requests …) counts as web content, together with the
  files it saved: a later call that reads such a file yields web content too
  (`r4-contracts/src/r4_contracts/taint.py:54-70`,
  `companion/taint.py:131-305`, `companion/taint_exec.py`). *(Fixed
  2026-10-30: before, downloads and `mcp.*` results were not
  tracked.)* The fence only asks; whether the data are right is still
  yours to check.
- It **fails closed**: with no approver, the answer is "denied".
- **Optional, [R4-specific]: the decision model.** A small scoring model
  (Bespoke-Nimble-9B) can be asked fixed questions at fixed points; it can
  only turn an automatic approval into a question, never remove one
  (`decisions/harness.py`; ADR-076). Claude Code's auto mode uses a
  classifier the other way round, to approve.

**OS sandbox: opt-in** (**[General · R4 settings]**, ADR-101, 2026-10-30):
`/sandbox on` in the console (or `r4 --sandbox on`, `R4_SANDBOX=on`,
`r4 config sandbox on`) runs `shell_exec`, `python_exec`, the code tools,
local stdio MCP servers, hatch runtimes and project hooks under bubblewrap
(Linux) or Seatbelt (macOS). Inside it, writes are allowed only in the
workspace and a per-session temp folder; `~/.ssh`, `~/.aws`, the r4 config
folder and similar are unreadable; the network goes only through r4's
proxy, which follows `/web`. If the sandbox cannot start, the call fails
closed (`companion/sandbox/`). **It is off by default**, and every session
opens with a notice saying so: then `python_exec` and `shell_exec` run as
normal processes with your rights (`tools.py:599-632`), and approving a call
is running it yourself. opencode, LangChain and the OpenAI Agents SDK have
no sandbox; Claude Code offers an optional one and Codex sandboxes by
default.

## 12. Skills and MCP: knowledge and tools on demand (slides 23–24)

**Skills** — **[General pattern]**: progressive disclosure, as in Claude
Code / the Agent SDK's Agent Skills and opencode. Each turn the system
prompt carries only one catalog line per skill (at most 40 lines × 120
characters, `skills.py:376-395`), e.g. "residual-report — Writes a P-wave
travel-time residual report under the tutorial observatory's rules …". The
full `SKILL.md` body enters only when the skill is active, in
`<turn_context>`.

What R4 adds:

- **[R4-specific] Keyword activation.** A skill's header lists keywords
  (`x-r4-keywords`); at the start of a turn the harness scans **your
  message** for them as whole words (a plural `s` / `es` also matches; no
  substring hits; `skills.py:159-177, 312-344`; `session_flow.py:30-53`).
  A hit puts the body into **this** turn; skills it depends on come along.
  If instead the model calls `activate_skill`, the body arrives from the
  **next** turn (`session.py:375-394`). Japanese keywords work too (the word
  boundary only looks at Latin letters and digits), so a skill needs its
  own Japanese keywords for Japanese prompts. Elsewhere (docs checked
  2026-11-05): in Claude Code, opencode and Codex the model decides from the
  description, or you invoke the skill by name (`/skill-name`, `$name`);
  none documents a keyword scan (Claude Code auto-loads a few skills by file
  path or import only). Which way works better has **not been measured**:
  keywords are predictable but literal (a synonym or another language
  misses); the model's choice handles paraphrases but only when the model
  notices — write descriptions that say when to use the skill.
- **[R4-specific] Skill tool limits.** An active skill's `allowed-tools`
  narrow the domain tools: others are refused before any approval. Your
  shell, python, file and patch tools are never refused
  (`skills.py:187-221`), and the tools array itself does not change (§9).
  Claude Code's `allowed-tools` works the other way: it lets the listed
  tools run without asking.
- **[General · R4 settings] `tool_search`.** Need a tool the skill does not
  list? The model calls `tool_search`; the top 5 matches (whole-word
  ranking, stopwords ignored) become callable from the next turn
  (`tool_search.py:20-35`; `session.py:985-995`). Claude Code's ToolSearch
  defers MCP tool schemas instead; R4 always sends the schemas and uses the
  search only to lift refusals.

**MCP** (Model Context Protocol) — **[General · R4 settings]**: the shared
standard for plugging outside tools into an agent (Claude Code, the OpenAI
Agents SDK, opencode and LangChain all speak it). R4's difference is where
the registry lives: on UMRS, not in a local `.mcp.json`. Each server tool
becomes `mcp.<server>.<tool>` with its input schema and joins the tools
array (`mcp_first_class.py:126-205`); calls go through the UMRS gateway at
`/gw/mcp/invoke` (`mcp.py:518-555`). Hatch is R4's mechanism that packages
and starts MCP servers.

Slide 24 walks one call in the same four steps as tool calling: ① the
server's tools sit in the tool list (`mcp.platform-tools.list_jobs`,
description `[first-class MCP] …`, the server's own input schema); ② the
model writes an ordinary tool call with that name; ③ after the gate, the
harness POSTs `{"tool": "list_jobs", "arguments": {...}}` to the gateway,
UMRS finds the server in its registry and the server runs it; ④ the result
returns as a `role: tool` message wrapped in `[TOOL_OUTPUT_START]` …
`[TOOL_OUTPUT_END]` and counts as MCP output for the taint fence.

A successful call returns the tool's output scanned for injected
instructions and wrapped in boundary markers (`MCPToolResult.to_message()`,
`mcp.py:136-173`); a failed one returns `Tool error: <error>`
(`mcp_first_class.py:84-95`). *(Fixed 2026-10-30: before,
the handler read fields the result does not have, so every agent call of
an MCP tool returned an error.)*

## 13. Embeddings: meaning as 768 numbers (slide 27)

**[General pattern]** — the standard building block of retrieval (OpenAI
`file_search`, LangChain vector stores). Claude Code has no built-in
embedding search; it searches files with grep and glob.

An embedding model turns any text into a vector — `e5-base-v2` gives 768
numbers. Similar meaning → vectors pointing the same way (cosine
similarity near 1). Directions carry meaning too: the classic word-vector
example is **king − man + woman ≈ queen** (word2vec; Mikolov et al., 2013),
shown on the slide as an illustration, not measured with e5. Slide 27 also
scores one question ("How deep is the Moho under the Kii Channel?") against
four sentences with `e5-base-v2`: the answer 0.81, the same fact in other
words 0.76, the same topic but another question 0.68, an unrelated
sentence 0.64. On the Japanese versions the English-only `e5-base-v2`
ranks the unrelated sentence second (0.80), while `multilingual-e5-base`
gets the order right (0.86 / 0.76 / 0.75 / 0.70). UMRS adds the
`query:` / `passage:` prefixes the model
expects (`umrs/src/umrs/shared/utils.py:36-63`) and pgvector ranks by cosine
distance (`dense.py:157-165`).

Measured for the Lab 1.4 question over the English knowledge pack, one
chunk per section (17 chunks):

| Rank | Score | Section |
|---|---|---|
| 1 | 0.871 | 01 · History (v2 moved the Moho to 28 km, mantle 7.9 km/s) |
| 2 | 0.858 | 03 · What we found |
| 3 | 0.856 | 05 · Reference models |
| 4 | 0.855 | 02 · Acceptance rules (PQ-3) |
| 6 | 0.848 | 01 · The model (the table) |

The scores bunch together (all 17 lie between 0.77 and 0.87): the
**ranking** matters, not the value. `e5-base-v2` is English-only. On the
Japanese pack it ranks the rule section 採用の規則 (PQ-3) 15th of 17;
`multilingual-e5-base` ranks it 3rd, with 経緯 first.

## 14. RAG end to end (slides 26, 28–29)

**Why RAG** (slide 26): the model knows only its training text, never your
observatory's model; retraining is slow and still cites nothing. RAG
(retrieval-augmented generation) changes **what the model reads**, not the
model: search your documents → put the passages in the context with labels
→ answer from them and cite the label, which you then check.

**[General · R4 settings]** for the pipeline — the generic RAG pattern
(LangChain's ensemble retriever also fuses with RRF; OpenAI `file_search`
is hosted hybrid search) with R4's numbers. **[R4-specific]**: the
knowledge lives per project on UMRS, and the router below.

**Ingest** is a durable job on the server, so it survives interruptions
(`companion/knowledge_tools.py:139-230`): upload → chunk (scheme
`fixed-v0`, 256-token chunks) → embed (`e5-base-v2`) → index, plus a
knowledge-graph step in which the local model extracts terms and
relations.

**Query** (`umrs/src/umrs/knowledge/retrieval/pipeline.py:3-21`): encode
the question → dense lane (cosine, meaning) → lexical lane (exact words and
identifiers such as `PQ-3` or `KRSO-1D`, BM25-style) → fuse the two
rankings (RRF, reciprocal rank fusion, k = 60) → fetch the text → rerank
(off by default) → cut to the top 4–6, depending on the reader profile.
**No chat model is involved in retrieval.**

**In R4** (`companion/retrieval_router.py`), two routes:

- **Agent-decided** — `R4_RAG_ROUTER=off`, **the default**: the agent calls
  `search_docs` as a tool when it decides to; results come back as
  ordinary tool results labelled `[label] <file> › <section>`
  (`tools.py:2560-2575`).
- **Automatic** — `R4_RAG_ROUTER=on`: before round 1, on most turns (not
  slash commands, short acknowledgements or code-intent messages), the
  harness retrieves and puts passages labelled `[S1]`, `[S2]` … into your
  turn. The pack size follows the reading model's profile: **4 passages /
  2,000 tokens** for `qwen2.5:7b`, `qwen2.5:14b` and `qwen3:30b`,
  **3 / 1,200** for `llama3.2:3b`, and never more than 12 % of the context
  budget (`context_budget.py:25, 51-77`; at 32k that ceiling is 3,047
  tokens and does not bind). Passages flagged as prompt injection are
  dropped.

Elsewhere: LangChain's two-step RAG chains always retrieve; OpenAI
`file_search` and Claude Code's own search are decided by the model. A
per-turn router with both routes is R4's.

**The `[S1]` labels are R4's own format** (ADR-093 D-4), not an OpenAI
convention: the pack is a `<passages>` block of `[S1] source › section`
entries followed by "cite passages as [S1]" and the question
(`r4-contracts/src/r4_contracts/context_pack.py`); numbering continues across
a session (`evidence_ledger.py`). OpenAI's Chat Completions API has no
citation convention; its Responses API `file_search` returns
`file_citation` annotations, and Anthropic's API has document citations —
structured fields, not inline labels.

**Does it help? (slide 29)** — measured on the same 3B model with exactly
these two plain-text prompts (`next_token_probe.py`, `lab_en` /
`lab_rag_en`):

- A: `Our observatory's reference model puts the Moho under the Kii Channel
  at a depth of `
- B: `KRSO-1D v2 (Kinki Regional Seismic Observatory reference model): upper
  crust 0-18 km, 5.9 km/s; lower crust 18-28 km, 6.7 km/s; mantle below the
  Moho at 28 km, 7.9 km/s.` + a blank line + prompt A

| | A · question only | B · with the passage |
|---|---|---|
| most likely answer | 100 km | **28 km** |
| P(exactly "28 km"), temperature 0.4 (R4's default) | 0.5 % | **75 %** |
| P(exactly "28 km"), temperature 1 | 0.9 % | 19 % |

With the Japanese prompts: 100 km → 28 km; 0.1 % → 42 % at 0.4. "Exactly
28 km" = p("2") × p("8" | "2") × p(no further digit), from
`next_token_probe.json` (`answer_28`). RAG moves the odds the right way; it
does not guarantee the answer — read the cited source.

**Who handles `[S1]`?** R4 writes the label in front of each passage and
asks the model to cite it; the model only copies the label as text; after
every answer R4's `citation_check` (no model call) lists the sources and
flags a label that was never retrieved or a number missing from the cited
passage, and the console shows the sources as chips. `R4_RAG_VERIFY=strict`
turns an unsupported answer into "NOT ENOUGH INFO"; there is no automatic
retry.

**Can RAG guarantee the answer? (slide 30)** No. Adding "Answer from the
reference above only, and copy its numbers exactly" (prompt C) did not
help: P(exactly "28 km") at 0.4 was 73 % in English (75 % without it) and
30 % in Japanese (42 % without), where the most likely answer became
**18 km**, the other number in the passage. Reliability comes from
checking: open the cited source, ask for the quoted sentence next to the
number, let the agent read values from the file with a tool or test the
result; better retrieval, a larger model and a low temperature only raise
the odds.

## 15. See it yourself in the console

| Section | In the console (`r4`) |
|---|---|
| 1, 6, 10 — tokens and the window | `/context` (totals), `/cost` (turns, prompt and completion tokens) |
| 2–4 — the model | `/models` (which model answers); compare the same question asked twice |
| 7–8 — tool calls and the loop | **Ctrl+O** (verbose trace), **F9** Activity tray (Agent / Tools / Events) |
| 11 — the permission gate | the **y / a / n** prompt; **Shift+Tab** cycles the mode; `/settings rules` |
| 12 — skills, MCP | `/skills`, `/skills on <name>`; `/mcp`, `/mcp tools` |
| 13–14 — knowledge | `/rag docs`, `/knowledge <query>` (raw snippets), `/rag <question>` (answer with sources); the router only by relaunching with `R4_RAG_ROUTER=on r4` |

## 16. Where each piece lives in R4

| Piece | File (`r4/src/r4/…`) |
|---|---|
| agent loop | `companion/agent.py` — `_run_agentic_turn` |
| model call (OpenAI-compatible) | `client.py` |
| tools (`python_exec`, `file_read` …) | `companion/tools.py` |
| instruction files (`R4.md` …) | `briefing.py` |
| context budget, compaction | `companion/context.py`, `companion/context_budget.py` |
| compaction flow between turns | `companion/session_turns.py` |
| per-turn block (`<turn_context>`) | `companion/turn_context.py` |
| large tool output spill | `agent/large_response.py` |
| permission gate | `companion/permissions.py` |
| permission rule files, protected paths (G14) | `companion/permission_rules.py` |
| user hooks (G13) | `companion/user_hooks.py` |
| skills | `companion/skills.py`, `companion/builtin/` |
| `tool_search` (G18) | `companion/tool_search.py` |
| background agents (G9) | `companion/async_agents.py` |
| build contracts and the fresh-context reviewer (G15 / G16) | `companion/build_contract.py`, `companion/sprint_review.py` |
| MCP tools | `companion/mcp_first_class.py`, `mcp.py` |
| RAG router, knowledge tools | `companion/retrieval_router.py`, `companion/knowledge_tools.py` |
| retrieval (server) | `umrs/src/umrs/knowledge/retrieval/` |

Everything above is ordinary, readable Python. A harness is engineering,
not magic, and you can change it. Homework for the curious: read
`_run_agentic_turn` and `PermissionManager.check()`.

## 17. General or R4-specific, at a glance

| General pattern (same idea in Claude Code, OpenAI Agents SDK, opencode, LangChain) | General, with R4's own settings | R4-specific |
|---|---|---|
| tokens, probabilities, hallucination, parameters | temperature 0.4 | 25-round checkpoint on no new evidence |
| context window, stateless model | context order for the prefix cache; no tool replay between turns | stalled-reply nudges (≤ 2 / ≤ 3) |
| tool calling | text-tag fallback; 1,000 rounds + wrap-up | argument repair, null dropping |
| the agent loop | loop guards 4 / 3; output ladder up to 131,072 | client-side thinking budget |
| permission gate; opt-in OS sandbox, off by default | 8 parallel workers; sub-agents (half budget, ≤ 4 in background) | hard fit that drops tool definitions |
| skills (progressive disclosure) | token ratio 3.5 + calibration; 32k / 40k / 120k caps; microcompact; 80 % compaction, keep 6, breaker 3 | always-ask tools; taint fence; credential fence |
| embeddings; session and trace commands | modes; rule files (a project cannot allow); protected paths; y / a / n; hooks (exit 2) | keyword skill activation; skill limits as refusals |
| agentic coding | instruction-file chain (4k / 8k); `tool_search` top 5; MCP via UMRS; RAG numbers | RAG router; project knowledge on UMRS; build contracts + reviewer; decision model |

## Recap: who does what

| Layer | Does | Cannot | You control it with |
|---|---|---|---|
| Model | scores next tokens | know your lab; run code; remember | choice of model, temperature |
| Agent loop | turns tool requests into actions, repeats | judge if the result is right | your goal + acceptance check |
| Harness | assembles context, gates tools, manages memory | make a weak model strong | `R4.md`, skills, permissions |
| Knowledge | puts the right passage in front of the model | guarantee the model uses it | your documents, RAG on/off |

R4's own answer to "the model grades its own work": unattended runs get one
completion review against the task (`agent.py:3454-3485`), and build
contracts get a fresh-context reviewer with read-and-run-only tools and a
hard-threshold rubric (`sprint_review.py`, G15). Interactively, that
reviewer is you: the acceptance check in your prompt (slide 42).
