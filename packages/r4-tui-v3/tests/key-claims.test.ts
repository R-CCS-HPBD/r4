/**
 * Plan H-6 (2026-10-18): the key → claimants registry. Rank order, newest
 * claimant first within a rank, release, yield (no onKey), reactivity.
 * The store-owned table (keymap/store-claims.ts) is registered on import,
 * so every assertion here uses claim keys the table does not hold
 * ("ctrl+g", "approval") or checks owners by name.
 */
import { describe, it, expect, afterEach } from "bun:test";
import { createRoot, createEffect, createSignal } from "solid-js";
import {
  CLAIM_RANK,
  claimKeys,
  keyClaimants,
  keyClaimed,
  runClaim,
  topClaimant,
} from "../src/keymap/key-claims";
import { STORE_LAYERS } from "../src/keymap/store-claims";
import { overlayCloseSweep } from "../src/keymap";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";

const releases: Array<() => void> = [];

function claim(...args: Parameters<typeof claimKeys>): () => void {
  const release = claimKeys(...args);
  releases.push(release);
  return release;
}

afterEach(() => {
  for (const release of releases.splice(0)) release();
  resetWorkspace();
});

describe("claims registry", () => {
  it("a claim holds only while its predicate does", () => {
    let on = false;
    claim("t-owner", ["ctrl+g"], () => on);
    expect(keyClaimed("ctrl+g")).toBe(false);
    on = true;
    expect(keyClaimed("ctrl+g")).toBe(true);
    expect(keyClaimants("ctrl+g")).toEqual(["t-owner"]);
  });

  it("release removes the claimant; a second release is harmless", () => {
    const release = claim("t-owner", ["ctrl+g"]);
    release();
    release();
    expect(keyClaimed("ctrl+g")).toBe(false);
  });

  it("higher rank first; within a rank the newest claimant first", () => {
    claim("low", ["approval"], () => true, { rank: 1 });
    claim("old", ["approval"], () => true, { rank: 5 });
    claim("new", ["approval"], () => true, { rank: 5 });
    expect(keyClaimants("approval")).toEqual(["new", "old", "low"]);
    expect(topClaimant("approval")?.owner).toBe("new");
  });

  it("runClaim hands the key to the top claimant; no onKey means yield", () => {
    const seen: string[] = [];
    claim("handler", ["approval"], () => true, { rank: 1, onKey: (k) => (seen.push(k), true) });
    expect(runClaim("approval", "y")).toBe(true);
    expect(seen).toEqual(["y"]);
    claim("yielder", ["approval"], () => true, { rank: 2 });
    expect(runClaim("approval", "y")).toBe(false);
    expect(seen).toEqual(["y"]);
  });

  it("runClaim with no active claimant is false", () => {
    expect(runClaim("ctrl+g")).toBe(false);
  });

  it("an effect re-runs when a claim comes or goes", () => {
    const seen: boolean[] = [];
    const dispose = createRoot((d) => {
      createEffect(() => seen.push(keyClaimed("ctrl+g")));
      return d;
    });
    const release = claim("t-owner", ["ctrl+g"]);
    release();
    dispose();
    expect(seen).toEqual([false, true, false]);
  });

  it("an effect re-runs when a claim's predicate signal changes", () => {
    const [on, setOn] = createSignal(false);
    const seen: boolean[] = [];
    claim("t-owner", ["ctrl+g"], on);
    const dispose = createRoot((d) => {
      createEffect(() => seen.push(keyClaimed("ctrl+g")));
      return d;
    });
    setOn(true);
    dispose();
    expect(seen).toEqual([false, true]);
  });
});

describe("store-owned table (keymap/store-claims.ts)", () => {
  it("is declared in Esc-ladder order, highest rank first", () => {
    const ranks = STORE_LAYERS.map((layer) => layer.rank);
    expect([...ranks].sort((a, b) => b - a)).toEqual(ranks);
    expect(STORE_LAYERS.at(-1)?.owner).toBe("pane");
  });

  it("a component claim slots into the Esc ladder by rank", () => {
    actions.openPane("sessions");
    actions.openVenuePicker();
    claim("t-confirm", ["escape"], () => true, { rank: CLAIM_RANK.venuePicker + 1 });
    expect(keyClaimants("escape").slice(0, 3)).toEqual(["t-confirm", "venue-picker", "pane"]);
    expect(overlayCloseSweep()).toBe(false); // the component yields
    expect(workspace.venuePickerOpen).toBe(true);
  });

  it("the composer draft claims letters only while no pane holds the keys", () => {
    setWorkspace("chatDraft", "typing");
    expect(keyClaimants("letters")).toContain("composer-draft");
    actions.openPane("sessions");
    setWorkspace("paneFocus", true);
    expect(keyClaimants("letters")).not.toContain("composer-draft");
  });
});
