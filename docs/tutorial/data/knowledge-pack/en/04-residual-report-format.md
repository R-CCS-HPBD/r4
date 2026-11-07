# Residual report format (rule RR-2)

> Tutorial material; the observatory's report format is invented.

Every residual report contains, in this order:

1. **Header:** event (origin time UTC, latitude, longitude, depth), source of
   the picks (catalogue name and citation), reference model name and version.
2. **Table**, one row per station: station code, epicentral distance (km),
   phase, observed travel time (s), predicted travel time (s), residual
   (s, observed − predicted), accepted / rejected (with the PQ rule).
3. **Summary:** number of accepted stations, mean residual, RMS residual,
   and the mean residual of Pg and Pn separately.
4. **Figure:** travel time against epicentral distance — observed picks as
   points, the model prediction as a line.
5. **Reproducibility line:** the solver and grid spacing used, and the
   command that re-creates the report.

A report without its pick source and model version is not accepted.
