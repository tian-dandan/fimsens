"""
Image preparation: mosaic, auto-clip to valid data extent, align JRC mask.

No manual extent is required.  The pipeline:

  1. Mosaic multiple input images if provided.
  2. Determine which band to read:
       - SAR:     always band 1 (single-band backscatter).
       - optical: nir_band_index (1-based), validated against the actual
                  band count.  An error is raised immediately if the index
                  is out of range so the user knows before any processing.
  3. Detect the valid-data bounding box from that band — pixels flagged as
     nodata, NaN/Inf, or exactly 0 (GEE fill outside the acquisition swath)
     are treated as invalid edge fill.
  4. Read that band, clipped to the valid bounding box → sar.tif
  5. Reproject the JRC occurrence layer to the satellite CRS if needed,
     then clip and resample it to exactly match the sar.tif pixel grid
     in a single reproject() call.
  6. Apply the occurrence threshold → binary permanent-water mask →
     jrc_watermask.tif
"""

import os
import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import reproject, Resampling
import rasterio.windows


# ---------------------------------------------------------------------------
# Helper: find the bounding Window of valid pixels in a given band
# ---------------------------------------------------------------------------

def _find_valid_bounds(src, band_index=1):
    """
    Return (Window, bounds) of the smallest rectangle enclosing all valid
    pixels in *band_index*.
    """
    data   = src.read(band_index)
    nodata = src.nodata

    valid = np.ones(data.shape, dtype=bool)

    if nodata is not None:
        valid &= (data != nodata)

    if np.issubdtype(data.dtype, np.floating):
        valid &= np.isfinite(data)
        valid &= (data != 0.0)   # GEE fill outside SAR / optical swath

    rows = np.any(valid, axis=1)
    cols = np.any(valid, axis=0)

    if not rows.any() or not cols.any():
        raise ValueError(
            f"No valid pixels found in band {band_index} of '{src.name}'.\n"
            "Check that the image is not entirely nodata / NaN / zero."
        )

    r0 = int(np.argmax(rows))
    r1 = int(len(rows) - np.argmax(rows[::-1]))   # exclusive
    c0 = int(np.argmax(cols))
    c1 = int(len(cols) - np.argmax(cols[::-1]))   # exclusive

    window = rasterio.windows.Window(
        col_off=c0, row_off=r0,
        width=c1 - c0, height=r1 - r0,
    )
    bounds = rasterio.windows.bounds(window, src.transform)
    return window, bounds


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def imagePrep(
    sarImages,
    jrcImg,
    workspace,
    threshold      = 20,      # JRC occurrence % for the permanent-water seed
    image_type     = 'SAR',   # 'SAR' or 'optical'
    nir_band_index = 1,        # 1-based; used only when image_type='optical'
):
    """
    Prepare satellite and JRC images for flood mapping.

    Parameters
    ----------
    sarImages : list[str]
        One or more paths to SAR backscatter OR optical images.
    jrcImg : str
        Path to the JRC Global Surface Water 'occurrence' raster (0–100).
    workspace : str
        Directory where output files are written.
    threshold : int | float
        JRC occurrence threshold (%) for the binary permanent-water mask:
        pixels with occurrence above this value become seed water. Default 20,
        established experimentally. Lower it (e.g. 10) for narrow rivers, whose
        occurrence values are diluted by mixed pixels and can otherwise fall
        below the cut, leaving the channel without seeds for the region grow.
    image_type : str
        'SAR' or 'optical'.
    nir_band_index : int
        1-based index of the NIR band (optical mode only).
    """

    sar_out     = os.path.join(workspace, 'rs_band.tif')
    jrc_out     = os.path.join(workspace, 'jrc_seed.tif')
    _tmp_mosaic = os.path.join(workspace, '_tmp_mosaic.tif')

    # ── 1. Mosaic multiple inputs ────────────────────────────────────────────
    if len(sarImages) > 1:
        print(f"Mosaicking {len(sarImages)} images …")
        srcs      = [rasterio.open(p) for p in sarImages]
        mosaic, t = merge(srcs)
        prof      = srcs[0].profile.copy()
        prof.update(
            driver='GTiff',
            count=mosaic.shape[0],
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=t,
        )
        for s in srcs:
            s.close()
        with rasterio.open(_tmp_mosaic, 'w', **prof) as dst:
            dst.write(mosaic)
        work = _tmp_mosaic
    else:
        work = sarImages[0]

    # ── 2. Determine which band to read ─────────────────────────────────────
    with rasterio.open(work) as src:
        n_bands = src.count

        if image_type == 'optical':
            if nir_band_index < 1 or nir_band_index > n_bands:
                raise ValueError(
                    f"nir_band_index={nir_band_index} is out of range — "
                    f"the image has {n_bands} band(s).  "
                    f"Valid range: 1 – {n_bands}."
                )
            read_band = nir_band_index
            print(f"Optical mode: reading band {read_band} as NIR  "
                  f"(image has {n_bands} band(s))")
        else:
            read_band = 1
            if n_bands > 1:
                print(f"SAR mode: reading band 1  "
                      f"(image has {n_bands} bands — extra bands ignored)")
            else:
                print(f"SAR mode: single-band image")

        # ── 3. Find valid-data extent ────────────────────────────────────────
        win, bounds = _find_valid_bounds(src, band_index=read_band)
        sat_crs   = src.crs
        sat_trans = rasterio.windows.transform(win, src.transform)
        sat_w     = win.width
        sat_h     = win.height

        data = src.read(read_band, window=win)
        prof = src.profile.copy()
        prof.update(
            count=1, dtype='float32',
            height=sat_h, width=sat_w,
            transform=sat_trans,
        )

    # ── 4. Save rs_band.tif ─────────────────────────────────────────────────
    with rasterio.open(sar_out, 'w', **prof) as dst:
        dst.write(data.astype(np.float32), 1)

    print(f"Saved: {sar_out}")
    print(f"  Band   : {read_band}")
    print(f"  Size   : {sat_w} × {sat_h} px")
    print(f"  CRS    : {sat_crs}")
    print(f"  Bounds : left={bounds[0]:.1f}  bottom={bounds[1]:.1f}  "
          f"right={bounds[2]:.1f}  top={bounds[3]:.1f}")

    if os.path.exists(_tmp_mosaic):
        os.remove(_tmp_mosaic)

    # ── 5. Reproject + clip + resample JRC to match rs_band.tif ─────────────
    print("Aligning JRC water mask to satellite grid …")

    jrc_raw = np.zeros((sat_h, sat_w), dtype=np.float32)

    with rasterio.open(jrcImg) as jrc_src:
        if jrc_src.crs != sat_crs:
            print(f"  Reprojecting JRC: {jrc_src.crs} → {sat_crs}")
        else:
            print("  JRC CRS matches satellite — no reprojection needed.")

        reproject(
            source        = rasterio.band(jrc_src, 1),
            destination   = jrc_raw,
            src_transform = jrc_src.transform,
            src_crs       = jrc_src.crs,
            dst_transform = sat_trans,
            dst_crs       = sat_crs,
            resampling    = Resampling.nearest,
        )

    binary = (jrc_raw > threshold).astype(np.uint8)

    jrc_prof = {
        'driver'    : 'GTiff',
        'dtype'     : 'uint8',
        'width'     : sat_w,
        'height'    : sat_h,
        'count'     : 1,
        'crs'       : sat_crs,
        'transform' : sat_trans,
    }
    with rasterio.open(jrc_out, 'w', **jrc_prof) as dst:
        dst.write(binary, 1)

    water_pct = 100.0 * binary.sum() / binary.size
    print(f"Saved: {jrc_out}")
    print(f"  Permanent-water pixels: {binary.sum():,}  ({water_pct:.2f}% of scene)")
    print("Image preparation complete.")
