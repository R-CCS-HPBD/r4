/**
 * Title-screen submit routing (the `/sessions` 502 fix): text accepted
 * outside ChatPanel must follow the SAME grammar as the console input —
 * `/` dispatches through the slash router (never the LLM bridge),
 * `!` runs the local shell and relays the full result as a model turn
 * (2026-10-01), plain text becomes a model turn.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { actions, workspace, setWorkspace } from "../src/stores";

describe("submitChatText routing", () => {
  beforeEach(() => {
    setWorkspace("chatMessages", []);
    setWorkspace("helpOpen", false);
    setWorkspace("chatStreaming", false);
  });

  test("slash command dispatches through the slash router, NOT the LLM", async () => {
    // /help opens the help overlay via dispatchSlashFromChat. The dispatcher
    // echoes the command as a user line BY DESIGN (see slash-pickers.test.ts
    // "dispatchSlashFromChat echo") — the 502 bug's signature is an LLM turn
    // firing (chatStreaming), which must NOT happen here.
    await actions.submitChatText("/help");
    expect(workspace.helpOpen).toBe(true);
    expect(workspace.chatStreaming).toBe(false);
    const echo = workspace.chatMessages.find((m) => m.role === "user");
    expect(echo?.content).toBe("/help");
    actions.closeHelp();
  });

  test("unknown slash command still routes to the dispatcher (error notice, no LLM turn)", async () => {
    await actions.submitChatText("/definitely-not-a-command");
    expect(workspace.chatStreaming).toBe(false);
    const echo = workspace.chatMessages.find((m) => m.role === "user");
    expect(echo?.content).toBe("/definitely-not-a-command");
  });

  test("plain text becomes a model turn (user chat message)", async () => {
    await actions.submitChatText("hello r4");
    const users = workspace.chatMessages.filter((m) => m.role === "user");
    expect(users).toHaveLength(1);
    expect(users[0].content).toBe("hello r4");
  });

  test("empty / whitespace input is a no-op", async () => {
    await actions.submitChatText("   ");
    expect(workspace.chatMessages).toHaveLength(0);
    expect(workspace.helpOpen).toBe(false);
  });

  test("!cmd runs the local shell AND relays full output as a model turn (2026-10-01)", async () => {
    await actions.submitChatText("!echo splash-routing-probe");
    // The transcript keeps the run marker notice…
    const notices = workspace.chatMessages.filter(
      (m) => m.role !== "user" && m.content.includes("splash-routing-probe"),
    );
    expect(notices.length).toBeGreaterThan(0);
    // …and the agent receives the full result as a user turn so it responds.
    const users = workspace.chatMessages.filter((m) => m.role === "user");
    expect(users).toHaveLength(1);
    expect(users[0].content).toContain("splash-routing-probe");
    expect(users[0].content).toContain("--- stdout ---");
    expect(users[0].content).toContain("TUI's local shell");
  });

  test("!cmd relay turn never names the tab (verbs/output are not titles)", async () => {
    const titleOf = () =>
      workspace.chatTabs.find((t) => t.id === workspace.activeChatTabId)?.title;
    const before = titleOf();
    await actions.submitChatText("!echo splash-tab-probe");
    expect(titleOf()).toBe(before);
  });
});
