/**
 * Legacy SlashBar helpers — retired.
 *
 * The unified catalog and dispatch live in `../commands/slash.ts`.
 * SlashBar consumes that module directly; keep this file only if external
 * imports still need a thin re-export.
 */

export { filterSlashCommands, dispatchSlashCommand, loadSlashCatalog, needsArgPrompt } from "../commands/slash";
