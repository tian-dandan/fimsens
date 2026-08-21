"""
utils.py
--------
Shared I/O helpers and visualisation utilities for the fimsens.filter package.

Functions
---------
read_raster      – Read a single-band raster; return (array, meta).
save_raster      – Write a single-band array as a GeoTIFF (creates dirs).
align_rasters    – Reproject source raster onto target raster grid.
plot_predict_map – Visualise the predict classification map overlaid on the
                   labeled object raster; save to PNG.
compute_object_attributes – Compute per-object morphometric attributes from
                   a labeled raster and optional DEM (area, centroid, LWSE…).
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine
from scipy.stats import gaussian_kde
from skimage.measure import regionprops_table


# ---------------------------------------------------------------------------
# Raster I/O
# ---------------------------------------------------------------------------

def read_raster(path: str, band: int = 1) -> Tuple[np.ndarray, dict]:
    """Read a single band from a raster file."""
    with rasterio.open(path) as src:
        arr = src.read(band)
        meta = src.meta.copy()
    return arr, meta


def save_raster(
    array: np.ndarray,
    output_path: str,
    meta: dict,
    nodata: Optional[float] = None,
    compress: str = "lzw",
) -> None:
    """Write a single-band array to a GeoTIFF. Parent directories are created automatically."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_meta = meta.copy()
    out_meta.update({"count": 1, "dtype": str(array.dtype), "compress": compress})
    if nodata is not None:
        out_meta["nodata"] = nodata

    with rasterio.open(output_path, "w", **out_meta) as dst:
        dst.write(array, 1)
    print(f"[save_raster] Written → {output_path}")


# ---------------------------------------------------------------------------
# Raster alignment
# ---------------------------------------------------------------------------

def align_rasters(
    source_path: str,
    target_path: str,
    output_path: str,
    resampling: str = "bilinear",
) -> str:
    """
    Reproject and resample *source_path* onto the exact grid of *target_path*.

    If CRS, pixel size, and origin already match, no file is written and
    ``source_path`` is returned unchanged.

    Parameters
    ----------
    source_path : str
        Raster to reproject (e.g. DEM).
    target_path : str
        Reference raster whose grid defines the output (e.g. FIM objects raster).
    output_path : str
        Destination path for the aligned raster.
    resampling : str
        ``'bilinear'`` (default), ``'nearest'``, ``'cubic'``, ``'lanczos'``, ``'average'``.

    Returns
    -------
    str
        ``output_path`` when reprojection was performed; ``source_path`` when already aligned.
    """
    from rasterio.warp import reproject, Resampling

    resamp_map = {
        "bilinear": Resampling.bilinear,
        "nearest":  Resampling.nearest,
        "cubic":    Resampling.cubic,
        "lanczos":  Resampling.lanczos,
        "average":  Resampling.average,
    }
    resamp = resamp_map.get(resampling, Resampling.bilinear)

    with rasterio.open(target_path) as tgt:
        dst_crs       = tgt.crs
        dst_transform = tgt.transform
        dst_width     = tgt.width
        dst_height    = tgt.height

    with rasterio.open(source_path) as src:
        already_aligned = (
            src.crs == dst_crs
            and src.width  == dst_width
            and src.height == dst_height
            and abs(src.transform.a - dst_transform.a) < 1e-6
            and abs(src.transform.e - dst_transform.e) < 1e-6
            and abs(src.transform.c - dst_transform.c) < 1e-3
            and abs(src.transform.f - dst_transform.f) < 1e-3
        )
        if already_aligned:
            print("[align_rasters] Source already matches target grid — skipping.")
            return source_path

        src_crs       = src.crs
        src_transform = src.transform
        src_nodata    = src.nodata
        src_count     = src.count

        out_meta = src.meta.copy()
        out_meta.update({
            "crs":       dst_crs,
            "transform": dst_transform,
            "width":     dst_width,
            "height":    dst_height,
            "compress":  "lzw",
        })
        if out_meta.get("nodata") is None:
            out_meta["nodata"] = -9999.0

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        with rasterio.open(output_path, "w", **out_meta) as dst:
            for band_idx in range(1, src_count + 1):
                reproject(
                    source=rasterio.band(src, band_idx),
                    destination=rasterio.band(dst, band_idx),
                    src_transform=src_transform,
                    src_crs=src_crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=resamp,
                    src_nodata=src_nodata,
                    dst_nodata=out_meta["nodata"],
                )

    print(
        f"[align_rasters] {os.path.basename(source_path)} → {output_path}\n"
        f"  Source : CRS={src_crs}, pixel={src_transform.a:.4g} m\n"
        f"  Target : CRS={dst_crs}, pixel={dst_transform.a:.4g} m, "
        f"size=({dst_height}×{dst_width})"
    )
    return output_path


