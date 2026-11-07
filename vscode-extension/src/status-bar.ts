/**
 * Status bar indicator — shows Gateway connection state.
 *
 * States:
 *   $(cloud)  R4: Connected     — Gateway reachable
 *   $(cloud-offline)  R4: Offline   — Gateway unreachable
 *   $(key)    R4: Not configured — No URL or token
 */

import * as vscode from "vscode";
import { GatewayClient } from "./gateway-client";
import { AuthManager } from "./auth";

export class StatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private timer: ReturnType<typeof setInterval> | undefined;
  private _polling = false;

  constructor(
    private readonly auth: AuthManager,
    private readonly getClient: () => GatewayClient | undefined,
  ) {
    this.item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      50,
    );
    this.item.command = "r4.login";
    this.item.show();
  }

  /** Start periodic health checks.  Idempotent — multiple calls are safe. */
  startPolling(intervalMs: number): void {
    if (this._polling) {
      return;
    }
    this.stopPolling();
    this._polling = true;
    void this.refresh();
    if (intervalMs > 0) {
      this.timer = setInterval(() => void this.refresh(), intervalMs);
    }
  }

  stopPolling(): void {
    this._polling = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }

  async refresh(): Promise<void> {
    const configured = await this.auth.isConfigured();
    if (!configured) {
      this.item.text = "$(key) R4: Not configured";
      this.item.tooltip = "Click to login to R4 Gateway";
      this.item.backgroundColor = undefined;
      this.item.command = "r4.login";
      return;
    }

    const client = this.getClient();
    if (!client) {
      this.item.text = "$(lock) R4: Authenticated";
      this.item.tooltip = "Credentials stored — Gateway client initialising…";
      this.item.backgroundColor = undefined;
      this.item.command = "r4.login";
      return;
    }

    const ok = await client.checkHealth();
    const sourceName = await this.auth.getActiveSourceName();
    const accountName = await this.auth.getAccountName();
    // Show "source:account" when both are available, else just "source".
    const idParts: string[] = [];
    if (sourceName) { idParts.push(sourceName); }
    if (accountName) { idParts.push(accountName); }
    const idSuffix = idParts.length > 0 ? ` (${idParts.join(":")})` : "";
    if (ok) {
      this.item.text = `$(cloud) R4: Connected${idSuffix}`;
      this.item.tooltip = `Connected to ${await this.auth.getGatewayUrl()}`;
      this.item.backgroundColor = undefined;
      this.item.command = "r4.switchSource";
    } else {
      this.item.text = `$(lock) R4: Authenticated${idSuffix}`;
      this.item.tooltip = `Credentials stored — cannot reach ${await this.auth.getGatewayUrl()}`;
      this.item.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.warningBackground",
      );
      this.item.command = "r4.login";
    }
  }

  dispose(): void {
    this.stopPolling();
    this.item.dispose();
  }
}
