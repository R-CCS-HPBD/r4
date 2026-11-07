/**
 * A chat tab's title (ADR-103 VP-3b), as the console's tab strip shows a
 * tab: its session's name, `●` while a turn runs, `!` when something waits
 * for you in a background tab. Long names are cut at 28 characters (the
 * console's cell width). Pure.
 */

export type TabMark = "" | "●" | "!";

export const TAB_TITLE_MAX = 28;

/** The server's create-time placeholder (`Session YYYY-MM-DD HH:MM`) is no name. */
const PLACEHOLDER_RE = /^Session \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/;

export function tabTitle(name: string, mark: TabMark): string {
  const clean = name.trim() && !PLACEHOLDER_RE.test(name.trim()) ? name.trim() : "R4 chat";
  const cut = clean.length > TAB_TITLE_MAX ? `${clean.slice(0, TAB_TITLE_MAX - 1)}…` : clean;
  return mark ? `${mark} ${cut}` : cut;
}
