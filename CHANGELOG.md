# Changelog

All notable changes to FIMsens are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Check this file before upgrading a production environment to a new tag.

## [Unreleased]

### Planned
- Programmatic scene-selection strategy for batch processing (nearest to flood
  peak, highest coverage, lowest cloud) — currently selection is only available
  through the interactive widget, which blocks unattended batch runs.
- QA / processing-metadata output alongside each result.
- Module renaming to match the paper's terminology (`raw` → SLAT,
  `filter` → FOR-Filter, `grow` → RS-FloodXDepth, `download` → discovery),
  with backwards-compatible aliases.
- Small test chip under `examples/data/` so the pipeline can be run without a
  Google Earth Engine account.

---

## [0.2.0] — 2026-08-18

First release after a full audit of the merge from `Components_v1` into the
package. 96 of 105 shared functions were byte-identical in logic; the nine that
differed were reviewed individually and two real defects were fixed.

### ⚠ Breaking

- **The package is renamed `fimsense` → `fimsens`.** Update imports:
  `import fimsense` → `import fimsens`. The project's formal name is FIMsens.
- **`grow.generate_lwse_layers` parameter order changed.** The two output paths
  moved ahead of the tuning parameters so the latter could take defaults:
  `(input_image, dem_path, output_boundary_path, output_centroid_path, n_points=200, min_pixels=20, k_neighbors=5)`.
  Callers using keyword arguments are unaffected; positional callers must be
  updated. A guard raises `TypeError` naming this change if an output path is
  not a string.
- **`grow.idw2Flood1` parameter order changed.** `fieldName` moved from second
  position to the end and now defaults to `'LWSE_new'`. A guard raises
  `ValueError` if `demName` does not point to an existing file, which is what
  the old positional order produces.
- **`raw.leeFilt` now defaults to `db_input=True`.** Earth Engine's
  `COPERNICUS/S1_GRD` collection is log-scaled — VV/VH are in dB — so this
  matches what `fimsens.download` produces. The previous default of `False`, and
  the docstring claiming GEE exports linear power, were both wrong.
- The `fimsens-gui` console script is removed. The GUI was never packaged, so
  the command only ever raised `ModuleNotFoundError`. It now lives at
  `gui/fimsens_gui.py` and is run directly; see `gui/README.md`.
- `raw.utils.create_message` is removed (dead code, never called).

### Fixed

- **`filter.ObjectFilter.save_results` silently changed units.** Numeric columns
  above 1e9 are divided by 1e6 to fit the shapefile field width, which in
  practice affects the volume columns `depre_vol` and `depth_vol`. Because the
  scaling is data-dependent, the same column was m³ for a small study area and
  million-m³ for a large one, with nothing in the file to distinguish them.
  Scaled columns are now renamed to carry the unit (`depth_vMm3`, `depre_vMm3`,
  generic fallback `<8 chars>_M`), the removed notice is printed again,
  coordinate columns are excluded from scaling, and name collisions are
  deduplicated before DBF truncation. The CSV was and remains unscaled.
- **`grow.idw2Flood` / `idw2Flood1` assumed a north-up DEM without checking.**
  Both difference the interpolated LWSE surface against the DEM array
  pixel-by-pixel, which is meaningless on a rotated grid. `_require_north_up()`
  now raises with a pointer to `filter.align_rasters`.
- Missing-dependency errors now say what to install. `import fimsens.raw`
  without GDAL previously raised a bare `No module named 'osgeo'`; it now
  explains that GDAL is not pip-installable and gives the conda command.
  Similarly for the `[gee]` and `[viz]` extras. Unrecognised import errors are
  re-raised untouched so real bugs are not disguised.

### Changed

- Default values added, so most calls no longer need every parameter spelled
  out: `filterOutliers(BC=0.555)`, `region_split(threshold=0.3, tile_size=5000,
  levels=4)`, `interpFlood(power=2.0)`, `imagePrep(threshold=20)`,
  `idw2Flood(max_points=12, power=3.0)`,
  `generate_lwse_layers(n_points=200, min_pixels=20, k_neighbors=5)`.
- `raw.bimodalFit(gamma=...)` now defaults to `None` and derives the initial
  parameters from `image_type` via the new `GAMMA_INIT` mapping
  (`'SAR'` → `'18 31 1 2'`, `'optical'` → `'12 10 1.8 0.33'`). A hard-coded
  default would silently disagree with `image_type`. An unknown `image_type`
  now raises instead of quietly taking the SAR branch.
- `raw.regionGrow(threshold=...)` deliberately keeps **no** default. The correct
  value depends on sensor and polarisation (−15 to −18 dB for S1 VV, −20 to −23
  for VH, 0.08–0.12 for optical NIR), and a wrong one fails silently — −15 on
  optical NIR data is always true and classifies the whole scene as flood.
- `imagePrep(threshold=20)` — JRC occurrence percentage for the permanent-water
  seed, established experimentally. Lower it for narrow rivers, where mixed
  pixels dilute occurrence and the channel can otherwise be left without seeds.
- Docstring coverage went from 63/78 to **78/78 public functions**.
- Repository reorganised: `docs/` (user guide, parameter guide), `examples/`,
  `tests/`, `gui/`. `environment.yml` added for conda-based setup.

### Documentation

- README installation section rewritten around `environment.yml` +
  `pip install -e . --no-deps`, explaining why GDAL forces conda and why
  `--no-deps` matters.
- Fixed the repository URL in both READMEs — they pointed at a
  non-existent account, so `git clone` copied from the README failed.
- The Quick Start download example called `GEESearcher.search()` and
  `GEEDownloader.direct_download()`, neither of which exists. Replaced with the
  real entry points and a pointer to the example notebook.
- The Quick Start called `regionGrow(threshold=0.05)` — an optical value applied
  to SAR data, i.e. exactly the silent failure the required-parameter decision
  above is meant to prevent.

---

## [0.1.0] — 2026-06-07

Initial release: the `Components_v1` scripts merged into an installable package.
