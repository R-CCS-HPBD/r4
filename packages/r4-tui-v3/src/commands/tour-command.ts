/**
 * /tour command — interactive guided walkthrough wizard.
 *   /tour        resume the wizard where the user left off
 *   /tour <n>    jump to step n (1-based)
 *   /tour map    print the full r4/umrs function index (catalog-derived)
 */

import { actions } from "../stores";
import { localSlashCommands, PROJECT_SUBCOMMANDS, ENDPOINT_SUBCOMMANDS } from "./slash";
import type { SlashDispatchResult } from "./slash";

export function handleTourCommand(args: string): SlashDispatchResult {
  const q = args.trim().toLowerCase();
  if (q === "map" || q === "index") {
    void import("../lib/tour").then(({ buildTourLines }) => {
      actions.sendSystemNotice(
        buildTourLines({
          entries: localSlashCommands(),
          projectSubs: PROJECT_SUBCOMMANDS,
          endpointSubs: ENDPOINT_SUBCOMMANDS,
        }).join("\n"),
      );
    });
    return { kind: "handled" };
  }
  if (/^\d+$/.test(q)) {
    actions.openTour(Number(q) - 1);
    return { kind: "handled" };
  }
  actions.openTour();
  return { kind: "handled" };
}
