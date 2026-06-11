"""
Local Lee speckle filter for SAR images.

Two filter variants are available:

  1. Standard Lee filter (default)
  2. Enhanced Lee filter  (enhanced=True)

Usage
-----
  from fimsense.raw import leeFilt

  # Linear input (GEE export default)
  leeFilt('S1_linear.tif', 'S1_lee.tif', window_size=7, enl=4.9)

  # dB input (SNAP / pre-processed product)
  leeFilt('S1_dB.tif', 'S1_lee.tif', window_size=7, enl=4.9, db_input=True)
"""

import os
import numpy as np
import rasterio
from scipy.ndimage import uniform_filter


def _local_mean_var_ignore_invalid(arr, window_size):
    """Compute local mean/variance while ignoring NaN/inf values."""
    valid = np.isfinite(arr)
    arr0 = np.where(valid, arr, 0.0)

    w = uniform_filter(valid.astype(np.float64), size=window_size)
    sum_x = uniform_filter(arr0, size=window_size)
    sum_x2 = uniform_filter(arr0 ** 2, size=window_size)

    with np.errstate(invalid='ignore', divide='ignore'):
        mean = sum_x / w
        mean2 = sum_x2 / w
        var_l = mean2 - mean ** 2

    mean[w <= 0] = np.nan
    var_l[w <= 0] = np.nan
    var_l = np.maximum(var_l, 0.0)
    return mean, var_l, valid

def _lee(arr, window_size, enl):
    """Standard Lee filter on a 2-D float array."""
    mean, var_l, valid = _local_mean_var_ignore_invalid(arr, window_size)

    var_n  = (mean ** 2) / enl
    k      = var_l / (var_l + var_n + 1e-15)
    k      = np.clip(k, 0.0, 1.0)

    out = mean + k * (arr - mean)
    out[~valid] = np.nan
    return out


def _enhanced_lee(arr, window_size, enl, cu=0.523, cmax=1.73):
    """Enhanced Lee filter on a 2-D float array."""
    mean, var_l, valid = _local_mean_var_ignore_invalid(arr, window_size)
    std_l  = np.sqrt(var_l)

    ci     = std_l / (mean + 1e-15)

    var_n  = (mean ** 2) / enl
    k      = var_l / (var_l + var_n + 1e-15)
    k      = np.clip(k, 0.0, 1.0)

    out = np.where(ci <= cu,   mean,
          np.where(ci >= cmax, arr,
                               mean + k * (arr - mean)))
    out[~valid] = np.nan
    return out


def leeFilt(input_path, output_path, window_size=7, enl=4.9,
            enhanced=False, db_input=False):
    """
    Apply a Lee speckle filter to a SAR GeoTIFF and write the result.

    Parameters
    ----------
    input_path  : str   Path to the input SAR image.
    output_path : str   Path where the filtered GeoTIFF will be saved.
    window_size : int   Sliding-window size (pixels, must be odd). Default 7.
    enl         : float Equivalent Number of Looks. Sentinel-1 IW GRD → 4.9.
    enhanced    : bool  False = Standard Lee; True = Enhanced Lee.
    db_input    : bool  False = linear (GEE export); True = dB (SNAP output).
    """
    if window_size % 2 == 0:
        raise ValueError(f"window_size must be odd (got {window_size}).")
    if enl <= 0:
        raise ValueError(f"enl must be > 0 (got {enl}).")

    filter_fn = _enhanced_lee if enhanced else _lee
    label     = "Enhanced Lee" if enhanced else "Lee"
    scale     = "dB→linear→dB" if db_input else "linear"

    print(f"Lee filter  [{label}]  window={window_size}×{window_size}  "
          f"ENL={enl}  scale={scale}")

    with rasterio.open(input_path) as src:
        profile    = src.profile.copy()
        n_bands    = src.count
        src_nodata = src.nodata

        out_data = np.zeros((n_bands, src.height, src.width), dtype=np.float32)

        for b in range(1, n_bands + 1):
            band = src.read(b).astype(np.float64)

            if src_nodata is not None:
                band[band == src_nodata] = np.nan

            if db_input:
                band = 10.0 ** (band / 10.0)

            filtered = filter_fn(band, window_size, enl)

            if db_input:
                filtered = 10.0 * np.log10(np.maximum(filtered, 1e-15))

            if src_nodata is not None:
                filtered = np.where(np.isfinite(filtered), filtered, src_nodata)

            out_data[b - 1] = filtered.astype(np.float32)

    profile.update(dtype='float32', count=n_bands, nodata=src_nodata)

    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(out_data)

    print(f"  Saved: {output_path}")
    print(f"  Bands filtered: {n_bands}")
