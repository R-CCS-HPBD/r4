"""Old lab script: observed P travel times against distance (Lab 2.4 "existing code").

Deliberately written the way analysis scripts often are: hard-coded paths,
matplotlib, runs top to bottom. In Lab 2.4 the agent turns it into a fourth
workflow step ('plot', after 'rank'). The step cannot import matplotlib (on
the stand-alone dev profile steps run inside the umrs container), so the port
has to draw the figure another way, e.g. write an SVG by hand.
"""

import csv
import json
from datetime import datetime

import matplotlib.pyplot as plt

DATA = "../../tasks/eikonal/data/synthetic-kii"
KM_PER_DEG = 111.195


def seconds(t):
    return datetime.strptime(t.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")


with open(DATA + "/event.json") as fh:
    event = json.load(fh)
t0 = seconds(event["origin_time"])
dist, tt, phase = [], [], []
with open(DATA + "/p_picks.csv") as fh:
    for row in csv.DictReader(fh):
        dist.append(float(row["distance_deg"]) * KM_PER_DEG)
        tt.append((seconds(row["pick_time"]) - t0).total_seconds())
        phase.append(row["phase"])

plt.figure(figsize=(8, 5))
for ph, mk in (("Pg", "o"), ("Pb", "s"), ("Pn", "^")):
    sel = [i for i, p in enumerate(phase) if p == ph]
    plt.scatter([dist[i] for i in sel], [tt[i] for i in sel], marker=mk, label=ph)
plt.xlabel("epicentral distance (km)")
plt.ylabel("P travel time (s)")
plt.title(event["region"])
plt.legend()
plt.tight_layout()
plt.savefig("travel_times.png", dpi=120)
print("saved travel_times.png")
