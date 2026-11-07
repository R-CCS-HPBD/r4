/**
 * `/explain <error.id>` text: cause + remediation from the error registry
 * (bridge verb `explain`). Pure, so the VS Code host prints it the same way
 * (ADR-103 D-3); `commands/files-command.ts` uses it.
 */

export const EXPLAIN_USAGE = "explain: give an error id — /explain auth.forbidden";

export interface ExplainAnswer {
  found?: boolean;
  cause?: string;
  remediation?: string;
  exit_code?: number | null;
  retryable?: boolean;
  hint?: string;
}

/** The notice for one answer; an unknown id says so with the --list hint. */
export function explainText(errorId: string, out: ExplainAnswer): string {
  if (!out.found) return `explain ${errorId}: unknown error id — ${out.hint ?? "r4 explain --list"}`;
  const lines = [`explain ${errorId}`, `  cause: ${out.cause}`, `  fix: ${out.remediation}`];
  if (out.exit_code !== null && out.exit_code !== undefined) {
    lines.push(`  exit ${out.exit_code} · retryable ${out.retryable ? "yes" : "no"}`);
  }
  return lines.join("\n");
}
