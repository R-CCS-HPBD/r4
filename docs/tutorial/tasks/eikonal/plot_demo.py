"""Draw a vertical slice of the 3-D basin model with first-arrival wavefronts.

python plot_demo.py [--impl eikonal_slow] [--n 61] [--out wavefronts.png]
"""

from __future__ import annotations

import argparse
import importlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import models  # noqa: E402
import numpy as np  # noqa: E402

H_KM = 0.5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--impl", default="eikonal_slow")
    parser.add_argument("--n", type=int, default=61)
    parser.add_argument("--out", default="wavefronts.png")
    args = parser.parse_args()
    n = args.n
    shape = (n // 2 + 1, n, n)
    v = models.basin(shape, H_KM)
    src = (shape[0] // 6, n // 2, n // 5)
    T = importlib.import_module(args.impl).solve(v, H_KM, src)
    sl = src[1]  # the vertical x–z plane through the source
    width, depth = (n - 1) * H_KM, (shape[0] - 1) * H_KM
    fig, ax = plt.subplots(figsize=(8, 4.8))
    im = ax.imshow(v[:, sl, :], extent=(0, width, depth, 0), cmap="cividis")
    ax.contour(
        np.linspace(0, width, n),
        np.linspace(0, depth, shape[0]),
        T[:, sl, :],
        levels=np.arange(0.5, T.max(), 0.5),
        colors="white",
        linewidths=0.8,
    )
    ax.plot(src[2] * H_KM, src[0] * H_KM, marker="*", color="#F2A541", markersize=16)
    ax.set_xlabel("x (km)")
    ax.set_ylabel("depth (km)")
    ax.set_title("3-D eikonal: wavefronts every 0.5 s (slice through the source)")
    fig.colorbar(im, ax=ax, label="P velocity (km/s)", shrink=0.85)
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)
    print("saved", args.out)


if __name__ == "__main__":
    main()
