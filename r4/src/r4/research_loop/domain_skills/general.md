# Research Domain Skill: General Scientific Method (Default)

## General Principles
- All hypotheses must be testable, falsifiable, and specific
- Experiments require explicit methodology documentation before execution
- Results must include error bars, confidence intervals, and sample sizes
- Negative and null results are evidence — record them, don't discard
- Statistical rigor: appropriate tests for data type (parametric vs non-parametric)
- Reproducibility: all parameters, seeds, and random state must be recorded
- Peer review: every conclusion must survive adversarial review (PR agent)

## Hypothesis Quality Checklist
- [ ] Specific and measurable (not "improves performance" but "increases X by >Y%")
- [ ] Falsifiable (can be proven false by evidence)
- [ ] Grounded in literature or theory (not random guess)
- [ ] Testable with available resources (time, compute, data)
- [ ] Includes expected effect size (not just "any effect")

## Experiment Design Checklist
- [ ] Clear independent and dependent variables
- [ ] Control group or baseline comparison specified
- [ ] Sample size justified (power analysis or precedent)
- [ ] Confounding variables identified and controlled
- [ ] Statistical test specified before data collection
- [ ] Resource requirements estimated (time, compute, data volume)

## Evidence Evaluation Standards

### Evidence Strength Heuristic
| p-value | Effect Size (Cohen's d) | Sample Size | Strength Score |
|---------|------------------------|-------------|----------------|
| < 0.001 | > 0.8 | > 100 | 0.9–1.0 (Decisive) |
| < 0.01 | > 0.5 | > 50 | 0.7–0.9 (Strong) |
| < 0.05 | > 0.3 | > 30 | 0.5–0.7 (Moderate) |
| < 0.1 | > 0.2 | > 10 | 0.2–0.5 (Weak) |
| > 0.1 | < 0.2 | < 10 | 0.0–0.2 (Insufficient) |

### Confounding Variable Checklist
- Selection bias: was the sample representative?
- Measurement error: were instruments calibrated?
- Confounding by indication: is the effect caused by something else?
- Temporal confounding: did something else change during the experiment?
- Observer bias: was the analyst blinded to conditions?

## Output Standards
- Every experiment result must be reported, regardless of outcome
- Evidence must be linked to specific hypotheses via A2L provenance
- Iteration decisions must be documented with reasoning
- Final publication must include methodology, results, and limitations
