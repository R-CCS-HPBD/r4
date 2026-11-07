"""Science core of the Part 2 workflow — standard library + NumPy only.

Reads an event (``event.json``) and its first-P picks (``p_picks.csv``, from
the ISC Bulletin or the synthetic fallback), places the stations around the
epicentre, and scores 1-D velocity models by the RMS of observed − predicted
travel times, each prediction one 3-D eikonal solve. Kept apart from the
UMRS ``Step`` wrappers (``steps.py``) so ``run_local.py`` can run it on a
laptop.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import math
from datetime import datetime

import eikonal
import numpy as np

KM_PER_DEG = 111.195
AK135_CRUST = ((0.0, 5.8), (20.0, 6.5), (35.0, 8.04))
DEPTH_KM = 60.0
MARGIN_KM = 20.0


def parse_event(text: str) -> dict:
    return json.loads(text)


def parse_picks(text: str) -> list[dict]:
    picks = list(csv.DictReader(io.StringIO(text)))
    for p in picks:
        d_km, az = float(p["distance_deg"]) * KM_PER_DEG, math.radians(float(p["azimuth_deg"]))
        p["x_km"], p["y_km"], p["dist_km"] = d_km * math.sin(az), d_km * math.cos(az), d_km
    return picks


def _time(s: str) -> datetime:
    s = s.rstrip("Z")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f" if "." in s else "%Y-%m-%dT%H:%M:%S")


def observed_seconds(picks: list[dict], origin_time: str) -> np.ndarray:
    t0 = _time(origin_time)
    return np.array([(_time(p["pick_time"]) - t0).total_seconds() for p in picks])


def grid_for(picks: list[dict], h: float):
    """Grid shape and corner so that the epicentre (0, 0) is a node."""
    xs = [p["x_km"] for p in picks] + [0.0]
    ys = [p["y_km"] for p in picks] + [0.0]
    lo_x = math.floor((min(xs) - MARGIN_KM) / h) * h
    lo_y = math.floor((min(ys) - MARGIN_KM) / h) * h
    nx = int(math.ceil((max(xs) + MARGIN_KM - lo_x) / h)) + 1
    ny = int(math.ceil((max(ys) + MARGIN_KM - lo_y) / h)) + 1
    nz = int(math.ceil(DEPTH_KM / h)) + 1
    return (nz, ny, nx), lo_x, lo_y


def layered(shape, h: float, layers) -> np.ndarray:
    z = np.arange(shape[0])[:, None, None] * h
    v = np.full(shape, layers[0][1], dtype=float)
    for top, vel in layers[1:]:
        v[np.broadcast_to(z >= top - 1e-9, shape)] = vel
    return v


def predicted_seconds(event: dict, picks: list[dict], layers, h: float) -> np.ndarray:
    shape, lo_x, lo_y = grid_for(picks, h)
    src = (round(event["depth_km"] / h), round(-lo_y / h), round(-lo_x / h))
    T = eikonal.solve(layered(shape, h, layers), h, src)
    out = []
    for p in picks:
        fx, fy = (p["x_km"] - lo_x) / h, (p["y_km"] - lo_y) / h
        i, j = int(fy), int(fx)
        wy, wx = fy - i, fx - j
        top = T[0, i, j] * (1 - wx) + T[0, i, j + 1] * wx
        bottom = T[0, i + 1, j] * (1 - wx) + T[0, i + 1, j + 1] * wx
        out.append(top * (1 - wy) + bottom * wy)
    return np.array(out)


def score(event: dict, picks: list[dict], layers, h: float) -> dict:
    res = observed_seconds(picks, event["origin_time"]) - predicted_seconds(event, picks, layers, h)
    return {
        "layers": [list(layer) for layer in layers],
        "rms_s": round(float(np.sqrt(np.mean(res**2))), 4),
        "mean_s": round(float(np.mean(res)), 4),
    }


def candidates(cfg: dict) -> list[tuple]:
    """Three-layer models from the value lists in a step's config."""
    conrad = cfg.get("conrad_km", 18.0)
    grid = itertools.product(cfg["upper"], cfg["lower"], cfg["moho_km"], cfg["mantle"])
    return [((0.0, u), (conrad, lo), (m, ma)) for u, lo, m, ma in grid]


def report_markdown(event: dict, ranked: list[dict], baseline: dict, n_picks: int, h: float) -> str:
    lines = [
        f"# P-wave residual report — {event.get('region', 'event')}",
        "",
        f"Origin {event['origin_time']}, {event['latitude']} N, {event['longitude']} E, "
        f"depth {event['depth_km']:.1f} km; {n_picks} stations; source: {event.get('source', '')}.",
        f"3-D eikonal (fast sweeping, NumPy), grid spacing {h} km.",
        "",
        "| Model | RMS (s) | Mean (s) |",
        "|---|---|---|",
        f"| ak135 crust | {baseline['rms_s']} | {baseline['mean_s']} |",
    ]
    for r in ranked[:5]:
        desc = ", ".join(f"{v} km/s from {t:g} km" for t, v in r["layers"])
        lines.append(f"| {desc} | {r['rms_s']} | {r['mean_s']} |")
    lines += [
        "",
        f"{len(ranked)} candidate models searched; the five best are listed.",
        "Several models fit almost equally well: Moho depth trades off against velocity.",
        f"Models whose Moho depths differ by less than the grid spacing ({h} km) cannot be "
        "told apart.",
    ]
    return "\n".join(lines) + "\n"
