# Validation Plan: ADR-012 + ADR-013

> **HISTORICAL (banner added 2026-10-18)** — pre-2026-09-22 design reset: a manual validation checklist for ADR-012 and ADR-013 as of 2026-07-05 (GLM-5.2 endpoint, pre-reset commands). Not an ADR and not maintained; never cite as current. See [docs/README.md](../README.md) §4.

> **Goal:** Verify every implemented function is robust, provides a
> frictionless user experience, and is ready for production operation and
> long-term maintenance.
>
> **Organization:** By validation objective, not by ADR. Each test lists
> which ADR(s) it validates. This scales as ADR-014+ are added.
>
> **Validation Model:** All tests use `GLM-5.2` (a.k.a. `glm-5.2-v2`) as the
> endpoint model. This model is available on the AI4S cluster and has an
> active endpoint ready for validation.

---

## A. Smoke Tests

> Quick verification that core paths work at all.
> Target: <5 minutes total.

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| A1 | `r4 chat --model GLM-5.2 "Hello"` returns a response | ADR-012 |
| A2 | `r4 chat --model GLM-5.2 --stream "Count to 10"` streams tokens and ends with `[DONE]` | ADR-012 |
| A3 | `curl http://<endpoint>:8090/health` returns `{"status": "ok"}` | ADR-012 |
| A4 | `curl http://<endpoint>:8090/manifest` returns JSON with `assistant: true` | ADR-012 |
| A5 | `r4 knowledge export my-project --output test.kb` creates a `.kb` file | ADR-013 |
| A6 | `r4 knowledge validate test.kb` returns "✓ Bundle is valid" | ADR-013 |
| A7 | `r4 knowledge info test.kb` shows manifest details | ADR-013 |
| A8 | `r4 knowledge list` lists available bundles | ADR-013 |
| A9 | `r4 knowledge import test.kb --project-id imported-project` imports bundle | ADR-013 |
| A10 | `r4 up --local --public http://localhost:11434 --model ollama/llama3 "Hello"` returns local response | ADR-013 |

---

## B. Functional Tests

> Verify each component function in isolation.

### B1. Assistant Service

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| B1.1 | Non-streaming chat returns valid JSON with `choices[0].message.content` | ADR-012 |
| B1.2 | Streaming chat returns SSE chunks in order, terminated by `data: [DONE]` | ADR-012 |
| B1.3 | `--system "You are a pirate"` changes response persona | ADR-012 |
| B1.4 | `--max-tokens 5` truncates response at ~5 tokens | ADR-012 |
| B1.5 | `--temperature 0.0` produces deterministic response | ADR-012 |
| B1.6 | Request with `project_id`, `rag`, `rag_top_k`, `rag_threshold` → upstream receives request WITHOUT these keys | ADR-012 |
| B1.7 | Request with `rag: true` and RAG service down → request succeeds, no RAG context injected (fail-open) | ADR-012 |
| B1.8 | Request with `rag: true` and RAG service up → RAG context injected into system message | ADR-012 |
| B1.9 | Request with `rag: false` → RAG service not called, request succeeds | ADR-012 |

### B2. Gateway Route (`/gw/chat/*`)

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| B2.1 | `GET /gw/chat/v1/models` with valid auth → returns model list JSON | ADR-012 |
| B2.2 | `POST /gw/chat/v1/chat/completions` with valid auth → returns chat completion JSON | ADR-012 |
| B2.3 | Request without auth token → returns 401, not 500 | ADR-012 |
| B2.4 | Request with expired token → returns 401 with clear message | ADR-012 |
| B2.5 | Streaming request through Gateway → SSE chunks arrive in order, `[DONE]` sentinel present | ADR-012 |

