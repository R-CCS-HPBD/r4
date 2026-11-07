/**
 * Resolve the console data source (live CLI vs offline fixtures).
 */

import type { DataSource } from "../adapters/types";
import { OfflineDataSource, R4CliDataSource } from "../adapters";
import { probeR4Cli } from "../adapters/r4-cli";

export type BackendSelectionReason = "explicit" | "forced" | "r4-unavailable" | "cli-unavailable";

export interface BackendSelection {
  source: DataSource;
  offline: boolean;
  reason?: BackendSelectionReason;
}

export interface ResolveDataSourceOptions {
  forceOffline?: boolean;
  /** Injectable for tests — defaults to probeR4Cli. */
  probeCli?: () => boolean;
  /** Injectable for tests — PATH `r4 --version` check. */
  probePathR4?: () => boolean;
}

function defaultProbePathR4(): boolean {
  try {
    const pathCheck = Bun.spawnSync(["r4", "--version"], { timeout: 3000 });
    return pathCheck.exitCode === 0;
  } catch {
    return false;
  }
}

function selectCliSource(): BackendSelection {
  return { source: new R4CliDataSource(), offline: false };
}

export function resolveDataSource(opts: ResolveDataSourceOptions = {}): BackendSelection {
  const probeCli = opts.probeCli ?? probeR4Cli;
  const probePath = opts.probePathR4 ?? defaultProbePathR4;

  if (opts.forceOffline) {
    return { source: new OfflineDataSource(), offline: true, reason: "forced" };
  }

  const envBackend = process.env["R4_TUI_BACKEND"];
  if (envBackend === "offline") {
    return { source: new OfflineDataSource(), offline: true, reason: "explicit" };
  }

  const cliReachable = probePath() || probeCli();

  if (envBackend === "cli") {
    if (!cliReachable) {
      return {
        source: new OfflineDataSource(),
        offline: true,
        reason: "cli-unavailable",
      };
    }
    return selectCliSource();
  }

  if (cliReachable) {
    return selectCliSource();
  }

  return {
    source: new OfflineDataSource(),
    offline: true,
    reason: "r4-unavailable",
  };
}

export function offlineBannerMessage(reason?: BackendSelectionReason): string {
  if (reason === "cli-unavailable") {
    return (
      "OFFLINE FALLBACK — R4_TUI_BACKEND=cli but r4 is not reachable. " +
      "Install r4 or fix PYTHONPATH. Using demo fixtures."
    );
  }
  if (reason === "r4-unavailable") {
    return (
      "OFFLINE FALLBACK — r4 CLI not found. Install r4 (pip install -e r4[companion]) " +
      "or set R4_TUI_BACKEND=cli. Using demo fixtures only."
    );
  }
  return "DEMO MODE";
}
