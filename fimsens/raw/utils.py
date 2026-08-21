"""
fimsens.raw.utils — small GDAL read/write helpers used by the raw pipeline.
"""

from osgeo import gdal


def readImageGDAL(filename):
    """
    Read band 1 of a raster and return both the array and the open dataset.

    Parameters
    ----------
    filename : str
        Raster path.

    Returns
    -------
    tuple[ndarray, gdal.Dataset]
        The band-1 array, and the still-open GDAL dataset. The dataset is
        returned so the caller can reuse its geotransform and projection when
        writing an output — typically by passing it to
        :func:`writeImageGDAL` as ``reference``. Keep a name bound to it until
        writing is done; when GDAL's dataset object is garbage-collected the
        file handle closes.

    Raises
    ------
    FileNotFoundError
        If GDAL cannot open the file.
    """
    dataset = gdal.Open(filename, gdal.GA_ReadOnly)
    if not dataset:
        raise FileNotFoundError(f"Failed to open {filename}")
    band = dataset.GetRasterBand(1)
    image = band.ReadAsArray()
    return image, dataset


def writeImageGDAL(filename, data, reference):
    """
    Write a single-band **Byte** GeoTIFF, copying georeferencing from a reference dataset.

    Parameters
    ----------
    filename : str
        Output GeoTIFF path.
    data : ndarray
        2-D array to write. Written as GDT_Byte, so values are truncated to
        0-255 — this helper is for masks and class rasters, not for
        backscatter, elevation or depth. Use rasterio or an explicit
        ``driver.Create(..., gdal.GDT_Float32)`` for continuous data.
    reference : gdal.Dataset
        Open dataset whose geotransform and projection are copied, normally
        the second value returned by :func:`readImageGDAL`.

    Raises
    ------
    RuntimeError
        If GDAL cannot create the output file.
    """
    driver = gdal.GetDriverByName("GTiff")
    out_dataset = driver.Create(filename, data.shape[1], data.shape[0], 1, gdal.GDT_Byte)
    if not out_dataset:
        raise RuntimeError(f"Failed to create {filename}")
    out_dataset.SetGeoTransform(reference.GetGeoTransform())
    out_dataset.SetProjection(reference.GetProjectionRef())
    out_band = out_dataset.GetRasterBand(1)
    out_band.WriteArray(data)
    out_band.FlushCache()
