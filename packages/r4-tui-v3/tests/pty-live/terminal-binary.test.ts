import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS } from "./driver";

const LIVE = process.env.R4_PTY_LIVE === "1";
// Relative to this file (packages/r4-tui-v3/tests/pty-live → packages/r4-tui-v3/dist-bin),
// not one developer's checkout path (2026-11-06, public export P5b-2).
const BIN_PATH = `${import.meta.dir}/../../dist-bin/r4-tui-linux-x64`;
// Binary tier: runs only with R4_PTY_LIVE=1 AND a locally built binary
// (`bun run build:bin`). Linux-x64 path matches the build script output.
const BIN_PRESENT = await Bun.file(BIN_PATH).exists();
describe.skipIf(!LIVE || !BIN_PRESENT)("compiled-binary terminal pane receipt", () => {
  it("open unfocused → click focus → echo → release → kill", async () => {
    const tui = await spawnTui({
      live: false,
      binPath: BIN_PATH,
    });
    try {
      const first = await tui.waitFor("esc to skip", 30_000);
      if (first.includes("esc to skip")) tui.send(KEYS.esc);
      await tui.waitFor("Chat with R4", 30_000);
      tui.send("/term");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const open = await tui.waitFor("Terminal · click to focus", 15_000);
      expect(open).toContain("Terminal · click to focus");
      const lines = open.split("\n");
      const row = lines.find((l) => l.includes("Terminal · click to focus"))!;
      const bar0 = row.lastIndexOf("┃", row.indexOf("Terminal") - 1);
      const rowIdx = lines.indexOf(row);
      // click inside pane
      for (const s of [`\x1b[<0;${bar0 + 9};${rowIdx + 4}M`, `\x1b[<0;${bar0 + 9};${rowIdx + 4}m`]) tui.send(s);
      const deadline = Date.now() + 8_000;
      let frame = tui.screen.dump();
      while (frame.includes("Terminal · click") && Date.now() < deadline) {
        await tui.pump(120);
        frame = tui.screen.dump();
      }
      expect(frame).not.toContain("Terminal · click");
      tui.send("clear"); tui.send(KEYS.enter); await tui.pump(300);
      tui.send("echo BIN_$((6*6))"); tui.send(KEYS.enter);
      const echoed = await tui.waitFor("BIN_36", 15_000);
      expect(echoed).toContain("BIN_36");
      // release + kill
      for (const s of ["\x1b[<0;6;6M", "\x1b[<0;6;6m"]) tui.send(s);
      await tui.waitFor("Terminal · click to focus", 8_000);
      tui.send("/term kill");
      await tui.pump(400);
      tui.send(KEYS.enter);
      expect(await tui.waitFor("terminal shell killed", 8_000)).toContain("terminal shell killed");
    } finally {
      tui.kill();
    }
  }, 120_000);
});
