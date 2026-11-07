/**
 * Type declarations for the VS Code webview API.
 *
 * This file provides the `acquireVsCodeApi()` global that VS Code
 * injects into webview contexts. It is not present in regular browsers.
 */

interface VsCodeApi<T = unknown> {
  postMessage(message: T): void;
  getState(): T;
  setState(state: T): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

interface Window {
  acquireVsCodeApi(): VsCodeApi;
}
