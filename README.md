# FIMsens

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
<!-- Add once a Zenodo release exists:
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
-->

**FIMsens** is a modular multi-sensor toolset for flood extent and depth mapping.

When you look at a satellite image after a flood it is tempting to treat the task
as simple: find the water, classify it, produce a map. In practice the satellite
rarely sees the whole flood. Some dry surfaces look like water — commission
errors. Real floodwater hides under tree canopy, between buildings, beneath
clouds, or in weak-signal pixels — omission errors. Filtering aggressively
removes false detections but recovers nothing that was never observed;
reconstructing from unreliable detections expands the wrong areas.

So the useful question is not *which pixels are water*, but **which detections can
we trust, and how do we use them to recover what the satellite missed**.

FIMsens answers it as a sequence rather than a single classification step:
**discover → detect → assess → reconstruct → estimate depth**. Each stage improves
the evidence before passing it on, and every intermediate output is preserved, so
a final map can be traced back to what produced it. Directly observed and
reconstructed inundation are kept as **separate classes** — users can see which
areas satellite evidence supports directly and which were inferred from terrain.

## Modules

The paper and this codebase use different names for the same four stages. The
mapping:

| Stage | Paper / talk | Method | Code |
|---|---|---|---|
| 1 | Discovery | — | `fimsens.download` |
| 2 | Detection | **SLAT** — Seeded Local Adaptive Thresholding | `fimsens.raw` |
| 3 | Assessment | **FOR-Filter** — Flood Object Reliability Filter | `fimsens.filter` |
| 4 | Reconstruction + depth | **RS-FloodXDepth** | `fimsens.grow` |

> Module directory names are scheduled to move to the paper's terminology in a
> future release, with backwards-compatible aliases. See `CHANGELOG.md`.

**1 · Discovery** (`fimsens.download`) — Not merely a download step. For one
event there may be several Sentinel-1, Sentinel-2 or Landsat scenes; some cover
only part of the area, some optical scenes are cloud-wrecked, some radar passes
sit closer to the flood peak. Starting from an area of interest and an event
period, FIMsens searches for candidates, lets you preview and compare them, and
downloads and preprocesses what you select. The workflow does not assume the
right image has already been chosen.

**2 · Detection** (`fimsens.raw`) — **SLAT** starts from reliable water seeds and
grows into connected water-like pixels. Rather than one threshold for the whole
image, it estimates thresholds at multiple local scales, because water does not
look the same everywhere: a wide smooth water body gives a very different signal
from shallow water, narrow channels, flooded vegetation or wind-roughened
surfaces. The output is the directly observable portion of the flood — starting
evidence, not an answer.

**3 · Assessment** (`fimsens.filter`) — The **FOR-Filter** asks whether each
detected flood *object* makes physical sense, judging whole objects rather than
isolated pixels, on three principles. *Scale*: real floods form spatially
coherent objects, so scattered fragments are more likely noise. *Storage*: an
isolated water body is still plausible inside a closed topographic depression
where water can collect. *Connectivity*: floodwater connected to a river should
hold a reasonable water-level relationship with that river and the terrain. In
the reference case the false-positive fraction fell from ~29 % to under 1 %,
while most true flood area was retained.

**4 · Reconstruction and depth** (`fimsens.grow`) — **RS-FloodXDepth** combines
the surviving flood objects with a DEM. For each object it estimates a local
water-surface elevation, then asks whether nearby low-lying terrain should be
underwater at that level; where the terrain is hydrologically consistent, the
area is added as reconstructed inundation. Depth is the difference between the
estimated water surface and ground elevation. In the reference case mapped flood
area grew from 38 to 73 km² and omission error fell from ~41 % to 2 %.

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

## Quick Start

### Download SAR imagery

Downloading is interactive — you search, preview the candidate scenes on a map,
tick the ones you want, then download. That flow does not compress into a
snippet, so it lives in **[`examples/01_single_event_interactive.ipynb`](examples/01_single_event_interactive.ipynb)**, which is the
reference for this stage.

The entry points:

```python
from fimsens.download import GEESearcher, GEEDownloader

GEESearcher.search_all(...)          # or search_sentinel1 / search_sentinel2 / search_landsat
GEESearcher.get_jrc(), get_dem(...)  # permanent-water mask and DEM
GEEDownloader.download_images(...)   # plus download_jrc / download_dem
```

Sentinel-1 arrives from `COPERNICUS/S1_GRD`, whose VV/VH bands are in **dB** —
which is why `leeFilt` below defaults to `db_input=True`.

### Raw flood mapping

```python
from fimsens.raw import leeFilt, region_split, bimodalFit, filterOutliers, interpFlood, regionGrow, postProcess

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
#    `threshold` has NO default on purpose — the right value depends on the
#    sensor and polarisation, and a wrong one fails silently:
#      S1 VV open water   -15 to -18 dB      S1 VH open water   -20 to -23 dB
#      S1 VV wind-roughened   -12 to -15 dB  optical NIR [0-1]  0.08 to 0.12
regionGrow(workspace, "data/jrc_seed.tif", "output/S1_lee.tif", threshold=-15)

# 7. Morphological post-processing
postProcess("output/water_connect.tif", "output/flood_clean.tif", minObject=100, minHoleSize=10000)
```

### Commission error filtering

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

### Flood grow and depth estimation

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

## License

MIT License — see [LICENSE](LICENSE) for details.

## Citation

If you use FIMsens in your research, please cite it. GitHub renders a
"Cite this repository" button from `CITATION.cff`, which also produces BibTeX.

> Tian, D. (2026). *FIMsens: a modular multi-sensor toolset for flood extent and
> depth mapping* (Version 0.2.0) [Computer software].
> https://github.com/tian-dandan/fimsens

<!-- Replace with the archived release once a Zenodo DOI exists, and with the
     journal article once it is published. -->
