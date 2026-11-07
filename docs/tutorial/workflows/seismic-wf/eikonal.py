"""Fast sweeping eikonal solver, NumPy diagonal-plane version (self-contained copy).

Copied from docs/tutorial/tasks/eikonal/answer_key/eikonal_numpy_diag.py so the
workflow bundle uploads as one directory; keep the two in step. NumPy is
available inside the umrs container, Numba is not.
"""

from __future__ import annotations

import math

import numpy as np

SOURCE_RADIUS = 3
TOL = 1e-9
MAX_ITER = 50


def _planes(shape) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Padded index arrays of every plane i + j + k = s, in sweep order."""
    nz, ny, nx = shape
    i, j, k = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    s = (i + j + k).ravel()
    order = np.argsort(s, kind="stable")
    bounds = np.searchsorted(s[order], np.arange(nz + ny + nx - 1))
    flat = [a.ravel()[order] + 1 for a in (i, j, k)]
    edges = list(bounds) + [s.size]
    return [tuple(a[lo:hi] for a in flat) for lo, hi in zip(edges[:-1], edges[1:], strict=True)]


def _godunov(a, b, c, f):
    """Vectorised 3-D Godunov update; a, b, c, f are 1-D arrays."""
    srt = np.sort(np.stack([a, b, c]), axis=0)
    a, b, c = srt
    t = a + f
    with np.errstate(invalid="ignore"):
        t2 = 0.5 * (a + b + np.sqrt(np.maximum(2.0 * f * f - (a - b) ** 2, 0.0)))
        s = a + b + c
        t3 = (s + np.sqrt(np.maximum(s * s - 3.0 * (a * a + b * b + c * c - f * f), 0.0))) / 3.0
    t = np.where(t <= b, t, np.where(t2 <= c, t2, t3))
    return t


def _sweep(T: np.ndarray, F: np.ndarray, planes) -> float:
    change = 0.0
    for i, j, k in planes:
        a = np.minimum(T[i - 1, j, k], T[i + 1, j, k])
        b = np.minimum(T[i, j - 1, k], T[i, j + 1, k])
        c = np.minimum(T[i, j, k - 1], T[i, j, k + 1])
        reach = np.isfinite(np.minimum(np.minimum(a, b), c))
        if not reach.any():
            continue
        t = np.full(a.shape, np.inf)
        t[reach] = _godunov(a[reach], b[reach], c[reach], F[i, j, k][reach])
        old = T[i, j, k]
        better = t < old
        if better.any():
            o, n = old[better], t[better]
            dec = np.where(np.isinf(o), n, o - n)
            change = max(change, float(dec.max()))
            T[i[better], j[better], k[better]] = n
    return change


def _views(T, F):
    out = []
    for fz in (1, -1):
        for fy in (1, -1):
            for fx in (1, -1):
                sl = (slice(None, None, fz), slice(None, None, fy), slice(None, None, fx))
                out.append((T[sl], F[sl]))
    return out


def solve(velocity, h, src):
    slow = 1.0 / np.asarray(velocity, dtype=float)
    shape = slow.shape
    T = np.full(tuple(n + 2 for n in shape), np.inf)
    F = np.full(T.shape, np.inf)
    F[1:-1, 1:-1, 1:-1] = h * slow
    si, sj, sk = src
    r = SOURCE_RADIUS
    for i in range(max(0, si - r), min(shape[0], si + r + 1)):
        for j in range(max(0, sj - r), min(shape[1], sj + r + 1)):
            for k in range(max(0, sk - r), min(shape[2], sk + r + 1)):
                dist = math.sqrt((i - si) ** 2 + (j - sj) ** 2 + (k - sk) ** 2)
                T[i + 1, j + 1, k + 1] = h * dist * slow[si, sj, sk]
    planes = _planes(shape)
    views = _views(T, F)
    for _ in range(MAX_ITER):
        change = max(_sweep(t, f, planes) for t, f in views)
        if change < TOL:
            break
    return T[1:-1, 1:-1, 1:-1].copy()
