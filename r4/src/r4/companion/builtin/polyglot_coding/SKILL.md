---
name: polyglot_coding
description: "Polyglot build, run, and test support for C/C++/Fortran/R/Python"
allowed-tools: "file_read file_write file_edit list_dir grep_search glob_search detect_code_context detect_language_servers start_language_servers compile_code run_code test_code shell_exec"
metadata:
  x-r4-keywords: "c code, c source, c++, cpp, fortran, f90, rscript, gcc, g++, gfortran, cmake, makefile, compiler, compile, linker, openmp, mpi, r language, python, pytest"
  x-r4-dependencies: "code"
---

You help with agentic coding across C, C++, Fortran, R, and Python.

## Workflow (adapt per file count, high freedom)

1. `detect_code_context` FIRST — it grounds build-system and toolchain
   choices in the repository (Make/CMake/module files) instead of guesses.
2. `detect_language_servers` / `start_language_servers` when semantic
   navigation or refactoring would beat text search; grep_search /
   glob_search otherwise.
3. Plan short, edit small, then VERIFY before the next edit:
   `compile_code` (or syntax check) → `run_code` for smoke → `test_code`
   for the focused suite. Diagnose failures from the actual output, fix one
   thing, repeat.
4. Multi-file or structurally exact changes: prefer apply_patch via the
   code skill's discipline; shell builds via shell_exec only when the
   language-aware tools cannot express the step.

## Constraints

- HPC-first: prefer Make/CMake/module-aware workflows already present in
  the repository; never invent a new build system alongside the existing one.
- Do not invent durable job orchestration locally — cluster-side execution
  belongs to UMRS workflows (see workflow_ops).
- Compiler/MPI flags: mirror what the repo's build files already use; do not
  sprinkle optimization flags the build system did not declare.
