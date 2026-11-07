import { describe, expect, test } from "bun:test";
import {
  BRIDGE_PROTOCOL_VERSION,
  checkBridgeProtocol,
} from "../src/bridge/jsonl-client";

describe("checkBridgeProtocol", () => {
  test("accepts matching integer version", () => {
    expect(() =>
      checkBridgeProtocol({ bridge_protocol: BRIDGE_PROTOCOL_VERSION }),
    ).not.toThrow();
  });

  test("accepts legacy server without version field, fires notice callback", () => {
    let noticed = false;
    expect(() =>
      checkBridgeProtocol({ session: { id: "s1" } }, () => {
        noticed = true;
      }),
    ).not.toThrow();
    expect(noticed).toBe(true);
  });

  test("rejects integer mismatch with actionable message", () => {
    expect(() =>
      checkBridgeProtocol({ bridge_protocol: BRIDGE_PROTOCOL_VERSION + 1 }),
    ).toThrow(/Bridge protocol mismatch: TUI expects v1, server speaks v2/);
  });

  test("rejects non-integer mismatched versions", () => {
    expect(() => checkBridgeProtocol({ bridge_protocol: "0.9" })).toThrow(
      /Bridge protocol mismatch/,
    );
  });
});
