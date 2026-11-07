# Frontend-Venue Sessions (Remote Companion Sessions)

**Status 2026-09-10 (venue system leg complete on umrs-front):** the venue system is
live on `umrs-front`: migrations 0085-0087 + 0087grants applied; keys sealed;
`umrs-infer` healthy on the egress-net bridge; gateway lanes serve
`/gw/user-sources` + `/gw/secrets`; provisioned sessions run through the
sidecar (nginx + squid on the egress pod, CPU-only worker, GPU/vLLM absent);
the full I-series (§1-§14 of the verification grid) is live-verified —
isolation, injection, streaming, web class, off-host redirect policy,
unpush → deny-next-request, job-driven teardown, isolation self-test (exit 78).
See the repository's development notes for live evidence.

Frontend-venue ("remote") sessions run the companion agent on the platform
host instead of on your laptop. The agent keeps working after you
disconnect, and it can use **your own provider credentials** without ever
possessing the key.

## What the worker can and cannot do

| Can | Cannot |
|---|---|
| Call provider inference for your pushed sources (streaming, big bodies, long generations) | Read the provider key (it never reaches the sandbox) |
| Reach the public internet for tools (`pip`, `git`, `curl`, `uv`) through the web proxy on 80/443 | Reach infrastructure (UMRS API/Postgres/minio/metadata) — the squid deny set + internal network kill those routes |
| Read its mounted session token file (used by the runtime to call the sidecar proxy) | Use that token off its own egress IP (lease is IP-bound; replay from anywhere else → 401 `ip_mismatch`) |
| Renew its own lease (heartbeat every 15 min default) | Renew higher/longer than the lease's hard lifetime cap |

**Known non-working through the web proxy:** raw-TCP/UDP tools that ignore
`HTTPS_PROXY`, SSH git remotes (`git@host:...`), QUIC/HTTP-3 (clients fall
back to TCP).
**Not in the sandbox:** GPUs, vLLM, model weight mounts (Amendment 1 — the
worker is CPU-only).

## Pushing a source for remote use

Once per source, explicitly:

```bash
r4 source list                      # see what's local
r4 source push openai-main          # asks confirmation; prints fingerprint
r4 source push openai-main --yes    # scripted form
r4 source list --remote             # badges: pushed / stale / tombstoned / - / ?
```

`stale` means the server's fingerprint differs from your local key — push
again (rotates the sealed copy); `?` means the platform was unreachable
(badge is named, never silent).

Rotation/revocation:

```bash
r4 source unpush openai-main        # tombstones + revokes referencing leases
                                 # (live sessions deny on the next request)
r4 source remove openai-main        # local removal ALSO removes the
                                 # UMRS profile/token artifacts (G-2) and
                                 # attempts a remote unpush
```

Local credentials stay in `~/.config/r4/sources.json` (0600). Add flags are
`--api-key-env VAR` / `--api-key-stdin` (preferred — the plain
`--api-key KEY` flag warns because it leaks into shell history/process
lists). Credentialed `http://` sources are refused unless added with
`--allow-insecure-http` intent, and are never pushed.

## Starting a remote session

**Since 2026-09-21 (repo-side — the live receipt on umrs-front lands with
the next deployment converge).**

TUI:

1. `/sessions new` (or `n` in the sessions pane) → **Server (frontend)**.
   The row is disabled with the reason while the session journal is
   unreachable.
2. The **remote source picker** lists your public sources that carry a
   key, with the server's badge (`pushed (47m)` · `stale` · `not pushed`
   · `server unreachable`). `w` cycles the web policy
   (public → allowlist → none). Enter on a pushed row starts the sandbox;
   Enter on a row that is not pushed asks once, pushes it (sealed
   server-side — the key never reaches the sandbox), then starts.
3. Chat as usual. A remote turn is queued to the server (owner-scope
   queue), the sandbox worker runs it, partial text streams into the
   bubble as the worker reports it, and the final assistant row is adopted
   from the journal. The status bar shows `remote:running/idle · lease 47m`.
4. Tool approvals the worker needs appear on the usual approval card and
   are answered through the durable inbox (any of your clients can answer;
   unanswered after 15 min = denied).
5. `/venue` shows the runtime state and lease; `/venue stop` tears it down
   (lease revoked); `/venue sources` pushes/unpushes without starting;
   `/venue approvals` re-opens pending approvals. In the sessions pane,
   `t` stops the remote runtime of a Server row.

Refusals are re-told with the server's words: `source_not_pushed` (the
picker offers the push), `unavailable` (the venue is not configured on
that server), `already_running`, `provision_failed` (the step that failed).

CLI:

```bash
r4 session start <id> --source openai-main [--web public|allowlist|none] [--json]
r4 session venue <id> [--json]      # state, sources, lease expiry, worker phase
r4 session stop <id>
```

The sandbox never holds your provider key or your JWT: it holds a
session token bound to its egress IP, renewed by heartbeat, revoked on
stop/unpush. Turns for a `frontend` session are never executed on your
laptop — if the runtime is stopped, start it again or fork the session to
run locally.

## Web policy

Three modes at session start (default `public`):

- `public` — any public-internet host on 80/443; infrastructure always denied.
- `allowlist` — plus a per-session domain list (stricter projects).
- `none` — no web egress at all (research input comes via `KNOWLEDGE_INGEST` jobs).

## Where things are audited

- Push/unpush events: `user_source_audit` (owner-scoped; `r4 source audit <name>`).
- Per-request inference outcomes (+ denials with reason codes): broker audit (`/umrs/secrets/audit`).
- Lease lifecycle (issue/renew/revoke/idle teardown by the session job): same audit trail.
