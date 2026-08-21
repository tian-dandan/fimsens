# FIMsens — User Guide v2

**FIMsens** is a Python package for SAR and optical flood inundation mapping (FIM). It provides a complete pipeline from satellite image download through raw flood detection, commission-error filtering, and water surface elevation estimation with depth mapping.

## Components

| Subpackage | What it does |
|---|---|
| `fimsens.download` | Search and download Sentinel-1/2/Landsat imagery and DEMs from Google Earth Engine |
| `fimsens.raw` | Speckle filtering, adaptive tile splitting, bimodal gamma threshold fitting, region growing, and IDW flood classification |
| `fimsens.filter` | Rule-based commission error filter using object attributes, DEM depressions, and LWSE consistency |
| `fimsens.grow` | Local Water Surface Elevation (LWSE) estimation from flood boundaries + IDW depth mapping and region-grow extension |

## Installation

FIMsens depends on **GDAL**, which cannot be installed with pip alone — PyPI
ships no standalone binary wheel, so `pip install gdal` tries to compile against
a system libgdal that must already be present. Use conda for the environment and
pip only for FIMsens itself.

```bash
git clone https://github.com/tian-dandan/fimsens.git
cd fimsens

conda env create -f environment.yml     # GDAL + the whole geospatial stack
conda activate fimsens

pip install -e . --no-deps              # FIMsens itself
```

`--no-deps` matters: `environment.yml` already installed every dependency from
conda-forge. Without it, pip would resolve them again from PyPI and could shadow
the conda builds — the usual way to end up with two GDAL copies and a segfault.

Verify:

```python
import fimsens, fimsens.raw, fimsens.filter, fimsens.grow
print(fimsens.__version__)
```

If an import fails, the error message names the missing dependency and the
command that installs it.

### Installing without cloning

For testing a specific version without a working copy:

```bash
pip install git+https://github.com/tian-dandan/fimsens@v1.0.0
```

The conda environment is still required first — this replaces only the last step
above. Pin a tag rather than tracking `main` so you get a reproducible version.

### Optional extras

Already included in `environment.yml`. Only needed when installing FIMsens
outside that environment:

```bash
pip install fimsens[gee]    # Google Earth Engine download tools
pip install fimsens[viz]    # matplotlib visualisation
pip install fimsens[numba]  # Numba JIT for faster region grow
pip install fimsens[all]    # All of the above
```

> The desktop GUI is **not** part of the installed package. It lives in
> `gui/fimsens_gui.py` in this repository — see `gui/README.md`.

---

## Quick Start Example

The example below follows the full SAR flood mapping pipeline. Each section below the example documents every parameter and how to choose it.

### Step 1 — Download SAR imagery

```python
import ee
ee.Authenticate()
ee.Initialize()

from fimsens.download import GEESearcher, GEEDownloader

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

### Step 2 — Raw flood mapping

```python
from fimsens.raw import leeFilt, region_split, bimodalFit, filterOutliers, interpFlood, regionGrow, postProcess

workspace = "output/"

