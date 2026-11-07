/**
 * The tone of a status chip. A plain type (ADR-103 D-3, 2026-11-01): lib
 * modules the VS Code extension shares (`sandbox.ts`) name it without
 * reaching the Solid component; `components/status-chip.tsx` re-exports it.
 */
export type ChipState = "ok" | "warn" | "error" | "info" | "dim";
