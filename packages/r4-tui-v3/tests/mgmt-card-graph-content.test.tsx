/**
 * TUI audit 2026-10-17 (#54) — the knowledge-graph-content card renders the
 * graph's nodes, edge count and owner instead of the generic JSON fallback.
 */
import { test, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { MgmtCardView } from "../src/components/mgmt-card";

test("knowledge-graph-content renders header and node rows", async () => {
  const card = {
    v: 1,
    type: "knowledge-graph-content",
    payload: {
      graph_id: "g-1",
      name: "mof-lit",
      kind: "literature",
      owner_id: "alice",
      nodes: [{ id: "n1", label: "MOF-5", type: "material" }],
      edges: [{ from: "n1", to: "n1" }],
      truncated: true,
    },
  };
  const setup = await testRender(() => <MgmtCardView card={card as never} />, { width: 100, height: 20 });
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  expect(frame).toContain("knowledge graph content");
  expect(frame).toContain("mof-lit");
  expect(frame).toContain("alice");
  expect(frame).toContain("1 nodes, 1 edges (truncated)");
  expect(frame).toContain("MOF-5");
  expect(frame).toContain("[material]");
  expect(frame).not.toContain("☰");
  setup.renderer.destroy();
});
