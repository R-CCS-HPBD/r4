# Validator Stability Review

*Generated: 2026-05-13 — Architecture Stewardship Phase*

## Purpose

This document audits all 9 enforcement validators for regex fragility, path
normalization assumptions, CI portability, Python-version sensitivity, and
false-positive risk. It provides a stability rating and identifies hardening
opportunities that can be addressed with additive-only changes.

---

## Summary Matrix

| # | Validator | Fragility | Key Risk | Python Min | CI Portable |
|---|-----------|-----------|----------|-----------|-------------|
| 1 | `import_boundary_validator` | Medium | `sys.stdlib_module_names` (3.10+) | 3.10 | ✅ |
| 2 | `contracts_fence` | Low | Hardcoded path | 3.7 (with tomli) | ✅ |
| 3 | `dto_equivalence_check` | Medium | `ast.dump()` cosmetic sensitivity | 3.7 | ✅ |
| 4 | `schema_version_tracker` | Medium-High | Fallback YAML regex | 3.7 | ⚠ (needs yaml lib) |
| 5 | `stale_name_validator` | Low | Minimal; well-scoped | 3.6 | ✅ |
| 6 | `config_drift_validator` | Medium | Required-marker whitespace sensitivity | 3.6 | ✅ |
| 7 | `exemption_governance_validator` | Medium-High | Metadata regex lookahead fragility | 3.6 | ✅ |
| 8 | `orphan_module_detector` | Medium | Heavy hardcoding; relative import gaps | 3.6 | ✅ |
| 9 | `adr_linkage_validator` | Low | 3-digit ADR limit | 3.6 | ✅ |

---

## Detailed Findings

### 1. import_boundary_validator.py

**Mechanism**: AST-based import analysis with rule-driven boundary enforcement.

**Strengths:**
- Uses Python AST (not regex) for import extraction — robust against formatting
- `pathlib.Path` throughout — cross-platform safe
- TYPE_CHECKING-guarded imports correctly excluded
- `# noqa: boundary` escape hatch for legitimate exceptions

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Python 3.10+ dependency | Medium | Uses `sys.stdlib_module_names` — unavailable on 3.9 |
| Hardcoded `rule.root` paths | Low | `"umrs/src/umrs"`, `"r4/src/r4"`, `"gateway/gateway"` |
| Relative import resolution limits | Low | `level > len(parts)` guard may miss edge cases |
| Submodule path assumption | Low | If UMRS submodule moves, all root strings break |

**False-Positive History**: None known. The AST approach is inherently accurate.

**Hardening Opportunity**: Add test asserting `sys.stdlib_module_names` availability
(already implicitly tested via import, but explicit check improves CI diagnostics).

---

### 2. contracts_fence.py

**Mechanism**: TOML parsing of `r4-contracts/pyproject.toml` to enforce zero
dependencies.

**Strengths:**
- Uses standard TOML library (tomllib 3.11+ / tomli fallback)
- Single-purpose, minimal code surface
- Graceful error on missing file

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Hardcoded path `"r4-contracts"` | Low | Directory rename would silently pass (no deps = no error) |
| `tomli` not in stdlib | Low | Must be available in dev/CI environment |

**False-Positive History**: None possible (checks for presence of `[project.dependencies]`
key — binary condition).

**Hardening Opportunity**: None needed. This is the most stable validator.

---

### 3. dto_equivalence_check.py

**Mechanism**: AST-based class field comparison between r4-contracts and UMRS
mirror DTOs.

**Strengths:**
- Compares field names (robust)
- Handles both dataclass and enum patterns
- Graceful handling of missing files

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| `ast.dump()` for type comparison | Medium | Cosmetic annotation differences trigger false positives |
| Hardcoded 14 file path pairs | Medium | Must be updated when new DTOs are added |
| Enum member detection heuristic | Low | Assumes assignment-style enum (not Enum subclass) |

**False-Positive Scenario**: If one file uses `Optional[str]` and the other uses
`str | None`, `ast.dump()` produces different outputs despite semantic equivalence.
This has not occurred in practice because both sides follow the same style guide.

**Hardening Opportunity**: Add test with known-good annotation equivalences to
detect `ast.dump()` sensitivity regression across Python versions.

---

### 4. schema_version_tracker.py

**Mechanism**: AST extraction of `SCHEMA_VERSION` constants compared against
`tools/schema_versions.yaml` registry.

