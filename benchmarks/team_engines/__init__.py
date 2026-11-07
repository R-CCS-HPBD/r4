"""Cross-engine benchmark harness (ADR-020 §13).

Submits the same TeamSpec cases to every selected engine through the
public TeamService API (never internals), meters with authoritative
broker/result data, grades outcomes, and writes append-only JSONL per
(case, engine) plus a markdown comparison table.
"""

from __future__ import annotations
