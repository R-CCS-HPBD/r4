# KRSO-1D v2 — the observatory's reference P-velocity model

> Tutorial material. The Kinki Regional Seismic Observatory (KRSO), its
> model and every number in this file are **invented for the R4 + UMRS
> tutorial**. They are deliberately things no language model can know, so
> the RAG on/off comparison in Lab 1.4 has a clear answer.

## The model

| Layer | Top (km) | Bottom (km) | P velocity (km/s) |
|---|---|---|---|
| Upper crust | 0 | 18 | 5.9 |
| Lower crust | 18 | 28 | 6.7 |
| Uppermost mantle | 28 (Moho) | — | 7.9 |

The mid-crust boundary ("Conrad") is fixed at **18 km** and the **Moho at
28 km**. Station elevations are ignored and no station corrections are
applied in v2.

## When to use which model (rule RM-1)

- Events and stations inside the KRSO network area (Kinki, Kii Channel,
  eastern Shikoku): **KRSO-1D v2**.
- Paths longer than 3° epicentral distance: the global model **ak135**.
- Never mix models within one residual report.

## History

- **v1 (2019)** had the Moho at 32 km and a mantle velocity of 8.0 km/s.
- **v2 (2025)** moved the Moho up to 28 km and lowered the mantle velocity to
  7.9 km/s after the review of Kii Channel events showed Pn arriving
  systematically early (see `03-event-review-notes.md`).
