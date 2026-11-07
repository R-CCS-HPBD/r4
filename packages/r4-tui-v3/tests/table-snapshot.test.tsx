import { describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";

const TABLE_MD = [
  "| Name   | Status |",
  "| ------ | ------ |",
  "| umrs   | ready  |",
  "| code   | ready  |",
].join("\n");

describe("MarkdownBlock table rendering (finalized content)", () => {
  test("pipe table renders as bordered grid, not raw pipes", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content={TABLE_MD} streaming={false} />,
      { width: 60, height: 12 },
    );
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("┌");
    expect(frame).toContain("┼");
    expect(frame).toContain("└");
    expect(frame).toContain("umrs");
    expect(frame).toContain("ready");
    expect(frame).not.toContain("| ------ |");
    setup.renderer.destroy();
  });

  test("plain text content without pipes renders normally", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content={"alfa beta\ngamma"} streaming={false} />,
      { width: 60, height: 6 },
    );
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("alfa beta");
    expect(frame).toContain("gamma");
    expect(frame).not.toContain("┼");
    setup.renderer.destroy();
  });
});
