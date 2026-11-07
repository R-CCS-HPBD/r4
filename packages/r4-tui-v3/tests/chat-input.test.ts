/**
 * Slash-line routing contract for the chat composer.
 *
 * A pasted/seeded FULL slash line ("/login <JWT>", "/source add <url>")
 * must stay in the composer so Enter dispatches it verbatim. Only a bare
 * typed "/" fragment diverts into the command palette. Regression: the
 * palette used to receive the whole line as a fuzzy query, match nothing,
 * and dead-end — a pasted JWT was never recognised as a command.
 */
import { describe, test, expect } from "bun:test";
import { shouldDivertSlashToPalette } from "../src/lib/chat-input";

describe("shouldDivertSlashToPalette", () => {
  test("diverts a bare typed slash fragment", () => {
    expect(shouldDivertSlashToPalette("/")).toBe(true);
    expect(shouldDivertSlashToPalette("/log")).toBe(true);
    expect(shouldDivertSlashToPalette("/sessions")).toBe(true);
    expect(shouldDivertSlashToPalette("//help")).toBe(true);
  });

  test("keeps full slash lines with args in the composer", () => {
    expect(shouldDivertSlashToPalette("/login eyJhbGciOiJIUzI1NiJ9.payload.sig")).toBe(false);
    expect(shouldDivertSlashToPalette("/source add https://ollama:11434")).toBe(false);
    // menu-seeded prefix "/login " (trailing space) must survive pasting.
    expect(shouldDivertSlashToPalette("/login ")).toBe(false);
    expect(shouldDivertSlashToPalette("/sessions resume abc123")).toBe(false);
  });

  test("ignores non-slash input", () => {
    expect(shouldDivertSlashToPalette("")).toBe(false);
    expect(shouldDivertSlashToPalette("hello /world")).toBe(false);
    expect(shouldDivertSlashToPalette("!ls -la")).toBe(false);
  });
});
