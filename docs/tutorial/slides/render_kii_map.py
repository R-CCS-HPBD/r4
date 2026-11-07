# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cartopy>=0.24",
#   "matplotlib>=3.9",
#   "numpy>=1.26",
#   "scipy>=1.12",
#   "fonttools>=4.50",
# ]
# ///
"""Render the Kii Channel figures of the story slide, EN and JA.

    uv run --script docs/tutorial/slides/render_kii_map.py [--lang en|ja|both]
        [--cache-dir DIR] [--out docs/tutorial/slides/assets]

Writes ``kii-station-map.{en,ja}.png`` (6.2 x 3.45 in) and
``kii-traveltime.{en,ja}.png`` (5.7 x 3.8 in), 300 dpi, which the deck build
inserts at native size; the deck build itself never imports cartopy. Needs
the network: the ETOPO1 subset (0.34 MB, NOAA ERDDAP) and Natural Earth 1:10m
(about 42 MB) are cached under ``--cache-dir`` (default
``${XDG_CACHE_HOME:-~/.cache}/r4-tutorial-maps``), and the ISC picks are
downloaded into the gitignored ``tasks/eikonal/data/kii-2021-12-03`` — never
committed; only the two figures are.

Data: the REAL event, ISC 621611300 (2021-12-03, Kii Channel): International
Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830; station
positions rebuilt from ISC's distance and azimuth (fetch_isc_picks.py).
ETOPO1 1 arc-minute global relief (Amante & Eakins 2009, NOAA NCEI,
doi:10.7289/V5C8276M, public domain); Natural Earth (public domain). Review
2026-11-05 replaced the synthetic stations with the real ones.

Japanese font, first found: ``$R4_MAP_JA_FONT`` (a file path); a static
Noto Sans JP / Noto Sans CJK JP / IPAexGothic; a weight-400 instance of the
system's variable NotoSansCJK-VF.ttc (made once with fontTools, cached; the
variable file itself renders at weight 100); Droid Sans Japanese.
"""

from __future__ import annotations

import argparse
import os
import time
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import cartopy  # noqa: E402
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import kii_story_data as ksd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from cartopy.geodesic import Geodesic  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib import patheffects as pe  # noqa: E402
from matplotlib.colors import LightSource, LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from scipy.io import netcdf_file  # noqa: E402

HERE = Path(__file__).resolve().parent
PC = ccrs.PlateCarree()

INK, MUTED, ALERT, WHITE = "#1B2B3A", "#5B6B7A", "#D1495B", "#FFFFFF"
LAKE = "#E3F0F1"
SEA_RAMP = ["#2F5F7A", "#6FA3B8", "#B9D8DF", "#E3F0F1"]
LAND_RAMP = ["#E9EEDC", "#C9D3A9", "#B7AE86", "#9A8466", "#F2EEE8"]

DEM_BOX = (131.5, 139.0, 31.0, 37.0)  # lon0, lon1, lat0, lat1
DEM_NAME = "etopo1_kii_131.5-139.0_31.0-37.0.nc"
DEM_SHAPE = (361, 451)
DEM_HOSTS = ("coastwatch.pfeg.noaa.gov", "upwell.pfeg.noaa.gov")
DEM_QUERY = "/erddap/griddap/etopo180.nc?altitude%5B(31.0):1:(37.0)%5D%5B(131.5):1:(139.0)%5D"
EXTENT = (132.3, 138.1, 31.75, 36.25)
SCALE_START = (132.45, 31.93)  # lower left, over the sea
SCALE_KM = 50

FIGSIZE = (6.2, 3.45)
DPI = 300
MAP_RECT = [0.055, 0.075, 0.57, 0.91]
SIDE_X = 0.645

