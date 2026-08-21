"""
object_generation.py
--------------------
Generate labeled flood objects from a binary FIM raster.

Replaces:
    - ShorelineFromImage.MorOp        → morph_smooth()
    - ImageObject.RegionManagement    → generate_flood_objects()

Standard dependencies: rasterio, scipy.ndimage, scikit-image
"""

from __future__ import annotations

import os
from typing import Tuple, Optional

import numpy as np
import rasterio
from scipy.ndimage import binary_erosion, binary_dilation
from skimage.measure import label
from skimage.morphology import remove_small_objects, remove_small_holes


# ---------------------------------------------------------------------------
# Morphological smoothing
# ---------------------------------------------------------------------------

def morph_smooth(
    binary_arr: np.ndarray,
    operation: str = "eded",
    struct: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Apply a sequence of morphological operations to a binary array.

    Replaces ``ShorelineFromImage.MorOp``.

    Parameters
    ----------
    binary_arr : ndarray
        Binary input (0/1 or bool).
    operation : str
        Sequence of operations where 'e' = erosion, 'd' = dilation.
        Default ``'eded'``.
    struct : ndarray, optional
        Structuring element.  Default: 3×3 all-ones (8-connected).

    Returns
    -------
    result : ndarray, dtype uint8
    """
    if struct is None:
        struct = np.ones((3, 3), dtype=bool)

    op_map = {"e": binary_erosion, "d": binary_dilation}
    arr = binary_arr.astype(bool)

    for ch in operation.lower():
        if ch not in op_map:
            raise ValueError(f"Unknown operation '{ch}'. Use 'e' (erosion) or 'd' (dilation).")
        arr = op_map[ch](arr, structure=struct)

    return arr.astype(np.uint8)


# ---------------------------------------------------------------------------
# Flood object generation
# ---------------------------------------------------------------------------

def generate_flood_objects(
    fim_path: str,
    output_path: str,
    min_front: int = 0,
    min_back: int = 100,
    morph_operation: str = "eded",
    connectivity: int = 2,
) -> Tuple[np.ndarray, dict, int]:
    """
    Generate a labeled flood-object raster from a binary FIM.

    Replaces the ``IO.RegionManagement`` workflow:
        ``regionGeneration → removeSmallObjs → writeImage``

    Steps
    -----
    1. Read binary FIM (rasterio).
    2. Morphological smoothing (scipy.ndimage).
    3. Connected-component labeling (skimage.measure.label).
    4. Remove small foreground objects (skimage.morphology.remove_small_objects).
    5. Fill small holes (skimage.morphology.remove_small_holes).
    6. Re-label and save as UInt32 GeoTIFF.

    Parameters
    ----------
    fim_path : str
        Path to input binary FIM raster.
    output_path : str
        Path to save the labeled object raster (UInt32 GeoTIFF).
    min_front : int
        Remove foreground objects with area < min_front pixels.
        Use 0 to keep everything.
    min_back : int
        Fill holes with area <= min_back pixels.
        Use 0 to skip hole filling.
    morph_operation : str
        Morphological operation sequence applied before labeling.
    connectivity : int
        1 = 4-connected, 2 = 8-connected (default).

    Returns
    -------
    fim_objs : ndarray, uint32
        Labeled array; background = 0, each object has a unique integer ID.
    meta : dict
        Rasterio metadata of the output raster.
    num_regions : int
        Total number of labeled flood objects.
    """
    # --- Read input ---
    with rasterio.open(fim_path) as src:
        fim_arr = src.read(1)
        meta = src.meta.copy()
        valid_mask = src.read_masks(1) > 0
        nodata_val = src.nodata

    if nodata_val is not None:
        valid_mask &= fim_arr != nodata_val

    flood_mask = (fim_arr == 1) & valid_mask

    # --- Morphological smoothing ---
    fim_morph = morph_smooth(flood_mask.astype(np.uint8), operation=morph_operation).astype(bool)
    fim_morph &= valid_mask

    # --- Label connected components ---
    labeled = label(fim_morph, connectivity=connectivity)

    # --- Remove small foreground objects ---
    mask = labeled > 0
    if min_front > 0:
        mask = remove_small_objects(mask, min_size=min_front, connectivity=connectivity)

    # --- Fill small holes ---
    if min_back > 0:
        mask = remove_small_holes(mask, area_threshold=min_back, connectivity=connectivity)

    mask &= valid_mask

    # --- Re-label ---
    fim_objs = label(mask, connectivity=connectivity).astype(np.uint32)
    num_regions = int(fim_objs.max())

    # --- Save output raster ---
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_meta = meta.copy()
    out_meta.update({"dtype": "uint32", "count": 1, "nodata": 0})
    with rasterio.open(output_path, "w", **out_meta) as dst:
        dst.write(fim_objs, 1)

    print(f"[generate_flood_objects] {num_regions} objects saved → {output_path}")
    return fim_objs, out_meta, num_regions


# ---------------------------------------------------------------------------
# Label transfer utility (for validation continuity across re-runs)
# ---------------------------------------------------------------------------

def transfer_labels(
    old_obj_arr: np.ndarray,
    new_obj_arr: np.ndarray,
    old_labels: dict,
) -> dict:
    """
    Map ground-truth labels from old object IDs to new object IDs via maximum
    spatial overlap.

    Parameters
    ----------
    old_obj_arr : ndarray
        Old labeled raster (integer IDs).
    new_obj_arr : ndarray
        New labeled raster (integer IDs).
    old_labels : dict
        Mapping {old_id: label}.

    Returns
    -------
    new_labels : dict
        Mapping {new_id: label} for objects with overlap.
    """
    import pandas as pd

    if old_obj_arr.shape != new_obj_arr.shape:
        raise ValueError(
            f"Shape mismatch: old={old_obj_arr.shape}, new={new_obj_arr.shape}"
        )

    overlap_mask = (new_obj_arr > 0) & (old_obj_arr > 0)
    if not np.any(overlap_mask):
        return {}

    pairs = pd.DataFrame(
        {
            "new_id": new_obj_arr[overlap_mask].astype(np.int64),
            "old_id": old_obj_arr[overlap_mask].astype(np.int64),
        }
    )

    best = (
        pairs.groupby(["new_id", "old_id"])
        .size()
        .reset_index(name="overlap_px")
        .sort_values(["new_id", "overlap_px"], ascending=[True, False])
        .drop_duplicates("new_id")
    )

    new_labels = {}
    for _, row in best.iterrows():
        nid = int(row["new_id"])
        oid = int(row["old_id"])
        if oid in old_labels:
            lbl = int(old_labels[oid])
            new_labels[nid] = 0 if lbl == 3 else (lbl if lbl in (0, 1, 2) else 0)

    return new_labels
