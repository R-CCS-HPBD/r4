/**
 * Help overlay — what the user can do from this UI.
 *
 * Two halves: (1) the interactive surfaces (tabs, buttons, per-panel
 * actions); (2) the slash-command catalog the companion surfaces share,
 * so a user moving between the webview and `r4 chat` sees both halves of
 * the command story in one place.
 */

import { useEffect, useMemo, type ReactElement } from 'react';
import { refreshSystem } from '@/lib/actions';
import { useApp } from '@/state/store';

/** Static summary of the interactive surfaces (kept in sync with components). */
const UI_ACTIONS: Array<{ area: string; actions: Array<[string, string]> }> = [
  {
    area: 'Chat tab',
    actions: [
      ['New chat', 'Open a fresh agent session'],
      ['Mode (Ask / Plan / Auto)', 'How tool calls get approved: ask each, plan first, or auto-approve'],
      ['◇ KG chip', 'Open the project knowledge graph (pulses while knowledge builds)'],
      ['LOOP chip', 'One or more research loops are active — open their panel'],
    ],
  },
  {
    area: 'Studio → Executions',
    actions: [
      ['Select a workflow', 'Preview its step DAG as a planned lane before running'],
      ['Run workflow', 'Start an execution — it appears live with per-step status'],
      ['Click a node', 'Inspect it; cancel the run, or approve/reject a waiting gate'],
    ],
  },
  {
    area: 'Studio → Knowledge & Loops',
    actions: [
      ['Feed data into knowledge', 'Pick files or add search terms; the graph grows below'],
      ['+/− per graph', 'Include or exclude knowledge graphs the agent may use'],
      ['Start a research loop', 'Choose venue + agentic mode; the card shows iterations'],
      ['Stop', 'Halt a running or paused loop (idempotent)'],
    ],
  },
  {
    area: 'System tab',
    actions: [
      ['Skills', 'See which skills are loaded into the session; load/unload them'],
      ['MCP servers', 'List registered tool servers, expand their tools, run health checks'],
      ['Commands', 'Slash-command catalog shared with the companion terminal'],
    ],
  },
  {
    area: 'Chat composer',
    actions: [
      ['/… text', 'Slash commands execute in the companion terminal (r4 chat); here they are sent as natural language'],
    ],
  },
];

export default function HelpOverlay(props: { onClose: () => void }): ReactElement {
  const { system } = useApp();

  // The catalog may not be loaded yet (System tab not opened) — fetch it.
  useEffect(() => {
    if (!system.loadedOnce) refreshSystem();
  }, [system.loadedOnce]);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof system.commands>();
    for (const command of system.commands) {
      const arr = map.get(command.category) ?? [];
      arr.push(command);
      map.set(command.category, arr);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [system.commands]);

  return (
    <div className="r4-help-backdrop" role="presentation" onClick={props.onClose}>
      <div
        className="r4-help-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Help — what can I do here?"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="r4-help-head">
          <strong>What can I do here?</strong>
          <button
            type="button"
            className="r4-btn"
            onClick={props.onClose}
            title="Close help"
            aria-label="Close help"
          >
            ✕
          </button>
        </div>
        <div className="r4-help-body">
          {UI_ACTIONS.map((section) => (
            <div key={section.area} className="r4-system-command-group">
              <div className="r4-studio-key">{section.area}</div>
              <ul className="r4-system-tool-list">
                {section.actions.map(([label, description]) => (
                  <li key={label}>
                    <strong>{label}</strong>
                    <span className="r4-studio-hint"> — {description}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {grouped.length > 0 && (
            <div className="r4-system-command-group">
              <div className="r4-studio-key">slash commands (companion parity)</div>
              {grouped.map(([category, commands]) => (
                <div key={category}>
                  <div className="r4-studio-hint">{category}</div>
                  <ul className="r4-system-tool-list">
                    {commands.map((command) => (
                      <li key={command.name}>
                        <code>/{command.name}</code>
                        <span className="r4-studio-hint"> — {command.help}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
