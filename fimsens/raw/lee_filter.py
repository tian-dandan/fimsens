"""
Local Lee speckle filter for SAR images.

Two filter variants are available:

  1. Standard Lee filter (default)
  2. Enhanced Lee filter  (enhanced=True)

Usage
-----
  from fimsens.raw import leeFilt

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


def _warn_if_unit_mismatch(band, db_input, input_path):
    """
    Print a warning when ``db_input=True`` but the pixel values look linear.

    SAR backscatter in dB is predominantly negative (Sentinel-1 VV is roughly
    -25..0 dB), whereas linear power/amplitude is strictly positive and small.
    So all-positive data with a maximum below 2 is almost certainly linear and
    ``db_input=True`` would be wrong.

    This only warns — the caller's choice is still honoured, because a
    genuinely unusual image should not be silently reinterpreted.
    """
    if not db_input:
        return
    finite = band[np.isfinite(band)]
    if finite.size == 0:
        return
    vmin, vmax = float(finite.min()), float(finite.max())
    if vmin > 0 and vmax < 2:
        print(
            f"  [WARNING] db_input=True but {input_path} has values in "
            f"[{vmin:.4g}, {vmax:.4g}] — all positive and below 2, which looks "
            f"like linear power, not dB. If so, pass db_input=False; "
            f"otherwise the dB->linear conversion will corrupt the result."
        )


def leeFilt(input_path, output_path, window_size=7, enl=4.9,
            enhanced=False, db_input=True):
    """
    Apply a Lee speckle filter to a SAR GeoTIFF and write the result.

    Parameters
    ----------
    input_path  : str   Path to the input SAR image.
    output_path : str   Path where the filtered GeoTIFF will be saved.
    window_size : int   Sliding-window size (pixels, must be odd). Default 7.
                        Raise to 9 or 11 for noisier scenes (urban, rough
                        terrain); larger windows suppress more speckle but
                        blur flood boundaries.
    enl         : float Equivalent Number of Looks. Default 4.9, which is
                        specific to Sentinel-1 IW GRD. For other products
                        (EW mode, differently multi-looked data) recompute as
                        (mean / std)^2 over a homogeneous area.
    enhanced    : bool  False = Standard Lee (default); True = Enhanced Lee,
                        which better preserves point targets (buildings,
                        ships) at some cost in speckle suppression over water.
    db_input    : bool  True (default) = input is in decibels. This matches
                        fimsens.download: Earth Engine's COPERNICUS/S1_GRD
                        collection is log-scaled, with VV/VH in dB.
                        The filter converts dB -> linear power before
                        filtering and back afterwards, so the output is also
                        in dB.
                        Set False only for linear power/amplitude input (for
                        example COPERNICUS/S1_GRD_FLOAT, or a SNAP export that
                        was not converted to dB).
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

            if b == 1:
                _warn_if_unit_mismatch(band, db_input, input_path)

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
