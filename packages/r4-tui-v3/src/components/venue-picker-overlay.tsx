/**
 * Session-start venue picker (P2, plan 2026-09-09 §4.2): where should this
 * session run — Local (this machine, dies with r4) or Server/frontend
 * (platform server, survives disconnect).
 *
 * The Server option is gated on the list_sessions journal probe
 * (remote_available / remote_reason): unreachable → disabled WITH the reason
 * rendered on the row (never a dead button without a reason, §6 risk 3).
 * Choice rides the initialize RPC's additive venue param (see newSession),
 * after the web-access question (ADR-099 D-2, web-access-overlay.tsx).
 */

import { For, Show, createMemo, createSignal, onMount } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import type { KeyEvent } from "@opentui/core";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";

/** Overlay is 64% of the terminal — row caps track it (were fixed 68/72;
 * the 68 floor overran the box at 80 columns, cutting rows mid-word). */
const venueCap = () => Math.max(24, Math.floor(termWidth() * 0.64) - 2);

/** Rows the overlay needs: title + padding (2) + footer + key trap, plus
 * per option a label row and, when `full`, a consequence row and a gap. */
export function venuePickerRows(options: number, full: boolean): number {
  return 5 + options * (full ? 3 : 1);
}
import { KeyTrap } from "./key-trap";
import { handleListKey, clampIndex } from "../lib/selectable-list";
import { venuePickerOptions, type VenuePickerOption } from "../lib/session-filters";
import { probeSessionRemote } from "../bridge/session-bridge";
import { Floating } from "../ui/Floating";

export function VenuePickerOverlay() {
  const [probing, setProbing] = createSignal(true);
  const [options, setOptions] = createSignal<VenuePickerOption[]>(
    venuePickerOptions(false, "probing the session journal…"),
  );
  const [cursor, setCursor] = createSignal(0);
  const dims = useTerminalDims();
  // Sized to its content (stab-focus, 2026-10-21): the fixed 42% box held
  // ~10 rows at 80x24 for ~14 rows of content, so Yoga squeezed the option
  // boxes and their rows drew over each other. Short terminals drop the
  // consequence lines instead.
  const full = () => venuePickerRows(options().length, true) <= dims().height - 2;
  const boxRows = () => Math.min(venuePickerRows(options().length, full()), Math.max(1, dims().height));
  const boxTop = () => Math.max(0, Math.min(Math.floor(dims().height * 0.24), dims().height - boxRows()));

  onMount(() => {
    void (async () => {
      // T5 topic-25: probe deadline — an un-reached lane release the picker in ≤2 s
      // (before this, the bridge could hold the STOP window forever on slow networks).
      const probe = await Promise.race<{ available: boolean; reason: string }>([
        probeSessionRemote(),
        new Promise((resolve) =>
          setTimeout(
            () => resolve({ available: false, reason: "probe timed out (2s)" }),
            2_000,
          ),
        ),
      ]);
      const next = venuePickerOptions(probe.available, probe.reason);
      setOptions(next);
      // Pre-select the in-process default when it is usable (never a
      // disabled row — that would be a pre-confirmed dead button).
      const preferred = next.findIndex(
        (o) => o.choice === workspace.lastVenueChoice && !o.disabled,
      );
      setCursor(preferred >= 0 ? preferred : next.findIndex((o) => !o.disabled));
      setProbing(false);
    })();
  });

  const title = createMemo(() =>
    probing() ? "where should this session run? (probing server…)" : "where should this session run?",
  );

  const choose = (option: VenuePickerOption | undefined) => {
    if (!option) return;
      if (option.disabled) {
        // Disabled with reason — the row shows it; the keypress re-tells it.
        const prefix =
          option.key === "c"
            ? "cluster venue"
            : option.key === "f"
              ? "server (frontend) venue"
              : "venue";
        actions.sendSystemNotice(`${prefix} ${option.disabledReason}`);
        return;
      }
    actions.setLastVenueChoice(option.choice);
    actions.closeVenuePicker();
    // ADR-099 D-2 (M-4): the user chooses the agent's web access next, for a
    // local and a remote session alike. Local: the answer rides the
    // initialize call that creates the session. Server: the question offers
    // what the caps allow, then the remote source picker (ADR-046 A2) creates
    // the session with the answer and calls session.venue_start.
    actions.openWebAccessPrompt(
      option.choice === "frontend"
        ? { purpose: "new-remote", remote: true }
        : { purpose: "new-local", remote: false, venue: option.choice },
    );
  };

  const onKey = (event: KeyEvent) => {
    const name = String(event.name ?? "").toLowerCase();
    // Direct letters choose without moving the cursor (opencode grammar).
    if (name === "l" || name === "f" || name === "c") {
      event.preventDefault();
      choose(options().find((o) => o.key === name));
      return;
    }
    handleListKey(event.name, {
      selected: cursor(),
      length: options().length,
      ctrl: false,
      onMove: (index) => {
        event.preventDefault();
        setCursor(clampIndex(index, options().length));
      },
      onEnter: (index) => {
        event.preventDefault();
        choose(options()[index]);
      },
      onEscape: () => {
        event.preventDefault();
        actions.closeVenuePicker();
      },
    });
  };

  return (
    <Floating
      position="absolute"
      width="64%"
      height={boxRows()}
      left="18%"
      top={boxTop()}
      title={title()}
      flexDirection="column"
      padding={1}
    >
      <box flexDirection="column" flexGrow={1} overflow="hidden">
        <For each={options()}>
          {(option, idx) => {
            const focused = () => idx() === cursor();
            return (
              <box flexDirection="column" flexShrink={0} marginBottom={full() ? 1 : 0}>
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={focused() ? palette.bgHover : undefined}
                  onMouseDown={() => {
                    setCursor(idx());
                    choose(option);
                  }}
                >
                  <text
                    fg={
                      option.disabled
                        ? palette.fgDim
                        : focused()
                          ? palette.fgBright
                          : palette.fg
                    }
                  >
                    {truncate(
                      `${focused() ? "▸" : " "} [${option.key}] ${option.label}${option.disabled ? `  (${option.disabledReason})` : ""}`,
                      venueCap(),
                    )}
                  </text>
                </box>
                <Show when={full()}>
                  <box height={1} width="100%" overflow="hidden" paddingLeft={4}>
                    <text fg={palette.fgDim}>{truncate(option.consequence, venueCap() - 4)}</text>
                  </box>
                </Show>
              </box>
            );
          }}
        </For>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>
          {"↑↓ select · Enter/l/f choose · Esc cancel · [c] cluster shows why (unavailable)"}
        </text>
      </box>
      <box height={1} width={1} overflow="hidden">
        <KeyTrap focused onKeyDown={onKey} />
      </box>
    </Floating>
  );
}
