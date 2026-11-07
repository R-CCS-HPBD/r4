import { describe, expect, it } from "bun:test";
import { companionUnavailableReply, generateLocalReply } from "../src/lib/chat-reply";

describe("chat fallback copy", () => {
  it("does not tell live users to connect r4 CLI", () => {
    const reply = companionUnavailableReply("ECONNREFUSED");
    expect(reply).toContain("[Chat unavailable]");
    expect(reply).toContain("Companion");
    expect(reply.toLowerCase()).not.toContain("connect the r4 cli backend");
  });

  it("blames gateway/TLS when SSL handshake times out", () => {
    const reply = companionUnavailableReply(
      "Request timed out: _ssl.c:983: The handshake operation timed out.",
    );
    expect(reply).toContain("LLM/gateway network failed");
    expect(reply).toContain("Companion is running");
    expect(reply.toLowerCase()).not.toContain("check companion is installed");
    expect(reply).toMatch(/r4 doctor|r4 health|vpn/i);
    // Generic copy (2026-11-07): no site host names a public or stand-alone
    // user cannot act on.
    expect(reply).toContain("r4 source list");
    expect(reply.toLowerCase()).not.toMatch(/rikyu|data0/);
  });

  it("blames the LLM provider on HTTP 5xx, not the bridge", () => {
    for (const detail of ["API error: 502", "API error: 504: upstream timed out", "HTTP 503"]) {
      const reply = companionUnavailableReply(detail);
      expect(reply).toContain("[Chat unavailable]");
      expect(reply).toContain("LLM provider returned a server error");
      expect(reply).toContain("bridge and Companion are fine");
      expect(reply).toContain(detail);
      expect(reply.toLowerCase()).not.toContain("bridge failed");
      expect(reply.toLowerCase()).not.toContain("check companion is installed");
      expect(reply).toMatch(/\/model|r4 health|r4 endpoint list/);
    }
  });

  // Stabilization sweep 2026-10-21, chat#10: the copy claimed a retry it
  // could not know about (the streamed 500 had been sent once).
  it("makes no retry claim on a provider 5xx", () => {
    expect(companionUnavailableReply("API error: 500: fake upstream exploded")).not.toContain("retried");
  });

  // chat#7: httpx RemoteProtocolError mid-stream read "bridge failed … is
  // companion installed? … r4 console --offline".
  it("names a provider stream dropped mid-reply, not the bridge", () => {
    const reply = companionUnavailableReply(
      "peer closed connection without sending complete message body (incomplete chunked read)",
    );
    expect(reply).toContain("closed the stream mid-reply");
    expect(reply).toContain("incomplete");
    expect(reply.toLowerCase()).not.toContain("bridge failed");
    expect(reply).not.toContain("r4 console --offline");
  });

  // chat#12: the client's own read timeout was called "server-side".
  it("names the client read timeout, not a server-side timeout", () => {
    const reply = companionUnavailableReply("Request timed out: timed out.");
    expect(reply).toContain("read timeout");
    expect(reply).toContain("R4_TIMEOUT");
    expect(reply).not.toContain("server-side");
    expect(reply).not.toContain("next time");
  });

  it("does not reclassify non-5xx API errors as provider failures", () => {
    const reply = companionUnavailableReply("API error: 401: unauthorized");
    expect(reply).toContain("Research Companion bridge failed");
    expect(reply).not.toContain("LLM provider returned a server error");
  });

  it("network branch still wins over provider spelling absence", () => {
    const reply = companionUnavailableReply("connect timeout to https://api.rikyu.example/v1");
    expect(reply).toContain("LLM/gateway network failed");
    expect(reply).not.toContain("LLM provider returned a server error");
  });

  it("offline hey reply mentions Companion, not CLI install", () => {
    const reply = generateLocalReply("hey", 0, "offline");
    expect(reply.startsWith("[Offline]")).toBe(true);
    expect(reply).toMatch(/Companion|demo\/fixture/i);
    expect(reply.toLowerCase()).not.toContain("connect the r4 cli backend");
  });

  // c2-tui (2026-10-21, PTY journey j11): the bridge process died mid-reply
  // (killed); the copy said "bridge failed … Check companion is installed …
  // or use r4 console --offline" although the next message respawned it and
  // chat worked again. Say what happened and that a resend restarts it.
  it("a bridge that exited mid-turn: reply incomplete, the next message restarts it", () => {
    for (const detail of ["bridge exited", "bridge exited: Killed"]) {
      const reply = companionUnavailableReply(detail);
      expect(reply).toContain("[Chat unavailable]");
      expect(reply).toContain("exited during this turn");
      expect(reply).toContain("incomplete");
      expect(reply).toContain("next message starts a new bridge");
      expect(reply).not.toContain("r4 console --offline");
    }
  });
});
