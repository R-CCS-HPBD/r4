# ADR-095: Harness-refinement lane — offline propose–probe–compose over r4's editable surface, candidates landing only in the merge-on-green skill review plane

**Status:** Proposed
**Date:** 2026-10-21
**Relates to:**
- ADR-084/D-27: the merge-on-green skill review plane is the ONLY production gate a refined candidate may pass through; D-27's open leg ("the research loop proposing edits to EXISTING skills") is what this ADR names a method for.
- ADR-087: the model-call contract every refinement rollout reuses unchanged.
- ADR-076 / D-57: the decision-model gate stays off inside study cells (comparability with the 2026-10-04 baseline).
- ADR-093: judge and solver sit on the rikyu glm-5.3 stack; no decision-model use.
- Gap rows D-27 (skills), D-44 (catalog quality measurement and its precision rule).
- CONSTITUTION ownership: the refinement runner must not become a second store, scheduler, or credential path in `r4/` or `gateway/`.

## Context

On 2026-10-20 the maintainer asked whether `google-research/harness2` (Harness², arXiv:2610.14224) can be used for r4 development. The evaluation answered "not as a dependency, but as a method": Harness² optimizes the harness AROUND a frozen model — system prompt, guardrails, memory, tool guidance, skills, scripts — through a contrastive propose–probe–compose (PPC) loop that needs no ground truth, and that method maps exactly onto D-27's open leg. Direct adoption fails the platform's ownership rules: harness2 carries its own results store, its own scheduler, and its own model-config/credential path (Vertex `.env`), while its executors run unsandboxed with unrestricted network.

A feasibility spike (2026-10-20, outside the repo at `/tmp/opencode/h2-spike/`) then ran the full PPC cycle against the REAL r4 companion, unmodified, on the same-model 54-question suite (arm A, glm-5.3, prompts byte-identical via the lane's own `run_glm`, blind glm-5.3 judge in the lane's `grade_r4` shape). One k=1 task (TASK-01) completed: base rollout → two editor sessions → two probe rollouts → composer → composition exec → adoption. The composed edits were executed through r4's actual injection surfaces: `guardrails.md` landed as the workdir `R4.md` briefing file, system additions landed in `session.metadata["system_prompt_override"]`, and the composed skill was project-staged and pre-activated (`skills_active: ['example-corpus']` on the adopted run).

Spike numbers (n=1 — machinery receipt, not an effect claim): base 1.0 score / 4306 s / 161,648 tokens; adopted candidate 0.5 / 729 s / 119,811 tokens; probes down to 239 s / 60,318 tokens. The 0.5-vs-1.0 gap is inside the judge's own replicate noise (the judge excused the same omitted fact in the base run), consistent with D-44's precision measurement: identical runs differ by ±0.07, and accuracy claims need two runs per cell with paired statistics. A scoped study — the 20 hardest non-ceiling questions × 2 replicate seeds, judged blind — was launched the same night to produce the defensible number.

Two r4-grade defects surfaced by the spike were fixed in wave `wave/h2-followups` with regression pins: `r4 chat` crashing on null reply content (reasoning-exhausted small `max_tokens`), and the same-model lane's judge helper inheriting r4's 300 s read timeout (aborts slow judge completions on a loaded rikyu).

## Decision

**D-1 — Refinement runs offline; the in-session loop never self-modifies.** Harness-refinement candidates are produced by an external runner (Harness²-shaped: propose → probe on the same task → compose from behavioral contrasts), on its own schedule, with its own state outside `r4/` and `gateway/`. The runner is an experiment tool, not a platform component: it holds no production credentials, writes nothing into production stores, and reads model endpoints only through the same dummy-key passthrough the benchmark lanes use.

**D-2 — The merge-on-green skill review plane is the ONLY production gate.** A composed candidate reaches production as a skill proposal under ADR-084: human decision, green test receipt required, agent actors refused (`skills.merge_not_green`, typed 403). The runner proposes; the plane decides. No harness edit lands enabled by a machine decision.

