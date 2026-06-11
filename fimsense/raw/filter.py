import os
from osgeo import gdal,ogr, osr
import json
import numpy as np


def filterOutliers(workspace, threshold_points, BC):
    """
    Remove statistical outliers from threshold point shapefile.

    Parameters
    ----------
    workspace         : str   Output directory.
    threshold_points  : str   Path to threshold.shp (output of bimodalFit).
    BC                : float Bimodality coefficient threshold.
                              Points with BC > this value use tighter outlier bounds.
    """
    gdal.UseExceptions()
    ogr.UseExceptions()
    output_shapefile = os.path.join(workspace,"threshold_filtered.shp")
    driver = ogr.GetDriverByName('ESRI Shapefile')
    input_ds = driver.Open(threshold_points, 0)  # 0 means read-only
    if not input_ds:
        raise RuntimeError(f"Unable to open input shapefile: {threshold_points}")

    input_layer = input_ds.GetLayer()
    values = []
    for feature in input_layer:
        value = feature.GetField('threshold')
        if value is not None and np.isfinite(value):
            values.append(value)

    values = np.array(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        raise RuntimeError("No valid threshold values found after filtering.")
    mean_value = np.nanmean(values)
    std_deviation = np.nanstd(values)
    threshold_u25 = mean_value + std_deviation * 1.5
    threshold_l25 = mean_value - std_deviation * 1.5
    threshold_u3 = mean_value + std_deviation * 2
    threshold_l3 = mean_value - std_deviation * 2
    sql_query = f"(BC > {BC} and (threshold > {threshold_l3} and threshold <  {threshold_u3})) or (BC < {BC} and (threshold > {threshold_l25} and threshold < {threshold_u25}))"
    print(f"Executing query {sql_query}")
    input_ds.ExecuteSQL(f"SELECT * FROM {input_layer.GetName()} WHERE {sql_query}")

    if os.path.exists(output_shapefile):
        driver.DeleteDataSource(output_shapefile)

    output_ds = driver.CreateDataSource(output_shapefile)
    spatial_ref = input_layer.GetSpatialRef()
    output_layer = output_ds.CreateLayer(input_layer.GetName(), spatial_ref, input_layer.GetGeomType())

    layer_defn = input_layer.GetLayerDefn()
    for i in range(layer_defn.GetFieldCount()):
        field_defn = layer_defn.GetFieldDefn(i)
        output_layer.CreateField(field_defn)

    selected_features = input_ds.ExecuteSQL(f"SELECT * FROM {input_layer.GetName()} WHERE {sql_query}")
    for feature in selected_features:
        output_layer.CreateFeature(feature)
    print(f"Shapefile written {output_shapefile}")
    input_ds.ReleaseResultSet(selected_features)
    input_ds = None
    output_ds = None
