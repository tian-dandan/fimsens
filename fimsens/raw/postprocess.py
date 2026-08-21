"""
Post-processing for the binary flood map.

Replaces the original ImageObject C-extension (no longer installable) with
standard Python packages:

  * Morphological closing / opening : scipy.ndimage  (compiled C, fast)
  * Small-object removal            : skimage.morphology.remove_small_objects
  * Small-hole filling              : skimage.morphology.remove_small_holes

Both skimage and scipy are included in every standard scientific Python
environment (Anaconda, conda-forge, pip). No compiled extension needed.

Inputs / outputs are read/written with rasterio to preserve CRS, transform,
and nodata metadata exactly.
"""

import os
import numpy as np
import rasterio
from scipy.ndimage import binary_closing, binary_opening
from skimage.morphology import remove_small_objects, remove_small_holes


def postProcess(inImg, outImg, minObject=100, minHoleSize=10000):
    """
    Clean up a binary flood map.

    Operations (in order)
    ---------------------
    1. Morphological closing  (3 × 3 structuring element) — bridges narrow gaps.
    2. Morphological opening  (3 × 3 structuring element) — removes isolated noise.
    3. Remove objects smaller than *minObject* pixels.
    4. Fill holes smaller than *minHoleSize* pixels.

    Parameters
    ----------
    inImg : str
        Path to the input binary flood raster (0/1, uint8).
    outImg : str
        Path for the output cleaned raster.
    minObject : int
        Minimum object size in pixels.  Objects smaller than this are removed.
    minHoleSize : int
        Maximum hole size in pixels that will be filled.
        Holes larger than this are left open.
    """

    # ── Read ────────────────────────────────────────────────────────────────
    with rasterio.open(inImg) as src:
        image   = src.read(1)           # 2-D uint8 array
        profile = src.profile.copy()

    mask = image.astype(bool)           # work in boolean throughout

    # ── Morphological closing ────────────────────────────────────────────────
    struct3 = np.ones((3, 3), dtype=bool)
    print("Post-processing: morphological closing …")
    mask = binary_closing(mask, structure=struct3)

    # ── Morphological opening ────────────────────────────────────────────────
    print("Post-processing: morphological opening …")
    mask = binary_opening(mask, structure=struct3)

    # ── Remove small objects ─────────────────────────────────────────────────
    print(f"Post-processing: removing objects < {minObject:,} px …")
    mask = remove_small_objects(mask, min_size=minObject, connectivity=2)

    # ── Fill small holes ─────────────────────────────────────────────────────
    print(f"Post-processing: filling holes < {minHoleSize:,} px …")
    mask = remove_small_holes(mask, area_threshold=minHoleSize, connectivity=2)

    # ── Write ────────────────────────────────────────────────────────────────
    out_array = mask.astype(np.uint8)
    profile.update(dtype='uint8', count=1)

    with rasterio.open(outImg, 'w', **profile) as dst:
        dst.write(out_array, 1)

    flood_px = int(out_array.sum())
    print(f"Post-processing complete.")
    print(f"  Flood pixels : {flood_px:,}  ({100*flood_px/out_array.size:.2f}% of scene)")
    print(f"  Saved: {outImg}")
