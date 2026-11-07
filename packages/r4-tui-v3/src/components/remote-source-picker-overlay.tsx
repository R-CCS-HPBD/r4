/**
 * Remote source picker (wave 2026-09-21, ADR-046 A2 — G-RS-20/21).
 *
 * "start" mode (from the venue picker's Server row, after the web-access
 * question): pick the pushed source the sandbox will use, then create the
 * session with the user's web-access answer and call session.venue_start.
 * A row that is not pushed yet asks once, pushes through the bridge (the key
 * never crosses the TUI), then starts. A retry reuses the session the first
 * attempt created.
 *
 * "current" mode (/venue start): the same choice, then session.venue_start
 * for THIS session with its own web-access choice — no new session (TUI
 * audit r2 #57). A session without a choice is asked first.
 *
 * The web access is shown, never cycled here (ADR-099 D-2: it was `w`
 * cycling a preselected `public`; the user now chooses it in the question).
 *
 * "manage" mode (/venue sources): push / unpush rows without starting.
 *
 * Honesty rules: rows show the server badge verbatim (pushed / stale /
 * tombstoned / not pushed / server unreachable); refusals from the server
 * (`venue.source_not_pushed`, `venue.unavailable` …) are re-told on the
 * footer with the server's own words.
 */

import { For, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { workspace, actions, activeTab, type RemoteSourcePickerMode } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";
import { KeyTrap } from "./key-trap";
import { CLAIM_RANK, claimKeys } from "../keymap/key-claims";
import { handleListKey, clampIndex } from "../lib/selectable-list";
import {
  eligibleRemoteSources,
  remoteBadge,
  type ChipState,
  type RemoteSourceRow,
} from "../lib/venue-runtime";
import { userWebChoice, webAccessLabel, type WebChoice } from "../lib/web-access";
import {
  listRemoteSources,
  pushSource,
  startRemoteSession,
  unpushSource,
  type StartRemoteResult,
} from "../bridge/venue-bridge";
import { newSession } from "../bridge/session-bridge";
import { Floating } from "../ui/Floating";

/** Session + start verbs the picker drives (injectable for tests). */
export interface RemoteStartDeps {
  newSession: (venue: "frontend", webAccess?: WebChoice) => Promise<boolean>;
  startRemoteSession: typeof startRemoteSession;
}

export type RemoteStartOutcome = { kind: "no-session" } | { kind: "started"; result: StartRemoteResult };

/**
 * The picker's start step. "start" mode creates the frontend session once —
 * a push-then-retry reuses it instead of leaving an empty session behind per
 * attempt; "current" mode starts the session already open. The new session
 * records the web-access answer (ADR-099 D-2), so its worker restores it.
 */
export function makeRemoteStarter(
  mode: () => RemoteSourcePickerMode,
  deps: RemoteStartDeps = { newSession, startRemoteSession },
): (opts: { source: string; webAccess: WebChoice }) => Promise<RemoteStartOutcome> {
  let created = false;
  return async (opts) => {
    if (mode() === "start" && !created) {
      if (!(await deps.newSession("frontend", opts.webAccess))) return { kind: "no-session" };
      created = true;
    }
    return { kind: "started", result: await deps.startRemoteSession(opts) };
  };
}

const cap = () => Math.max(60, Math.floor(termWidth() * 0.7) - 8);

const BADGE_COLOR: Record<ChipState, () => string> = {
  ok: () => palette.statusOk,
  warn: () => palette.statusWarn,
  error: () => palette.statusErr,
  info: () => palette.accent,
  dim: () => palette.fgDim,
};

function RemoteSourceRowView(props: { row: RemoteSourceRow; focused: boolean; onPick: () => void }) {
  const badge = () => remoteBadge(props.row);
  const provider = () => props.row.provider ?? "openai-compat";
  return (
    <box flexDirection="column" marginBottom={0}>
      <box
        height={1}
        width="100%"
        overflow="hidden"
        backgroundColor={props.focused ? palette.bgHover : undefined}
        onMouseDown={props.onPick}
      >
        <text fg={props.focused ? palette.fgBright : palette.fg}>
          {truncate(`${props.focused ? "▸" : " "} ${props.row.name} · ${provider()} · `, cap() - 24)}
        </text>
        <text fg={BADGE_COLOR[badge().state]()}>{badge().glyph}</text>
      </box>
      <box height={1} width="100%" overflow="hidden" paddingLeft={4}>
        <text fg={palette.fgDim}>{truncate(props.row.url, cap() - 4)}</text>
      </box>
    </box>
  );
}

export function RemoteSourcePickerOverlay() {
  const mode = () => workspace.remoteSourcePickerMode;
  /** Both start modes pick a source and start; only "manage" does not. */
  const starting = () => mode() !== "manage";
  const startRemote = makeRemoteStarter(mode);
  const [loading, setLoading] = createSignal(true);
  const [rows, setRows] = createSignal<RemoteSourceRow[]>([]);
  const [remoteAvailable, setRemoteAvailable] = createSignal(true);
  const [cursor, setCursor] = createSignal(0);
  const [busy, setBusy] = createSignal<string | null>(null);
  const [footer, setFooter] = createSignal<string | null>(null);
  /** The answer this start uses: the question's (new session) or the
   * session's own choice (/venue start); null = not chosen yet. */
  const webAccess = (): WebChoice | null =>
    mode() === "start" ? workspace.pendingRemoteWebAccess : userWebChoice(activeTab().webAccess);
  /** Row name awaiting a push confirmation (second Enter confirms). */
  const [confirmPush, setConfirmPush] = createSignal<string | null>(null);
  // Round 2: a pending push confirm owns Esc (first Esc clears it, the
  // second closes) — the global sweep yields to this trap meanwhile (plan
  // H-6: ranked just above the picker's own store layer).
  onCleanup(
    claimKeys("remote-source-picker-confirm", ["escape"], () => confirmPush() !== null, {
      rank: CLAIM_RANK.remotePickerConfirm,
    }),
  );

  const refresh = async () => {
    setLoading(true);
    try {
      const listed = await listRemoteSources();
      setRows(eligibleRemoteSources(listed.rows));
      setRemoteAvailable(listed.remoteAvailable);
      setCursor((c) => clampIndex(c, Math.max(1, eligibleRemoteSources(listed.rows).length)));
    } catch (err) {
      setFooter(`source list failed: ${err instanceof Error ? err.message : String(err)}`);
      setRows([]);
    } finally {
      setLoading(false);
    }
  };

  onMount(() => {
    void refresh();
  });

  const title = createMemo(() =>
    starting()
      ? "remote session — which pushed source should the sandbox use?"
      : "pushed sources — p push · u unpush",
  );

  const selected = () => rows()[cursor()];

  /** No answer yet: ask first (the question reopens this picker). */
  const askWebFirst = () => {
    actions.closeRemoteSourcePicker();
    actions.openWebAccessPrompt(
      mode() === "start" ? { purpose: "new-remote", remote: true } : { purpose: "venue-current", remote: true },
    );
  };

  const startWith = async (row: RemoteSourceRow) => {
    const web = webAccess();
    if (!web) {
      askWebFirst();
      return;
    }
    setBusy(`starting remote session with ${row.name}…`);
    setFooter(null);
    try {
      const outcome = await startRemote({ source: row.name, webAccess: web });
      if (outcome.kind === "no-session") {
        setFooter("could not create the session (see the error card)");
        return;
      }
      const result = outcome.result;
      if (result.ok) {
        actions.closeRemoteSourcePicker();
        void import("../bridge/session-bridge").then((m) => m.fetchSessions());
        return;
      }
      const error = result.error;
      if (error?.code === "source_not_pushed") {
        setFooter(`server: ${error.message} — press Enter to push '${row.name}' and retry`);
        setConfirmPush(row.name);
        await refresh();
        return;
      }
      setFooter(error ? `server refused (${error.code}): ${error.message}` : result.detail);
    } finally {
      setBusy(null);
    }
  };

  const pushThen = async (row: RemoteSourceRow, thenStart: boolean) => {
    setBusy(`pushing '${row.name}'…`);
    setConfirmPush(null);
    const pushed = await pushSource(row.name);
    setBusy(null);
    if (!pushed.ok) {
      setFooter(pushed.detail);
      return;
    }
    await refresh();
    if (thenStart) {
      const fresh = rows().find((r) => r.name === row.name) ?? row;
      await startWith(fresh);
    }
  };

  const choose = (row: RemoteSourceRow | undefined) => {
    if (!row || busy()) return;
    const badge = remoteBadge(row);
    if (mode() === "manage") {
      if (badge.needsPush) void pushThen(row, false);
      else setFooter(`'${row.name}' is ${badge.glyph} — u unpushes it`);
      return;
    }
    if (!remoteAvailable()) {
      setFooter("server unreachable — the sealed-source list did not answer; try again later");
      return;
    }
    if (badge.needsPush) {
      if (confirmPush() === row.name) {
        void pushThen(row, true);
        return;
      }
      setConfirmPush(row.name);
      setFooter(
        `'${row.name}' is ${badge.glyph}: push its key to the platform (sealed; the sandbox never sees it)? Enter again = yes · Esc = no`,
      );
      return;
    }
    void startWith(row);
  };

  const unpushSelected = () => {
    const row = selected();
    if (!row || busy()) return;
    setBusy(`unpushing '${row.name}'…`);
    void unpushSource(row.name).then(async () => {
      setBusy(null);
      await refresh();
    });
  };

  const pushSelected = () => {
    const row = selected();
    if (row && !busy()) void pushThen(row, false);
  };

  /** Letter verbs (opencode grammar): handled before list navigation. */
  const letterVerbs: Record<string, () => void> = {
    p: pushSelected,
    u: unpushSelected,
    r: () => void refresh(),
  };

  const onKey = (event: KeyEvent) => {
    const verb = letterVerbs[String(event.name ?? "").toLowerCase()];
    if (verb) {
      event.preventDefault();
      verb();
      return;
    }
    handleListKey(event.name, {
      selected: cursor(),
      length: Math.max(1, rows().length),
      ctrl: false,
      onMove: (index) => {
        event.preventDefault();
        setConfirmPush(null);
        setCursor(clampIndex(index, Math.max(1, rows().length)));
      },
      onEnter: (index) => {
        event.preventDefault();
        choose(rows()[index]);
      },
      onEscape: () => {
        event.preventDefault();
        if (confirmPush()) {
          setConfirmPush(null);
          setFooter(null);
          return;
        }
        actions.closeRemoteSourcePicker();
      },
    });
  };

  const emptyText = () =>
    loading()
      ? "loading pushed sources…"
      : "no eligible sources — add a public source with an API key first (/connect or `r4 source add … --api-key-env`)";

  const hint = () => {
    const b = busy();
    if (b) return b;
    if (footer()) return footer()!;
    const web = starting() ? ` · web: ${webAccessLabel(webAccess())}` : "";
    return `↑↓ select · Enter ${starting() ? "start" : "push"} · p push · u unpush · r refresh${web} · Esc close`;
  };

  return (
    <Floating
      position="absolute"
      width="70%"
      height="50%"
      left="15%"
      top="20%"
      title={title()}
      flexDirection="column"
      padding={1}
    >
      <box flexDirection="column" flexGrow={1} overflow="hidden">
        {rows().length === 0 ? (
          <box height={1}>
            <text fg={palette.fgDim}>{truncate(emptyText(), cap())}</text>
          </box>
        ) : (
          <For each={rows()}>
            {(row, idx) => (
              <RemoteSourceRowView
                row={row}
                focused={idx() === cursor()}
                onPick={() => {
                  setCursor(idx());
                  choose(row);
                }}
              />
            )}
          </For>
        )}
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={busy() || confirmPush() ? palette.statusWarn : palette.fgDim}>{truncate(hint(), cap())}</text>
      </box>
      <box height={1} width={1} overflow="hidden">
        <KeyTrap focused onKeyDown={onKey} />
      </box>
    </Floating>
  );
}