### B3. Knowledge Bundle Format

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| B3.1 | Export bundle → manifest.json contains correct `format`, `version`, `project_id`, `chunk_count` | ADR-013 |
| B3.2 | Export bundle → documents/chunks.jsonl contains all chunks with `chunk_id`, `content`, `metadata` | ADR-013 |
| B3.3 | Export bundle → embeddings/embeddings.jsonl contains all embeddings with `chunk_id`, `embedding` | ADR-013 |
| B3.4 | Export bundle → graph/entities.json and graph/relations.json contain knowledge graph | ADR-013 |
| B3.5 | Export bundle → catalog/catalog.json contains science catalog metadata | ADR-013 |
| B3.6 | Import bundle → all files written to local knowledge store at `~/.config/r4/knowledge/<project_id>/` | ADR-013 |
| B3.7 | Validate bundle with corrupted manifest → returns clear error: "Invalid manifest format" | ADR-013 |
| B3.8 | Validate bundle with missing chunks file → returns clear error: "Missing required file" | ADR-013 |

### B4. RAG with Knowledge Bundle

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| B4.1 | `r4 chat --knowledge test.kb "What is in the KB?"` → loads bundle, searches with BM25, injects context | ADR-013 |
| B4.2 | Load bundle with embeddings, query with `query_embedding` → returns results ranked by 50% BM25 + 50% vector | ADR-013 |
| B4.3 | Load bundle without embeddings → returns results ranked by BM25 only | ADR-013 |
| B4.4 | Load bundle with 0 chunks → returns empty RAG response, no crash | ADR-013 |
| B4.5 | Load bundle, query, unload bundle, query again → first query returns local results, second hits platform | ADR-013 |

### B5. Local Inference Mode

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| B5.1 | `r4 up --local --public https://api.openai.com --api-key sk-... --model gpt-4o "Hello"` → response from OpenAI | ADR-013 |
| B5.2 | `r4 up --local --public http://localhost:11434 --model ollama/llama3 "Hello"` → response from local Ollama | ADR-013 |
| B5.3 | `r4 up --local --public http://localhost:11434 --model ollama/llama3 --knowledge test.kb "What is in the KB?"` → local inference with knowledge bundle | ADR-013 |
| B5.4 | `r4 up --local` (no --public) → returns clear error: "Local mode requires a public endpoint URL" | ADR-013 |
| B5.5 | `r4 up --local --public http://localhost:99999 --model test "Hello"` → returns clear error about unreachable endpoint | ADR-013 |

---

## C. Integration Tests

> Verify components work together end-to-end.

### C1. End-to-End Chat Flow

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| C1.1 | `r4 endpoint up --model GLM-5.2` → endpoint ready → `r4 chat --model GLM-5.2 "Hello"` → response received → `r4 endpoint down` → deprovisioned | ADR-012 |
| C1.2 | Same as C1.1 but with `--stream` flag | ADR-012 |
| C1.3 | Same as C1.1 but with `--knowledge test.kb` flag | ADR-012, ADR-013 |
| C1.4 | Frontend Assistant Service running → chat through `/gw/chat/*` → response received | ADR-012 |
| C1.5 | Frontend Assistant Service stopped → chat proxy returns 503 | ADR-012 |

### C2. End-to-End Knowledge Portability Flow

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| C2.1 | Upload document → index → export bundle → import bundle → chat with bundle → response includes document context | ADR-013 |
| C2.2 | Export bundle on platform A → import bundle on platform B → chat with bundle on platform B → response includes context | ADR-013 |
| C2.3 | Export bundle → validate bundle → import bundle → validate local knowledge store → all chunks present | ADR-013 |

### C3. End-to-End Local Inference Flow

| # | Test | Validates | Pass? |
|---|------|-----------|-------|
| C3.1 | Start Ollama → `r4 up --local --public http://localhost:11434 --model ollama/llama3 "Hello"` → response from local Ollama | ADR-013 |
| C3.2 | Start Ollama → `r4 up --local --public http://localhost:11434 --model ollama/llama3 --knowledge test.kb "What is in the KB?"` → local inference with knowledge bundle | ADR-013 |
| C3.3 | Start external API → `r4 up --local --public https://api.openai.com --api-key sk-... --model gpt-4o "Hello"` → response from OpenAI | ADR-013 |

---

## D. Failure Tests

> Intentionally inject failures to validate graceful degradation.

### D1. Component Failure Injection

