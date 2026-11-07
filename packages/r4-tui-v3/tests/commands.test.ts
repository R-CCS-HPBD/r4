import { describe, expect, test } from "bun:test";

import { runCommand } from "../src/commands";

describe("command dispatch", () => {
  test("reports an unknown command as an error", async () => {
    const result = await runCommand("missing:command");

    expect(result).toEqual({
      kind: "error",
      message: "Command not found: missing:command",
    });
  });
});