# ---------------------------------------------------------------------------
# Object attribute computation
# ---------------------------------------------------------------------------

def _kde_peak(values: np.ndarray) -> float:
    valid = values[~np.isnan(values)]
    if len(valid) < 5:
        return float(np.nanmean(valid)) if len(valid) > 0 else np.nan
    try:
        kde = gaussian_kde(valid)
        xs = np.linspace(valid.min(), valid.max(), 500)
        return float(xs[np.argmax(kde(xs))])
    except Exception:
        return float(np.nanmean(valid))


def compute_object_attributes(
    label_arr: np.ndarray,
    meta: dict,
    dem_path: Optional[str] = None,
    lwse_method: str = "kde",
    pixel_area_km2: Optional[float] = None,
) -> pd.DataFrame:
    """
    Compute per-object morphometric attributes.

    Parameters
    ----------
    label_arr : ndarray
        Integer-labeled raster (background = 0).
    meta : dict
        Rasterio metadata of the label raster.
    dem_path : str, optional
        DEM raster. Required for elevation-based attributes (LWSE, etc.).
    lwse_method : str
        ``'kde'`` or ``'mean'`` for LWSE estimation.
    pixel_area_km2 : float, optional
        Area of one pixel in km². If None, computed from the raster transform.

    Returns
    -------
    attrs : DataFrame
        One row per object. Columns always present:
            ID, area_px, area_km2, cent_row, cent_col, x, y
        Columns added when ``dem_path`` is provided:
            elev_mean, elev_std, elev_min, elev_max, LWSE
    """
    transform: Affine = meta["transform"]

    if pixel_area_km2 is None:
        px_width_m  = abs(transform.a)
        px_height_m = abs(transform.e)
        pixel_area_km2 = (px_width_m * px_height_m) / 1e6

    props = regionprops_table(
        label_arr,
        properties=["label", "area", "centroid"],
    )
    df = pd.DataFrame(props).rename(
        columns={
            "label": "ID",
            "area": "area_px",
            "centroid-0": "cent_row",
            "centroid-1": "cent_col",
        }
    )
    df["area_km2"] = df["area_px"] * pixel_area_km2

    df["x"] = transform.c + df["cent_col"] * transform.a + df["cent_row"] * transform.b
    df["y"] = transform.f + df["cent_col"] * transform.d + df["cent_row"] * transform.e

    if dem_path is not None:
        dem_arr, _ = read_raster(dem_path)
        with rasterio.open(dem_path) as dem_src:
            dem_nodata = dem_src.nodata
            inv_t = ~dem_src.transform

        elev_mean_list, elev_std_list = [], []
        elev_min_list,  elev_max_list = [], []
        lwse_list = []

        for obj_id in df["ID"].values:
            rows, cols = np.where(label_arr == obj_id)
            xs = transform.c + cols * transform.a + rows * transform.b
            ys = transform.f + cols * transform.d + rows * transform.e

            dem_h, dem_w = dem_arr.shape
            elevs = []
            for xi, yi in zip(xs, ys):
                px, py = inv_t * (xi, yi)
                px, py = int(px), int(py)
                if 0 <= px < dem_w and 0 <= py < dem_h:
                    v = float(dem_arr[py, px])
                    if dem_nodata is None or v != dem_nodata:
                        elevs.append(v)

            elevs = np.array(elevs, dtype=float)
            if len(elevs) == 0:
                elevs = np.array([np.nan])

            elev_mean_list.append(float(np.nanmean(elevs)))
            elev_std_list.append(float(np.nanstd(elevs)))
            elev_min_list.append(float(np.nanmin(elevs)))
            elev_max_list.append(float(np.nanmax(elevs)))
            lwse_list.append(
                _kde_peak(elevs) if lwse_method == "kde" else float(np.nanmean(elevs))
            )

        df["elev_mean"] = elev_mean_list
        df["elev_std"]  = elev_std_list
        df["elev_min"]  = elev_min_list
        df["elev_max"]  = elev_max_list
        df["LWSE"]      = lwse_list

    print(f"[compute_object_attributes] {len(df)} objects processed.")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Visualisation
