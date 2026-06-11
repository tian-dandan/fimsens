import os
from osgeo import gdal, ogr
import numpy as np


def interpFlood(workspace, threshold_points, field, sarImage, power):
    """
    Interpolate threshold points via IDW and classify flood pixels.

    Parameters
    ----------
    workspace         : str   Output directory. Writes threshold_idw.tif and flood.tif.
    threshold_points  : str   Path to threshold_filtered.shp (output of filterOutliers).
    field             : str   Field name containing threshold values (e.g. 'threshold').
    sarImage          : str   Path to rs_band.tif (SAR or optical NIR raster).
    power             : float IDW power parameter (e.g. 2.0).
    """
    gdal.UseExceptions()
    ogr.UseExceptions()

    dem = gdal.Open(sarImage)
    band = dem.GetRasterBand(1)
    demData = band.ReadAsArray()
    dem__prj = dem.GetProjection()
    geotransform = dem.GetGeoTransform()
    origin_x = geotransform[0]
    pixel_width = geotransform[1]
    rotation_x = geotransform[2]
    origin_y = geotransform[3]
    rotation_y = geotransform[4]
    pixel_height = geotransform[5]

    cols = dem.RasterXSize
    rows = dem.RasterYSize

    min_x = origin_x
    max_x = origin_x + (cols * pixel_width) + (rows * rotation_x)
    min_y = origin_y + (cols * rotation_y) + (rows * pixel_height)
    max_y = origin_y
    outbnd = [min_x, min_y, max_x, max_y]

    idwImage  = os.path.join(workspace, "threshold_idw.tif")
    outRaster = os.path.join(workspace, "flood.tif")

    # ── IDW interpolation ────────────────────────────────────────────────────
    print("Performing spatial interpolation")
    if os.path.exists(idwImage):
        os.remove(idwImage)

    ds = gdal.Grid(idwImage, threshold_points, format='GTiff',
                   outputBounds=outbnd,
                   width=cols, height=rows, outputType=gdal.GDT_Float32,
                   algorithm=f'invdist:power={power}:smoothing=1.0',
                   zfield=field)
    idwRaster = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    print(f"  IDW threshold surface saved: {idwImage}")

    # ── Build valid-data mask from rs_band ───────────────────────────────────
    nodata_val = band.GetNoDataValue()
    if nodata_val is not None:
        valid_mask = (demData != nodata_val)
    else:
        if np.issubdtype(demData.dtype, np.floating):
            valid_mask = np.isfinite(demData) & (demData != 0.0)
        else:
            valid_mask = np.ones(demData.shape, dtype=bool)

    # ── Classify: water where SAR < threshold, but only inside valid area ────
    flood_array = np.where(valid_mask & (demData < idwRaster), 1, 0).astype(np.uint8)
    FLOOD_NODATA = 255
    flood_array[~valid_mask] = FLOOD_NODATA

    # ── Write flood.tif ──────────────────────────────────────────────────────
    if os.path.exists(outRaster):
        os.remove(outRaster)
    print(f"  Writing flood map: {outRaster}")

    driver = gdal.GetDriverByName('GTiff')
    outds = driver.Create(outRaster, cols, rows, 1, gdal.GDT_Byte)
    outds.SetGeoTransform(geotransform)
    outds.SetProjection(dem__prj)
    out_band = outds.GetRasterBand(1)
    out_band.SetNoDataValue(FLOOD_NODATA)
    out_band.WriteArray(flood_array)
    outds = None