TEXT = {
    "en": {
        "epicentre": "epicentre (ISC,\n{d:.0f} km deep)",
        "land": "station (ISC Bulletin)",
        "iso": "predicted first arrival\n(KRSO-1D v2)",
        "iso_unit": "{t} s",
        "cbar": "elevation / depth (m)",
        "attr": "Picks: ISC Bulletin (2026), doi:10.31905/D808B830\n"
        "Relief: ETOPO1 (NOAA NCEI) · coast: Natural Earth",
        "tt_title": "Travel time vs distance — real picks (ISC)",
        "tt_x": "epicentral distance (km)",
        "tt_y": "travel time after the origin (s)",
        "tt_picks": "observed first P ({n} stations)",
        "tt_ak135": "ak135, global model ({m})",
        "tt_krso": "KRSO-1D v2, regional model ({m})",
        "tt_src": "2021-12-03 Kii Channel · ISC Bulletin (2026), doi:10.31905/D808B830",
    },
    "ja": {
        "epicentre": "震央（ISC、深さ {d:.0f} km）",
        "land": "観測点（ISC）",
        "iso": "初動の予測等走時線\n（KRSO-1D v2）",
        "iso_unit": "{t} 秒",
        "cbar": "標高・水深（m）",
        "attr": "読み取り：ISC Bulletin (2026), doi:10.31905/D808B830\n"
        "地形：ETOPO1（NOAA NCEI）／海岸線：Natural Earth",
        "tt_title": "走時と震央距離 — 実際の読み取り（ISC）",
        "tt_x": "震央距離（km）",
        "tt_y": "地震発生からの走時（秒）",
        "tt_picks": "観測された P 波初動（{n} 観測点）",
        "tt_ak135": "ak135・全球モデル（{m}）",
        "tt_krso": "KRSO-1D v2・地域モデル（{m}）",
        "tt_src": "2021-12-03 紀伊水道 · ISC Bulletin (2026), doi:10.31905/D808B830",
    },
}


# ── Data ─────────────────────────────────────────────────────────────────────


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "r4-tutorial-maps"


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        dest.write_bytes(resp.read())
    if dest.read_bytes()[:3] != b"CDF":
        dest.unlink()
        raise ValueError(f"{url} did not return NetCDF-3 (b'CDF' magic)")


