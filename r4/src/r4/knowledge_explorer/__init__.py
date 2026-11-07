"""U4: Knowledge explorer (`r4 knowledge explore`).

Three-pane TUI over the UMRS research layer: section tree (artifacts /
findings / memory / plans) → documents with verification/coverage badges
→ detail pane (evidence chain via lineage, readiness, export action).

``data.py`` holds the client-facing fetch helpers (testable without a
TUI); ``app.py`` hosts the Textual app shell.
"""
