/**
 * Left navigator.
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For } from "solid-js";
import { workspace, actions } from "../stores";
import { palette, spaceColor } from "../themes";
import { truncate } from "../lib/text";
import { Panel } from "../ui/Panel";

import type { NavNode, Space } from "../types";

/** Sidebar content width minus indent. */
const LABEL_MAX = 20;

function Node(props: { node: NavNode; depth: number }) {
  const selected = () => workspace.selectedNodeId === props.node.id;
  const color = () => (props.node.route ? spaceColor(props.node.route as Space) : palette.fg);
  const line = () => {
    const marker = selected() ? "›" : " ";
    const icon = props.node.icon ?? " ";
    const label = truncate(props.node.label, Math.max(8, LABEL_MAX - props.depth));
    return truncate(`${marker} ${icon} ${label}`, LABEL_MAX + 4);
  };

  return (
    <box flexDirection="column" width="100%" paddingLeft={props.depth}>
      <box
        width="100%"
        height={1}
        overflow="hidden"
        backgroundColor={selected() ? palette.bgActive : undefined}
        onMouseDown={() => {
          if (props.node.route) {
            actions.setSpace(props.node.route as Space);
          } else {
            actions.selectNode(props.node.id);
          }
        }}
      >
        <text fg={selected() ? palette.fgBright : color()}>{line()}</text>
      </box>
      <For each={props.node.children}>{(child) => <Node node={child} depth={props.depth + 1} />}</For>
    </box>
  );
}

export function CommandCenter() {
  return (
    <Panel
      width={26}
      accent={palette.accent}
      title="Command Center"
      flexDirection="column"
      overflow="hidden"
    >
      <Node node={workspace.navTree} depth={0} />
    </Panel>
  );
}
