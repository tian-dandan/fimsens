
from osgeo import gdal, ogr, osr
import numpy as np
import os
from shapely.geometry import Polygon
from tqdm import tqdm

def split_tile(dataset, x_offset, y_offset, x_tile_size, y_tile_size, geotransform, level, max_level, threshold):
    """
    Split the tile recursively up to max_level levels and return polygons for tiles
    that have mean value greater than the threshold.
    """
    tile_info = []
    if level > max_level:
        return tile_info

    tile_data = dataset.ReadAsArray(x_offset, y_offset, x_tile_size, y_tile_size)
    mean_value = np.mean(tile_data)

    if mean_value > threshold and mean_value < 1 - threshold:
        min_x = geotransform[0] + x_offset * geotransform[1]
        max_x = min_x + x_tile_size * geotransform[1]
        min_y = geotransform[3] + y_offset * geotransform[5]
        max_y = min_y + y_tile_size * geotransform[5]
        tile_info.append((min_x, min_y, max_x, max_y, mean_value,level))
    else:
        sub_tile_size_x = x_tile_size // 2
        sub_tile_size_y = y_tile_size // 2
        for sub_i in range(2):
            for sub_j in range(2):
                sub_x_offset = x_offset + sub_j * sub_tile_size_x
                sub_y_offset = y_offset + sub_i * sub_tile_size_y
                sub_x_tile_size = min(sub_tile_size_x, dataset.RasterXSize - sub_x_offset)
                sub_y_tile_size = min(sub_tile_size_y, dataset.RasterYSize - sub_y_offset)

                tile_info.extend(
                    split_tile(dataset, sub_x_offset, sub_y_offset, sub_x_tile_size, sub_y_tile_size, geotransform, level + 1, max_level, threshold)
                )

    return tile_info

def create_polygons(tile_info, output_shapefile, projection):
    for ext in ('.shp', '.dbf', '.shx', '.prj', '.cpg', '.qpj'):
        sidecar = os.path.splitext(output_shapefile)[0] + ext
        if os.path.exists(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass

    driver = ogr.GetDriverByName('ESRI Shapefile')
    shapefile = driver.CreateDataSource(output_shapefile)
    if shapefile is None:
        raise RuntimeError(
            f"OGR could not create shapefile: {output_shapefile}\n"
            "Check that the directory exists and no other application has the file open."
        )
    srs = osr.SpatialReference()
    srs.ImportFromWkt(projection)
    layer = shapefile.CreateLayer('tiles', srs, ogr.wkbPolygon)

    layer.CreateField(ogr.FieldDefn('mean', ogr.OFTReal))
    layer.CreateField(ogr.FieldDefn('level', ogr.OFTInteger))

    for min_x, min_y, max_x, max_y, mean_value,level in tile_info:
        ring = ogr.Geometry(ogr.wkbLinearRing)
        ring.AddPoint(min_x, min_y)
        ring.AddPoint(max_x, min_y)
        ring.AddPoint(max_x, max_y)
        ring.AddPoint(min_x, max_y)
        ring.AddPoint(min_x, min_y)
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(ring)

        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(poly)
        feature.SetField('mean', mean_value)
        feature.SetField('level',level)
        layer.CreateFeature(feature)
        feature = None

    shapefile = None


def region_split(workspace, water_image_path, threshold, tile_size, levels):
    """
    Adaptively split a SAR image into tiles for bimodal gamma fitting.

    Parameters
    ----------
    workspace        : str   Output directory (regions.shp written here).
    water_image_path : str   Path to rs_band.tif.
    threshold        : float Mean value threshold for tile selection (e.g. 0.3).
    tile_size        : float Target tile size in map units (metres).
    levels           : int   Maximum recursion depth.
    """
    dataset = gdal.Open(water_image_path)
    if not dataset:
        raise FileNotFoundError(f"Failed to open {water_image_path}")

    x_size = dataset.RasterXSize
    y_size = dataset.RasterYSize
    geotransform = dataset.GetGeoTransform()
    projection = dataset.GetProjection()
    x_res = dataset.GetGeoTransform()[1]
    y_res = -dataset.GetGeoTransform()[5]
    resolution = (x_res + y_res) / 2
    tile_size = int(tile_size / resolution)
    tile_info = []
    x_tiles = (x_size + tile_size - 1) // tile_size
    y_tiles = (y_size + tile_size - 1) // tile_size
    import itertools
    for i, j in tqdm(itertools.product(range(0,y_tiles), range(0,x_tiles)),desc="procesing tiles"):

        x_offset = j * tile_size
        y_offset = i * tile_size
        x_tile_size = min(tile_size, x_size - x_offset)
        y_tile_size = min(tile_size, y_size - y_offset)
        tile_info.extend(
            split_tile(dataset, x_offset, y_offset, x_tile_size, y_tile_size, geotransform, 1, levels, threshold)
        )

    dataset = None
    output_shapefile = os.path.join(workspace,'regions.shp')

    print(f"Writing {len(tile_info)} tiles to polygon {output_shapefile}")
    create_polygons(tile_info, output_shapefile, projection)
