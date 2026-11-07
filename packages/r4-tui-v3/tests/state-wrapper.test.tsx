import { describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { StateWrapper } from "../src/components/state-wrapper";

describe("StateWrapper", () => {
  it("renders children in live state", async () => {
    const setup = await testRender(
      () => (
        <box>
          <StateWrapper state="live" emptyMessage="empty" title="TestPanel">
            <text>content</text>
          </StateWrapper>
        </box>
      ),
      { width: 60, height: 12 },
    );
    // two render passes settle reactive child anchors shared across suites
    await setup.renderOnce();
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("TestPanel");
    expect(frame).toContain("LIVE");
    expect(frame).toContain("content");
    setup.renderer.destroy();
  });
});

describe("demo visuals boot flags", () => {
  it("enables demo visuals for --demo", async () => {
    const { resolveBootFlags } = await import("../src/lib/console-mode");
    expect(resolveBootFlags(["--demo"]).demoVisuals).toBe(true);
  });

  it("keeps demo visuals off by default", async () => {
    const { resolveBootFlags } = await import("../src/lib/console-mode");
    const prev = process.env["R4_TUI_BACKEND"];
    const prevDemo = process.env["R4_TUI_DEMO"];
    delete process.env["R4_TUI_BACKEND"];
    delete process.env["R4_TUI_DEMO"];
    expect(resolveBootFlags([]).demoVisuals).toBe(false);
    if (prev !== undefined) process.env["R4_TUI_BACKEND"] = prev;
    if (prevDemo !== undefined) process.env["R4_TUI_DEMO"] = prevDemo;
  });
});

describe("demo visuals boot flags", () => {
  it("enables demo visuals for --demo", async () => {
    const { resolveBootFlags } = await import("../src/lib/console-mode");
    expect(resolveBootFlags(["--demo"]).demoVisuals).toBe(true);
  });

  it("keeps demo visuals off by default", async () => {
    const { resolveBootFlags } = await import("../src/lib/console-mode");
    const prev = process.env["R4_TUI_BACKEND"];
    const prevDemo = process.env["R4_TUI_DEMO"];
    delete process.env["R4_TUI_BACKEND"];
    delete process.env["R4_TUI_DEMO"];
    expect(resolveBootFlags([]).demoVisuals).toBe(false);
    if (prev !== undefined) process.env["R4_TUI_BACKEND"] = prev;
    if (prevDemo !== undefined) process.env["R4_TUI_DEMO"] = prevDemo;
  });
});
