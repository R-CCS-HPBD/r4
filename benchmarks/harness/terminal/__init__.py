"""Terminal-Bench lane: run the R4 Companion under Harbor (terminal-bench 4.0).

Parallel to the SWE-bench lane (`swe/`): Harbor schedules the task container,
this lane's installed agent mirrors the companion runtime into it, and the
in-container driver executes one CompanionSession per task instruction.
"""