**Strengths:**
- AST-based constant extraction (robust for primary path)
- Handles missing schema modules gracefully (returns None)
- `--update` mode for maintaining registry

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Fallback YAML regex `r"^(\w+):\s*(\d+)"` | High | Only handles `key: number`; no comments, no quotes |
| Hardcoded schema module paths | Medium | 3 paths in `SCHEMA_MODULES` dict |
| PyYAML not in stdlib | Medium | If unavailable, falls back to fragile regex |

**False-Positive Scenario**: If `schema_versions.yaml` gains comments or quoted
values, the fallback regex silently misparses. Current registry is simple enough
that this hasn't triggered.

**Hardening Opportunity**: Add CI check that PyYAML is available (it's already
in the dev environment). Alternatively, use `tomllib` for the registry file
(TOML is stdlib in 3.11+).

---

### 5. stale_name_validator.py

**Mechanism**: Regex search for deprecated package names (`aisci`) across
configuration files.

**Strengths:**
- Word-boundary regex (`\baisci\b`) — precise matching
- Allowlist mechanism for legitimate references
- Self-reference protection (skips lines mentioning the validator)
- Case-insensitive matching

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Limited glob patterns | Low | Only scans specific file types |
| Allowlist maintenance | Low | Must update when new legitimate references added |

**False-Positive History**: None. Word-boundary matching and allowlist prevent spurious hits.

**Hardening Opportunity**: None needed. This is a well-designed, minimal validator.

---

### 6. config_drift_validator.py

**Mechanism**: Multi-check validator scanning deployment configs for secrets,
forbidden patterns, and required security markers.

