/**
 * System tab — skills (loaded/not), MCP servers, slash-command catalog.
 *
 * Fetched through the ACP bridge (skills/list, mcp/servers+tools,
 * commands/list) — the same surfaces the companion terminal UI shows.
 * Each slice fails independently: when the gateway has no MCP control
 * plane (public sources) the other cards still render.
 */

import { useEffect, useMemo, useState, type ReactElement } from 'react';
import {
  checkMcpHealth,
  refreshSystem,
  setSkillActive,
} from '@/lib/actions';
import { useApp } from '@/state/store';
import type { MCPServerInfo, SkillEntry } from '@/types/protocol';

// ── Skills ─────────────────────────────────────────────────────────────────

function skillStatePill(skill: SkillEntry): ReactElement {
  return skill.active ? (
    <span className="r4-system-pill r4-system-pill-active">loaded</span>
  ) : (
    <span className="r4-system-pill">not loaded</span>
  );
}

/** Tooltip for the load/unload toggle button. */
function skillToggleTitle(bound: boolean, skill: SkillEntry): string {
  if (!bound) return 'Skill toggles need a live chat session';
  return skill.active
    ? `Unload ${skill.name} from this session`
    : `Load ${skill.name} into this session`;
}

/** One skill row: name + source + state pill + load/unload toggle. */
function SkillRow(props: {
  skill: SkillEntry;
  bound: boolean;
  busy: boolean;
  onToggle: (skill: SkillEntry) => void;
}): ReactElement {
  const { skill, bound, busy, onToggle } = props;
  return (
    <li
      className="r4-studio-list-item"
      title={`${skill.description ?? skill.name}\nTools: ${(skill.tools ?? []).join(', ') || '—'}`}
    >
      <span className="r4-studio-list-title">
        {skill.name}
        <span className="r4-studio-hint"> · {skill.source ?? 'builtin'}</span>
      </span>
      {skillStatePill(skill)}
      <button
        type="button"
        className={`r4-btn${skill.active ? ' r4-btn-active' : ''}`}
        disabled={!bound || busy}
        aria-pressed={skill.active}
        title={skillToggleTitle(bound, skill)}
        onClick={() => onToggle(skill)}
      >
        {skill.active ? 'unload' : 'load'}
      </button>
    </li>
  );
}

function SkillsCard(): ReactElement {
  const { system } = useApp();
  const [busyName, setBusyName] = useState<string | null>(null);
  const bound = system.sessionBound && typeof system.skillsSessionId === 'string';

  const onToggle = (skill: SkillEntry): void => {
    if (!bound || system.skillsSessionId == null) return;
    setBusyName(skill.name);
    setSkillActive(system.skillsSessionId, skill.name, !skill.active);
    // The systemState reply clears the busy marker (list re-renders anyway).
    setTimeout(() => setBusyName(null), 1500);
  };

  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>Skills ({system.skills.filter((s) => s.active).length}/{system.skills.length} loaded)</span>
        <button type="button" className="r4-btn" title="Reload skills" onClick={refreshSystem}>
          ⟳
        </button>
      </div>
      {!bound && (
        <div className="r4-studio-empty">
          Start a chat session to load/unload skills. Listing shows available skills
          (none loaded until a session binds).
        </div>
      )}
      {system.errors.skills && (
        <div className="r4-studio-error">skills: {system.errors.skills}</div>
      )}
      <ul className="r4-studio-list" aria-label="Skills">
        {system.skills.map((skill) => (
          <SkillRow
            key={skill.name}
            skill={skill}
            bound={bound}
            busy={busyName === skill.name}
            onToggle={onToggle}
          />
        ))}
        {system.loadedOnce && system.skills.length === 0 && !system.errors.skills && (
          <li className="r4-studio-empty">No skills discovered</li>
        )}
      </ul>
    </section>
  );
}

// ── MCP servers ────────────────────────────────────────────────────────────

function serverKey(server: MCPServerInfo): string {
  return server.server_id ?? server.id ?? server.name;
}

function serverStatusTone(status: string): string {
  const tone = status.toLowerCase();
  if (tone === 'healthy' || tone === 'ok' || tone === 'up') return 'completed';
  if (tone === 'unhealthy' || tone === 'down' || tone === 'error') return 'failed';
  return 'pending';
}

