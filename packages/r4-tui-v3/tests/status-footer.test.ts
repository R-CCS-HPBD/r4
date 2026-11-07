import { describe, expect, it } from "bun:test";
import { formatStatusFooter } from "../src/lib/status-footer";

const base = {
  cwd: "/Users/x/workspace/RCCS/ai4s-platform",
  gw: "connected",
  umrs: "connected",
  hpc: "ok",
  mcp: null,
  perms: null,
};

describe("D3 status footer", () => {
  it("renders all segments in order", () => {
    expect(formatStatusFooter(base)).toBe(
      "ai4s-platform · gw ok · umrs ok · hpc ok · lsp none · mcp —",
    );
  });

  it("marks connecting/error/down states compactly", () => {
    const line = formatStatusFooter({ ...base, gw: "connecting", umrs: "error", hpc: "down" });
    expect(line).toContain("gw …");
    expect(line).toContain("umrs err");
    expect(line).toContain("hpc err");
  });

  it("renders mcp ok/error when reported", () => {
    expect(formatStatusFooter({ ...base, mcp: "ok" })).toContain("mcp ok");
    expect(formatStatusFooter({ ...base, mcp: "error" })).toContain("mcp error");
  });

  it("omits the perm segment entirely when no triple is reported", () => {
    // The mid bar carries permission MODE; "perm —" was a decorative stub.
    expect(formatStatusFooter({ ...base, perms: null })).not.toContain("perm");
    expect(formatStatusFooter({ ...base, perms: null })).not.toContain("— ·");
  });

  it("compresses the permission triple", () => {
    expect(
      formatStatusFooter({ ...base, perms: { egress: "controlled", filesystem: "read_only", compute: "none" } }),
    ).toContain("perm ctl/ro/no");
    expect(
      formatStatusFooter({ ...base, perms: { egress: "open", filesystem: "read_write", compute: "gpu" } }),
    ).toContain("perm open/rw/yes");
  });

  it("uses the cwd basename and keeps a bare root", () => {
    expect(formatStatusFooter({ ...base, cwd: "/" })).toContain("· gw");
    expect(formatStatusFooter({ ...base, cwd: "/" }).startsWith("/ · gw")).toBe(true);
    expect(formatStatusFooter({ ...base, cwd: "/a/b/" }).startsWith("b · ")).toBe(true);
  });
});
