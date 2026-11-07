"""Kii Channel data shared by the deck build and the map renderer.

No python-pptx and no cartopy here: ``build_tutorial_deck.py`` (offline deck
build) and ``render_kii_map.py`` (uv script with cartopy) both import this
module. Two sources:

* the SYNTHETIC offline set written by
  ``docs/tutorial/tasks/eikonal/make_synthetic_picks.py`` (the labs' fallback);
* the REAL event (ISC 621611300, 2021-12-03), downloaded by
  ``fetch_isc_picks.py`` into the gitignored ``tasks/eikonal/data/`` folder
  when the map is rendered (review 2026-11-05: slide 6 shows real stations
  and arrival times). Only the rendered figures are committed; the repository
  keeps no copy of the ISC values (decision 2026-10-27). Cite: International
  Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830.
"""

from __future__ import annotations

import csv
import math
import sys
from datetime import datetime
from pathlib import Path

TASK = Path(__file__).resolve().parent.parent / "tasks" / "eikonal"
PICKS_CSV = TASK / "data" / "synthetic-kii" / "p_picks.csv"
REAL_EVENT_ID = "621611300"
REAL_DIR = TASK / "data" / "kii-2021-12-03"
KM_PER_DEG = 111.195
_TIME_FMT = "%Y-%m-%dT%H:%M:%S.%f"

# First-arrival bins: ((en, ja) label, colour, lower s, upper s).
# The colours mirror deck_kit SEAFOAM / OCEAN / NAVY / AMBER.
ARRIVAL_BINS = [
    (("arrival < 5 s", "到達 < 5 秒"), "#5CC8C2", 0.0, 5.0),
    (("5–12 s", "5–12 秒"), "#0E7C86", 5.0, 12.0),
    (("12–20 s", "12–20 秒"), "#0B1F33", 12.0, 20.0),
    (("> 20 s", "> 20 秒"), "#F2A541", 20.0, math.inf),
]
ISOCHRON_TIMES_S = (5.0, 12.0, 20.0, 30.0)
# The real event (review 2026-11-05): first arrivals run from 4.8 s to 38 s.
REAL_ARRIVAL_BINS = [
    (("arrival < 10 s", "到達 < 10 秒"), "#5CC8C2", 0.0, 10.0),
    (("10–20 s", "10–20 秒"), "#0E7C86", 10.0, 20.0),
    (("20–30 s", "20–30 秒"), "#0B1F33", 20.0, 30.0),
    (("≥ 30 s", "≥ 30 秒"), "#F2A541", 30.0, math.inf),
]
REAL_ISOCHRON_TIMES_S = (10.0, 20.0, 30.0)


def _task_import():
    """Put the eikonal task folder on ``sys.path`` (lazily, once)."""
    if str(TASK) not in sys.path:
        sys.path.insert(0, str(TASK))


def _parse_time(text: str) -> datetime:
    return datetime.strptime(text.rstrip("Z"), _TIME_FMT)


def event() -> dict:
    """The synthetic event (``make_synthetic_picks.EVENT``)."""
    _task_import()
    from make_synthetic_picks import EVENT

    return EVENT


def real_event() -> dict:
    """The ISC event (``event.json``); downloads it first when missing (network)."""
    if not (REAL_DIR / "event.json").exists():
        import subprocess

        cmd = [sys.executable, str(TASK / "fetch_isc_picks.py"), "--event", REAL_EVENT_ID]
        subprocess.run([*cmd, "--out", str(REAL_DIR)], check=True)  # noqa: S603 (fixed args)
    import json

    return json.loads((REAL_DIR / "event.json").read_text(encoding="utf-8"))


def load_real_picks() -> list[dict]:
    """The ISC picks, in the same shape as :func:`load_picks`."""
    return load_picks(REAL_DIR / "p_picks.csv", real_event())


def load_picks(path: Path = PICKS_CSV, ev: dict | None = None) -> list[dict]:
    """One dict per pick: station, lon, lat, dist_km, t_s (since origin), phase."""
    t0 = _parse_time((ev or event())["origin_time"])
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [
        {
            "station": row["station"],
            "lon": float(row["longitude"]),
            "lat": float(row["latitude"]),
            "dist_km": float(row["distance_deg"]) * KM_PER_DEG,
            "t_s": (_parse_time(row["pick_time"]) - t0).total_seconds(),
            "phase": row["phase"],
        }
        for row in rows
    ]


def arrival_bin(t_s: float, bins=ARRIVAL_BINS) -> int:
    """Index into ``bins`` (default :data:`ARRIVAL_BINS`) for a first-arrival time."""
    for idx, (_label, _colour, lo, hi) in enumerate(bins):
        if lo <= t_s < hi:
            return idx
    raise ValueError(f"arrival time {t_s} s falls outside every bin")


def isochron_radius_km(
    t_s: float, lo: float = 1.0, hi: float = 320.0, depth: float | None = None
) -> float:
    """Epicentral distance (km) where the KRSO-1D v2 first arrival equals ``t_s``.

    Bisection on ``first_arrival(r, depth, KRSO_1D_V2)``, which grows
    monotonically with distance.
    """
    _task_import()
    from make_synthetic_picks import first_arrival
    from models import KRSO_1D_V2

    depth = event()["depth_km"] if depth is None else depth
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if first_arrival(mid, depth, KRSO_1D_V2)[0] < t_s:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