| # | Test | Expected Behavior | Validates | Pass? |
|---|------|-------------------|-----------|-------|
| D1.1 | Kill Assistant Service mid-stream | Client receives SSE error event + `[DONE]`, not a hang | ADR-012 |
| D1.2 | Kill Assistant Service before request | Chat proxy returns 502 with clear error message | ADR-012 |
| D1.3 | Kill RAG service before request | Chat succeeds, no RAG context injected (fail-open) | ADR-012 |
| D1.4 | Kill RAG service mid-request | Chat succeeds, no RAG context injected (fail-open) | ADR-012 |
| D1.5 | Kill Gateway mid-stream | Client receives connection error, not a hang | ADR-012 |
| D1.6 | Kill embedding model service | RAG falls back to BM25-only search | ADR-013 |
| D1.7 | Kill vector DB (pgvector) | RAG falls back to BM25-only search | ADR-013 |
| D1.8 | RAG query timeout (set timeout to 1ms) | Chat succeeds, no RAG context injected (fail-open) | ADR-012 |

### D2. Network Failure Injection

| # | Test | Expected Behavior | Validates | Pass? |
|---|------|-------------------|-----------|-------|
| D2.1 | Drop network between Gateway and Assistant Service | Gateway returns 502 after retry budget exhausted | ADR-012 |
| D2.2 | Drop network between client and Gateway | Client retries with exponential backoff, eventually fails with clear error | ADR-012 |
| D2.3 | Drop network between Assistant Service and LiteLLM | Assistant Service returns 502, chat fails with clear error | ADR-012 |
| D2.4 | Introduce 500ms latency between Gateway and Assistant Service | Chat still works, latency increased by ~500ms | ADR-012 |

### D3. Resource Exhaustion

| # | Test | Expected Behavior | Validates | Pass? |
|---|------|-------------------|-----------|-------|
| D3.1 | Send 10 concurrent streaming requests | All 10 streams complete successfully | ADR-012 |
| D3.2 | Send 50 concurrent streaming requests | Streams either complete or fail with clear 429/503 error, no crash | ADR-012 |
| D3.3 | Send request with 10MB message body | Request succeeds or fails with clear size limit error | ADR-012 |
| D3.4 | Fill disk on compute node | Endpoint fails with clear error, no silent corruption | ADR-012 |

---

## E. Performance Tests

> Verify latency and throughput targets.

### E1. Assistant Service Latency

| # | Metric | Target | Pass? |
|---|--------|--------|-------|
| E1.1 | Non-streaming chat p50 latency | <500ms over direct vLLM |
| E1.2 | Non-streaming chat p99 latency | <2s over direct vLLM |
| E1.3 | Streaming first token p50 latency | <100ms over direct vLLM |
| E1.4 | RAG context injection overhead | <50ms per request |
| E1.5 | Extension stripping overhead | <1ms per request |

### E2. Knowledge Bundle Performance

| # | Metric | Target | Pass? |
|---|--------|--------|-------|
| E2.1 | Export 1,000 chunks | <5s |
| E2.2 | Export 10,000 chunks | <30s |
| E2.3 | Import 1,000 chunks | <3s |
| E2.4 | Import 10,000 chunks | <30s |
| E2.5 | BM25 search over 10,000 chunks | <100ms |
| E2.6 | Vector search over 10,000 embeddings | <50ms |
| E2.7 | Hybrid search (BM25 + vector) over 10,000 chunks | <150ms |

### E3. Concurrency Performance

| # | Metric | Target | Pass? |
|---|--------|--------|-------|
| E3.1 | 1 concurrent streaming request | tokens/sec baseline |
| E3.2 | 5 concurrent streaming requests | tokens/sec within 90% of baseline |
| E3.3 | 20 concurrent streaming requests | tokens/sec within 75% of baseline |
| E3.4 | 50 concurrent streaming requests | tokens/sec within 50% of baseline |
| E3.5 | 100 concurrent streaming requests | System does not crash; requests succeed or fail with clear error |

### E4. Cold Start Performance

| # | Metric | Target | Pass? |
|---|--------|--------|-------|
| E4.1 | Endpoint launch to ready | <5min (Slurm queue dependent) |
| E4.2 | Knowledge bundle load to first query | <5s for 1,000 chunks |
| E4.3 | Assistant Service startup to health | <10s |

---

## F. Security Tests

> Verify auth propagation, input validation, and bundle security.

