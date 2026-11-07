/**
 * Resolve a Live verify answer for the Research panel.
 * Order: CLI verifyKnowledge → offline demo.
 */

import { workspace, activeTab } from "../stores";
import { agentActions } from "../stores/agent-runtime";
import { mapVerifyResponse } from "./knowledge-verify";
import type { VerificationAnswer } from "../types";

function isGatedLiveStub(answer: VerificationAnswer): boolean {
  return (
    answer.confidence === 0 &&
    (answer.answer.includes("not connected to UMRS") ||
      answer.reasoningPath.some((p) => p.includes("demo verification answers are disabled")))
  );
}

function verificationResultToAnswer(
  question: string,
  result: ReturnType<typeof mapVerifyResponse>,
): VerificationAnswer {
  return {
    question,
    answer: result.answer,
    confidence: result.confidence,
    reasoningPath: result.reasoningPath.length
      ? result.reasoningPath
      : ["CLI: r4 project verify"],
    evidenceIds: result.evidenceIds,
    graphTraversal: result.graphTraversal,
    supportingPapers: result.supportingPapers,
    supportingExperiments: result.supportingExperiments,
    relatedHypotheses: result.relatedHypotheses,
  };
}

/** Async resolve used by VerificationChat UI.
 * Order: CLI verifyKnowledge → offline demo.
 */
export async function resolveVerificationForUi(question: string): Promise<VerificationAnswer> {
  const sync = agentActions.queryVerification(question);

  // UMRS path or offline demo already produced a real-looking answer
  if (!isGatedLiveStub(sync)) return sync;

  // Live without UMRS: fall through to CLI project verify (awaited)
  const projectId = activeTab().project?.id;
  const source = workspace.dataSource;
  if (!projectId) {
    return {
      ...sync,
      answer:
        "No active project — /project switch <id>, then ask again (or use F4 Knowledge verify).",
      reasoningPath: ["CLI verify requires an active project"],
    };
  }
  if (!source?.verifyKnowledge) {
    return {
      ...sync,
      answer: `${sync.answer} (UMRS best-effort; CLI verify unavailable)`,
      reasoningPath: [...sync.reasoningPath, "UMRS HTTP path is best-effort"],
    };
  }

  try {
    const raw = await source.verifyKnowledge(projectId, question);
    if (raw.error) {
      return {
        question,
        answer: String(raw.error),
        confidence: 0,
        reasoningPath: ["CLI: r4 project verify failed"],
        evidenceIds: [],
        graphTraversal: [],
        supportingPapers: [],
        supportingExperiments: [],
        relatedHypotheses: [],
      };
    }
    const mapped = mapVerifyResponse(question, raw);
    agentActions.emitEvent("agent_reasoning", {
      source: "verification_query",
      via: "cli_verifyKnowledge",
      question,
    });
    return verificationResultToAnswer(question, mapped);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      question,
      answer: message,
      confidence: 0,
      reasoningPath: ["CLI: r4 project verify error"],
      evidenceIds: [],
      graphTraversal: [],
      supportingPapers: [],
      supportingExperiments: [],
      relatedHypotheses: [],
    };
  }
}