**D-3 — Effect claims obey the D-44 precision rule.** Refinement is measured on the same-model 54-question suite with the lane's blind glm-5.3 judge, two runs per cell, paired bootstrap CI + sign test. A candidate without a paired-significant effect is not submitted to the review plane. Efficiency (tokens/requests) may be gated from one run; accuracy may not.

**D-4 — The r4 editable surface is a named contract.** Candidates edit only: `systemprompt.md` / `LongTermMEMORY.md` / `tool_descriptions/tool_guidance.md` → `session.metadata["system_prompt_override"]`; `guardrails.md` → workdir `R4.md` (the repo-briefing chain, 4000-char file cap); `skills/<name>/SKILL.md` → `.r4/skills/` project staging with pre-activation in headless runs; `scripts/<name>.py` → the workspace, run via the shell tool. There are no subagent or plugin surfaces on this seam (multi-agent stays the ADR-072/092 team seam; nothing edits tool behavior inside the loop).

**D-5 — Harness² is never vendored.** No dependency on, or copy of, harness2 code in `r4/` or `gateway/`: its ledger/scheduler/credential design breaks the ownership rules, it is unsupported research code (3 commits at evaluation time), and its BSD/Apache stack carries benchmark-answer leakage hazards the platform's sandbox rules forbid. The method and the external runner pattern are what we adopt; any r4-native refinement engine proposed later gets its own ADR.

### Why

The review plane already exists and already refuses untested machine edits; without D-2 the refinement loop would be a silent side door around ADR-084. Without D-3 the platform would ship harness regressions judged on single runs, which D-44's precision work has already shown to be noise. D-1 and D-5 keep the runner from growing roots into the client — the exact pattern CONSTITUTION's ownership table exists to prevent.

## Consequences

- Positive: D-27's open leg gets a method with published (and locally reproduced) feasibility; the merge-on-green plane gains a principled source of proposals; the same-model lane becomes usable as a refinement instrument, not only a comparison instrument.
- Cost: proposer/editor sessions dominate compute (measured 40 min–2 h each on a loaded rikyu; off-peak significantly faster), so a paired 20-question study is a multi-day background effort; candidates today execute unsandboxed on the host (prompt-level read-only corpus protection) — a production lane must reuse `benchmarks/harness/same_model/sandbox.py` (bwrap) before any wider rollout.
- NOT in scope: runtime/online self-modification of the harness; sandboxed execution of candidate harnesses as a product feature; multi-agent (team) harness refinement; the r4-native refinement engine (its own future ADR).

## Alternatives Considered

- **Vendor harness2 as a dependency** — rejected: second store/scheduler/credential path (ownership), Vertex-only proposer assumptions, unsupported research code, executor network posture contrary to mini-rivault.
- **Keep letting the knowledge chain's compile step propose skill edits** — rejected as the sole path: it never measures effect, so regressions ship silently; PPC is exactly the missing measurement loop feeding the same review plane.
- **Refine competitor harnesses instead (OpenCode/Claude Code formats)** — rejected: that optimizes someone else's product surface; the platform's asset is r4's own harness.

## Rollout-status (2026-10-21)

- D-1…D-5: PROPOSED; nothing landed in-tree beyond this document.
- Evidence (outside the repo, `/tmp/opencode/h2-spike/`): substrate + bench adapter + driver; full k=1 PPC cycle receipt on TASK-01 (all four D-4 surfaces exercised; adoption fingerprint recorded).
- In flight: scoped paired study (20 hardest non-ceiling questions × 2 seeds, blind glm-5.3 judge; `study/run_study.sh`); its result decides the first submission to the review plane.
- Landed this wave (`wave/h2-followups`, pending maintainer review): the two defect fixes with regression pins (`r4/tests/test_cli_format_thinking.py` 4 pins; `benchmarks/harness/test_glmjudge.py` 3 pins); gate `ALL CHECKS PASSED` (full r4 SDK suite 6753 green).

## Related

- Xu et al., "Harness²: Recursive Agent Harnessing for an Open World", arXiv:2610.14224 (2026) — method source; Apache-2.0.
- `docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md` rows D-27, D-44 (dated notes appended this wave).

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
