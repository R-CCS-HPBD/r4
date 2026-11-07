/**
 * Stub vscode module for non-VSCode runtime test bundles.
 *
 * Several vscode-extension source files import vscode at runtime (e.g.
 * thinking.ts uses it for ChatResponseMarkdownPart, ChatResponseThinkingPart).
 * This module pre-registers a require() handler so that `require("vscode")`
 * returns a minimal stub before the test bundles load.
 *
 * Import THIS module first in test files that bundle agent-engine.ts.
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);

/** @type {Record<string, unknown>} */
const vscode = {};

// Common runtime types referenced by thinking.ts
vscode.ChatResponseMarkdownPart = class ChatResponseMarkdownPart {};
vscode.ChatResponseThinkingPart = class ChatResponseThinkingPart {};

// Register a require hook before anything that depends on vscode is loaded.
require.cache["vscode"] = {
  exports: vscode,
  id: "vscode",
  filename: "stub",
  loaded: true,
};

// Global require hook for transitive requires.
// We intercept require("vscode") in child modules.
const Module = await import("node:module");
const origResolve = require.resolve;
require.resolve = function (id, ...rest) {
  if (id === "vscode") return "vscode";
  return origResolve.call(this, id, ...rest);
};
