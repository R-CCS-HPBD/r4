/**
 * Tool → icon/label mapping for agent-work cards (wave T3-ui S3).
 *
 * Single source of truth: every agent tool call shown in the webview maps
 * through here. A category drives both the glyph and the accessible label;
 * unknown tools ALWAYS fall back to the default entry — never an exception.
 *
 * Icons are declared as inline-SVG specs, not emoji, so they inherit colour
 * from currentColor, scale on hi-dpi, and don't vary with the OS emoji set.
 * `d` is a single-path silhouette; the accessible name comes from `label`.
 */

export type ToolCategory =
  | 'read'
  | 'search'
  | 'write'
  | 'exec'
  | 'test'
  | 'web'
  | 'mcp'
  | 'default';

export interface ToolIconSpec {
  category: ToolCategory;
  /** Accessible name (never localise inline — pass through i18n at render). */
  label: string;
  /** Single-path 24×24 silhouette for the glyph. */
  d: string;
}

const ICONS: Record<ToolCategory, ToolIconSpec> = {
  read: {
    category: 'read',
    label: 'read',
    d: 'M4 4h10l6 6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm10 0v6h6',
  },
  search: {
    category: 'search',
    label: 'search',
    d: 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm11 18-6-6',
  },
  write: {
    category: 'write',
    label: 'write',
    d: 'M12 20h9M16 3l5 5L7.5 21.5 2 22l0.5-5.5L16 3z',
  },
  exec: {
    category: 'exec',
    label: 'exec',
    d: 'M4 4l8 8-8 8M12 20h8',
  },
  test: {
    category: 'test',
    label: 'test',
    d: 'M6 2v6l8 8 8-8V2M4 22h16',
  },
  web: {
    category: 'web',
    label: 'web',
    d: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 4 3 14 0 18M12 3c-3 4-3 14 0 18',
  },
  mcp: {
    category: 'mcp',
    label: 'mcp',
    d: 'M12 2l3 3-3 3-3-3zm0 8l3 3-3 3-3-3zm0 8l3 3-3 3-3-3z',
  },
  default: {
    category: 'default',
    label: 'tool',
    d: 'M14.7 6.3a5 5 0 0 0-6.6 6.6L2 19l3 3 6.1-6.1a5 5 0 0 0 6.6-6.6l-3.4 3.4-3-3 3.4-3.4z',
  },
};

const READ_TOOLS = new Set(['lab_file_read', 'file_read', 'read_text_file', 'list_dir']);
const WRITE_TOOLS = new Set([
  'file_write',
  'file_edit',
  'file_patch',
  'write_text_file',
  'create_file',
  'apply_patch',
  'edit_files',
]);
const EXEC_TOOLS = new Set(['shell_exec', 'python_exec', 'nodejs_exec', 'hpc_submit']);
const TEST_TOOLS = new Set(['test_code', 'compile_code', 'run_code']);
const WEB_TOOLS = new Set(['web_search', 'web_fetch', 'web_api', 'web_screenshot']);

/** Resolve the icon spec for one agent tool call. */
export function iconForTool(name: string): ToolIconSpec {
  if (READ_TOOLS.has(name)) return ICONS.read;
  if (WRITE_TOOLS.has(name)) return ICONS.write;
  if (EXEC_TOOLS.has(name)) return ICONS.exec;
  if (TEST_TOOLS.has(name)) return ICONS.test;
  if (WEB_TOOLS.has(name)) return ICONS.web;
  if (name.startsWith('mcp_') || name.startsWith('mcp:')) return ICONS.mcp;
  if (name.endsWith('_search')) return ICONS.search;
  return ICONS.default;
}
