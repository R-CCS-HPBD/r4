import { defineConfig } from "@vscode/test-cli";

export default defineConfig({
  files: "test/extension-host.test.cjs",
  launchArgs: ["--disable-extensions"],
  mocha: {
    timeout: 20_000,
  },
});
