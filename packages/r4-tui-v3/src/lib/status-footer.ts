/**
 * D3: unified status footer read-model.
 * Pure string builder — cwd · sources · lsp · mcp · permission badge.
 */

export interface StatusFooterInput {
  cwd: string;
  gw: string;
  umrs: string;
  hpc: string;
  mcp: "ok" | "error" | null;
  perms: { egress: string; filesystem: string; compute: string } | null;
  /** Active r4 source (gateway) — visibly drives which backend data shows. */
  src?: string;
}

function short(state: string): string {
  switch (state) {
    case "ok":
    case "connected":
    case "ready":
      return "ok";
    case "connecting":
      return "…";
    case "error":
    case "down":
      return "err";
    default:
      return "-";
  }
}

function cwdBase(cwd: string): string {
  const trimmed = cwd.replace(/[/\\]+$/, "");
  if (!trimmed) return "/";
  const idx = trimmed.lastIndexOf("/");
  return idx >= 0 ? trimmed.slice(idx + 1) || trimmed : trimmed;
}

function permBadge(perms: NonNullable<StatusFooterInput["perms"]>): string {
  const eg = perms.egress === "controlled" ? "ctl" : perms.egress === "open" ? "open" : "no";
  const fs = perms.filesystem === "read_only" ? "ro" : perms.filesystem === "none" ? "no" : "rw";
  const cpu = perms.compute === "none" ? "no" : "yes";
  return `perm ${eg}/${fs}/${cpu}`;
}

/** `src:rccs · cwd · gw ok · umrs ok · hpc ok · lsp none · mcp ok · perm ctl/ro/no`
 * No `perm —` stub: permission MODE lives in the mid bar badge; the footer
 * perm segment renders only when the bridge reports real egress/fs/compute
 * triples (nothing → omit the segment, not a decorative dash). */
export function formatStatusFooter(input: StatusFooterInput): string {
  const parts = [
    ...(input.src ? [`src:${input.src}`] : []),
    cwdBase(input.cwd),
    `gw ${short(input.gw)}`,
    `umrs ${short(input.umrs)}`,
    `hpc ${short(input.hpc)}`,
    "lsp none",
    `mcp ${input.mcp ?? "—"}`,
    ...(input.perms ? [permBadge(input.perms)] : []),
  ];
  return parts.join(" · ");
}
