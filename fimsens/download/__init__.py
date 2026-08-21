"""
fimsens.download — Download satellite imagery and auxiliary data from Google Earth Engine.

Classes
-------
GEESearcher    : Search Sentinel-1, Sentinel-2, Landsat, JRC, and DEM collections
GEEDownloader  : Download images to local GeoTIFF files
GEEPreview     : Interactive map and checkbox widget (Jupyter)

Helpers
-------
DEM_CATALOG    : Supported DEM sources and their GEE asset IDs
parse_aoi      : Parse various AOI formats → ee.Geometry + extent string
fix_arcgis_crs : Standalone script to repair CRS in downloaded GeoTIFFs

Note
----
Requires a Google Earth Engine account and `earthengine-api` installed.
Run `ee.Authenticate()` once before use, then `ee.Initialize()` at the start
of each session.
"""

from .._deps import helpful_import

with helpful_import("download"):
    from .gee_utils import get_utm_zone_and_crs, parse_aoi, format_timestamp, suppress_stderr
    from .gee_search import GEESearcher, DEM_CATALOG as SEARCH_DEM_CATALOG
    from .gee_download import GEEDownloader, DEM_CATALOG, direct_download

__all__ = [
    "GEESearcher",
    "GEEDownloader",
    "DEM_CATALOG",
    "SEARCH_DEM_CATALOG",
    "direct_download",
    "get_utm_zone_and_crs",
    "parse_aoi",
    "format_timestamp",
    "suppress_stderr",
]
