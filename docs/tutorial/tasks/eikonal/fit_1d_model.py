"""Grid-search a three-layer 1-D P model that best fits the observed arrivals.

    python fit_1d_model.py --data data/kii-channel-2021-12-03 --impl answer_key.eikonal_numba

Every candidate model is one independent 3-D eikonal solve — which is why this
step becomes the fan-out workflow in Part 2. Prints the RMS residual of ak135
and of the best candidate; writes ``<data>/model_fit.csv``.

The origin time and hypocentre stay those of the ISC (located with ak135), so
the fit is a teaching approximation, not a relocation.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import itertools

import japan_event
import models
import numpy as np

UPPER = (5.7, 5.9, 6.1)
LOWER = (6.5, 6.7, 6.9)
MOHO = (24.0, 26.0, 28.0, 30.0, 32.0, 35.0)
MANTLE = (7.7, 7.9, 8.1)
CONRAD_KM = 18.0  # fixed mid-crust boundary (KRSO-1D v2 convention)


def rms_for(layers, event, picks, impl, h, grid):
    shape, lo_x, lo_y = grid
    v = models.layered(shape, h, layers)
    src = (round(event["depth_km"] / h), round(-lo_y / h), round(-lo_x / h))
    T = impl.solve(v, h, src)
    res = [
        japan_event.seconds_after(p["pick_time"], event["origin_time"])
        - japan_event.surface_time(T, p["x_km"], p["y_km"], lo_x, lo_y, h)
        for p in picks
    ]
    return float(np.sqrt(np.mean(np.square(res)))), float(np.mean(res))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/kii-channel-2021-12-03")
    parser.add_argument("--impl", default="eikonal_slow")
    parser.add_argument("--h", type=float, default=2.0)
    args = parser.parse_args()
    from pathlib import Path

    data = Path(args.data)
    impl = importlib.import_module(args.impl)
    event, picks = japan_event.load(data)
    grid = japan_event.build_grid(picks, args.h, 60.0, 20.0)
    base = rms_for(models.AK135_CRUST, event, picks, impl, args.h, grid)
    rows = []
    for up, low, moho, mantle in itertools.product(UPPER, LOWER, MOHO, MANTLE):
        layers = ((0.0, up), (CONRAD_KM, low), (moho, mantle))
        rms, mean = rms_for(layers, event, picks, impl, args.h, grid)
        rows.append(
            {
                "upper": up,
                "lower": low,
                "moho_km": moho,
                "mantle": mantle,
                "rms_s": rms,
                "mean_s": mean,
            }
        )
    rows.sort(key=lambda r: r["rms_s"])
    with (data / "model_fit.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    best = rows[0]
    print(f"ak135 crust: RMS {base[0]:.2f} s (mean {base[1]:+.2f} s)")
    print(
        f"best of {len(rows)}: upper {best['upper']}, lower {best['lower']}, Moho {best['moho_km']} km, "
        f"mantle {best['mantle']} km/s → RMS {best['rms_s']:.2f} s (mean {best['mean_s']:+.2f} s)"
    )


if __name__ == "__main__":
    main()
