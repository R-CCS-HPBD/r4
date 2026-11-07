/**
 * 2026-10-01 primitive pins for the opentui 0.3.4 reconciler defect that
 * duplicated overlay rows on every arrow key: a REACTIVE StyledText child
 * (t`…` reading a signal — both the JSX-child and the children-prop form)
 * APPENDS its chunks to the text buffer on each re-render instead of
 * replacing the previous ones. <StyledTextView> routes the same content
 * through the renderable's `content` setter, which replaces wholesale.
 *
 * Row-level regression pins live in list-overlay-toggle-rows.test.tsx.
 */
import { it, expect, describe } from "bun:test";
import { testRender } from "@opentui/solid";
import { createSignal, For } from "solid-js";
import { t, fg } from "@opentui/core";
import { StyledTextView } from "../src/ui/StyledTextView";

describe("StyledTextView", () => {
  it("reactive styled text is replaced, not appended", async () => {
  const [on, setOn] = createSignal(true);
  const setup = await testRender(() => (
    <box flexDirection="column" width="100%">
      <StyledTextView text={t`m ${on() ? fg("#00ff00")("on ") : fg("#888888")("off ")}label-x`} />
    </box>
  ), { width: 80, height: 10 });
  await setup.renderOnce();
  const count = () => setup.captureCharFrame().split("label-x").length - 1;
  console.log("s0:", count());
  for (let i = 1; i <= 6; i++) {
    setOn(i % 2 === 0);
    await setup.renderOnce();
    console.log(`s${i}:`, count());
  }
  expect(count()).toBe(1);
  setup.renderer.destroy();
}, 20000);
});

describe("opentui 0.3.4 raw reconciler (defect documentation)", () => {
  it("a reactive StyledText children-prop DUPLICATES per signal change — never use it", async () => {
    const { createSignal } = await import("solid-js");
    const { t, fg } = await import("@opentui/core");
    const [on, setOn] = createSignal(true);
    const setup = await testRender(() => (
      <box flexDirection="column" width="100%">
        <text children={t`m ${on() ? fg("#00ff00")("on ") : "off "}label-y`} />
      </box>
    ), { width: 200, height: 10 });
    await setup.renderOnce();
    const count = () => setup.captureCharFrame().split("label-y").length - 1;
    expect(count()).toBe(1);
    setOn(false);
    await setup.renderOnce();
    // The defect: two copies after ONE update. If a future opentui upgrade
    // fixes the reconciler (count back to 1), StyledTextView stays correct
    // anyway — keep these pins to hear about it.
    expect(count()).toBe(2);
    setup.renderer.destroy();
  }, 20000);
});
