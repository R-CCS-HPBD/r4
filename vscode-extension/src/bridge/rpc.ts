/**
 * Typed bridge RPC results: the console's own `rpc()` and generated
 * `RpcResultMap` (ADR-103 D-3). Covered verbs only; an uncovered verb stays
 * on `bridge.request` until its family gets a schema (TUI_REFERENCE §12).
 */

export { rpc } from "../../../packages/r4-tui-v3/src/bridge/rpc";
export type { RpcMethod, RpcRail, RpcResult, RpcResultMap } from "../../../packages/r4-tui-v3/src/bridge/rpc";
