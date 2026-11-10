# Appendix B — Environment Variables

Complete reference of environment variables by component.

## R4 Client

| Variable | Default | Description |
|----------|---------|-------------|
| `R4_GATEWAY_URL` | — | Gateway endpoint URL |
| `R4_API_KEY` | — | Authentication token |
| `R4_PROJECT_ID` | — | Default project ID |
| `R4_MODEL` | — | Default model name |
| `R4_DECISION_MODEL` | `Bespoke-Nimble-9B` (since 2026-10-03; used only where the platform serves it) | Decision model for `decision_evaluate` and the harness gates (`off` disables). Overrides `/decision-model` |
| `R4_TIMEOUT` | `300` | Request timeout (seconds). For agent rounds, which are streamed, this is the idle limit between chunks |
| `R4_STREAM_BLOCKING` | `1` | Agent-loop model calls are streamed from the server and assembled; `0` sends them as plain non-streaming POSTs |
| `R4_MAX_OUTPUT_TOKENS` | (unset) | Output limit sent with every agent round. Unset: the model's published limit, else 131,072 for reasoning models (glm-5, kimi-k2/k3, deepseek-r1, qwen3, gpt-oss…), else the server default; `0` never sends one. Clamped to the context window left after the prompt |
| `R4_MODEL_CONTEXT_WINDOW` | (unset) | Override the model's context window in tokens; it sets the compaction budget (window × 0.90 − 4,096) and the tool-history budget. Default: the endpoint's published window; for a model reached directly on an Ollama server, min(trained, served) from `/api/ps` and `/api/show`, else 32,768; else R4's model table. `/context` shows the window in use (2026-10-30) |
| `R4_MEMORY_DIR` | `~/.config/r4/memory` | Where saved memory lives (`user/` and `projects/<key>/`, one file per note); read at each call (2026-11-07) |
| `R4_MEMORY_INDEX_CHARS` | `6000` | Characters of the saved-memory index one turn carries; longer indexes end with a marker that says more exists (2026-11-07) |
| `R4_COMPANION_MEMORY_DIR` | `~/.config/r4/companion` | Folder of the old `memory.json`; since 2026-11-07 only read once, to copy its notes into the user scope of `R4_MEMORY_DIR` |
| `R4_COMPANION_LENGTH_RECOVERIES` | `4` (`12` with `R4_AUTONOMOUS=1`) | How many times per turn the agent continues after a reply is cut at the output limit before it stops |
| `R4_AUTONOMOUS` | (unset) | `1` marks an unattended run (the Terminal-Bench driver sets it): stuck-loop guards send a "change approach" nudge instead of ending on the checkpoint digest, and the output-limit and empty-reply recoveries are raised |
| `R4_AUTOPILOT_SERVER_POLICY` | (unset = off) | `1` consults the organisation allowlist at the session's project scope and shows `autopilot(policy)`. Since the 2026-11-09 autopilot policy update, a local policy prompt is auto-answered with an audit note rather than blocking autopilot; backend authorization still applies. Off, no local organisation-policy consult is made |
| `R4_COMPANION_REASONING_KEEP` | `2` | Earlier assistant rounds whose reasoning is replayed in full; older rounds get a short placeholder (`-1` = replay all) |
| `R4_COMPANION_REASONING_MAX_CHARS` | `24000` | Kept rounds replay only the last N chars of their reasoning, behind an "earlier reasoning omitted" marker (`0` = whole). A 116k-token reasoning replayed in full grew a TB 4.0 prompt from 15k to 131k tokens |
| `R4_COMPANION_THINK_BUDGET` | `16000` | Reasoning tokens (counted as 4 chars each) a model round may spend before any answer or tool call. Past it, the round is stopped and the agent closes the deliberation so the model acts on its plan. `0` = no budget. Turned off for the session if the model server refuses the closing request |
| `R4_COMPANION_THINK_BUDGET_SEC` | `600` | Seconds (from the request) a model round may deliberate before any answer or tool call; past it the deliberation is closed as with `R4_COMPANION_THINK_BUDGET`, whichever comes first. `0` = no time limit |
| `R4_COMPANION_TEXT_BUDGET` | `64000` with `R4_AUTONOMOUS=1`, else `0` | Chars of reply text a round may write before any tool call. Past it, the round stops and the agent asks for the next step's tool call. In tool mode, a text reply cut at the output limit is never taken as the final answer |
| `R4_COMPANION_TOOL_ARGS_BUDGET` | `100000` | Chars of tool-call arguments one model round may stream. Past it, or past a quarter of it when at least 80% of the lines are comments (planning written into code), the round is stopped before the call runs and the agent asks for a runnable step. `0` = no limit |
| `R4_COMPANION_HISTORY_FULL_ROUNDS` | `40` | Tool rounds kept at the normal trim; older rounds keep only `R4_COMPANION_OLD_ROUND_CHARS` of each result and argument string. The boundary moves in blocks of 10 rounds; `tool_result_fetch` re-reads a shortened result; `0` = off |
| `R4_COMPANION_OLD_ROUND_CHARS` | `400` | Chars kept per result and per argument string in rounds older than `R4_COMPANION_HISTORY_FULL_ROUNDS` |
| `R4_COMPANION_ARG_MAX_CHARS` | `2000` | Tool calls of earlier rounds (all but the latest) are replayed with each long argument string clipped to about this many chars, head and tail kept (`0` = replay in full). Whole files written through `shell_exec` heredocs or `file_write` were replayed verbatim on every request: 390k of a 510k-char TB 4.0 prompt |
| `R4_COMPANION_LENGTH_TAIL_CHARS` | `12000` | Chars of a cut-off reply's reasoning carried into the continuation nudge after `finish_reason=length` |
| `R4_TRANSCRIPT_PATH` | (unset) | Write a JSONL transcript of the session (tool calls, results, model rounds, nudges) to this file |
| `R4_TRANSCRIPT_DIR` | (unset) | As above, one `<session_id>.jsonl` per session in this directory |
| `R4_OIDC_ISSUER` | — | OIDC issuer URL |
| `R4_OIDC_CLIENT_ID` | — | OIDC client ID |

