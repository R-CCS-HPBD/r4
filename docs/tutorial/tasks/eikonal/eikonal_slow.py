"""3-D first-arrival travel times by the fast sweeping method — the SLOW reference.

Solves the eikonal equation |grad T| = 1 / v on a 3-D grid with a point
source: first-order Godunov upwind update and Gauss–Seidel sweeps in the
eight diagonal orderings (Zhao, 2005). Written the way a first working
version often is — plain Python loops over nested lists. Correct, and
deliberately slow: the attendee's job (with the agent) is to make it fast
without changing a single answer.

    from eikonal_slow import solve
    T = solve(velocity, h, (iz, iy, ix))   # velocity: 3-D array [iz, iy, ix] km/s; h: km

Any faster version must keep this signature and pass test_eikonal.py.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

INF = float("inf")
SOURCE_RADIUS = 3  # nodes around the source initialised with straight-ray times
TOL = 1e-9
MAX_ITER = 50


def godunov(a, b, c, f):
    """Smallest t with sum of ((t - n)+)^2 over the three axis minima = f^2."""
    a, b, c = sorted((a, b, c))
    t = a + f
    if t <= b:
        return t
    t = 0.5 * (a + b + math.sqrt(2.0 * f * f - (a - b) ** 2))
    if t <= c:
        return t
    s = a + b + c
    return (s + math.sqrt(s * s - 3.0 * (a * a + b * b + c * c - f * f))) / 3.0


def _axis_min(T, i, j, k, shape):
    nz, ny, nx = shape
    a = min(T[i - 1][j][k] if i > 0 else INF, T[i + 1][j][k] if i < nz - 1 else INF)
    b = min(T[i][j - 1][k] if j > 0 else INF, T[i][j + 1][k] if j < ny - 1 else INF)
    c = min(T[i][j][k - 1] if k > 0 else INF, T[i][j][k + 1] if k < nx - 1 else INF)
    return a, b, c


def _init(slow, h, src, shape):
    nz, ny, nx = shape
    T = [[[INF] * nx for _ in range(ny)] for _ in range(nz)]
    si, sj, sk = src
    r = SOURCE_RADIUS
    for i in range(max(0, si - r), min(nz, si + r + 1)):
        for j in range(max(0, sj - r), min(ny, sj + r + 1)):
            for k in range(max(0, sk - r), min(nx, sk + r + 1)):
                dist = math.sqrt((i - si) ** 2 + (j - sj) ** 2 + (k - sk) ** 2)
                T[i][j][k] = h * dist * slow[si][sj][sk]
    return T


def _sweep(T, slow, h, orders, shape):
    change = 0.0
    oi, oj, ok = orders
    for i in oi:
        for j in oj:
            for k in ok:
                a, b, c = _axis_min(T, i, j, k, shape)
                if a == INF and b == INF and c == INF:
                    continue
                t = godunov(a, b, c, h * slow[i][j][k])
                if t < T[i][j][k]:
                    change = max(change, T[i][j][k] - t if T[i][j][k] < INF else t)
                    T[i][j][k] = t
    return change


def _orders(shape):
    """The eight sweep orderings: every axis forwards or backwards."""
    axes = [(range(n), range(n - 1, -1, -1)) for n in shape]
    return list(itertools.product(*axes))


def solve(velocity, h, src):
    """Travel time (s) from ``src`` = (iz, iy, ix) to every node."""
    vel = np.asarray(velocity, dtype=float)
    shape = vel.shape
    slow = (1.0 / vel).tolist()
    T = _init(slow, h, src, shape)
    orders = _orders(shape)
    for _ in range(MAX_ITER):
        change = max(_sweep(T, slow, h, o, shape) for o in orders)
        if change < TOL:
            break
    return np.array(T)
