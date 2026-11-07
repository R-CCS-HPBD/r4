/**
 * Backend detection for live CLI vs offline fixtures.
 */

import { describe, it, expect, afterEach } from "bun:test";
import { offlineBannerMessage, resolveDataSource } from "../src/lib/detect-backend";

const envBackup = { ...process.env };

afterEach(() => {
  process.env = { ...envBackup };
});

describe("resolveDataSource", () => {
  it("honors explicit offline env", () => {
    process.env.R4_TUI_BACKEND = "offline";
    const sel = resolveDataSource();
    expect(sel.offline).toBe(true);
    expect(sel.source.name).toBe("offline");
    expect(sel.reason).toBe("explicit");
  });

  it("honors forced offline flag", () => {
    process.env.R4_TUI_BACKEND = "cli";
    const sel = resolveDataSource({ forceOffline: true });
    expect(sel.offline).toBe(true);
    expect(sel.source.name).toBe("offline");
    expect(sel.reason).toBe("forced");
  });

  it("selects cli when R4_TUI_BACKEND=cli and probe succeeds", () => {
    process.env.R4_TUI_BACKEND = "cli";
    const sel = resolveDataSource({
      probeCli: () => true,
      probePathR4: () => false,
    });
    expect(sel.offline).toBe(false);
    expect(sel.source.name).toBe("r4-cli");
  });

  it("falls back when R4_TUI_BACKEND=cli but r4 is missing", () => {
    process.env.R4_TUI_BACKEND = "cli";
    const sel = resolveDataSource({
      probeCli: () => false,
      probePathR4: () => false,
    });
    expect(sel.offline).toBe(true);
    expect(sel.source.name).toBe("offline");
    expect(sel.reason).toBe("cli-unavailable");
  });

  it("prefers PATH r4 before Python probe", () => {
    delete process.env.R4_TUI_BACKEND;
    const sel = resolveDataSource({
      probePathR4: () => true,
      probeCli: () => {
        throw new Error("probeCli should not run when PATH succeeds");
      },
    });
    expect(sel.offline).toBe(false);
    expect(sel.source.name).toBe("r4-cli");
  });

  it("uses Python probe when PATH r4 is absent", () => {
    delete process.env.R4_TUI_BACKEND;
    const sel = resolveDataSource({
      probePathR4: () => false,
      probeCli: () => true,
    });
    expect(sel.offline).toBe(false);
    expect(sel.source.name).toBe("r4-cli");
  });

  it("auto-falls back when neither probe succeeds", () => {
    delete process.env.R4_TUI_BACKEND;
    const sel = resolveDataSource({
      probePathR4: () => false,
      probeCli: () => false,
    });
    expect(sel.offline).toBe(true);
    expect(sel.reason).toBe("r4-unavailable");
  });
});

describe("offlineBannerMessage", () => {
  it("explains r4-unavailable fallback", () => {
    expect(offlineBannerMessage("r4-unavailable")).toContain("OFFLINE FALLBACK");
  });

  it("explains cli-unavailable fallback", () => {
    expect(offlineBannerMessage("cli-unavailable")).toContain("R4_TUI_BACKEND=cli");
  });

  it("labels explicit demo mode", () => {
    expect(offlineBannerMessage("explicit")).toContain("DEMO MODE");
  });
});