leeFilt("data/S1.tif", "output/S1_lee.tif", window_size=7, enl=4.9)
region_split(workspace, "output/S1_lee.tif", threshold=0.3, tile_size=5000, levels=4)
bimodalFit(workspace, "output/S1_lee.tif", "output/regions.shp", gamma="18 31 1 2")
filterOutliers(workspace, "output/threshold.shp", BC=0.555)
interpFlood(workspace, "output/threshold_filtered.shp", "threshold", "output/S1_lee.tif", power=2.0)
regionGrow(workspace, "data/jrc_seed.tif", "output/S1_lee.tif", threshold=-15)
postProcess("output/water_connect.tif", "output/flood_clean.tif", minObject=100, minHoleSize=10000)
```

### Step 3 — Commission error filtering

```python
from fimsens.filter import (
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

### Step 4 — Flood grow and depth estimation

```python
from fimsens.grow import generate_lwse_layers, idw2Flood1

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

---

## Parameter Reference

### Download — `GEESearcher` and `GEEDownloader`

#### `GEESearcher.search()`

| Parameter | Type | Default | Options / Guidance |
|---|---|---|---|
| `aoi_input` | multiple | — | Study area. See supported formats below. |
| `start_date` | str | — | Start date in `'YYYY-MM-DD'` format. |
| `end_date` | str | — | End date in `'YYYY-MM-DD'` format. |
| `collection` | str | — | GEE collection ID. See table below. |
| `max_cloud` | float | `30` | Maximum cloud cover percentage (0–100). Applies to S2 and Landsat only; ignored for S1. Lower values (≤10) are recommended for optical flood mapping. |

**Supported `aoi_input` formats:**

| Format | Example |
|---|---|
| List / tuple (WGS-84) | `[116.3, 39.7, 116.6, 40.0]` |
| Comma-separated string | `"116.3,39.7,116.6,40.0"` |
| Shapefile path | `"path/to/aoi.shp"` |
| GeoJSON / GeoPackage path | `"path/to/aoi.geojson"` or `"path/to/aoi.gpkg"` |
| GeoDataFrame | `gdf` (uses `total_bounds`) |
| Shapely geometry | `polygon` (uses `.bounds`) |

All formats are internally converted to a bounding-box (`ee.Geometry.BBox`), so the actual GEE filter always uses the envelope of the input geometry regardless of format.

**Supported collections:**

| `collection` string | Sensor | Use case |
|---|---|---|
| `"COPERNICUS/S1_GRD"` | Sentinel-1 SAR | All-weather flood mapping; works through cloud cover |
| `"COPERNICUS/S2_SR_HARMONIZED"` | Sentinel-2 optical | Cloud-free conditions; 10 m resolution |
| `"LANDSAT/LC09/C02/T1_L2"` | Landsat 9 | 30 m resolution; longer archive |

#### `GEEDownloader` / `direct_download()`

| Parameter | Type | Default | Guidance |
|---|---|---|---|
| `target_scale` | int | `10` | Output pixel resolution in metres. Use `10` for S1/S2. Use `30` for Landsat or when file size is a concern. |
| `tile_size` | int | `1000` | Download tile side in pixels. Reduce to `512` if GEE times out over large or complex areas. |
| `n_threads` | int | `10` | Concurrent download threads. Reduce to `4–6` on slow connections. |
| `block_threshold` | int | `10000` | Pixel count above which a tile is split further. Lower values produce more, smaller sub-tiles. |

#### `GEEDownloader.download_dem()`

| `dem_name` | Resolution | Notes |
|---|---|---|
| `"SRTM 30m"` | 30 m | Default. Global coverage; good for most flood studies. |
| `"NASADEM 30m"` | 30 m | Reprocessed SRTM; slightly improved accuracy. |
| `"Copernicus GLO-30"` | 30 m | Best global accuracy; recommended where available. |
| `"ALOS AW3D30 30m"` | 30 m | Good in vegetated or mountainous terrain. |
| `"USGS 3DEP 1m"` | 1 m | USA only; highest resolution available. |

---

### Raw Mapping — `fimsens.raw`

#### `leeFilt` — SAR speckle filter

```python
leeFilt(input_path, output_path, window_size=7, enl=4.9, enhanced=False, db_input=False)
```

| Parameter | Default | Options / Guidance |
|---|---|---|
| `window_size` | `7` | Must be an odd integer. Larger windows (`9`, `11`) smooth more speckle but reduce edge sharpness. `7` is the standard choice for Sentinel-1 IW GRD. |
| `enl` | `4.9` | Equivalent Number of Looks. Use `4.9` for Sentinel-1 IW GRD (the GEE export default). Adjust if working with a multi-looked product: ENL = (mean/std)². |
| `enhanced` | `False` | `False` = Standard Lee filter. `True` = Enhanced Lee filter, which preserves edges and point targets better at the cost of slightly higher noise in uniform areas. |
| `db_input` | `False` | **`True` for GEE exports** — GEE downloads Sentinel-1 as dB (`units = "backscatter dB"`). `False` = linear power scale (e.g., manually converted data). The `leeFilt` source docstring says "False = linear (GEE export)" which is incorrect; GEE S1 data is in dB. Mismatching this flag will produce incorrect filtering results. |

#### `region_split` — Adaptive tile splitting

```python
region_split(workspace, water_image_path, threshold=0.3, tile_size=5000, levels=4)
```

| Parameter | Default | Guidance |
|---|---|---|
| `threshold` | `0.3` | Mean value cutoff for selecting a tile as a candidate for bimodal fitting. A tile is selected if `threshold < mean < 1 - threshold`, i.e., it contains a mix of water and land. Increase to `0.4` if too many homogeneous (all-land) tiles are being selected. |
| `tile_size` | `5000` | Initial tile side length in map units (metres for projected CRS). Larger values reduce total tile count but may include too much spatial variation for accurate threshold fitting. `2000–5000 m` is typical. |
| `levels` | `4` | Maximum quadtree recursion depth. Each level halves the tile side. `4` allows tiles down to `tile_size / 2⁴ = ~300 m` at 10 m resolution. Increase to `5` for very heterogeneous scenes. |

#### `bimodalFit` — Bimodal gamma threshold fitting

```python
bimodalFit(workspace, sarImage, regions, gamma="18 31 1 2", image_type='SAR')
```

| Parameter | Default | Guidance |
|---|---|---|
| `gamma` | `'18 31 1 2'` | Initial bimodal gamma parameters: `'alpha1 alpha2 rate1 rate2'`. The EM algorithm refines these from the data, so they are starting estimates rather than exact values. See recommended presets below. |
| `image_type` | `'SAR'` | `'SAR'` for SAR backscatter (linear or dB). `'optical'` for optical NIR 0–1 reflectance. The optical mode internally scales values ×100 before fitting and divides the threshold back by 100. |

**Recommended `gamma` presets:**

| Scenario | `gamma` string | Notes |
|---|---|---|
| SAR dB (GEE default) | `'18 31 1 2'` | Water component (alpha1=18, rate1=1) and land component (alpha2=31, rate2=2). GEE exports Sentinel-1 in dB; this preset is calibrated for dB-scale value ranges (typically −30 to 0 dB). |
| Optical NIR 0–1 | `'12 10 1.8 0.33'` | Lower alpha, different rate ratio reflecting the inverted contrast (water is darker in NIR). |

The fitting converges from these starting values using EM over 100 iterations, so results are generally robust to moderate deviations from the presets.

#### `filterOutliers` — Threshold outlier removal

```python
filterOutliers(workspace, threshold_points, BC=0.555)
```

| Parameter | Default | Guidance |
|---|---|---|
| `BC` | `0.555` | Bimodality Coefficient threshold. Tiles with BC > this value have a clearly bimodal histogram and are treated as high-confidence fits, so a tighter outlier band is applied (±2σ vs ±1.5σ for low-BC tiles). BC > 0.555 indicates bimodality above chance. Raise to `0.6` in heterogeneous scenes to apply looser filtering to more tiles. |

#### `interpFlood` — IDW threshold interpolation and flood classification

```python
interpFlood(workspace, threshold_points, field, sarImage, power=2.0)
```

| Parameter | Default | Guidance |
|---|---|---|
| `field` | `'threshold'` | Field name in the threshold shapefile to interpolate. Always `'threshold'` unless you have renamed the field. |
| `power` | `2.0` | IDW distance-decay exponent. Higher values (`3–4`) make the interpolated surface adhere more closely to local threshold points and reduce influence from distant tiles. Lower values (`1–1.5`) produce a smoother, more global surface. `2.0` is the standard choice. |

#### `regionGrow` — Region growing from permanent-water seed

```python
regionGrow(workspace, seed_image_path, grow_image_path, threshold)
```

| Parameter | Default | Guidance |
|---|---|---|
| `seed_image_path` | — | Binary raster of permanent/historical water (e.g., JRC Global Surface Water). Pixels > 0 are used as seeds. |
| `threshold` | **none — required** | Growth threshold: a pixel is added to the flood region if its value is below this and it is 8-connected to a water pixel. Deliberately has no default, because the correct value depends on sensor and polarisation and a wrong one fails silently — `-15` on optical NIR data (range 0–1) is always true and would classify the whole scene as flood. See recommended values below. |

**Recommended `threshold` values:**

| Input type | Recommended value | Notes |
|---|---|---|
| SAR VV dB (GEE) | `-15` to `-18` | Open water VV backscatter is typically −25 to −15 dB. Use −15 dB as a starting point and tighten (lower) if non-water dark surfaces (roads, shadows) are over-included. |
| SAR VH dB (GEE) | `-20` to `-23` | VH has ~5–8 dB lower backscatter than VV for the same surface; threshold accordingly. |
| Optical NIR 0–1 | `0.10` | Water reflectance in NIR is typically below 0.1. Optical imagery is downloaded as reflectance [0–1], not dB. |

If the region grow produces too little water, increase the threshold (less negative for dB). If it over-grows into dark non-water areas, decrease it.

#### `postProcess` — Morphological cleanup

```python
postProcess(inImg, outImg, minObject=100, minHoleSize=10000)
```

| Parameter | Default | Guidance |
|---|---|---|
| `minObject` | `100` | Minimum flood object size in pixels. Objects smaller than this are removed as noise. At 10 m resolution, 100 px = 10,000 m² = 1 ha. Increase to `500` to remove small commission errors in urban areas. |
| `minHoleSize` | `10000` | Maximum interior hole size (pixels) that will be filled. Holes larger than this are left open, preserving islands. At 10 m resolution, 10,000 px = 1 km². |

---

### Commission Error Filter — `fimsens.filter`

#### `generate_flood_objects` — Object labeling

```python
generate_flood_objects(fim_path, output_path, min_front=0, min_back=100,
                       morph_operation='eded', connectivity=2)
```

| Parameter | Default | Guidance |
|---|---|---|
| `min_front` | `0` | Remove foreground objects smaller than this many pixels before labeling. Use `0` to keep all objects (Rule 0 of `ObjectFilter` handles size filtering later). |
| `min_back` | `100` | Fill interior holes smaller than this many pixels. Helps close gaps caused by SAR shadowing or floating vegetation. |
| `morph_operation` | `'eded'` | Morphological sequence applied before labeling: `'e'` = erosion, `'d'` = dilation. `'eded'` removes small protrusions while preserving overall shape. `'ed'` is a lighter alternative. |
| `connectivity` | `2` | `1` = 4-connected (cardinal directions only). `2` = 8-connected (includes diagonals). Use `2` for flood mapping — diagonal connectivity better reflects continuous water bodies. |

#### `ObjectFilter` — Sequential rule-based filter

Rules are applied in order. Each rule only acts on objects not yet classified.

**Rule 0 — Remove small objects**

```python
filt.rule0_remove_small(threshold_px=50)
```

| Parameter | Default | Guidance |
|---|---|---|
| `threshold_px` | `50` | Objects with area below this pixel count are labelled as false positives (predict=0). At 10 m resolution, 50 px = 5,000 m². Increase in noisy environments (e.g., urban SAR). |

**Rule 1 — Identify large confirmed objects**

```python
filt.rule1_identify_large(n_clusters=2, n_init=50)
```

| Parameter | Default | Guidance |
|---|---|---|
| `n_clusters` | `2` | Number of KMeans clusters in log-area space. `2` separates large confirmed flood objects from smaller uncertain ones. Rarely needs changing. |
| `n_init` | `50` | KMeans restarts. Higher values reduce sensitivity to random initialisation. `50` is conservative; `10` is faster with minimal quality loss for large datasets. |

**Rule 2 — Depression-stored water**

```python
filt.rule2_depression_water()
```

No tunable parameters. Classifies objects as depression water (predict=2) if they occupy a DEM pit (`has_pit=1`) and their estimated water volume fits within the depression volume (`depth_vol < depre_vol`). Requires `add_depression_attributes()` to have been run first.

**Rule 3 — LWSE consistency filter**

```python
filt.rule3_filter_by_delta_lwse(reference_df, threshold_high=None, threshold_low=None,
                                 k_neighbors=5, min_threshold=5.0)
```

| Parameter | Default | Guidance |
|---|---|---|
| `threshold_high` | `auto` | Objects whose LWSE exceeds the local reference mean by more than this (metres) are flagged as false positives (predict=4). Default: `max(mean + 2σ, min_threshold)` from the reference layer. Override if you know the expected maximum LWSE deviation for your study area. |
| `threshold_low` | `auto` | Objects whose LWSE falls below the local reference mean by more than this are flagged as tentative flood (predict=5). Default: `mean − 2σ`. |
| `k_neighbors` | `5` | Number of nearest reference segments used to compute the local LWSE mean. Increase to `10` in areas with sparse reference coverage. |
| `min_threshold` | `5.0` | Minimum value of `threshold_high` in metres. Prevents overly tight filtering when reference variance is very low (e.g., flat terrain). |

**Rule 4 — Distance from confirmed flood**

```python
filt.rule4_filter_by_distance(distance_threshold='auto', n_init=50)
```

| Parameter | Default | Guidance |
|---|---|---|
| `distance_threshold` | `'auto'` | Objects farther than this pixel distance from the nearest confirmed (predict=1) object are classified as false positives (predict=6). `'auto'` uses KMeans to find the gap between near and far clusters in log-distance space. Set a fixed value (e.g., `1300`) to override, for instance when the auto-threshold produces poor splits. |
| `n_init` | `50` | KMeans restarts for the auto-threshold estimation (same rationale as Rule 1). |

**Predict value legend:**

| Value | Meaning |
|---|---|
| `0` | False positive — too small (Rule 0) |
| `1` | True flood — large confirmed object (Rule 1) |
| `2` | Depression-stored water, retained (Rule 2) |
| `4` | False positive — LWSE too high (Rule 3) |
| `5` | Tentative flood — LWSE too low (Rule 3) |
| `6` | False positive — too far from confirmed flood (Rule 4) |
| `7` | True flood — proximate to confirmed flood (Rule 4) |

---

### Flood Grow & Depth — `fimsens.grow`

#### `generate_lwse_layers` — Local Water Surface Elevation estimation

```python
generate_lwse_layers(input_image, dem_path, n_points, min_pixels, k_neighbors,
                     output_boundary_path, output_centroid_path)
```

| Parameter | Default | Guidance |
|---|---|---|
| `n_points` | `200` | Boundary pixels per LWSE segment. Each segment's pixels are pooled to estimate one LWSE value via KDE. Smaller values (`100`) give finer spatial resolution but noisier estimates. Larger values (`500`) produce smoother estimates for large flood objects. `200` is a good default for 10 m imagery. |
| `min_pixels` | `20` | Skip flood objects whose boundary contour has fewer than this many pixels. These are too small for reliable LWSE estimation. |
| `k_neighbors` | `5` | Nearest-neighbour count for the IDW outlier replacement step. A segment whose LWSE is a statistical outlier (>3σ above or <2σ below the local mean) is replaced with the IDW estimate from its `k_neighbors` nearest segments. Increase to `10` in sparse point fields. |

#### `idw2Flood1` — IDW depth mapping with region grow

```python
idw2Flood1(fcName, fieldName, demName, floodSeedRaster, idw_temp, outRaster,
           outClassRaster, output_shapefile, alignedSeedRaster, max_points=12, power=3.0)
```

| Parameter | Default | Guidance |
|---|---|---|
| `fieldName` | — | Field in the LWSE centroid shapefile to interpolate. Use `'LWSE_new'` (the outlier-filtered column from `generate_lwse_layers`). Use `'LWSE'` to compare against the unfiltered estimate. |
| `max_points` | `12` | Maximum number of LWSE points used in the IDW neighbourhood. Increasing this makes the surface smoother but slower. `8–15` is the practical range. |
| `power` | `3.0` | IDW distance-decay exponent for the LWSE surface. Higher values (`4–5`) create a more localised surface that follows each point closely; useful in topographically complex terrain. Lower values (`2`) produce a smoother, more regionally influenced surface. `3.0` balances local fidelity with spatial continuity. |
| `flood_class` | `1` | Pixel value in `floodSeedRaster` that marks confirmed flood pixels. Change only if your seed raster uses a different convention. |

**Output classification raster classes (`outClassRaster`):**

| Value | Meaning |
|---|---|
| `0` | Non-flood |
| `1` | Seed flood (confirmed by SAR thresholding) |
| `2` | Grown flood (DEM < LWSE surface, 8-connected to seed) |

---

## License

MIT License — see [LICENSE](LICENSE) for details.

## Citation

If you use FIMsens in your research, please cite:

> Tian, D. (2024). FIMsens: A Python package for flood inundation mapping from SAR and optical imagery. GitHub. https://github.com/tian-dandan/fimsens