### F1. Auth Propagation

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| F1.1 | Send chat request without auth token | Returns 401 | ADR-012 |
| F1.2 | Send chat request with valid auth token | Returns 200 with chat response | ADR-012 |
| F1.3 | Send chat request with expired auth token | Returns 401 | ADR-012 |
| F1.4 | Send chat request with malformed auth token | Returns 401 | ADR-012 |
| F1.5 | Auth token from user A used to access user B's project | Returns 403 | ADR-012 |

### F2. Input Validation

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| F2.1 | Send 10MB prompt | Request succeeds or fails with clear size limit error | ADR-012 |
| F2.2 | Send JSON injection payload in message | Request succeeds, injection has no effect | ADR-012 |
| F2.3 | Send malformed SSE chunk | Client handles gracefully, no crash | ADR-012 |
| F2.4 | Send invalid UTF-8 in message | Request fails with clear error, no crash | ADR-012 |
| F2.5 | Send very long system prompt (100KB) | Request succeeds or fails with clear size limit error | ADR-012 |

### F3. Knowledge Bundle Security

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| F3.1 | Inspect `.kb` bundle contents | No secrets, credentials, or PII leaked in bundle | ADR-013 |
| F3.2 | Import bundle from untrusted source | Bundle is validated before import, no code execution | ADR-013 |
| F3.3 | Export bundle with sensitive project data | Bundle contains only indexed chunks and embeddings, not raw files | ADR-013 |
| F3.4 | Create bundle with zip bomb (1000x compression) | Import fails with clear error or size limit, no OOM | ADR-013 |
| F3.5 | Create bundle with directory traversal (`../../etc/passwd`) | Import fails with clear error, no file access outside bundle | ADR-013 |
| F3.6 | Create bundle with symlinks pointing outside bundle | Import fails with clear error, no file access outside bundle | ADR-013 |
| F3.7 | Create bundle with duplicate filenames | Import fails with clear error or handles gracefully | ADR-013 |

### F4. Multi-User Isolation

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| F4.1 | User A creates knowledge project A → User B cannot retrieve project A | Returns 403 for User B | ADR-013 |
| F4.2 | Two users stream simultaneously → responses never cross | Each user receives their own response | ADR-012 |
| F4.3 | User A's endpoint quota is independent of User B's quota | Quota enforcement is per-user | ADR-012 |
| F4.4 | User A imports knowledge bundle → User B cannot access it | Knowledge store is per-user | ADR-013 |

---

## G. UX Tests

> Verify first-time user experience is frictionless.

### G1. Onboarding Flow

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| G1.1 | Fresh install: `pip install r4` | Installs without errors | ADR-012 |
| G1.2 | First command: `r4` | Shows helpful welcome message with next steps | ADR-012 |
| G1.3 | `r4 auth login --gateway-url https://data0.ai.r-ccs.riken.jp/ai4s` | Authenticates and saves profile | ADR-012 |
| G1.4 | `r4 chat --model GLM-5.2 "Hello"` | Returns response within 10s | ADR-012 |
| G1.5 | `r4 rag upload document.pdf --project my-project` | Uploads and indexes document | ADR-012 |
| G1.6 | `r4 chat --model GLM-5.2 --knowledge my-project "What is in the document?"` | Returns response with document context | ADR-012, ADR-013 |

### G2. Measurable UX Success Criteria

| # | Test | Target | Validates | Pass? |
|---|------|--------|-----------|-------|
| G2.1 | Fresh machine, no documentation: developer completes `r4 chat "Hello"` | <10 minutes | ADR-012 |
| G2.2 | Fresh machine, no documentation: developer completes `r4 knowledge export` → `r4 knowledge import` → `r4 chat --knowledge` | <15 minutes | ADR-013 |
| G2.3 | Observer records number of commands requiring Google/search/docs | Target = 0 | ADR-012, ADR-013 |
| G2.4 | Observer records number of error messages that don't suggest a fix | Target = 0 | ADR-012, ADR-013 |

