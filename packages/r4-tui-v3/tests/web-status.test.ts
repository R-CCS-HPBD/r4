/**
 * The always-on web chip (maintainer 2026-11-03: "web search and web search
 * server availability should be visible always").
 */

import { describe, expect, it } from "bun:test";
import { searchBackend, webStatusChip } from "../src/lib/web-status";

const status = (over: Record<string, unknown> = {}) => ({
  state: "absent",
  url: "",
  engine: "podman",
  starting: false,
  external_url: "",
  ...over,
});

describe("searchBackend", () => {
  it("maps search_server.status; R4_WEB_SEARCH_URL counts as up", () => {
    expect(searchBackend(null)).toBe("unknown");
    expect(searchBackend(status())).toBe("none");
    expect(searchBackend(status({ state: "running" }))).toBe("up");
    expect(searchBackend(status({ state: "down" }))).toBe("down");
    expect(searchBackend(status({ starting: true }))).toBe("starting");
    expect(searchBackend(status({ external_url: "https://searx.example.org" }))).toBe("up");
  });

  it("an older bridge without external_url still maps", () => {
    expect(searchBackend({ state: "running", url: "u", engine: "", starting: false })).toBe("up");
  });
});

describe("webStatusChip", () => {
  it("local session: the web choice and the search backend", () => {
    expect(webStatusChip({ mode: "public" }, "up", "local")).toEqual({ label: "web:public srch:up", state: "ok" });
    expect(webStatusChip({ mode: "public" }, "none", "local")).toEqual({ label: "web:public srch:none", state: "warn" });
    expect(webStatusChip({ mode: "allowlist" }, "down", "local")).toEqual({ label: "web:allow srch:down", state: "info" });
    expect(webStatusChip({ mode: "none" }, "up", "local")).toEqual({ label: "web:off srch:up", state: "dim" });
  });

  it("not chosen yet (or no session) still shows a chip", () => {
    expect(webStatusChip(null, "unknown", undefined).label).toBe("web:? srch:?");
    expect(webStatusChip({ mode: "" }, "starting", "local").label).toBe("web:? srch:…");
  });

  it("a frontend session searches on the server: the choice alone", () => {
    expect(webStatusChip({ mode: "public" }, "none", "frontend")).toEqual({ label: "web:public", state: "ok" });
  });
});
