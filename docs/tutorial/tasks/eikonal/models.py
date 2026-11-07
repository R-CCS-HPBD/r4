"""3-D velocity models and exact first-arrival travel times (units km, km/s, s).

Grid convention: arrays are indexed ``[iz, iy, ix]`` (depth first); node
``(iz, iy, ix)`` sits at ``z = iz * h``, ``y = iy * h``, ``x = ix * h``.

Two models have closed-form travel times, which is what makes a solver
checkable:

- constant velocity:  t = r / v
- linear gradient v(z) = v0 + g z:
  t = arccosh(1 + g^2 r^2 / (2 v(z_s) v(z))) / g      (r = source–receiver distance)

``layered`` builds a 1-D model from (top depth, velocity) pairs, e.g. the
ak135 crust used by the ISC to compute its residuals.
"""

from __future__ import annotations

import numpy as np

# ak135 (Kennett, Engdahl & Buland, 1995) P velocity, top of each layer (km, km/s).
AK135_CRUST = ((0.0, 5.8), (20.0, 6.5), (35.0, 8.04))
# KRSO-1D v2 — the invented Kinki Regional Seismic Observatory's reference model
# (tutorial knowledge pack, file 01). The synthetic fallback picks are computed with it.
KRSO_1D_V2 = ((0.0, 5.9), (18.0, 6.7), (28.0, 7.9))
MODELS = {"ak135": AK135_CRUST, "krso": KRSO_1D_V2}


def grid(shape: tuple[int, int, int], h: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Depth, y and x coordinates of every node, each of ``shape``."""
    nz, ny, nx = shape
    return np.meshgrid(np.arange(nz) * h, np.arange(ny) * h, np.arange(nx) * h, indexing="ij")


def constant(shape, v: float = 6.0) -> np.ndarray:
    return np.full(shape, v)


def gradient(shape, h: float, v0: float = 4.0, g: float = 0.05) -> np.ndarray:
    """v(z) = v0 + g z."""
    z, _, _ = grid(shape, h)
    return v0 + g * z


def layered(shape, h: float, layers=AK135_CRUST) -> np.ndarray:
    """1-D model: each (top, v) pair holds from its top down to the next top."""
    z, _, _ = grid(shape, h)
    v = np.full(shape, layers[0][1], dtype=float)
    for top, vel in layers[1:]:
        v[z >= top - 1e-9] = vel
    return v


def basin(shape, h: float) -> np.ndarray:
    """Two layers over a half-space plus a slow ellipsoidal sedimentary basin."""
    nz, ny, nx = shape
    z, y, x = grid(shape, h)
    depth = (nz - 1) * h
    v = np.where(z < 0.25 * depth, 4.0, np.where(z < 0.6 * depth, 5.8, 6.8))
    cx, cy = 0.45 * (nx - 1) * h, 0.5 * (ny - 1) * h
    rx, ry, rz = 0.18 * (nx - 1) * h, 0.25 * (ny - 1) * h, 0.22 * depth
    inside = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 + (z / rz) ** 2 < 1.0
    return np.where(inside, 2.5, v)


def distance_from(shape, h: float, src) -> np.ndarray:
    z, y, x = grid(shape, h)
    return np.sqrt((z - src[0] * h) ** 2 + (y - src[1] * h) ** 2 + (x - src[2] * h) ** 2)


def exact_constant(shape, h: float, src, v: float = 6.0) -> np.ndarray:
    return distance_from(shape, h, src) / v


def exact_gradient(shape, h: float, src, v0: float = 4.0, g: float = 0.05) -> np.ndarray:
    z, _, _ = grid(shape, h)
    r2 = distance_from(shape, h, src) ** 2
    zs = src[0] * h
    return np.arccosh(1.0 + g * g * r2 / (2.0 * (v0 + g * zs) * (v0 + g * z))) / g
