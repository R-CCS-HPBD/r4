/**
 * B3 — clipboard write for message copy.
 * OSC52 reaches most modern terminals; on macOS also pbcopy (Terminal.app
 * ignores OSC52). Best-effort: never throws.
 */

import { spawnSync } from "node:child_process";

export function osc52Sequence(text: string): string {
  const b64 = Buffer.from(text, "utf8").toString("base64");
  // OSC 52 ; c ; <base64> BEL — clipboard selection.
  return (`\x1b]52;c;${b64}\x07`);
}

export function copyText(text: string): { ok: boolean; via: string; error?: string } {
  // 1) OSC52 — most terminal emulators (iTerm2, kitty, alacritty, wezterm…).
  try {
    process.stdout.write(osc52Sequence(text));
  } catch {
    // stdout may be unavailable in tests; continue to pbcopy
  }
  // 2) pbcopy on macOS (Terminal.app has no OSC52).
  if (process.platform === "darwin") {
    const res = spawnSync("pbcopy", { input: text });
    if (res.error) {
      return { ok: false, via: "osc52", error: String(res.error) };
    }
    return { ok: true, via: "pbcopy" };
  }
  return { ok: true, via: "osc52" };
}
