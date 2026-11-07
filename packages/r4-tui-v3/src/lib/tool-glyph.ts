/**
 * v3-side per-tool glyph table (B1 wave). Mirrors the tui-js registry so both
 * TUIs show identical icons for the same tools; keep additions in lockstep.
 * Synced to the full tui-js registry 2026-09-01 (OpenTUI convergence W1b).
 */
const TOOL_GLYPHS: Record<string, string> = {
  // file access
  file_read: "◫",
  file_write: "✎",
  file_edit: "✎~",
  list_dir: "▤",
  detect_code_context: "◐",
  // search
  glob_search: "◎",
  grep_search: "☰",
  grep: "☰",
  search_agentic: "☰+",
  web_search: "⌁",
  // execution
  shell_exec: "$",
  python_exec: "🐍",
  compile_code: "⚙",
  run_code: "▶",
  test_code: "✓",
  hpc_submit: "⇑",
  hpc_job_status: "◔",
  // platform
  platform_health: "♥",
  platform_status: "♥",
  endpoint_provision: "≋",
  endpoint_list: "≋",
  endpoint_status: "≋",
  endpoint_stop: "≋✕",
  pool_up: "≋↑",
  pool_down: "≋↓",
  // workflows / research
  workflow_upload: "⛁",
  workflow_run: "⤳",
  workflow_status: "◔",
  workflow_list: "▦",
  workflow_describe: "𝒊",
  workflow_results: "▣",
  workflow_cancel: "◼",
  cancel: "◼",
  job_list: "▦",
  job_status: "◔",
  project_run: "⤳",
  project_plan: "🧭",
  research_loop_start: "⥁",
  research_loop_stop: "⥁✕",
  // knowledge + rag
  knowledge_ingest: "◉+",
  knowledge_status: "◉",
  knowledge_query: "◉?",
  lab_catalog_search: "⌘",
  lab_file_read: "◫📦",
  rag_query: "📚",
  rag_upload: "📚⬆",
  // models / sources / mcp
  list_models: "▦",
  model_list_registry: "▦",
  model_stage: "⇓",
  source_list: "⌂",
  source_add: "⌂+",
  mcp_list_tools: "⌁",
  mcp_list_servers: "⌁",
  mcp_invoke: "⌁→",
  // ui meta
  tool_disabled: "⯃",
  agent_spawn: "♢",
};

/** Glyph for a tool name (empty string when no dedicated one exists). */
export function toolGlyph(name: string): string {
  return TOOL_GLYPHS[name] ?? "";
}

/** Category-key colorizer per tool family (T5 topic-17 limb B): bridges chips
 * decode which class of agent action is running by color alone. */
const TOOL_CATEGORY_FG: Record<string, string | undefined> = {
  shell_exec: "statusInfo",
  python_exec: "knowledge",
  file_read: "fgDim",
  file_write: "accent",
  file_edit: "accent",
  glob_search: "dashboard",
  grep_search: "dashboard",
  web_search: "statusInfo",
};

/** fg slot-name for a tool name; undefined → caller default. Never recasts text. */
export function toolFamilyFgKey(name: string): string | undefined {
  return TOOL_CATEGORY_FG[name];
}

/** Iconized label: glyph + text (glyph skipped when unknown). */
export function iconizeToolLabel(name: string, text: string): string {
  const glyph = toolGlyph(name);
  return glyph ? `${glyph} ${text}` : text;
}

/**
 * Deny recognizer aligned with the contract funnel (union of the tui-js
 * contract patterns and the broader v3 blocked-text match).
 */
export function isDeniedText(text: string): boolean {
  const t = String(text ?? "");
  return (
    t.includes("[tool_contract:permission_denied") ||
    t.includes("[tool_contract:blocked_hook") ||
    /^tool .*blocked/i.test(t.trim()) ||
    / permission policy\.?$/i.test(t.trim()) ||
    t.trim() === "allowed_tools_missing" ||
    /\bblocked\b/i.test(t)
  );
}
