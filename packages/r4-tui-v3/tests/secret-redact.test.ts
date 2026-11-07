/**
 * Cycle-2 security lane (2026-10-21): the S4 masks only knew r4's own key
 * flags, so a `!` line holding a credential in any other common shape —
 * `https://user:pass@host`, `export R4_API_KEY=…`, `HF_TOKEN=… cmd`, a bare
 * JWT, `"api_key": "…"`, curl `-u user:pass`, `r4 mcp register --auth-token`,
 * a key continued with backslash-newline — reached the transcript, the model
 * relay (and the server queue) and the prompt history verbatim.
 * Repro: $C2/security/redact/fuzz_tui_redact.ts (28 leaks on the base).
 */
import { describe, expect, test } from "bun:test";

import { maskShellText, shellDisplayCommand } from "../src/lib/local-shell";
import { carriesSecret, redactSecretFlags, redactSlashSecrets, secretValues } from "../src/lib/secret-redact";

// Fakes built from parts so the repo's own secret scanner stays quiet.
const JWT = ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJ0ZXN0In0", "FAKESIGFAKESIGFAKESIG"].join(".");
const SK = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE";
const PW = "Hunter2Passw0rd";
const HF = "hf_FAKEfakeFAKEfakeFAKEfake123";

const LEAKY_LINES: Array<[string, string, string]> = [
  ["URL credentials", `curl https://alice:${PW}@gw.example/api`, PW],
  ["git URL token", `git clone https://oauth2:${HF}@github.com/x/y`, HF],
  ["curl -u", `curl -u alice:${PW} https://h`, PW],
  ["export", `export R4_API_KEY=${SK}`, SK],
  ["env prefix", `R4_API_KEY=${JWT} r4 chat`, JWT],
  ["r4 mcp --auth-token", `r4 mcp register srv http://h --auth-token ${SK}`, SK],
  ["bare JWT", `echo ${JWT}`, JWT],
  ["JSON key", `echo '{"api_key": "${PW}"}' > cfg.json`, PW],
  ["bare HF token", `huggingface-cli login ${HF}`, HF],
];

describe("a credential in any common shape is masked on a `!` line", () => {
  for (const [label, line, secret] of LEAKY_LINES) {
    test(`${label}: the shown line, the history and the output`, () => {
      expect(shellDisplayCommand(line)).not.toContain(secret);
      expect(carriesSecret(line)).toBe(true);
      expect(maskShellText(line, `printed ${secret} back`)).not.toContain(secret);
    });
  }

  test("a key continued with backslash-newline is masked whole, and scrubbed joined", () => {
    const line = `r4 login -k ${JWT.slice(0, 20)}\\\n${JWT.slice(20)}`;
    expect(shellDisplayCommand(line)).toBe("r4 login -k •••");
    expect(secretValues(line)).toContain(JWT);
    expect(maskShellText(line, `token ${JWT} saved`)).toBe("token ••• saved");
  });

  test("a slash line with a credential flag is masked", () => {
    expect(redactSlashSecrets(`/mcp register x http://h --auth-token ${SK}`)).not.toContain(SK);
  });
});

describe("lines without a credential are left as typed", () => {
  const PLAIN = [
    "ls -la",
    "sort -u names.txt",
    "curl --user-agent probe https://example.com/x",
    "python train.py --max-tokens 100",
    "echo max_tokens=5",
    "r4 login --api-key-env R4_KEY",
    "r4 source add x --url https://h --api-key-stdin",
    "echo http://host:8080/path@v1",
    "git clone git@github.com:x/y.git",
    "ssh alice@cluster",
  ];
  for (const line of PLAIN) {
    test(line, () => {
      expect(redactSecretFlags(line)).toBe(line);
      expect(carriesSecret(line)).toBe(false);
    });
  }
});
