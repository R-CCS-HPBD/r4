/**
 * Shared R4 platform constants — extracted from companion CLI and extension.
 *
 * These constants are used by both the companion agent (Python r4) and the
 * VSCode extension (TypeScript) to ensure consistent behavior.
 */

// ── Known R4 tools (always available regardless of MCP endpoint) ──

/** Known R4 platform tools — injected as system prompt fallback.
 *  These tools exist in the R4 ecosystem regardless of whether the MCP
 *  tools endpoint returns results. */
export const KNOWN_R4_TOOLS: ReadonlyArray<{ name: string; description: string }> = [
  { name: "knowledge_search", description: "Search project knowledge objects with keyword query" },
  { name: "rag_search", description: "Search indexed documents with rag query and optional filters" },
  { name: "rag_status", description: "Check RAG index status and progress" },
  { name: "list_models", description: "List available models from the R4 registry" },
  { name: "list_endpoints", description: "List active inference endpoints with status and model info" },
  { name: "endpoint_health", description: "Check health status of specific endpoints" },
  { name: "list_jobs", description: "Monitor platform jobs with filters and status" },
  { name: "workspace.diagnostics", description: "Get diagnostics for a file in the workspace" },
  { name: "terminal.run", description: "Run a shell command in an integrated terminal" },
  { name: "workspace.readFile", description: "Read file contents from the workspace" },
  { name: "workspace.writeFile", description: "Write content to a file in the workspace" },
] as const;

// ── Plan mode constants (mirrors r4/companion/session.py) ──

/** Plan mode system prompt — identical to CompanionSession._PLAN_MODE_ONLY_SYSTEM */
export const PLAN_MODE_SYSTEM_PROMPT =
  "Plan mode is active. This turn is planning only — no tools, files, or side effects.\n" +
  "Return a structured plan (steps, assumptions, risks, open questions). " +
  "Do not present the work as completed and do not imply anything was executed.\n" +
  "For open-ended or brainstorming requests, frame items as a proposed roadmap, " +
  "not a final deliverable.";

/** Plan mode confirmation footer */
export const PLAN_MODE_FOOTER = (
  "\n\n---\n" +
  "**Awaiting your approval** — review the plan and confirm with yes/confirm " +
  "to execute, or no/cancel to revise."
);

/** Plan mode confirmation regex (mirrors CompanionSession._PLAN_MODE_CONFIRM_RE) */
export const PLAN_CONFIRM_RE = /\b(yes|yep|confirm|approved|go\s*ahead|proceed|execute|run\s*it|do\s*it|continue)\b/i;

/** Plan mode cancellation regex (mirrors CompanionSession._PLAN_MODE_CANCEL_RE) */
export const PLAN_CANCEL_RE = /\b(no|nope|cancel|stop|abort|nevermind|never mind)\b/i;

// ── R4 system prompt template ──

/**
 * Build the R4 agent system prompt with dynamic tool list.
 *
 * @param mcpTools — MCP tools from /gw/mcp/tools
 * @param workspaceTools — Workspace tools from AgentToolBridge
 * @returns System prompt string
 */
export function buildR4SystemPrompt(
  mcpTools: { name: string; description?: string; inputSchema?: unknown }[],
  workspaceTools: { name: string; description: string }[],
): string {
  // Merge all available tools (MCP + workspace + known fallback)
  const toolMap = new Map<string, string>();

  // Add MCP tools (highest priority — they have full schema)
  for (const t of mcpTools) {
    toolMap.set(t.name, t.description ?? "");
  }

  // Add workspace tools
  for (const t of workspaceTools) {
    if (!toolMap.has(t.name)) {
      toolMap.set(t.name, t.description ?? "");
    }
  }

  // Add known R4 tools (fallback — ensures model knows what tools exist)
  for (const t of KNOWN_R4_TOOLS) {
    if (!toolMap.has(t.name)) {
      toolMap.set(t.name, t.description);
    }
  }

  const toolsList = Array.from(toolMap.entries())
    .map(([name, desc]) => `  - ${name}: ${desc}`)
    .join("\n");

  return [
    "You are the R4 Platform Agent — an AI assistant for HPC researchers.",
    "",
    "You have access to these tools through the R4 Gateway:",
    "",
    toolsList.length > 0 ? toolsList : "(No tools available — use general knowledge only)",
    "",
    "CRITICAL BEHAVIOR RULES:",
    "- You MUST use tools to answer questions when tools are available.",
    "- NEVER generate shell commands (like r4 endpoint list) — use the tools listed above.",
    "- NEVER say 'I should use a command' — actually CALL the tool.",
    "- When you want to use a tool, output a tool call immediately.",
    "- Do not explain what you would do — just DO it with the tool.",
    "",
    "Platform-specific capabilities:",
    "- Knowledge search: Search project knowledge objects with knowledge_search",
    "- RAG: Search indexed documents with rag_search, check status with rag_status",
    "- Models: List available models with list_models",
    "- Endpoints: List/provision inference endpoints with list_endpoints, endpoint_health",
    "- Jobs: Monitor platform jobs with list_jobs",
    "- Workflows: Check workflow status and trigger workflows",
    "",
    "Workspace tools (VS Code specific):",
    "- workspace.diagnostics: Get diagnostics for files",
    "- terminal.run: Run shell commands",
    "- workspace.readFile: Read workspace files",
    "- workspace.writeFile: Write workspace files",
    "",
    "When using tools:",
    "- Always call tools directly — do not describe them in text.",
    "- For destructive or costly operations, ask for confirmation first.",
    "- After failures, diagnose and suggest a fix.",
    "- Provide concise, technically precise answers.",
    "- Prefer showing working code over lengthy explanations.",
    "",
    "MARKDOWN FORMATTING (REQUIRED):",
    "- Use proper fenced code blocks with language tags: ```python, ```bash, ```json, ```yaml",
    "- ALWAYS include the language identifier in code blocks (e.g., ```bash NOT just ```)",
    "- This enables syntax highlighting in the chat UI. Responses without language tags appear as plain text.",
    "",
    "IMPORTANT RULES:",
    "- NEVER hallucinate tool names. Only use tools listed above.",
    "- NEVER download models inside inference containers — use r4 model stage",
    "- Route all LLM traffic through the Gateway, never directly to vLLM",
    "- Use presigned URLs for file access, not direct uploads",
    "",
    "If you don't have a tool for something, provide the best answer using your knowledge.",
  ].join("\n");
}


// ── Plan mode helpers ──

/** Build the plan execution prompt. */
export function buildPlanExecutionPrompt(pendingRequest: string, confirmation: string): string {
  return (
    `The user has approved this plan. Execute the following request:\n\n` +
    `> ${pendingRequest}\n\n` +
    `You have authorization to proceed. Execute the plan.\n\n` +
    `User confirmation: ${confirmation}`
  );
}

/** Build the plan revision prompt. */
export function buildPlanRevisionPrompt(pendingRequest: string, revision: string): string {
  return (
    `The user wants to revise the plan.\n\n` +
    `Original request: ${pendingRequest}\n` +
    `Revision: ${revision}\n\n` +
    `Please incorporate the changes and return a revised plan.`
  );
}
