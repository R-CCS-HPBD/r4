/**
 * VtScreen (the pty-live frame scraper) — pure, runs in the default suite.
 * Pins the 2026-10-18 fixes the offline journeys rely on (plan H-9).
 */

import { describe, it, expect } from "bun:test";
import { VtScreen } from "./screen";

const bytes = (text: string) => new TextEncoder().encode(text);

describe("VtScreen", () => {
  it("paints text at absolute cursor positions", () => {
    const screen = new VtScreen(3, 10);
    screen.feed("\x1b[2;3Hab\x1b[1;1Hz");
    expect(screen.dump()).toBe("z\n  ab\n");
  });

  it("applies relative moves and line / display erases", () => {
    const screen = new VtScreen(2, 10);
    screen.feed("\x1b[1;1Habcdef\x1b[1;3H\x1b[K\x1b[2;1Hxy\x1b[A\x1b[2Dq");
    expect(screen.dump()).toBe("qb\nxy");
    screen.feed("\x1b[2J");
    expect(screen.dump()).toBe("\n");
  });

  it("keeps a UTF-8 glyph split across two PTY chunks intact", () => {
    const screen = new VtScreen(1, 10);
    const glyph = bytes("▸ Jobs");
    screen.feed(glyph.slice(0, 2));
    screen.feed(glyph.slice(2));
    expect(screen.dump()).toBe("▸ Jobs");
  });

  it("swallows CSI sequences with intermediates (cursor style) whole", () => {
    const screen = new VtScreen(1, 10);
    screen.feed("\x1b[1;1H\x1b[1 qok");
    expect(screen.dump()).toBe("ok");
  });

  it("swallows OSC strings (cursor colour) whole", () => {
    const screen = new VtScreen(1, 20);
    screen.feed("\x1b[1;1H\x1b]12;#ff8800\x07ok\x1b]12;#00ff00\x1b\\!");
    expect(screen.dump()).toBe("ok!");
  });
});

describe("VtScreen wide glyphs (w3-tui item 9, 2026-10-21)", () => {
  it("a CJK glyph covers two cells, so a repaint after it lands where the terminal puts it", () => {
    const screen = new VtScreen(1, 12);
    screen.feed("\x1b[1;1H›漢字abc");
    expect(screen.dump()).toBe("›漢字abc");
    // OpenTUI's repaint after deleting 字: "abc" from column 4 (1-based), then
    // blanks over the old tail. A one-column emulator kept a stale cell.
    screen.feed("\x1b[1;4Habc  ");
    expect(screen.dump()).toBe("›漢abc");
  });

  it("an emoji (astral code point) is one glyph, two cells", () => {
    const screen = new VtScreen(1, 8);
    screen.feed("a😀b");
    expect(screen.dump()).toBe("a😀b");
    screen.feed("\x1b[1;4HZ");
    expect(screen.dump()).toBe("a😀Z");
  });
});

/**
 * Escape sequences split across PTY chunks (c2-devops, 2026-10-21). The
 * journey "approval card: the queued feedback reaches the composer" failed on
 * main 686f71c9 and 1126563d with the composer row reading "3try listing
 * instead" and a stray "[" at the right edge: a chunk ended inside
 * `ESC [ 3 8 ; 2 ; … m` (or right after a lone ESC), the emulator parsed each
 * chunk on its own, dropped `ESC [` / `ESC` and printed the tail as text.
 * OpenTUI repaints only changed cells, so the garbage over the "›" prompt cell
 * stayed. Whether it happens depends on where the kernel cuts the stream.
 */
describe("VtScreen escape sequences split across PTY chunks", () => {
  it("an SGR sequence cut after `ESC [ 3` still colours, not prints, its tail", () => {
    const screen = new VtScreen(1, 30);
    screen.feed("\x1b[1;1H\x1b[3");
    screen.feed("8;2;10;20;30m› try listing instead");
    expect(screen.dump()).toBe("› try listing instead");
  });

  it("a lone ESC at a chunk end starts the next chunk's sequence", () => {
    const screen = new VtScreen(1, 20);
    screen.feed("\x1b[1;1Hok\x1b");
    screen.feed("[1;1HX");
    expect(screen.dump()).toBe("Xk");
  });

  it("an OSC string cut before its terminator is swallowed whole", () => {
    const screen = new VtScreen(1, 20);
    screen.feed("\x1b[1;1H\x1b]12;#ff");
    screen.feed("8800\x07ok");
    expect(screen.dump()).toBe("ok");
  });

  it("any two-chunk cut of a real paint stream yields the one-chunk frame", () => {
    const stream = bytes(
      "\x1b[?25l\x1b[1;1H\x1b[38;2;120;130;140m┃\x1b[0m \x1b[1mR4\x1b[22m 17:24\x1b[2;3H" +
        "\x1b]12;#ff8800\x07\x1b[2 q› try 漢字 \u{1F600}\x1b[K\x1b[3;1H\x1b[48;5;236m[\x1b[0m",
    );
    const whole = new VtScreen(3, 30);
    whole.feed(stream);
    const expected = whole.dump();
    const broken: number[] = [];
    for (let cut = 1; cut < stream.length; cut++) {
      const screen = new VtScreen(3, 30);
      screen.feed(stream.slice(0, cut));
      screen.feed(stream.slice(cut));
      if (screen.dump() !== expected) broken.push(cut);
    }
    expect(broken).toEqual([]);
  });
});

describe("VtScreen escape sequences split across PTY chunks (c2-tui, 2026-10-21)", () => {
  // A PTY read can end in the middle of a CSI/OSC. The scraper parsed each
  // chunk on its own, swallowed the orphan "ESC [" and printed the tail as
  // text: a transcript row read "/38;2;45;212;238m" instead of "/sessions",
  // and a cursor move that lost its head painted at the wrong column.
  it("a truecolor SGR split after ESC [ paints nothing", () => {
    const screen = new VtScreen(1, 20);
    screen.feed("\x1b[1;1H/ses\x1b[");
    screen.feed("38;2;45;212;238msions");
    expect(screen.dump()).toBe("/sessions");
  });

  it("a cursor move split inside its parameters still moves the cursor", () => {
    const screen = new VtScreen(2, 12);
    screen.feed("\x1b[2;");
    screen.feed("4Hfirst");
    expect(screen.dump()).toBe("\n   first");
  });

  it("a lone ESC at a chunk end joins the next chunk", () => {
    const screen = new VtScreen(1, 12);
    screen.feed("\x1b[1;1Hab\x1b");
    screen.feed("[1;6Hcd");
    expect(screen.dump()).toBe("ab   cd");
  });

  it("an OSC split before its terminator is swallowed whole", () => {
    const screen = new VtScreen(1, 20);
    screen.feed("\x1b[1;1Hok\x1b]12;#ff");
    screen.feed("8800\x07!");
    expect(screen.dump()).toBe("ok!");
  });
});
