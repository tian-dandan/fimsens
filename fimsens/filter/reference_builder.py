"""
reference_builder.py
--------------------
Build a spatially-varying LWSE (Local Water Surface Elevation) reference layer
from the boundaries of large, confirmed flood objects.

The reference layer is later used by ObjectFilter.rule3 to flag objects whose
LWSE is inconsistent with the surrounding true-flood surface.

Pipeline
--------
1. extract_boundary_segments  – ordered boundary pixels, split into ~200-px segments
2. sample_dem_at_boundary     – look up DEM elevation at each boundary pixel
3. compute_segment_attributes – LWSE (KDE peak) + elevation stats per segment
4. compute_reference_delta_lwse – delta_LWSE = LWSE − local_mean(k-NN LWSE)
5. build_reference_layer      – convenience wrapper that runs the full pipeline
                                and saves CSV + SHP

Standard dependencies: cv2, rasterio, scipy, geopandas, shapely
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import cv2
import numpy as np
import pandas as pd
import rasterio
import geopandas as gpd
from scipy.stats import gaussian_kde
from scipy.spatial.distance import cdist
from shapely.geometry import Point


# ---------------------------------------------------------------------------
# Step 1 – Extract ordered boundary pixels and split into segments
# ---------------------------------------------------------------------------

def extract_boundary_segments(
    label_raster: np.ndarray,
    n_points: int = 200,
    min_pixels: int = 20,
    include_holes: bool = False,
) -> pd.DataFrame:
    """
    Extract ordered boundary (contour) pixels for each object, then split
    into sequential segments of ``n_points`` pixels each.

    Parameters
    ----------
    label_raster : ndarray
        Integer-labeled raster.  Background = 0.
    n_points : int
        Pixels per segment (default 200).
    min_pixels : int
        Skip objects whose contour has fewer than this many pixels.
    include_holes : bool
        If True, also extract interior (hole) contours.

    Returns
    -------
    DataFrame with columns:
        object_id, contour_id, is_hole, seq, col, row, segment_id
    """
    mode = cv2.RETR_CCOMP if include_holes else cv2.RETR_EXTERNAL
    records = []

    object_ids = np.unique(label_raster)
    object_ids = object_ids[object_ids > 0]

    for obj_id in object_ids:
        ys, xs = np.where(label_raster == obj_id)
        if len(xs) < min_pixels:
            continue

        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())

        obj_mask = (label_raster[y0 : y1 + 1, x0 : x1 + 1] == obj_id).astype(np.uint8) * 255
        obj_mask = np.pad(obj_mask, 1, mode="constant", constant_values=0)

        contours, hierarchy = cv2.findContours(obj_mask, mode, cv2.CHAIN_APPROX_NONE)
        if hierarchy is None:
            continue
        hierarchy = hierarchy[0]

        for c_id, cnt in enumerate(contours):
            pts = cnt[:, 0, :]
            cols = pts[:, 0] + x0 - 1
            rows = pts[:, 1] + y0 - 1

            is_hole = bool(include_holes and hierarchy[c_id][3] != -1)

            for seq, (col, row) in enumerate(zip(cols, rows)):
                records.append(
                    {
                        "object_id": int(obj_id),
                        "contour_id": int(c_id),
                        "is_hole": int(is_hole),
                        "seq": int(seq),
                        "col": int(col),
                        "row": int(row),
                    }
                )

    if not records:
        return pd.DataFrame(
            columns=["object_id", "contour_id", "is_hole", "seq", "col", "row", "segment_id"]
        )

    df = (
        pd.DataFrame(records)
        .sort_values(["object_id", "contour_id", "seq"])
        .reset_index(drop=True)
    )
    df["segment_id"] = df.groupby(["object_id", "contour_id"]).cumcount() // n_points
    return df


# ---------------------------------------------------------------------------
# Step 2 – Sample DEM elevation at boundary pixels
# ---------------------------------------------------------------------------

def sample_dem_at_boundary(
    segments_df: pd.DataFrame,
    dem_path: str,
    obj_transform: rasterio.transform.Affine,
) -> pd.DataFrame:
    """
    Add geo-coordinates and DEM-sampled elevation to each boundary pixel.

    Parameters
    ----------
    segments_df : DataFrame
        Output of :func:`extract_boundary_segments`.
    dem_path : str
        Path to DEM raster (must be in the same CRS as the object raster).
    obj_transform : Affine
        Affine transform of the object raster (used to convert col/row → x/y).

    Returns
    -------
    DataFrame with added columns: x, y, Elev
    """
    df = segments_df.copy()

    t = obj_transform
    df["x"] = t.c + df["col"] * t.a + df["row"] * t.b
    df["y"] = t.f + df["col"] * t.d + df["row"] * t.e

    with rasterio.open(dem_path) as dem_src:
        dem_data = dem_src.read(1).astype(float)
        inv_t = ~dem_src.transform
        h, w = dem_data.shape

        def _sample(x: float, y: float) -> float:
            px, py = inv_t * (x, y)
            px, py = int(px), int(py)
            if 0 <= px < w and 0 <= py < h:
                v = dem_data[py, px]
                nodata = dem_src.nodata
                return float(np.nan if (nodata is not None and v == nodata) else v)
            return float(np.nan)

        df["Elev"] = [_sample(x, y) for x, y in zip(df["x"], df["y"])]

    return df


# ---------------------------------------------------------------------------
# Step 3 – Compute per-segment LWSE and elevation statistics
# ---------------------------------------------------------------------------

def _kde_peak(values: np.ndarray) -> float:
    """Return the KDE density peak of an array (= estimated water surface elevation)."""
    valid = values[~np.isnan(values)]
    if len(valid) < 5:
        return float(np.nanmean(values))
    try:
        kde = gaussian_kde(valid)
        xs = np.linspace(valid.min(), valid.max(), 500)
        return float(xs[np.argmax(kde(xs))])
    except Exception:
        return float(np.nanmean(valid))


def compute_segment_attributes(
    segments_df: pd.DataFrame,
    lwse_method: str = "kde",
) -> pd.DataFrame:
    """
    Aggregate boundary pixels into one row per (object_id, segment_id).

    Parameters
    ----------
    segments_df : DataFrame
        Output of :func:`sample_dem_at_boundary` (needs Elev, col, row, x, y).
    lwse_method : str
        ``'kde'`` (default) – KDE density peak of boundary elevations.
        ``'mean'``           – simple mean.

    Returns
    -------
    DataFrame with one row per segment.
    Columns: object_id, segment_id, num_points, col, row, x, y,
             elev_mean, elev_std, elev_min, elev_max, elev_range, LWSE
    """
    records = []
    for (obj_id, seg_id), grp in segments_df.groupby(["object_id", "segment_id"]):
        elevs = grp["Elev"].values

        lwse = _kde_peak(elevs) if lwse_method == "kde" else float(np.nanmean(elevs))

        records.append(
            {
                "object_id": int(obj_id),
                "segment_id": int(seg_id),
                "num_points": len(grp),
                "col": float(grp["col"].mean()),
                "row": float(grp["row"].mean()),
                "x": float(grp["x"].mean()),
                "y": float(grp["y"].mean()),
                "elev_mean": float(np.nanmean(elevs)),
                "elev_std": float(np.nanstd(elevs)),
                "elev_min": float(np.nanmin(elevs)),
                "elev_max": float(np.nanmax(elevs)),
                "elev_range": float(np.nanmax(elevs) - np.nanmin(elevs)),
                "LWSE": lwse,
            }
        )

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Step 4 – Compute delta_LWSE for each reference segment
# ---------------------------------------------------------------------------

def compute_reference_delta_lwse(
    cent_df: pd.DataFrame,
    k_neighbors: int = 5,
) -> pd.DataFrame:
    """
    For each reference segment centroid compute:
        LWSE_local = mean LWSE of k nearest neighbours
        delta_LWSE = LWSE − LWSE_local

    Parameters
    ----------
    cent_df : DataFrame
        Output of :func:`compute_segment_attributes`.
    k_neighbors : int
        Number of nearest neighbours (default 5).

    Returns
    -------
    cent_df with added columns: LWSE_local, delta_LWSE
    """
    df = cent_df.copy().reset_index(drop=True)
    coords = df[["col", "row"]].values
    lwse_vals = df["LWSE"].values

    dist_matrix = cdist(coords, coords, metric="euclidean")
    np.fill_diagonal(dist_matrix, np.inf)

    k = min(k_neighbors, len(df) - 1)
    nearest_idx = np.argsort(dist_matrix, axis=1)[:, :k]

    df["LWSE_local"] = np.array([lwse_vals[idx].mean() for idx in nearest_idx])
    df["delta_LWSE"] = df["LWSE"] - df["LWSE_local"]
    return df


# ---------------------------------------------------------------------------
# Step 5 – Full pipeline convenience wrapper
# ---------------------------------------------------------------------------

def build_reference_layer(
    large_obj_path: str,
    dem_path: str,
    output_dir: str,
    n_points: int = 200,
    min_pixels: int = 20,
    k_neighbors: int = 5,
    lwse_method: str = "kde",
) -> pd.DataFrame:
    """
    End-to-end pipeline: large-object raster + DEM → reference CSV + SHP.

    Parameters
    ----------
    large_obj_path : str
        Labeled raster containing only the large (confirmed) flood objects.
    dem_path : str
        DEM raster in the same CRS as the object raster.
    output_dir : str
        Directory for output files.
    n_points : int
        Boundary pixels per segment.
    min_pixels : int
        Minimum contour length to process.
    k_neighbors : int
        Neighbours for local LWSE estimation.
    lwse_method : str
        ``'kde'`` or ``'mean'``.

    Returns
    -------
    cent_df : DataFrame
        Reference segments with LWSE, delta_LWSE, centroids.
    """
    os.makedirs(output_dir, exist_ok=True)

    with rasterio.open(large_obj_path) as src:
        large_obj = src.read(1)
        transform = src.transform
        crs = src.crs

    print(f"[build_reference_layer] Large objects: {np.unique(large_obj[large_obj > 0])}")

    print("  Extracting boundary segments...")
    segs_df = extract_boundary_segments(large_obj, n_points=n_points, min_pixels=min_pixels)
    n_segs = segs_df.groupby(["object_id", "segment_id"]).ngroups
    print(f"  {len(segs_df):,} boundary pixels → {n_segs} segments")

    print("  Sampling DEM at boundary pixels...")
    segs_df = sample_dem_at_boundary(segs_df, dem_path, transform)

    print("  Computing segment attributes (LWSE)...")
    cent_df = compute_segment_attributes(segs_df, lwse_method=lwse_method)

    cent_df = compute_reference_delta_lwse(cent_df, k_neighbors=k_neighbors)

    ref_mean = cent_df["delta_LWSE"].mean()
    ref_std  = cent_df["delta_LWSE"].std()
    print(f"  Reference delta_LWSE: mean={ref_mean:.4f} m, std={ref_std:.4f} m")
    print(f"  Suggested threshold (mean + 2·std): {ref_mean + 2*ref_std:.4f} m")

    csv_path = os.path.join(output_dir, "reference_centroids_delta_LWSE.csv")
    shp_path = os.path.join(output_dir, "reference_centroids_delta_LWSE.shp")

    cent_df.to_csv(csv_path, index=False)

    geometry = [Point(x, y) for x, y in zip(cent_df["x"], cent_df["y"])]
    gdf = gpd.GeoDataFrame(cent_df, geometry=geometry, crs=crs)
    gdf.to_file(shp_path)

    print(f"  Saved: {csv_path}")
    print(f"  Saved: {shp_path}")
    return cent_df
