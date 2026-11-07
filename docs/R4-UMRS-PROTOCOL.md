# R4 ↔ UMRS Wire Protocol — v1.0

> **Status:** PROTOCOL OF RECORD (2026-09-22, gap row D-04). This document is
> the public, versioned R4–UMRS protocol. Version constant:
> `r4_contracts.protocol.PROTOCOL_VERSION` ("1.0"). Change rule: command or
> framing changes bump MAJOR (both sides must agree or refuse);
> additive commands bump MINOR (older peers keep working, missing the new
> verbs).

## 1. Lanes (ADR-059)

| Lane | Transport | Carries |
|---|---|---|
| Data | ZeroMQ, CurveZMQ encryption + ZAP (session mode) | files, jobs, models, workflows, embedding export |
| Control | HTTPS to UMRS's edge process | chat, MCP, knowledge, sessions, catalog, projects, venue, remaining families |

The workstation's only outbound connections are the ZeroMQ channel (direct
TCP 50110–50118) and HTTPS to the edge (443). There is no plaintext channel.

## 2. Handshake and version negotiation

- Liveness on the ZMQ control socket: `HAND_CHECK` → `READY!:v<MAJOR.MINOR>`
  (the server also answers the legacy `READY?` retry with the same reply).
- **Pre-1.0 servers** answer bare `READY!`; clients parse it as ready with
  version "" (compat: `r4_contracts.protocol.parse_handshake_reply`).
- Compatibility rule (`is_protocol_compatible`): MAJOR must match; "" (v0)
  is accepted during the 1.x era and its retirement is announced in a MAJOR
  bump note.

## 3. ZeroMQ data plane (command catalog v1)

String commands, `CMD[:arg:…]` over a DEALER↔ROUTER control socket; the
full wire detail is `umrs/doc/zmq_api_contract.md` (normative for framing).
The v1 surface:

| Command | Meaning |
|---|---|
| `HAND_CHECK` | liveness + version (`READY!:v1.0`) |
| `CREATE_DATA_QUEUE:<client>` | per-client PULL data socket endpoint |
| `OBJECT:<client>:<count>` | read S3 object refs from the data socket |
| `FILE:<client>:<count>` | chunked file upload (`__END_OF_FILE__`) |
| `STATUS…` | status payloads |
| job lifecycle (submit / status / cancel) | durable job registry ops |
| model / workflow ops | registry and provisioning verbs |

Errors on the control socket: `CMD_ERROR:…` (malformed) or a named
negative reply (`NO_DATA_QUEUE`, `NO_DATA_PORT_AVAILABLE:<range>`).
Unknown commands never produce a new reply shape — they follow the same
error frames.

## 4. HTTPS control plane

- Families are lane-classified in `umrs/src/umrs/shared/lanes.py`
  (`v1-stable`, `v1beta`, `internal`; the sidecar route table pinned by
  `test_surface_characterization.py`).
- Every platform route answers success JSON or a FastAPI problem detail
  carrying the shared typed error taxonomy in the
  `x-platform-error-code` header (`r4_contracts.errors` families — the
  taxonomy, not the body text, is the contract).
- Identity arrives as gateway-verified headers with an HMAC signature
  (`x-user-id`, `x-verified-project-id`, `x-verified-hmac`, … —
  `_verified_headers`); routes never trust raw `x-user-id`.
- The venue rail (`/umrs/companion/sessions/{id}/rail/gw/…`) is part of
  this lane with its lease-token auth and read-only family subset (D-42).

## 5. Companion bridge (TUI ↔ runtime)

The companion runtime bridge speaks **protocol v1** over JSONL
(newline-delimited JSON objects): request verbs (`get_science_loop`,
`send_chat`, approvals, session ops — see
`r4/companion/ui/bridge_server.py`) and streamed events
(`chat.delta`, `chat.done`, job progress). v1 rule: unknown keys are
ignored by readers (additive-safe); a v2 shape change requires a named
`protocol` field bump on the hello frame.

## 6. Error and version honesty

- Every lane reports what it knows, refuses by name what it doesn't
  (typed codes on HTTP, named negative replies on ZMQ, `error` objects on
  the bridge). Lanes never fabricate success for an unknown verb.
- Contract pins: `tests/test_protocol_v1.py` (both sides), the surface
  characterization suite (route table), `test_zmq_sessions_api.py` and
  the transport boundary audit (lane separation).
