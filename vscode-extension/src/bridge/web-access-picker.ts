/**
 * The web-access question in VS Code (ADR-099 D-2, ADR-103 VP-3a): a
 * QuickPick of Off / Allowlist / Public with the console's wording, and an
 * input box for the allowlist's domains. Nothing is applied without an
 * answer; Esc backs out (null).
 */

import * as vscode from "vscode";

import {
  validateWebChoice,
  WEB_MODE_TEXT,
  webPromptDefaults,
  type WebAccessChoice,
  type WebCaps,
  type WebChoice,
  type WebMode,
} from "../../../packages/r4-tui-v3/src/lib/web-access";

interface ModeItem extends vscode.QuickPickItem {
  mode: WebMode;
}

function modeItems(caps: WebCaps, current: WebAccessChoice | null): ModeItem[] {
  return caps.allow.map((mode) => ({
    mode,
    label: WEB_MODE_TEXT[mode].label,
    description: WEB_MODE_TEXT[mode].consequence,
    picked: current?.mode === mode,
  }));
}

/** The allowlist's domains, checked by the console's own rule (at least one,
 * well-formed, inside the caps). */
async function askDomains(current: WebAccessChoice | null, caps: WebCaps): Promise<WebChoice | null> {
  const { domainsText } = webPromptDefaults(current, null, caps);
  const answer = await vscode.window.showInputBox({
    title: "Web access: allowed domains",
    prompt: "Space- or comma-separated (.a.org includes its subdomains)",
    value: domainsText,
    ignoreFocusOut: true,
    validateInput: (text) => validateWebChoice("allowlist", text, caps).error ?? undefined,
  });
  if (answer === undefined) return null;
  return validateWebChoice("allowlist", answer, caps).choice;
}

export async function pickWebAccess(ask: {
  current: WebAccessChoice | null;
  caps: WebCaps;
  note: string;
}): Promise<WebChoice | null> {
  const items = modeItems(ask.caps, ask.current);
  const picked = await vscode.window.showQuickPick(items, {
    title: "The agent's web access for this session",
    placeHolder: ask.note,
    ignoreFocusOut: true,
  });
  if (!picked) return null;
  if (picked.mode !== "allowlist") return { mode: picked.mode, domains: [] };
  return askDomains(ask.current, ask.caps);
}
