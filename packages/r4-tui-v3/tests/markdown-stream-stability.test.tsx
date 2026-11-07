/**
 * Streaming markdown stability contract (heading flash regression).
 *
 * While a message streams, a COMPLETED block (e.g. a `##` heading) must be
 * its own renderable and must survive later appends untouched. With
 * "coalesced" block mode the whole message is ONE unstable block that is
 * re-rendered on every chunk — finished heading lines repaint each token
 * and visibly flash for as long as generation continues. MarkdownBlock
 * therefore streams with internalBlockMode="top-level".
 */
import { test, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { createSignal } from "solid-js";
import type { MarkdownRenderable } from "@opentui/core";
import { markdownSyntaxStyle, STREAMING_BLOCK_MODE } from "../src/ui/MarkdownBlock";

test("completed heading block survives later streamed appends (no repaint)", async () => {
  const [content, setContent] = createSignal("intro line\n\n## Heading Title\n");
  let md: MarkdownRenderable | undefined;
  const setup = await testRender(
    () => (
      <markdown
        ref={(r: MarkdownRenderable) => (md = r)}
        content={content()}
        streaming={true}
        internalBlockMode={STREAMING_BLOCK_MODE}
        syntaxStyle={markdownSyntaxStyle()}
      />
    ),
    { width: 60, height: 20 },
  );
  await setup.renderOnce();
  const statesBefore = (md!._blockStates ?? []).map((b) => ({
    type: b.token.type,
    renderable: b.renderable,
  }));
  const heading = statesBefore.find((b) => b.type === "heading");
  // The heading IS a real heading block, not coalesced into a paragraph.
  expect(heading).toBeDefined();

  // Generation continues below the finished heading.
  setContent("intro line\n\n## Heading Title\n\nnext paragraph grows here\n");
  await setup.renderOnce();
  const statesAfter = (md!._blockStates ?? []).map((b) => ({
    type: b.token.type,
    renderable: b.renderable,
  }));
  const headingAfter = statesAfter.find((b) => b.type === "heading");

  // Same renderable instance → the heading row is not re-created/repainted
  // while the stream continues below it.
  expect(headingAfter?.renderable).toBe(heading!.renderable);
  setup.renderer.destroy();
});

test("streaming still renders all blocks (no content loss)", async () => {
  const [content, setContent] = createSignal("intro\n\n## Title\n");
  const setup = await testRender(
    () => (
      <markdown
        content={content()}
        streaming={true}
        internalBlockMode={STREAMING_BLOCK_MODE}
        syntaxStyle={markdownSyntaxStyle()}
      />
    ),
    { width: 60, height: 20 },
  );
  await setup.renderOnce();
  setContent("intro\n\n## Title\n\ntrailing paragraph");
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  expect(frame).toContain("intro");
  // Streaming headings render with concealed markers ("Title", not
  // "## Title") — the per-line finalized path keeps the raw markers, so
  // they reappear on finalize (same as before the block-mode change).
  expect(frame).toContain("Title");
  expect(frame).toContain("trailing paragraph");
  setup.renderer.destroy();
});
