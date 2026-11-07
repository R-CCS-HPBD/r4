"""Simulate a real Japanese earthquake and compare with observed P arrivals.

    python japan_event.py --data data/kii-channel-2021-12-03 --impl answer_key.eikonal_numba

Reads ``event.json`` + ``p_picks.csv`` (from fetch_isc_picks.py, or the
pre-downloaded copy), builds a 3-D grid around the epicentre with a velocity
model (default: the ak135 crust, which the ISC also uses), solves the eikonal
equation from the ISC hypocentre, reads the predicted travel time at every
station and writes:

- ``<data>/residuals.csv`` — observed − predicted, next to ISC's own ak135 residual
- ``<data>/comparison.png`` — travel time vs distance, and a residual map

Coordinates: azimuthal-equidistant around the epicentre (x east, y north,
km), flat Earth — adequate within ~300 km for a first look.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import time
from datetime import datetime
from pathlib import Path

import models
import numpy as np

KM_PER_DEG = 111.195


def load(data: Path) -> tuple[dict, list[dict]]:
    event = json.loads((data / "event.json").read_text(encoding="utf-8"))
    with (data / "p_picks.csv").open(encoding="utf-8") as fh:
        picks = list(csv.DictReader(fh))
    for p in picks:
        d_km, az = float(p["distance_deg"]) * KM_PER_DEG, math.radians(float(p["azimuth_deg"]))
        p["x_km"], p["y_km"], p["dist_km"] = d_km * math.sin(az), d_km * math.cos(az), d_km
    return event, picks


def seconds_after(t: str, origin: str) -> float:
    def parse(s):  # ISC writes 2 fractional digits; fromisoformat needs 3.11+
        s = s.rstrip("Z")
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f" if "." in s else "%Y-%m-%dT%H:%M:%S")

    return (parse(t) - parse(origin)).total_seconds()


def build_grid(picks: list[dict], h: float, depth_km: float, margin: float):
    """Grid nodes such that the epicentre (0, 0) falls on a node."""
    xs, ys = [p["x_km"] for p in picks], [p["y_km"] for p in picks]
    lo_x = math.floor((min(xs + [0.0]) - margin) / h) * h
    lo_y = math.floor((min(ys + [0.0]) - margin) / h) * h
    nx = int(math.ceil((max(xs + [0.0]) + margin - lo_x) / h)) + 1
    ny = int(math.ceil((max(ys + [0.0]) + margin - lo_y) / h)) + 1
    nz = int(math.ceil(depth_km / h)) + 1
    return (nz, ny, nx), lo_x, lo_y


def surface_time(T: np.ndarray, x: float, y: float, lo_x: float, lo_y: float, h: float) -> float:
    """Bilinear interpolation of T on the surface (z = 0) at (x, y)."""
    fx, fy = (x - lo_x) / h, (y - lo_y) / h
    i, j = int(fy), int(fx)
    wy, wx = fy - i, fx - j
    top = T[0, i, j] * (1 - wx) + T[0, i, j + 1] * wx
    bottom = T[0, i + 1, j] * (1 - wx) + T[0, i + 1, j + 1] * wx
    return float(top * (1 - wy) + bottom * wy)


def simulate(event, picks, impl, h, depth_km, margin, layers=models.AK135_CRUST):
    shape, lo_x, lo_y = build_grid(picks, h, depth_km, margin)
    v = models.layered(shape, h, layers)
    src = (round(event["depth_km"] / h), round(-lo_y / h), round(-lo_x / h))
    t0 = time.perf_counter()
    T = impl.solve(v, h, src)
    elapsed = time.perf_counter() - t0
    for p in picks:
        p["observed_s"] = seconds_after(p["pick_time"], event["origin_time"])
        p["predicted_s"] = surface_time(T, p["x_km"], p["y_km"], lo_x, lo_y, h)
        p["residual_s"] = p["observed_s"] - p["predicted_s"]
    return T, shape, (lo_x, lo_y), elapsed


def write_residuals(data: Path, picks: list[dict]) -> None:
    cols = [
        "network",
        "station",
        "phase",
        "dist_km",
        "observed_s",
        "predicted_s",
        "residual_s",
        "isc_residual_s",
        "latitude",
        "longitude",
    ]
    with (data / "residuals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in picks:
            w.writerow([round(p[c], 3) if isinstance(p[c], float) else p[c] for c in cols])


def _plot_curve(ax, picks: list[dict], T, origin_xy, h) -> None:
    lo_x, lo_y = origin_xy
    j0, i0 = round(-lo_x / h), round(-lo_y / h)
    rays = [T[0, i0, j0:], T[0, i0, j0::-1], T[0, i0:, j0], T[0, i0::-1, j0]]
    ray = max(rays, key=len)  # 1-D model: time depends on distance only — use the longest ray
    ax.plot(np.arange(len(ray)) * h, ray, color="#0E7C86", label="simulated (3-D eikonal)")
    for phase, marker in (("Pg", "o"), ("Pb", "s"), ("Pn", "^"), ("P", "D")):
        sel = [p for p in picks if p["phase"] == phase]
        if sel:
            ax.scatter(
                [p["dist_km"] for p in sel],
                [p["observed_s"] for p in sel],
                marker=marker,
                color="#F2A541",
                edgecolor="k",
                zorder=3,
                label=f"observed {phase}",
            )
    ax.set_xlabel("epicentral distance (km)")
    ax.set_ylabel("P travel time (s)")
    ax.set_title("Observed vs simulated first arrivals")
    ax.legend(fontsize=9)


def _plot_map(fig, ax, event: dict, picks: list[dict]) -> None:
    res = np.array([p["residual_s"] for p in picks])
    lim = max(0.5, float(np.abs(res).max()))
    sc = ax.scatter(
        [float(p["longitude"]) for p in picks],
        [float(p["latitude"]) for p in picks],
        c=res,
        cmap="RdBu_r",
        vmin=-lim,
        vmax=lim,
        s=90,
        edgecolor="k",
    )
    ax.plot(
        event["longitude"],
        event["latitude"],
        marker="*",
        color="#F2A541",
        markersize=18,
        markeredgecolor="k",
    )
    ax.set_xlabel("longitude (°E)")
    ax.set_ylabel("latitude (°N)")
    ax.set_title("Residual: observed − simulated (s)")
    ax.set_aspect(1 / math.cos(math.radians(event["latitude"])))
    fig.colorbar(sc, ax=ax, label="late (red) / early (blue), s")


def plot(data: Path, event: dict, picks: list[dict], T, origin_xy, h) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    _plot_curve(ax1, picks, T, origin_xy, h)
    _plot_map(fig, ax2, event, picks)
    fig.suptitle(
        f"{event['region']} · {event['origin_time']} · depth {event['depth_km']:.0f} km "
        "· picks: ISC Bulletin (doi:10.31905/D808B830)",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(data / "comparison.png", dpi=150)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data/kii-channel-2021-12-03"))
    parser.add_argument("--model", choices=sorted(models.MODELS), default="ak135")
    parser.add_argument("--impl", default="eikonal_slow")
    parser.add_argument("--h", type=float, default=2.0, help="grid spacing, km")
    parser.add_argument("--depth", type=float, default=60.0, help="model depth, km")
    parser.add_argument("--margin", type=float, default=20.0, help="km around the stations")
    args = parser.parse_args()
    impl = importlib.import_module(args.impl)
    event, picks = load(args.data)
    T, shape, origin_xy, elapsed = simulate(
        event, picks, impl, args.h, args.depth, args.margin, models.MODELS[args.model]
    )
    write_residuals(args.data, picks)
    plot(args.data, event, picks, T, origin_xy, args.h)
    res = np.array([p["residual_s"] for p in picks])
    isc = np.array([float(p["isc_residual_s"] or "nan") for p in picks])
    print(
        f"grid {shape} = {np.prod(shape) / 1e6:.2f} M nodes, solved in {elapsed:.2f} s ({args.impl})"
    )
    print(
        f"{len(picks)} stations: residual mean {res.mean():+.2f} s, RMS {np.sqrt((res**2).mean()):.2f} s"
    )
    if np.isfinite(isc).all():
        print(
            f"ours − ISC's ak135 residual: mean {np.mean(res - isc):+.2f} s, "
            f"std {np.std(res - isc):.2f} s"
        )


if __name__ == "__main__":
    main()
