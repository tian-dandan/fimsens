"""
Region-grow flood mapping.

Two backends are available — selected automatically at import time:

  1. Numba JIT (fastest)    — requires: pip install numba
  2. scipy.ndimage fallback — no extra install needed

Works for both SAR (low backscatter = water) and optical NIR
(low reflectance = water) because the threshold condition is value < threshold.
"""

import os
import numpy as np
from .utils import readImageGDAL, writeImageGDAL

# ---------------------------------------------------------------------------
# Try to compile a Numba BFS; fall back to scipy if Numba is unavailable
# ---------------------------------------------------------------------------

try:
    from numba import njit, types
    from numba.typed import List as NbList

    @njit(cache=True)
    def _bfs_numba(grow_image, seed_mask, valid_mask, threshold, rows, cols):
        """
        Iterative BFS (8-connectivity) from all seed pixels.
        Returns a boolean output array: True where a water-connected pixel was found.
        """
        output  = np.zeros((rows, cols), dtype=np.uint8)
        visited = np.zeros((rows, cols), dtype=np.bool_)

        queue_r = np.empty(rows * cols, dtype=np.int32)
        queue_c = np.empty(rows * cols, dtype=np.int32)
        head = 0
        tail = 0

        for r in range(rows):
            for c in range(cols):
                if seed_mask[r, c] and valid_mask[r, c] and grow_image[r, c] < threshold:
                    visited[r, c]  = True
                    output[r, c]   = 1
                    queue_r[tail]  = r
                    queue_c[tail]  = c
                    tail += 1

        dr = (-1, -1, -1,  0,  0,  1,  1,  1)
        dc = (-1,  0,  1, -1,  1, -1,  0,  1)

        while head < tail:
            r = queue_r[head]
            c = queue_c[head]
            head += 1

            for k in range(8):
                nr = r + dr[k]
                nc = c + dc[k]
                if (0 <= nr < rows and 0 <= nc < cols
                        and not visited[nr, nc]
                        and valid_mask[nr, nc]
                        and grow_image[nr, nc] < threshold):
                    visited[nr, nc] = True
                    output[nr, nc]  = 1
                    queue_r[tail]   = nr
                    queue_c[tail]   = nc
                    tail += 1

        return output

    _BACKEND = 'numba'

except ImportError:
    _BACKEND = 'scipy'


def _grow_scipy(grow_image, seed_mask, valid_mask, threshold):
    """scipy connected-component fallback."""
    from scipy import ndimage

    candidates = (grow_image < threshold) & valid_mask

    struct     = np.ones((3, 3), dtype=int)
    labeled, _ = ndimage.label(candidates, structure=struct)

    seed_labels = np.unique(labeled[seed_mask])
    seed_labels = seed_labels[seed_labels > 0]

    if seed_labels.size == 0:
        return np.zeros_like(grow_image, dtype=np.uint8)

    lookup = np.zeros(labeled.max() + 1, dtype=np.uint8)
    lookup[seed_labels] = 1
    return lookup[labeled]


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def regionGrow(workspace, seed_image_path, grow_image_path, threshold):
    """
    Grow flood regions from permanent-water seed pixels.

    Parameters
    ----------
    workspace        : str   Output directory. Result → <workspace>/water_connect.tif
    seed_image_path  : str   Binary permanent-water mask (jrc_seed.tif).
    grow_image_path  : str   SAR backscatter or optical NIR raster (rs_band.tif).
    threshold        : float Growth threshold.
                             SAR linear VV: recommended 0.05
                             Optical NIR 0-1: recommended 0.10
    """
    seed_image, seed_dataset = readImageGDAL(seed_image_path)
    grow_image, grow_dataset = readImageGDAL(grow_image_path)

    nodata = grow_dataset.GetRasterBand(1).GetNoDataValue()
    valid  = (grow_image != nodata) if nodata is not None else (grow_image != 0)

    seed_mask = seed_image > 0
    rows, cols = grow_image.shape

    print(f"Region grow  [backend: {_BACKEND}]  threshold={threshold}")

    if _BACKEND == 'numba':
        output_image = _bfs_numba(
            grow_image.astype(np.float64),
            seed_mask,
            valid,
            float(threshold),
            rows, cols,
        )
    else:
        output_image = _grow_scipy(grow_image, seed_mask, valid, threshold)

    if output_image.sum() == 0:
        print("WARNING: no water pixels found. "
              "Check threshold and seed image.")

    out_path = os.path.join(workspace, 'water_connect.tif')
    writeImageGDAL(out_path, output_image, seed_dataset)

    n_water = int(output_image.sum())
    print(f"  Water pixels: {n_water:,}  ({100*n_water/output_image.size:.2f}% of scene)")
    print(f"  Saved: {out_path}")
