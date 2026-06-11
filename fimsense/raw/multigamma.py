
import os
import numpy as np
from .gamma_fit import find_threshold,invpsi,fit,compute_bimodality

from osgeo import gdal, ogr, osr
from tqdm import tqdm

def bimodalFit(workspace, sarImage, regions, gamma, image_type='SAR'):
    """
    Fit a bimodal gamma distribution to each tile and find the water/land threshold.

    Parameters
    ----------
    workspace   : str   Output directory (threshold.shp written here).
    sarImage    : str   Path to rs_band.tif (SAR dB or optical NIR 0-1).
    regions     : str   Path to regions.shp produced by region_split.
    gamma       : str   Initial parameters as 'alpha1 alpha2 rate1 rate2'.
                        SAR dB recommended   : '18 31 1 2'
                        Optical NIR recommended: '12 10 1.8 0.33'
    image_type  : str   'SAR' (default) or 'optical'.
                        For optical NIR (0-1 float), pixel values are scaled
                        by 100 before fitting so the gamma distribution receives
                        values in a practical range (~0-100 rather than 0-1).
                        The recovered threshold is divided by 100 before saving
                        so threshold.shp stores values on the original 0-1 scale.
    """
    # Scale factor: optical NIR 0-1 → ×100 for fitting; threshold ÷100 after.
    scale = 100.0 if image_type == 'optical' else 1.0

    print(f"bimodal: {workspace},{sarImage},{regions},{gamma}  [image_type={image_type}]")
    shapefile_path = regions
    shapefile = ogr.Open(shapefile_path,0)
    layer = shapefile.GetLayer()
    src_srs = layer.GetSpatialRef()

    raster = gdal.Open(sarImage)
    geotransform = raster.GetGeoTransform()
    projection = raster.GetProjection()
    pixel_width = geotransform[1]
    pixel_height = abs(geotransform[5])
    raster_srs = osr.SpatialReference()
    raster_srs.ImportFromWkt(projection)

    outshapefile = os.path.join(workspace,"threshold.shp")
    thresholds =[]
    BCs = []
    init = [float(i) for i in gamma.split()]
    alpha = np.array(init[0:2])
    rate = np.array(init[2:4])
    for feature in tqdm(layer,desc="Fitting bimodal distributions"):
        mean = feature.GetField("mean")
        geom = feature.GetGeometryRef()
        mem_driver = gdal.GetDriverByName('MEM')

        minX, maxX, minY, maxY = geom.GetEnvelope()

        options = gdal.WarpOptions(
            format='GTiff',
            cutlineDSName=shapefile_path,
            cutlineWhere=f"FID={feature.GetFID()}",
            cropToCutline=True,
            )
        clipped_raster = gdal.Warp('/vsimem/in_memory_warp', raster, options=options)

        image = clipped_raster.GetRasterBand(1).ReadAsArray().flatten()
        clipped_raster = None

        image = image[np.isfinite(image)]
        if image.size == 0:
            thresholds.append(float('inf'))
            BCs.append(float('nan'))
            continue

        image_fit = image * scale

        BC = compute_bimodality(image_fit)
        min_value = np.percentile(image_fit, 0)
        shift = min_value - 2
        shifted_image = image_fit - shift
        new_alpha, new_rate, pi = fit(shifted_image, alpha, rate,
                                      np.array([mean, 1 - mean]), k=2)
        if np.isnan(new_alpha[0]):
            print("Cannot find a good fit to the data")
        min_s = np.percentile(shifted_image, 0)
        max_s = np.percentile(shifted_image, 100)
        bins  = np.linspace(min_s, max_s, 1000)
        try:
            index = find_threshold(bins, new_alpha, new_rate, pi)
            if index is None or index < 0:
                threshold = float('inf')
            else:
                threshold = (bins[index] + shift) / scale   # back to original units
        except Exception:
            threshold = float('inf')
        thresholds.append(threshold)
        BCs.append(BC)
    driver = ogr.GetDriverByName("ESRI Shapefile")
    if driver is None:
        raise RuntimeError("ESRI Shapefile driver not available")

    point_ds = driver.CreateDataSource(outshapefile)
    point_layer = point_ds.CreateLayer("points", srs=src_srs, geom_type=ogr.wkbPoint)

    point_layer.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    point_layer.CreateField(ogr.FieldDefn("threshold", ogr.OFTReal))
    point_layer.CreateField(ogr.FieldDefn("BC", ogr.OFTReal))
    shapefile = ogr.Open(shapefile_path,1)
    layer = shapefile.GetLayer()
    for i, polygon_feature in tqdm(enumerate(layer),desc="writing features"):
        geom = polygon_feature.GetGeometryRef()
        centroid = geom.Centroid()

        point_feature = ogr.Feature(point_layer.GetLayerDefn())
        point_feature.SetGeometry(centroid)
        point_feature.SetField("id", polygon_feature.GetFID())
        thr = thresholds[i]
        point_feature.SetField("threshold", float(thr) if np.isfinite(thr) else -9999.0)
        bc  = BCs[i]
        point_feature.SetField("BC", float(bc) if np.isfinite(bc) else -9999.0)

        point_layer.CreateFeature(point_feature)
    point_ds = None
    shapefile = None
    raster = None

    return
