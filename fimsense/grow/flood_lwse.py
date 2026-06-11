"""
flood_lwse.py
-------------
Compute Local Water Surface Elevation (LWSE) from flood object boundaries.

Pipeline
--------
1. extract_boundary_segments – ordered contour pixels, split at every n_points
2. sample_dem_at_boundary    – look up DEM elevation at each boundary pixel
3. compute_segment_lwse      – LWSE (KDE peak + std) per segment
4. filter_lwse_outliers      – IDW-based outlier replacement
5. generate_lwse_layers      – convenience wrapper (main entry point)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

import cv2
import rasterio
from scipy.spatial import cKDTree
from scipy.stats import gaussian_kde
from scipy.signal import argrelextrema
from skimage.measure import label as sk_label
from skimage.morphology import remove_small_objects


# ---------------------------------------------------------------------------
# Step 1 – Extract ordered boundary pixels and split into segments
# ---------------------------------------------------------------------------

def extract_boundary_segments(
    label_raster: np.ndarray,
    n_points: int = 200,
    min_pixels: int = 20,
) -> pd.DataFrame:
    """
    Extract ordered contour pixels for each labeled object and split into
    sequential segments of ``n_points`` pixels.

    Parameters
    ----------
    label_raster : ndarray
        Integer-labeled raster (background = 0).
    n_points : int
        Pixels per segment (default 200).
    min_pixels : int
        Skip objects whose contour has fewer than this many pixels.

    Returns
    -------
    DataFrame with columns:
        object_id, contour_id, seq, col, row, segment_id
    """
    records = []
    object_ids = np.unique(label_raster)
    object_ids = object_ids[object_ids > 0]

    for obj_id in object_ids:
        ys, xs = np.where(label_raster == obj_id)
        if len(xs) < min_pixels:
            continue

        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())

        obj_mask = (
            (label_raster[y0 : y1 + 1, x0 : x1 + 1] == obj_id).astype(np.uint8) * 255
        )
        obj_mask = np.pad(obj_mask, 1, mode="constant", constant_values=0)

        contours, hierarchy = cv2.findContours(
            obj_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
        )
        if hierarchy is None:
            continue

        for c_id, cnt in enumerate(contours):
            pts = cnt[:, 0, :]
            cols = pts[:, 0] + x0 - 1
            rows = pts[:, 1] + y0 - 1

            if len(cols) < min_pixels:
                continue

            for seq, (col, row) in enumerate(zip(cols, rows)):
                records.append(
                    {
                        "object_id": int(obj_id),
                        "contour_id": int(c_id),
                        "seq": int(seq),
                        "col": int(col),
                        "row": int(row),
                    }
                )

    if not records:
        return pd.DataFrame(
            columns=["object_id", "contour_id", "seq", "col", "row", "segment_id"]
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
    Add geographic coordinates and DEM-sampled elevation to each boundary pixel.

    Parameters
    ----------
    segments_df : DataFrame
        Output of :func:`extract_boundary_segments`.
    dem_path : str
        Path to DEM raster (must share CRS with the label raster).
    obj_transform : Affine
        Affine transform of the label raster (col/row → x/y).

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
        nodata = dem_src.nodata

        def _sample(x: float, y: float) -> float:
            px, py = inv_t * (x, y)
            px, py = int(px), int(py)
            if 0 <= px < w and 0 <= py < h:
                v = dem_data[py, px]
                return float(np.nan if (nodata is not None and v == nodata) else v)
            return float(np.nan)

        df["Elev"] = [_sample(x, y) for x, y in zip(df["x"], df["y"])]

    return df


# ---------------------------------------------------------------------------
# Step 3 – Compute LWSE per segment using KDE
# ---------------------------------------------------------------------------

def _compute_lwse(values: np.ndarray) -> float:
    valid = values[~np.isnan(values)]
    if len(valid) < 5:
        return float(np.nanmean(valid)) if len(valid) > 0 else float("nan")

    try:
        kde = gaussian_kde(valid)
        x_vals = np.linspace(valid.min(), valid.max(), 300)
        kde_vals = kde(x_vals)

        peaks = list(argrelextrema(kde_vals, np.greater)[0])
        peaks = [i for i in peaks if kde_vals[i] > 0.05]

        if not peaks:
            peak_val = x_vals[int(np.argmax(kde_vals))]
            return float(peak_val + np.std(valid))

        peak_val = x_vals[peaks[-1]]

        valleys = argrelextrema(kde_vals, np.less)[0]
        left_valleys = valleys[valleys < peaks[-1]]

        if len(left_valleys) == 0:
            return float(peak_val + np.std(valid))

        valley_val = x_vals[left_valleys[-1]]
        subset = valid[valid > valley_val]
        return float(peak_val + (np.std(subset) if len(subset) > 0 else 0.0))

    except Exception:
        return float(np.nanmean(valid))


def compute_segment_lwse(segments_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate boundary pixels into one centroid row per
    (object_id, contour_id, segment_id), computing LWSE for each.

    Returns
    -------
    DataFrame with columns:
        object_id, contour_id, segment_id, x, y, col, row, LWSE
    """
    records = []
    for (obj_id, c_id, seg_id), grp in segments_df.groupby(
        ["object_id", "contour_id", "segment_id"]
    ):
        elevs = grp["Elev"].values
        lwse = _compute_lwse(elevs)
        if np.isnan(lwse):
            continue
        records.append(
            {
                "object_id": int(obj_id),
                "contour_id": int(c_id),
                "segment_id": int(seg_id),
                "x": float(grp["x"].mean()),
                "y": float(grp["y"].mean()),
                "col": float(grp["col"].mean()),
                "row": float(grp["row"].mean()),
                "LWSE": lwse,
            }
        )
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Step 4 – Filter LWSE outliers via IDW of k nearest neighbours
# ---------------------------------------------------------------------------

