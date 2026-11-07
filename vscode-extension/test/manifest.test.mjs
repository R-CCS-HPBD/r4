import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const manifest = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);

test("routes BYOK utility requests through the selected agent model", () => {
  assert.equal(
    manifest.contributes.configurationDefaults["chat.byokUtilityModelDefault"],
    "mainAgent",
  );
  assert.equal(
    manifest.contributes.configuration.properties[
      "chat.byokUtilityModelDefault"
    ],
    undefined,
  );
  assert.equal(
    manifest.contributes.languageModelChatProviders[0].vendor,
    "r4",
  );
});

test("contributes the console-in-editor command and the bridge settings (ADR-103)", () => {
  assert.ok(manifest.contributes.commands.some((command) => command.command === "r4.openConsole"));
  const props = manifest.contributes.configuration.properties;
  assert.equal(props["r4.pythonPath"].default, "");
  assert.deepEqual(props["r4.agent.backend"].enum, ["acp", "bridge"]);
  assert.equal(props["r4.agent.backend"].default, "acp");
});

test("contributes a dedicated R4 Agent webview without replacing Copilot surfaces", () => {
  const views = manifest.contributes.views["r4-platform"];
  assert.deepEqual(
    views.find((view) => view.id === "r4.agent"),
    { id: "r4.agent", name: "Agent", type: "webview" },
  );
  assert.ok(
    manifest.contributes.commands.some((command) => command.command === "r4.focusAgent"),
  );
  assert.ok(manifest.contributes.chatParticipants.some((participant) => participant.id === "r4.chat"));
  assert.ok(manifest.contributes.languageModelChatProviders.some((provider) => provider.vendor === "r4"));
  assert.ok(manifest.contributes.mcpServerDefinitionProviders.some((provider) => provider.id === "r4"));
});
