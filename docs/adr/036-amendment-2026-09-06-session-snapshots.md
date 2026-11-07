# ADR-036 Amendment (2026-09-06, WP-04 first leg) — Session snapshot journal

**Status:** Proposed (with implementation, same PR)
- **Base document:** [036-loop-hosting-companion-job-kind.md](036-loop-hosting-companion-job-kind.md) (Draft; the F2 precondition for server-side loops + durable multi-client)
- **Gap anchors:** G-P6-1 (companion SQLite silent authority), G-P6-2 (chat sessions not answerable cross-client)

## Decision (first leg)

1. **The companion client session is journaled over Postgres.** A new
   `companion_session_snapshots` table (migration 0080, keyed
   `(user_id, session_id)`) holds the full session document payload;
   every client `save()` mirrors here via the new router
   `POST /umrs/companion/sessions/{id}/snapshot` — best-effort by law:
   mirror failures never break saves (counted + degraded status),
   local rows remain usable offline.

2. **SQLite becomes an explicit read-model.** When a session is absent
   locally, `CompanionSessionStore.rebuild_from_remote(client)` pulls the
   owner's snapshots into the same columns. Existing local rows are left
   untouched (authority = newest writer; rebuild only fills gaps).

3. **Default-on with a hard switch.** Mirroring on by default,
   off via `R4_SESSION_SNAPSHOT=off` (also programmatic
   `set_snapshot_enabled(False)`); `sync_status()` surfaces
   `local/unwired/mirrored/degraded` + sync error count to any surface
   that wants to badge it honestly.

4. **Owner-pinned, fail-closed identity.** The router reads the
   gateway-verified `x-user-id`; absent identity → 401; absent pool → 503;
   list/GET are owner-scoped only (payloads never cross tenants).

## Deferred

- True write-ahead journal ordering and mid-update merge (this leg has
  the snapshot upsert only; chat-history merge on conflict is not
  implemented and unnecessary for the "fresh machine" proof).
- The durable approval inbox for pending approvals (G-P6-2's leg), by
  design the ADR-036 big one.
- Snapshot retention/GC policy: closed client-side 2026-09-10 (WP-04 round 2):
  CLI `archive/remove --older-than-days N` now mirror-deletes each row through
  the owner-scoped journal DELETE (umrs), so server table growth is bounded
  by live rows; a server-side GC cron remains a deferred durable-job decision
  (see G-P6-1 remedy note), not a gap.
- Mirroring push into a durable async worker (asyncpg path of the loop
  runner — same round as WP-05).
- Automatic snapshot emissive from the Bun/TUI surfaces (currently
  reachable where `session.client.post` exists; other surfaces mirror
  only if a Client is held).

## Wire gating note

Eligible mirror sites are every CompanionSessionStore construction (its
`_mirror_snapshot` consults the session's own client). The fn carries no
extra-wide identity — the user id is resolved server-side from the
gateway-verified header.
