"""Answer key B — the same loops, compiled with Numba (``uv pip install numba``).

Algorithm and update order are exactly those of eikonal_slow.py, so the
answers agree to rounding; only the data structure (a NumPy array instead of
nested lists) and the ``@njit`` decorator change. The first call includes
compilation (a few seconds); time the second.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit

SOURCE_RADIUS = 3
TOL = 1e-9
MAX_ITER = 50
ORDERS = np.array([(a, b, c) for a in (1, -1) for b in (1, -1) for c in (1, -1)], dtype=np.int64)


@njit(cache=True)
def _godunov(a, b, c, f):
    if a > b:
        a, b = b, a
    if b > c:
        b, c = c, b
    if a > b:
        a, b = b, a
    t = a + f
    if t <= b:
        return t
    t = 0.5 * (a + b + math.sqrt(2.0 * f * f - (a - b) ** 2))
    if t <= c:
        return t
    s = a + b + c
    return (s + math.sqrt(s * s - 3.0 * (a * a + b * b + c * c - f * f))) / 3.0


@njit(cache=True)
def _axis_min(T, i, j, k):
    nz, ny, nx = T.shape
    a = min(T[i - 1, j, k] if i > 0 else np.inf, T[i + 1, j, k] if i < nz - 1 else np.inf)
    b = min(T[i, j - 1, k] if j > 0 else np.inf, T[i, j + 1, k] if j < ny - 1 else np.inf)
    c = min(T[i, j, k - 1] if k > 0 else np.inf, T[i, j, k + 1] if k < nx - 1 else np.inf)
    return a, b, c


@njit(cache=True)
def _sweep(T, S, h, oi, oj, ok):
    """One Gauss–Seidel sweep visiting nodes in the order given by oi, oj, ok."""
    change = 0.0
    for i in oi:
        for j in oj:
            for k in ok:
                a, b, c = _axis_min(T, i, j, k)
                if a == np.inf and b == np.inf and c == np.inf:
                    continue
                t = _godunov(a, b, c, h * S[i, j, k])
                if t < T[i, j, k]:
                    change = max(change, T[i, j, k] - t if T[i, j, k] < np.inf else t)
                    T[i, j, k] = t
    return change


@njit(cache=True)
def _iterate(T, S, h):
    nz, ny, nx = T.shape
    fwd = (np.arange(nz), np.arange(ny), np.arange(nx))
    bwd = (fwd[0][::-1], fwd[1][::-1], fwd[2][::-1])
    for _ in range(MAX_ITER):
        change = 0.0
        for o in range(ORDERS.shape[0]):
            oi = fwd[0] if ORDERS[o, 0] > 0 else bwd[0]
            oj = fwd[1] if ORDERS[o, 1] > 0 else bwd[1]
            ok = fwd[2] if ORDERS[o, 2] > 0 else bwd[2]
            change = max(change, _sweep(T, S, h, oi, oj, ok))
        if change < TOL:
            break


def solve(velocity, h, src):
    S = 1.0 / np.asarray(velocity, dtype=float)
    T = np.full(S.shape, np.inf)
    si, sj, sk = src
    r = SOURCE_RADIUS
    for i in range(max(0, si - r), min(S.shape[0], si + r + 1)):
        for j in range(max(0, sj - r), min(S.shape[1], sj + r + 1)):
            for k in range(max(0, sk - r), min(S.shape[2], sk + r + 1)):
                T[i, j, k] = (
                    h * math.sqrt((i - si) ** 2 + (j - sj) ** 2 + (k - sk) ** 2) * S[si, sj, sk]
                )
    _iterate(T, S, h)
    return T
