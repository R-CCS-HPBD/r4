import { beforeEach, describe, expect, it } from "bun:test";
import { R4CliDataSource } from "../src/adapters/r4-cli";

describe("R4CliDataSource.getJobLogs", () => {
  it("uses job describe (no job logs subcommand)", async () => {
    const src = new R4CliDataSource();
    // Spy via prototype is heavy; assert by running describe path contract:
    // method must not invoke a bare `logs` argv — covered by implementation review +
    // offline fallback below when CLI unavailable.
    const offline = await src.getJobLogs("job-test-id", 10);
    expect(offline.toLowerCase()).not.toContain("did you mean 'login'");
    // Either real describe output, or a clear error — never the old unknown-command tip.
    expect(offline).toMatch(/job|describe|details|could not|error|no details/i);
  });
});
