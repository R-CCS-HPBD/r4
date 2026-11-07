---
name: analysis
description: "Runs reproducible data analysis and visualization in Python. Use for plots, charts, statistics, numpy/pandas/matplotlib/scipy work."
allowed-tools: "python_exec file_read file_write"
metadata:
  x-r4-keywords: "plot, graph, chart, analyse, analyze, visuali, dataframe, numpy, pandas, matplotlib, scipy, statistics"
---

You do data analysis with short, reproducible Python.

## Working pattern

1. Inspect the data first (file_read for small files; for large/remote lab
   data hand off to the data_management skill's catalog tools instead of
   guessing paths).
2. python_exec for interactive exploration; when the analysis must be
   repeatable, write the script with file_write and say how to re-run it.
3. Save artifacts to files (CSV for tables, PNG for figures) and report the
   exact paths. Never leave results only in stdout when the user asked for
   a deliverable.

## Platform specifics the model must not rediscover

- Headless environment: use `matplotlib.use("Agg")` before `pyplot` import;
  never call `plt.show()`. Save with `fig.savefig(path, dpi=150,
  bbox_inches="tight")`.
- numpy/pandas/matplotlib/scipy are available; do not pip-install inside
  the companion. If a package is missing, report it instead of installing.
- Long computations: keep single python_exec runs short; split stages into
  separate calls with intermediate files so failures are resumable.

## Quality bar

- Show the key numbers, not just the plot
- State assumptions (null handling, filters, units) in one line
- Prefer a small printed summary table plus one figure over many figures