### G3. Error Message Quality

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| G3.1 | `r4 chat` with no model configured | Shows: "No model configured. Run: r4 config set model <name>" | ADR-012 |
| G3.2 | `r4 chat --model nonexistent "Hello"` | Shows: "Model 'nonexistent' not found. Available models: [list]" | ADR-012 |
| G3.3 | `r4 knowledge export` with no project | Shows: "No project specified. Run: r4 project switch <id>" | ADR-013 |
| G3.4 | `r4 up --local` with no endpoint | Shows: "Local mode requires a public endpoint URL. Pass --public <url>" | ADR-013 |
| G3.5 | Network error during chat | Shows: "Connection failed: <reason>. Check your network and try again." | ADR-012 |
| G3.6 | Auth token expired | Shows: "Authentication expired. Run: r4 auth login" | ADR-012 |

### G4. Help Text Quality

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| G4.1 | `r4 --help` | Lists all top-level commands with one-line descriptions | ADR-012 |
| G4.2 | `r4 chat --help` | Shows all chat flags with examples | ADR-012 |
| G4.3 | `r4 knowledge --help` | Shows all knowledge subcommands with examples | ADR-013 |
| G4.4 | `r4 up --help` | Shows --local, --knowledge, --public flags with examples | ADR-013 |
| G4.5 | `r4 rag --help` | Shows all RAG subcommands with examples | ADR-012 |

### G5. Interactive Mode UX

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| G5.1 | `r4 chat` (interactive, no message) | Launches companion TUI with welcome message | ADR-012 |
| G5.2 | Type a message in companion TUI | Response streams in real-time | ADR-012 |
| G5.3 | Use `/help` in companion TUI | Shows all available slash commands | ADR-012 |
| G5.4 | Use `/rag query "test"` in companion TUI | Executes RAG query and shows results | ADR-012 |
| G5.5 | Use `/endpoint up --model GLM-5.2` in companion TUI | Provisions endpoint from within TUI | ADR-012 |

---

## H. Operational Tests

> Verify the system can be operated, recovered, and upgraded in production.

### H1. Recovery Tests

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| H1.1 | Kill Assistant Service process → systemd/docker restarts it → health restored → chat succeeds | Auto-recovery within 30s | ADR-012 |
| H1.2 | Kill Gateway process → systemd/docker restarts it → health restored → chat succeeds | Auto-recovery within 30s | ADR-012 |
| H1.3 | Kill UMRS process → systemd/docker restarts it → health restored → chat succeeds | Auto-recovery within 60s | ADR-012 |
| H1.4 | Network interruption between client and Gateway → network restored → client reconnects → chat succeeds | Eventual success after network recovery | ADR-012 |
| H1.5 | Network interruption between Gateway and Assistant Service → network restored → chat succeeds | Gateway retries, eventually succeeds | ADR-012 |

### H2. Upgrade/Compatibility Tests

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| H2.1 | Create knowledge bundle with v1.0 format → upgrade CLI → bundle still imports | Backward compatibility | ADR-013 |
| H2.2 | Old assistant service → new gateway → compatible | Backward compatibility | ADR-012 |
| H2.3 | Old endpoint manifest → new deployment → still accepted | Backward compatibility | ADR-012 |
| H2.4 | Existing OpenAI client (`/gw/llm/v1/*`) still works after ADR-012 deployment | Backward compatibility | ADR-012 |
| H2.5 | Assistant can be disabled (stop frontend assistant service) | Feature toggle | ADR-012 |
| H2.6 | Existing automation scripts still work after ADR-012 deployment | Backward compatibility | ADR-012 |

### H3. Long-Running Stability Tests

| # | Test | Duration | Expected | Validates | Pass? |
|---|------|----------|----------|-----------|-------|
| H3.1 | Chat soak: continuous requests, no memory leak, stable latency | 24 hours | Memory growth <10%, latency stable | ADR-012 |
| H3.2 | Streaming sessions: 1000 sequential streaming sessions, memory stable, FD count stable, thread count stable | 24 hours | No resource leaks | ADR-012 |
| H3.3 | Knowledge bundle load/unload cycle: 1000 cycles, no memory leak | 4 hours | Memory growth <5% | ADR-013 |