/** Expanded section: server URL + exposed tool list. */
function McpServerTools(props: {
  server: MCPServerInfo;
  tools: Array<{ name: string; description?: string }>;
}): ReactElement {
  const { server, tools } = props;
  return (
    <div className="r4-system-tools">
      {server.url && <div className="r4-studio-hint">{server.url}</div>}
      {tools.length === 0 && (
        <div className="r4-studio-hint">No tools exposed by this server</div>
      )}
      <ul className="r4-system-tool-list">
        {tools.map((tool) => (
          <li key={tool.name} title={tool.description ?? tool.name}>
            <code>{tool.name}</code>
            {tool.description && <span className="r4-studio-hint"> — {tool.description}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One MCP server row: name/status, health trigger, expandable tool list. */
function McpServerRow(props: {
  server: MCPServerInfo;
  expanded: boolean;
  tools: Array<{ name: string; description?: string }>;
  healthBusy: boolean;
  onToggleExpand: (key: string) => void;
  onHealth: (server: MCPServerInfo) => void;
}): ReactElement {
  const { server, expanded, tools, healthBusy, onToggleExpand, onHealth } = props;
  const key = serverKey(server);
  const status = String(server.status ?? 'unknown');
  const toggle = (): void => onToggleExpand(key);
  return (
    <li className="r4-studio-list-item r4-system-server-item">
      <span
        className="r4-studio-list-title"
        title={server.description ?? server.url ?? server.name}
        onClick={toggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggle();
          }
        }}
      >
        {server.name}
        <span className="r4-studio-hint"> · {tools.length} tools</span>
      </span>
      <span className={`r4-studio-status r4-studio-status-${serverStatusTone(status)}`}>
        {status}
      </span>
      {(server.server_id ?? server.id) && (
        <button
          type="button"
          className="r4-btn"
          disabled={healthBusy}
          title="Trigger a health check"
          onClick={() => onHealth(server)}
        >
          {healthBusy ? 'checking…' : 'health'}
        </button>
      )}
      {expanded && <McpServerTools server={server} tools={tools} />}
    </li>
  );
}

function McpCard(): ReactElement {
  const { system } = useApp();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [healthBusy, setHealthBusy] = useState<string | null>(null);

  const toolsByServer = useMemo(() => {
    const map = new Map<string, typeof system.mcpTools>();
    for (const tool of system.mcpTools) {
      const key = tool.server ?? '';
      const arr = map.get(key) ?? [];
      arr.push(tool);
      map.set(key, arr);
    }
    return map;
  }, [system.mcpTools]);

  const onHealth = (server: MCPServerInfo): void => {
    const id = server.server_id ?? server.id ?? '';
    if (!id || healthBusy) return;
    setHealthBusy(id);
    checkMcpHealth(id);
  };

  // Health replies land as a fresh systemState → clear the busy marker.
  useEffect(() => {
    setHealthBusy(null);
  }, [system.mcpServers]);

  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>MCP servers ({system.mcpServers.length})</span>
        <button type="button" className="r4-btn" title="Reload MCP servers" onClick={refreshSystem}>
          ⟳
        </button>
      </div>
      {system.errors.mcpServers && (
        <div className="r4-studio-empty" title={system.errors.mcpServers}>
          MCP server list unavailable (public sources have no MCP control plane).
        </div>
      )}
      {!system.errors.mcpServers && system.loadedOnce && system.mcpServers.length === 0 && (
        <div className="r4-studio-empty">No MCP servers registered</div>
      )}
      <ul className="r4-studio-list" aria-label="MCP servers">
        {system.mcpServers.map((server) => (
          <McpServerRow
            key={serverKey(server)}
            server={server}
            expanded={expandedId === serverKey(server)}
            tools={toolsByServer.get(server.name) ?? []}
            healthBusy={healthBusy !== null}
            onToggleExpand={(key) => setExpandedId(expandedId === key ? null : key)}
            onHealth={onHealth}
          />
        ))}
      </ul>
    </section>
  );
}

// ── Slash-command catalog ─────────────────────────────────────────────────

function CommandsCard(): ReactElement {
  const { system } = useApp();
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
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>Commands</span>
      </div>
      <div className="r4-studio-hint">
        Slash commands run in the companion terminal UI (<code>r4 chat</code>); chat
        here accepts them as natural text. Buttons in this panel and Studio drive the
        same operations directly.
      </div>
      {system.errors.commands && (
        <div className="r4-studio-error">commands: {system.errors.commands}</div>
      )}
      {grouped.map(([category, commands]) => (
        <div key={category} className="r4-system-command-group">
          <div className="r4-studio-key">{category}</div>
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
    </section>
  );
}

// ── Tab root ───────────────────────────────────────────────────────────────

export default function SystemTab(): ReactElement {
  const { system } = useApp();

  useEffect(() => {
    refreshSystem();
  }, []);

  return (
    <div className="r4-studio r4-system-tab">
      <div className="r4-system-columns">
        <div className="r4-system-column">
          <SkillsCard />
        </div>
        <div className="r4-system-column">
          <McpCard />
          <CommandsCard />
        </div>
      </div>
      {!system.loadedOnce && <div className="r4-studio-empty">Loading system state…</div>}
    </div>
  );
}
