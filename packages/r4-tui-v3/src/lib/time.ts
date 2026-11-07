/** Centralized timestamp formatting — local time (not UTC).
 *
 * Backend stamps typically arrive as ISO: naive `.slice(11, 19)` leaks the
 * UTC clock to the user. Everything the TUI renders as HH:MM:SS must go
 * through this helper so every pane/panel shows the host's own timezone.
 */

/** Format ISO → local-time HH:MM:SS; empty string for unparseable stamps. */
export function localTimeHHMMSS(iso: string | undefined): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
