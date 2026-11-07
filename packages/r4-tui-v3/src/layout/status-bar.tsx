/**
 * Status bar.
 */

import { actions, workspace, activeTab } from "../stores";
import { formatVenueChip } from "../lib/venue-runtime";
import { sandboxChip } from "../lib/sandbox";
import { agentRuntime } from "../stores/agent-runtime";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { formatObservabilitySummary, type ResourceSnapshot } from "../lib/observability-strip";
import { useTerminalDims } from "../lib/terminal-dims";
import { paneLayout } from "../lib/pane-layout";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { isOfflineBackend } from "../lib/console-mode";
import { modeBadge } from "../lib/mode-badge";
import { For, Show, createEffect, createSignal, onCleanup, onMount } from "solid-js";
import { refreshStatusline } from "../lib/statusline";
import { StatusChip, type ChipState as StatusChipState } from "../components/status-chip";
import {
  agentPhase,
  PhaseGlyph,
  pulseFgForElapsed,
  workStatusLine,
  type AgentPhase,
} from "../components/agent-activity";
import { teamsStore } from "../stores/teams";
import { summarizeRing } from "../lib/team-live";
import { truncateSmart } from "../lib/text";
import { workflowBadgeCells, workflowBadgeLabel } from "../lib/workflow-badge";
import { escInterruptHint } from "../lib/esc-interrupt";
import { webStatusChip } from "../lib/web-status";
import { refreshSearchServerStatus, searchServerBackend } from "../lib/search-server-state";
import { worktreeChip } from "../lib/worktree";
import { syncWorktreeFromBridge } from "../commands/worktree-command";

interface Chip {
  label: string;
  state: StatusChipState;
  /** Label cells when they differ from `label.length` (the workflow badge
   * reserves its ambiguous-width glyphs as wide, 2026-10-24 review). */
  cells?: number;
}

/** Cells one chip takes: label + 1-col padding each side. */
function oneChipCells(chip: Chip): number {
  return (chip.cells ?? chip.label.length) + 2;
}

/** Unread count + the three connection edges (gw / umrs / hpc). */
function connectionChips(unread: number): Chip[] {
  const chips: Chip[] = [];
  if (unread > 0) chips.push({ label: `n:${unread}`, state: "info" });
  const edge = (name: string, value: string): Chip => ({
    label: `${name}:${value}`,
    state: value === "connected" ? "ok" : "warn",
  });
  const c = workspace.connection;
  chips.push(edge("gw", c.gateway), edge("umrs", c.umrs), edge("hpc", c.hpc));
  return chips;
}

/** Cells a StatusChip takes: label + 1-col padding each side. */
function chipCells(chips: Chip[]): number {
  let cells = 0;
  for (const c of chips) cells += oneChipCells(c);
  return cells;
}

/** Chips that fit `room` cells, in priority order: a chip that does not fit
 * is dropped whole (a later, smaller one may still fit) — never clipped
 * mid-label by the engine ("umrs:degra", "gw:"). */
function fitChips(chips: Chip[], room: number): Chip[] {
  const kept: Chip[] = [];
  let left = room;
  for (const chip of chips) {
    const cells = oneChipCells(chip);
    if (cells > left) continue;
    kept.push(chip);
    left -= cells;
  }
  return kept;
}

/** Cells the left segment keeps before the chip row may take the rest: the
 * identity head ("acct@project", "? · signed-out") — the agents count and
 * the work line's tail yield to the chips. A cut text ends in "…", so the
 * floor keeps one cell for it past the head (the ADR-104 one-badge mid left
 * room for sbx:off at 80 cols, and "signed-out" read "signed-ou…"). */
function leftFloor(text: string): number {
  const head = text.split(" · agents ")[0] ?? text;
  const ellipsis = text.length > head.length ? 1 : 0;
  return Math.min(text.length, Math.max(12, head.length + ellipsis));
}

