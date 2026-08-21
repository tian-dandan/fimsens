"""
idw2flood.py
------------
IDW interpolation of LWSE point shapefile → flood depth and classification rasters.

Two entry points:
  idw2Flood   – depth-only: LWSE surface minus DEM elevation.
  idw2Flood1  – depth + region-grow: seed mask expanded by DEM < LWSE condition.
"""

import os
from osgeo import gdal, ogr, osr
import numpy as np
import pyproj


def _require_north_up(geotransform, dem_path: str, caller: str) -> None:
    """
    Reject a DEM whose geotransform carries rotation (shear) terms.

    Both IDW functions difference the interpolated LWSE surface against the DEM
    array pixel-by-pixel (``lwse - demData``).  That is only meaningful when the
    two arrays sit on the same grid, which in turn requires the DEM to be
    north-up: ``gdal.Grid`` always writes an axis-aligned raster, so a rotated
    DEM would be silently differenced against a surface it does not align with.

    Every raster produced by :mod:`fimsens.download` is north-up by
    construction (the GEE tile requests hard-code ``shearX``/``shearY`` to 0 and
    the mosaic step goes through ``gdal.Warp``), so this guard only fires for
    externally supplied DEMs.
    """
    if geotransform[2] != 0 or geotransform[4] != 0:
        raise ValueError(
            f"[{caller}] DEM has a rotated geotransform (shear terms "
            f"{geotransform[2]}, {geotransform[4]}): {dem_path}\n"
            f"{caller} assumes a north-up grid — the IDW surface and the DEM "
            f"are differenced pixel-by-pixel, which is meaningless on a rotated "
            f"grid.\nReproject the DEM onto the flood-map grid first:\n\n"
            f"    from fimsens.filter import align_rasters\n"
            f"    demName = align_rasters(demName, fim_raster, 'dem_aligned.tif')\n"
        )


def reproject_shapefile(input_shapefile, snap_raster_file, output_shapefile):
    """Reproject a shapefile to match the CRS of a raster and save the result."""
    src_ds = ogr.Open(input_shapefile)
    src_layer = src_ds.GetLayer()
    src_crs = src_layer.GetSpatialRef()

    raster_ds = gdal.Open(snap_raster_file)
    raster_projection = raster_ds.GetProjection()
    raster_crs = osr.SpatialReference()
    raster_crs.ImportFromWkt(raster_projection)

    transformer = pyproj.Transformer.from_crs(
        src_crs.ExportToProj4(), raster_crs.ExportToProj4(), always_xy=True
    )

    driver = ogr.GetDriverByName('ESRI Shapefile')
    output_ds = driver.CreateDataSource(output_shapefile)
    output_layer = output_ds.CreateLayer('reprojected', geom_type=ogr.wkbPoint)

    src_layer_defn = src_layer.GetLayerDefn()
    for i in range(src_layer_defn.GetFieldCount()):
        field_defn = src_layer_defn.GetFieldDefn(i)
        output_layer.CreateField(field_defn)

    for feature in src_layer:
        geom = feature.GetGeometryRef()
        x, y = geom.GetX(), geom.GetY()
        new_x, new_y = transformer.transform(x, y)

        new_geom = ogr.Geometry(ogr.wkbPoint)
        new_geom.SetPoint(0, new_x, new_y)

        new_feature = ogr.Feature(output_layer.GetLayerDefn())
        new_feature.SetGeometry(new_geom)
        for i in range(feature.GetFieldCount()):
            new_feature.SetField(i, feature.GetField(i))

        output_layer.CreateFeature(new_feature)
        new_feature = None

    src_ds = None
    output_ds = None
    print(f"Reprojected shapefile saved to {output_shapefile}")


def _region_grow(seed_mask, dem, lwse):
    """Perform region growing from seed pixels using DEM < LWSE as the grow condition."""
    grown = np.zeros_like(seed_mask, dtype=bool)
    visited = np.zeros_like(seed_mask, dtype=bool)
    stack = list(zip(*np.where(seed_mask)))

    while stack:
        r, c = stack.pop()
        if visited[r, c]:
            continue
        visited[r, c] = True
        grown[r, c] = True

        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if (0 <= nr < dem.shape[0]) and (0 <= nc < dem.shape[1]):
                    if not visited[nr, nc] and dem[nr, nc] < lwse[nr, nc]:
                        stack.append((nr, nc))
    return grown


