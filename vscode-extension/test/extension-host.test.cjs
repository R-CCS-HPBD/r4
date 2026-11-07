const assert = require("node:assert/strict");
const vscode = require("vscode");

suite("R4 extension host", () => {
  test("activates and registers the Agent view command", async () => {
    const extension = vscode.extensions.getExtension("mnagaso.r4-platform");
    assert.ok(extension, "R4 extension is installed in the test host");

    await extension.activate();

    assert.equal(extension.isActive, true);
    const commands = await vscode.commands.getCommands(true);
    assert.ok(commands.includes("r4.focusAgent"));
    await vscode.commands.executeCommand("r4.focusAgent");
  });
});
