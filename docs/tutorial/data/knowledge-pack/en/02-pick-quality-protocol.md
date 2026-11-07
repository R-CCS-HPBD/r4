# Pick quality and acceptance protocol (PQ rules, version 3)

> Tutorial material; the observatory and its rules are invented.

## Pick classes

| Class | Onset | Timing uncertainty | Weight |
|---|---|---|---|
| A | impulsive | ≤ 0.05 s | 1.0 |
| B | clear | ≤ 0.1 s | 0.5 |
| C | emergent | ≤ 0.3 s | 0.25 |
| D | doubtful | > 0.3 s | 0 (not used) |

## Acceptance rules

- **PQ-1** Use only the first P arrival per station.
- **PQ-3** Reject a pick when its absolute residual against the reference
  model (`01-reference-model-krso-1d-v2.md`) exceeds **2.5 s**. Report how
  many picks were rejected and why.
- **PQ-5** A pick labelled Pn is valid only beyond **140 km** epicentral
  distance; a "Pn" closer than that is relabelled for review.
- **PQ-7** A residual report needs at least **8 accepted stations**;
  otherwise it is marked *provisional*.

Residuals are always **observed − predicted** (positive = late).
