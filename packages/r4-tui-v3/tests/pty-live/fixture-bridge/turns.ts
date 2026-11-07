/**
 * Canned turns of the fixture bridge, chosen by the first word(s) of the
 * prompt. Each reply names a distinct marker so a journey can assert it on
 * the rendered screen; `{prior}` is the number of earlier user turns the
 * fixture session holds (it proves /clear and rewind cut the companion's
 * history, not only the transcript). The markers are fixture text; the
 * wire shapes around them are bridge_server's (server.ts).
 */

export type TurnStep =
  | { wait: number }
  | { chunk: string }
  | { round: true }
  | { thinking: string | null }
  | { approval: { tool: string; description: string } };

/** One `dispatch_slash_command` output line (bridge_server CollectingRenderer). */
export interface SlashLine {
  kind: "info" | "system" | "error";
  text: string;
}

export interface TurnScript {
  steps: TurnStep[];
  reply: string;
  citations?: Array<{ sid: string; artifact_id: string; title: string; kind: string; preview: string }>;
  /** In plan mode, leave the session awaiting plan confirmation (the plan card). */
  plan?: boolean;
}

const slowSteps = (): TurnStep[] => {
  const steps: TurnStep[] = [];
  for (let i = 1; i <= 8; i += 1) steps.push({ chunk: `slow-part-${i} ` }, { wait: 700 });
  return steps;
};

const SCRIPTS: Array<[string, () => TurnScript]> = [
  ["fx slow", () => ({ steps: slowSteps(), reply: "SLOW-TURN-DONE" })],
  [
    // A multi-line command (heredoc-style script): the card must keep its
    // key legend on screen while the command scrolls (c3-tui item 5).
    "fx approve long",
    () => ({
      steps: [
        {
          approval: {
            tool: "shell_exec",
            description: `Run shell command: ${Array.from({ length: 18 }, (_, i) => `echo long-cmd-line-${i + 1}`).join("\n")}`,
          },
        },
      ],
      reply: "APPROVAL-LONG-END",
    }),
  ],
  [
    "fx approve",
    () => ({
      // bridge_server._on_tool_approval: shell_exec asks with the
      // registry's "Run shell command: <cmd>" description.
      steps: [{ approval: { tool: "shell_exec", description: "Run shell command: echo fixture-approval-probe" } }],
      reply: "APPROVAL-TURN-END",
    }),
  ],
  [
    "fx plan",
    () => ({
      steps: [{ chunk: "Proposed plan: 1) read the data 2) fit the model. " }],
      reply: "PLAN-PROPOSED",
      plan: true,
    }),
  ],
  [
    "fx think",
    () => ({
      steps: [{ thinking: "weighing-fixture-hypotheses " }, { wait: 300 }, { thinking: null }],
      reply: "THOUGHT-TURN-DONE [S1]",
      citations: [
        {
          sid: "S1",
          artifact_id: "fx-artifact-1",
          title: "Fixture Source Paper",
          kind: "passage",
          preview: "fixture passage preview",
        },
      ],
    }),
  ],
  // Answered by the fake remote worker when the session is a frontend one
  // (R4_PTY_FIXTURE_VENUE=frontend); steps never run there.
  ["fx remote", () => ({ steps: [], reply: "REMOTE-TURN-DONE" })],
];

export function scriptFor(text: string): TurnScript {
  const found = SCRIPTS.find(([prefix]) => text.trim().startsWith(prefix));
  if (found) return found[1]();
  return { steps: [{ wait: 150 }], reply: "FIXTURE-REPLY (prior turns: {prior})" };
}

/**
 * Registry names the fixture answers (a subset of the companion's
 * COMPANION_COMMANDS, r4/src/r4/companion/ui/command_bridge.py). Any other
 * name answers `handled: false`, as dispatch_shared_command does for a name
 * it does not know.
 */
const FORWARDED = new Set(["/export", "/artifact", "/skills", "/models"]);

/** dispatch_slash_command replies for catalog commands other than /clear:
 * the line is echoed back verbatim so a journey proves it arrived intact. */
export function slashReply(command: string): { handled: boolean; output: SlashLine[] } {
  const name = command.trim().split(/\s+/)[0] ?? "";
  if (!FORWARDED.has(name)) return { handled: false, output: [] };
  return { handled: true, output: [{ kind: "system", text: `fixture companion ran: ${command.trim()}` }] };
}
