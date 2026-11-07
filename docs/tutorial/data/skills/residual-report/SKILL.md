---
name: residual-report
description: "Writes a P-wave travel-time residual report under the tutorial observatory's rules (reference model KRSO-1D v2, pick rules PQ-1..PQ-7, report format RR-2). Use for residuals, travel times, picks, Pn, Pg, Moho, residual report."
allowed-tools: "python_exec file_read file_write search_docs shell_exec"
metadata:
  x-r4-keywords: "residual, residuals, travel time, pick, picks, Pn, Pg, Moho, residual report, KRSO, 残差, 走時, モホ"
---

You write travel-time residual reports for the Kinki Regional Seismic
Observatory (an invented observatory; tutorial material in
docs/tutorial/data/knowledge-pack).

## Procedure

1. Look up the rules before computing: search the project's documents for
   "KRSO-1D v2", "PQ-3" and "RR-2". Never rely on memory for model values or
   thresholds — quote the source document.
2. Use the reference model the rules require (RM-1) and say which one.
3. Compute residuals as observed − predicted with the tutorial's eikonal
   solver (docs/tutorial/tasks/eikonal); keep the first P per station (PQ-1),
   reject |residual| > 2.5 s (PQ-3), check Pn labels against 140 km (PQ-5).
4. Name the pick source and cite it. Downloaded ISC data: "International
   Seismological Centre (2026), On-line Bulletin,
   https://doi.org/10.31905/D808B830". The offline fallback is SYNTHETIC —
   say so.

## Output

The RR-2 report: header, station table, summary (accepted stations, mean
and RMS, Pg and Pn means), travel-time figure, reproducibility line.
Recompute one station's residual by hand and say which one you checked.
