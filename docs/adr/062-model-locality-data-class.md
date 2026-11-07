# ADR-062: Model locality — per-project data-class policy for inference

**Status:** Accepted (policy; enforcement wave lands separately)
**Date:** 2026-09-22 (maintainer decision, design reset wave)
**Relates to:** ADR-046 (public-kind sources via the credential-injecting proxy), ADR-047 (fine-tune executor, dispatch gated), gap rows D-51 / D-54 in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

The design states model locality: research data never leaves the site for
inference. The tree has two deliberate counter-pressures: multi-source
routing and ADR-046 "public-kind sources" let a user route inference to a
public provider with their own key (through the credential-injecting proxy on
the front-end), and the fine-tune executor class exists behind a dispatch
gate (ADR-047). Neither is wrong; what is missing is the policy object that
says when each is allowed.

## Decision

1. **Per-project data-class flag.** Every project carries
   `data_class ∈ {sensitive, non_sensitive}`, default **sensitive**.
   - `sensitive`: inference only on self-deployed models inside the
     institution (mini-RiVault, ADR-061 `model-server`); public-kind sources
     are refused for that project's sessions and jobs.
   - `non_sensitive`: public providers are allowed, still only through the
     front-end proxy (keys never reach sandboxes or workstations, ADR-046).
2. **Fine-tuning** stays gated behind an explicit per-project flag
   (`fine_tune_enabled`, default off), independent of data class; competence
   comes from the catalog by default (D-54, design kept).
3. The flag lives on the durable project record (ADR-051); the enforcement
   point is source/model resolution (routing layer), so a `sensitive`
   project cannot reach a public endpoint even by explicit request.

## Consequences

- Gap row D-51 moves toward Implemented once the flag + routing gate land
  (enforcement wave; ROADMAP phase 4 / standing work); this ADR is the
  policy of record.
- Projects default to the safe class; widening is an explicit per-project
  act attributable to its owner.
- No current deployment changes. The routing-layer gate and the project
  column migration are the follow-up implementation wave.