def idw2Flood(fcName, fieldName, demName, outRaster, idw_temp, output_shapefile,
              max_points=12, power=3.0):
    """
    IDW interpolation of LWSE → flood depth raster (LWSE − DEM, NaN where LWSE < DEM).

    Parameters
    ----------
    fcName           : str   Input LWSE point shapefile.
    fieldName        : str   Field name for LWSE values (e.g. 'LWSE_new').
    demName          : str   DEM raster path. Must be north-up — see
                             _require_north_up().
    outRaster        : str   Output flood depth GeoTIFF path.
    idw_temp         : str   Temporary IDW raster path.
    output_shapefile : str   Temporary reprojected shapefile path.
    max_points       : int   IDW neighbourhood size. Default 12. Raise to 20
                             for a smoother depth surface; lower to 6 to keep
                             it more local.
    power            : float IDW distance-decay exponent for the LWSE surface.
                             Default 3.0. Higher (4-5) makes the surface follow
                             individual LWSE points closely, useful in
                             topographically complex terrain; lower (2) gives a
                             smoother, more regionally averaged surface, better
                             for flat floodplains.
                             Note: the THRESHOLD interpolation in
                             fimsens.raw.interpFlood defaults to 2.0 — the two
                             are intentionally different, not a typo.
    """
    dem = gdal.Open(demName)
    band = dem.GetRasterBand(1)
    demData = band.ReadAsArray()
    dem__prj = dem.GetProjection()
    geotransform = dem.GetGeoTransform()
    _require_north_up(geotransform, demName, "idw2Flood")

    cols = dem.RasterXSize
    rows = dem.RasterYSize

    min_x = geotransform[0]
    max_x = min_x + cols * geotransform[1]
    max_y = geotransform[3]
    min_y = max_y + rows * geotransform[5]
    outbnd = [min_x, min_y, max_x, max_y]

    reproject_shapefile(fcName, demName, output_shapefile)

    algorithm_string = f'invdist:power={power}:smoothing=0.0:max_points={max_points}'
    ds = gdal.Grid(idw_temp, output_shapefile, format='GTiff',
                   outputBounds=outbnd,
                   width=cols, height=rows, outputType=gdal.GDT_Float32,
                   algorithm=algorithm_string,
                   zfield=fieldName)

    idwRaster = ds.GetRasterBand(1).ReadAsArray()
    result_array = np.where(idwRaster >= demData, idwRaster - demData, np.nan)

    if os.path.exists(outRaster):
        os.remove(outRaster)
    driver = gdal.GetDriverByName('GTiff')
    outds = driver.Create(outRaster, cols, rows, 1, gdal.GDT_Float32)
    outds.SetGeoTransform(geotransform)
    outds.SetProjection(dem__prj)
    out_band = outds.GetRasterBand(1)
    out_band.WriteArray(result_array)
    out_band.SetNoDataValue(float('nan'))
    outds = None
    print(f"Flood depth raster saved: {outRaster}")


