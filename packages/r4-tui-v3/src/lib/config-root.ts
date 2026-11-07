/**
 * The console's config directory: $R4_CONFIG_DIR or ~/.config/r4 (the
 * theme file lives there).
 *
 * ADR-104 D-3 (2026-10-31): this module used to also hold the console's own
 * per-project "always allow" rule file, read to answer approvals before the
 * companion saw them. The companion owns those rules now (one G14 loader);
 * `/settings rules` reads and edits them through the bridge
 * (`permission_rules.list` / `permission_rules.remove`).
 */

import { join } from "node:path";
import { homedir } from "node:os";

export function configRoot(env: Record<string, string | undefined> = process.env): string {
  return env.R4_CONFIG_DIR?.trim() || join(homedir(), ".config", "r4");
}