# ---------------------------------------------------------------------------

def plot_predict_map(
    label_arr: np.ndarray,
    predict_df: pd.DataFrame,
    output_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 10),
    background_img: Optional[np.ndarray] = None,
    title: str = "Commission Error Filter Results",
) -> None:
    """
    Visualise the predict classification map overlaid on the labeled object raster.

    Parameters
    ----------
    label_arr : ndarray
        Integer-labeled raster (background = 0).
    predict_df : DataFrame
        Output of ``ObjectFilter.get_results()`` — must have ``ID`` and ``predict``.
    output_path : str, optional
        Path to save the PNG. If None, ``plt.show()`` is called.
    figsize : (int, int)
        Figure size in inches.
    background_img : ndarray, optional
        Grayscale or RGB background image (same spatial extent).
    title : str
        Plot title.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.colors import ListedColormap, BoundaryNorm
    except ImportError:
        print("[plot_predict_map] matplotlib not installed — plotting skipped.")
        return

    color_map = {
        0:   ("#d73027", "FP: too small (0)"),
        1:   ("#3169b1", "True flood — large (1)"),
        2:   ("#3aa468", "Depression water (2)"),
        4:   ("#f46d43", "FP: LWSE too high (4)"),
        5:   ("#abd9e9", "Tentative flood: LWSE low (5)"),
        6:   ("#fdae61", "FP: too far (6)"),
        7:   ("#313695", "True flood: near confirmed (7)"),
        -1:  ("#888888", "Unclassified"),
    }

    predict_raster = np.full(label_arr.shape, -1, dtype=float)
    id_to_predict = dict(zip(predict_df["ID"].values,
                             predict_df["predict"].fillna(-1).values))
    for obj_id, pred_val in id_to_predict.items():
        predict_raster[label_arr == obj_id] = float(pred_val) if not np.isnan(pred_val) else -1.0

    vals   = sorted(color_map.keys())
    colors = [color_map[v][0] for v in vals]
    cmap   = ListedColormap(colors)
    bounds = [v - 0.5 for v in vals] + [vals[-1] + 0.5]
    norm   = BoundaryNorm(bounds, cmap.N)

    fig, ax = plt.subplots(figsize=figsize)

    if background_img is not None:
        ax.imshow(background_img, cmap="gray", alpha=0.4)

    masked = np.ma.masked_where(label_arr == 0, predict_raster)
    ax.imshow(masked, cmap=cmap, norm=norm, interpolation="none")

    patches = [
        mpatches.Patch(color=color_map[v][0], label=color_map[v][1])
        for v in vals
    ]
    ax.legend(handles=patches, loc="lower right", fontsize=8)
    ax.set_title(title, fontsize=14)
    ax.axis("off")

    plt.tight_layout()
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"[plot_predict_map] Saved → {output_path}")
    else:
        plt.show()
    plt.close(fig)
