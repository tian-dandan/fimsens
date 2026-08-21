"""
fimsens.filter — Commission error filter for flood inundation maps.

Pipeline:
    1. object_generation  : binary FIM → labeled flood objects
    2. reference_builder  : large confirmed objects → local LWSE reference layer
    3. depression         : depression fill, pit detection, depth metrics
    4. object_filter      : rule-based classification of remaining objects
    5. utils              : shared I/O, plotting, validation helpers
"""

import os
import warnings

# Suppress KMeans MKL memory-leak warning on Windows (cosmetic only, no impact on results)
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Suppress geopandas / fiona shapefile field-width and name-laundering warnings
warnings.filterwarnings(
    "ignore",
    message=".*Column names longer than 10 characters.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*Normalized/laundered field name.*",
    category=RuntimeWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*not successfully written.*field width.*",
    category=RuntimeWarning,
)

from .._deps import helpful_import

with helpful_import("filter"):
    from .object_generation import morph_smooth, generate_flood_objects
    from .reference_builder import (
        extract_boundary_segments,
        sample_dem_at_boundary,
        compute_segment_attributes,
        compute_reference_delta_lwse,
        build_reference_layer,
    )
    from .depression import (
        fill_depressions_spill,
        fill_depressions_from_raster,
        compute_depression_metrics,
        detect_pit_presence,
        compute_depth_metrics,
        add_depression_attributes,
    )
    from .object_filter import ObjectFilter
    from .utils import (
        read_raster,
        save_raster,
        plot_predict_map,
        compute_object_attributes,
        align_rasters,
    )

__all__ = [
    "morph_smooth",
    "generate_flood_objects",
    "extract_boundary_segments",
    "sample_dem_at_boundary",
    "compute_segment_attributes",
    "compute_reference_delta_lwse",
    "build_reference_layer",
    "fill_depressions_spill",
    "fill_depressions_from_raster",
    "compute_depression_metrics",
    "detect_pit_presence",
    "compute_depth_metrics",
    "add_depression_attributes",
    "ObjectFilter",
    "read_raster",
    "save_raster",
    "plot_predict_map",
    "compute_object_attributes",
    "align_rasters",
]
