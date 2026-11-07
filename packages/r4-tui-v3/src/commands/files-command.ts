/**
 * /files slash family (TUI/rccs function sweep, 2026-09-27):
 * upload/download through the gateway's project-namespace Files client.
 * Bridge verbs files.upload / files.download; every failure lands as its
 * own named notice (gateway/auth errors verbatim), never a silent break.
 */

import { EXPLAIN_USAGE, explainText, type ExplainAnswer } from "../lib/explain-text";
import { getCompanionBridge } from "../bridge";
import { actions } from "../stores";
import { isOfflineBackend } from "../lib/console-mode";

export type FilesDispatchResult = { kind: "handled" };

interface UploadReply {
  ok?: boolean;
  key?: string;
  bucket?: string;
  uri?: string;
  size?: number;
}

interface DownloadReply {
  ok?: boolean;
  key?: string;
  dest?: string;
}

function _parseSub(args: string): { verb: string; rest: string[] } {
  const tokens = args.trim().split(/\s+/).filter(Boolean);
  const verb = (tokens.shift() ?? "").toLowerCase();
  return { verb, rest: tokens };
}

async function _upload(rest: string[]): Promise<void> {
  const path = rest[0];
  if (!path) {
    actions.sendSystemNotice("files upload: give a local path — /files upload <path> [key]");
    return;
  }
  const key = rest[1];
  const bridge = getCompanionBridge();
  try {
    const out = (await bridge.request(
      "files.upload",
      key ? { path, key } : { path },
      120_000,
    )) as UploadReply;
    actions.sendSystemNotice(
      `files upload ✓ ${out.key ?? path}` +
        (out.bucket ? ` [${out.bucket}]` : "") +
        (out.size !== undefined ? ` ${out.size} B` : "") +
        (out.uri ? `\n  uri: ${out.uri}` : ""),
    );
  } catch (err) {
    actions.sendSystemNotice(
      `files upload failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

async function _download(rest: string[]): Promise<void> {
  const key = rest[0];
  const dest = rest[1];
  if (!key || !dest) {
    actions.sendSystemNotice(
      "files download: give the object key and a local destination — /files download <key> <dest>",
    );
    return;
  }
  const bridge = getCompanionBridge();
  try {
    const out = (await bridge.request("files.download", { key, dest }, 120_000)) as DownloadReply;
    actions.sendSystemNotice(`files download ✓ ${out.key ?? key} → ${out.dest ?? dest}`);
  } catch (err) {
    actions.sendSystemNotice(
      `files download failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

export async function handleFilesCommand(args: string): Promise<FilesDispatchResult> {
  if (isOfflineBackend()) {
    actions.sendSystemNotice("files: requires a live backend");
    return { kind: "handled" };
  }
  const { verb, rest } = _parseSub(args);
  if (verb === "upload") {
    await _upload(rest);
    return { kind: "handled" };
  }
  if (verb === "download") {
    await _download(rest);
    return { kind: "handled" };
  }
  actions.sendSystemNotice(
    `files — gateway file transfer:\n  /files upload <path> [key]   upload into the project namespace\n  /files download <key> <dest> download an object to a local path`,
  );
  return { kind: "handled" };
}

/**
 * /explain <error.id> — cause + remediation from the registry (bridge verb
 * explain). Unknown ids answer honestly with the --list hint.
 */
export async function handleExplainCommand(args: string): Promise<FilesDispatchResult> {
  const errorId = args.trim();
  if (!errorId) {
    actions.sendSystemNotice(EXPLAIN_USAGE);
    return { kind: "handled" };
  }
  const bridge = getCompanionBridge();
  try {
    const out = (await bridge.request("explain", { error_id: errorId }, 15_000)) as ExplainAnswer;
    actions.sendSystemNotice(explainText(errorId, out));
  } catch (err) {
    actions.sendSystemNotice(
      `explain failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return { kind: "handled" };
}