### R4 local tool sandbox

*Added 2026-10-30* (what the
sandbox does: [Appendix D](appendix-d-security.md#local-tool-sandbox-on-your-workstation)).

| Variable | Default | Description |
|----------|---------|-------------|
| `R4_SANDBOX` | (unset = off) | `on` / `off` (also `true`/`false`, `1`/`0`, `yes`/`no`): the local tool sandbox for a local session with no choice of its own. Order: `r4 --sandbox`, the session's own choice, this variable, the profile default (`r4 config sandbox`), off; a launch `off` never loosens a session that is already on. An `on` from here is saved on the session, so a later resume stays on |
| `R4_LAUNCH_SANDBOX` | (unset) | Set by `r4 --sandbox on\|off` for the console bridge, `r4 -p` and the REPL; the first reader takes it out of the environment, so nothing the agent starts inherits it. Not meant to be set by hand |
| `R4_WORKTREE` | (unset = the profile default, else `off`) | `auto` / `off`: in `auto` the agent moves the session into a git worktree (`.r4/worktrees/auto-<session>`) before its first write, and every later write stays inside it. Order: the session's `/worktree mode`, this variable, the profile default (`r4 config worktree`), off |
| `R4_SANDBOXED` | (unset) | Set inside every sandboxed process to the backend (`bwrap` or `seatbelt`). An `r4` started inside one reports the sandbox as `on (inherited)` and does not wrap again. Read-only for users: setting it by hand only makes that `r4` believe it is already sandboxed |

Inside the sandbox, names containing `TOKEN`, `SECRET` or `PASSWORD`, names
ending in `_API_KEY`, the AWS key names, `SSH_AUTH_SOCK`, `GPG_AGENT_INFO`,
`DISPLAY`, `XAUTHORITY` and the host's `HTTP(S)_PROXY`/`ALL_PROXY`/`NO_PROXY`
are removed; `R4_GATEWAY_URL` and `R4_GATEWAY_TOKEN` stay. To keep another
name, add it to `sandbox_policy.env_keep` under the profile in `config.json`
(`profiles.<name>.sandbox_policy`; see Appendix D).

### Web search backend

*Added 2026-10-30.*
The agent's `web_search` tool runs only in sessions whose web access is
Public, and needs a SearXNG instance. `r4 search-server up` starts one on
this machine (podman, else docker); `r4 search-server status` and `down`
check and remove it. Without an instance `web_search` is not offered, and
choosing Public with `/web` offers to start one (2026-11-01; `/search-server`
in the console does the same at any time). `web_fetch` needs none.

| Variable | Default | Description |
|----------|---------|-------------|
| `R4_WEB_SEARCH_URL` | (unset) | A SearXNG base URL with the JSON format enabled (`search.formats: [html, json]`); it wins over the local instance |
| `R4_SEARXNG_PORT` | `8888` | Loopback port of the local instance (`127.0.0.1` only) |
| `R4_SEARXNG_IMAGE` | `docker.io/searxng/searxng:2026.10.15-b00212a83` | Image of the local instance |
| `R4_CONTAINER_ENGINE` | (unset: podman, else docker) | Engine `r4 search-server` drives |

### R4 agent behaviour switches (TB 4.0 harness fixes)

*Added 2026-10-24, revised the same day after review* (plan items H1–H20).
Each harness fix has a switch, so a benchmark arm can measure it against the
build before the fix. The defaults are the product behaviour: leave them unset.
`R4_AUTONOMOUS=1` (above) turns on the unattended behaviours marked so below; it
never removes tools or skills. `R4_AGENT_PROFILE=terminal` is a benchmark
ablation, not a product mode. Every on/off switch reads `1`/`true`/`yes`/`on` as
on and `0`/`false`/`no`/`off` as off; any other value keeps the default. With
`R4_COMPANION_PROMPT_HYGIENE=0` and `R4_AUTONOMOUS` unset, the system prompt and
tool descriptions are the ones R4 sent before these fixes.

| Variable | Default | Description |
|----------|---------|-------------|
| `R4_COMPANION_PROMPT_HYGIENE` | on | Prompt fixes (H19, H15): the skill catalog is listed once (the `<project_state>` block and `project_status` leave out "Companion Skills" during a turn), bodies of platform skills stay out while no R4 platform is reachable (named on one "Skills not loaded" line), the `code` skill names the project's own build and test commands for verification, and `apply_patch` names the Codex envelope it accepts. Unattended runs (`R4_AUTONOMOUS=1`) also get "stay inside this root unless the task names another path" instead of "ask the user" on the workspace line, and are not offered `web_fetch` / `web_search` while the session's web access refuses them (`web_fetch` needs allowlist or public access, `web_search` public); interactive sessions keep both, so the tool's refusal still names `/web`. `0` restores the old prompt and tool descriptions (`apply_patch` still accepts the envelope) |
| `R4_AGENT_PROFILE` | (unset) | `terminal`: the lean benchmark profile (H2). A short core prompt instead of the companion prompt, only the tools `shell_exec`, `python_exec`, `file_read`, `file_write`, `file_edit`, `apply_patch`, `list_dir`, `grep_search`, `glob_search`, `todo_write` and `tool_result_fetch`, only the `code` and `polyglot_coding` skills, and no skill catalog, research spine, project state or workflow templates. It also caps shell output at 16,000 chars (head and tail, H11), keeps 24k tokens of tool history and clips earlier tool-call arguments to 600 chars after 3 rounds, and spills large results to `$TMPDIR/r4-outputs` (H16). Any other value: no change |
| `R4_TOOL_PYTHON` | (unset) | Interpreter for `python_exec`, `compile_code`, `run_code` and `test_code` (H1). Unset: when R4 runs from its own venv that the user did not activate (pipx, `uv tool`), the first usable (runs, Python 3.8 or newer) of the user's active environment (`VIRTUAL_ENV`, then `CONDA_PREFIX`), the workspace's `.venv` / `venv`, then `python3`/`python` on `PATH` outside R4's venv; else R4's interpreter. A path uses that interpreter; `sys` restores the old behaviour (R4's own interpreter) |
| `R4_SHELL_DETACH_ON_TIMEOUT` | on with `R4_AUTONOMOUS=1`, else off | `1`: a `shell_exec` command that outlives its timeout keeps running; the result names its pid and the files its output goes to (a private `$TMPDIR/r4-shell-*` directory), for a later `tail` or `kill` (H18). A `find` that hits its 25 s cap is still killed. Detached commands get SIGTERM when R4 exits (the TUI bridge also on SIGTERM/SIGHUP), and the directory is removed then unless one of them outlived it. The TUI honours the opt-in; the streamed shell of ACP clients never detaches. `0`: the timeout kills the process group, as before |
| `R4_COMPANION_COMPLETION_REVIEW` | on with `R4_AUTONOMOUS=1` | Unattended runs only: the first final answer of a task gets one review request that restates the task and asks to check each requirement against the actual files before finishing (H4). `0` turns it off; interactive sessions never get it |
| `R4_COMPANION_TIME_BUDGET_SEC` | (unset) | The task's time budget in seconds, quoted in the completion review with the elapsed time |
| `R4_COMPANION_SAME_CALL_GUARD` | on with `R4_AUTONOMOUS=1`, else off | The stuck-loop guard also fires when the same tool call (or an A/B pair) repeats 6 times in a row, whatever its results (H5); in an unattended run it asks the model to change approach. Polling commands (`sleep`, `tail`, `watch`, `wait`), scheduler and monitoring probes (`squeue`, `sacct`, `pjstat`, `qstat`, `nvidia-smi`, `git status`, `curl`/`wget` to localhost …), read-only probes and status-like tools are exempt. `0` turns it off; `1` turns it on in interactive sessions too, where a trip switches to answer mode |
| `R4_COMPANION_NUDGES_AS_USER` | on with `R4_AUTONOMOUS=1`, else off | Harness notes sent mid-turn (checkpoints, loop nudges) stay at their place in the conversation as user messages that start with `[harness note]`, merged into an adjacent user message so no two user messages follow each other, instead of being merged into the system prompt (H8). `0` restores the merge; `1` turns it on in interactive sessions too |
| `R4_COMPANION_TEMPERATURE` | `0.4` | Sampling temperature of agent rounds (H14), 0–2; an invalid value is ignored |
| `R4_COMPANION_TOP_P` | (unset: not sent) | `top_p` sent with agent rounds (H14), 0–1; an invalid value is ignored |
| `R4_ASK_USER_MODE` | `ask` | `auto`: `ask_user` returns an "auto-answered" note instead of waiting (the model picks the most reasonable option and says so). In the TUI bridge and remote sessions the r4 config's `ask_user_mode` wins over this variable; with no UI attached to answer (a headless run, the benchmark driver) only this variable counts (H12) |

### R4 retrieval

*Added 2026-10-10.* These variables control how R4 asks UMRS for passages and
what the console does with them. UMRS still decides relevance, the cut and
sufficiency; nothing here re-ranks or re-cuts passages.

| Variable | Default | Description |
|----------|---------|-------------|
| `R4_RAG_ROUTER` | `off` | Search before the model's first round and put UMRS's passages into the user turn. `off`: never. `auto`: only for the size classes in `R4_RAG_ROUTER_AUTO`. `on`: every model. Slash commands, acknowledgements, short messages, messages about files (code fences, paths, tracebacks) and turns without a project are never routed. Any other value means `off` |
| `R4_RAG_ROUTER_AUTO` | `small` | Comma list of the size classes `auto` routes: `small`, `medium`, `large`, `unknown`. Empty or no known class means `small` |
| `R4_RAG_PROFILE` | (unset) | Use the `small`, `medium` or `large` profile for every model, instead of the one picked from the detected scale. The profile sets the console's passage count and token cap (small 3 / 1,200, medium 4 / 2,000, large 10 / 8,000, never more than 12% of the context budget). It also sets the `profile` sent to UMRS. It does not change which classes `auto` routes |
| `R4_RAG_VERIFY` | `annotate` | Citation check of the answer when the router has shown passages: this turn received some, or the answer cites an `[S#]` label from an earlier turn. No model call is made. `annotate`: add a footer of at most 400 characters naming the sources and the check. `strict`: as `annotate`, and an answer whose cited sentences are all unsupported is downgraded to NOT ENOUGH INFO (a trailing note when streamed). `off`: no check |
| `R4_RAG_EXPAND` | (unset) | Query expansion before the routed search: `multi` (two extra queries, fused by UMRS), `hyde` and `llm_rewrite` (evaluation only), or `off`. Unset: `multi` for a large reader, none otherwise. One call to the session's model; if it fails, the search uses the question alone |
| `R4_RAG_RETRIEVE_TIMEOUT` | `10` | Seconds one `rag.retrieve()` or `rag.fetch_passage()` call may wait; never above `R4_TIMEOUT` |
| `R4_MODEL_SCALE` | (unset) | `small`, `medium` or `large`: treat every model in this process as this size class, before any `r4 model scale set` pin or probe |
| `R4_KNOWLEDGE_BUNDLE` | (unset) | Path to a `.kb` bundle, `.kb.tar.gz` archive or bundle directory. `client.rag` searches it offline instead of the platform. `r4 chat --knowledge` sets it for the console it launches |

## Knowledge / OCR

Read by `ResearchProject.__init__()` on the **client machine** at the start of
each `r4 project init` (or any direct `ResearchProject` instantiation).
All four must be set together; the feature stays off if any is missing.

| Variable | Default | Description |
|----------|---------|-------------|
| `KNOWLEDGE_OCR_ENABLED` | `""` (off) | Set to `1` / `true` / `yes` / `on` to enable OCR fallback for scanned PDFs |
| `KNOWLEDGE_OCR_BASE_URL` | — | OpenAI-compatible base URL for the vision model (e.g. `https://gw.example.com/gw/llm/v1`). Point at the Gateway `/gw/llm/v1` route — never at LiteLLM directly. |
| `KNOWLEDGE_OCR_MODEL` | — | Served model name exactly as shown in `r4 model list` (must accept image input). |
| `KNOWLEDGE_OCR_API_KEY` | — | Bearer token forwarded as `Authorization: Bearer <key>`. Usually the same key as `R4_API_KEY`. |

## vLLM

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_MODEL_PATH` | — | Pre-staged model path |
| `VLLM_MAX_MODEL_LEN` | `4096` | Fixed max context length |
| `VLLM_MAX_NUM_SEQS` | `4` | Max concurrent sequences |
| `VLLM_ENABLE_TOOL_CALLING` | `true` | Enable function calling |
| `VLLM_TENSOR_PARALLEL_SIZE` | `1` | GPU tensor parallelism |
| `VLLM_PIPELINE_PARALLEL_SIZE` | `1` | Pipeline parallelism |
| `VLLM_PORT` | `8000` | Service port |
| `TRANSFORMERS_OFFLINE` | `1` | Mandatory: no internet access |
| `HF_HUB_OFFLINE` | `1` | Mandatory: no HF Hub access |

## LiteLLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_MASTER_KEY` | — | Authentication key |
| `LITELLM_PORT` | `11434` | API port |

## UMRS LLM and chat proxies

Upstream limits of the OpenAI-compatible proxies `/umrs/llm/v1/*` (`LLM_PROXY_*`)
and `/umrs/chat/v1/*` (`CHAT_PROXY_*`), in seconds. Split on 2026-10-05 (W5)
so long reasoning turns are not cut:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROXY_CONNECT_TIMEOUT` / `CHAT_PROXY_CONNECT_TIMEOUT` | `10` | TCP connect to the upstream |
| `LLM_PROXY_IDLE_TIMEOUT` / `CHAT_PROXY_IDLE_TIMEOUT` | `600` | Longest silence between two chunks of a **streaming** reply. A stream that keeps producing chunks has no total limit |
| `LLM_PROXY_TOTAL_TIMEOUT` / `CHAT_PROXY_TOTAL_TIMEOUT` | `7200` | Overall deadline of one **non-streaming** request. When it is exceeded, the LLM proxy returns 504 `llm.gateway_timeout` and the chat proxy returns 502 `chatapi.bad_gateway` |
| `LLM_PROXY_READ_TIMEOUT` / `CHAT_PROXY_READ_TIMEOUT` | — | **Deprecated** (2026-10-05). Alias for the idle limit, used only when the `*_IDLE_TIMEOUT` name is unset. It no longer limits non-streaming requests |
| `LLM_PROXY_LIST_TIMEOUT` | `120` | Read limit for each endpoint while `GET /v1/models` builds the aggregate list |

## Gateway

| Variable | Default | Description |
|----------|---------|-------------|
| `GENAI_BASE_URL` | — | Backend LiteLLM URL (**legacy Python Gateway only** — not read by NGINX + auth-sidecar edge) |
| `UMRS_BASE_URL` | — | UMRS service URL |
| `RATE_LIMIT_RPS` | `5` | Requests per second limit |
| `AUDIT_LOG_DB` | — | Audit log database URL (**legacy Python Gateway only**) |

## NGINX

NGINX limits are set in the deployed `nginx.conf` (client_max_body_size,
proxy timeouts) — not via environment variables. Edit the config in
`deploy/` and redeploy via Ansible to change them.

On the gateway edge, these are set in `gateway/nginx/conf.d/server.conf`.
Rebuild the gateway image after changing this file.
Since 2026-10-05, `/gw/llm/` and `/gw/companion/` allow upstream reads of up
to `7200s`, matching the proxies' total limit; the other routes keep their own
limits (for example, `/gw/chat/` stays at `600s`). The Apache TLS front on 443
has its own `ProxyTimeout 600`, which is managed by the privileged play.

## Slurm

| Variable | Default | Description |
|----------|---------|-------------|
| `SLURM_PARTITION` | `gpu` | Slurm partition |
| `SLURM_TIME` | `02:00:00` | Job time limit |
| `SLURM_GPUS` | `1` | GPU count per job |
| `SLURM_CPUS` | `8` | CPU count per job |

## EgressGuard

| Variable | Default | Description |
|----------|---------|-------------|
| `EGRESS_GUARD_ENABLED` | `true` | Enable/disable guard |
| `EGRESS_PROXY_PORT` | `8888` | Proxy listen port |
| `EGRESS_DOMAIN_ALLOWLIST` | — | Comma-separated domains |
| `EGRESS_BLOCK_PATTERNS` | — | Regex patterns to block |
| `EGRESS_MAX_RESPONSE_MB` | `100` | Max response size |
| `EGRESS_RATE_LIMIT_RPM` | `60` | Rate limit (requests/min) |
| `EGRESS_AUDIT_LOG_PATH` | — | Audit log directory |
| `EGRESS_LOG_BODIES` | `false` | Log request/response bodies |

## MinIO / S3

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_ROOT_USER` | — | MinIO root username |
| `MINIO_ROOT_PASSWORD` | — | MinIO root password |
| `S3_ENDPOINT` | — | S3/MinIO endpoint URL |
| `S3_ACCESS_KEY` | — | S3 access key |
| `S3_SECRET_KEY` | — | S3 secret key |

## PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | — | Database password |
| `POSTGRES_DB` | `umrs` | Database name |
| `POSTGRES_USER` | `umrs` | Database user |
| `DATABASE_URL` | — | Full connection URL |

## Vision

| Variable | Default | Description |
|----------|---------|-------------|
| `VISION_REQUIRE_PRESIGNED_URLS` | `true` | Enforce presigned URL access |
| `VISION_ADAPTERS` | — | Comma-separated adapter names to enable |
| `VISION_ADAPTERS_DIR` | — | Directory containing adapter definitions |
| `VISION_PRESIGNED_URL_TTL_SEC` | — | TTL for presigned URLs |
| `VISION_S3_PRESIGN_ENDPOINT` | — | S3 endpoint used for presigned URLs |
| `VISION_STACK` | — | Vision stack type |

## UMRS Server

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_SITE_CONFIG` | — | Path to `site.yaml` (primary config file) |
| `UMRS_HTTP_HOST` | `0.0.0.0` | UMRS HTTP bind address |
| `UMRS_HTTP_PORT` | `8000` | UMRS HTTP port |
| `UMRS_DB_URL` | — | PostgreSQL connection URL |
| `UMRS_LOG_LEVEL` | `INFO` | Logging verbosity |
| `UMRS_LOG_FORMAT` | — | Log output format |
| `UMRS_AUTH_MODE` | — | Authentication mode |
| `UMRS_ADMIN_TOKEN` | — | Admin bearer token |
| `UMRS_PUBLIC_GATEWAY_URL` | unset | 2026-10-03: this server's public gateway base URL (e.g. `https://data0.ai.r-ccs.riken.jp/rccs`). `umrs-server admin token generate-jwt` and `user add --issue-token` embed it as the token's `gateway_url` claim, so `r4 auth login --api-key TOKEN` needs no other flag; `--gateway-url URL` overrides it, `--no-gateway-url` leaves it out. One rule for every source: `http(s)://HOST[:PORT][/PATH]` from letters, digits and `. _ ~ / -` only, trailing slash dropped; an env value outside it is ignored with a warning, an explicit `--gateway-url` outside it exits 2. On umrs-front the converge writes one `.env` line per site, `UMRS_<C>_PUBLIC_GATEWAY_URL` (from `umrs_front_<c>_gateway_url`), and compose maps each site's line into its own `umrs-server-<c>` under this name. The URL is per venue: the shared hub must pick it by the user's venue |
| `UMRS_TRANSPORT` | — | Transport backend (`zmq` or `http`) |
| `UMRS_TUNNEL_MODE` | — | When `true`, bind ZMQ to `127.0.0.1` only |
| `UMRS_MAX_CONCURRENT_JOBS` | — | Maximum simultaneous jobs |
| `UMRS_MAX_GPUS_PER_JOB` | — | GPU cap per job |
| `UMRS_MAX_WALLTIME_SEC` | — | Maximum job wall-time |
| `UMRS_REQUIRE_PROJECT` | — | Require project context for API calls |
| `UMRS_SCHEDULER_ENABLED` | — | Enable cron-based scheduler (`false` disables the background tick thread entirely) |
| `UMRS_SCHEDULER_TICK_SECONDS` | `60` | Interval between scheduler executor ticks (floor 1s) |
| `UMRS_SERVICE_LIFECYCLE` | — | Service lifecycle management mode |
| `UMRS_ALLOWED_PARTITIONS` | — | Comma-separated Slurm partition allowlist |

### UMRS Project data classes (2026-10-16)

What a project's data class allows, and the class a new project starts as.
For users, see
[Chapter 5](05-projects-models.md#data-class-what-may-leave-your-machine).

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_INSTITUTIONAL_PROVIDER_HOSTS` | unset: the packaged list, `api.rikyu.r-ccs.riken.jp` (RIKEN's Rikyu) | Host names of the inference services your institution runs, separated by commas or spaces. A `sensitive` project may use a provider on one of these hosts; every other public or commercial provider is refused for it. **Unset**, the packaged list applies, and it names RIKEN's Rikyu service: **a site outside RIKEN should set this variable explicitly.** **Set but empty**, the site has no institutional provider. Write bare host names (`llm.example.ac.jp`). A scheme, port, path or trailing dot is dropped and the bare host is used. An entry that is not a host (`user:password@…`, a query, a wildcard, a scheme other than http or https) is ignored, and a warning is logged; the warning shows the host, never a user name, password or query. Matching is exact and ignores letter case; any port on a listed host matches. The value must reach **both** the UMRS server, which checks remote-session starts and gives r4 the list at login, **and** the inference proxy (`umrs-infer` on umrs-front), which checks every proxied request. A process that does not receive it uses the packaged list, so the checks disagree. On umrs-front, set the inventory variable `umrs_front_institutional_provider_hosts`: the converge writes the one `.env` line that the servers and `umrs-infer` both read. Do not edit that line by hand there: the next converge rewrites it, and while the inventory variable is undefined, a converge that finds the line stops rather than delete it (a deleted list would fall back to the packaged one). Move the value into the variable (an empty string keeps "no institutional provider"), or delete the line on purpose. r4 picks up a change at each user's next login. On a stand-alone stack the packaged list applies (RIKEN's Rikyu) and cannot be changed yet. |
| `UMRS_PROJECT_DEFAULT_DATA_CLASS` | `sensitive` (stand-alone stack: `non_sensitive`) | The data class a project takes **when it is created**: `sensitive` or `non_sensitive`. Any other value counts as `sensitive`, and a warning is logged. A change never alters existing projects; their owners change those with `r4 project data-class set`. Read by the UMRS server, which creates the project rows |

### UMRS Store Backends (durability, 2026-08)

Research satellites, workflow execution, and connector sync state are
Postgres-backed by default. All follow the same contract: `pg` = force
PostgreSQL (error if unavailable), `memory` = in-memory reference, `auto`
(default) = PostgreSQL when a pool resolvable via `DB_*`/DATABASE_URL,
else in-memory.

| Variable | Default | Feature |
|----------|---------|---------|
| `UMRS_SCIENTIFIC_MEMORY_STORE_BACKEND` | `auto` | `/umrs/scientific-memory/*` persistence |
| `UMRS_EVIDENCE_STORE_BACKEND` | `auto` | Evidence store durability |
| `UMRS_EXPERIMENT_REGISTRY_STORE_BACKEND` | `auto` | Experiment registry durability |
| `UMRS_EMBEDDINGS_STORE_BACKEND` | `auto` | Embeddings (pgvector cosine) |
| `UMRS_KNOWLEDGE_GRAPH_STORE_BACKEND` | `auto` | Knowledge graph nodes/edges |
| `UMRS_KNOWLEDGE_MODEL_STORE_BACKEND` | `auto` | Knowledge objects (versions+tombstones) |
| `UMRS_HYPOTHESIS_LIFECYCLE_STORE_BACKEND` | `auto` | Hypothesis lifecycle + history |
| `UMRS_TENANTS_STORE_BACKEND` | `auto` | Tenants catalogue |
| `UMRS_CONNECTOR_SYNC_STATE_BACKEND` | `auto` | Connector cursors/DLQ/dedup |
| `UMRS_WORKFLOW_EXECUTION_REGISTRY` | `auto` | Workflow execution records |

### UMRS Storage / S3

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_STORAGE_PATH` | — | Local filesystem storage path |
| `UMRS_MINIO_HOST` | — | MinIO hostname |
| `UMRS_MINIO_PORT` | — | MinIO port |
| `UMRS_S3_URL` | — | Full S3 object-store URL advertised via connect-info (`UMRS_MINIO_URL` is the legacy name, still read) |
| `UMRS_S3_BUCKET` | — | Default S3 bucket name |
| `UMRS_PRESIGNED_URL_TTL_SEC` | — | Default presigned URL TTL |
| `UMRS_PRESIGNED_URL_MAX_TTL_SEC` | — | Maximum presigned URL TTL |

### UMRS Model Registry

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_REGISTRY_BACKEND` | — | Registry storage backend |
| `UMRS_REGISTRY_POSTGRES_DSN` | — | Postgres DSN for model registry |
| `UMRS_ENDPOINT_REGISTRY_SHARED` | unset | (2026-10-03): when `1/true/yes`, the endpoint registry lives in the shared `umrs_db` (hub table, migration 0117) venue-scoped by the site's `site.yaml` name; unset keeps the per-cluster `UMRS_REGISTRY_POSTGRES_DSN` split (rollback path). **Lossy rollback (2026-10-03):** each `front_*` table is frozen at the U-1 flip, so unsetting the flag loads the flip-time snapshot: endpoints registered since are missing and their jobs get reaped as orphans, removed ones come back with stale host/port/status. No reverse-copy tool exists yet |
| `UMRS_REGISTRY_READ_ONLY` | unset (`1` in the standalone RAG service) | 2026-10-03: when `1/true/yes`, this process only reads an endpoint registry that other processes own. It runs no DDL, uses a read-only session, never inserts, updates or deletes (`mark_used` stays in memory), and re-reads the table on each `list_all`. `python -m umrs.api.rag_service` sets it by default. **Never set it in the umrs-front `.env`:** `env_file` feeds all four servers. `start.sh` passes it to `umrs-rag` only, and a `umrs-server` that finds it drops it with an error log |
| `UMRS_REGISTRY_VENUE` | site.yaml `name` | 2026-10-03: the venue a read-only reader sees in the shared registry. On umrs-front, `start.sh` sets `rccs-front` for `umrs-rag`; `UMRS_RAG_REGISTRY_VENUE` in `.env` changes that venue, and an empty value falls back to `rccs-front`: an all-venue read is not offered there (a hub design decision). Elsewhere, empty with no site.yaml means every venue, and a warning is logged. Writers ignore it: a server always uses its site.yaml name |
| `UMRS_MODEL_STAGING_ROOT` | — | Root directory for staged models |

### UMRS RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_RAG_DATABASE_URL` | — | RAG-specific Postgres URL |
| `UMRS_RAG_BIND_HOST` | — | RAG service bind address |
| `UMRS_RAG_PORT` | — | RAG service port |
| `UMRS_RAG_EMBEDDING_MODEL` | — | Default embedding model |
| `UMRS_RAG_EMBEDDING_DIM` | — | Embedding dimension |
| `UMRS_RAG_CHUNK_MAX_TOKENS` | — | Max tokens per chunk |
| `UMRS_RAG_RERANKER_DEVICE` | — | Device for reranker (`cpu` or `cuda`) |

### UMRS ZMQ Transport

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_CONTROL_PORT` | — | ZMQ control channel port |
| `UMRS_DATA_PORT` | — | ZMQ data channel port |
| `UMRS_ZMQ_AUTH_TYPE` | — | Auth type (`curve` or `null`) |
| `UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY` | — | CURVE server public key |
| `UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY` | — | CURVE client public key |
| `UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY` | — | CURVE client secret key |
| `UMRS_ZMQ_MAX_PAYLOAD_BYTES` | — | Max ZMQ message size |

### UMRS Step Execution

| Variable | Default | Description |
|----------|---------|-------------|
| `UMRS_STEP_EXECUTOR` | — | Step executor backend (`slurm`, `podman`, `compose`) |
| `UMRS_STEP_EXECUTOR_TIMEOUT` | — | Timeout per step |
| `UMRS_STEP_IMAGE` | — | Default container image for steps |
| `UMRS_STEP_GPU` | — | GPU allocation for steps |
| `UMRS_STEP_PODMAN_BIN` | — | Path to Podman binary |
| `UMRS_STEP_PODMAN_NETWORK` | — | Podman network name |
| `UMRS_STEP_PODMAN_EXTRA_ARGS` | — | Extra Podman arguments |
| `UMRS_STEP_PAYLOAD_MAX_BYTES` | — | Max step payload size |
| `UMRS_STEP_PAYLOAD_THRESHOLD_BYTES` | — | Threshold for S3 offload |

### UMRS Janitor (Auto-Cleanup)

| Variable | Default | Description |
|----------|---------|-------------|
| `JANITOR_INTERVAL_SEC` | — | Sweep interval (seconds) |
| `JANITOR_BATCH_SIZE` | — | Jobs processed per sweep |
| `JANITOR_ENABLE_TTL` | — | Enable TTL-based cleanup |
| `JANITOR_ENABLE_LIVENESS` | — | Enable liveness checks |
| `JANITOR_ENABLE_ORPHAN` | — | Enable orphan detection |
| `JANITOR_ENABLE_STUCK` | — | Enable stuck-job recovery |
| `JANITOR_STUCK_TIMEOUT_SEC` | — | Stuck job threshold |
| `JANITOR_TTL_GRACE_SEC` | — | Grace period before TTL expiry |
| `JANITOR_ENABLE_FAILED_RETENTION` | `true` | Remove FAILED / TERMINATED / EXPIRED endpoint rows after the retention (a FAILED first replica of a new service recipe stays: it pauses that service's upgrade) |
| `JANITOR_FAILED_RETENTION_SEC` | `21600` | How long an ended endpoint row stays in the registry |
| `JANITOR_ENABLE_PARTITION_MOVE` | `true` | Move a queued model job to another of its listed partitions once that one is blank (Slurm sites that choose per submit) |
| `JANITOR_PARTITION_MOVE_AFTER_SEC` | `120` | How long a model job must have been queued before a move is tried |
| `JANITOR_PARTITION_MOVE_COOLDOWN_SEC` | `300` | The shortest time between two move attempts for the same job |
| `JANITOR_ENABLE_TTL_AUTO_EXTEND` | — | Auto-extend TTL for active jobs |
| `JANITOR_TTL_AUTO_EXTEND_SEC` | — | TTL extension duration |
| `JANITOR_TTL_AUTO_EXTEND_THRESHOLD_SEC` | — | Trigger auto-extend threshold |

## A2L (Artifact & Agent Lineage)

| Variable | Default | Description |
|----------|---------|-------------|
| `A2L_PORT` | `8002` | A2L service port |
| `A2L_HOST` | `0.0.0.0` | A2L bind address |
| `LOG_LEVEL` | `INFO` | A2L log level |
| `DB_HOST` | `postgres` | PostgreSQL host for A2L |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | `postgres` | Database password |
| `DB_NAME` | `e2e_test` | Database name |
| `UMRS_BASE_URL` | `http://umrs:8000` | UMRS URL (for A2L poller) |
| `POLLER_INTERVAL_SEC` | `10` | A2L execution-status polling interval |
| `REDIS_URL` | — | Redis/KeyDB URL for event bus (optional) |