def fetch_dem(cache_dir: Path) -> Path:
    """ETOPO1 subset from NOAA ERDDAP, cached; 3 tries with 10/20/40 s backoff."""
    dest = cache_dir / DEM_NAME
    if dest.exists() and dest.read_bytes()[:3] == b"CDF":
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for attempt, wait in enumerate((10, 20, 40)):
        url = f"https://{DEM_HOSTS[attempt % 2]}{DEM_QUERY}"
        try:
            _download(url, dest)
            return dest
        except (OSError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
            time.sleep(wait)
    raise RuntimeError("ETOPO1 download failed:\n" + "\n".join(errors))


def load_dem(path: Path):
    """(lon, lat, z) with latitude ascending; z in metres (sea negative)."""
    nc = netcdf_file(path, "r", mmap=False)
    lat = nc.variables["latitude"][:].copy()
    lon = nc.variables["longitude"][:].copy()
    z = nc.variables["altitude"][:].astype(float)
    nc.close()
    if lat[0] > lat[-1]:
        lat, z = lat[::-1], z[::-1]
    assert z.shape == DEM_SHAPE, f"unexpected DEM shape {z.shape}"
    return lon, lat, z


def relief_cmap():
    """Sea + land ramps joined at 0 m, with the matching two-slope norm."""
    sea = LinearSegmentedColormap.from_list("sea", SEA_RAMP)
    land = LinearSegmentedColormap.from_list("land", LAND_RAMP)
    cols = np.vstack([sea(np.linspace(0, 1, 128)), land(np.linspace(0, 1, 128))])
    cmap = LinearSegmentedColormap.from_list("relief", cols)
    return cmap, TwoSlopeNorm(vmin=-6000, vcenter=0, vmax=3000)


def relief_rgb(z, cmap, norm):
    """Hillshaded RGB (light from the north-west, 45 degrees up)."""
    light = LightSource(azdeg=315, altdeg=45)
    return light.shade(z, cmap=cmap, norm=norm, blend_mode="soft", vert_exag=0.05, dx=1850, dy=1850)


# ── Fonts ────────────────────────────────────────────────────────────────────

_NOTO_VF = Path("/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc")
_STATIC_JA_NAMES = ("Noto Sans JP", "Noto Sans CJK JP", "IPAexGothic")
_DROID = (
    Path("/usr/share/fonts/google-droid-sans-fonts/DroidSansJapanese.ttf"),
    Path("/usr/share/fonts/google-droid-sans-fonts/DroidSansFallbackFull.ttf"),
)
_INSTANCE_FAMILY = "Noto Sans CJK JP W400 (r4 map)"


def _static_ja_font() -> Path | None:
    for entry in font_manager.fontManager.ttflist:
        static = "VF" not in Path(entry.fname).name
        if entry.name in _STATIC_JA_NAMES and static and entry.weight in (400, "normal"):
            return Path(entry.fname)
    return None


def _noto_instance(cache_dir: Path) -> Path | None:
    """Weight-400 static instance of the variable Noto CJK JP face (cached)."""
    dest = cache_dir / "fonts" / "NotoSansCJKjp-W400.otf"
    if dest.exists() or not _NOTO_VF.exists():
        return dest if dest.exists() else None
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    print("instancing", _NOTO_VF, "at wght=400 (once, about 90 s)")
    font = instancer.instantiateVariableFont(TTFont(_NOTO_VF, fontNumber=0), {"wght": 400})
    for rec in font["name"].names:
        if rec.nameID in (1, 4, 16):
            rec.string = _INSTANCE_FAMILY
    dest.parent.mkdir(parents=True, exist_ok=True)
    font.save(dest)
    return dest


def ja_font(cache_dir: Path) -> Path:
    """The Japanese font file to use; fails loudly when none is found."""
    env = os.environ.get("R4_MAP_JA_FONT")
    candidates = [Path(env)] if env else []
    candidates += [_static_ja_font(), _noto_instance(cache_dir), *_DROID]
    for path in candidates:
        if path is not None and path.exists():
            return path
    raise SystemExit("no Japanese font found; set R4_MAP_JA_FONT to a static .ttf/.otf")


def use_font(lang: str, cache_dir: Path) -> str:
    """Register and select the figure font; returns its family name."""
    if lang == "ja":
        path = ja_font(cache_dir)
        font_manager.fontManager.addfont(str(path))
        name = font_manager.FontProperties(fname=str(path)).get_name()
    else:
        installed = {f.name for f in font_manager.fontManager.ttflist}
        name = next((n for n in ("Carlito", "Calibri") if n in installed), "DejaVu Sans")
        path = name
    plt.rcParams["font.family"] = [name, "DejaVu Sans"]
    print(f"[{lang}] font: {name} ({path})")
    return name


# ── Drawing ──────────────────────────────────────────────────────────────────

_HALO = [pe.withStroke(linewidth=1.6, foreground=WHITE, alpha=0.85)]


def draw_base(ax, dem, cmap, norm) -> None:
    """Hillshaded relief, coastline, prefecture lines, lakes, 1-degree grid."""
    lon, lat, z = dem
    ax.set_extent(EXTENT, crs=PC)
    ax.imshow(
        relief_rgb(z, cmap, norm),
        origin="lower",
        extent=(lon[0], lon[-1], lat[0], lat[-1]),
        transform=PC,
        interpolation="bilinear",
        zorder=0,
    )
    feat = cfeature.NaturalEarthFeature
    ax.add_feature(feat("physical", "lakes", "10m"), facecolor=LAKE, edgecolor=INK, lw=0.3)
    ax.add_feature(feat("physical", "coastline", "10m"), facecolor="none", edgecolor=INK, lw=0.45)
    ax.add_feature(
        feat("cultural", "admin_1_states_provinces_lines", "10m"),
        facecolor="none",
        edgecolor=MUTED,
        lw=0.3,
        linestyle=(0, (3, 2)),
    )
    gl = ax.gridlines(
        draw_labels=True, lw=0.3, color=WHITE, alpha=0.7, xlocs=range(132, 140), ylocs=range(31, 38)
    )
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 8, "color": MUTED}
    ax.spines["geo"].set_edgecolor(INK)
    ax.spines["geo"].set_linewidth(0.6)


def draw_isochrons(ax, ev: dict, text: dict) -> None:
    """Dashed geodesic rings at the predicted first-arrival times, labelled to the NE."""
    geod = Geodesic()
    for t_s in ksd.REAL_ISOCHRON_TIMES_S:
        radius_m = ksd.isochron_radius_km(t_s, depth=ev["depth_km"]) * 1000.0
        ring = geod.circle(lon=ev["longitude"], lat=ev["latitude"], radius=radius_m, n_samples=180)
        ring = np.vstack([ring, ring[:1]])
        ax.plot(ring[:, 0], ring[:, 1], transform=PC, color=INK, lw=0.5, alpha=0.7)
        ax.lines[-1].set_linestyle((0, (2, 2)))
        lon, lat, _ = geod.direct((ev["longitude"], ev["latitude"]), 45.0, radius_m)[0]
        ax.text(
            lon,
            lat,
            text["iso_unit"].format(t=int(t_s)),
            transform=PC,
            fontsize=8,
            color=INK,
            ha="left",
            va="bottom",
            path_effects=_HALO,
            zorder=4,
        )


