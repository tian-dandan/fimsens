# FIMsens — Parameter Guide

This guide is a companion to `examples/01_single_event_interactive.ipynb`. The recommended workflow is:

1. **Run `examples/01_single_event_interactive.ipynb` from start to finish** using the default Lake Powell settings to verify your environment is set up correctly.
2. **Come back to this guide** to understand what each parameter controls and how to change it for your own flood event.

All parameters referenced here correspond directly to cells in `examples/01_single_event_interactive.ipynb`.

---

## Cell 0 — Global Configuration

These are the first things to change when applying the pipeline to a new event.

### Study area

| Variable | Options | Notes |
|---|---|---|
| `AOI_BBOX` | `[xmin, ymin, xmax, ymax]` in WGS-84 decimal degrees | Easiest option. Get coordinates from Google Maps or QGIS. |
| `AOI_SHAPEFILE` | Path to `.shp`, `.geojson`, or `.gpkg` | Set this and leave `AOI_BBOX` as `None` if you have a shapefile. The pipeline uses the bounding box of the shapefile, not the exact polygon. |

### Date range

| Variable | Format | Notes |
|---|---|---|
| `START_DATE` | `'YYYY-MM-DD'` | Set to just before or at the start of the flood event. |
| `END_DATE` | `'YYYY-MM-DD'` | Set to just after the flood peak. A 3–5 day window usually captures one Sentinel-1 overpass. |
| `MAX_CLOUD` | `0`–`100` (%) | Only applies to Sentinel-2 and Landsat. Ignored for S1. Use `≤ 20` for clean optical imagery; raise to `90` if the event was cloudy and you just need scene metadata. |

### GEE project

