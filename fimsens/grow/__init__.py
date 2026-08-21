"""
fimsens.grow — Flood growing and depth estimation.

Functions
---------
generate_lwse_layers : binary flood raster + DEM → boundary & centroid shapefiles
                       with Local Water Surface Elevation (LWSE) per segment.
idw2Flood            : IDW interpolation of LWSE points → flood depth raster.
idw2Flood1           : IDW + region-grow → depth map and 3-class flood raster.
"""

from .._deps import helpful_import

with helpful_import("grow"):
    from .flood_lwse import generate_lwse_layers
    from .idw2flood import idw2Flood, idw2Flood1, raster_to_polygons

__all__ = [
    "generate_lwse_layers",
    "idw2Flood",
    "idw2Flood1",
    "raster_to_polygons",
]
