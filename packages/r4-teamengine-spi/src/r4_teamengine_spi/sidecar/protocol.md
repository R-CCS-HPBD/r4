# Team Engine Sidecar Wire Protocol (ADR-020, phase 4)

The LSP-analog for remote engines. Payloads are exactly the
`r4_contracts.team_engine` dataclasses serialized as JSON.

## Transports

- **Control**: JSON-RPC 2.0 over a length-prefixed stream (stdio or TCP
  loopback). One connection per run attempt.
- **Events**: Server-Sent Events (SSE) — one `TeamEvent` JSON per frame.

## Methods (runtime -> sidecar)

| Method                    | Params                        | Returns            |
|---------------------------|-------------------------------|--------------------|
| `engine/descriptor`       | `{}`                          | EngineDescriptor   |
| `engine/validate`         | `{spec}`                      | `[ValidationIssue]`|
| `engine/compile`          | `{spec, ctx}`                 | `{plan_id}`        |
| `run/start`               | `{plan_id, run}`              | `{run_id}`         |
| `run/checkpoint`          | `{run_id}`                    | CheckpointRef      |
| `run/resume`              | `{run_id, ref}`               | `{}`               |
| `run/cancel`              | `{run_id, reason}`            | `{}`               |
| `run/state`               | `{run_id}`                    | `{state}`          |
| `run/graph`               | `{run_id}`                    | ExecutionGraph     |
| `run/result`              | `{run_id}`                    | TeamResult         |
| `run/metrics`             | `{run_id}`                    | RunMetrics         |

Broker calls (sidecar -> runtime) ride the same JSON-RPC connection in the
reverse direction: `broker/tools.execute`, `broker/models.stream`,
`broker/memory.recall`, `broker/artifacts.put`, `broker/budget.spend`,
`broker/workers.request`. The runtime is the server for broker methods and
the client for engine methods — symmetric, single socket.

## Trust

Sidecars are the mandatory home for Untrusted-tier engines: separate
container, no network beside the SPI socket, filesystem read-only except
attempt scratch (mini-RiVault mount model).
