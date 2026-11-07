/**
 * /acquisition argument parsing (pure).
 *
 * Session-scoped knowledge-acquisition mode — the user decides mid-session
 * whether the chat may create/extend knowledge:
 *   /acquisition                → read the current mode
 *   /acquisition open           → agent may propose/extend (approvals gate)
 *   /acquisition off            → no knowledge writes this session
 *   /acquisition extend <graph> → extend ONLY that pre-existing graph
 *     (comma-separated list pins several graphs)
 */

export type AcquisitionParse =
  | { ok: true; params: Record<string, unknown> }
  | { ok: false; error: string };

const USAGE =
  "usage: /acquisition [open|off|extend <graph>[,<graph>…]] — " +
  "session knowledge write mode";

export function parseAcquisitionArgs(raw: string): AcquisitionParse {
  const text = raw.trim();
  if (!text) return { ok: true, params: {} };
  const [verb, ...rest] = text.split(/\s+/);
  const head = verb!.toLowerCase();
  if (head === "open" || head === "on") {
    return { ok: true, params: { mode: "open" } };
  }
  if (head === "off" || head === "disable" || head === "disabled") {
    return { ok: true, params: { mode: "disabled" } };
  }
  if (head === "extend" || head === "pin" || head === "pinned") {
    const joined = rest.join(" ").trim();
    if (!joined) {
      return { ok: false, error: "usage: /acquisition extend <graph name or id>" };
    }
    const graphs = joined
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    return { ok: true, params: { mode: "pinned", graphs } };
  }
  return { ok: false, error: USAGE };
}
