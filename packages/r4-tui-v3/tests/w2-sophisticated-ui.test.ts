import { describe, expect, test } from "bun:test";
import * as splashLib from "../src/lib/splash";
import {
  LEGACY_WAVE_LOGO,
  R4_LOGO,
  SPLASH_TAGLINE,
  splashLines,
  splashLinesWithLegacy,
} from "../src/lib/splash";
import { mcpServerNoticeText, skillNoticeText } from "../src/lib/inventory-notice";
import { mergeSkillInventories } from "../src/lib/skill-inventory";

describe("splash branding", () => {
  test("logo is 6 rows of non-empty block art", () => {
    expect(R4_LOGO).toHaveLength(6);
    for (const row of R4_LOGO) {
      expect(row.trim().length).toBeGreaterThan(0);
      expect(row.length).toBeLessThanOrEqual(60);
    }
  });

  test("splash lines contain tagline, version, and hints", () => {
    const lines = splashLines("1.2.3");
    const joined = lines.join("\n");
    expect(joined).toContain(SPLASH_TAGLINE);
    expect(joined).toContain("v1.2.3");
    expect(joined).toContain("chat is the primary surface");
    // No function keys anywhere (F-keys are unavailable on some terminals).
    expect(joined).not.toMatch(/\bF\d+\b/);
    expect(joined).toContain("Ctrl+P command palette");
  });

  test("title screen waits for the first message — no auto-dismiss timer", () => {
    // The title screen stays until the user submits a chat (or Esc skips);
    // the lib must not export a dismiss timeout for the overlay to arm.
    expect(
      (splashLib as Record<string, unknown>).SPLASH_DISMISS_MS,
    ).toBeUndefined();
  });

  test("legacy wave art is ADDED after block logo, never replacing it", () => {
    const lines = splashLinesWithLegacy("1.0.0");
    expect(lines.slice(0, R4_LOGO.length)).toEqual(R4_LOGO);
    const waveIdx = lines.findIndex((l) => l === LEGACY_WAVE_LOGO[0]);
    expect(waveIdx).toBeGreaterThan(R4_LOGO.length);
    expect(lines.slice(waveIdx, waveIdx + LEGACY_WAVE_LOGO.length)).toEqual(
      LEGACY_WAVE_LOGO,
    );
  });
});

describe("inventory notices", () => {
  test("skill notice with description and mcp tag", () => {
    expect(
      skillNoticeText({
        name: "research",
        source: "builtin",
        hasMcp: true,
        description: "Deep research loop",
      }),
    ).toBe("skill research [builtin] · +mcp — Deep research loop");
  });

  test("skill notice without description", () => {
    expect(
      skillNoticeText({ name: "x", source: "user", hasMcp: false, description: "" }),
    ).toBe("skill x [user]");
  });

  test("mcp server notice pieces", () => {
    expect(
      mcpServerNoticeText({
        name: "platform",
        status: "healthy",
        url: "http://gw:8000",
        description: "Platform tools",
      }),
    ).toBe("mcp platform [healthy] — http://gw:8000 (Platform tools)");
    expect(mcpServerNoticeText({ server_id: "s1" })).toBe("mcp s1 [unknown]");
    expect(mcpServerNoticeText({})).toBe("mcp (unnamed) [unknown]");
  });

  test("mcp notice carries source tag + actionable hint when present", () => {
    expect(
      mcpServerNoticeText({
        name: "context7",
        status: "declared (not probed) · not agent-visible",
        source: "user-config:user",
        url: "https://mcp.context7.com/mcp",
        hint: "to use it: 1) r4-mcp remote-proxy … 2) r4 mcp register context7 …",
      }),
    ).toBe(
      "mcp context7 [declared (not probed) · not agent-visible · user-config:user]"
        + " — https://mcp.context7.com/mcp"
        + "\n  → to use it: 1) r4-mcp remote-proxy … 2) r4 mcp register context7 …",
    );
  });
});

describe("mergeSkillInventories", () => {
  const skill = (name: string, source = "builtin") => ({
    name,
    source,
    hasMcp: false,
    description: "",
  });

  test("dedupes identical names across inventories", () => {
    const out = mergeSkillInventories(
      [skill("umrs"), skill("code")],
      [skill("umrs"), skill("figure-generation", "local")],
    );
    expect(out.map((s) => s.name)).toEqual(["code", "figure-generation", "umrs"]);
  });

  test("keeps bridge-only builtins + cli-only locals together", () => {
    const out = mergeSkillInventories(
      [skill("umrs"), skill("code")],
      [skill("figure-generation", "local")],
    );
    expect(out).toHaveLength(3);
  });
});