def draw_stations(ax, picks: list[dict]) -> None:
    """Stations coloured by their observed first-arrival time (ISC picks)."""
    for pick in picks:
        colour = ksd.REAL_ARRIVAL_BINS[ksd.arrival_bin(pick["t_s"], ksd.REAL_ARRIVAL_BINS)][1]
        ax.scatter(
            pick["lon"],
            pick["lat"],
            marker="^",
            s=46,
            c=colour,
            edgecolors=WHITE,
            linewidths=0.6,
            transform=PC,
            zorder=5,
        )


def draw_epicentre(ax, ev: dict) -> None:
    ax.scatter(
        ev["longitude"],
        ev["latitude"],
        marker="*",
        s=170,
        c=ALERT,
        edgecolors=WHITE,
        linewidths=0.7,
        transform=PC,
        zorder=6,
    )


def draw_scale_bar(ax) -> None:
    """A geodesic 50 km bar: white 2 pt line, INK outline, label above."""
    lon0, lat0 = SCALE_START
    lon1, lat1, _ = Geodesic().direct((lon0, lat0), 90.0, SCALE_KM * 1000.0)[0]
    outline = [pe.Stroke(linewidth=3.2, foreground=INK), pe.Normal()]
    ax.plot([lon0, lon1], [lat0, lat1], transform=PC, color=WHITE, lw=2.0, zorder=7)
    ax.lines[-1].set_path_effects(outline)
    ax.lines[-1].set_solid_capstyle("butt")
    ax.text(
        (lon0 + lon1) / 2,
        lat0 + 0.07,
        f"{SCALE_KM} km",
        transform=PC,
        fontsize=8,
        color=INK,
        ha="center",
        va="bottom",
        path_effects=_HALO,
        zorder=7,
    )


def _legend_handles(lang: str, text: dict, depth: float) -> list:
    idx = 0 if lang == "en" else 1
    dot = {"marker": "^", "linestyle": "none", "markersize": 7, "markeredgecolor": WHITE}
    bins = ksd.REAL_ARRIVAL_BINS
    handles = [Line2D([], [], color=c, label=lab[idx], **dot) for lab, c, _lo, _hi in bins]
    return handles + [
        Line2D([], [], marker="*", linestyle="none", markersize=11, color=ALERT,
               markeredgecolor=WHITE, label=text["epicentre"].format(d=depth)),
    ]  # fmt: skip


def draw_side_panel(fig, lang: str, cmap, norm, depth: float) -> None:
    """Legend, isochron key, elevation colour bar and attribution (right column)."""
    text = TEXT[lang]
    fig.legend(
        handles=_legend_handles(lang, text, depth),
        loc="upper left",
        bbox_to_anchor=(SIDE_X - 0.01, 1.0),
        fontsize=9,
        frameon=False,
        handletextpad=0.35,
        labelspacing=0.32,
        borderaxespad=0.2,
        labelcolor=INK,
    )
    _isochron_key(fig, text)
    _colour_bar(fig, text, cmap, norm)
    fig.text(SIDE_X, 0.03, text["attr"], fontsize=6.5, color=MUTED, va="bottom", linespacing=1.3)


def _isochron_key(fig, text: dict) -> None:
    y = 0.395
    key = Line2D([SIDE_X + 0.012, SIDE_X + 0.062], [y, y], transform=fig.transFigure)
    key.set(color=INK, lw=0.6, linestyle=(0, (2, 2)))
    fig.lines.append(key)
    fig.text(SIDE_X + 0.075, y, text["iso"], fontsize=8.5, color=INK, va="center", linespacing=1.15)


def _colour_bar(fig, text: dict, cmap, norm) -> None:
    cax = fig.add_axes([SIDE_X + 0.015, 0.205, 0.31, 0.032])
    ticks = [-6000, -3000, 0, 1500, 3000]
    cb = fig.colorbar(
        plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal", ticks=ticks
    )
    cb.ax.set_xticklabels([f"{t:,}".replace("-", "−") for t in ticks])
    cb.ax.tick_params(labelsize=8, colors=MUTED, length=2, width=0.4)
    cb.outline.set_linewidth(0.4)
    cax.set_title(text["cbar"], fontsize=8.5, color=INK, pad=3, loc="left")


# ── Driver ───────────────────────────────────────────────────────────────────


