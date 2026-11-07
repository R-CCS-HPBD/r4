# Demo 01: Chat & Sessions

Run one-shot and streaming chat requests, then inspect session history.

## What It Shows

- `r4 chat` — one-shot inference
- `r4 chat --stream` — streaming inference
- `r4 session` — local session registry
- `r4 companion sessions` — companion session inspection

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Run `00_onboarding` first to provision the endpoint.

## Detailed Step-by-Step (verified 2026-08-27)

1. **One-shot chat** (non-streaming)
   ```bash
   r4 chat "What is 2 + 2? Answer with one token." --model "$DEMO_MODEL_NAME"
   # → 4
   ```
2. **Streaming chat**
   ```bash
   r4 chat "List three short benefits of using the r4 CLI." \
       --model "$DEMO_MODEL_NAME" --stream
   ```
3. **Local session registry**
   ```bash
   r4 session --json    # [] on fresh host; prior sessions survive if run
   ```
4. **Companion session inspection**
   ```bash
   r4 companion sessions    # "No saved sessions." on a fresh profile
   ```

After demo00's endpoint provisioning, chats answer within seconds.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No active endpoint available` (API 503) | chat hits default gateway which has no ready endpoint | run demo00 first, or set `R4_GATEWAY_URL`+`R4_API_KEY` env to a cluster that has one; model@source ref also works: `…--model name@<source>` |
| Model resolution dies | resolver found no candidates anywhere | `R4_DEMO_MODEL=<name>` explicitly, or register the source properly (`r4 source add <source> …`) |
| Session listing is empty | none saved yet — recent chats go to session store only after full round-trip | send any `r4 chat …` once, then re-run `r4 session --json` |
