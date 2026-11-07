/**
 * The agent webview's HTML (the bundled React app), shared by the sidebar
 * view and the chat tabs (ADR-103 VP-3b). The nonce gates both the script
 * and every message the page sends back.
 */

import * as vscode from "vscode";

export function buildWebviewHtml(webview: vscode.Webview, root: vscode.Uri, nonce: string): string {
  const base = webview.asWebviewUri(root).toString();
  const csp = [
    "default-src 'none'",
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
    `font-src ${webview.cspSource}`,
    `img-src ${webview.cspSource} data:`,
  ].join("; ");
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <link rel="stylesheet" href="${base}/assets/index.css">
</head>
<body>
  <div id="root" data-nonce="${nonce}"></div>
  <script type="module" nonce="${nonce}" src="${base}/assets/index.js"></script>
</body>
</html>`;
}

/** A fresh nonce (32 alphanumerics). */
export function createWebviewNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 32; i += 1) out += chars.charAt(Math.floor(Math.random() * chars.length));
  return out;
}