def filter_lwse_outliers(cent_df: pd.DataFrame, k_neighbors: int) -> pd.DataFrame:
    """
    Replace LWSE values that are statistical outliers with their IDW estimate
    from the k nearest segment centroids.

    Outlier criterion:
        LWSE < neighbour_mean − 2σ   OR   LWSE > neighbour_mean + 3σ

    Adds column ``LWSE_new``.
    """
    df = cent_df.copy().reset_index(drop=True)
    coords = np.column_stack([df["x"].values, df["y"].values])

    if coords.shape[0] == 0:
        df["LWSE_new"] = np.nan
        return df

    k = min(k_neighbors, len(df) - 1)
    if k < 1:
        df["LWSE_new"] = df["LWSE"].values
        return df

    tree = cKDTree(coords)
    elevs = df["LWSE"].values
    filtered = []

    for i in range(len(df)):
        distances, indices = tree.query(coords[i], k=k + 1)
        indices, distances = indices[1:], distances[1:]
        distances = np.where(distances == 0, 1e-10, distances)

        neighbors = elevs[indices]
        weights = 1.0 / distances
        idw = np.sum(weights * neighbors) / np.sum(weights)
        mean_val = np.mean(neighbors)
        std_val = np.std(neighbors)

        if elevs[i] < mean_val - 2 * std_val or elevs[i] > mean_val + 3 * std_val:
            filtered.append(idw)
        else:
            filtered.append(elevs[i])

    df["LWSE_new"] = filtered
    return df


# ---------------------------------------------------------------------------
# Main pipeline – convenience wrapper
# ---------------------------------------------------------------------------

def generate_lwse_layers(
    input_image: str,
    dem_path: str,
    n_points: int,
    min_pixels: int,
    k_neighbors: int,
    output_boundary_path: str,
    output_centroid_path: str,
) -> None:
    """
    End-to-end pipeline: binary flood raster + DEM →
        boundary shapefile (pixels + elevation) and
        centroid shapefile (segment centroids + LWSE / LWSE_new).

    Parameters
    ----------
    input_image : str
        Binary flood raster (flood = 1, background = 0).
        Connected-component labeling is applied internally.
    dem_path : str
        DEM raster in the same CRS as the label raster.
    n_points : int
        Boundary pixels per segment (e.g. 200).
    min_pixels : int
        Skip objects whose contour has fewer than this many pixels.
    k_neighbors : int
        Neighbours for IDW outlier filtering.
    output_boundary_path : str
        Output shapefile — boundary pixels with elevation.
    output_centroid_path : str
        Output shapefile — segment centroids with LWSE and LWSE_new.
    """
    with rasterio.open(input_image) as src:
        binary_arr = src.read(1)
        transform = src.transform
        crs = src.crs

    flood_mask = binary_arr > 0

    if min_pixels > 0:
        flood_mask = remove_small_objects(flood_mask, min_size=min_pixels, connectivity=2)

    label_raster = sk_label(flood_mask, connectivity=2).astype(np.int32)
    n_objects = int(label_raster.max())
    print(f"  Binary → {n_objects} labeled objects (min_pixels={min_pixels})")

    print("  Extracting boundary segments...")
    segs_df = extract_boundary_segments(
        label_raster, n_points=n_points, min_pixels=min_pixels
    )
    n_segs = segs_df.groupby(["object_id", "contour_id", "segment_id"]).ngroups
    print(f"  {len(segs_df):,} boundary pixels → {n_segs} segments")

    print("  Sampling DEM at boundary pixels...")
    segs_df = sample_dem_at_boundary(segs_df, dem_path, transform)

    bnd_geometry = [Point(x, y) for x, y in zip(segs_df["x"], segs_df["y"])]
    bnd_gdf = gpd.GeoDataFrame(segs_df, geometry=bnd_geometry, crs=crs)
    bnd_gdf.to_file(output_boundary_path)

    print("  Computing LWSE per segment...")
    cent_df = compute_segment_lwse(segs_df)
    print(f"  {len(cent_df)} segments with valid LWSE")

    print("  Filtering LWSE outliers (IDW)...")
    cent_df = filter_lwse_outliers(cent_df, k_neighbors)

    cent_geometry = [Point(x, y) for x, y in zip(cent_df["x"], cent_df["y"])]
    cent_gdf = gpd.GeoDataFrame(cent_df, geometry=cent_geometry, crs=crs)
    cent_gdf.to_file(output_centroid_path)

    print("  Boundary and Centroid layers with LWSE created.")
