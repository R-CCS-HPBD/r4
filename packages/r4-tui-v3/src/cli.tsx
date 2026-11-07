#!/usr/bin/env bun
/**
 * CLI entry point.
 */

import { render } from "@opentui/solid";
import { App } from "./app";
import { resetWorkspace } from "./stores";
import { resolveBootFlags, setDemoVisuals } from "./lib/console-mode";
import pkg from "../package.json" with { type: "json" };

const HELP = `r4-console-v3 — R4 Research Operations Console

Usage: r4-console-v3 [options]

Options:
  --demo         Enable demo visuals and fixture data (offline exploration)
  --smoke        Initialize and exit without rendering (CI/self-test)
  --version, -v  Print version and exit
  --help, -h     Show this help and exit

Environment:
  R4_TUI_BACKEND=offline|cli   Force a data source (default: probe for r4 CLI)
  R4_TUI_DEMO=1                Same as --demo (demo visuals + fixtures)
  R4_TUI_PYTHON=<path>         Python with \`r4\` installed (set automatically by \`r4 console\`)
`;

export async function main(argv: string[] = []): Promise<void> {
  if (argv.includes("--help") || argv.includes("-h")) {
    console.log(HELP);
    return;
  }
  if (argv.includes("--version") || argv.includes("-v")) {
    console.log(pkg.version);
    return;
  }
  const boot = resolveBootFlags(argv);
  if (boot.demoVisuals) {
    process.env["R4_TUI_DEMO"] = "1";
  }
  const smoke = argv.includes("--smoke");
  resetWorkspace();
  if (smoke) return;
  setDemoVisuals(boot.demoVisuals);
  // Title screen is the front door in EVERY mode (offline demo chat accepts
  // the first message too). Headless tests render <App/> without chatFirst,
  // so store defaults stay off for them.
  // exitOnCtrlC off: Ctrl+C interrupts the turn / clears the draft and only
  // a double press exits (Claude-Code parity; the keymap owns Ctrl+C).
  render(
    () => (
      <App
        forceOffline={boot.forceOffline}
        demoVisuals={boot.demoVisuals}
        chatFirst={true}
        fresh={boot.fresh}
      />
    ),
    {
      exitOnCtrlC: false,
      // Multiline composer needs REAL Shift+Enter / Alt+Enter delivery — the
      // kitty keyboard protocol is what disambiguates them from plain Enter
      // (terminals without kitty support still get Ctrl+J as the fallback).
      useKittyKeyboard: { disambiguate: true, alternateKeys: true },
    },
  );
}

if (import.meta.main) {
  main(process.argv.slice(2)).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
