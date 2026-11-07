"""Download observed P-wave arrival times for one earthquake from the ISC Bulletin.

    python fetch_isc_picks.py --event 621611300 [--max-deg 2.5] [--out data/kii-2021-12-03]

Writes ``<out>/event.json`` (ISC hypocentre and origin time) and
``<out>/p_picks.csv`` (one row per station: network, station, phase, pick
time, epicentral distance and azimuth, station latitude/longitude rebuilt from
distance and azimuth, and ISC's own residual against ak135).

Standard library only. Data: International Seismological Centre (2026),
On-line Bulletin, https://doi.org/10.31905/D808B830 — cite it whenever these
data are used (https://www.isc.ac.uk/iscbulletin/citing.php).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

URL = "https://www.isc.ac.uk/fdsnws/event/1/query?eventid={event}&includearrivals=true"
NS = {"q": "http://quakeml.org/xmlns/bed/1.2"}
P_PHASES = {"P", "Pg", "Pn", "Pb"}


def fetch(event: str) -> ET.Element:
    req = urllib.request.Request(URL.format(event=event), headers={"User-Agent": "r4-tutorial"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (fixed https URL)
        return ET.fromstring(resp.read())


def text(node: ET.Element, path: str) -> str | None:
    found = node.find(path, NS)
    return None if found is None else found.text


def destination(lat: float, lon: float, dist_deg: float, az_deg: float) -> tuple[float, float]:
    """Point at great-circle distance and azimuth from (lat, lon), spherical Earth."""
    la, d, az = math.radians(lat), math.radians(dist_deg), math.radians(az_deg)
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(az))
    dlon = math.atan2(
        math.sin(az) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2)
    )
    return round(math.degrees(la2), 4), round(lon + math.degrees(dlon), 4)


def origin_of(event: ET.Element) -> dict:
    o = event.find("q:origin", NS)
    return {
        "isc_event_id": event.get("publicID", "").rsplit("=", 1)[-1],
        "origin_time": text(o, "q:time/q:value"),
        "latitude": float(text(o, "q:latitude/q:value")),
        "longitude": float(text(o, "q:longitude/q:value")),
        "depth_km": float(text(o, "q:depth/q:value")) / 1000.0,
        "region": text(event, "q:description/q:text"),
        "source": "International Seismological Centre (2026), On-line Bulletin, "
        "https://doi.org/10.31905/D808B830",
    }


def p_rows(event: ET.Element, origin: dict, max_deg: float) -> list[dict]:
    picks = {p.get("publicID"): p for p in event.findall("q:pick", NS)}
    rows: dict[str, dict] = {}
    for arr in event.find("q:origin", NS).findall("q:arrival", NS):
        phase, dist = text(arr, "q:phase"), float(text(arr, "q:distance") or 1e9)
        pick = picks.get(text(arr, "q:pickID"))
        if phase not in P_PHASES or dist > max_deg or pick is None:
            continue
        wid = pick.find("q:waveformID", NS)
        az = float(text(arr, "q:azimuth"))
        lat, lon = destination(origin["latitude"], origin["longitude"], dist, az)
        row = {
            "network": wid.get("networkCode", ""),
            "station": wid.get("stationCode", ""),
            "phase": phase,
            "pick_time": text(pick, "q:time/q:value"),
            "distance_deg": dist,
            "azimuth_deg": az,
            "latitude": lat,
            "longitude": lon,
            "isc_residual_s": text(arr, "q:timeResidual"),
        }
        key = row["station"]
        if key not in rows or row["pick_time"] < rows[key]["pick_time"]:
            rows[key] = row  # keep the first P per station
    return sorted(rows.values(), key=lambda r: r["distance_deg"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="ISC event id")
    parser.add_argument("--max-deg", type=float, default=2.5, help="epicentral distance limit")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = fetch(args.event)
    event = root.find(".//q:event", NS)
    origin = origin_of(event)
    rows = p_rows(event, origin, args.max_deg)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "event.json").write_text(json.dumps(origin, indent=2) + "\n", encoding="utf-8")
    with (args.out / "p_picks.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"{origin['region']} {origin['origin_time']}: {len(rows)} stations within {args.max_deg}°"
    )


if __name__ == "__main__":
    main()
