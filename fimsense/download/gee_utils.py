"""
gee_utils.py — Shared utilities for GEE Explorer
"""
from __future__ import annotations

import contextlib
import os
import sys
from datetime import datetime
from typing import Tuple

import ee


# ─── Geometry helpers ──────────────────────────────────────────────────────────

def get_utm_zone_and_crs(lat: float, lon: float) -> str:
    """Return EPSG code for the UTM zone covering (lat, lon)."""
    if lat < -80 or lat > 84:
        raise ValueError("Latitude must be between -80 and 84 degrees.")
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:326{zone:02d}" if lat >= 0 else f"EPSG:327{zone:02d}"


def parse_aoi(aoi_input) -> Tuple[ee.Geometry, str]:
    """
    Parse various AOI formats → (ee.Geometry.BBox, "xmin,ymin,xmax,ymax").

    Accepted input types
    --------------------
    list / tuple   [xmin, ymin, xmax, ymax]
    str            "xmin,ymin,xmax,ymax"  OR  path to .shp / .geojson / .gpkg
    GeoDataFrame   uses total_bounds
    Shapely geom   uses bounds
    """
    if isinstance(aoi_input, (list, tuple)) and len(aoi_input) == 4:
        xmin, ymin, xmax, ymax = map(float, aoi_input)

    elif isinstance(aoi_input, str):
        if aoi_input.endswith((".shp", ".geojson", ".gpkg")):
            import geopandas as gpd
            gdf = gpd.read_file(aoi_input)
            xmin, ymin, xmax, ymax = gdf.total_bounds
        else:
            xmin, ymin, xmax, ymax = map(float, aoi_input.split(","))

    elif hasattr(aoi_input, "total_bounds"):        # GeoDataFrame
        xmin, ymin, xmax, ymax = aoi_input.total_bounds

    elif hasattr(aoi_input, "bounds"):              # Shapely geometry
        xmin, ymin, xmax, ymax = aoi_input.bounds

    else:
        raise TypeError(f"Unsupported AOI type: {type(aoi_input)}")

    extent_gcs = f"{xmin},{ymin},{xmax},{ymax}"
    ee_geom    = ee.Geometry.BBox(xmin, ymin, xmax, ymax)
    return ee_geom, extent_gcs


def format_timestamp(ms: int) -> str:
    """Convert EE system:time_start (ms since epoch) to 'YYYY-MM-DD'."""
    return datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


# ─── IO helpers ───────────────────────────────────────────────────────────────

@contextlib.contextmanager
def suppress_stderr():
    """Context manager that silences stderr (used to quiet GDAL noise)."""
    with open(os.devnull, "w") as devnull:
        old = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old
