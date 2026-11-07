# Domain primer — seismic first arrivals

> General background for the tutorial's subject. Unlike files 01–04 this is
> textbook knowledge, so a capable model may already know most of it: use it
> to practise checking RAG answers **against their source**.

## P waves and the eikonal equation

P (compressional) waves are the fastest seismic waves, so they arrive first.
In the high-frequency (ray) limit their first-arrival travel time T obeys
the **eikonal equation** |∇T| = 1 / v, where v is the P velocity. Grid
methods such as **fast marching** and **fast sweeping** solve it directly
for every point of a model.

## Crustal phases

- **Pg** — the direct wave through the upper crust.
- **Pb** (also P*) — refracted along a mid-crust velocity increase.
- **Pn** — a **head wave** travelling along the top of the mantle just below
  the **Moho**, the crust–mantle boundary.

Near the source Pg arrives first. Beyond the **crossover distance** Pn
overtakes it, because it spends most of its path in the faster mantle. For a
single layer of thickness h and velocity v1 over a half-space of velocity
v2, with source and receiver at the surface, the crossover distance is
x = 2h √((v2 + v1) / (v2 − v1)). Typical continental Moho depths are
25–45 km and uppermost-mantle P velocities about 7.8–8.2 km/s.

## Reference models

- **ak135** (Kennett, Engdahl & Buland, 1995) is a global 1-D model; its
  crust has a 35 km Moho. International catalogues such as the ISC Bulletin
  compute travel-time residuals against it.
- Regional networks use regional 1-D models; the Japan Meteorological Agency,
  for example, locates earthquakes with its own model, JMA2001.

## Residuals

A residual is observed minus predicted travel time. A systematic negative
mean (early arrivals) means the model is too slow along those paths — or the
origin time and location are biased. Location and structure trade off
against each other: always say what was held fixed.
