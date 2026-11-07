# Validator Promotion Process (Q-15)

How a validator graduates from **advisory** to **hard-fail**.

## Rules

1. A new validator starts as **advisory** (warn-only, exit 0) unless it
   blocks an already-demonstrated security class.
2. Promotion to **hard-fail** requires:
   - ≥1 quarter running green in CI with zero noise complaints, AND
   - evidence of having caught (or verifiably targeted at) a real defect
     class, AND
   - elimination of all of its own exemptions/false-positives ≥90 days old.
3. Exemptions carry metadata (`added=YYYY-MM-DD`) so age can be computed
   without git history; when metadata is missing the scorer falls back to
   git blame of the exemption line, then to file mtime.
4. Exemptions ≥ **90 days** old are flagged for review in the maturity
   scorer output (`EXEMPTION AGE TRACKING` section). A flagged exemption must
   be (a) re-justified and re-dated, or (b) deleted, before its validator may
   be promoted.

## Program

| Stage | Action |
|---|---|
| Quarterly | Run `python tools/enforcement_maturity_scorer.py`; review flagged exemptions; re-date or remove each. |
| Promotion PR | Bump `enforcement_level` for the validator in `enforcement_maturity_scorer.py`; include CI-green evidence + the ≥90-day exemption review in the PR description. |
| Demotion | Demote to advisory if the validator raises noise complaints or its caught-defect class is retired (document down-grade in the same PR). |

## Exemption age computation

`tools/enforcement_maturity_scorer.py::_score_exemption_ages`:

1. **metadata first** — parse the `# noqa: boundary (... added=YYYY-MM-DD)`
   comment from the exemption.
2. **git blame fallback** — when metadata is absent/invalid, blame the exact
   line number.
3. **file mtime** — last-resort fallback, flagged `source=file_mtime`.

Ages are informational by design; they never fail the scorecard, but a
hard-fail validator is not allowed to carry ≥90-day-old unreviewed
exemptions.