| Variable | Notes |
|---|---|
| `GEE_PROJECT` | Your Google Earth Engine project ID (e.g. `'ee-yourname'`). Found at [code.earthengine.google.com](https://code.earthengine.google.com) under your account settings. |

### Output directories

`ROOT` controls where all output files are saved. Sub-folders `01_download/`, `02_raw/`, `03_filter/`, `04_grow/` are created automatically. Change `ROOT` for each new event to keep results organised, e.g.:

```python
ROOT = r'D:\floods\mississippi_2023'
```

---

## Stage 1 — Download

### Scene selection (interactive widget)

After running the search cell, a colour-coded table shows all available scenes. Use the preview map and checkbox widget to select which to download.

**Choosing a Sentinel-1 scene:**
- Pick the scene **closest to the flood peak** in time.
- If both ascending and descending passes are available, prefer the one with a lower incidence angle over the flooded area (descending orbits are standard for mid-latitudes).
- VV+VH dual-polarisation products are automatically filtered. VV is the primary band for open-water detection; VH is more sensitive to flooded vegetation.

### Download resolution

| Variable | Default | Options | Notes |
|---|---|---|---|
| `SCALE_S1` | `10` | `10`, `20` | Sentinel-1 native resolution is 10 m. Use 20 m only if storage is limited or the scene is very large. |
| `SCALE_S2` | `10` | `10`, `20` | Sentinel-2 10 m bands (B2 B3 B4 B8). Use 20 m for the SWIR bands (B11 B12). |
| `SCALE_LANDSAT` | `30` | `30` | Landsat native resolution. |
| `TILE_SIZE` | `1000` | `512`–`2000` | Number of pixels per download tile. Reduce to `512` if GEE returns a "request size too large" error. |
| `INNER_THREADS` | `10` | `4`–`20` | Concurrent tile downloads. Reduce to `4` on slow or metered connections. |

### DEM selection

Choose the DEM based on your study area. The DEM is used in all downstream stages (object attributes, depression analysis, depth estimation).

| Option | Coverage | Resolution | Best for |
|---|---|---|---|
| `'SRTM 30m'` | Global | 30 m | Default; works everywhere |
| `'NASADEM 30m'` | Global | 30 m | Slightly improved accuracy over SRTM in many areas |
| `'Copernicus GLO-30'` | Global | 30 m | Best global accuracy; recommended when available |
| `'ALOS AW3D30 30m'` | Global | 30 m | DSM (includes vegetation canopy height); use in areas with dense forest if SRTM is void-filled |
| `'USGS 3DEP 10m'` | USA only | 10 m | Best option for flood mapping in the contiguous USA, Hawaii, or Alaska |
| `'USGS 3DEP 1m'` | USA only (sparse) | 1 m | Lidar-derived; highest accuracy but large file size and incomplete coverage |

> Use a higher-resolution DEM if available — depth estimation accuracy depends directly on DEM quality.

---

## Stage 2 — Raw Flood Mapping

### 2.1 Lee speckle filter

| Parameter | Default | When to change |
|---|---|---|
| `window_size` | `7` | Increase to `9` or `11` for noisier images (e.g. urban areas, rough terrain). Larger windows smooth more speckle but blur flood boundaries. Must be an odd number. |
| `enl` | `4.9` | This value is specific to **Sentinel-1 IW GRD**. If you use a different product (e.g. EW mode or multi-looked), recompute as `(mean / std)²` from a homogeneous area of the image. |
| `db_input` | `True` | Keep `True` for GEE downloads (S1 is exported in dB). Set `False` only if you have pre-converted the image to linear power scale. |
| `enhanced` | `False` | Set `True` for Enhanced Lee filter, which better preserves point targets (buildings, ships) at a small cost in speckle suppression over water. |

### 2.2 Adaptive tile splitting

| Parameter | Default | When to change |
|---|---|---|
| `threshold` | `0.3` | Controls which tiles are selected for bimodal fitting. A tile is selected if `threshold < mean_backscatter < 1 − threshold`. Increase to `0.4` if most tiles are being skipped (very dry scene). Decrease to `0.2` if the flood is very large and dominates the histogram. |
| `tile_size` | `5000` | Initial tile side length in metres. Use `2000`–`3000` for small, localised floods; `5000`–`8000` for large regional events. |
| `levels` | `4` | Quadtree depth. Each level halves the tile size. Increase to `5` for very heterogeneous land cover. |

### 2.3 Bimodal gamma threshold fitting

| Parameter | Default | Options | When to change |
|---|---|---|---|
| `gamma` | `'18 31 1 2'` | String of 4 numbers: `'alpha1 alpha2 rate1 rate2'` | Default is calibrated for **SAR VV in dB**. Use `'12 10 1.8 0.33'` for optical NIR [0–1]. Only adjust if the fitting consistently fails (check the BC values in `threshold.shp`). |
| `image_type` | `'SAR'` | `'SAR'`, `'optical'` | Change to `'optical'` when mapping floods from Sentinel-2 or Landsat NIR reflectance. |

### 2.4 Outlier threshold filtering

| Parameter | Default | When to change |
|---|---|---|
| `BC` | `0.555` | The Bimodality Coefficient threshold. Tiles with BC > 0.555 are treated as well-bimodal and get a tighter filter (±2σ); others get ±1.5σ. Raise to `0.6` in very heterogeneous scenes (mixed urban/agricultural) to apply loose filtering to more tiles. Lower to `0.5` if good tiles are being incorrectly excluded. |

### 2.5 IDW flood classification

| Parameter | Default | When to change |
|---|---|---|
| `power` | `2.0` | IDW distance-decay exponent. Increase to `3`–`4` if the threshold surface has sharp local variation (complex terrain); the surface becomes more localised. Decrease to `1.5` for a smoother, more globally influenced surface in flat terrain. |

### 2.6 Region grow

| Parameter | Default | Options | When to change |
|---|---|---|---|
| `threshold` | `-15` | dB value (negative) | The growth threshold in dB. A pixel is added to the flood if its backscatter is below this value **and** it is 8-connected to a permanent-water seed pixel. |

**Typical values by polarisation and surface:**

| Scenario | Recommended value |
|---|---|
| S1 VV, open water (rivers, lakes) | `−15` to `−18` dB |
| S1 VV, rough or wind-disturbed water | `−12` to `−15` dB |
| S1 VH, open water | `−20` to `−23` dB |
| Optical NIR [0–1] | `0.08` to `0.12` |

If the grown flood area is **too small**: raise the threshold (less negative for SAR, higher for optical).  
If the grow is **over-extending** into non-water areas (dark roads, shadows): lower it.

### 2.7 Post-processing

| Parameter | Default | When to change |
|---|---|---|
| `minObject` | `100` | Minimum flood object size in pixels. At 10 m resolution: 100 px = 1 ha. Increase to `500` in urban areas with many small commission errors. Decrease to `20` for detailed mapping of narrow channels. |
| `minHoleSize` | `10000` | Maximum hole size to fill, in pixels. At 10 m resolution: 10,000 px = 1 km². Decrease if you want to preserve smaller islands. Increase if there are large gaps inside water bodies. |

---

## Stage 3 — Commission Error Filter

### 3.1 Object generation

| Parameter | Default | When to change |
|---|---|---|
| `morph_operation` | `'eded'` | Morphological sequence before labeling: `'e'` = erosion, `'d'` = dilation. `'eded'` smooths boundaries without significantly changing object area. Use `'ed'` for a lighter pass. Rarely needs changing. |
| `min_back` | `100` | Fill interior holes ≤ this many pixels before labeling. Increase to `500` if objects have large internal gaps from SAR layover or shadow. |
| `connectivity` | `2` | `1` = 4-connected (cardinal only); `2` = 8-connected (includes diagonals). Keep `2` for flood mapping. |

### 3.3 Filter rules

**Rule 0 — Remove small objects**

| Parameter | Default | When to change |
|---|---|---|
| `threshold_px` | `50` | Objects with fewer than this many pixels are classified as noise. At 10 m: 50 px = 5,000 m². Increase to `200`–`500` in areas with many small false positives (urban, agricultural). Decrease if you need to map small water bodies. |

**Rule 1 — Identify large confirmed objects**

| Parameter | Default | When to change |
|---|---|---|
| `n_clusters` | `2` | Number of KMeans clusters in log-area space. Keep at `2` (small vs large). |
| `n_init` | `50` | KMeans restarts. Reduce to `10` for faster processing on large datasets; the quality difference is minimal. |

**Rule 3 — LWSE consistency**

| Parameter | Default | When to change |
|---|---|---|
| `threshold_high` | `None` (auto) | Auto = `max(mean + 2σ, 5.0 m)` of reference delta_LWSE. Override (e.g. `10.0`) if auto-thresholding is too aggressive and is removing true flood objects on hilly terrain. |
| `threshold_low` | `None` (auto) | Auto = `mean − 2σ`. Override if objects below a river's LWSE are being incorrectly retained. |
| `k_neighbors` | `5` | Number of reference LWSE segments used to estimate the local mean. Increase to `10` if the reference layer is sparse (few large flood objects). |
| `min_threshold` | `5.0` | Floor on `threshold_high` in metres. Prevents over-filtering on very flat terrain where LWSE variance is near zero. Lower to `2.0` in flat coastal areas; raise to `10.0` in mountainous terrain. |

**Rule 4 — Distance from confirmed flood**

| Parameter | Default | When to change |
|---|---|---|
| `distance_threshold` | `'auto'` | Auto uses KMeans to find the near/far gap in log-distance space. Override with a fixed pixel distance (e.g. `1300`) if auto-split produces poor results — check the distance histogram plot in the notebook. |

---

## Stage 4 — Flood Depth Estimation

### 4.1 LWSE layers

| Parameter | Default | When to change |
|---|---|---|
| `n_points` | `200` | Boundary pixels per LWSE segment. Each segment's pixels are pooled to estimate one LWSE value via KDE. Decrease to `100` for finer spatial resolution on large flood objects; increase to `500` for smoother estimates and fewer segments. |
| `min_pixels` | `20` | Skip flood objects with fewer than this many boundary pixels. Increase to `50` if small objects are generating noisy LWSE estimates. |
| `k_neighbors` | `5` | Neighbours for IDW outlier replacement within the LWSE point field. Increase to `10` if there are very few segments (sparse flood). |

### 4.2 Depth mapping

| Parameter | Default | When to change |
|---|---|---|
| `fieldName` | `'LWSE_new'` | The LWSE field to interpolate. `'LWSE_new'` is the outlier-filtered version (recommended). Use `'LWSE'` to compare against the raw estimate. |
| `max_points` | `12` | IDW neighbourhood size. Increase to `20` for a smoother depth surface; decrease to `6` to keep the surface more local. |
| `power` | `3.0` | IDW exponent. Higher values (4–5) make the surface closely follow individual LWSE points — useful in topographically complex terrain. Lower values (2) produce a smoother, more regionally averaged surface — better for flat floodplains. |

---

## Common scenarios

### Flood from Sentinel-2 (optical, cloud-free)

Change in Cell 0:
```python
START_DATE = '...'
END_DATE   = '...'
MAX_CLOUD  = 10    # strict cloud filter for optical
```

Change in Stage 2:
```python
# leeFilt: skip (not needed for optical)
# bimodalFit:
gamma      = '12 10 1.8 0.33'
image_type = 'optical'
# regionGrow:
threshold  = 0.10   # NIR reflectance [0-1]
```

### Very large flood (regional scale, > 10,000 km²)

```python
# region_split: larger tiles
tile_size = 8000
levels    = 5

# postProcess: keep more detail
minObject   = 50
minHoleSize = 50000

# generate_lwse_layers: coarser segments for efficiency
n_points = 500
```

### Small localised flood (< 10 km²)

```python
# region_split
tile_size = 2000
levels    = 3

# postProcess: preserve small objects
minObject   = 20
minHoleSize = 1000

# generate_lwse_layers: finer segments
n_points = 100
```

---

## Output files reference

| File | Stage | Description |
|---|---|---|
| `01_download/S1_*.tif` | 1 | Sentinel-1 SAR backscatter, dB, VV + VH bands |
| `01_download/CaseJRC_*.tif` | 1 | JRC permanent water occurrence (0–100 %) |
| `01_download/CaseDEM_*.tif` | 1 | DEM in metres |
| `02_raw/S1_lee.tif` | 2 | Lee-filtered SAR image (dB) |
| `02_raw/threshold_idw.tif` | 2 | Spatially varying threshold surface |
| `02_raw/flood.tif` | 2 | Raw binary flood map (before region grow) |
| `02_raw/flood_clean.tif` | 2 | Post-processed binary flood map |
| `03_filter/object_attributes.csv` | 3 | Per-object morphometric + elevation attributes |
| `03_filter/object_predict.csv` | 3 | Per-object filter results (predict values 0–7) |
| `03_filter/object_predict.shp` | 3 | Same as CSV but as point shapefile |
| `03_filter/flood_filtered.tif` | 3 | Commission-error-filtered binary flood mask |
| `04_grow/centroid_elev.shp` | 4 | Segment centroids with LWSE and LWSE_new values |
| `04_grow/FloodClass.tif` | 4 | Classification: 0 = dry, 1 = seed flood, 2 = grown flood |
| `04_grow/FloodDepth.tif` | 4 | Flood depth in metres (NaN where non-flood) |
| `04_grow/flood_depth_overview.png` | 4 | Summary visualisation |