/** CPU · RAM · host for the footer (the agents:0 lead is noise there). */
function obsLabel(resources: ResourceSnapshot): string {
  const text = formatObservabilitySummary({ agentTree: null, resources });
  return text === "agents:0" ? "" : text.replace(/^agents:0 · /, "");
}

/** The CPU/RAM/host chip sized to `room` cells: whole, then without the
 * hostname, else dropped — the lowest-value chip yields first. */
function observabilityChip(resources: ResourceSnapshot | null, room: number): Chip | null {
  if (!resources) return null;
  const fits = (label: string) => label !== "" && label.length + 2 <= room;
  const label = [obsLabel(resources), obsLabel({ ...resources, hostname: "" })].find(fits);
  return label ? { label, state: "dim" } : null;
}

/** The active session's chips: the remote runtime + lease countdown (Wave
 * 2026-09-21, ADR-046 A2; hidden for local sessions) and the workflow arm.
 * Never fabricated. The sandbox chip is not here: it rides after the
 * mid-turn identity chip (rightChips). */
function sessionChips(): Chip[] {
  const chips: Chip[] = [];
  const venueChip = formatVenueChip(activeTab().venueRuntime);
  if (venueChip) chips.push(venueChip);
  if (activeTab().workflowArmed) chips.push({ label: "wf:on", state: "warn" });
  return chips;
}

/** Circle chip fragment: first two refs + overflow count ("" when none). */
function formatCircleChip(refs: string[]): string {
  if (!refs.length) return "";
  const shown = refs.slice(0, 2).join(",");
  const more = refs.length > 2 ? `+${refs.length - 2}` : "";
  return ` ○${shown}${more}`;
}

/** Team chip: armed marker, then first running teamup session's live summary. */
function formatTeamChip(): { label: string; state: StatusChipState } | null {
  if (workspace.teamupArmed) {
    const width = workspace.teamupArmedParallel;
    const plan = workspace.teamupArmedPlan;
    return {
      label: plan
        ? `team:on · plan:${plan.template}`
        : `team:on · ${workspace.teamupArmedCrew}${width > 1 ? ` ×${width}` : ""}`,
      state: "warn",
    };
  }
  const running = teamsStore.sessions.find((row) => row.status === "running");
  if (!running) return null;
  const summary = summarizeRing(teamsStore.live[running.session_id] ?? []);
  const active = summary?.activeRole ? ` ${summary.activeRole}⣾` : "";
  return {
    label: truncateSmart(`team ${running.name}${active}`, 24),
    state: "info",
  };
}

