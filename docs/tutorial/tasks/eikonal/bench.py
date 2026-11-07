"""Time an implementation against the slow reference on the 3-D basin model.

    python bench.py                                       # reference only
    python bench.py --impl my_fast --sizes 21 41 61       # grids (n/2, n, n)

Prints seconds per solve, the speed-up, and the largest difference from the
reference — speed never at the cost of a different answer. The real workload
(one earthquake, 1.26 M nodes) is ``japan_event.py --impl my_fast``.
"""

from __future__ import annotations

import argparse
import importlib
import time

import eikonal_slow
import models
import numpy as np

H_KM = 1.0


def timed(solve, v, h, src, repeat=1):
    solve(v, h, src)  # warm-up (compilation, caches)
    t0 = time.perf_counter()
    for _ in range(repeat):
        T = solve(v, h, src)
    return (time.perf_counter() - t0) / repeat, T


def row(n: int, impl) -> str:
    shape = (n // 2 + 1, n, n)
    v = models.basin(shape, H_KM)
    src = (2, n // 2, n // 5)
    t_ref, T_ref = timed(eikonal_slow.solve, v, H_KM, src)
    t_new, T_new = timed(impl.solve, v, H_KM, src, repeat=3)
    diff = float(np.max(np.abs(T_new - T_ref)))
    nodes = np.prod(shape) / 1e6
    return f"{nodes:7.2f} M {t_ref:10.2f} s {t_new:10.4f} s {t_ref / t_new:9.1f}× {diff:10.2e}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--impl", default="eikonal_slow")
    parser.add_argument("--sizes", type=int, nargs="+", default=[21, 41])
    args = parser.parse_args()
    impl = importlib.import_module(args.impl)
    print(f"{'nodes':>9} {'reference':>12} {args.impl[-12:]:>12} {'speed-up':>10} {'max |ΔT|':>10}")
    for n in args.sizes:
        print(row(n, impl))


if __name__ == "__main__":
    main()
