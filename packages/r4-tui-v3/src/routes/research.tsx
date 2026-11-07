/**
 * Research Workspace Route — autonomous scientific research environment.
 *
 * Layout:
 * ┌──────────────────────────────────────────────────────────────┐
 * │ Mission Control                                               │
 * ├──────────────┬──────────────────────────────┬────────────────┤
 * │ Agents       │     Project Graph            │ Live verify    │
 * ├──────────────┴──────────────────────────────┴────────────────┤
 * │ Bottom: Ops | Strategy | Explain                              │
 * └──────────────────────────────────────────────────────────────┘
 */

import { AgentMonitor } from "../components/agent-monitor";
import { KnowledgeGraphViewer } from "../components/knowledge-graph-viewer";
import { VerificationChat } from "../components/verification-chat";
import { MissionControl } from "../components/mission-control";
import { BottomPanel } from "../components/bottom-panel";

export function ResearchRoute() {
  return (
    <box flexDirection="column" width="100%" height="100%" gap={0} overflow="hidden">
      <MissionControl />

      <box
        flexDirection="row"
        width="100%"
        flexGrow={1}
        flexShrink={1}
        minHeight={12}
        alignItems="stretch"
        gap={0}
        overflow="hidden"
      >
        <AgentMonitor />
        <KnowledgeGraphViewer />
        <VerificationChat />
      </box>

      <BottomPanel />
    </box>
  );
}
