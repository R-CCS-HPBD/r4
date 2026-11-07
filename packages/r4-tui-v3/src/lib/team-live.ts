/**
 * Team live events (TUI team visibility wave, 2026-09-27).
 *
 * Pure logic for the live team surface: the team_event line shape pushed by
 * the bridge during a running iteration, a ring buffer per session, and the
 * derived summaries the UI renders (which agent thinks now, last previews).
 * No bridge/store imports — unit-testable in isolation.
 *
 * Honesty contract: a summary is built ONLY from lines the bridge really
 * pushed; absent pieces stay undefined (never fabricated).
 */

export interface TeamEventLine {
  session_id: string;
  session_name?: string;
  kind: string; // iteration_started | agent_status | agent_message | iteration_complete | run_finished
  iteration: number;
  max_iterations?: number;
  role?: string;
  status?: string; // thinking | idle | error (agent_status)
  preview?: string; // bounded agent_message text (≤1200 + "…" server-side)
  emitted_at?: string;
  /** run_finished only: loop continuation verdict + per-role error tails. */
  continued?: boolean;
  errors?: Array<{ role?: string; error?: string }>;
  error_text?: string;
  /** team_started: crew + the bootstrap receipt. */
  crew?: string;
  roles?: string[];
  goal?: string;
  tailor?: { root?: string; tailored?: string[]; kept_default?: string[]; degraded?: string };
  /** agent_activity: one discrete work fact of a role (2026-09-28). */
  activity_kind?: string;
  name?: string;
  duration_ms?: number | null;
}

/** Latest per-role work facts from a ring (agent_activity lines only). */
export function activityRowsFor(ring: TeamEventLine[], role: string, cap = 12): TeamEventLine[] {
  const rows: TeamEventLine[] = [];
  for (let i = ring.length - 1; i >= 0 && rows.length < cap; i--) {
    const line = ring[i]!;
    if (line.kind === "agent_activity" && line.role === role) rows.unshift(line);
  }
  return rows;
}

export interface TeamAgentLive {
  role: string;
  status?: string;
  lastPreview?: string;
  updatedAt?: string;
}

export interface TeamLiveSummary {
  iteration: number;
  maxIterations?: number;
  /** role currently marked thinking, if any. */
  activeRole?: string;
  /** most recent io a user sees at a glance. */
  lastLine?: string;
}

const RING_CAP = 200;
const PREVIEW_CLIP = 96;

/** Append a line to the session ring (cap-bounded, order-preserving). */
export function appendTeamEvent(ring: TeamEventLine[], line: TeamEventLine): TeamEventLine[] {
  const next = [...ring, line];
  return next.length > RING_CAP ? next.slice(next.length - RING_CAP) : next;
}

/** Latest per-role live rows derived from a ring (most recent event wins). */
export function liveAgentsFromRing(ring: TeamEventLine[]): TeamAgentLive[] {
  const byRole = new Map<string, TeamAgentLive>();
  for (const line of ring) {
    if (!line.role) continue;
    const current = byRole.get(line.role) ?? { role: line.role };
    if (line.kind === "agent_status" && line.status) current.status = line.status;
    if (line.kind === "agent_message" && line.preview !== undefined) {
      current.lastPreview = line.preview;
    }
    if (line.emitted_at) current.updatedAt = line.emitted_at;
    byRole.set(line.role, current);
  }
  return [...byRole.values()];
}

/** One-line summary for work-pane rows / notices / the status bar chip. */
export function summarizeRing(ring: TeamEventLine[]): TeamLiveSummary | undefined {
  if (ring.length === 0) return undefined;
  const last = ring[ring.length - 1]!;
  const agents = liveAgentsFromRing(ring);
  const active = agents.find((a) => a.status === "thinking");
  const errored = agents.find((a) => a.status === "error");
  return {
    iteration: last.iteration,
    maxIterations: last.max_iterations,
    activeRole: active?.role,
    lastLine: summarizeTail(last, errored),
  };
}

function summarizeTail(last: TeamEventLine, errored?: TeamAgentLive): string {
  if (last.kind === "iteration_complete") return "iteration complete";
  if (last.kind === "iteration_started") return "iteration started";
  if (last.kind === "agent_status") {
    return `${last.role} ${last.status ?? ""}`.trim();
  }
  if (last.kind === "agent_message" && last.preview !== undefined) {
    const clip = clipOneLine(last.preview);
    return `${last.role}: ${clip}`;
  }
  if (errored) return `${errored.role} error`;
  return last.kind;
}

/** A role's latest full answer (last agent_message in the ring). Server
 * bounds the carriage (≤1200 + "…"); absent = undefined, never invented. */
export function finalMessageFor(ring: TeamEventLine[], role: string): string | undefined {
  for (let i = ring.length - 1; i >= 0; i--) {
    const line = ring[i]!;
    if (line.kind === "agent_message" && line.role === role && line.preview !== undefined) {
      return line.preview;
    }
  }
  return undefined;
}

/** Hard-wrap to width (every character shown, never silently cut). */
export function wrapLines(text: string, width: number): string[] {
  if (!text) return [];
  const out: string[] = [];
  for (const raw of text.split("\n")) {
    let rest = raw;
    while (rest.length > width) {
      out.push(rest.slice(0, width));
      rest = rest.slice(width);
    }
    out.push(rest);
  }
  return out;
}

/** Bound a preview to one line for chips/rows. */
export function clipOneLine(text: string, cap = PREVIEW_CLIP): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= cap ? flat : `${flat.slice(0, cap - 1)}…`;
}

/** Terminal statuses end the live poller (rows carry plain English strings). */
export function teamSessionIsTerminal(status: string): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "canceled"
  );
}

/** Render one ring line as a short notice-detail row. */
export function teamEventNoticeLine(line: TeamEventLine): string {
  const iter = `i${line.iteration}`;
  if (line.kind === "agent_status") return `${iter} ${line.role} → ${line.status ?? "?"}`;
  if (line.kind === "agent_message") {
    return `${iter} ${line.role}: ${clipOneLine(line.preview ?? "") || "(empty)"}`;
  }
  return `${iter} ${line.kind.replace(/_/g, " ")}`;
}
