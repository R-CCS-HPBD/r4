# ADR-042: The ACP rail is a custom JSON-RPC transport — name it honestly, conform later by adapter

**Status:** Proposed (with implementation in WP-08 round 1, same PR)
- **Date:** 2026-09-07
- **Related:** ADR-028 (companion SSE), WP-08 (P9 surface parity), G-P9-3,
  G-P9-1 (capability registry).

## Context

The `r4 acp` transport's module header claims it "implements a JSON-RPC based
protocol that allows editors (VS Code, Cursor, Zed, etc.) to drive r4 through
a **standard wire protocol** ... following the patterns established by editor
agent protocol integrations". That phrasing invites comparison with the Agent
Client Protocol (the editor-agent standard Zed and friends implement) — but
the served vocabulary is entirely custom:

| Method family | ACP standard (agentclientprotocol.com) | This rail serves |
|---|---|---|
| Handshake | `initialize`, `authenticate` | none (`health/check` custom) |
| Agent session | `session/new`, `session/load`, `session/prompt`, `session/cancel` | none (`chat/send` custom) |
| Agent→client updates | `session/update` notifications | custom SSE/event payloads |
| Permissions | `session/request_permission` | bridge-side approval cards |
| Filesystem | `fs/read_text_file`, `fs/write_text_file` | none |
| Terminal | `terminal/create|output|kill|release|wait_for_exit` | none |
| Platform management | (out of scope for ACP standard) | `projects/*`, `workflows/*`, `jobs/*`, `endpoints/*`, `research/*`, `knowledge/*`, `catalog/*`, `skills/*`, `mcp/*`, `commands/*` (36 verbs) |

Consequence of the claim: a user could reasonably point Zed's ACP client at
this rail expecting it to attach; it cannot (G-P9-3, Partial — the claim is
partially true only in the SHAPE, newline-delimited JSON-RPC 2.0).

## Decision

**Rename the claims, keep the module, conform later by adapter.**

1. **The header stops claiming the standard.** `r4/src/r4/acp.py` is
   re-branded in prose as the *R4 editor JSON-RPC rail* — custom vocabulary,
   JSON-RPC 2.0 framing, newline-delimited stdio. The module/file/command
   names (`acp.py`, `r4 acp`, the VS Code extension's client wiring) stay:
   they are internal identifiers, and churning them buys nothing pre-release.
2. **Standard conformance, when wanted, is an ADAPTER in front of the same
   runtime** — a thin process speaking `initialize`, `session/new`,
   `session/prompt`, `session/cancel`, `session/update`, mapping onto
   `_AgentSessionRuntime` (the same seam the custom rail and the bridge
   already share). The capability registry (WP-08) constrains its platform
   surface to the same canonical verbs. This keeps conformance additive and
   testable in isolation instead of invading the working rail.
3. **The vscode-extension is the reference consumer** and keeps working
   unchanged: it speaks the custom vocabulary by design, and the renamed
   header says exactly that.

## Consequences

- G-P9-3 → Closed-by-decision: the dishonest claim is removed in this PR;
  conformance is a named, bounded future adapter (not a broken promise).
- Extension and bridge consumers see no behavior change; the only edited
  surface is prose (module header) plus this ADR.
- A future adapter PR should land with the ACP-standard handshake test
  (initialize → capabilities) as its acceptance, against
  `_AgentSessionRuntime`, not against this rail's dispatch table.

## Verification

- Vocabulary evidence: method registrations enumerate `chat/send`,
  `health/check`, `models/list`, `tools/list` + the 36 platform verbs in
  `ACP_PLATFORM_METHODS` (grep `register`/`ACP_PLATFORM_METHODS` in
  `r4/src/r4/acp.py`); zero match the ACP-standard families above.
- The renamed header text is the pin: it names the rail "custom", lists the
  standard families it does NOT implement, and points at the adapter plan
  (this ADR).
