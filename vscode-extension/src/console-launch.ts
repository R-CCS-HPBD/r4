/**
 * `R4: Open Console in Editor` (ADR-103, VP-0): the console itself in an
 * editor-area terminal, so every console function is reachable from VS Code
 * while the native ones land. No vscode import: the spec is unit-tested.
 */

export interface ConsoleTerminalSpec {
  name: string;
  shellPath: string;
  shellArgs: string[];
  cwd: string | undefined;
  env: Record<string, string>;
}

/** The terminal that runs `r4 console` in the workspace folder. `r4 console`
 * pins the console's workspace to its launch directory, so cwd is enough;
 * R4_TUI_WORKSPACE is set too for a console started some other way. */
export function consoleTerminalSpec(cliPath: string, workspaceFolder: string | undefined): ConsoleTerminalSpec {
  return {
    name: "R4 Console",
    shellPath: cliPath,
    shellArgs: ["console"],
    cwd: workspaceFolder,
    env: workspaceFolder ? { R4_TUI_WORKSPACE: workspaceFolder } : {},
  };
}
