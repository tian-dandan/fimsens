"""
depression.py
-------------
Surface-depression analysis for flood objects.

Pipeline
--------
1. fill_depressions_spill  – spill-elevation DEM fill (Liu & Wang 2006)
                              → produces 'fill_depth' (depression depth raster)
2. compute_depression_metrics – per-object depression statistics from fill_depth
                              → depre_mn, depre_max, depre_sd, depre_pct, depre_vol
3. detect_pit_presence     – label pit bottoms in large depressions;
                              check overlap with flood objects → has_pit
4. compute_depth_metrics   – per-object flood depth statistics from LWSE
                              → depth_mn, depth_md, depth_min, depth_max,
                                 depth_sd, frac_neg, depth_vol

Standard dependencies: numpy, scipy, rasterio
"""

from __future__ import annotations

import heapq
import os
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import rasterio
from scipy.ndimage import gaussian_filter, label as nd_label


# ---------------------------------------------------------------------------
# D8 neighbour offsets (E SE S SW W NW N NE)
# ---------------------------------------------------------------------------
_OFFS = np.array(
    [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)],
    dtype=int,
)


# ---------------------------------------------------------------------------
# Step 1 – Spill-elevation depression fill
# ---------------------------------------------------------------------------

def fill_depressions_spill(
    elev: np.ndarray,
    nodata_mask: Optional[np.ndarray] = None,
    gaussian_sigma: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fill topographic depressions using the Liu & Wang (2006) spill-elevation
    algorithm (O(N log N) priority-queue).

    Parameters
    ----------
    elev : 2-D ndarray
        Input DEM elevations (float32 / float64).
    nodata_mask : 2-D bool ndarray, optional
        True where cells are invalid.  Boundary cells are used as seeds.
    gaussian_sigma : float
        Sigma for Gaussian pre-smoothing (default 1.5; set to 0 to skip).

    Returns
    -------
    spill : ndarray
        Depressionless (spill) surface.
    fill_depth : ndarray
        Depression depth = max(spill − elev, 0).
    """
    elev = np.asarray(elev, dtype=float)
    H, W = elev.shape

    if nodata_mask is None:
        nodata_mask = np.zeros((H, W), dtype=bool)
    else:
        nodata_mask = np.asarray(nodata_mask, dtype=bool)

    if gaussian_sigma > 0:
        elev = gaussian_filter(elev, sigma=gaussian_sigma)

    spill   = np.full((H, W), np.inf, dtype=elev.dtype)
    closed  = np.zeros((H, W), dtype=bool)
    in_heap = np.zeros((H, W), dtype=bool)

    heap = []

    def _push(sv: float, r: int, c: int) -> None:
        spill[r, c] = sv
        heapq.heappush(heap, (sv, r, c))
        in_heap[r, c] = True

    # Seed boundary cells
    for r in range(H):
        for c in (0, W - 1):
            if not nodata_mask[r, c]:
                _push(elev[r, c], r, c)
    for c in range(W):
        for r in (0, H - 1):
            if not nodata_mask[r, c]:
                _push(elev[r, c], r, c)

    # Best-first expansion
    while heap:
        s_cur, r, c = heapq.heappop(heap)
        if closed[r, c]:
            continue
        closed[r, c] = True

        for dr, dc in _OFFS:
            rr, cc = r + dr, c + dc
            if rr < 0 or rr >= H or cc < 0 or cc >= W:
                continue
            if nodata_mask[rr, cc] or closed[rr, cc]:
                continue
            cand = max(elev[rr, cc], spill[r, c])
            if cand < spill[rr, cc]:
                _push(cand, rr, cc)
            elif not in_heap[rr, cc]:
                _push(cand, rr, cc)

    unreachable = ~np.isfinite(spill) & ~nodata_mask
    spill[unreachable] = elev[unreachable]

    fill_depth = np.maximum(spill - elev, 0.0).astype(elev.dtype)
    return spill, fill_depth


def fill_depressions_from_raster(
    dem_path: str,
    output_dir: Optional[str] = None,
    gaussian_sigma: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """
    Convenience wrapper: read DEM raster, fill depressions, optionally save outputs.

    Returns
    -------
    spill      : ndarray
    fill_depth : ndarray
    meta       : rasterio metadata of the DEM
    """
    with rasterio.open(dem_path) as src:
        dem_arr  = src.read(1).astype(float)
        meta     = src.meta.copy()
        nodata   = src.nodata
        nodata_mask = np.zeros_like(dem_arr, dtype=bool)
        if nodata is not None:
            nodata_mask = (dem_arr == nodata)

    spill, fill_depth = fill_depressions_spill(
        dem_arr, nodata_mask=nodata_mask, gaussian_sigma=gaussian_sigma
    )

    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)
        _save_float_raster(spill,      meta, os.path.join(output_dir, "dem_filled.tif"))
        _save_float_raster(fill_depth, meta, os.path.join(output_dir, "depression_depth.tif"))

    return spill, fill_depth, meta


def _save_float_raster(arr: np.ndarray, meta: dict, path: str) -> None:
    out_meta = meta.copy()
    out_meta.update({"dtype": "float32", "count": 1, "compress": "lzw"})
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with rasterio.open(path, "w", **out_meta) as dst:
        dst.write(arr.astype(np.float32), 1)
    print(f"  Saved: {path}")


# ---------------------------------------------------------------------------
# Step 2 – Per-object depression metrics
# ---------------------------------------------------------------------------

def compute_depression_metrics(
    label_arr: np.ndarray,
    fill_depth: np.ndarray,
    pix_area_m2: float,
    depression_min_depth_m: float = 0.1,
) -> pd.DataFrame:
    """
    Compute per-object depression statistics from the fill-depth raster.

    Parameters
    ----------
    label_arr : ndarray
        Integer-labeled flood object raster (background = 0).
    fill_depth : ndarray
        Depression depth raster (spill − elev ≥ 0).
    pix_area_m2 : float
        Pixel area in m².
    depression_min_depth_m : float
        Pixels with fill_depth > this are considered 'in depression' (default 0.1 m).

    Returns
    -------
    DataFrame with columns: ID, depre_mn, depre_max, depre_sd, depre_pct, depre_vol
    """
    obj_ids = np.unique(label_arr)
    obj_ids = obj_ids[obj_ids > 0]

    records = []
    for obj_id in obj_ids:
        mask = label_arr == obj_id
        depths = fill_depth[mask]
        depths = depths[~np.isnan(depths)]

        if len(depths) == 0:
            records.append(dict(ID=int(obj_id),
                                depre_mn=np.nan, depre_max=np.nan,
                                depre_sd=np.nan, depre_pct=0.0, depre_vol=0.0))
            continue

        in_dep = (depths > depression_min_depth_m).sum()
        records.append({
            "ID":       int(obj_id),
            "depre_mn": float(np.nanmean(depths)),
            "depre_max": float(np.nanmax(depths)),
            "depre_sd": float(np.nanstd(depths)),
            "depre_pct": float(in_dep / len(depths)),
            "depre_vol": float(np.nansum(depths) * pix_area_m2),
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Step 3 – Pit-bottom detection and has_pit flag
# ---------------------------------------------------------------------------

def detect_pit_presence(
    label_arr: np.ndarray,
    fill_depth: np.ndarray,
    min_depression_size_px: int = 50,
) -> pd.DataFrame:
    """
    Detect topographic pit bottoms inside large depressions and flag which
    flood objects contain at least one pit bottom.

    Parameters
    ----------
    label_arr : ndarray
        Integer-labeled flood object raster.
    fill_depth : ndarray
        Depression depth raster from :func:`fill_depressions_spill`.
    min_depression_size_px : int
        Minimum connected depression size to be considered (default 50).

    Returns
    -------
    DataFrame with columns: ID, has_pit  (has_pit ∈ {0, 1})
    """
    pit_mask_raw = fill_depth > 0
    dep_labels, num_dep = nd_label(pit_mask_raw)

    if num_dep == 0:
        obj_ids = np.unique(label_arr)
        obj_ids = obj_ids[obj_ids > 0]
        return pd.DataFrame({"ID": obj_ids.astype(int), "has_pit": 0})

    dep_sizes = np.bincount(dep_labels.ravel())
    large_ids = np.where(dep_sizes >= min_depression_size_px)[0]
    large_mask = np.isin(dep_labels, large_ids[large_ids > 0])

    H, W = fill_depth.shape
    pit_bottoms = np.zeros((H, W), dtype=np.uint8)

    ys, xs = np.where(large_mask)
    for y, x in zip(ys, xs):
        center = fill_depth[y, x]
        is_local_max = True
        for dy, dx in [(-1, -1), (-1, 0), (-1, 1),
                       (0, -1),           (0, 1),
                       (1, -1),  (1, 0),  (1, 1)]:
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W:
                if fill_depth[ny, nx] > center:
                    is_local_max = False
                    break
        if is_local_max:
            pit_bottoms[y, x] = 1

    print(f"[detect_pit_presence] {pit_bottoms.sum()} pit-bottom pixels "
          f"in {(large_mask > 0).sum()} large-depression pixels.")

    obj_ids = np.unique(label_arr)
    obj_ids = obj_ids[obj_ids > 0]
    records = []
    for obj_id in obj_ids:
        obj_mask = label_arr == obj_id
        has_pit = int(np.any(obj_mask & (pit_bottoms > 0)))
        records.append({"ID": int(obj_id), "has_pit": has_pit})

    df = pd.DataFrame(records)
    n_with_pit = int(df["has_pit"].sum())
    print(f"[detect_pit_presence] {n_with_pit}/{len(df)} objects contain pit bottoms.")
    return df


# ---------------------------------------------------------------------------
# Step 4 – Per-object flood depth metrics  (requires LWSE column)
# ---------------------------------------------------------------------------

def compute_depth_metrics(
    label_arr: np.ndarray,
    dem_arr: np.ndarray,
    attrs_df: pd.DataFrame,
    pix_area_m2: float,
    lwse_col: str = "LWSE",
) -> pd.DataFrame:
    """
    Compute per-object flood depth statistics using the estimated LWSE.

    depth = LWSE − DEM_elevation  (positive = submerged)

    Parameters
    ----------
    label_arr : ndarray
        Integer-labeled flood object raster.
    dem_arr : ndarray
        DEM elevation array (same spatial extent and resolution as label_arr).
    attrs_df : DataFrame
        Object attribute table — must contain ``ID`` and the LWSE column.
    pix_area_m2 : float
        Pixel area in m².
    lwse_col : str
        Name of the LWSE column in attrs_df (default ``'LWSE'``).

    Returns
    -------
    DataFrame with columns:
        ID, depth_mn, depth_md, depth_min, depth_max, depth_sd, frac_neg, depth_vol
    """
    id_to_lwse = dict(zip(attrs_df["ID"].values, attrs_df[lwse_col].values))

    records = []
    for obj_id, lwse in id_to_lwse.items():
        if np.isnan(lwse):
            records.append(dict(ID=int(obj_id),
                                depth_mn=np.nan, depth_md=np.nan,
                                depth_min=np.nan, depth_max=np.nan,
                                depth_sd=np.nan, frac_neg=np.nan, depth_vol=np.nan))
            continue

        mask = label_arr == obj_id
        if not mask.any():
            records.append(dict(ID=int(obj_id),
                                depth_mn=np.nan, depth_md=np.nan,
                                depth_min=np.nan, depth_max=np.nan,
                                depth_sd=np.nan, frac_neg=np.nan, depth_vol=np.nan))
            continue

        elev  = dem_arr[mask].astype(float)
        depth = lwse - elev
        depth = depth[~np.isnan(depth)]
        if len(depth) == 0:
            continue

        pos_depth = depth[depth > 0]
        volume = float(pos_depth.sum() * pix_area_m2) if len(pos_depth) > 0 else 0.0

        records.append({
            "ID":       int(obj_id),
            "depth_mn": float(np.mean(depth)),
            "depth_md": float(np.median(depth)),
            "depth_min": float(np.min(depth)),
            "depth_max": float(np.max(depth)),
            "depth_sd": float(np.std(depth)),
            "frac_neg": float(np.mean(depth < 0)),
            "depth_vol": volume,
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Convenience wrapper – run the full depression pipeline and merge results
# ---------------------------------------------------------------------------

def add_depression_attributes(
    attrs_df: pd.DataFrame,
    label_arr: np.ndarray,
    dem_path: str,
    pix_area_m2: float,
    output_dir: Optional[str] = None,
    gaussian_sigma: float = 1.5,
    min_depression_size_px: int = 50,
    depression_min_depth_m: float = 0.1,
    lwse_col: str = "LWSE",
) -> pd.DataFrame:
    """
    Full pipeline: DEM → fill → depression metrics → has_pit → depth metrics.

    Merges all new columns into *attrs_df* and returns the enriched DataFrame.
    """
    with rasterio.open(dem_path) as _dem_src:
        _dem_shape = (_dem_src.height, _dem_src.width)
    if _dem_shape != label_arr.shape:
        raise ValueError(
            f"DEM shape {_dem_shape} does not match label_arr shape "
            f"{label_arr.shape}. Reproject the DEM to the FIM grid first:\n\n"
            f"    from fimsens.filter import align_rasters\n"
            f"    dem_path = align_rasters(dem_path, fim_objects_path, "
            f"'dem_aligned.tif')\n"
        )

    print("[add_depression_attributes] Filling depressions...")
    spill, fill_depth, dem_meta = fill_depressions_from_raster(
        dem_path, output_dir=output_dir, gaussian_sigma=gaussian_sigma
    )

    with rasterio.open(dem_path) as src:
        dem_arr = src.read(1).astype(float)
        nodata = src.nodata
        if nodata is not None:
            dem_arr[dem_arr == nodata] = np.nan

    print("[add_depression_attributes] Computing depression metrics...")
    dep_df = compute_depression_metrics(
        label_arr, fill_depth, pix_area_m2,
        depression_min_depth_m=depression_min_depth_m,
    )

    print("[add_depression_attributes] Detecting pit presence...")
    pit_df = detect_pit_presence(
        label_arr, fill_depth,
        min_depression_size_px=min_depression_size_px,
    )

    if lwse_col in attrs_df.columns:
        print("[add_depression_attributes] Computing water depth metrics...")
        depth_df = compute_depth_metrics(
            label_arr, dem_arr, attrs_df, pix_area_m2, lwse_col=lwse_col
        )
    else:
        print(f"[add_depression_attributes] '{lwse_col}' column not found — "
              "skipping depth metrics. Compute LWSE first.")
        depth_df = pd.DataFrame({"ID": attrs_df["ID"].values})

    result = attrs_df.copy()
    result = result.merge(dep_df, on="ID", how="left")
    result = result.merge(pit_df, on="ID", how="left")
    result = result.merge(depth_df, on="ID", how="left")

    n_pit = int(result.get("has_pit", pd.Series([0])).sum())
    print(f"[add_depression_attributes] Done. {n_pit} objects with pits.")
    return result