def idw2Flood1(fcName, demName, floodSeedRaster, idw_temp, outRaster, outClassRaster,
               output_shapefile, alignedSeedRaster,
               fieldName='LWSE_new', max_points=12, power=3.0, flood_class=1):
    """
    IDW + region-grow → flood depth map and 3-class classification raster.

    Class values in outClassRaster:
        1 = seed flood (confirmed)
        2 = grown flood (DEM < LWSE, connected to seed)
        0 = non-flood

    .. note::
       The parameter order changed on 2026-08-18: ``fieldName`` moved from
       second position to the end so it could take a default (Python requires
       defaulted parameters to come last). Call this function with KEYWORD
       arguments. Positional callers written against the old order will pass
       the field name where demName is expected.

    Parameters
    ----------
    fcName            : str   Input LWSE point shapefile.
    demName           : str   DEM raster path. Must be north-up — see
                              _require_north_up().
    floodSeedRaster   : str   Binary seed flood raster (flood pixels = flood_class).
    idw_temp          : str   Temporary IDW raster path.
    outRaster         : str   Output flood depth GeoTIFF path.
    outClassRaster    : str   Output 3-class classification raster path.
    output_shapefile  : str   Temporary reprojected shapefile path.
    alignedSeedRaster : str   Temporary aligned seed raster path.
    fieldName         : str   Field holding LWSE values. Default 'LWSE_new',
                              the outlier-filtered version. Use 'LWSE' to
                              compare against the raw estimate.
    max_points        : int   IDW neighbourhood size. Default 12. Raise to 20
                              for a smoother depth surface; lower to 6 to keep
                              it more local.
    power             : float IDW distance-decay exponent for the LWSE surface.
                              Default 3.0. Higher (4-5) follows individual LWSE
                              points closely, useful in complex terrain; lower
                              (2) gives a smoother regional surface, better for
                              flat floodplains.
                              Note: fimsens.raw.interpFlood interpolates a
                              different surface and defaults to 2.0 — the two
                              are intentionally different, not a typo.
    flood_class       : int   Pixel value in floodSeedRaster that marks seeds (default 1).
    """
    if not isinstance(demName, str) or not os.path.exists(demName):
        raise ValueError(
            f"idw2Flood1(): demName does not point to an existing file: "
            f"{demName!r}.\nIf that looks like a field name rather than a "
            f"path, this call is using the OLD parameter order: fieldName "
            f"moved from second position to the end on 2026-08-18, so the "
            f"second positional argument is now demName. Update this call to "
            f"use keyword arguments."
        )
    dem = gdal.Open(demName)
    band = dem.GetRasterBand(1)
    demData = band.ReadAsArray()
    dem_proj = dem.GetProjection()
    geotransform = dem.GetGeoTransform()
    _require_north_up(geotransform, demName, "idw2Flood1")
    cols, rows = dem.RasterXSize, dem.RasterYSize

    min_x = geotransform[0]
    max_x = min_x + cols * geotransform[1]
    max_y = geotransform[3]
    min_y = max_y + rows * geotransform[5]
    out_bounds = [min_x, min_y, max_x, max_y]

    reproject_shapefile(fcName, demName, output_shapefile)

    algorithm_string = f'invdist:power={power}:smoothing=0.0:max_points={max_points}'
    ds = gdal.Grid(idw_temp, output_shapefile, format='GTiff',
                   outputBounds=out_bounds,
                   width=cols, height=rows, outputType=gdal.GDT_Float32,
                   algorithm=algorithm_string,
                   zfield=fieldName)

    lwse = ds.GetRasterBand(1).ReadAsArray()

    warp_options = gdal.WarpOptions(
        format='GTiff',
        dstSRS=dem_proj,
        outputBounds=out_bounds,
        width=cols,
        height=rows,
        resampleAlg=gdal.GRA_NearestNeighbour,
        dstNodata=9999,
    )
    gdal.Warp(alignedSeedRaster, floodSeedRaster, options=warp_options)

    floodSeedDS = gdal.Open(alignedSeedRaster)
    floodSeed = floodSeedDS.GetRasterBand(1).ReadAsArray()

    seed_mask = floodSeed == flood_class
    grown_mask = _region_grow(seed_mask, demData, lwse)

    depth_map = np.full_like(demData, np.nan, dtype=float)
    depth_map[seed_mask] = np.maximum(0, lwse[seed_mask] - demData[seed_mask])
    depth_map[grown_mask & ~seed_mask] = lwse[grown_mask & ~seed_mask] - demData[grown_mask & ~seed_mask]

    class_map = np.zeros_like(demData, dtype=np.uint8)
    class_map[seed_mask] = 1
    class_map[grown_mask & ~seed_mask] = 2

    driver = gdal.GetDriverByName('GTiff')

    if os.path.exists(outRaster):
        os.remove(outRaster)
    outds = driver.Create(outRaster, cols, rows, 1, gdal.GDT_Float32)
    outds.SetGeoTransform(geotransform)
    outds.SetProjection(dem_proj)
    outds.GetRasterBand(1).WriteArray(depth_map)
    outds.GetRasterBand(1).SetNoDataValue(float('nan'))
    outds = None

    if os.path.exists(outClassRaster):
        os.remove(outClassRaster)
    classds = driver.Create(outClassRaster, cols, rows, 1, gdal.GDT_Byte)
    classds.SetGeoTransform(geotransform)
    classds.SetProjection(dem_proj)
    classds.GetRasterBand(1).WriteArray(class_map)
    classds = None

    print("Flood depth and classification rasters saved.")


def raster_to_polygons(input_raster_path, output_vector_path, field_name="class"):
    """Convert a classified raster (e.g., 3-class flood map) into polygons."""
    src_ds = gdal.Open(input_raster_path)
    band = src_ds.GetRasterBand(1)

    drv = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(output_vector_path):
        drv.DeleteDataSource(output_vector_path)
    out_ds = drv.CreateDataSource(output_vector_path)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(src_ds.GetProjection())
    out_layer = out_ds.CreateLayer("flood_polygons", srs=srs, geom_type=ogr.wkbPolygon)

    field_defn = ogr.FieldDefn(field_name, ogr.OFTInteger)
    out_layer.CreateField(field_defn)

    gdal.Polygonize(band, None, out_layer, 0, [], callback=None)

    src_ds = None
    out_ds = None
    print(f"Raster polygonized to: {output_vector_path}")
