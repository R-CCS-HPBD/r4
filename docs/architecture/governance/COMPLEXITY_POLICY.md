# Complexity Policy

This repository treats cyclomatic complexity as an architectural maintainability
signal, not a metric to game. Refactors should reduce branching by clarifying
responsibilities, isolating side effects, and preserving public behavior.

## Thresholds

- Target: each function or method should be at most 9.
- Hard warning: 15 or higher.
- Critical: 25 or higher.

Existing critical functions are tracked in audit reports and should be reduced
incrementally by subsystem. New or changed functions are gated in CI.

## CI Gate

The main CI workflow runs a changed-file complexity gate:

- Python: `tools/complexity_audit.py`, Ruff McCabe `C901`, Radon, and Xenon.
- Polyglot source files: Lizard with `-C 9` for JavaScript, TypeScript, Rust,
  Go, C, C++, and Python.
- Shell: ShellCheck for shell quality before decomposition work.

The gate is changed-file scoped so existing complexity debt does not block every
PR. Refactoring campaigns should still run full audits and update reports.

## Local Commands

Run a full Python audit:

```bash
python tools/complexity_audit.py . --max-complexity 9
```

Run the gate for specific files:

```bash
python tools/complexity_audit.py path/to/file.py --max-complexity 9
ruff check --select C901 --config 'lint.mccabe.max-complexity=9' path/to/file.py
```

Run Lizard for polyglot files when installed:

```bash
lizard -C 9 path/to/file.py path/to/file.ts path/to/file.cpp
```

## Preferred Refactoring Patterns

- Extract orchestration, validation, policy decisions, and side effects into
  named helpers with small contracts.
- Replace repeated scanner or parser loops with reusable collection helpers.
- Keep security decisions explicit; do not hide auth, token, ACL, CURVE/ZMQ, or
  permission checks behind generic abstractions.
- Preserve serialization formats, CLI output, protocol payloads, retry behavior,
  and async/concurrency semantics.
- Add or update focused tests before widening a refactor to adjacent modules.

## Reporting

Each campaign slice should record:

- file and function
- before and after complexity
- refactoring approach
- validation commands
- remaining exceptions and rationale