export function StatusBar() {
  const dims = useTerminalDims();
  // Claude-Code parity /statusline: a user command's first stdout line as a
  // chip, refreshed every 5 s (no file = no chip, no process spawned).
  const [userLine, setUserLine] = createSignal("");
  onMount(() => {
    // Async (r2 #70): the synchronous run blocked the whole UI for up to
    // the command's timeout every 5 s.
    const refresh = () => {
      void refreshStatusline({
        model: activeTab().currentModel ?? "",
        project: activeTab().identityProjectId || "",
        session: activeTab().sessionId || "",
        permission: activeTab().permissionMode,
      }).then(setUserLine);
    };
    refresh();
    const id = setInterval(refresh, 5000);
    onCleanup(() => clearInterval(id));
  });
  // ADR-086 identity chip: the caller's circles, refreshed on a slow
  // cadence (membership change lands ≤ this tick; failures keep the last
  // known refs, never a fabricated empty). Static imports: a dynamic
  // import evaluated the real jsonl-client before mocked suites could
  // intercept it (bun mock.module ordering, found 2026-10-05).
  onMount(() => {
    const refreshCircles = async () => {
      try {
        if (isOfflineBackend()) return;
        // never touch the real bridge before the health flow proved a live
        // gateway — a blind mount-time request would spawn/poison the bridge
        // singleton in offline and test contexts (found 2026-10-05)
        if (workspace.connection.gateway !== "connected") return;
        // circles.list is a read: since plan H-8 (2026-10-18) the bridge
        // answers reads mid-turn, so no turn guard is needed here.
        const payload = (await getCompanionBridge().request("circles.list", {}, 10_000)) as {
          circles?: Array<Record<string, unknown>>;
        };
        const rows = Array.isArray(payload.circles) ? payload.circles : [];
        actions.setIdentityCircles(
          rows
            .map((r) => `${String(r.project_id ?? "")}/${String(r.circle_id ?? "")}`)
            .filter((r) => !r.startsWith("/") && !r.endsWith("/")),
        );
      } catch {
        // offline or bridge hiccup — keep the last known refs
      }
    };
    void refreshCircles();
    const id = setInterval(() => void refreshCircles(), 60_000);
    onCleanup(() => clearInterval(id));
    // fire as soon as the gateway flips live (no ≤60 s dead window after login)
    createEffect(() => {
      if (workspace.connection.gateway === "connected") void refreshCircles();
    });
  });
  // The web chip's search backend (ADR-102): read only once a bridge
  // session exists (never spawn the bridge from the bar), then every 30 s
  // — /search-server refreshes it at once after a start or stop.
  onMount(() => {
    const refreshSearch = () => {
      if (!isOfflineBackend() && activeTab().sessionId) void refreshSearchServerStatus();
    };
    const id = setInterval(refreshSearch, 30_000);
    onCleanup(() => clearInterval(id));
    createEffect(() => {
      if (activeTab().sessionId) {
        refreshSearch();
        // ADR-108: a session launched in a worktree (r4 --worktree) shows its chip.
        if (!isOfflineBackend()) void syncWorktreeFromBridge();
      }
    });
  });
  // Segment caps track the real terminal — the bar is full-width, so fixed
  // 14/40-col caps wasted most of a wide terminal (and crushed narrow ones).
  const leftCap = () => Math.max(40, Math.floor(dims().width * 0.38));
  // mid never shrinks in layout, so its cap also stays inside the bar.
  const midCap = () =>
    Math.min(Math.max(40, Math.floor(dims().width * 0.32)), Math.max(16, dims().width - 4));
  const modelCap = () => Math.max(14, Math.floor(dims().width * 0.18));
  const agentCount = () => {
    const runtimeActive = agentRuntime.agents.filter(
      (a) => a.state === "running" || a.state === "planning" || a.state === "waiting",
    ).length;
    const runtimeTotal = agentRuntime.agents.length;
    if (runtimeTotal > 0) return { active: runtimeActive, total: runtimeTotal };
    const wsActive = workspace.agents.filter((a) => a.status !== "idle").length;
    return { active: wsActive, total: workspace.agents.length };
  };
  const unread = () => workspace.unreadNotifications;
  const modelLabel = () => {
    const current = activeTab().currentModel;
    if (current) return truncate(current, modelCap());
    const ready = workspace.endpoints.find((ep) => ep.status === "ready");
    if (ready?.model) return truncate(ready.model, modelCap());
    return "none";
  };

  // Idle left segment (no live turn): subagent activity first, then
  // acct/project. The LIVE turn state is owned by the animated work
  // indicator below — the one place turn status renders (deduplicated from
  // the chat transcript row + input placeholder, 2026-10).
  const idleLeft = () => {
    const activeAgents = agentRuntime.agents.filter(
      (a) => a.state === "running" || a.state === "planning" || a.state === "waiting",
    );
    if (activeAgents.length > 0) {
      const first = activeAgents[0]!;
      const detail = first.currentTask ?? first.currentObjective ?? first.state;
      const extra = activeAgents.length > 1 ? ` · +${activeAgents.length - 1}` : "";
      return truncate(`● ${first.role}: ${detail}${extra}`, leftCap());
    }
    // Wave E: acct/project anchors the left segment when no turn is live;
    // authz flags ride as ⚠︎ until the audit is clean (space of last resort).
    // ADR-086: account@project + the caller's circles ride the same chip.
    const authz = activeTab().authzFlags > 0 ? ` ⚠︎${activeTab().authzFlags}` : "";
    return truncate(
      `${identityChip()}${authz} · agents ${agentCount().active}/${agentCount().total}`,
      leftCap(),
    );
  };
  // `{account}@{project} ○{project/circle}[ +N]` with a JWT; without one the
  // credential state is named explicitly instead of masquerading the profile
  // as an account (maintainer 2026-10-07: "default/{project}" was too
  // ambiguous — it hid that nobody is logged in). Old bridges lack the
  // credential field → last-ditch legacy shape (stale bridge only).
  const identityChip = () => {
    const project = activeTab().identityProjectId || "?";
    const who = workspace.identityUsername
      ? `${workspace.identityUsername}@${project}`
      : activeTab().identityCredential === "api-key"
        ? `${project} · key-only(${activeTab().identityProfile})`
        : activeTab().identityCredential === "none"
          ? `${project} · signed-out`
          : `${activeTab().identityProfile}/${project}`;
    const circles = workspace.identityCircles;
    if (circles.length === 0) return who;
    const more = circles.length > 1 ? ` +${circles.length - 1}` : "";
    return `${who} ○${circles[0]}${more}`;
  };
  // Volume context for the work line: completed tool calls this turn +
  // active/total agents (type of work rides in the status text itself).
  const workCounts = () => ({
    tools: activeTab().turnToolActivity.length,
    agentsActive: agentCount().active,
    agentsTotal: agentCount().total,
  });
  const mid = () => {
    // ADR-104 D-1: one mode, one badge (autopilot names its trust source,
    // ADR-035 badge honesty).
    const badge = modeBadge(activeTab().interactionMode, activeTab().trustSource);
    const sheetNote =
      workspace.paneView !== null && paneLayout(dims().width) === "sheet"
        ? " · pane: sheet"
        : "";
    // The model name yields to the cap, never the mode (stabilization
    // 2026-10-21: a tail cut dropped the badge first).
    const room = Math.max(8, midCap() - badge.length - sheetNote.length - 3);
    return `${truncate(modelLabel(), room)} · ${badge}${sheetNote}`;
  };
  /** The live work line; an armed Esc chord leads it (lib/esc-interrupt:
   * the composer's placeholder is hidden while a draft is typed). */
  const workText = () => {
    const line = workStatusLine(agentPhase(), workCounts());
    const armed = escInterruptHint();
    return armed ? `${armed} · ${line}` : line;
  };
  /** Cells the left segment asks for (idle text, or the work indicator). */
  const leftText = () => (agentPhase() === "idle" ? idleLeft() : truncate(workText(), leftCap() - 2));
  /** Bar cells for the three segments: width − 2 padding − 2 gaps. */
  const barCells = () => dims().width - 4;

  // V2/G4: right side as a chip row — state edges readable at a glance,
  // divisions shown by the chip boxes themselves instead of hand-woven gaps.
  const rightChips = () => {
    const companion = agentRuntime.agents.find((a) => a.role === "companion");
    const chips = connectionChips(unread());
    // Venue · workflow arm, in that order (sessionChips).
    chips.push(...sessionChips());
    // Team visibility (2026-09-27): one chip while a teamup session is
    // running; hidden otherwise (never a fabricated presence).
    const teamChip = formatTeamChip();
    if (teamChip) chips.push(teamChip);
    // 2026-10-06 report: the live work indicator owns the left segment for
    // the whole turn, which hid account@project ○circle until the turn
    // ended (invisible for minutes while thinking). Mirror the identity as
    // a dim chip on the right whenever the left segment is in work mode —
    // idle keeps it left (Wave E anchor), live keeps it visible here.
    if (agentPhase() !== "idle" && activeTab().identityProjectId) {
      chips.push({ label: truncateSmart(identityChip(), 28), state: "dim" });
    }
    // ADR-101 local tool sandbox (sbx:on / sbx:off / sbx:n/a; none for a
    // frontend session or a bridge without session.sandbox). After the
    // mid-turn identity: fitChips keeps greedily in order, so sbx yields to
    // the identity on a tight bar (tui-lane review 2026-10-30).
    const sbxChip = sandboxChip(activeTab().sandbox, activeTab().sessionVenue);
    if (sbxChip) chips.push(sbxChip);
    // ADR-108 D-2: the worktree this tab works in (/worktree).
    const wtChip = worktreeChip(activeTab().worktree);
    if (wtChip) chips.push(wtChip);
    // Workflow badge (2026-10-24): live UMRS executions + the latest one's
    // step progress, read off the store's own poll; hidden at zero and
    // below WORKFLOW_BADGE_MIN_COLS. Alt+W opens its live DAG. After the
    // team and mid-turn identity chips: fitChips keeps greedily in order,
    // so the badge yields to them on a tight bar (2026-10-24 review).
    const wfBadge = workflowBadgeLabel(dims().width);
    if (wfBadge) chips.push({ label: wfBadge, state: "info", cells: workflowBadgeCells(wfBadge) });
    void companion;
    if (userLine()) chips.unshift({ label: userLine(), state: "info" });
    // Host CPU/RAM/name rides last and only in the room left after the
    // user-critical segments (stabilization 2026-10-21: at ≤120 cols it kept
    // full width while model · mode and the identity were crushed).
    // Every chip is whole or gone (w3-tui 2026-10-21: the engine's shrink
    // clipped labels mid-word and garbled the identity segment).
    // Web access + search backend (maintainer 2026-11-03: "visible
    // always"): reserved first, so fitChips never drops it.
    const web = webStatusChip(activeTab().webAccess, searchServerBackend(), activeTab().sessionVenue);
    const room = barCells() - mid().length - leftFloor(leftText()) - oneChipCells(web);
    const fitted = fitChips(chips, room);
    const kept = [web, ...fitted];
    const obs = observabilityChip(workspace.resources, room - chipCells(fitted) - (leftText().length - leftFloor(leftText())));
    if (obs) kept.push(obs);
    return kept;
  };
  /** The left segment gets what the mid and the chips leave, ellipsized. */
  const leftRoom = () => Math.max(1, barCells() - mid().length - chipCells(rightChips()));

  return (
    <box
      width="100%"
      height={1}
      backgroundColor={palette.bgPanel}
      flexDirection="row"
      paddingLeft={1}
      paddingRight={1}
      overflow="hidden"
      gap={1}
    >
      <box flexGrow={1} overflow="hidden">
        <Show
          when={agentPhase() !== "idle"}
          fallback={
            <text fg={palette.fgDim} wrapMode="none">
              {truncate(idleLeft(), leftRoom())}
            </text>
          }
        >
          {/* The single animated work indicator: phase spinner + status
              (names the running tool / phase) + tool & agent counts +
              elapsed seconds with hue escalation. */}
          <box flexDirection="row" overflow="hidden" gap={1}>
            <PhaseGlyph phase={agentPhase() as Exclude<AgentPhase, "idle">} />
            <text fg={pulseFgForElapsed()} wrapMode="none">
              {truncate(workText(), Math.max(8, Math.min(leftCap(), leftRoom()) - 2))}
            </text>
          </box>
        </Show>
      </box>
      {/* model · mode never shrinks; the chip row gives way first
          (shrink weight 8 vs the identity segment's 1), clipped at its end. */}
      <box flexGrow={1} flexShrink={0} overflow="hidden">
        <text fg={palette.fgDim} wrapMode="none">
          {mid()}
        </text>
      </box>
      <box flexDirection="row" gap={0} flexShrink={0} overflow="hidden">
        <For each={rightChips()}>
          {(chip) => (
            <box flexShrink={0}>
              <StatusChip label={chip.label} state={chip.state} />
            </box>
          )}
        </For>
      </box>
    </box>
  );
}
