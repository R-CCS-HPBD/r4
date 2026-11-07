/**
 * ADR-101 test fixtures: the bridge's `session.sandbox` (SandboxState),
 * typed by the generated contract so a drifted fake fails tsc.
 */

import type { SandboxState } from "../../src/bridge/rpc-types.gen";

/** sandbox_bridge.SANDBOX_NOTICE_OFF, as the bridge sends it. */
export const OFF_NOTICE =
  "Sandbox off: shell, Python and MCP servers run with your full permissions. Turn it on with /sandbox on (this session) or r4 --sandbox on (at launch).";

/** sandbox/setting.py SANDBOX_DETAIL_LINES, as the bridge sends them in `detail`. */
export const DETAIL_LINES = [
  "Writes: only the workspace, a per-session temp directory and your extra_write_paths.",
  "Reads: everything except the r4 config directory and credential stores (~/.ssh, ~/.gnupg, ~/.aws, ~/.kube, ~/.docker, ~/.netrc, ...).",
  "Network: isolated; web traffic only through r4's proxy, which follows /web (off, allowlist or public) and refuses private and cloud-metadata addresses.",
  "Environment: API keys, passwords and session tokens are removed (R4_GATEWAY_URL and R4_GATEWAY_TOKEN stay).",
  "It applies from the agent's next command; what already runs keeps running unconfined.",
  "/sandbox off turns it off; /sandbox default on makes it the default.",
];

export function wireSandbox(over: Partial<SandboxState> = {}): SandboxState {
  return {
    mode: "off",
    source: "default_off",
    backend: "bwrap",
    available: true,
    reason: "",
    tmp_dir: "/tmp/r4-sandbox-1000/s-abc/tmp",
    write_roots: ["/work/proj", "/tmp/r4-sandbox-1000/s-abc/tmp"],
    network: "isolated (web: off)",
    notice: OFF_NOTICE,
    mcp_error: "",
    detail: [...DETAIL_LINES],
    ...over,
  };
}
