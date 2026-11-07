/**
 * Suppresses further thinking UI updates once visible answer text has started.
 * Avoids VS Code pinning later markdown (especially code fences) inside the
 * thinking container — see microsoft/vscode#291423.
 */
export class ThinkingStreamGate {
  private answerStarted = false;
  private thinkingActive = false;
  private thinkingFinalized = false;

  markAnswerStarted(): void {
    this.answerStarted = true;
  }

  get isAnswerStarted(): boolean {
    return this.answerStarted;
  }

  reportThinking(report: (text: string) => void, text: string): void {
    if (!this.answerStarted && text) {
      report(text);
      this.thinkingActive = true;
    }
  }

  /** Close the thinking block once; no-op if no thinking was reported. */
  finalize(reportDone: () => void): void {
    if (this.thinkingActive && !this.thinkingFinalized) {
      reportDone();
      this.thinkingFinalized = true;
    }
  }
}
