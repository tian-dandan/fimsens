# FIMsense

**FIMsense** is a Python package for SAR and optical flood inundation mapping (FIM). It provides a complete pipeline from satellite image download through raw flood detection, commission-error filtering, and water surface elevation estimation with depth mapping.

## Components

| Subpackage | What it does |
|---|---|
| `fimsense.download` | Search and download Sentinel-1/2 imagery from Google Earth Engine |
| `fimsense.raw` | Speckle filtering, adaptive tile splitting, bimodal gamma threshold fitting, region growing, and IDW flood classification |
| `fimsense.filter` | Rule-based commission error filter using object attributes, DEM depressions, and LWSE consistency |
| `fimsense.grow` | Local Water Surface Elevation (LWSE) estimation from flood boundaries + IDW depth mapping and region-grow extension |

## Installation

### 1. Install GDAL (required, conda only)

GDAL must be installed via conda before installing fimsense:

```bash
conda create -n fimsense python=3.11
conda activate fimsense
conda install -c conda-forge gdal
```

### 2. Install fimsense

```bash
# From PyPI (after publishing)
pip install fimsense

# Or from source
git clone https://github.com/tiandan-geo/fimsense.git
cd fimsense
pip install -e .
```

### Optional extras

```bash
pip install fimsense[gee]    # Google Earth Engine download tools
pip install fimsense[viz]    # matplotlib visualisation
pip install fimsense[numba]  # Numba JIT for faster region grow
pip install fimsense[all]    # Everything
```

## Quick Start

### Download SAR imagery

```python
import ee
ee.Authenticate()
ee.Initialize()

from fimsense.download import GEESearcher, GEEDownloader

searcher = GEESearcher()
results = searcher.search(
    aoi="path/to/aoi.shp",
    start_date="2023-09-01",
    end_date="2023-09-30",
    collection="COPERNICUS/S1_GRD",
)

downloader = GEEDownloader()
downloader.direct_download(results[0], output_dir="data/")
```

### Raw flood mapping

```python
from fimsense.raw import leeFilt, region_split, bimodalFit, filterOutliers, interpFlood, regionGrow, postProcess

workspace = "output/"

# 1. Speckle filter
leeFilt("data/S1.tif", "output/S1_lee.tif", window_size=7, enl=4.9)

# 2. Adaptive tile splitting
region_split(workspace, "output/S1_lee.tif", threshold=0.3, tile_size=5000, levels=4)

# 3. Bimodal gamma threshold fitting
bimodalFit(workspace, "output/S1_lee.tif", "output/regions.shp", gamma="18 31 1 2")

# 4. Outlier filtering
filterOutliers(workspace, "output/threshold.shp", BC=0.555)

# 5. IDW flood classification
interpFlood(workspace, "output/threshold_filtered.shp", "threshold", "output/S1_lee.tif", power=2.0)

# 6. Region grow from JRC seed
regionGrow(workspace, "data/jrc_seed.tif", "output/S1_lee.tif", threshold=0.05)

# 7. Morphological post-processing
postProcess("output/water_connect.tif", "output/flood_clean.tif", minObject=100, minHoleSize=10000)
```

### Commission error filtering

```python
from fimsense.filter import (
    generate_flood_objects, compute_object_attributes,
    build_reference_layer, add_depression_attributes,
    ObjectFilter
)

fim_objs, meta, n = generate_flood_objects("output/flood_clean.tif", "output/fim_objects.tif")
attrs = compute_object_attributes(fim_objs, meta, dem_path="data/dem.tif")

filt = ObjectFilter(attrs)
filt.rule0_remove_small(50).rule1_identify_large()

filt.save_large_objects(fim_objs, "output/large_objects.tif", meta)
ref_df = build_reference_layer("output/large_objects.tif", "data/dem.tif", "output/")

attrs = add_depression_attributes(attrs, fim_objs, "data/dem.tif", pix_area_m2=100)
filt2 = ObjectFilter(attrs)
filt2.rule0_remove_small(50).rule1_identify_large().rule2_depression_water() \
     .rule3_filter_by_delta_lwse(ref_df).rule4_filter_by_distance()
filt2.save_results("output/")
```

### Flood grow and depth estimation

```python
from fimsense.grow import generate_lwse_layers, idw2Flood1

generate_lwse_layers(
    input_image="output/flood_clean.tif",
    dem_path="data/dem.tif",
    n_points=200,
    min_pixels=20,
    k_neighbors=5,
    output_boundary_path="output/boundary_elev.shp",
    output_centroid_path="output/centroid_elev.shp",
)

idw2Flood1(
    fcName="output/centroid_elev.shp",
    fieldName="LWSE_new",
    demName="data/dem.tif",
    floodSeedRaster="output/flood_clean.tif",
    idw_temp="output/idw_temp.tif",
    outRaster="output/FloodDepth.tif",
    outClassRaster="output/FloodClass.tif",
    output_shapefile="output/reprojected.shp",
    alignedSeedRaster="output/aligned_seed.tif",
    max_points=12,
    power=3.0,
)
```

## License

MIT License — see [LICENSE](LICENSE) for details.

## Citation

If you use FIMsense in your research, please cite:

> Tian, D. (2024). FIMsense: A Python package for flood inundation mapping from SAR and optical imagery. GitHub. https://github.com/tiandan-geo/fimsense