def render(lang: str, out: Path, cache_dir: Path) -> Path:
    """Draw one language version; returns the PNG path."""
    use_font(lang, cache_dir)
    cmap, norm = relief_cmap()
    dem = load_dem(fetch_dem(cache_dir))
    ev = ksd.real_event()
    fig = plt.figure(figsize=FIGSIZE, dpi=DPI, facecolor=WHITE)
    ax = fig.add_axes(MAP_RECT, projection=ccrs.Mercator(central_longitude=ev["longitude"]))
    draw_base(ax, dem, cmap, norm)
    draw_isochrons(ax, ev, TEXT[lang])
    draw_stations(ax, ksd.load_real_picks())
    draw_epicentre(ax, ev)
    draw_scale_bar(ax)
    draw_side_panel(fig, lang, cmap, norm, ev["depth_km"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"kii-station-map.{lang}.png"
    fig.savefig(path, dpi=DPI, facecolor=WHITE)
    plt.close(fig)
    print("wrote", path)
    return path


TT_FIGSIZE = (5.7, 3.8)


def _model_curve(ev: dict, layers, xs) -> list[float]:
    from make_synthetic_picks import first_arrival

    return [first_arrival(x, ev["depth_km"], layers)[0] for x in xs]


def _mean_misfit(ev: dict, picks: list[dict], layers) -> float:
    """Mean of observed − predicted first-arrival time (s); positive = model too early."""
    pred = _model_curve(ev, layers, [p["dist_km"] for p in picks])
    return float(np.mean([p["t_s"] - q for p, q in zip(picks, pred, strict=True)]))


def _misfit_words(lang: str, mean_s: float) -> str:
    """'0.8 s too late on average' from mean(observed − predicted)."""
    late = mean_s < 0
    if lang == "en":
        return f"{abs(mean_s):.1f} s too {'late' if late else 'early'} on average"
    return f"平均 {abs(mean_s):.1f} 秒{'遅い' if late else '早い'}"


def render_traveltime(lang: str, out: Path, cache_dir: Path) -> Path:
    """Real picks against the ak135 and KRSO-1D v2 first-arrival curves."""
    use_font(lang, cache_dir)
    text = TEXT[lang]
    ev, picks = ksd.real_event(), ksd.load_real_picks()
    from models import AK135_CRUST, KRSO_1D_V2

    xs = list(range(2, 291, 2))
    fig, ax = plt.subplots(figsize=TT_FIGSIZE, dpi=DPI, facecolor=WHITE)
    for layers, colour, key in (
        (AK135_CRUST, MUTED, "tt_ak135"),
        (KRSO_1D_V2, "#0E7C86", "tt_krso"),
    ):
        label = text[key].format(m=_misfit_words(lang, _mean_misfit(ev, picks, layers)))
        ax.plot(xs, _model_curve(ev, layers, xs), color=colour, lw=1.8, label=label)
    ax.scatter(
        [p["dist_km"] for p in picks],
        [p["t_s"] for p in picks],
        s=22,
        color=INK,
        zorder=3,
        label=text["tt_picks"].format(n=len(picks)),
    )
    ax.set_xlim(0, 290)
    ax.set_ylim(0, 42)
    ax.set_xlabel(text["tt_x"], fontsize=10, color=INK)
    ax.set_ylabel(text["tt_y"], fontsize=10, color=INK)
    ax.set_title(text["tt_title"], fontsize=11, color=INK, loc="left")
    ax.grid(color="#C9D8DC", lw=0.5)
    ax.tick_params(labelsize=9, colors=MUTED)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, labelcolor=INK)
    fig.text(0.99, 0.01, text["tt_src"], fontsize=6.5, color=MUTED, ha="right", va="bottom")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path = out / f"kii-traveltime.{lang}.png"
    fig.savefig(path, dpi=DPI, facecolor=WHITE)
    plt.close(fig)
    print("wrote", path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the Kii story-slide PNGs")
    parser.add_argument("--lang", choices=("en", "ja", "both"), default="both")
    parser.add_argument("--cache-dir", type=Path, default=default_cache_dir())
    parser.add_argument("--out", type=Path, default=HERE / "assets")
    args = parser.parse_args()
    cartopy.config["data_dir"] = str(args.cache_dir / "natural_earth")
    for lang in ("en", "ja") if args.lang == "both" else (args.lang,):
        render(lang, args.out, args.cache_dir)
        render_traveltime(lang, args.out, args.cache_dir)


if __name__ == "__main__":
    main()
