"""Write the SYNTHETIC offline fallback: P picks for an invented event near the Kii Channel.

    python make_synthetic_picks.py [--out data/synthetic-kii]

Used only when the venue network cannot reach the ISC Bulletin. Nothing here
comes from a real catalogue: the event, the stations and the arrival times
are invented. The times are exact first arrivals (direct Pg, Pb refracted at
the mid-crust boundary, Pn head wave along the Moho) through the
observatory's reference model KRSO-1D v2 (knowledge pack, file 01), plus
Gaussian picking noise — so the model search in Part 2 should recover
KRSO-1D v2, and ak135 should look about 1 s slow beyond the crossover.

Same file format as ``fetch_isc_picks.py`` writes, with ``source=synthetic``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

from fetch_isc_picks import destination
from models import KRSO_1D_V2

EVENT = {
    "event_id": "SYNTH-KII-01",
    "origin_time": "2026-01-15T03:12:40.00Z",
    "latitude": 33.80,
    "longitude": 135.15,
    "depth_km": 12.0,
    "region": "Kii Channel (SYNTHETIC event, invented for the tutorial)",
    "source": "synthetic — make_synthetic_picks.py; no real catalogue data",
}
N_STATIONS = 30
SEED = 1203
NOISE_S = 0.08
KM_PER_DEG = 111.195


def _cos_crit(v_slow: float, v_fast: float) -> float:
    return math.sqrt(1.0 - (v_slow / v_fast) ** 2)


def _head_wave(r: float, hs: float, layers, n: int) -> float:
    """Head wave along the top of layer ``n`` (index into ``layers``), or inf before critical."""
    v_ref = layers[n][1]
    t, x_crit = r / v_ref, 0.0
    for m in range(n):
        top, v = layers[m]
        thick = layers[m + 1][0] - top
        legs = 2.0 * thick - (min(hs, layers[m + 1][0]) - top if hs > top else 0.0)
        c = _cos_crit(v, v_ref)
        t += legs * c / v
        x_crit += legs * (v / v_ref) / c
    return t if r >= x_crit else math.inf


def first_arrival(r: float, hs: float, layers=KRSO_1D_V2) -> tuple[float, str]:
    """Earliest of Pg, Pb, Pn for a source at depth hs (km) inside the top layer."""
    candidates = {
        "Pg": math.hypot(r, hs) / layers[0][1],
        "Pb": _head_wave(r, hs, layers, 1),
        "Pn": _head_wave(r, hs, layers, 2),
    }
    phase = min(candidates, key=candidates.get)
    return candidates[phase], phase


def stations(rng: random.Random) -> list[tuple[float, float]]:
    """(distance_km, azimuth_deg) pairs, spread from 15 to 280 km."""
    out = []
    for n in range(N_STATIONS):
        dist = 15.0 + (280.0 - 15.0) * (n + rng.random()) / N_STATIONS
        out.append((dist, rng.uniform(0.0, 360.0)))
    return out


def rows() -> list[dict]:
    rng = random.Random(SEED)
    origin = datetime.strptime(EVENT["origin_time"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")
    out = []
    for n, (dist_km, az) in enumerate(stations(rng)):
        t, phase = first_arrival(dist_km, EVENT["depth_km"])
        t += rng.gauss(0.0, NOISE_S)
        lat, lon = destination(EVENT["latitude"], EVENT["longitude"], dist_km / KM_PER_DEG, az)
        pick = origin + timedelta(seconds=t)
        out.append(
            {
                "network": "SY",
                "station": f"S{n + 1:02d}",
                "phase": phase,
                "pick_time": pick.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
                "distance_deg": round(dist_km / KM_PER_DEG, 4),
                "azimuth_deg": round(az, 2),
                "latitude": lat,
                "longitude": lon,
                "isc_residual_s": "",
                "source": "synthetic",
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parent / "data" / "synthetic-kii"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "event.json").write_text(json.dumps(EVENT, indent=2) + "\n", encoding="utf-8")
    data = rows()
    with (args.out / "p_picks.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)
    print(f"wrote {len(data)} synthetic picks to {args.out}")


if __name__ == "__main__":
    main()