### H4. State Validation Tests

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| H4.1 | Auth: login → token refresh → logout → re-login → profile switching → all states transition correctly | Full auth lifecycle | ADR-012 |
| H4.2 | Knowledge bundles: import → delete → reimport → overwrite → all states transition correctly | Full bundle lifecycle | ADR-013 |
| H4.3 | Endpoints: provision → ready → degrade → ready → teardown → all states transition correctly | Full endpoint lifecycle | ADR-012 |

---

## I. Observability Tests

> Verify operators can diagnose failures through logs, metrics, and traces.

### I1. Log Correlation

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| I1.1 | Send chat request → Gateway logs contain request ID → Assistant Service logs contain same request ID → endpoint logs contain same request ID | Request ID propagates through all components | ADR-012 |
| I1.2 | Send RAG query → RAG service logs contain same request ID as Gateway | Request ID propagates to RAG service | ADR-012 |
| I1.3 | Failed request → error logs contain request ID, user ID, project ID, error reason | Error logs are actionable | ADR-012 |

### I2. Metrics Validation

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| I2.1 | Send chat request → `/metrics` endpoint shows increased request count | Request counter works | ADR-012 |
| I2.2 | Send streaming request → `/metrics` endpoint shows increased streaming count | Streaming counter works | ADR-012 |
| I2.3 | Send chat request → `/metrics` endpoint shows latency histogram | Latency histogram works | ADR-012 |
| I2.4 | Send RAG query → `/metrics` endpoint shows RAG query duration | RAG duration metric works | ADR-012 |
| I2.5 | Load knowledge bundle → `/metrics` endpoint shows bundle chunk count | Bundle metric works | ADR-013 |

### I3. Tracing Validation

| # | Test | Expected | Validates | Pass? |
|---|------|----------|-----------|-------|
| I3.1 | Send chat request → Jaeger trace contains spans for: Gateway → Assistant Service → LiteLLM → vLLM | Distributed tracing works | ADR-012 |
| I3.2 | Send RAG query → Jaeger trace contains spans for: Gateway → RAG Service → pgvector | RAG tracing works | ADR-012 |
| I3.3 | Failed request → Jaeger trace shows error span with error reason | Error tracing works | ADR-012 |

---

## J. Documentation Tests

> Verify documentation is accurate and examples are executable.

### J1. ADR Completeness

| # | Check | Pass? |
|---|-------|-------|
| J1.1 | ADR-012 has Context, Decision, Consequences, Alternatives | |
| J1.2 | ADR-013 has Context, Decision, Consequences, Alternatives | |
| J1.3 | ADR-012 status is "Accepted" | |
| J1.4 | ADR-013 status is "Accepted" | |

### J2. Documentation Accuracy

| # | Check | Pass? |
|---|-------|-------|
| J2.1 | Every documented command actually works (execute each one) | |
| J2.2 | Every example in docs is copy-paste executable | |
| J2.3 | `r4 --help` output matches documented commands | |
| J2.4 | `r4 knowledge --help` output matches documented subcommands | |
| J2.5 | `r4 up --help` output matches documented flags (--local, --knowledge, --public) | |

### J3. User Documentation

| # | Check | Pass? |
|---|-------|-------|
| J3.1 | Getting started guide mentions knowledge bundles | |
| J3.2 | User manual has section on local inference mode | |
| J3.3 | User manual has section on knowledge portability | |
| J3.4 | Troubleshooting guide covers common ADR-012/013 issues | |

---

## Execution Order

1. **A. Smoke Tests** — quick verification that core paths work
2. **B. Functional Tests** — verify each component function in isolation
3. **C. Integration Tests** — verify components work together end-to-end
4. **D. Failure Tests** — intentionally inject failures to validate graceful degradation
5. **E. Performance Tests** — verify latency and throughput targets
6. **F. Security Tests** — verify auth propagation, input validation, and bundle security
7. **G. UX Tests** — verify first-time user experience is frictionless
8. **H. Operational Tests** — verify the system can be operated, recovered, and upgraded
9. **I. Observability Tests** — verify operators can diagnose failures
10. **J. Documentation Tests** — verify documentation is accurate and examples are executable

---

## Sign-off

| Reviewer | Date | Status |
|----------|------|--------|
| | | |
| | | |
| | | |
