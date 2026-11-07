/**
 * Typed bridge RPC results (plan H-4.4).
 *
 * `rpc` takes the rail, the verb, params and a timeout: it is the rail's
 * `request` call with the result typed by the generated contract (`rpc-types.gen.ts`, from
 * `contracts/bridge/v1/`, from `r4/src/r4/companion/ui/rpc_schema.py`).
 * It forwards its arguments unchanged — same call, same arity, same timing
 * (no extra await) — so fakes and spies see exactly what they saw before.
 *
 * Only covered verbs are accepted; an uncovered verb stays on
 * `rail.request` until its family gets a schema.
 */

import type { RpcMethod, RpcResult } from "./rpc-types.gen";

export type { RpcMethod, RpcResult, RpcResultMap } from "./rpc-types.gen";

/** Anything with the bridge's request signature (the live client, a tab's client, a test fake). */
export interface RpcRail {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
}

export function rpc<M extends RpcMethod>(
  rail: RpcRail,
  method: M,
  ...args: [params?: Record<string, unknown>, timeoutMs?: number]
): Promise<RpcResult<M>> {
  return rail.request(method, ...args) as Promise<RpcResult<M>>;
}