**Strengths:**
- Multiple detection layers (forbidden strings, regex patterns, required markers)
- `errors="ignore"` on file reads — safe against encoding issues
- Production-specific focus (doesn't flag dev configs)

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Required-marker whitespace sensitivity | Medium | Exact string match for Jinja template lines |
| Hardcoded ansible role paths | Medium | Path changes break template scanning silently |
| DSN regex edge cases | Low | Empty passwords not detected |
| Template evolution | Medium | If Jinja template formatting changes, markers stop matching |

**False-Positive Scenario**: A whitespace reformatting of a Jinja template could
cause required markers to stop matching, triggering a false negative (not detecting
the security marker) rather than false positive.

**Hardening Opportunity**: Normalize whitespace before marker comparison (strip
leading/trailing whitespace from both expected and actual lines).

---

### 7. exemption_governance_validator.py

**Mechanism**: Scans `# noqa: boundary` annotations for governance metadata
(reason, owner, expires) and validates exemption hygiene.

**Strengths:**
- Comprehensive metadata extraction
- Date validation with month-level and day-level precision
- Dynamic import of boundary rules for context-aware validation
- Fallback to empty findings if import fails

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Metadata regex with lookahead | High | Values containing `=` break parsing |
| Month-end date comparison | Medium | Exemptions expiring mid-month may flag early |
| `sys.path.insert()` hack | Medium | Dynamic import of `import_boundary_validator` |
| Import extraction regex | Low | `from X import Y` regex misses tuple imports |

**False-Positive Scenario**: An exemption with `reason=needed for x=y mapping`
would be misparsed because the regex splits on `=` within values. This hasn't
occurred because reasons don't contain `=` in practice.

**Hardening Opportunity**: Add test with metadata values containing special
characters to document and protect the parsing boundary.

---

### 8. orphan_module_detector.py

**Mechanism**: Static import-chain analysis to identify modules with no inbound
imports.

**Strengths:**
- Multi-source import scanning (AST-based)
- Known-entrypoint list prevents false orphan detection
- Dynamic-dispatch package awareness
- Exclusion patterns for test/config files
- Confidence scoring based on module size

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| Heavy hardcoding (entrypoints, exclusions) | Medium | Maintenance burden; new modules need manual registration |
| Relative import resolution gaps | Medium | Failed resolution silently skipped |
| Module path dedup heuristic | Low | `parts[0] == parts[1]` dedup assumes standard layout |
| Name-based classification | Low | "adapter", "shim" in filename affects confidence |

**False-Positive History**: 30 current candidates — several are LOAD_BEARING modules
that appear orphaned to static analysis but are dynamically loaded. The
DYNAMIC_IMPORT_MODULES and DYNAMIC_DISPATCH_PACKAGES lists mitigate this.

**Hardening Opportunity**: Add test asserting that all DYNAMIC_IMPORT_MODULES
entries actually exist on disk (detect stale entries after file deletions).

---

### 9. adr_linkage_validator.py

**Mechanism**: Cross-references ADR documents against governance docs and
validator tool references.

**Strengths:**
- Permissive regex for ADR references (`ADR[\s-]*\d{3}`)
- Status extraction handles multiple Markdown formatting styles
- Recently hardened: `(?:^|/)(\d{3})-[\w-]+\.md` prevents date-string false positives
- No submodule path dependencies

**Risks:**

| Risk | Severity | Details |
|------|----------|---------|
| 3-digit ADR number limit | Low | Won't match 4-digit numbers (unlikely near-term) |
| Tool reference regex limited | Low | Only matches `python tools/foo.py` pattern |
| Hardcoded ADR directory | Low | `"docs/adr"` — standard and unlikely to change |

**False-Positive History**: Had a false-positive on date strings matching `\d{3}`
(e.g., "2026-05-02.md" triggered on "026"). Fixed by requiring path-component
boundary in regex.

**Hardening Opportunity**: None needed. Recent regex fix addressed the only
known false-positive vector.

---

## Cross-Cutting Stability Concerns

### Python Version Compatibility

| Minimum Required | Validators Affected | Feature Used |
|-----------------|-------------------|-------------|
| 3.10+ | `import_boundary_validator` | `sys.stdlib_module_names` |
| 3.11+ (with fallback) | `contracts_fence`, `schema_version_tracker` | `tomllib` |
| 3.6+ | All others | f-strings, pathlib |

**Current environment**: Python 3.14.2 — all features available.
**CI environment**: Must verify 3.10+ is available.

### Path Normalization

All validators use `pathlib.Path` for path operations. No raw string path
manipulation. Cross-platform behavior is handled by Path's platform-aware
internals.

**OS-specific risk**: None identified. All validators use `.as_posix()` for
output/comparison where needed.

### Submodule Path Assumptions

6 of 9 validators hardcode `umrs/` as a path prefix. If the UMRS submodule
path changes, these validators would need updating:

1. `import_boundary_validator` — rule roots
2. `dto_equivalence_check` — file pair paths
3. `schema_version_tracker` — schema module paths
4. `config_drift_validator` — deploy scan roots
5. `exemption_governance_validator` — scan roots
6. `orphan_module_detector` — source roots, entrypoints

**Mitigation**: The submodule path is part of the repository structure and
changing it would require coordinated updates across many files. Low risk.

### Regex Inventory (All Validators)

| Validator | Pattern Count | Fragility Assessment |
|-----------|-------------|---------------------|
| import_boundary | 0 (AST-only) | N/A |
| contracts_fence | 0 (TOML-only) | N/A |
| dto_equivalence | 0 (AST-only) | N/A |
| schema_version | 1 (fallback) | High (simple regex for YAML) |
| stale_name | 1 | Low (word-boundary) |
| config_drift | 3 | Medium (DSN regex) |
| exemption_governance | 4 | High (lookahead metadata) |
| orphan_module | 0 (AST + string match) | N/A |
| adr_linkage | 4 | Low (recently hardened) |

---

## Recommended Additive Hardening

These are safe, test-only improvements that do not change validator behavior:

| # | Validator | Test to Add | Purpose |
|---|-----------|-------------|---------|
| 1 | `schema_version_tracker` | Assert PyYAML importable | Prevent silent fallback to fragile regex |
| 2 | `dto_equivalence_check` | Test with `Optional[X]` vs `X | None` | Document annotation sensitivity |
| 3 | `exemption_governance` | Test metadata with `=` in value | Document parsing boundary |
| 4 | `orphan_module_detector` | Assert DYNAMIC_IMPORT_MODULES files exist | Detect stale entries |
| 5 | `config_drift_validator` | Test with reformatted template | Document whitespace sensitivity |

**These tests are NOT implemented in this review** — they are recommendations
for a future additive-only test PR.

---

## Stability Classification

| Tier | Validators | Meaning |
|------|-----------|---------|
| **Stable** | contracts_fence, stale_name, adr_linkage | Low maintenance; unlikely to produce false positives |
| **Mature** | import_boundary, orphan_module, config_drift | Working well; hardcoded paths are the main maintenance vector |
| **Monitoring** | dto_equivalence, schema_version, exemption_governance | Specific edge cases could trigger issues under evolution |

---

## Conclusion

The validator suite is operationally stable for the current repository structure.
No validators produce false positives in the current state (all 9 pass clean).
The primary maintenance vectors are:

1. **Hardcoded paths** (6 validators) — coupled to repository structure
2. **Regex fragility** (2 validators) — exemption_governance and schema_version
3. **Python version lock** (1 validator) — import_boundary requires 3.10+

No behavioral changes are recommended. The identified hardening opportunities
are additive test cases only.
